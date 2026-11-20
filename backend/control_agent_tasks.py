"""Opt-in, narrowly scoped MCP control of LIVE workspace-agent tasks.

The only entry points are the app's existing harness/send and harness/cancel
intents. Never expose a generic intent dispatcher, question-answer endpoint,
approval decision, folder grant, or model/tool selection to MCP. The write
capability is required even to read task details.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import OrderedDict
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from backend.control_writes import scene_revision
from backend.domain.node_states import HarnessState
from backend.events import DEFAULT_SESSION_ID, EventBus
from backend.session_context import get_session_context

_MAX_RECEIPTS = 256
_MAX_REPLY = 16_000
_MAX_DETAIL = 2_000


class _StrictRequest(BaseModel):
    # Reject, rather than silently ignore, any attempt to smuggle approval,
    # workspace binding or other app-only parameters through these routes.
    model_config = ConfigDict(extra="forbid")


class StartAgentTask(_StrictRequest):
    operation_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    node_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=20_000)


class CancelAgentTask(_StrictRequest):
    node_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=100)


def _get_agent_node(document, node_id: str):
    node = document.nodes.get(node_id)
    if node is None or node.kind != "harness" or not isinstance(node.state, HarnessState):
        raise HTTPException(status_code=404, detail="unknown agent node on the live canvas")
    return node


def _task_snapshot(node) -> dict[str, Any]:
    state = node.state
    reply = str(state.harness_reply or "")
    steps = [
        {"text": str(step.get("text") or "")[:200], "status": str(step.get("status") or "pending")}
        for step in state.harness_plan[:20] if isinstance(step, dict)
    ]
    return {
        "source": "live",
        "node_id": node.id,
        "run_id": state.harness_run_id,
        "permission_mode": state.harness_task_permission_mode,
        "status": state.harness_status,
        "status_detail": str(state.harness_status_detail or "")[:_MAX_DETAIL],
        "checklist": steps,
        "reply": reply[:_MAX_REPLY],
        "reply_truncated": len(reply) > _MAX_REPLY,
        "spent_turns": state.harness_spent_turns,
        "max_turns": state.harness_max_turns,
        # An MCP caller can OBSERVE a pause, but only the real Graphlink UI
        # can approve/deny tools, answer questions, or grant a directory.
        "awaiting_app_approval": bool(state.harness_awaiting_approval),
        "awaiting_app_answer": bool(state.harness_awaiting_question),
    }


def register_control_agent_task_api(app: FastAPI, bus: EventBus) -> None:
    """Called only when the backend has an explicit MCP write token."""
    # Receipts survive state changes caused by a running agent (unlike a
    # scene revision), yet are bound to the current canvas lifetime and node
    # workspace identity so an old response cannot be replayed on a reload.
    receipts: OrderedDict[str, tuple[str, str, str, str]] = OrderedDict()
    start_lock = asyncio.Lock()

    @app.get("/api/control/agent/task")
    async def get_agent_task(
        node_id: str = Query(min_length=1, max_length=200),
        run_id: str | None = Query(default=None, min_length=1, max_length=100),
    ) -> dict[str, Any]:
        document = get_session_context(bus.session(DEFAULT_SESSION_ID)).canvas_document
        node = _get_agent_node(document, node_id)
        if run_id is not None and node.state.harness_run_id != run_id:
            raise HTTPException(status_code=409, detail="agent node has a different latest run")
        return _task_snapshot(node)

    @app.post("/api/control/agent/task/start")
    async def start_agent_task(body: StartAgentTask) -> dict[str, Any]:
        if not body.text.strip():
            raise HTTPException(status_code=422, detail="task text must not be blank")
        fingerprint = hashlib.sha256(json.dumps(
            body.model_dump(exclude={"operation_id"}),
            sort_keys=True, ensure_ascii=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()

        async with start_lock:
            session = bus.session(DEFAULT_SESSION_ID)
            context = get_session_context(session)
            document = context.canvas_document
            dispatcher = context.agent_dispatcher
            node = _get_agent_node(document, body.node_id)
            # Also initializes the revision epoch for this SceneDocument.
            current_revision = scene_revision(document)
            epoch = getattr(document, "_mcp_revision_epoch")
            previous = receipts.get(body.operation_id)
            if previous is not None:
                old_fingerprint, old_epoch, old_workspace, old_run = previous
                if old_fingerprint != fingerprint:
                    raise HTTPException(status_code=409, detail="operation_id reused with different input")
                if old_epoch != epoch or old_workspace != node.state.harness_workspace_id:
                    raise HTTPException(status_code=409, detail="canvas changed since agent task started")
                # A later task on the same node must not be mistaken for a
                # retry of the previous one, including the brief interval
                # before the new task's coroutine publishes its run_id.
                active = dispatcher._runs.get(old_run)
                if (node.state.harness_run_id != old_run
                        and (active is None or active.kind != "harness"
                             or active.node_id != node.id)):
                    raise HTTPException(status_code=409, detail="agent node has a newer task")
                if any(h.kind == "harness" and h.node_id == node.id and h.request_id != old_run
                       for h in dispatcher._runs.values()):
                    raise HTTPException(status_code=409, detail="agent node has a newer task")
                receipts.move_to_end(body.operation_id)
                return {"node_id": node.id, "run_id": old_run, "accepted": True, "replayed": True}

            if body.expected_revision != current_revision:
                raise HTTPException(status_code=409, detail="scene changed; reread graphlink_list_nodes")
            guard = getattr(session, "chat_mutation_guard", None)
            if guard and guard.get("active"):
                raise HTTPException(status_code=409, detail="canvas load/save or autosave is in progress")
            if node.pending_request_id or dispatcher._runs.is_busy("harness"):
                raise HTTPException(status_code=409, detail="an agent task is already in progress")

            # The app's existing validated send intent owns run claiming,
            # workspace trust checks and the human-only approval mechanism.
            # No MCP request can supply a workspace, tool grant or approval.
            # External MCP callers may control tasks, not inherit previous
            # in-app "approve for session" grants on this workspace.
            run_id = await session.dispatch_intent("harness", "send", [node.id, body.text.strip(), False])
            if not isinstance(run_id, str) or not run_id:
                raise HTTPException(status_code=409, detail="the agent task could not start")
            # start_harness_run claims a RunHandle synchronously but schedules
            # run_harness on the next event-loop turn. Expose that claim now
            # so an immediate MCP status/cancel cannot see the PREVIOUS
            # task's ID or state before the worker has started.
            handle = dispatcher._runs.get(run_id)
            if (handle is not None and handle.kind == "harness"
                    and handle.node_id == node.id and node.state.harness_run_id != run_id):
                node.state.harness_run_id = run_id
                node.state.harness_status = "running"
                node.state.harness_status_detail = ""
                node.state.harness_goal = body.text.strip()
                node.pending_request_id = run_id
            receipts[body.operation_id] = (
                fingerprint, epoch, node.state.harness_workspace_id, run_id,
            )
            if len(receipts) > _MAX_RECEIPTS:
                receipts.popitem(last=False)
            return {"node_id": node.id, "run_id": run_id, "accepted": True, "replayed": False}

    @app.post("/api/control/agent/task/cancel")
    async def cancel_agent_task(body: CancelAgentTask) -> dict[str, Any]:
        session = bus.session(DEFAULT_SESSION_ID)
        context = get_session_context(session)
        node = _get_agent_node(context.canvas_document, body.node_id)
        handle = context.agent_dispatcher._runs.get(body.run_id)
        if handle is None:
            if node.state.harness_run_id == body.run_id and node.state.harness_status in (
                "done", "failed", "stopped", "interrupted",
            ):
                return {"node_id": node.id, "run_id": body.run_id, "cancel_requested": False}
            raise HTTPException(status_code=409, detail="agent task is not running on this node")
        if handle.kind != "harness" or handle.node_id != node.id:
            raise HTTPException(status_code=409, detail="run does not belong to this agent node")
        await session.dispatch_intent("harness", "cancel", [body.run_id])
        return {"node_id": node.id, "run_id": body.run_id, "cancel_requested": True}
