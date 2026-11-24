"""Read-only workflow journal, explicit content disclosure and canvas status.

The journal intentionally stores references, not bodies. A reveal is possible
only when a retained plan identifies the live node and its recorded content
fingerprint still matches. A pruned/remapped or edited result is unavailable,
never reconstructed from a different run.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.domain.graph import SceneDocument
from backend.domain.graph_runner import _inspect, _matching_node, _output_digest
from backend.domain.model import SceneError
from backend.domain.table_workflows import _current_result
from backend.domain.graph_run_history import RUN_ID
from backend.events import IntentValidationError, SessionBus
from graphlink_plugins.mcp_connect import core as mcp
from graphlink_plugins.scientific.graph_run_adapter import prepare as prepare_science


@dataclass
class HistoryArgs:
    offset: int | None = None


@dataclass
class RunDetailArgs:
    graphId: int
    runId: str


@dataclass
class StepDetailArgs(RunDetailArgs):
    stepIndex: int


@dataclass
class PreviewStepArgs:
    nodeIds: list[str]
    previewToken: str
    stepIndex: int
    scope: str | None = None
    sourceRunId: str | None = None


@dataclass
class NoArgs:
    pass


def _retained(document: SceneDocument, run_id: str) -> dict | None:
    return next((run for run in document.graph_runs if run["runId"] == run_id), None)


def _step_can_retry(document: SceneDocument, step: dict) -> bool:
    try:
        _matching_node(document, step)
        return True
    except SceneError:
        return False


def _input_body(document: SceneDocument, step: dict, expected: str | None) -> Any:
    if not expected:
        return None
    try:
        node = _matching_node(document, step)
        live = step.copy()
        _inspect(document, live)
        if live["inputHash"] != expected:
            return None
        if step["kind"] == "tool":
            prepared = mcp.prepare(document, node)
            return prepared.arguments if prepared.input_hash == expected else None
        if step["kind"] == "scientific":
            _, inputs, _, digest, _ = prepare_science(document, node, lambda _pack: True)
            return inputs if digest == expected else None
        if step["kind"] in {"note", "model"}:
            return document._text_output(step["sourceId"])
        _, output = document._require_table_output(step["nodeId"])
        table = document._read_table(step["sourceId"])
        a, b = table.columns(output.label_column, output.value_column)
        return {"columns": [output.label_column, output.value_column],
                "rows": [[row[a], row[b]] for row in table.rows]}
    except (SceneError, KeyError, ValueError, TypeError):
        return None


def _output_body(document: SceneDocument, step: dict, expected: str | None) -> Any:
    if not expected:
        return None
    try:
        node = _matching_node(document, step)
        if _output_digest(node) != expected:
            return None
        if step["kind"] == "scientific":
            from backend.api.intents_graph_runner import _state_data
            return _state_data(node).get("latest")
        if step["kind"] in {"note", "model", "tool"}:
            return node.content
        return _current_result(node)
    except (SceneError, KeyError, ValueError, TypeError):
        return None


def _status(step: dict, active: bool) -> str:
    if active and step.get("approval") == "pending":
        return "Waiting for approval"
    status = step["status"]
    if status == "pending":
        return "Running" if active and step.get("startedAt") else "Queued"
    return {
        "running": "Running", "current": "Skipped", "completed": "Done",
        "blocked": "Skipped", "failed": "Failed", "cancelled": "Cancelled",
        "reconciliation-required": "Failed",
    }.get(status, status)


def register_workflow_visibility(
    bus: SessionBus, document: SceneDocument, active_run: Callable[[], str | None],
) -> None:
    def check_graph(graph_id: int) -> None:
        if type(graph_id) is not int or document.current_chat_id != graph_id:
            raise IntentValidationError(["Open the workflow's saved graph again."])

    async def get_workflow_history(offset: int | None = None):
        from backend.chat_library import list_workflow_runs
        offset = 0 if offset is None else offset
        if type(offset) is not int or offset < 0:
            raise IntentValidationError(["Choose a valid history page."])
        graph_id = document.current_chat_id
        if graph_id is None:
            return {"graphId": None, "runs": [], "hasMore": False}
        rows = await asyncio.to_thread(list_workflow_runs, bus.chat_db_path, graph_id, offset, 51)
        check_graph(graph_id)
        return {"graphId": graph_id, "runs": rows[:50], "hasMore": len(rows) > 50}

    async def read_detail(graph_id: int, run_id: str):
        from backend.chat_library import load_workflow_run
        check_graph(graph_id)
        if not RUN_ID.fullmatch(run_id):
            raise IntentValidationError(["Choose a valid run ID."])
        receipt = await asyncio.to_thread(load_workflow_run, bus.chat_db_path, graph_id, run_id)
        check_graph(graph_id)
        if receipt is None:
            raise IntentValidationError(["This run does not belong to the open graph."])
        return receipt

    async def get_workflow_run_detail(graph_id: int, run_id: str):
        receipt = await read_detail(graph_id, run_id)
        plan = _retained(document, run_id)
        terminal = receipt["status"] in {"completed", "partial", "cancelled"}
        retained = plan is not None and len(plan["steps"]) == len(receipt["steps"])
        receipt["retained"] = retained
        for index, step in enumerate(receipt["steps"]):
            input_ref = json.loads(step["input_ref"])
            output_ref = json.loads(step["output_ref"]) if step["output_ref"] else None
            saved = plan["steps"][index] if retained else None
            # The journal is authoritative for status/usage; the retained plan
            # supplies the remapped live IDs, never free-form payloads.
            step["nodeId"] = saved["nodeId"] if saved else None
            step["sourceIds"] = (saved.get("sourceIds", [saved["sourceId"]]) if saved
                                 else input_ref["nodeIds"])
            step["input_ref"] = input_ref
            step["output_ref"] = output_ref
            step["retryable"] = bool(terminal and saved and _step_can_retry(document, saved))
        return receipt

    async def show_workflow_step(graph_id: int, run_id: str, index: int):
        receipt = await read_detail(graph_id, run_id)
        plan = _retained(document, run_id)
        if type(index) is not int or not 0 <= index < len(receipt["steps"]):
            raise IntentValidationError(["Choose a valid step."])
        if plan is None or len(plan["steps"]) != len(receipt["steps"]):
            return {"input": None, "output": None}
        step = plan["steps"][index]
        row = receipt["steps"][index]
        if step["kind"] != row["kind"] or step["operation"] != row["operation"]:
            return {"input": None, "output": None}
        expected_in = json.loads(row["input_ref"])["sha256"]
        expected_out = json.loads(row["output_ref"])["sha256"] if row["output_ref"] else None
        return {"input": _input_body(document, step, expected_in),
                "output": _output_body(document, step, expected_out)}

    async def show_workflow_preview_step(node_ids, token, index, scope=None, source_run_id=None):
        from backend.api.intents_graph_runner import _plan
        current, steps = _plan(document, node_ids, getattr(bus, "mcp_connect_runtime", None), scope, source_run_id)
        if current != token or type(index) is not int or not 0 <= index < len(steps):
            raise IntentValidationError(["The graph or preview changed. Preview the run again."])
        step = steps[index]
        return {"input": _input_body(document, step, step.get("inputHash")), "output": None}

    async def get_workflow_canvas_state():
        # Active/resumed runs take precedence over newer finished runs.
        active_ids = {active_run(), getattr(bus, "graph_model_active_run_id", None),
                      getattr(bus, "mcp_active_run_id", None)}
        runs = list(reversed(document.graph_runs))
        active = next((run for run in runs if run["runId"] in active_ids or run["status"] == "running"), None)
        if active is not None:
            runs = [active, *(run for run in runs if run is not active)]
        statuses: dict[str, str] = {}
        for run in runs:
            for step in run["steps"]:
                node_id = step["nodeId"]
                if node_id in document.nodes:
                    statuses.setdefault(node_id, _status(step, run is active))
        progress = None
        if active is not None:
            progress = {
                "runId": active["runId"],
                "total": len(active["steps"]),
                "finished": sum(s["status"] not in {"pending", "running"} for s in active["steps"]),
            }
        return {"statuses": statuses, "progress": progress}

    bus.register_intent("scene", "getWorkflowHistory", get_workflow_history, args_schema=HistoryArgs)
    bus.register_intent("scene", "getWorkflowRunDetail", get_workflow_run_detail, args_schema=RunDetailArgs)
    bus.register_intent("scene", "showWorkflowStep", show_workflow_step, args_schema=StepDetailArgs)
    bus.register_intent("scene", "showWorkflowPreviewStep", show_workflow_preview_step, args_schema=PreviewStepArgs)
    bus.register_intent("scene", "getWorkflowCanvasState", get_workflow_canvas_state, args_schema=NoArgs)
