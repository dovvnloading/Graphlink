"""ADR-014 stage 14.3: first-party migration of the pre-SDK "System Prompt"
picker action. A byte-faithful relocation of backend/plugins.py's old
hardcoded `if name == "System Prompt":` branch - the one built-in whose
shape genuinely differs from the other 7 (a note placed ABOVE the branch
root, not a child fanned below the selected node):

- With NO selection, creates the note unattached at the picker's reported
  viewport center; SceneDocument.adopt_pending_system_prompt then connects
  it to the first branch root created afterwards. A system prompt is
  authored before the branch it governs, so requiring an existing node
  (the pre-fix behavior) forced a wasted send just to unlock the action.
- Resolves parent_node_id's BRANCH ROOT (SceneDocument.get_branch_root),
  the same parent-edge walk backend/agents.py's
  _resolve_branch_system_prompt uses at send time - the note attaches to
  that root, not to whichever node was actually selected.
- A root can only ever have ONE effective system-prompt note
  (_resolve_branch_system_prompt has no deterministic "which one wins"
  rule for two at once) - reuses an existing one instead of creating a
  silently-inert duplicate, returning its id with NO new node created.
- The only branch that both creates AND connects in one record_command
  call - and the edge direction is REVERSED from every other built-in's
  root -> child edge: note -> root, the exact shape
  _resolve_branch_system_prompt looks for.

Same command_type string ("pluginSystemPrompt"), same parent-validation
warning text, same dedup/reversed-edge/branch-root-walk logic as the
branch it replaces. See plugins/web_research/plugin.py's own docstring for
the shared register_builtin_plugin escape-hatch rationale."""

from __future__ import annotations

from backend.canvas import SceneDocument
from backend.plugin_sdk import HostContext, PluginRunContext


def _execute(
    document: SceneDocument, run_ctx: PluginRunContext, parent_node_id: "str | None",
) -> "str | None":
    if not parent_node_id or parent_node_id not in document.nodes:
        # No selection: create the prompt UNATTACHED, at the viewport center.
        # A system prompt is the one node that is naturally authored BEFORE
        # the branch it governs - requiring a node to exist first meant
        # sending a message just to earn the right to set the prompt that
        # should have shaped it. SceneDocument.adopt_pending_system_prompt
        # connects this note to the first branch root that appears, which is
        # the note -> root edge _resolve_branch_system_prompt looks for.
        return _create_pending(document, run_ctx)
    root = document.get_branch_root(parent_node_id)
    existing = next(
        (
            document.nodes[edge.source]
            for edge in document.edges.values()
            if edge.target == root.id
            and edge.source in document.nodes
            and document.nodes[edge.source].kind == "note"
            and document.nodes[edge.source].state.is_system_prompt
        ),
        None,
    )
    if existing is not None:
        return existing.id

    def _create_system_prompt_note():
        nx, ny = document.place_child(root.id, "note", prefer="above")
        created = document.add_note(nx, ny, is_system_prompt=True)
        document.connect(created.id, root.id)
        return created

    note, _command = document.record_command(
        "pluginSystemPrompt", "user", _create_system_prompt_note,
        node_ids=[root.id],
    )
    return note.id


def _create_pending(
    document: SceneDocument, run_ctx: PluginRunContext,
) -> "str | None":
    """Creates (or reuses) the unattached system-prompt note.

    Reuse mirrors the attached path's own dedup rule: a second pending note
    would be just as inert as a second attached one, since only the first
    can ever be adopted by a root."""
    existing = next(
        (
            node
            for node in document.nodes.values()
            if node.kind == "note"
            and node.state.is_system_prompt
            and not any(edge.source == node.id for edge in document.edges.values())
        ),
        None,
    )
    if existing is not None:
        return existing.id

    x = float(run_ctx.spawn_x or 0.0)
    y = float(run_ctx.spawn_y or 0.0)

    def _create():
        return document.add_note(x, y, is_system_prompt=True)

    note, _command = document.record_command("pluginSystemPrompt", "user", _create)
    return note.id


def register(host: HostContext) -> None:
    host.register_builtin_plugin(
        name="System Prompt",
        description=(
            "Adds a special node to override the default system prompt for a "
            "conversation branch."
        ),
        category="Branch Foundations",
        handler=_execute,
        # Creatable on an empty canvas: see _execute's own no-selection path.
        requires_parent=False,
    )
