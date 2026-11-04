"""Opt-in, revisioned MCP graph writes on the existing LIVE canvas.

Only the three additive/content-replacement graph operations and a guarded
save are exposed. No arbitrary SessionBus intent dispatch, deletion, code
execution, workspace switching, or automatic approval of harness actions.

The write token is provisioned separately by headless.main() and is accepted
ONLY for two explicit POST routes by backend/app.py's auth middleware.

Each mutation requires an exact SHA-256 snapshot revision from the existing
read-only graphlink_list_nodes result. The revision covers the actual scene
payload AND current graph/workspace IDs, so an unrelated UI edit or load
between observation and mutation refuses with 409 instead of guessing.

Operation IDs are replay-safe within this process; after restart, an old
revision rejects a repeated mutation rather than blindly duplicating it.
SAVE reuses app-chat-library/saveChat's existing autosave/optimistic DB guard.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections import OrderedDict
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from backend.events import DEFAULT_SESSION_ID, EventBus
from backend.providers.base import ToolCall
from backend.session_context import get_session_context
from backend.tools import GRAPH_MUTATE, RunContext
from backend.tools_graph import (
    make_connect_handler,
    make_create_node_handler,
    make_set_node_content_handler,
)

DEFAULT_WRITE_TOKEN_FILE = Path.home() / ".graphlink" / "headless.write.token"
_WRITE_ROUTES = frozenset({
    "/api/control/scene/mutate",
    "/api/control/scene/save",
})
_MAX_RECEIPTS = 256
_ALLOWED_NODE_KINDS = frozenset({"chat", "note", "code", "document"})
_ALLOWED_FIELDS = {
    "create_node": frozenset({"kind", "parent_id", "title", "content", "language", "is_user"}),
    "connect_nodes": frozenset({"source_id", "target_id"}),
    "set_node_content": frozenset({"node_id", "content"}),
}


def is_write_control_request(method: str, path: str) -> bool:
    """Fail closed for any future route not expressly approved here."""
    return method == "POST" and path in _WRITE_ROUTES


def scene_revision(document) -> str:
    """A reproducible revision covering UI edits, graph identity, and wiring."""
    # A digest of scene content alone is NOT a safe revision across
    # restarts: the new process would start on another identical empty
    # canvas and could accept a pre-crash create-node retry as a NEW write.
    # Bind the revision to this SceneDocument's lifetime as well.
    epoch = getattr(document, "_mcp_revision_epoch", None)
    if epoch is None:
        epoch = secrets.token_hex(16)
        setattr(document, "_mcp_revision_epoch", epoch)
    snapshot = {
        "epoch": epoch,
        "scene": document.scene_payload(),
        "chat_id": document.current_chat_id,
        "workspace_id": document.current_workspace_id,
    }
    encoded = json.dumps(
        snapshot, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class _BaseWrite(BaseModel):
    operation_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class MutationRequest(_BaseWrite):
    operation: Literal["create_node", "connect_nodes", "set_node_content"]
    arguments: dict[str, Any]


class SaveRequest(_BaseWrite):
    pass


async def _deny_approval(_call: ToolCall) -> bool:
    # The control API is NOT a pathway to the harness approval system.
    return False


def _validate_arguments(operation: str, arguments: dict[str, Any]) -> None:
    if set(arguments) - _ALLOWED_FIELDS[operation]:
        raise HTTPException(status_code=422, detail="unexpected graph mutation fields")
    required: tuple[str, ...]
    if operation == "create_node":
        kind = arguments.get("kind")
        if not isinstance(kind, str) or kind not in _ALLOWED_NODE_KINDS:
            raise HTTPException(status_code=422, detail="unsupported node kind for MCP writes")
        required = ("kind",)
    elif operation == "connect_nodes":
        required = ("source_id", "target_id")
    else:
        required = ("node_id", "content")
    if any(k not in arguments for k in required):
        raise HTTPException(status_code=422, detail="missing required graph mutation fields")
    for name, value in arguments.items():
        if name == "is_user":
            if not isinstance(value, bool):
                raise HTTPException(status_code=422, detail="is_user must be a boolean")
        elif not isinstance(value, str):
            raise HTTPException(status_code=422, detail=f"{name} must be a string")
        elif len(value) > (20000 if name == "content" else 200):
            raise HTTPException(status_code=422, detail=f"{name} exceeds size limit")
    if operation == "create_node" and arguments["kind"] == "document":
        if not arguments.get("title") or not arguments.get("parent_id"):
            raise HTTPException(status_code=422, detail="document requires title and parent_id")


def register_control_write_api(app: FastAPI, bus: EventBus) -> None:
    """Mount only approved writes; called only when a write token is configured."""
    # A successful operation receipt is valid only for the identical
    # canvas snapshot (including its lifetime epoch). A different graph or
    # an intervening edit must not replay a stale success.
    receipts: OrderedDict[str, tuple[str, dict[str, Any], str]] = OrderedDict()
    write_lock = asyncio.Lock()

    def replay_or_conflict(operation_id: str, fingerprint: str) -> dict[str, Any] | None:
        entry = receipts.get(operation_id)
        if entry is None:
            return None
        if entry[0] != fingerprint:
            raise HTTPException(status_code=409, detail="operation_id reused with different input")
        document = get_session_context(bus.session(DEFAULT_SESSION_ID)).canvas_document
        if scene_revision(document) != entry[2]:
            # Without this, a retry after loading another canvas would
            # report a successful write/save to the PREVIOUS canvas, and
            # return a node ID that does not exist in the current one.
            raise HTTPException(status_code=409, detail="canvas changed since operation; reread graphlink_list_nodes")
        return {**entry[1], "replayed": True}

    def record(operation_id: str, fingerprint: str, result: dict[str, Any]) -> None:
        document = get_session_context(bus.session(DEFAULT_SESSION_ID)).canvas_document
        receipts[operation_id] = (fingerprint, result, scene_revision(document))
        receipts.move_to_end(operation_id)
        if len(receipts) > _MAX_RECEIPTS:
            receipts.popitem(last=False)

    def guard_revision(expected: str):
        session = bus.session(DEFAULT_SESSION_ID)
        document = get_session_context(session).canvas_document
        actual = scene_revision(document)
        if expected != actual:
            raise HTTPException(status_code=409, detail="scene changed; reread graphlink_list_nodes")
        return session, document

    @app.post("/api/control/scene/mutate")
    async def mutate_scene(body: MutationRequest) -> dict[str, Any]:
        args = body.arguments
        _validate_arguments(body.operation, args)
        # Include all inputs in the receipt's fingerprint, including
        # expected_revision, so a retry cannot accidentally change its
        # semantics under the same operation_id.
        fingerprint = hashlib.sha256(json.dumps(
            body.model_dump(exclude={"operation_id"}), sort_keys=True,
            ensure_ascii=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        async with write_lock:
            replay = replay_or_conflict(body.operation_id, fingerprint)
            if replay is not None:
                return replay
            session, document = guard_revision(body.expected_revision)
            busy = session.chat_mutation_guard
            if busy and busy.get("active"):
                raise HTTPException(status_code=409, detail="canvas load/save or autosave is in progress")
            if body.operation == "set_node_content":
                node = document.nodes.get(args["node_id"])
                if node is not None and node.kind not in _ALLOWED_NODE_KINDS:
                    raise HTTPException(
                        status_code=403,
                        detail="MCP write scope cannot modify this node kind",
                    )
            handlers = {
                "create_node": make_create_node_handler,
                "connect_nodes": make_connect_handler,
                "set_node_content": make_set_node_content_handler,
            }
            handler = handlers[body.operation](document)
            context = RunContext(
                granted_scopes=frozenset({GRAPH_MUTATE}), request_approval=_deny_approval,
            )
            # Every allowed handler performs a synchronous SceneDocument
            # record_command, without awaiting, so no other request can
            # interleave between the revision check and the mutation.
            result = await handler(ToolCall(
                id=body.operation_id, name=f"graph.{body.operation}",
                arguments=args,
            ), context)
            if result.is_error:
                raise HTTPException(status_code=400, detail=result.content[:500])
            payload = json.loads(result.content)
            response = {
                "operation_id": body.operation_id,
                "operation": body.operation,
                "result": payload,
                "revision": scene_revision(document),
                "saved": False,
                "replayed": False,
            }
            record(body.operation_id, fingerprint, response)
            await session.publish("scene")
            return response

    @app.post("/api/control/scene/save")
    async def save_scene(body: SaveRequest) -> dict[str, Any]:
        fingerprint = body.expected_revision + ":save"
        async with write_lock:
            replay = replay_or_conflict(body.operation_id, fingerprint)
            if replay is not None:
                return replay
            session, document = guard_revision(body.expected_revision)
            # This is the exact same guarded intent as clicking Save in
            # Graphlink. A conflict/busy/IO failure must never return success.
            saved = await session.dispatch_intent("app-chat-library", "saveChat", [])
            if saved is not True:
                raise HTTPException(status_code=409, detail="Graphlink save failed or is busy; inspect notifications")
            response = {
                "operation_id": body.operation_id,
                "saved": True,
                "graph_id": document.current_chat_id,
                "updated_at": session.chat_save_state.get("updated_at"),
                "revision": scene_revision(document),
                "replayed": False,
            }
            record(body.operation_id, fingerprint, response)
            # Saving a brand-new graph changes hasSavedChat in the scene
            # metadata. The existing saveChat intent publishes the library
            # and notifications, but not the scene topic; publish it here
            # so an attached UI sees the new saved identity immediately.
            # The receipt precedes this await: if a transport publish fails
            # after the DB commit, replaying this operation_id still reports
            # the already-successful save instead of writing twice.
            await session.publish("scene")
            return response
