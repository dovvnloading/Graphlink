"""Manual, preview-bound execution of deterministic canvas transforms."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.domain.graph import SceneDocument
from backend.domain.graph_run_external import RECONCILIATION_DETAIL, drain_cancelled_task
from backend.domain.graph_run_history import RUN_HISTORY_LIMIT, RUN_ID, can_prune_run
from backend.domain.graph_run_inspector import build_graph_run_trace
from backend.domain.graph_runner import plan_graph_run, schedule_graph_steps, step_sources
from backend.domain.model import SceneError
from backend.workflow.graph_execution import GraphRunExecution
from backend.workflow.graph_steps import _output_digest, adapter_for_step, pin_output_version as _pin_output_version
from backend.workflow.model_note_adapter import _digest as _digest
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


def _public_step(step: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in step.items() if key not in {"targetHash", "configHash", "endpointHash"}}


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "runId": run["runId"], "status": run["status"], "startedAt": run["startedAt"],
        "maxConcurrency": run.get("maxConcurrency", 3),
        "steps": [_public_step(step) for step in run["steps"]],
        "trace": build_graph_run_trace(run),
    }


def _find_run(document: SceneDocument, run_id: str) -> dict[str, Any] | None:
    return next((run for run in document.graph_runs if run["runId"] == run_id), None)


def _plan(document: SceneDocument, node_ids: list[str], mcp_runtime: Any = None) -> tuple[str, list[dict[str, str]]]:
    try:
        steps = plan_graph_run(document, node_ids)
        for step in steps:
            adapter = adapter_for_step(step)
            if adapter.pin_preview is not None and step["status"] in {"ready", "waiting"}:
                adapter.pin_preview(document.nodes[step["nodeId"]], step, mcp_runtime)
        model_steps = [s for s in steps if adapter_for_step(s).local_model and s["status"] in {"ready", "waiting"}]
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
                await drain_cancelled_task(write)
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
        if run["runId"] in {active_run_id, getattr(bus, "graph_model_active_run_id", None), getattr(bus, "mcp_active_run_id", None)}:
            # The terminal SQLite write may still be in flight. A concurrent
            # reader must not receive a success receipt before it commits,
            # including an opt-in model step handled by the sibling intents.
            result["status"] = "running"
            result["trace"]["status"] = "running"
        return result

    async def preview_graph_run(node_ids: list[str]):
        token, steps = _plan(document, node_ids, getattr(bus, "mcp_connect_runtime", None))
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

        def record_step(operation: Callable, node_ids: tuple[str, ...]):
            return document.record_command("runGraphStep", "user", operation, node_ids=node_ids, run_id=run["runId"])

        async def run_step(step: dict[str, Any]) -> None:
            # Record when this task actually starts, not when it entered the
            # preview queue. On resume a pending step gets a new real start.
            if step.get("startedAt"):
                step["retryCount"] = step.get("retryCount", 0) + 1
            step["startedAt"] = datetime.now(timezone.utc).isoformat()
            step.pop("finishedAt", None)
            started_clock = time.monotonic()
            adapter = adapter_for_step(step)
            execution = GraphRunExecution(
                document, bus, settings_manager, confirmed_checkpoint, run, by_node_id, record_step, started_clock,
            )
            try:
                if "externalAttempt" in step:
                    # Even a corrupt/imported pending step with an old fence
                    # must never be sent again.
                    step.update(status="reconciliation-required", detail=RECONCILIATION_DETAIL)
                else:
                    await adapter.run(execution, step)

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
                    adapter.recover_cancelled is not None and adapter.recover_cancelled(execution, step)
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
            run["status"] = ("reconciliation-required" if any(
                step["status"] == "reconciliation-required" for step in run["steps"]
            ) else "cancelled")
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
        token, steps = _plan(document, node_ids, getattr(bus, "mcp_connect_runtime", None))
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
            oldest_finished = next((item for item in document.graph_runs if can_prune_run(item, document.graph_runs)), None)
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
