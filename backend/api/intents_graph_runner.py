"""Manual, preview-bound execution of deterministic canvas transforms."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.domain.graph import SceneDocument
from backend.domain.graph_run_external import RECONCILIATION_DETAIL, ExternalCall, dispatch_external_attempt
from backend.domain.graph_run_history import RUN_HISTORY_LIMIT, RUN_ID
from backend.domain.graph_run_inspector import build_graph_run_trace
from backend.domain.graph_runner import (
    _inspect, _matching_node, _output_digest, execute_graph_step, plan_graph_run, schedule_graph_steps,
    validate_graph_run_ancestors,
)
from backend.domain.model import SceneError
from backend.domain.node_states import ModelState
from backend.domain.nodes_content import _text_digest
from backend.domain.table_workflows import table_output
from backend.events import IntentValidationError, SessionBus


@dataclass
class GraphRunSelection:
    nodeIds: list[str]


@dataclass
class ExecuteGraphRunArgs(GraphRunSelection):
    previewToken: str
    operationId: str
    maxConcurrency: int | None = None


@dataclass
class ResumeGraphRunArgs:
    runId: str


@dataclass
class GetGraphRunArgs:
    pass


def _digest(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _public_step(step: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in step.items() if key not in {"targetHash", "configHash", "endpointHash"}}


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "runId": run["runId"], "status": run["status"], "startedAt": run["startedAt"],
        "maxConcurrency": run.get("maxConcurrency", 3),
        "steps": [_public_step(step) for step in run["steps"]],
        "trace": build_graph_run_trace(run),
    }


def _pin_output_version(document: SceneDocument, step: dict[str, Any]) -> None:
    """Capture the table's version during execution, never at inspection time."""
    node = document.nodes.get(step["nodeId"])
    output = table_output(node) if node is not None else None
    if output is not None:
        step["outputVersion"] = output.current.version


def _find_run(document: SceneDocument, run_id: str) -> dict[str, Any] | None:
    return next((run for run in document.graph_runs if run["runId"] == run_id), None)


def _plan(document: SceneDocument, node_ids: list[str]) -> tuple[str, list[dict[str, str]]]:
    try:
        steps = plan_graph_run(document, node_ids)
        model_steps = [s for s in steps if s["kind"] == "model" and s["status"] in {"ready", "waiting"}]
        if len(model_steps) > 8:
            raise SceneError("A graph run can issue at most eight local model calls.")
        # Existing current Model receipts are local data. They must
        # remain usable in deterministic branches even when Ollama is
        # offline; a new outbound call alone requires endpoint approval.
        if model_steps:
            from backend.api.intents_graph_model import _require_loopback_ollama
            endpoint = _require_loopback_ollama()
            for step in model_steps:
                step["endpointHash"] = _digest(["local_model_endpoint", endpoint])
    except SceneError as exc:
        raise IntentValidationError([str(exc)]) from exc
    token = _digest([str(document.graph_run_epoch), sorted(set(node_ids)), steps])
    return token, steps


