"""Manual, preview-bound execution of deterministic canvas transforms."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
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
    step_sources, validate_graph_run_ancestors,
)
from backend.domain.model import SceneError, SceneNode
from backend.domain.node_states import ModelState, PersistedPluginState, PluginStateSnapshot
from backend.domain.nodes_content import _text_digest
from backend.domain.table_workflows import table_output
from graphlink_plugins.scientific.data_table import TableState
from graphlink_plugins.scientific.graph_run_adapter import prepare as prepare_science
from graphlink_plugins.scientific.workbench import _run_token
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



def _state_data(node: SceneNode) -> dict[str, Any]:
    """A scientific node's state payload; a node without state cannot run."""
    if not isinstance(node.state, (PersistedPluginState, PluginStateSnapshot, TableState)):
        raise SceneError("The scientific node has no saved plugin state.")
    return node.state.data


def _adopt_scientific_result(
    document: SceneDocument, step: dict[str, Any], by_node_id: dict[str, Any],
    *, detail: str, elapsed_ms: int,
) -> bool:
    """Recover a committed result when its run receipt was not checkpointed.

    Check live inputs, configuration, ancestor receipts and the output before
    claiming completion. Used after dispatch, on resume, and when cancellation
    races with a calculation that has already committed.
    """
    try:
        validate_graph_run_ancestors(document, step, by_node_id)
        node = _matching_node(document, step)
        live = step.copy()
        _inspect(document, live)
        if (live["status"] != "current" or live["configHash"] != step["configHash"]
                or (step["inputHash"] and live["inputHash"] != step["inputHash"])):
            return False
        latest = _state_data(node).get("latest")
        if not isinstance(latest, dict):
            return False
        step.update(
            status="completed", detail=detail, inputHash=live["inputHash"],
            outputHash=_output_digest(node), scientificResult=latest,
            elapsedMs=elapsed_ms,
        )
        return True
    except SceneError:
        return False


async def _run_model_step(
    document: SceneDocument, confirmed_checkpoint: Callable[[], Awaitable[bool]],
    run: dict[str, Any], step: dict[str, Any], by_node_id: dict[str, Any],
) -> None:
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

async def _run_scientific_step(
    document: SceneDocument, bus: SessionBus, settings_manager: Any,
    run: dict[str, Any], step: dict[str, Any], by_node_id: dict[str, Any],
) -> None:
    """Run only an explicitly configured workbench through its existing
    granted, capped, killable PR 621 worker. No generic tool dispatch.
    """
    started = time.monotonic()

    def elapsed() -> int:
        return max(0, int((time.monotonic() - started) * 1000))

    validate_graph_run_ancestors(document, step, by_node_id)
    node = _matching_node(document, step)
    live = step.copy()
    _inspect(document, live)
    if live["configHash"] != step["configHash"]:
        raise SceneError("The scientific configuration changed since preview.")
    if step["inputHash"] and live["inputHash"] != step["inputHash"]:
        raise SceneError("The scientific inputs changed since preview.")
    step["inputHash"] = live["inputHash"]

    # A successful calculation may have committed just before an
    # interrupted receipt checkpoint. Do not repeat it on resume.
    if live["status"] == "current":
        if not _adopt_scientific_result(
            document, step, by_node_id,
            detail="Verified existing scientific result; not replayed.", elapsed_ms=elapsed(),
        ):
            raise SceneError("The committed scientific result could not be verified.")
        return
    if live["status"] != "ready" or live["targetHash"] != step["targetHash"]:
        raise SceneError("The scientific output changed since preview.")

    pack = node.kind.split(".", 1)[0]
    granted = lambda plugin_id: (
        settings_manager is not None
        and bool(settings_manager.get_plugin_grants().get(plugin_id, False))
    )
    operation, inputs, sources, input_hash, _same = prepare_science(document, node, granted)
    if input_hash != step["inputHash"]:
        raise SceneError("The scientific input fingerprint changed before dispatch.")
    answer = await bus.dispatch_intent("app-plugins", "invokePluginIntent", [
        pack, "run", {
            "node_id": node.id, "operation_id": operation.id,
            "inputs": inputs, "sources": sources,
            "expected_run": _run_token(_state_data(node)),
            "graph_run_id": run["runId"],
        },
    ])
    if not isinstance(answer, dict) or answer.get("ok") is not True:
        failure = answer if isinstance(answer, dict) else {}
        error_type = failure.get("errorType", "conflict" if failure.get("conflict") is True else "validation")
        if error_type not in {"timeout", "cancelled", "invalid_input", "worker_error", "validation", "conflict"}:
            error_type = "validation"
        measured = failure.get("elapsedMs")
        step.update(
            status="failed" if error_type != "cancelled" else "cancelled",
            errorType=error_type, elapsedMs=measured if type(measured) is int and measured >= 0 else elapsed(),
            detail=str(failure.get("error") or "Scientific execution was not authorized.")[:400],
        )
        return

    if not _adopt_scientific_result(
        document, step, by_node_id, detail="Scientific result recorded.",
        elapsed_ms=elapsed(),
    ):
        raise SceneError("The scientific result or its input changed during execution.")

def _make_graph_checkpoint(save_checkpoint: Callable[[], Awaitable[bool]]) -> Callable[[], Awaitable[None]]:
    """Serialize and shield all graph/journal SQLite checkpoint writes."""
    checkpoint_guard = asyncio.Lock()

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

    return checkpoint


