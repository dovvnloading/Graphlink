"""Read-only search over the current workspace agent's saved sessions.

The tool accepts only query/limit, never a path or agent id. Its transcript
is selected from the trusted per-run workspace id, not from the project
folder the agent may have been granted for fs tools.
"""

from __future__ import annotations

import asyncio
import sqlite3

from backend.harness.transcript import flush, transcript_path
from backend.harness.transcript_index import MAX_MATCHES, search_index
from backend.harness.workspace import WorkspaceError, workspace_dir
from backend.providers.base import ToolCall, ToolSpec
from backend.tools import FS_READ, RunContext, ToolRegistry, ToolResult

SEARCH_SPEC = ToolSpec(
    name="session.search",
    description=(
        "Full-text search of this agent's own persistent transcript across "
        "earlier tasks, including messages no longer in the active context "
        "after compaction. Returns ranked excerpts with message roles and "
        "transcript line numbers. Does not search other agents or project files."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Words to find (all must match)."},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_MATCHES},
        },
        "required": ["query"],
    },
)


def _search(workspace_id: str, query: str, limit: int) -> list[dict]:
    workspace = workspace_dir(workspace_id)
    if not flush(workspace):
        raise OSError("Transcript is still being written; retry the search.")
    return search_index(transcript_path(workspace), query, limit=limit)


def register_workspace_tools(registry: ToolRegistry, dispatcher, document) -> None:
    async def session_search(call: ToolCall, ctx: RunContext) -> ToolResult:
        query = call.arguments.get("query")
        if not isinstance(query, str):
            return ToolResult(content="session.search requires a text query.", is_error=True)
        limit = call.arguments.get("limit", 5)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_MATCHES:
            return ToolResult(
                content=f"session.search limit must be between 1 and {MAX_MATCHES}.",
                is_error=True,
            )
        workspace_id = getattr(ctx, "harness_workspace_id", None)
        if not isinstance(workspace_id, str) or not workspace_id:
            return ToolResult(content="No agent transcript is bound to this run.", is_error=True)
        try:
            hits = await asyncio.to_thread(_search, workspace_id, query, limit)
        except (OSError, sqlite3.Error, ValueError, WorkspaceError) as exc:
            return ToolResult(content=f"Transcript search unavailable: {exc}", is_error=True)
        if not hits:
            return ToolResult(content="No matching excerpts in this agent's past transcript.")
        lines = ["Matching excerpts from this agent's transcript (ranked):"]
        for hit in hits:
            lines.append(
                f"- transcript.jsonl:{hit['line']} ({hit['role']}): {hit['excerpt']}"
            )
        return ToolResult(content="\n".join(lines))

    registry.register(
        SEARCH_SPEC, session_search,
        scopes={FS_READ}, approval="auto", parallel_safe=True,
    )


USAGE_HINT = (
    "session.search searches your own saved transcript across earlier tasks "
    "and compactions; use it to recall details no longer in current context."
)
