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
from pathlib import Path
from backend import native_dialogs
from backend.knowledge_jobs import KnowledgeIngestManager
from backend.knowledge_embedding_jobs import KnowledgeEmbeddingJobManager
from backend.knowledge_uploads import KnowledgeDropUploads
from backend.knowledge_watches import WatchedFolderService
from dataclasses import dataclass
from uuid import uuid4

from backend.api._shared import make_publish_scene
from backend.domain.graph import SceneDocument, SceneError
from backend.domain.node_states import HarnessState
from backend.harness.workspace import WorkspaceError, bound_root
from backend.harness.workspace_files import _inside
from backend.events import SessionBus
from backend.knowledge_chunking import chunk_text
from backend.knowledge_ingest import IngestError, is_supported_knowledge_file
from backend.knowledge_embedding_config import configured_hybrid_search
from backend.knowledge_store import (
    DEFAULT_DB_PATH, get_or_create_workspace_collection, open_cited_document, replace_branch_document_with_chunks,
    list_library_documents, file_sources_for_document, remove_library_document, library_document_text,
    display_source_label,
)
from backend.notifications import NotificationState

_DEFAULT_K = 5
_MAX_K = 25


@dataclass
class SearchArgs:
    query: str
    k: int


@dataclass
class OpenCitationArgs:
    documentId: int
    chunkId: int
    citationId: str


@dataclass
class SetChatIndexIntoKnowledgeArgs:
    nodeId: str
    enabled: bool


@dataclass
class CancelIngestArgs:
    jobId: str


@dataclass
class CancelEmbeddingArgs:
    jobId: str


@dataclass
class DocumentIdArgs:
    documentId: int


@dataclass
class BeginUploadArgs:
    files: list[dict]


@dataclass
class AppendUploadArgs:
    uploadId: str
    fileIndex: int
    data: str


@dataclass
class UploadIdArgs:
    uploadId: str


@dataclass
class RemoveWatchedFolderArgs:
    folderId: int


@dataclass
class SyncWatchedFoldersArgs:
    folderId: int | None = None