def register_graph_runner(
    bus: SessionBus, document: SceneDocument, save_checkpoint: Callable[[], Awaitable[bool]], serialize_mutation: Callable,
    settings_manager: Any = None,
) -> None:
    active_run_id: str | None = None
    active_task: asyncio.Task[Any] | None = None
    cancel_requested = False
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

    checkpoint = _make_graph_checkpoint(save_checkpoint)

    async def confirmed_checkpoint() -> bool:
        await checkpoint()
        return True

    async def drive(run: dict[str, Any]):
        nonlocal active_run_id, active_task, cancel_requested
        active_run_id = run["runId"]
        active_task = asyncio.current_task()
        cancel_requested = False
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
            started_clock = time.monotonic()
            try:
                if "externalAttempt" in step:
                    # Even a corrupt/imported pending step with an old fence
                    # must never be sent again.
                    step.update(status="reconciliation-required", detail=RECONCILIATION_DETAIL)
                elif step["kind"] == "model":
                    try:
                        await _run_model_step(document, confirmed_checkpoint, run, step, by_node_id)
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
                elif step["kind"] == "scientific":
                    try:
                        await _run_scientific_step(document, bus, settings_manager, run, step, by_node_id)
                    except asyncio.CancelledError:
                        raise
                    except SceneError as exc:
                        step.update(status="blocked", detail=str(exc), errorType="validation",
                                    elapsedMs=max(0, int((time.monotonic() - started_clock) * 1000)))
                    except Exception:
                        step.update(status="failed", detail="The scientific runner could not finish.",
                                    errorType="worker_error",
                                    elapsedMs=max(0, int((time.monotonic() - started_clock) * 1000)))
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
                # The plugin can commit synchronously just before its awaited
                # dispatch returns. Never save a cancelled receipt over an
                # already committed scientific result, even on a user cancel.
                committed_science = (
                    step["kind"] == "scientific"
                    and _adopt_scientific_result(
                        document, step, by_node_id,
                        detail="Committed before cancellation; result preserved.",
                        elapsed_ms=max(0, int((time.monotonic() - started_clock) * 1000)),
                    )
                )
                if committed_science:
                    step["finishedAt"] = datetime.now(timezone.utc).isoformat()
                elif (cancel_requested and "externalAttempt" not in step
                        and step["status"] not in {"completed", "current"}):
                    step.update(
                        status="cancelled", detail="Graph run cancelled by the user.",
                        errorType="cancelled",
                        elapsedMs=max(0, int((time.monotonic() - started_clock) * 1000)),
                        finishedAt=datetime.now(timezone.utc).isoformat(),
                    )
                elif step["status"] == "pending" and "externalAttempt" not in step:
                    # A transport/task interruption (not user Run Cancel)
                    # leaves a pure, never-started operation resumable.
                    step.pop("startedAt", None)
                    step.pop("finishedAt", None)
                raise

        try:
            # Persist the approved plan before executing *any* step.
            await checkpoint()
            await schedule_graph_steps(
                run["steps"], max_concurrency=run.get("maxConcurrency", 3),
                execute=run_step, checkpoint_blocked=checkpoint_blocked,
                dependencies=step_sources,
            )
            if any(s["status"] == "reconciliation-required" for s in run["steps"]):
                run["status"] = "reconciliation-required"
            else:
                run["status"] = (
                    "partial" if any(s["status"] in {"blocked", "failed", "cancelled"} for s in run["steps"])
                    else "completed"
                )
            run["finishedAt"] = datetime.now(timezone.utc).isoformat()
            await checkpoint()
            terminal_saved = True
            return _public_run(run)
        except asyncio.CancelledError:
            if not cancel_requested:
                raise
            # The scheduler has drained workers (including PR 621's spawned
            # children). Preserve external reconciliation fences; mark all
            # unstarted local work terminal so Resume never replays it.
            for step in run["steps"]:
                if step["status"] == "pending":
                    step.update(
                        status="cancelled", detail="Graph run cancelled before this step started.",
                        errorType="cancelled", elapsedMs=0,
                        finishedAt=datetime.now(timezone.utc).isoformat(),
                    )
            run["status"] = "cancelled"
            run["finishedAt"] = datetime.now(timezone.utc).isoformat()
            await checkpoint()
            terminal_saved = True
            return _public_run(run)
        finally:
            # Every child and spawned worker is drained before releasing the
            # guard, even when the caller/task itself was cancelled.
            active_run_id = None
            active_task = None
            cancel_requested = False
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
            oldest_finished = next((item for item in document.graph_runs if item["status"] in {"completed", "partial", "cancelled"}), None)
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

    async def cancel_graph_run(run_id: str):
        """A separate intent: must NOT wait for the run mutation guard."""
        nonlocal cancel_requested
        if not RUN_ID.fullmatch(run_id):
            raise IntentValidationError(["Choose a valid graph run ID."])
        if active_run_id != run_id or active_task is None or active_task.done():
            return {"ok": False, "errorType": "not_running", "error": "That graph run is not active."}
        cancel_requested = True
        active_task.cancel()
        return {"ok": True}

    bus.register_intent("scene", "cancelGraphRun", cancel_graph_run, args_schema=ResumeGraphRunArgs)
    bus.register_intent("scene", "previewGraphRun", preview_graph_run, args_schema=GraphRunSelection)
    bus.register_intent("scene", "getGraphRun", get_graph_run, args_schema=GetGraphRunArgs)
    bus.register_intent("scene", "executeGraphRun", execute_graph_run, args_schema=ExecuteGraphRunArgs)
    bus.register_intent("scene", "resumeGraphRun", resume_graph_run, args_schema=ResumeGraphRunArgs)
