"""ADR-017 stage 17.2: registers `knowledge.search` on a ToolRegistry.

Note on how this is exercised today: ADR-007's own tool-use LOOP (offering
`registry.specs()` to a live model mid-conversation and feeding a returned
ToolCall through `registry.invoke()`) is explicitly ADR-008 scope, not yet
built - `api_provider.py`'s ChatRequest(...) call sites never pass `tools=`
yet. This module still registers a real, fully-invokable tool now (tested
end-to-end via direct `registry.invoke()` calls, exactly as backend/tests/
test_tool_registry.py's own precedent already tests the registry itself)
so ADR-008 has something real to wire in later - it is not exercised through
a live model conversation in this stage, matching ADR-007's own "renders
nothing until ADR-008 becomes the first writer" posture for tool-call
rendering (backend/tools.py's sibling stages).

ADR-017's OTHER surfacing mechanism - automatic per-branch context
augmentation, injected before a chat turn is sent - needs no tool-loop at
all and is where stage 17.4 delivers genuine end-to-end retrieval value.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.knowledge_store import DEFAULT_DB_PATH, search_chunks
from backend.providers.base import ToolCall, ToolSpec
from backend.tools import KNOWLEDGE_READ, RunContext, ToolRegistry, ToolResult

_MAX_K = 25

KNOWLEDGE_SEARCH_SPEC = ToolSpec(
    name="knowledge.search",
    description=(
        "Searches the local knowledge store (ingested documents) for chunks matching a query. "
        "Returns the best-matching passages with their source document title, source URI, and "
        "exact character offsets for citation."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query."},
            "collection_id": {
                "type": "integer",
                "description": "Restrict results to one collection. Omit to search everything.",
            },
            "k": {
                "type": "integer",
                "description": f"Maximum number of results to return (default 5, max {_MAX_K}).",
            },
        },
        "required": ["query"],
    },
)


def _format_results(results: list[dict]) -> str:
    if not results:
        return "No matching passages were found."
    payload = [
        {
            "document_title": r["document_title"],
            "source_uri": r["source_uri"],
            "chunk_id": r["chunk_id"],
            "offset_start": r["offset_start"],
            "offset_end": r["offset_end"],
            "text": r["text"],
        }
        for r in results
    ]
    return json.dumps(payload, ensure_ascii=False)


def make_knowledge_search_handler(db_path: Path | None = None):
    """Builds the `knowledge.search` handler bound to `db_path` (defaults
    to knowledge_store.DEFAULT_DB_PATH) - a factory rather than a bare
    module-level handler so tests can bind a throwaway tmp_path db without
    monkeypatching module state, matching this codebase's own established
    "inject the path, don't patch the default" preference elsewhere (e.g.
    backend.knowledge_ingest.ingest_file's own db_path parameter)."""
    resolved_db_path = db_path if db_path is not None else DEFAULT_DB_PATH

    async def handle_knowledge_search(call: ToolCall, ctx: RunContext) -> ToolResult:
        query = call.arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolResult(content="'query' must be a non-empty string.", is_error=True)

        collection_id = call.arguments.get("collection_id")
        if collection_id is not None and not isinstance(collection_id, int):
            return ToolResult(content="'collection_id' must be an integer.", is_error=True)

        k = call.arguments.get("k", 5)
        if not isinstance(k, int) or k < 1:
            return ToolResult(content="'k' must be a positive integer.", is_error=True)
        k = min(k, _MAX_K)

        results = search_chunks(resolved_db_path, query, collection_id=collection_id, k=k)
        return ToolResult(content=_format_results(results))

    return handle_knowledge_search


def register_knowledge_tools(registry: ToolRegistry, *, db_path: Path | None = None) -> None:
    """Registers `knowledge.search` as `auto`-approval (read-only, matching
    every other read-only tool's approval posture in backend/tools.py's own
    module docstring) under the `knowledge.read` scope."""
    registry.register(
        KNOWLEDGE_SEARCH_SPEC,
        make_knowledge_search_handler(db_path),
        scopes={KNOWLEDGE_READ},
        approval="auto",
    )