def register_graph_runner(
    bus: SessionBus, document: SceneDocument, save_checkpoint: Callable[[], Awaitable[bool]], serialize_mutation: Callable,
) -> None:
    active_run_id: str | None = None
    checkpoint_guard = asyncio.Lock()

    def visible_run(run: dict[str, Any]) -> dict[str, Any]:
        result = _public_run(run)
        if run["runId"] in {active_run_id, getattr(bus, "graph_model_active_run_id", None)}:
            # The terminal SQLite write may still be in flight. A concurrent
            # reader must not receive a success receipt before it commits,
            # including an opt-in model step handled by the sibling intents.
            result["status"] = "running"
            result["trace"]["status"] = "running"
        return result

    async def preview_graph_run(node_ids: list[str]):
        token, steps = _plan(document, node_ids)
        return {"previewToken": token, "steps": [_public_step(step) for step in steps]}

    async def get_graph_run():
        if not document.graph_runs:
            return None
        latest = visible_run(document.graph_runs[-1])
        latest["history"] = [visible_run(run) for run in reversed(document.graph_runs[:-1])]
        return latest

    async def checkpoint():
        # Every worker shares the same graph and journal. Serialize SQLite
        # snapshots (including external pre-dispatch fences and receipts);
        # overlapping writes could otherwise commit out of order.
        async with checkpoint_guard:
            # A cancelled request must not release the graph's save guard
            # while its SQLite worker is still writing or adopting a row id.
            write = asyncio.ensure_future(save_checkpoint())
            try:
                ok = await asyncio.shield(write)
            except asyncio.CancelledError:
                await write
                raise
            if not ok:
                raise IntentValidationError(["Could not save the run checkpoint. Reopen Graph Runner to inspect or resume."])

    async def confirmed_checkpoint() -> bool:
        await checkpoint()
        return True

    async def run_model_step(run: dict[str, Any], step: dict[str, Any], by_node_id: dict[str, Any]) -> None:
        """One reviewed local Model step with a durable fence, never auto-retried."""
        # Imported at execution time to keep the existing GraphModel one-shot
        # API's dependency on _digest out of module import cycles.
        from backend.api import intents_graph_model as local

        validate_graph_run_ancestors(document, step, by_node_id)
        node = _matching_node(document, step)
        if not isinstance(node.state, ModelState):
            raise SceneError("This is not an executable Model node.")
        if any(
            prior is not step and prior.get("nodeId") == node.id
            and prior.get("status") == "reconciliation-required"
            for record in document.graph_runs for prior in record.get("steps", [])
        ):
            raise SceneError("This Model output has an unresolved request; reconcile it before another call.")
        endpoint = local._require_loopback_ollama()
        if step.get("endpointHash") != _digest(["local_model_endpoint", endpoint]):
            raise SceneError("The Ollama endpoint changed since preview. Preview the graph again.")
        live = step.copy()
        _inspect(document, live)
        if live["status"] not in {"ready", "current"}:
            raise SceneError("Model input cannot be executed in its current state.")
        for key in ("configHash", "targetHash"):
            if live[key] != step[key]:
                raise SceneError("A Model configuration or output changed since preview.")
        if step["inputHash"] and step["inputHash"] != live["inputHash"]:
            raise SceneError("The Model input changed since preview.")
        # Deferred hashes become concrete only after the upstream Model
        # completion is committed. Save it with the same request fence.
        step["inputHash"] = live["inputHash"]
        binding = node.state.model_binding
        identity = binding.binding_id
        model_id, provider = binding.model_id, binding.provider_name
        prompt = document._text_output(step["sourceId"])

        async def invoke(_request_id: str) -> Any:
            return await asyncio.to_thread(
                local.local_ollama_complete, prompt=prompt, model_id=model_id, endpoint=endpoint,
            )

        async def apply_result(payload: Any) -> str:
            text = local._response_text(payload)
            current = _matching_node(document, step)
            if not isinstance(current.state, ModelState):
                raise SceneError("The Model node changed during inference.")
            new_live = step.copy()
            _inspect(document, new_live)
            if any(new_live[key] != step[key] for key in ("inputHash", "configHash", "targetHash")):
                raise SceneError("The Model input, configuration or output changed during inference.")
            current_model_state = current.state
            if current_model_state.model_binding.binding_id != identity:
                raise SceneError("The Model port was rebound during inference.")
            output_hash = _text_digest(text)

            def apply() -> None:
                current.content = text
                current_model_state.model_binding.last_input_hash = step["inputHash"]
                current_model_state.model_binding.last_output_hash = output_hash

            document.record_command("runGraphStep", "user", apply, node_ids=(current.id,), run_id=run["runId"])
            return output_hash

        await dispatch_external_attempt(
            step, ExternalCall(
                kind="model", provider_name=provider, model_id=model_id,
                input_ref=f"graph-text:{step['sourceId']}:{step['inputHash']}",
                input_hash=step["inputHash"],
            ), checkpoint=confirmed_checkpoint, invoke=invoke, apply_result=apply_result,
        )

    async def drive(run: dict[str, Any]):
        nonlocal active_run_id
        active_run_id = run["runId"]
        run["status"] = "running"
        terminal_saved = False

        async def checkpoint_blocked() -> None:
            try:
                await checkpoint()
            finally:
                await bus.publish("scene")

        by_node_id = {step["nodeId"]: step for step in run["steps"]}

        async def run_step(step: dict[str, Any]) -> None:
            # Record when this task actually starts, not when it entered the
            # preview queue. On resume a pending step gets a new real start.
            step["startedAt"] = datetime.now(timezone.utc).isoformat()
            step.pop("finishedAt", None)
            try:
                if "externalAttempt" in step:
                    # Even a corrupt/imported pending step with an old fence
                    # must never be sent again.
                    step.update(status="reconciliation-required", detail=RECONCILIATION_DETAIL)
                elif step["kind"] == "model":
                    try:
                        await run_model_step(run, step, by_node_id)
                    except asyncio.CancelledError:
                        raise
                    except SceneError as exc:
                        if step["status"] != "reconciliation-required":
                            step.update(status="blocked", detail=str(exc))
                    except Exception:
                        # The external adapter owns the uncertainty fence. An
                        # unadmitted failure is local; a sent call is uncertain.
                        if "externalAttempt" not in step:
                            step.update(status="blocked", detail="Model dispatch could not start.")
                        elif step["status"] != "reconciliation-required":
                            step.update(status="reconciliation-required", detail=RECONCILIATION_DETAIL)
                elif step["kind"] == "tool":
                    step.update(status="blocked", detail="No Graph Runner tool adapter is authorized.")
                else:
                    try:
                        validate_graph_run_ancestors(document, step, by_node_id)
                        if not step["inputHash"]:
                            live = step.copy()
                            _inspect(document, live)
                            if live["configHash"] != step["configHash"] or live["targetHash"] != step["targetHash"]:
                                raise SceneError("The deferred output or configuration changed since preview.")
                            step["inputHash"] = live["inputHash"]
                        digest, _ = document.record_command(
                            "runGraphStep", "user", lambda: execute_graph_step(document, step),
                            node_ids=(step["nodeId"],), run_id=run["runId"],
                        )
                    except SceneError as exc:
                        step.update(status="blocked", detail=str(exc))
                    else:
                        step.update(status="completed", detail="Output updated.", outputHash=digest)
                        _pin_output_version(document, step)

                step.setdefault("finishedAt", datetime.now(timezone.utc).isoformat())
                # Transforms and receipts share a transaction. The Model
                # adapter additionally checkpointed its request before send.
                try:
                    await checkpoint()
                finally:
                    await bus.publish("scene")
            except asyncio.CancelledError:
                # An unadmitted pending step is safe to resume. Do not retain
                # its aborted scheduling timestamps as if it had finished.
                if step["status"] == "pending" and "externalAttempt" not in step:
                    step.pop("startedAt", None)
                    step.pop("finishedAt", None)
                raise

        try:
            # Persist the approved plan before executing *any* step.
            await checkpoint()
            await schedule_graph_steps(
                run["steps"], max_concurrency=run.get("maxConcurrency", 3),
                execute=run_step, checkpoint_blocked=checkpoint_blocked,
            )
            if any(s["status"] == "reconciliation-required" for s in run["steps"]):
                run["status"] = "reconciliation-required"
            else:
                run["status"] = (
                    "partial" if any(s["status"] in {"blocked", "failed"} for s in run["steps"])
                    else "completed"
                )
            run["finishedAt"] = datetime.now(timezone.utc).isoformat()
            await checkpoint()
            terminal_saved = True
            return _public_run(run)
        finally:
            # schedule_graph_steps has cancelled and drained every child before
            # the shared graph mutation guard can be released.
            active_run_id = None
            if not terminal_saved:
                run["status"] = "interrupted"
                run.pop("finishedAt", None)

    @serialize_mutation
    async def start_run(node_ids: list[str], preview_token: str, run_id: str, max_concurrency: int):
        token, steps = _plan(document, node_ids)
        if token != preview_token:
            raise IntentValidationError(["The graph or selection changed. Preview the run again."])
        if not any(step["status"] == "ready" for step in steps):
            raise IntentValidationError(["The preview contains no steps ready to run."])
        for step in steps:
            if step["status"] in {"ready", "waiting"}:
                step.update(status="pending", detail="Waiting to run.")
            elif step["status"] == "current":
                # A current step also has an inspectable content-addressed
                # version, even though it does not execute in this run.
                step["outputHash"] = _output_digest(document.nodes[step["nodeId"]])
                _pin_output_version(document, step)
        run = {
            "runId": run_id, "previewHash": token, "selectionHash": _digest(sorted(set(node_ids))),
            "startedAt": datetime.now(timezone.utc).isoformat(), "steps": steps, "status": "running",
            "maxConcurrency": max_concurrency,
        }
        if len(document.graph_runs) >= RUN_HISTORY_LIMIT:
            oldest_finished = next((item for item in document.graph_runs if item["status"] in {"completed", "partial"}), None)
            if oldest_finished is None:
                raise IntentValidationError(["Resume an interrupted run before starting another."])
            document.graph_runs.remove(oldest_finished)
        document.graph_runs.append(run)
        # Once accepted, an old preview cannot be reused under a new id, even
        # if undo restores identical inputs or the history entry ages out.
        document.graph_run_epoch = uuid.uuid4()
        return await drive(run)

    async def execute_graph_run(node_ids: list[str], preview_token: str, run_id: str, max_concurrency: int | None = None):
        max_concurrency = 3 if max_concurrency is None else max_concurrency
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= 8:
            raise IntentValidationError(["Graph Runner concurrency must be between 1 and 8."])
        if not RUN_ID.fullmatch(run_id):
            raise IntentValidationError(["Use an operation ID of 8–100 letters, digits, hyphens or underscores."])
        existing = _find_run(document, run_id)
        if existing is not None:
            if (existing["previewHash"] != preview_token
                    or existing["selectionHash"] != _digest(sorted(set(node_ids)))
                    or existing.get("maxConcurrency", 3) != max_concurrency):
                raise IntentValidationError(["This operation ID already belongs to another preview or concurrency limit."])
            return visible_run(existing)
        result = await start_run(node_ids, preview_token, run_id, max_concurrency)
        if result is None:
            raise IntentValidationError(["Another graph operation is in progress. Try again."])
        return result

    @serialize_mutation
    async def continue_run(run_id: str):
        run = _find_run(document, run_id)
        if run is None:
            raise IntentValidationError(["This run does not belong to the open graph."])
        if run["status"] == "interrupted":
            # Retry the terminal status checkpoint even when all individual
            # outputs were already committed; no step will be rerun.
            return await drive(run)
        if run["status"] == "reconciliation-required":
            # An uncertain remote step remains excluded (only 'pending'
            # steps run). Independent local pending steps may still finish.
            if any(step["status"] == "pending" for step in run["steps"]):
                return await drive(run)
        return _public_run(run)

    async def resume_graph_run(run_id: str):
        result = await continue_run(run_id)
        if result is None:
            raise IntentValidationError(["Another graph operation is in progress. Try again."])
        return result

    bus.register_intent("scene", "previewGraphRun", preview_graph_run, args_schema=GraphRunSelection)
    bus.register_intent("scene", "getGraphRun", get_graph_run, args_schema=GetGraphRunArgs)
    bus.register_intent("scene", "executeGraphRun", execute_graph_run, args_schema=ExecuteGraphRunArgs)
    bus.register_intent("scene", "resumeGraphRun", resume_graph_run, args_schema=ResumeGraphRunArgs)
