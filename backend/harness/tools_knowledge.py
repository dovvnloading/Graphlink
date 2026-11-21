"""Approval-gated knowledge.add, offered only to the workspace agent.

The Agent's catalog forks Builder's read-only catalog. Only this discovered
workspace tool may ingest; the destination collection is derived from the
session's current workspace, not from a model-supplied ID. Source bytes are
read with the same bounded, no-follow open as fs.read, then extracted from
a private temporary snapshot to avoid a path swap between approval and
ingestion. The source file is never changed.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from api_provider import _raise_if_cancelled
from backend.attachments import AttachmentError
from backend.harness.tools_fs import _is_metadata_path, _read_bounded_bytes
from backend.harness.workspace import WorkspaceError, resolve_under_root
from backend.knowledge_ingest import IngestError, extract_text, ingest_text, is_supported_knowledge_file
from backend.knowledge_store import DEFAULT_DB_PATH, get_or_create_workspace_collection
from backend.providers.base import ToolCall, ToolSpec
from backend.tools import FS_READ, KNOWLEDGE_WRITE, RunContext, ToolRegistry, ToolResult

USAGE_HINT = (
    "knowledge.search reads cited, untrusted workspace knowledge; knowledge.add "
    "indexes an existing file from THIS agent's workspace into this graph "
    "workspace's collection, with a fresh human approval each time."
)

KNOWLEDGE_ADD_SPEC = ToolSpec(
    name="knowledge.add",
    description=(
        "With human approval, index an existing file inside YOUR bound "
        "workspace in the current graph workspace's persistent knowledge "
        "collection. Supports PDF, DOCX, HTML, Markdown, and readable text/code. "
        "The original file is not modified; duplicate contents are a no-op. "
        "Requires approval for every call. Never accepts a collection ID."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File inside the agent's bound workspace (relative or absolute inside it).",
            },
        },
        "required": ["path"],
    },
)


def _validated_source(root: Path, raw_path: str) -> Path:
    """Validate only the source path/type; execution repeats this after consent."""
    source = resolve_under_root(root, raw_path)
    if _is_metadata_path(source):
        raise WorkspaceError("VCS metadata cannot be added to knowledge.")
    if not source.is_file():
        raise WorkspaceError(f"Not a workspace file: {raw_path!r}.")
    if not is_supported_knowledge_file(source):
        raise IngestError(f"Unsupported knowledge file type: {source.name}.")
    root.resolve()  # Reject a replaced pinned workspace before asking for consent.
    return source


def _snapshot_source(root: Path, raw_path: str) -> tuple[Path, str, str]:
    """Confine and open the ORIGINAL bytes before parsing, including Office files.

    extract_text accepts paths, so a private, auto-cleaned snapshot supplies
    the same safe extraction pipeline without reopening a model-controlled
    original pathname after it might have become an escaping symlink.
    """
    source = _validated_source(root, raw_path)
    data = _read_bounded_bytes(source)
    root.resolve()  # Verify a pinned workspace was not swapped during the read.
    with TemporaryDirectory(prefix="graphlink-knowledge-add-") as directory:
        staged = Path(directory) / source.name
        staged.write_bytes(data)
        text, mime = extract_text(staged)
    return source, text, mime


def register_workspace_tools(
    registry: ToolRegistry, dispatcher, document, *, db_path: Path | None = None,
) -> None:
    """Register only on the Agent fork. Builder cannot invoke this write tool."""
    target_db = db_path if db_path is not None else DEFAULT_DB_PATH

    async def preflight_add(call: ToolCall, ctx: RunContext) -> str | None:
        """Fail invalid file requests before the registry opens an approval card."""
        raw_path = call.arguments.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip() or len(raw_path) > 4096:
            return "knowledge.add requires a valid workspace file path."
        root = getattr(ctx, "harness_workspace_dir", None)
        if not isinstance(root, Path):
            return "knowledge.add requires an active agent workspace."
        try:
            await asyncio.to_thread(_validated_source, root, raw_path)
        except (OSError, WorkspaceError, IngestError, UnicodeError, ValueError) as exc:
            return f"Could not add workspace file to knowledge: {exc}"
        return None

    async def add(call: ToolCall, ctx: RunContext) -> ToolResult:
        raw_path = call.arguments.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip() or len(raw_path) > 4096:
            return ToolResult(content="knowledge.add requires a valid workspace file path.", is_error=True)

        # Do not accept a standalone registry invocation as authorization:
        # a live harness run and its root, plus a graph workspace, are needed.
        root = getattr(ctx, "harness_workspace_dir", None)
        agent_id = getattr(ctx, "harness_node_id", None)
        run_id = getattr(ctx, "run_id", None)
        workspace_id = document.current_workspace_id
        if not isinstance(root, Path) or not agent_id or not run_id or workspace_id is None:
            return ToolResult(content="knowledge.add requires an active agent and current workspace.", is_error=True)

        def active() -> bool:
            agent = document.nodes.get(agent_id)
            return (
                agent is not None and agent.kind == "harness"
                and getattr(agent.state, "harness_run_id", None) == run_id
            )

        if not active():
            return ToolResult(content="This workspace-agent run is no longer active.", is_error=True)

        identity = (document.current_chat_id, workspace_id)
        try:
            source, text, mime = await asyncio.to_thread(_snapshot_source, root, raw_path)
            _raise_if_cancelled(ctx.cancel.event if ctx.cancel is not None else None)
            if (document.current_chat_id, document.current_workspace_id) != identity or not active():
                return ToolResult(content="The active agent/chat/workspace changed during file loading.", is_error=True)
            root.resolve()  # Recheck the pinned grant before a persistent write.

            def store():
                _raise_if_cancelled(ctx.cancel.event if ctx.cancel is not None else None)
                collection_id = get_or_create_workspace_collection(target_db, workspace_id)
                _raise_if_cancelled(ctx.cancel.event if ctx.cancel is not None else None)
                return ingest_text(
                    text, source_uri=str(source), title=source.name, mime=mime,
                    collection_id=collection_id, db_path=target_db,
                )

            outcome = await asyncio.to_thread(store)
        except (OSError, WorkspaceError, AttachmentError, IngestError, UnicodeError, ValueError) as exc:
            return ToolResult(content=f"Could not add workspace file to knowledge: {exc}", is_error=True)
        return ToolResult(content=json.dumps({
            "title": source.name,
            "source_uri": str(source),
            "document_id": outcome.document_id,
            "chunk_count": outcome.chunk_count,
            "was_new": outcome.was_new,
            "destination": "current workspace knowledge collection",
        }, ensure_ascii=False))

    # knowledge.write is a distinct capability, not covered by knowledge.read.
    # No session grant and no auto-edit exemption: tools.py refuses name-wide
    # consent, so a repeated call reaches the human approval gate again.
    registry.register(
        KNOWLEDGE_ADD_SPEC, add, scopes={FS_READ, KNOWLEDGE_WRITE}, approval="once",
        pre_approval_check=preflight_add,
    )
