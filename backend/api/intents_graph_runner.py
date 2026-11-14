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
from backend.domain.graph_run_external import RECONCILIATION_DETAIL
from backend.domain.graph_run_history import RUN_HISTORY_LIMIT, RUN_ID
from backend.domain.graph_runner import execute_graph_step, plan_graph_run, validate_graph_run_ancestors
from backend.domain.model import SceneError
from backend.events import IntentValidationError, SessionBus


@dataclass
class GraphRunSelection:
    nodeIds: list[str]


@dataclass
class ExecuteGraphRunArgs(GraphRunSelection):
    previewToken: str
    operationId: str


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
    return {key: value for key, value in step.items() if key not in {"targetHash", "configHash"}}


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "runId": run["runId"], "status": run["status"], "startedAt": run["startedAt"],
        "steps": [_public_step(step) for step in run["steps"]],
    }


def register_graph_runner(
    bus: SessionBus, document: SceneDocument, save_checkpoint: Callable[[], Awaitable[bool]], serialize_mutation: Callable,
) -> None:
    active_run_id: str | None = None

    def visible_run(run: dict[str, Any]) -> dict[str, Any]:
        result = _public_run(run)
        if run["runId"] in {active_run_id, getattr(bus, "graph_model_active_run_id", None)}:
            # The terminal SQLite write may still be in flight. A concurrent
            # reader must not receive a success receipt before it commits,
            # including an opt-in model step handled by the sibling intents.
            result["status"] = "running"
        return result

    def plan(node_ids: list[str]) -> tuple[str, list[dict[str, str]]]:
        try:
            steps = plan_graph_run(document, node_ids)
        except SceneError as exc:
            raise IntentValidationError([str(exc)]) from exc
        token = _digest([str(document.graph_run_epoch), sorted(set(node_ids)), steps])
        return token, steps

    async def preview_graph_run(node_ids: list[str]):
        token, steps = plan(node_ids)
        return {"previewToken": token, "steps": [_public_step(step) for step in steps]}

    async def get_graph_run():
        if not document.graph_runs:
            return None
        latest = visible_run(document.graph_runs[-1])
        latest["history"] = [visible_run(run) for run in reversed(document.graph_runs[:-1])]
        return latest

    def find_run(run_id: str):
        return next((run for run in document.graph_runs if run["runId"] == run_id), None)

    async def checkpoint():
        # A cancelled WebSocket request must not release the graph's save guard
        # while its SQLite worker still writes or is adopting an inserted row id.
        write = asyncio.ensure_future(save_checkpoint())
        try:
            ok = await asyncio.shield(write)
        except asyncio.CancelledError:
            await write
            raise
        if not ok:
            raise IntentValidationError(["Could not save the run checkpoint. Reopen Graph Runner to inspect or resume."])

    async def drive(run: dict[str, Any]):
        nonlocal active_run_id
        active_run_id = run["runId"]
        run["status"] = "running"
        terminal_saved = False
        try:
            # Persist the pinned plan BEFORE the first transform. Also flush an
            # in-memory output left by a failed write before resuming further.
            await checkpoint()
            by_node_id = {step["nodeId"]: step for step in run["steps"]}
            for step in run["steps"]:
                if step["status"] != "pending":
                    continue
                if "externalAttempt" in step:
                    # A recovered outbound request cannot pass through the
                    # deterministic transform runner or be sent a second time.
                    step.update(status="reconciliation-required", detail=RECONCILIATION_DETAIL)
                    await checkpoint()
                    await bus.publish("scene")
                    continue
                if step["kind"] in {"model", "tool"}:
                    # Adapters do not exist yet. Never fall through to local
                    # note/table evaluation or accidentally dispatch a call.
                    step.update(status="blocked", detail="No external Graph Runner adapter is configured.")
                    await checkpoint()
                    await bus.publish("scene")
                    continue
                try:
                    validate_graph_run_ancestors(document, step, by_node_id)
                    digest, _ = document.record_command(
                        "runGraphStep", "user", lambda: execute_graph_step(document, step),
                        node_ids=(step["nodeId"],), run_id=run["runId"],
                    )
                except SceneError as exc:
                    step.update(status="blocked", detail=str(exc))
                else:
                    step.update(status="completed", detail="Output updated.", outputHash=digest)
                # save_chat writes the graph, notes and journal in one SQLite
                # transaction. Restart sees either side of the transform intact.
                try:
                    await checkpoint()
                finally:
                    # The live graph retains both output and receipt on a failed
                    # disk write; keep other attached canvases in sync with it.
                    await bus.publish("scene")
            if any(s["status"] == "reconciliation-required" for s in run["steps"]):
                run["status"] = "reconciliation-required"
            else:
                run["status"] = (
                    "partial" if any(s["status"] in {"blocked", "failed"} for s in run["steps"])
                    else "completed"
                )
            await checkpoint()
            terminal_saved = True
            return _public_run(run)
        finally:
            # If the terminal write failed, resume must flush it again.
            active_run_id = None
            if not terminal_saved:
                run["status"] = "interrupted"

    @serialize_mutation
    async def start_run(node_ids: list[str], preview_token: str, run_id: str):
        token, steps = plan(node_ids)
        if token != preview_token:
            raise IntentValidationError(["The graph or selection changed. Preview the run again."])
        if not any(step["status"] == "ready" for step in steps):
            raise IntentValidationError(["The preview contains no steps ready to run."])
        for step in steps:
            if step["status"] == "ready":
                step.update(status="pending", detail="Waiting to run.")
        run = {
            "runId": run_id, "previewHash": token, "selectionHash": _digest(sorted(set(node_ids))),
            "startedAt": datetime.now(timezone.utc).isoformat(), "steps": steps, "status": "running",
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

    async def execute_graph_run(node_ids: list[str], preview_token: str, run_id: str):
        if not RUN_ID.fullmatch(run_id):
            raise IntentValidationError(["Use an operation ID of 8–100 letters, digits, hyphens or underscores."])
        existing = find_run(run_id)
        if existing is not None:
            if existing["previewHash"] != preview_token or existing["selectionHash"] != _digest(sorted(set(node_ids))):
                raise IntentValidationError(["This operation ID already belongs to another preview."])
            return visible_run(existing)
        result = await start_run(node_ids, preview_token, run_id)
        if result is None:
            raise IntentValidationError(["Another graph operation is in progress. Try again."])
        return result

    @serialize_mutation
    async def continue_run(run_id: str):
        run = find_run(run_id)
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
