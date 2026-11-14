"""Explicit local-model graph run: preview, durable dispatch, and output receipt.

This is intentionally narrower than a general model node. One source text
node feeds one unbound plain note, and the user must explicitly run it from
Graph Runner. Only local Ollama is permitted; cloud and tool adapters require
separate approval, spending and reconciliation protocols.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import api_provider
from backend.api.intents_graph_runner import _digest, _public_run, _public_step
from backend.domain.graph import SceneDocument
from backend.domain.graph_run_external import ExternalCall, dispatch_external_attempt
from backend.domain.graph_run_history import RUN_HISTORY_LIMIT, RUN_ID
from backend.domain.model import SceneError
from backend.domain.nodes_content import _text_digest
from backend.events import IntentValidationError, SessionBus
from graphlink_model_catalog import ModelRef
from graphlink_task_config import LOCAL_PROVIDER_OLLAMA, TASK_CHAT

_MAX_INPUT_BYTES = 65536
_MAX_OUTPUT_CHARS = 200000
_MODEL_ID = re.compile(r"[A-Za-z0-9._:/@+-]{1,192}")


def _require_loopback_ollama() -> None:
    """Ollama's SDK obeys OLLAMA_HOST; local-only must mean loopback."""
    configured = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    url = configured if "://" in configured else f"http://{configured}"
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
            or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise SceneError("This graph model step requires a loopback Ollama endpoint; check OLLAMA_HOST.")


@dataclass
class GraphModelSelection:
    sourceId: str
    targetId: str
    provider: str
    modelId: str


@dataclass
class ExecuteGraphModelArgs(GraphModelSelection):
    previewToken: str
    operationId: str


def model_step_preview(
    document: SceneDocument, source_id: str, target_id: str, provider: str, model_id: str,
) -> tuple[str, dict[str, str], str]:
    """Validate and freeze what a one-shot model call would consume and replace."""
    if provider != LOCAL_PROVIDER_OLLAMA:
        raise SceneError("Graph Runner currently permits only explicitly selected local Ollama models.")
    if not isinstance(model_id, str) or not _MODEL_ID.fullmatch(model_id):
        raise SceneError("Choose a valid installed Ollama model ID (1–192 safe characters).")
    _require_loopback_ollama()
    if source_id == target_id:
        raise SceneError("A model's source and output must be different nodes.")
    source_text = document._text_output(source_id)
    target = document._plain_note(target_id)
    if target.state.text_input_source_id or target.state.table_output is not None:
        raise SceneError("Choose an unbound plain note for the model output.")
    if not source_text.strip() or len(source_text.encode("utf-8", errors="surrogatepass")) > _MAX_INPUT_BYTES:
        raise SceneError("Model input must contain text and be at most 64 KiB.")
    fingerprint = _text_digest(source_text)
    step = {
        "nodeId": target_id, "sourceId": source_id,
        "kind": "model", "operation": "model",
        "label": target.title or "Model output",
        "status": "ready",
        "detail": f"Local Ollama model: {model_id}. One explicit run; no cloud request.",
        "inputHash": fingerprint,
        "configHash": _digest(["local_model_v1", provider, model_id]),
        "targetHash": _text_digest(target.content),
    }
    token = _digest([str(document.graph_run_epoch), step])
    return token, step, source_text


def _response_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise SceneError("The local model returned an invalid response.")
    message = payload.get("message")
    text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(text, str) or not text.strip() or len(text) > _MAX_OUTPUT_CHARS:
        raise SceneError("The local model returned no usable text or exceeded the output limit.")
    return text


