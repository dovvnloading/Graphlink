"""ADR-017 stage 17.2/17.4: registers `knowledge.search` on a ToolRegistry.

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
all; backend.knowledge_retrieval's own format_untrusted_context/
select_within_budget are what stage 17.4 built for it.

Stage 17.4: this tool now runs HYBRID search (backend.knowledge_retrieval.
hybrid_search - FTS5 fused with vector search via reciprocal rank fusion)
whenever an embedding provider/model is supplied to
register_knowledge_tools; omitted, it degrades to the same lexical-only
search stage 17.2 shipped (ADR-017 doc's own "degraded gracefully to
lexical-only when no embedding model is configured" consequence) - never
an error, since plenty of real setups are lexical-only by design."""

from __future__ import annotations

import json
from pathlib import Path

# SECURITY-FIX: the ONE source of truth for "which collection does this
# session's knowledge access scope to" - backend/api/intents_knowledge.py's
# own docstring on this function explains the ADR-020 stage 20.3 workspace-
# isolation decision it implements. Reused here (not reimplemented) so the
# LLM-facing tool below enforces the exact same scoping the human-driven
# "Knowledge" search panel already does, rather than a second copy that
# could silently drift from it.
from backend.api.intents_knowledge import _resolve_workspace_collection_id
from backend.domain.graph import SceneDocument
from backend.knowledge_retrieval import hybrid_search
from backend.knowledge_store import DEFAULT_DB_PATH
from backend.providers.base import ToolCall, ToolSpec
from backend.tools import KNOWLEDGE_READ, RunContext, ToolRegistry, ToolResult

_MAX_K = 25

KNOWLEDGE_SEARCH_SPEC = ToolSpec(
    name="knowledge.search",
    description=(
        "Searches the local knowledge store (ingested documents) for chunks matching a query. "
        "Returns the best-matching passages with their source document title, source URI, and "
        "exact character offsets for citation. Always scoped to the current workspace."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query."},
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


def make_knowledge_search_handler(
    db_path: Path | None = None, *,
    document: SceneDocument | None = None,
    embedding_provider=None, embedding_model_id: str | None = None,
):
    """Builds the `knowledge.search` handler bound to `db_path` (defaults
    to knowledge_store.DEFAULT_DB_PATH) - a factory rather than a bare
    module-level handler so tests can bind a throwaway tmp_path db without
    monkeypatching module state, matching this codebase's own established
    "inject the path, don't patch the default" preference elsewhere (e.g.
    backend.knowledge_ingest.ingest_file's own db_path parameter).

    `embedding_provider`/`embedding_model_id` are passed straight through
    to hybrid_search() - see that function's own docstring for the exact
    lexical-only degradation rule when either is omitted.

    SECURITY-FIX: `document`, when given, scopes every search to the
    CALLING SESSION's own current workspace via the same
    _resolve_workspace_collection_id the human-driven "Knowledge" search
    panel uses (backend/api/intents_knowledge.py) - never a caller-supplied
    collection_id, which this tool used to accept and pass straight through
    unchecked. reindex_graph_content indexes every node of every saved
    graph into this same store, so an unscoped/arbitrarily-scoped search
    tool handed to the model let prompt-injected content read any other
    workspace's chat content on request, even though this tool is
    registered `approval="auto"` specifically because it was assumed to be
    read-only WITHIN the session's own data. `document=None` (no session
    context - matches this function's own unit tests, which check
    registration/error-handling in isolation) falls back to an unscoped
    search, the pre-fix default for a caller with no workspace to scope to
    at all - production always passes a real document (see
    backend.agents.AgentDispatcher.builder_tool_registry)."""
    resolved_db_path = db_path if db_path is not None else DEFAULT_DB_PATH

    async def handle_knowledge_search(call: ToolCall, ctx: RunContext) -> ToolResult:
        query = call.arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolResult(content="'query' must be a non-empty string.", is_error=True)

        k = call.arguments.get("k", 5)
        if not isinstance(k, int) or k < 1:
            return ToolResult(content="'k' must be a positive integer.", is_error=True)
        k = min(k, _MAX_K)

        collection_id = (
            _resolve_workspace_collection_id(document, resolved_db_path) if document is not None else None
        )

        results = hybrid_search(
            resolved_db_path, query,
            embedding_provider=embedding_provider, embedding_model_id=embedding_model_id,
            collection_id=collection_id, k=k,
        )
        return ToolResult(content=_format_results(results))

    return handle_knowledge_search


def register_knowledge_tools(
    registry: ToolRegistry, *, db_path: Path | None = None,
    document: SceneDocument | None = None,
    embedding_provider=None, embedding_model_id: str | None = None,
) -> None:
    """Registers `knowledge.search` as `auto`-approval (read-only, matching
    every other read-only tool's approval posture in backend/tools.py's own
    module docstring) under the `knowledge.read` scope. See
    make_knowledge_search_handler's own docstring for why `document` -
    always passed in production - is what keeps this tool's "read-only"
    approval posture honest."""
    registry.register(
        KNOWLEDGE_SEARCH_SPEC,
        make_knowledge_search_handler(
            db_path, document=document,
            embedding_provider=embedding_provider, embedding_model_id=embedding_model_id,
        ),
        scopes={KNOWLEDGE_READ},
        approval="auto",
    )
