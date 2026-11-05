"""Read-only headless control API over Graphlink's actual running backend.

This is intentionally a curated interface, NOT an endpoint that dispatches
arbitrary SessionBus intents. The existing per-launch /api bearer-token,
Origin and Host checks protect every route here automatically. MCP stdio
clients connect to these routes; they never open another database or create a
second SceneDocument. Mutating control operations require a later, explicit
approval and revision-token design.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from backend.chat_library import get_all_chats, get_all_workspaces, load_chat_snapshot
from backend.events import DEFAULT_SESSION_ID, EventBus
from backend.providers.base import ToolCall
from backend.session_context import get_session_context
from backend.tools import GRAPH_READ, RunContext
from backend.tools_graph import _READ_MAX_DEPTH, make_list_nodes_handler, make_read_subgraph_handler
from backend.control_writes import scene_revision

DEFAULT_CONTROL_PORT = 8765
DEFAULT_TOKEN_FILE = Path.home() / ".graphlink" / "headless.token"
_MAX_SAVED_EXCERPT = 500

# Explicit allowlist: adding a control route must never implicitly grant
# the file-backed MCP token access to that route.
_READ_ONLY_STATIC_PATHS = frozenset({
    "/api/control/workspaces",
    "/api/control/graphs",
    "/api/control/scene/nodes",
    "/api/control/scene/subgraph",
})
_SAVED_GRAPH_PATH = re.compile(r"/api/control/graphs/[0-9]+\Z")


def is_readonly_control_request(method: str, path: str) -> bool:
    """True only for the five read-only MCP API route shapes."""
    return method == "GET" and (
        path in _READ_ONLY_STATIC_PATHS or _SAVED_GRAPH_PATH.fullmatch(path) is not None
    )


def _saved_node_summary(node: dict[str, Any]) -> dict[str, Any]:
    """Summarize a stored node without dumping large history/image payloads."""
    content: Any = ""
    for field in ("raw_content", "content", "code", "prompt", "query", "thinking_text", "html_content"):
        if field in node:
            content = node[field]
            break
    if isinstance(content, list):
        # Multimodal chat parts can contain huge encoded images. Only text
        # parts belong in the lightweight inventory response.
        content = " ".join(
            str(part.get("text", ""))[:_MAX_SAVED_EXCERPT]
            for part in content if isinstance(part, dict) and part.get("type") == "text"
        )
    elif not isinstance(content, str):
        content = ""
    return {
        "id": str(node.get("id") or ""),
        "kind": str(node.get("node_type") or ""),
        "title": str(node.get("title") or ""),
        "excerpt": content[:_MAX_SAVED_EXCERPT],
        "truncated": len(content) > _MAX_SAVED_EXCERPT,
    }


async def _deny_approval(_call: ToolCall) -> bool:
    return False


async def _run_read_tool(handler, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    # Use the SAME bounded, paginated graph handlers already available to
    # Graphlink's own Builder. Neither handler performs mutations.
    context = RunContext(granted_scopes=frozenset({GRAPH_READ}), request_approval=_deny_approval)
    result = await handler(ToolCall(id="control-read", name=name, arguments=arguments), context)
    if result.is_error:
        raise HTTPException(status_code=400, detail=result.content)
    return json.loads(result.content)


def register_control_api(app: FastAPI, bus: EventBus, db_path: Path) -> None:
    """Mount only explicitly permitted read operations on the authenticated app."""

    @app.get("/api/control/workspaces")
    async def list_workspaces() -> dict[str, Any]:
        workspaces = await asyncio.to_thread(get_all_workspaces, db_path)
        return {"workspaces": workspaces}

    @app.get("/api/control/graphs")
    async def list_graphs(
        workspace_id: int | None = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        graphs = await asyncio.to_thread(get_all_chats, db_path)
        if workspace_id is not None:
            graphs = [g for g in graphs if g["workspaceId"] == workspace_id]
        page = graphs[offset:offset + limit]
        return {
            "graphs": page, "total": len(graphs), "offset": offset,
            "returned": len(page), "more": offset + len(page) < len(graphs),
        }

    @app.get("/api/control/graphs/{graph_id}")
    async def get_graph(
        graph_id: int,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=40, ge=1, le=100),
    ) -> dict[str, Any]:
        # A single DB snapshot covers the graph, notes and pins, so a
        # simultaneous save cannot produce a torn read.
        row, notes, pins = await asyncio.to_thread(load_chat_snapshot, db_path, graph_id)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown graph")
        data = row.get("data")
        raw_nodes = data.get("nodes", []) if isinstance(data, dict) else []
        nodes = [n for n in raw_nodes if isinstance(n, dict)] if isinstance(raw_nodes, list) else []
        page = nodes[offset:offset + limit]
        return {
            "source": "saved",
            "graph": {
                "id": graph_id, "title": row["title"],
                "workspaceId": row["workspace_id"], "updatedAt": row["updated_at"],
            },
            "nodes": [_saved_node_summary(n) for n in page],
            "totalNodes": len(nodes), "offset": offset,
            "returned": len(page), "more": offset + len(page) < len(nodes),
            "notesCount": len(notes), "pinsCount": len(pins),
        }

    @app.get("/api/control/scene/nodes")
    async def list_scene_nodes(
        kind: str = Query(default="", max_length=100),
        query: str = Query(default="", max_length=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        document = get_session_context(bus.session(DEFAULT_SESSION_ID)).canvas_document
        output = await _run_read_tool(
            make_list_nodes_handler(document), "graph.list_nodes",
            {"kind": kind, "query": query, "offset": offset},
        )
        return {"source": "live", **output, "revision": scene_revision(document)}

    @app.get("/api/control/scene/subgraph")
    async def read_scene_subgraph(
        root_id: str = Query(min_length=1, max_length=200),
        depth: int = Query(default=_READ_MAX_DEPTH, ge=1, le=_READ_MAX_DEPTH),
        direction: str = Query(default="down", pattern="^(down|up|both)$"),
    ) -> dict[str, Any]:
        document = get_session_context(bus.session(DEFAULT_SESSION_ID)).canvas_document
        output = await _run_read_tool(
            make_read_subgraph_handler(document), "graph.read_subgraph",
            {"root_id": root_id, "depth": depth, "direction": direction},
        )
        return {"source": "live", **output}
