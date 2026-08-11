"""The SPA chat-library topic's wire contract (Qt-removal plan R2.5e, R8a
library redesign).

Started field-for-field identical to graphlink_chat_library_payload.py's
(now-deleted, Qt-era) ChatLibraryStatePayload; the Qt app is fully gone as
of R7.6b, so there is no longer any legacy shape to mirror. R8a adds what
the redesigned list needs to show real per-row content instead of just a
title and two timestamps: createdAtIso/updatedAtIso (real parseable
instants, for date-bucketed grouping - createdLabel/updatedLabel stay as
human display strings, not meant to be parsed back), preview (a one-line
snippet of the last message, computed once at save time - see
backend/chat_library.py's _extract_preview_and_message_count), and
messageCount.

ADR-020 stage 20.2 adds the real Chat Library UI's own data: each row now
carries which workspace it belongs to (`workspaceId`) plus its favorite/
archived flags and tag list, and the state payload gains a full
`workspaces` list (the switcher's own tab data) alongside `rows` - see
backend/chat_library.py's get_all_chats/get_all_workspaces for how each is
built. `tags` is already-sorted (by tag name, case-insensitively),
deduped, and case-normalized-for-display server-side (backend/
chat_library.py's set_graph_tags/_normalize_tags) - the frontend renders it
as-is rather than re-normalizing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AppChatLibraryRowPayload:
    id: int
    title: str
    createdLabel: str
    updatedLabel: str
    createdAtIso: str | None
    updatedAtIso: str | None
    preview: str
    messageCount: int
    # ADR-020 stage 20.2.
    workspaceId: int
    favorite: bool
    archived: bool
    tags: list[str]


@dataclass
class AppWorkspaceRowPayload:
    """ADR-020 stage 20.2: one row per workspace, always sent regardless of
    its own archived state (this whole topic's own "send everything, filter
    locally" design - see backend/chat_library.py's get_all_workspaces) -
    the real ChatLibraryDialog switcher hides archived workspaces from its
    tabs by default, client-side, same as it already does for archived
    graphs.

    ADR-020 stage 20.3: defaultModelProvider/defaultModelId are this
    workspace's own model-routing default (the new "workspace" rung in
    graphlink_model_catalog.resolve_model_ref's node->branch->workspace->auto
    chain) - empty string on BOTH means "no workspace default set", matching
    workspaces.default_model_provider/default_model_id's own NOT NULL
    DEFAULT '' column shape, not a null/omitted-field sentinel. Defaulted
    to "" here (rather than left required) so this stage's frontend work can
    land and typecheck against a real generated wire type ahead of the
    concurrent backend stage 20.3 commit that populates get_all_workspaces
    with real values and adds the corresponding DB columns + setWorkspaceDefaultModel
    intent handler - the same "frontend commit lands referencing a topic the
    backend hasn't wired a handler for yet, backend's own commit completes
    it" sequencing stage 20.2's own two commits (5999fbf then 4bd1185) already
    used for createWorkspace/renameWorkspace/archiveWorkspace."""

    id: int
    name: str
    icon: str
    archived: bool
    defaultModelProvider: str = ""
    defaultModelId: str = ""


@dataclass
class AppChatLibraryStatePayload:
    schemaVersion: int
    revision: int
    rows: list[AppChatLibraryRowPayload]
    # ADR-020 stage 20.2: the workspace switcher's own data.
    workspaces: list[AppWorkspaceRowPayload]
    notice: str | None = None
    minCompatibleSchemaVersion: int | None = None