def register_graph_model_runner(
    bus: SessionBus,
    document: SceneDocument,
    save_checkpoint: Callable[[], Awaitable[bool]],
    serialize_mutation: Callable,
    *,
    provider_runtime: api_provider.ProviderRuntime | None = None,
) -> None:
    """Install the two opt-in intents alongside the existing local Graph Runner."""

    def plan(selection: GraphModelSelection) -> tuple[str, dict[str, str], str]:
        try:
            return model_step_preview(
                document, selection.sourceId, selection.targetId, selection.provider, selection.modelId,
            )
        except SceneError as exc:
            raise IntentValidationError([str(exc)]) from exc

    async def preview_graph_model(source_id: str, target_id: str, provider: str, model_id: str):
        token, step, _prompt = plan(GraphModelSelection(source_id, target_id, provider, model_id))
        return {"previewToken": token, "steps": [_public_step(step)]}

    async def checkpoint() -> bool:
        # Session's actual graph and run journal share one SQLite transaction.
        write = asyncio.ensure_future(save_checkpoint())
        try:
            saved = await asyncio.shield(write)
        except asyncio.CancelledError:
            await write
            raise
        if saved is not True:
            raise IntentValidationError(["Could not save the model request checkpoint; no call was started."])
        return True

    @serialize_mutation
    async def start_model(selection: GraphModelSelection, preview_token: str, run_id: str):
        token, step, prompt = plan(selection)
        if token != preview_token:
            raise IntentValidationError(["The source, destination or model changed. Preview the model step again."])
        # An uncertain request targeting this note must be reconciled before
        # a fresh attempt, even if the source has since been edited.
        if any(
            prior.get("nodeId") == selection.targetId and
            prior.get("status") == "reconciliation-required"
            for run in document.graph_runs for prior in run.get("steps", [])
        ):
            raise IntentValidationError(["This output has an unresolved external request. Inspect its request ID first."])
        step.update(status="pending", detail="Waiting for the durable request checkpoint.")
        if len(document.graph_runs) >= RUN_HISTORY_LIMIT:
            finished = next((item for item in document.graph_runs if item["status"] in {"completed", "partial"}), None)
            if finished is None:
                raise IntentValidationError(["Run history is full of unfinished work. Reconcile an earlier run."])
            document.graph_runs.remove(finished)
        run = {
            "runId": run_id, "previewHash": token, "selectionHash": _digest([
                selection.sourceId, selection.targetId, selection.provider, selection.modelId,
            ]),
            "startedAt": datetime.now(timezone.utc).isoformat(),
            "status": "running", "steps": [step],
        }
        document.graph_runs.append(run)
        document.graph_run_epoch = uuid.uuid4()

        async def invoke(_request_id: str) -> Any:
            # A fixed ModelRef means no auto-selection or cross-provider
            # fallback. Only local Ollama is allowed until approval and cost
            # policy are implemented for paid cloud models.
            _require_loopback_ollama()
            kwargs: dict[str, Any] = {"model_ref": ModelRef(selection.provider, selection.modelId)}
            if provider_runtime is not None:
                kwargs["runtime"] = provider_runtime
            return await asyncio.to_thread(
                api_provider.chat, TASK_CHAT, [{"role": "user", "content": prompt}], **kwargs,
            )

        async def apply_result(payload: Any) -> str:
            text = _response_text(payload)
            # We are still inside the session mutation guard. The output,
            # undo record and completion receipt persist in one graph save.
            document.record_command(
                "runGraphModel", "user",
                lambda: document.set_note_content(selection.targetId, text),
                node_ids=(selection.targetId,), run_id=run_id,
            )
            return _text_digest(text)

        try:
            await dispatch_external_attempt(
                step,
                ExternalCall(
                    kind="model", provider=selection.provider, model=selection.modelId,
                    input_ref=f"graph-text:{selection.sourceId}:{step['inputHash']}",
                    input_hash=step["inputHash"],
                ),
                checkpoint=checkpoint, invoke=invoke, apply_result=apply_result,
            )
            run["status"] = "completed"
            await checkpoint()
            return _public_run(run)
        except BaseException:
            # The helper has already preserved the outbound request fence. A
            # completed output remains committed if the terminal status save
            # fails; resume never resends this model call.
            run["status"] = (
                "reconciliation-required" if step["status"] == "reconciliation-required"
                else "interrupted"
            )
            raise
        finally:
            await bus.publish("scene")

    async def execute_graph_model(
        source_id: str, target_id: str, provider: str, model_id: str, preview_token: str, operation_id: str,
    ):
        if not RUN_ID.fullmatch(operation_id):
            raise IntentValidationError(["Use an operation ID of 8–100 letters, digits, hyphens or underscores."])
        fingerprint = _digest([source_id, target_id, provider, model_id])
        existing = next((run for run in document.graph_runs if run["runId"] == operation_id), None)
        if existing is not None:
            if existing["previewHash"] != preview_token or existing["selectionHash"] != fingerprint:
                raise IntentValidationError(["This operation ID already belongs to another model preview."])
            return _public_run(existing)
        result = await start_model(
            GraphModelSelection(source_id, target_id, provider, model_id), preview_token, operation_id,
        )
        if result is None:
            raise IntentValidationError(["Another graph operation is in progress. Try again."])
        return result

    bus.register_intent(
        "scene", "previewGraphModel", preview_graph_model, args_schema=GraphModelSelection,
    )
    bus.register_intent(
        "scene", "executeGraphModel", execute_graph_model, args_schema=ExecuteGraphModelArgs,
    )
