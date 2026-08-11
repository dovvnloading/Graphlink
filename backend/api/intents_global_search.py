"""ADR-020 stage 20.4: the "globalSearch" topic's one WS intent - full-text
search across EVERY workspace's graphs AND real ingested documents at once,
the backend half of "a phrase in a node of a closed graph in another
workspace is found and focused" (this stage's own exit criterion).

Read-only, same "just return a value" shape as backend/api/
intents_knowledge.py's own `search` (that one is deliberately workspace-
scoped - see its own _resolve_workspace_collection_id docstring; this one
is deliberately NOT, by default - collection_id=None already means "no
filter, search everything" to backend.knowledge_store.search_chunks/
backend.knowledge_retrieval.hybrid_search, which is exactly the cross-
workspace mechanism this stage needs, not a new one).

Registered on its own "globalSearch" topic rather than folded into
"knowledge" - a deliberately different, wider-scoped search surface (every
workspace, not just the calling session's current one), matching this
codebase's own established practice of one topic per distinct feature
surface even when the underlying primitives overlap.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from backend.events import SessionBus
from backend.knowledge_retrieval import hybrid_search
from backend.knowledge_store import DEFAULT_DB_PATH, get_or_create_workspace_collection

_DEFAULT_K = 20
_MAX_K = 50

_GRAPH_SOURCE_URI_PREFIX = "graph:"


@dataclass
class GlobalSearchArgs:
    """`workspaceId=None` (the default - a trailing optional positional arg,
    same "a dataclass field with a default is correctly optional" shape
    backend/events.py's own _validate_intent_args docstring already
    documents) searches every workspace's corpus at once - the default,
    exit-criterion-driving behavior. A real int scopes to exactly that
    workspace's own knowledge collection, for the optional workspace-filter
    affordance this stage's own design allows (not required for the exit
    criterion, but cheap to support given collection_id already accepts
    either shape)."""

    query: str
    k: int
    workspaceId: int | None = None


def _graph_id_from_source_uri(source_uri: str) -> int | None:
    """Parses the synthetic `f"graph:{graph_id}"` scheme backend.
    knowledge_store.reindex_graph_content writes back OUT into a real int -
    the inverse of that function's own f-string. Returns None for anything
    that isn't that exact scheme (a real ingested document's own
    source_uri - a file path, `branch:{node_id}`, a web URL, ...) or a
    graph id that somehow failed to parse as an int (defensive; never
    actually produced by reindex_graph_content itself)."""
    if not source_uri.startswith(_GRAPH_SOURCE_URI_PREFIX):
        return None
    try:
        return int(source_uri[len(_GRAPH_SOURCE_URI_PREFIX):])
    except ValueError:
        return None


def _format_global_search_results(results: list[dict]) -> list[dict]:
    """Mirrors backend/api/intents_knowledge.py's own _format_search_results
    field-for-field, PLUS `sourceNodeId`/`graphId` - what tells the
    frontend which of the two jump actions a given result row needs
    (loadGraphAndFocusNode for a graph/node hit, "knowledge"'s own document-
    source_uri external-link for a real ingested-document hit): `chunks.
    source_node_id IS NOT NULL` is a real node id (see backend.
    knowledge_store's own migration "5"); `graphId` is derived from that
    SAME row's own `source_uri` ONLY when source_node_id is present - never
    computed independently, so a real document whose source_uri happens to
    start with "graph:" (not producible by any real ingestion path today,
    but not worth trusting blindly either) can never be misread as a graph
    hit."""
    formatted = []
    for result in results:
        source_node_id = result.get("source_node_id")
        graph_id = _graph_id_from_source_uri(result["source_uri"]) if source_node_id is not None else None
        formatted.append({
            "chunkId": result["chunk_id"],
            "documentId": result["document_id"],
            "documentTitle": result["document_title"],
            "sourceUri": result["source_uri"],
            "text": result["text"],
            "offsetStart": result["offset_start"],
            "offsetEnd": result["offset_end"],
            "sourceNodeId": source_node_id,
            "graphId": graph_id,
        })
    return formatted


def register_global_search_intents(bus: SessionBus) -> None:
    async def search(query: str, k: int, workspace_id: int | None = None):
        k = min(max(int(k or _DEFAULT_K), 1), _MAX_K)

        # Same blocking-I/O concern as backend/api/intents_knowledge.py's
        # own search() - real SQLite I/O (and, for the optional workspace
        # filter, a real SELECT-or-INSERT), never called inline on the event
        # loop.
        def _search():
            collection_id = (
                get_or_create_workspace_collection(DEFAULT_DB_PATH, int(workspace_id))
                if workspace_id is not None else None
            )
            return hybrid_search(DEFAULT_DB_PATH, query, k=k, collection_id=collection_id)

        results = await asyncio.to_thread(_search)
        return {"results": _format_global_search_results(results)}

    bus.register_intent("globalSearch", "search", search, args_schema=GlobalSearchArgs)
