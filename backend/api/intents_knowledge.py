"""ADR-017 stage 17.5: the "knowledge" topic's two WS intents.

`search` is read-only (no record_command/publish_scene - same "just return
a value" shape as backend/api/intents_diagnostics.py's own two intents),
the frontend-reachable counterpart to backend/tools_knowledge.py's
ToolRegistry-registered `knowledge.search` (that one is for a future
ADR-008 model-driven tool call; this one is for the "Knowledge" search
panel a human drives directly - same backend.knowledge_retrieval.
hybrid_search() underneath, two different callers).

`setChatIndexIntoKnowledge` DOES mutate the graph (ChatState.
index_into_knowledge - see backend/domain/node_states.py's own comment),
so it goes through record_command/publish_scene like every other scene
setter in this package - but see its own docstring below for why the
actual knowledge-store write happens BEFORE that call, not inside it.
"""

from __future__ import annotations

import asyncio

from backend.api._shared import make_publish_scene
from backend.domain.graph import SceneDocument, SceneError
from backend.events import SessionBus
from backend.knowledge_ingest import IngestError, ingest_text
from backend.knowledge_retrieval import hybrid_search
from backend.knowledge_store import DEFAULT_DB_PATH, get_or_create_workspace_collection
from backend.notifications import NotificationState

_DEFAULT_K = 5
_MAX_K = 25


def _resolve_workspace_collection_id(document: SceneDocument) -> int:
    """ADR-020 stage 20.3: every real ingestion/search call in this module
    scopes to the CALLING SESSION's current workspace - see backend.
    knowledge_store.get_or_create_workspace_collection's own docstring for
    the one-collection-per-workspace, auto-created, invisible scoping
    decision this implements. `document.current_workspace_id` is None for
    a session that has not yet loaded/created any chat with a real
    workspace context (see backend/domain/graph.py's own docstring on that
    field) - falls back to `0`, the pre-20.3 "no collection assigned"
    global sentinel (backend.knowledge_store's own module docstring), so a
    fresh, never-loaded session keeps searching/ingesting into the same
    undifferentiated pool every pre-20.3 build already used, rather than
    raising or guessing at some arbitrary workspace.

    Real blocking SQLite I/O (a SELECT, or an INSERT the very first time a
    given workspace ever ingests/searches anything) - callers run this
    inside asyncio.to_thread, never inline on the event loop, same posture
    every other blocking-I/O call in this module already follows (see
    search()'s own comment on hybrid_search)."""
    workspace_id = document.current_workspace_id
    if workspace_id is None:
        return 0
    return get_or_create_workspace_collection(DEFAULT_DB_PATH, workspace_id)


def branch_history_to_text(history: list[dict]) -> str:
    """Turns chat_branch_history()'s own {"role","content"} list into one
    plain-text document for branch indexing - content_parts-carrying
    entries (`content` is a LIST, not a str - see chat_branch_history's
    own docstring) contribute only their text-type parts, the same
    flattening posture the node's own `content` field already applies for
    every other plain-text consumer of chat history."""
    lines = []
    for turn in history:
        content = turn.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(
                str(part.get("text", "")) for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        else:
            continue
        text = text.strip()
        if text:
            lines.append(f"{turn.get('role', 'user')}: {text}")
    return "\n\n".join(lines)


def _format_search_results(results: list[dict]) -> list[dict]:
    return [
        {
            "chunkId": r["chunk_id"],
            "documentId": r["document_id"],
            "documentTitle": r["document_title"],
            "sourceUri": r["source_uri"],
            "text": r["text"],
            "offsetStart": r["offset_start"],
            "offsetEnd": r["offset_end"],
        }
        for r in results
    ]


def register_knowledge_intents(
    bus: SessionBus,
    document: SceneDocument,
    notifications: NotificationState | None = None,
) -> None:
    publish_scene = make_publish_scene(bus)

    async def search(query, k):
        k = min(max(int(k or _DEFAULT_K), 1), _MAX_K)

        # ADR-020 stage 20.3: scoped to the calling session's current
        # workspace - see _resolve_workspace_collection_id's own docstring.
        # Resolution + search both happen inside the SAME to_thread hop
        # (one thread-pool round trip, not two) since both are real
        # blocking SQLite I/O against the SAME underlying database file.
        def _search():
            collection_id = _resolve_workspace_collection_id(document)
            return hybrid_search(DEFAULT_DB_PATH, query, k=k, collection_id=collection_id)

        # Adversarial-review finding: hybrid_search() does real blocking
        # SQLite I/O (and, when an embedding provider is wired, a network
        # round-trip) - called inline, it would stall the whole event loop
        # for every other connection, unlike every other blocking-I/O
        # intent handler in this codebase (e.g. intents_settings_*.py).
        results = await asyncio.to_thread(_search)
        return {"results": _format_search_results(results)}

    async def set_chat_index_into_knowledge(node_id, enabled):
        """Runs the actual knowledge-store write (chat_branch_history() ->
        ingest_text()) BEFORE the flag flips - see backend/domain/
        node_states.py's own comment on ChatState.index_into_knowledge for
        why: a caller reading `indexIntoKnowledge: true` off the wire must
        be able to trust the branch really was indexed as of that toggle,
        never a flag that silently went True while the write underneath it
        failed. Disabling (`enabled=False`) never de-indexes anything
        already stored - the flag is a one-shot trigger + a "has this ever
        been indexed" marker, not a live subscription toggle.

        Validates node_id is a real chat node FIRST, before any knowledge-
        store write - chat_branch_history() itself does not raise for a
        bad/non-chat node_id (its own docstring: "should stop the walk
        quietly rather than raise"), so without this check a non-chat
        node's empty `content` would silently fail ingestion (IngestError,
        swallowed below) and this function would return before ever
        reaching set_chat_index_into_knowledge's own SceneError - the
        wrong failure surfacing for what is genuinely a bad call, not a
        transient indexing problem."""
        node = document.nodes.get(node_id)
        if node is None or node.kind != "chat":
            raise SceneError(f"node is not a chat node: {node_id}")

        if enabled:
            history = document.chat_branch_history(node_id)
            text = branch_history_to_text(history)

            # ADR-020 stage 20.3: same workspace-scoping, same single-hop
            # resolve-then-ingest shape as search() above.
            def _ingest():
                collection_id = _resolve_workspace_collection_id(document)
                return ingest_text(
                    text,
                    source_uri=f"branch:{node_id}", title=f"Branch (node {node_id})",
                    collection_id=collection_id,
                )

            try:
                # Same blocking-I/O concern as search() above - ingest_text()
                # does real SQLite writes (chunk + embedding-cache rows).
                await asyncio.to_thread(_ingest)
            except IngestError as exc:
                if notifications is not None:
                    notifications.show(str(exc), "error")
                return

        document.record_command(
            "setChatIndexIntoKnowledge", "user",
            lambda: document.set_chat_index_into_knowledge(node_id, enabled),
            node_ids=[node_id],
        )
        await publish_scene()

    bus.register_intent("knowledge", "search", search)
    bus.register_intent("scene", "setChatIndexIntoKnowledge", set_chat_index_into_knowledge)