def _resolve_workspace_collection_id(document: SceneDocument, db_path=None) -> int:
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

    `db_path` defaults to this module's own DEFAULT_DB_PATH (every call
    site in THIS file omits it, unchanged from before this parameter
    existed) - added only so backend/tools_knowledge.py's
    make_knowledge_search_handler, which already threads its own injectable
    db_path through every other knowledge_store call it makes (see that
    function's own docstring on why: test isolation, matching
    knowledge_ingest.ingest_file's established "inject the path" pattern),
    can reuse this SAME scoping logic against that same path rather than
    silently resolving against the real default database.

    Real blocking SQLite I/O (a SELECT, or an INSERT the very first time a
    given workspace ever ingests/searches anything) - callers run this
    inside asyncio.to_thread, never inline on the event loop, same posture
    every other blocking-I/O call in this module already follows (see
    search()'s own comment on hybrid_search)."""
    workspace_id = document.current_workspace_id
    if workspace_id is None:
        return 0
    return get_or_create_workspace_collection(db_path if db_path is not None else DEFAULT_DB_PATH, workspace_id)


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


def format_search_result_row(result: dict) -> dict:
    """The seven original fields plus stable provenance and rank diagnostics, shared
    verbatim with backend/api/intents_global_search.py's own
    _format_global_search_results (that one adds sourceNodeId/graphId on
    top of exactly this shape) - kept in one place so the two callers can
    never drift field-for-field."""
    return {
        "chunkId": result["chunk_id"],
        "documentId": result["document_id"],
        "documentTitle": result["document_title"],
        "sourceUri": display_source_label(str(result["source_uri"]), str(result["document_title"])),
        "text": result["text"],
        "trustLabel": "untrusted data",
        "offsetStart": result["offset_start"],
        "offsetEnd": result["offset_end"],
        "sourceFile": display_source_label(str(result["source_file"]), str(result["document_title"])),
        "section": result["section"],
        "citationId": result["citation_id"],
        "rankSignals": result.get("rank_signals", {}),
        "rrfScore": result.get("rrf_score"),
    }


def _format_search_results(results: list[dict]) -> list[dict]:
    return [format_search_result_row(r) for r in results]


def register_knowledge_watched_folder_intents(
    bus: SessionBus,
    document: SceneDocument,
    watcher: WatchedFolderService,
) -> None:
    """Register workspace-scoped, opt-in watched-folder operations."""

    def _watched_workspace_id() -> int:
        workspace_id = document.current_workspace_id
        if workspace_id is None:
            raise IngestError("Select a workspace before managing watched folders.")
        return workspace_id

    async def add_watched_folder():
        # The native picker is the only way a path may become watched.
        # Capture workspace before the dialog (which may outlive a switch).
        workspace_id = _watched_workspace_id()
        selected = await native_dialogs.pick_folder()
        if not selected:
            return {"cancelled": True}
        # Pin the native picker grant before a worker can persist the watch.
        canonical = str(Path(selected).expanduser().resolve(strict=True))
        return await watcher.add(workspace_id, canonical)

    async def remove_watched_folder(folder_id: int):
        return await watcher.remove(_watched_workspace_id(), folder_id)

    async def list_watched_folders():
        return await watcher.list(_watched_workspace_id())

    async def sync_watched_folders(folder_id: int | None = None):
        return await watcher.sync(_watched_workspace_id(), folder_id)

    bus.register_intent("knowledge", "addWatchedFolder", add_watched_folder)
    bus.register_intent(
        "knowledge", "removeWatchedFolder", remove_watched_folder, args_schema=RemoveWatchedFolderArgs,
    )
    bus.register_intent("knowledge", "listWatchedFolders", list_watched_folders)
    bus.register_intent(
        "knowledge", "syncWatchedFolders", sync_watched_folders, args_schema=SyncWatchedFoldersArgs,
    )


def register_knowledge_document_intents(
    bus: SessionBus,
    document: SceneDocument,
    ingest_jobs: KnowledgeIngestManager,
    uploads: KnowledgeDropUploads,
    db_path: Path,
) -> None:
    """Register workspace-scoped Knowledge Library document operations."""

    async def list_documents():
        workspace_id = document.current_workspace_id

        def load():
            collection_id = (
                get_or_create_workspace_collection(db_path, workspace_id)
                if workspace_id is not None else 0
            )
            return list_library_documents(db_path, collection_id=collection_id)

        return {"documents": await asyncio.to_thread(load)}

    async def open_document(document_id):
        workspace_id = document.current_workspace_id

        def load():
            collection_id = (
                get_or_create_workspace_collection(db_path, workspace_id)
                if workspace_id is not None else 0
            )
            return library_document_text(
                db_path, collection_id=collection_id, document_id=document_id,
            )

        result = await asyncio.to_thread(load)
        if result is None:
            raise IngestError("Document is not in the current workspace.")
        return result

    async def remove_document(document_id):
        workspace_id = document.current_workspace_id

        def remove():
            collection_id = (
                get_or_create_workspace_collection(db_path, workspace_id)
                if workspace_id is not None else 0
            )
            paths = file_sources_for_document(
                db_path, collection_id=collection_id, document_id=document_id,
            )
            deleted = remove_library_document(
                db_path, collection_id=collection_id, document_id=document_id,
            )
            if deleted:
                for path in paths:
                    uploads.cleanup_source(path)
            return {"removed": deleted}

        return await asyncio.to_thread(remove)

    async def reindex_document(document_id):
        # The client provides only an ID. Paths come exclusively from
        # previously indexed file_sources in this workspace, never from JSON.
        workspace_id = document.current_workspace_id

        def sources():
            collection_id = (
                get_or_create_workspace_collection(db_path, workspace_id)
                if workspace_id is not None else 0
            )
            return file_sources_for_document(
                db_path, collection_id=collection_id, document_id=document_id,
            )

        paths = await asyncio.to_thread(sources)
        if not paths:
            raise IngestError("This document has no file source to re-index.")
        return ingest_jobs.start(paths, folder=False, workspace_id=workspace_id)

    bus.register_intent("knowledge", "listDocuments", list_documents)
    bus.register_intent("knowledge", "openDocument", open_document, args_schema=DocumentIdArgs)
    bus.register_intent("knowledge", "removeDocument", remove_document, args_schema=DocumentIdArgs)
    bus.register_intent("knowledge", "reindexDocument", reindex_document, args_schema=DocumentIdArgs)


def register_knowledge_ingest_intents(
    bus: SessionBus,
    document: SceneDocument,
    ingest_jobs: KnowledgeIngestManager,
) -> None:
    """Register filesystem ingestion and ingestion-job control intents."""
    async def add_files():
        """The native multiple-file picker is the filesystem access grant.

        Capture the workspace before opening a potentially long-lived
        picker, so switching workspaces while it is open cannot redirect
        the finished import into the next workspace.
        """
        workspace_id = document.current_workspace_id
        selected = await native_dialogs.pick_files()
        if not selected:
            return {"cancelled": True}
        # Unsupported explicit selections are user errors, not jobs that
        # appear successful after silently processing zero files.
        canonical = [str(Path(path).expanduser().resolve(strict=True)) for path in selected]
        if any(not is_supported_knowledge_file(Path(path)) for path in canonical):
            raise IngestError("Selection contains an unsupported file type.")
        return ingest_jobs.start(canonical, folder=False, workspace_id=workspace_id)

    async def add_folder():
        workspace_id = document.current_workspace_id
        selected = await native_dialogs.pick_folder()
        if not selected:
            return {"cancelled": True}
        # Freeze the picker grant before any background scan can run.
        canonical = str(Path(selected).expanduser().resolve(strict=True))
        return ingest_jobs.start(canonical, folder=True, workspace_id=workspace_id)

    async def cancel_ingest(job_id):
        return ingest_jobs.cancel(job_id)

    async def get_ingest_jobs():
        return ingest_jobs.snapshot()

    bus.register_intent("knowledge", "addFiles", add_files)
    bus.register_intent("knowledge", "addFolder", add_folder)
    bus.register_intent("knowledge", "cancelIngest", cancel_ingest, args_schema=CancelIngestArgs)
    bus.register_intent("knowledge", "getIngestJobs", get_ingest_jobs)


def register_knowledge_existing_file_intents(
    bus: SessionBus,
    document: SceneDocument,
    ingest_jobs: KnowledgeIngestManager,
    *,
    composer_document=None,
    agent_dispatcher=None,
    settings_manager=None,
) -> None:
    """Register native-authorized imports from chat and workspace Files."""
    async def add_chat_attachment(attachment_id):
        """An attachment ID is a native-picker grant in this session, not a path from the browser."""
        if composer_document is None:
            raise IngestError("Chat attachments are unavailable.")
        attachment = next(
            (item for item in composer_document.staged_attachments if item.id == attachment_id), None,
        )
        if attachment is None:
            raise IngestError("This attachment is no longer staged. Attach the file again.")
        if attachment.kind != "document" or not is_supported_knowledge_file(Path(attachment.path)):
            raise IngestError("Only supported text and document attachments can be added to knowledge.")
        canonical = str(Path(attachment.path).expanduser().resolve(strict=True))
        return ingest_jobs.start(canonical, folder=False, workspace_id=document.current_workspace_id)

    async def add_workspace_file(node_id, relative_path, expected_root):
        """Resolve a Files-view selection through the CURRENT agent grant, never client-supplied roots."""
        if agent_dispatcher is None:
            raise WorkspaceError("Workspace file imports are unavailable.")
        node = document.nodes.get(node_id)
        if node is None or not isinstance(node.state, HarnessState):
            raise WorkspaceError("This agent node no longer exists.")
        root, _ = bound_root(
            node.state.harness_workspace_id,
            node.state.harness_workspace_path,
            settings_manager=agent_dispatcher._settings_manager,
        )
        if expected_root != str(root):
            raise WorkspaceError("Workspace folder changed. Refresh the Files list and choose a file again.")
        if not relative_path:
            raise WorkspaceError("Select a workspace file to add.")
        file = _inside(root, relative_path)
        if not file.is_file():
            raise WorkspaceError("Not a workspace file.")
        if not is_supported_knowledge_file(file):
            raise IngestError("This file type cannot be added to knowledge.")
        return ingest_jobs.start(
            str(file), folder=False, workspace_id=document.current_workspace_id, allowed_root=root,
        )

    bus.register_intent("knowledge", "addChatAttachment", add_chat_attachment)
    bus.register_intent("knowledge", "addWorkspaceFile", add_workspace_file)


def register_knowledge_embedding_intents(
    bus: SessionBus,
    document: SceneDocument,
    embedding_jobs: KnowledgeEmbeddingJobManager,
) -> None:
    """Register embedding-job status, cancellation, retry and refresh intents."""

    def embedding_snapshot():
        # The manager retains recent jobs from other workspaces. Identify
        # this session's CURRENT workspace so consumers never show a previous
        # workspace's progress after switching graphs.
        return {"workspaceId": document.current_workspace_id, **embedding_jobs.snapshot()}

    bus.register_topic("knowledge-embedding", embedding_snapshot)

    async def get_embedding_jobs():
        return embedding_snapshot()

    async def cancel_embedding(job_id: str):
        return embedding_jobs.cancel(job_id)

    async def resume_embedding():
        return embedding_jobs.resume(document.current_workspace_id)

    async def refresh_embeddings():
        # Called after Settings > Knowledge saves a genuinely new choice.
        # Cancels any old provider before queuing the current workspace.
        return embedding_jobs.refresh(document.current_workspace_id)

    bus.register_intent("knowledge", "getEmbeddingJobs", get_embedding_jobs)
    bus.register_intent("knowledge", "cancelEmbedding", cancel_embedding, args_schema=CancelEmbeddingArgs)
    bus.register_intent("knowledge", "resumeEmbedding", resume_embedding)
    bus.register_intent("knowledge", "refreshEmbeddings", refresh_embeddings)


def register_knowledge_search_intents(
    bus: SessionBus,
    document: SceneDocument,
    settings_manager=None,
    db_path=None,
) -> None:
    db_path = DEFAULT_DB_PATH if db_path is None else db_path
    async def search(query, k):
        k = min(max(int(k or _DEFAULT_K), 1), _MAX_K)
        workspace_id = document.current_workspace_id

        # ADR-020 stage 20.3: scoped to the calling session's current
        # workspace - see _resolve_workspace_collection_id's own docstring.
        # Resolution + search both happen inside the SAME to_thread hop
        # (one thread-pool round trip, not two) since both are real
        # blocking SQLite I/O against the SAME underlying database file.
        def _search():
            collection_id = _resolve_workspace_collection_id(document, db_path)
            return configured_hybrid_search(
                db_path, query, k=k, collection_id=collection_id,
                settings_manager=settings_manager,
            )

        # Adversarial-review finding: hybrid_search() does real blocking
        # SQLite I/O (and, when an embedding provider is wired, a network
        # round-trip) - called inline, it would stall the whole event loop
        # for every other connection, unlike every other blocking-I/O
        # intent handler in this codebase (e.g. intents_settings_*.py).
        results = await asyncio.to_thread(_search)
        # A slow vector request must not display the previous workspace's
        # passages after the session switches workspaces mid-search.
        if document.current_workspace_id != workspace_id:
            return {"results": []}
        return {"results": _format_search_results(results)}

    async def open_citation(document_id, chunk_id, citation_id):
        """Return only a verified passage's indexed document in this workspace."""
        workspace_id = document.current_workspace_id

        def _read():
            if not db_path.exists():
                return None
            collection_id = (
                0 if workspace_id is None
                else get_or_create_workspace_collection(db_path, workspace_id)
            )
            return open_cited_document(
                db_path, document_id=document_id, chunk_id=chunk_id,
                citation_id=citation_id, collection_id=collection_id,
            )

        source = await asyncio.to_thread(_read)
        # A workspace switch during the blocking read must not reveal the
        # previous workspace's document in a late request/reply.
        if document.current_workspace_id != workspace_id:
            return {"document": None}
        return {"document": source}

    bus.register_intent("knowledge", "search", search, args_schema=SearchArgs)
    bus.register_intent("knowledge", "openCitation", open_citation, args_schema=OpenCitationArgs)


def register_knowledge_intents(
    bus: SessionBus,
    document: SceneDocument,
    notifications: NotificationState | None = None,
    settings_manager=None,
    composer_document=None,
    agent_dispatcher=None,
    watch_service: WatchedFolderService | None = None,
) -> None:
    publish_scene = make_publish_scene(bus)
    db_path = watch_service.db_path if watch_service is not None else DEFAULT_DB_PATH
    watcher = watch_service if watch_service is not None else WatchedFolderService(db_path)
    embedding_jobs = KnowledgeEmbeddingJobManager(bus, db_path, settings_manager)
    ingest_jobs = KnowledgeIngestManager(bus, db_path, on_completed=embedding_jobs.schedule)
    uploads = KnowledgeDropUploads(db_path)
    bus.register_topic("knowledge-ingest", ingest_jobs.snapshot)
    # Session eviction must stop subsequent provider batches, even if an
    # already-running synchronous embed call has not returned yet.
    bus.knowledge_embedding_jobs = embedding_jobs

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
            # Pin the workspace BEFORE the to_thread hop: another window can
            # load a different graph while the worker is chunking this one.
            # Resolving document.current_workspace_id inside the worker would
            # write the OLD branch into the NEW workspace's collection.
            workspace_id = document.current_workspace_id
            # Each node gets a stable persisted key rather than relying on
            # node_id (reassigned on reload, reused across other graphs).
            source_key = document.branch_index_sources.setdefault(node_id, uuid4().hex)
            source_uri = f"branch:{source_key}"
            title = f"Branch (node {node_id})"

            # ADR-020 stage 20.3: same workspace scoping, but use a mutable
            # source-aware replacement instead of immutable content dedup.
            def _ingest():
                if not text.strip():
                    raise IngestError(f"'{title}' contained no indexable text.")
                chunks = chunk_text(text)
                if not chunks:
                    raise IngestError(f"'{title}' contained no indexable text.")
                collection_id = (
                    get_or_create_workspace_collection(db_path, workspace_id)
                    if workspace_id is not None else 0
                )
                return replace_branch_document_with_chunks(
                    db_path, source_uri=source_uri, title=title,
                    text=text, chunks=chunks, collection_id=collection_id,
                )

            try:
                # Same blocking-I/O concern as search() above - this branch ingest
                # does real SQLite writes (chunk + embedding-cache rows).
                await asyncio.to_thread(_ingest)
                embedding_jobs.schedule(workspace_id)
            except IngestError as exc:
                if notifications is not None:
                    notifications.show(str(exc), "error")
                    await bus.publish("notification")
                return
            # A load/new-chat may replace the graph while the DB write is
            # running. Never mark a newly assigned node id as indexed based
            # on the old node's completed operation.
            if document.nodes.get(node_id) is not node:
                return

        document.record_command(
            "setChatIndexIntoKnowledge", "user",
            lambda: document.set_chat_index_into_knowledge(node_id, enabled),
            node_ids=[node_id],
        )
        await publish_scene()


    async def begin_upload(files):
        if any(job["status"] in {"queued", "scanning", "running"} for job in ingest_jobs.snapshot()["jobs"]):
            raise IngestError("An import is already running in this window.")
        return await asyncio.to_thread(
            uploads.begin, files, workspace_id=document.current_workspace_id,
        )

    async def append_upload(upload_id, file_index, data):
        return await asyncio.to_thread(uploads.append, upload_id, file_index, data)

    async def finish_upload(upload_id):
        paths, workspace_id = await asyncio.to_thread(uploads.finish, upload_id)
        return ingest_jobs.start(paths, folder=False, workspace_id=workspace_id)

    async def cancel_upload(upload_id):
        return await asyncio.to_thread(uploads.cancel, upload_id)

    register_knowledge_search_intents(bus, document, settings_manager=settings_manager, db_path=db_path)
    register_knowledge_ingest_intents(bus, document, ingest_jobs)
    register_knowledge_watched_folder_intents(bus, document, watcher)
    register_knowledge_existing_file_intents(
        bus, document, ingest_jobs,
        composer_document=composer_document, agent_dispatcher=agent_dispatcher,
        settings_manager=settings_manager,
    )
    register_knowledge_embedding_intents(bus, document, embedding_jobs)
    register_knowledge_document_intents(bus, document, ingest_jobs, uploads, db_path)
    bus.register_intent("knowledge", "beginUpload", begin_upload, args_schema=BeginUploadArgs)
    bus.register_intent("knowledge", "appendUpload", append_upload, args_schema=AppendUploadArgs)
    bus.register_intent("knowledge", "finishUpload", finish_upload, args_schema=UploadIdArgs)
    bus.register_intent("knowledge", "cancelUpload", cancel_upload, args_schema=UploadIdArgs)
    bus.register_intent(
        "scene", "setChatIndexIntoKnowledge", set_chat_index_into_knowledge,
        args_schema=SetChatIndexIntoKnowledgeArgs,
    )
