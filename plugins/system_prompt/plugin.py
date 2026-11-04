"""First-party System Prompt plugin.

A system prompt is a normal note with `is_system_prompt=True`, not a new
node kind. Its edge points **from the note to the branch root**, which is
the direction the chat dispatcher uses when resolving the branch persona.

The picker supports both workflows:
- Before a conversation exists: create/reuse an unattached prompt note.
- On an existing branch: reuse its attached prompt, attach an existing
  unattached note, or create a new one above that branch's root.

All mutations are single undoable `pluginSystemPrompt` commands. Read-only
reuse never adds a command or modifies the scene. The domain owns the
definition of "unattached" so adoption on the first chat node and explicit
picker attachment cannot disagree about which notes are eligible.
"""

from __future__ import annotations

import math

from backend.canvas import SceneDocument
from backend.domain.model import SceneNode
from backend.domain.node_access import is_node_of
from backend.domain.node_states import NoteState
from backend.plugin_sdk import HostContext, PluginRunContext


_COMMAND_TYPE = "pluginSystemPrompt"
_CATEGORY = "Branch Foundations"
_DESCRIPTION = (
    "Adds a special node to override the default system prompt for a "
    "conversation branch."
)
_AMBIGUOUS_PENDING_MESSAGE = (
    "Multiple unattached System Prompt notes exist. Connect or remove extra "
    "notes before assigning a prompt to a branch."
)


def _is_prompt_note(node: SceneNode | None) -> bool:
    """Protect reads of the note-only state field on arbitrary scene nodes."""
    return (
        node is not None
        and is_node_of(node, "note", NoteState)
        and node.state.is_system_prompt
    )


def _attached_prompt(document: SceneDocument, root_id: str) -> SceneNode | None:
    """Match the chat dispatcher's first note -> root edge, in edge order."""
    for edge in document.edges.values():
        if edge.target != root_id:
            continue
        candidate = document.nodes.get(edge.source)
        if _is_prompt_note(candidate):
            return candidate
    return None


def _branch_root(document: SceneDocument, selected: SceneNode | None) -> SceneNode | None:
    """Resolve non-prompt selections to a branch root.

    A freestanding ordinary note is not a chat branch. Attaching a prompt to
    that note would be inert: the dispatcher only examines note -> chat-root
    edges. Such selections use the pending-prompt workflow instead.
    """
    if selected is None:
        return None
    root = document.get_branch_root(selected.id)
    if root is None or root.kind == "note":
        return None
    return root


def _finite_coordinate(value: float | None) -> float:
    """Treat missing or non-finite viewport coordinates as the scene origin.

    NaN/Infinity must not enter node positions: they cannot round-trip
    through standards-compliant JSON, and can corrupt canvas rendering.
    """
    try:
        number = float(value) if value is not None else 0.0
    except (ValueError, TypeError, OverflowError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _create_pending(document: SceneDocument, run_ctx: PluginRunContext) -> str:
    """Create a truly unattached note at the picker-reported viewport center."""
    x = _finite_coordinate(run_ctx.spawn_x)
    y = _finite_coordinate(run_ctx.spawn_y)

    note, _ = document.record_command(
        _COMMAND_TYPE, "user",
        lambda: document.add_note(x, y, is_system_prompt=True),
    )
    return note.id


def _attach_pending(document: SceneDocument, note: SceneNode, root: SceneNode) -> str:
    """Assign the user's existing note to a branch as one undoable edge.

    Reusing the note preserves its authored content and position. Undo must
    detach the edge, NOT delete the note that existed before this operation.
    """
    def connect_pending() -> SceneNode:
        document.connect(note.id, root.id)
        return note

    attached, _ = document.record_command(
        _COMMAND_TYPE, "user", connect_pending, node_ids=[note.id, root.id],
    )
    return attached.id


def _create_attached(document: SceneDocument, root: SceneNode) -> str:
    """Create one new prompt above the branch root with the reversed edge."""
    def create_and_connect() -> SceneNode:
        x, y = document.place_child(root.id, "note", prefer="above")
        note = document.add_note(x, y, is_system_prompt=True)
        document.connect(note.id, root.id)
        return note

    note, _ = document.record_command(
        _COMMAND_TYPE, "user", create_and_connect, node_ids=[root.id],
    )
    return note.id


def _execute(
    document: SceneDocument, run_ctx: PluginRunContext, parent_node_id: str | None,
) -> str | None:
    """Resolve an existing prompt first, then assign/create only if necessary.

    Selecting a prompt note is an idempotent action: the selected note is
    already the thing the picker is offering. Never attach another prompt
    **to** that note, which would create an ineffective prompt-of-a-prompt.
    """
    selected = document.nodes.get(parent_node_id) if parent_node_id else None
    if _is_prompt_note(selected):
        return selected.id

    root = _branch_root(document, selected)
    if root is not None:
        existing = _attached_prompt(document, root.id)
        if existing is not None:
            return existing.id

    # One source of truth, shared with SceneDocument.adopt_pending_system_prompt.
    # Avoid arbitrarily choosing among multiple saved/unconnected candidates:
    # the implicit adoption path already declines this ambiguous state.
    pending = document.unattached_system_prompt_notes()
    if len(pending) > 1:
        run_ctx.notifications.show(_AMBIGUOUS_PENDING_MESSAGE, "warning")
        return None

    if root is None:
        return pending[0].id if pending else _create_pending(document, run_ctx)
    if pending:
        return _attach_pending(document, pending[0], root)
    return _create_attached(document, root)


def register(host: HostContext) -> None:
    """Keep the public built-in picker contract and persisted node kind."""
    host.register_builtin_plugin(
        name="System Prompt",
        description=_DESCRIPTION,
        category=_CATEGORY,
        handler=_execute,
        requires_parent=False,
    )
