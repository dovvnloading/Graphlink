"""Run-scoped bindings to the application's existing workflow services.

The workflow specification is data, never authority. The caller supplies a
trusted approval callback and workflow/revision-specific cloud-consent lookup.
No effectful adapter can execute in the discovery-only registry.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import math
import mimetypes
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from backend.domain.graph_run_external import ExternalCall, dispatch_external_attempt
from graphlink_cloud_providers import CLOUD_PROVIDERS
from graphlink_model_catalog import ModelRef, unified_catalog
from graphlink_task_config import LOCAL_PROVIDER_OLLAMA, TASK_CHAT

from .contracts import AssetRef, FileValue, ImageValue, JsonValue, TableValue, TextValue
from .step_adapters import (
    LOCAL_PROVIDERS, MAX_MODEL_STEPS, MODEL_ID, check_json_result, digest,
    model_messages, validate_values,
)

_MAX_ASSET_BYTES = 20 * 1024 * 1024
_MAX_JSON_BYTES = 2 * 1024 * 1024


class WorkflowRequestJournal:
    """SQLite reservation and receipt for each external workflow step.

    Reservations survive process death. A second attempt (including a new
    host after restart) cannot replace an unresolved or completed reservation.
    The existing Graph Runner dispatch fence owns the phase transitions.
    """

    def __init__(self, db_path):
        self.db_path = Path(db_path)

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        try:
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS workflow_external_requests (
                    run_id TEXT NOT NULL, step_id TEXT NOT NULL,
                    workflow_id TEXT NOT NULL, revision TEXT NOT NULL,
                    kind TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    request_id TEXT NOT NULL UNIQUE, receipt_json TEXT NOT NULL,
                    outputs_json TEXT, PRIMARY KEY (run_id, step_id)
                )"""
            )
            return conn
        except BaseException:
            conn.close()
            raise

    def checkpoint(self, context, spec, step, outputs):
        request = step["externalAttempt"]
        key = (context.run_id, context.step_id)
        request_id = request["requestId"]
        receipt = json.dumps(step, ensure_ascii=False, allow_nan=False)
        output_json = (
            json.dumps(outputs, ensure_ascii=False, allow_nan=False)
            if step["status"] == "completed" else None
        )
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute(
                """SELECT request_id, workflow_id, revision, fingerprint
                   FROM workflow_external_requests WHERE run_id=? AND step_id=?""", key
            ).fetchone()
            identity = (request_id, spec.workflow_id, spec.revision, step["inputHash"])
            if old is None:
                if request["phase"] != "dispatched":
                    raise RuntimeError("External request reservation is missing")
                if request["kind"] == "model":
                    count = conn.execute(
                        "SELECT count(*) FROM workflow_external_requests WHERE run_id=? AND kind='model'",
                        (context.run_id,),
                    ).fetchone()[0]
                    if count >= MAX_MODEL_STEPS:
                        raise ValueError("A run may dispatch at most 64 model steps")
                conn.execute(
                    "INSERT INTO workflow_external_requests VALUES (?,?,?,?,?,?,?,?,?)",
                    (*key, spec.workflow_id, spec.revision, request["kind"],
                     step["inputHash"], request_id, receipt, output_json),
                )
            elif old != identity:
                raise RuntimeError("External request already reserved; inspect its durable receipt")
            else:
                conn.execute(
                    """UPDATE workflow_external_requests SET receipt_json=?, outputs_json=?
                       WHERE run_id=? AND step_id=?""", (receipt, output_json, *key)
                )
        return True

    def read(self, run_id, step_id):
        """Return the saved receipt and typed wire outputs, without replaying."""
        with closing(self._connect()) as conn:
            row = conn.execute(
                """SELECT receipt_json, outputs_json FROM workflow_external_requests
                   WHERE run_id=? AND step_id=?""", (run_id, step_id)
            ).fetchone()
        return None if row is None else (json.loads(row[0]), json.loads(row[1]) if row[1] else None)


class WorkflowRunHost:
    """Bind one immutable workflow revision to one admitted run.

    cloud_consent(workflow_id, revision, provider, endpoint) is a synchronous
    trusted lookup; approve(context, action, details) is an async, fresh user
    decision. Missing callbacks deny. Neither is taken from the step config.
    """

    def __init__(self, spec, run_id, *, settings_manager, journal, assets=None,
                 knowledge_db_path=None, collection_id=None, runtime=None,
                 cloud_consent=None, approve=None, dispatcher=None, document=None,
                 bus=None, notifications_state=None):
        self.spec = spec
        self.run_id = run_id
        self.settings = settings_manager
        self.journal = journal
        self.assets = assets
        self.knowledge_db_path = knowledge_db_path
        self.collection_id = collection_id
        self.runtime = runtime
        self.cloud_consent = cloud_consent
        self.approve = approve
        self.dispatcher = dispatcher
        self.document = document
        self.bus = bus
        self.notifications_state = notifications_state
        self.pricing = dict(settings_manager.get_pricing_overrides() or {}) if settings_manager else {}
        self.steps = {step.step_id: step for step in spec.steps}
        if len(self.steps) != len(spec.steps):
            raise ValueError("Duplicate workflow step ID")
        if sum(step.adapter.adapter_id == MODEL_ID for step in spec.steps) > MAX_MODEL_STEPS:
            raise ValueError("A workflow run supports at most 64 model steps")
        self._active = {}

    def _consented(self, provider, endpoint):
        return self.cloud_consent is not None and self.cloud_consent(
            self.spec.workflow_id, self.spec.revision, provider, endpoint
        ) is True

    def _require_cloud(self, provider, endpoint):
        if not self._consented(provider, endpoint):
            raise PermissionError("This workflow revision has no cloud consent for that destination")

    def _endpoint(self, provider):
        profile = CLOUD_PROVIDERS[provider]
        return profile.endpoint(self.settings.get_api_base_url(provider))

    async def _approve(self, context, action, details):
        if self.approve is None or await self.approve(context, action, details) is not True:
            raise PermissionError("Workflow action was not approved")

    async def run(self, adapter, prepared, context):
        if (context.run_id != self.run_id or self.steps.get(context.step_id) != prepared.spec
                or context.attempt < 1):
            raise ValueError("Step does not belong to this admitted run")
        if context.step_id in self._active:
            raise RuntimeError("Workflow step is already running")
        # Re-prepare at the trust boundary; forged PreparedStep values cannot
        # change a pinned config or smuggle unvalidated port values.
        if adapter.prepare(prepared.spec, prepared.inputs()) != prepared:
            raise ValueError("Workflow step preparation changed")
        event = threading.Event()
        self._active[context.step_id] = (context, event, asyncio.current_task())
        try:
            method = {
                MODEL_ID: self._model, "python.execute": self._python,
                "knowledge.search": self._knowledge, "web.research": self._web,
                "image.generate": self._image, "agent.run": self._agent,
            }[adapter.adapter_id]
            return await method(adapter, prepared, context, event)
        finally:
            self._active.pop(context.step_id, None)

    async def cancel(self, context):
        active = self._active.get(context.step_id)
        if active and active[0] == context:
            active[1].set()
            if active[2] is not asyncio.current_task():
                active[2].cancel()

    async def _worker(self, event, function, *args, **kwargs):
        """Do not release a step's ownership while its worker is still alive."""
        task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            event.set()
            # A repeated cancel must not detach a running provider/process.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if task.done() and not task.cancelled():
                task.exception()
            raise

    async def _external(self, adapter, prepared, context, call, invoke):
        fingerprint = adapter.fingerprint(prepared)
        step = {"status": "pending", "sourceId": context.step_id,
                "nodeId": context.step_id, "inputHash": fingerprint}
        outputs = {}
        wire = None

        async def apply(result):
            nonlocal wire
            wire = validate_values(prepared.spec.adapter.ports.outputs, result)
            outputs.update(result)
            return digest(wire)

        async def checkpoint():
            return await asyncio.to_thread(self.journal.checkpoint, context, self.spec, step, wire)

        await dispatch_external_attempt(step, call, checkpoint=checkpoint,
                                        invoke=invoke, apply_result=apply)
        return outputs

    async def _model(self, adapter, prepared, context, event):
        import api_provider
        from backend.providers.base import CancelToken, ChatRequest
        from backend.api.intents_graph_model import _require_loopback_ollama
        from backend.providers.graph_local_ollama import local_ollama_complete

        cfg = prepared.config()
        provider, model = cfg["provider"], cfg["model_id"]
        ref = ModelRef(provider, model)
        if not any(d.provider == provider and d.model_id == model and d.available and d.ready
                   for d in unified_catalog(self.settings)):
            raise ValueError("Select an available model from the unified catalog")
        messages = model_messages(cfg, prepared.inputs())
        if provider == LOCAL_PROVIDER_OLLAMA:
            endpoint = _require_loopback_ollama()
            if model.lower().endswith((":cloud", "-cloud")):
                raise ValueError("Ollama cloud models must use an explicitly configured cloud provider")
        elif provider in CLOUD_PROVIDERS:
            endpoint = self._endpoint(provider)
            self._require_cloud(provider, endpoint)
        else:
            endpoint = None

        def complete():
            if event.is_set():
                raise asyncio.CancelledError()
            if provider == LOCAL_PROVIDER_OLLAMA:
                response = local_ollama_complete(prompt=messages[-1]["content"],
                    system_prompt=messages[0]["content"], model_id=model, endpoint=endpoint)
                reply = response["message"]["content"]
            else:
                runtime = api_provider.comparison_runtime(ref, self.settings, runtime=self.runtime)
                snapshot = runtime.snapshot()
                if provider not in LOCAL_PROVIDERS:
                    actual = snapshot.api_base_url or CLOUD_PROVIDERS[provider].base_url
                    if actual != endpoint:
                        raise PermissionError("Provider endpoint changed after workflow consent")
                    self._require_cloud(provider, endpoint)
                client = snapshot.api_client if provider not in LOCAL_PROVIDERS else None
                try:
                    # Direct complete: no orchestration retry/fallback loop.
                    selected = api_provider._provider_for_model_ref(ref, snapshot)
                    reply = selected.complete(ChatRequest(task=TASK_CHAT, messages=messages,
                                                          model_ref=ref), CancelToken(event))
                finally:
                    if client is not None and callable(getattr(client, "close", None)):
                        client.close()
            if isinstance(reply, str):
                _, reply = api_provider.split_reasoning_and_content(reply)
            if not isinstance(reply, str) or not reply.strip():
                raise ValueError("Model returned no text")
            if cfg["output_kind"] == "text":
                return {"text": TextValue(reply)}
            if len(reply.encode("utf-8")) > _MAX_JSON_BYTES:
                raise ValueError("Model JSON exceeded the output limit")
            value = json.loads(reply)
            check_json_result(value, cfg["schema"])
            return {"json": JsonValue(value)}

        fingerprint = adapter.fingerprint(prepared)
        call = ExternalCall(kind="model", provider_name=provider.replace(" ", "-"),
                            model_id=model, input_ref="workflow-input:" + fingerprint,
                            input_hash=fingerprint)
        return await self._external(adapter, prepared, context, call,
                                    lambda _: self._worker(event, complete))

    def _asset(self, data, filename, media_type, *, image=False):
        if self.assets is None:
            raise RuntimeError("Workflow asset storage is unavailable")
        if len(data) > _MAX_ASSET_BYTES:
            raise ValueError("Workflow asset exceeds the size limit")
        ref = self.assets.put(data)
        asset = AssetRef(ref, ref, media_type, filename, len(data))
        return ImageValue(asset) if image else FileValue(asset)

    async def _python(self, adapter, prepared, context, event):
        from graphlink_plugins.code_sandbox.domain import VirtualEnvSandbox
        from graphlink_scratch_dirs import prepare_scratch_dir, remove_scratch_dir

        cfg, inputs = prepared.config(), prepared.inputs()
        await self._approve(context, "python.execute", {
            "fingerprint": adapter.fingerprint(prepared), "code": cfg["code"],
            "timeoutSeconds": cfg["timeout_seconds"], "hostExecution": True,
        })

        def execute():
            sandbox = VirtualEnvSandbox(uuid.uuid4().hex)
            root = Path(sandbox.base_dir)
            try:
                prepare_scratch_dir(root)
                values = {}
                for name, value in inputs.items():
                    if isinstance(value, TextValue):
                        values[name] = value.text
                    elif isinstance(value, JsonValue):
                        values[name] = value.value
                    elif isinstance(value, TableValue):
                        values[name] = {"columns": value.columns, "rows": value.rows}
                    else:
                        if self.assets is None:
                            raise RuntimeError("Workflow asset storage is unavailable")
                        data = self.assets.get(value.asset.sha256, max_bytes=_MAX_ASSET_BYTES)
                        if data is None or len(data) != value.asset.size_bytes:
                            raise ValueError("Missing or invalid input asset")
                        path = root / ("input_" + name)
                        path.write_bytes(data)
                        values[name] = str(path.resolve())
                (root / "workflow_inputs.json").write_text(
                    json.dumps(values, ensure_ascii=False, allow_nan=False), encoding="utf-8")
                prefix = (
                    "import json as _wf_json\nfrom pathlib import Path as _wf_path\n"
                    "globals().update(_wf_json.loads(_wf_path('workflow_inputs.json').read_text(encoding='utf-8')))\n"
                )
                suffix = ""
                kind = cfg["output_kind"]
                if kind != "text":
                    suffix = (
                        "\nimport json as _wf_json\nfrom pathlib import Path as _wf_path\n"
                        "_wf_path('workflow_result.json').write_text(_wf_json.dumps(result, allow_nan=False), encoding='utf-8')\n"
                    )
                sandbox.ensure_base_environment(lambda: not event.is_set())
                output, status = sandbox.execute_code(prefix + cfg["code"] + suffix,
                    lambda: not event.is_set(), timeout_seconds=cfg["timeout_seconds"])
                if status != 0:
                    raise RuntimeError("Python workflow step failed")
                if kind == "text":
                    return {"text": TextValue(output)}
                result_path = self._safe_output(root, "workflow_result.json")
                with result_path.open("rb") as stream:
                    raw = stream.read(_MAX_JSON_BYTES + 1)
                if len(raw) > _MAX_JSON_BYTES:
                    raise ValueError("Python result exceeded the JSON size limit")
                value = json.loads(raw)
                if kind == "json":
                    return {"json": JsonValue(value)}
                if kind == "table":
                    if not isinstance(value, dict) or set(value) != {"columns", "rows"}:
                        raise ValueError("Table result must contain columns and rows")
                    return {"table": TableValue(tuple(value["columns"]), tuple(map(tuple, value["rows"])))}
                path = self._safe_output(root, value)
                with path.open("rb") as stream:
                    data = stream.read(_MAX_ASSET_BYTES + 1)
                return {"file": self._asset(data, path.name, mimetypes.guess_type(path.name)[0]
                                            or "application/octet-stream")}
            finally:
                remove_scratch_dir(root)

        return await self._worker(event, execute)

    @staticmethod
    def _safe_output(root, name):
        if not isinstance(name, str) or Path(name).is_absolute():
            raise ValueError("Python file output must be a relative sandbox path")
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError("Python output is not a regular file inside the sandbox")
        return path

    async def _knowledge(self, adapter, prepared, context, event):
        from backend.knowledge_embedding_config import (
            _ChoiceGuardedEmbedder, embedding_provider_for_choice,
        )
        from backend.knowledge_retrieval import hybrid_search, format_untrusted_context
        from backend.api.intents_knowledge import format_search_result_row
        from backend.api.intents_graph_model import _require_loopback_ollama

        if self.knowledge_db_path is None or self.collection_id is None:
            raise RuntimeError("Knowledge collection was not bound to this workflow run")
        query = prepared.inputs()["query"].text
        kwargs = {"collection_id": self.collection_id, "k": prepared.config()["k"]}

        def search():
            choice = self.settings.get_knowledge_embedding_settings()
            source = choice.get("source", "off")
            provider = choice.get("cloud_provider")
            endpoint = None
            if source == "cloud" and provider in CLOUD_PROVIDERS:
                endpoint = self._endpoint(provider)

            def allowed():
                return (
                    not event.is_set() and self.settings.get_knowledge_embedding_settings() == choice
                    and (source != "cloud" or (
                        choice.get("cloud_opt_in") is True and endpoint is not None
                        and self._endpoint(provider) == endpoint and self._consented(provider, endpoint)
                    ))
                )

            if allowed() and source != "off":
                try:
                    if source == "ollama":
                        _require_loopback_ollama()
                    with embedding_provider_for_choice(self.settings, choice=choice) as (embedder, model):
                        if embedder is not None and allowed():
                            return hybrid_search(self.knowledge_db_path, query,
                                embedding_provider=_ChoiceGuardedEmbedder(embedder, allowed),
                                embedding_model_id=model, **kwargs)
                except Exception:
                    # Match Knowledge's keyword fallback, never send to a
                    # different provider or populate cold embeddings here.
                    pass
            return hybrid_search(self.knowledge_db_path, query, **kwargs)

        rows = await self._worker(event, search)
        return {"passages": JsonValue([format_search_result_row(row) for row in rows]),
                "text": TextValue(format_untrusted_context(rows))}

    def _research_runtime(self):
        import api_provider
        snapshot = (self.runtime or api_provider.DEFAULT_RUNTIME).snapshot()
        if snapshot.use_api_mode:
            provider = snapshot.api_provider_type
            if provider not in CLOUD_PROVIDERS:
                raise ValueError("Research model provider is not configured")
            endpoint = snapshot.api_base_url or CLOUD_PROVIDERS[provider].base_url
            self._require_cloud(provider, endpoint)
        elif snapshot.local_provider_type == LOCAL_PROVIDER_OLLAMA:
            from backend.api.intents_graph_model import _require_loopback_ollama
            _require_loopback_ollama()
        return api_provider.ProviderRuntime.from_snapshot(snapshot)

    async def _web(self, adapter, prepared, context, event):
        from backend.harness.tools_web import _approve_domain, _TaskResearchToken
        from backend.providers.base import CancelToken, ToolCall
        from graphlink_plugins.web_research.domain import ResearchLimits, WebResearchRequest, ResearchFailure
        from graphlink_plugins.web_research.providers import ApiResearchModel, RequestsDocumentFetcher
        from graphlink_plugins.web_research.service import WebResearchService

        query = prepared.inputs()["query"].text
        runtime = self._research_runtime()
        token = _TaskResearchToken(CancelToken(event))
        loop = asyncio.get_running_loop()
        call = ToolCall(id=uuid.uuid4().hex, name="web.research", arguments={"query": query})

        async def ask(tool_call):
            return self.approve is not None and await self.approve(
                context, "web.domain", tool_call.arguments
            ) is True

        approval_context = SimpleNamespace(cancel=CancelToken(event),
                                           _approved_web_domains=set(), request_approval=ask)
        if not await _approve_domain(approval_context, call, "duckduckgo.com"):
            raise PermissionError("Search domain was not approved")

        def on_request(url):
            host = urlsplit(url).hostname
            if not host:
                return False
            future = asyncio.run_coroutine_threadsafe(
                _approve_domain(approval_context, call, host, url=url), loop)
            while not event.is_set():
                try:
                    return future.result(timeout=0.1)
                except concurrent.futures.TimeoutError:
                    continue
            future.cancel()
            return False

        class ApprovedFetcher(RequestsDocumentFetcher):
            def fetch(self, result, *, limits, token):
                return super().fetch(result, limits=limits, token=token, on_request=on_request)

        class ConsentedResearchModel(ApiResearchModel):
            def _chat(inner, **kwargs):
                snapshot = runtime.snapshot()
                if snapshot.use_api_mode:
                    provider = snapshot.api_provider_type
                    endpoint = snapshot.api_base_url or CLOUD_PROVIDERS[provider].base_url
                    if not self._consented(provider, endpoint):
                        raise ResearchFailure("Workflow cloud consent was revoked",
                                              code="consent_required", retryable=False)
                token.raise_if_cancelled()
                return super()._chat(**kwargs)

        service = WebResearchService(fetcher=ApprovedFetcher(),
                                     model=ConsentedResearchModel(runtime=runtime))
        limit = prepared.config()["max_sources"]
        request = WebResearchRequest(request_id=call.id, node_id=context.step_id, chat_epoch=0,
            original_query=query, limits=ResearchLimits(max_sources=limit, max_search_results=limit))
        result = await self._worker(event, service.run, request, token=token)
        return {"text": TextValue(result.answer_markdown),
                "sources": JsonValue([source.to_dict() for source in result.sources])}

    async def _image(self, adapter, prepared, context, event):
        from backend.fal_integration import FalClient
        from backend.api._settings_shared import run_locked
        from backend.fal_media_controller import _quote_cost
        from backend.fal_media_preview import safe_media_host, _image_bytes
        from backend.fal_results import result_assets

        model = prepared.config()["model_id"]
        prompt = prepared.inputs()["prompt"].text
        key = self.settings.get_fal_api_key()
        if not key:
            raise ValueError("Configure Fal media credentials")
        self._require_cloud("Fal", "https://queue.fal.run")
        client = FalClient(key)
        quote = await self._worker(event, _quote_cost, client, model)
        estimate = quote.get("estimate")
        if type(estimate) not in (int, float) or not math.isfinite(estimate) or estimate < 0 or not quote.get("currency"):
            raise ValueError("A current Fal price estimate is required")
        expires = time.monotonic() + 180
        fingerprint = adapter.fingerprint(prepared)
        await self._approve(context, "image.generate", {
            "fingerprint": fingerprint, "modelId": model, "prompt": prompt, **quote,
        })

        def generate():
            if event.is_set() or time.monotonic() > expires or key != self.settings.get_fal_api_key():
                raise PermissionError("Fal approval or credentials changed")
            self._require_cloud("Fal", "https://queue.fal.run")
            request = client.submit(model, {"prompt": prompt}, options={"store_io": False})
            request_id = request["request_id"]
            try:
                run_locked(self.settings.upsert_fal_job, model, request_id, "IN_QUEUE")
                deadline = time.monotonic() + 240
                while not event.is_set() and time.monotonic() < deadline:
                    status = client.status(model, request_id)
                    if status["status"] == "COMPLETED":
                        if status.get("error"):
                            raise RuntimeError("Fal generation failed")
                        payload = client.result(model, request_id)
                        run_locked(self.settings.upsert_fal_job, model, request_id, "COMPLETED", payload)
                        for asset in result_assets(payload):
                            if asset.get("kind") == "image" and safe_media_host(asset.get("url")):
                                data, media_type = _image_bytes(asset["url"])
                                return {"image": self._asset(data, "generated" + (
                                    mimetypes.guess_extension(media_type) or ".img"), media_type, image=True)}
                        raise ValueError("Fal did not return a supported image")
                    event.wait(1.5)
                raise RuntimeError("Fal generation stopped or timed out")
            finally:
                if event.is_set():
                    try:
                        client.cancel(model, request_id)
                    except Exception:
                        pass

        call = ExternalCall(kind="tool", provider_name="Fal", tool=model,
                            input_ref="workflow-input:" + fingerprint, input_hash=fingerprint)
        return await self._external(adapter, prepared, context, call,
                                    lambda _: self._worker(event, generate))

    async def _agent(self, adapter, prepared, context, event):
        if any(v is None for v in (self.dispatcher, self.document, self.bus, self.notifications_state)):
            raise RuntimeError("Workspace agent is not bound to this run")
        folder = Path(prepared.config()["folder"]).expanduser().resolve()
        if not folder.is_dir() or str(folder) not in self.settings.get_harness_trusted_dirs():
            raise PermissionError("Choose a trusted workspace folder")
        task = prepared.inputs()["task"].text
        await self._approve(context, "agent.run", {
            "fingerprint": adapter.fingerprint(prepared), "folder": str(folder),
            "task": task, "permissionMode": "ask",
        })
        # Ensure the active workspace model has consent before task start.
        runtime = self._research_runtime()
        if self.dispatcher._runs.is_busy("harness"):
            raise RuntimeError("A workspace agent is already running")

        def create():
            node = self.document.add_harness_node(0, 0, task, max_turns=16)
            node.state.harness_workspace_path = str(folder)
            node.state.harness_permission_mode = "ask"
            node.state.harness_shell_allow_rules = []
            node.state.harness_network_always = False
            return node

        node, _ = self.document.record_command("harnessCreate", "agent", create)
        request_id = await self.dispatcher.start_harness_run(
            bus=self.bus, notifications_state=self.notifications_state, document=self.document,
            harness_node_id=node.id, user_text=task, allow_session_grants=False,
            require_user_workspace=True, workflow_runtime=runtime,
        )
        if not request_id:
            raise RuntimeError("Could not start workspace agent")
        try:
            while True:
                current = self.document.nodes.get(node.id)
                if current is None or current.state.harness_run_id != request_id:
                    raise RuntimeError("Workspace agent run was replaced")
                state = current.state
                if state.harness_status == "done":
                    return {"text": TextValue(state.harness_reply)}
                if state.harness_status in ("failed", "stopped", "cancelled"):
                    raise RuntimeError("Workspace agent did not complete")
                await asyncio.sleep(0.15)
        except asyncio.CancelledError:
            event.set()
            self.dispatcher.cancel_harness(request_id)
            raise
