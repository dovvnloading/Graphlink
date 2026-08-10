"""ADR-014 stage 14.3: first-party migration of the pre-SDK "System Prompt"
picker action. A byte-faithful relocation of backend/plugins.py's old
hardcoded `if name == "System Prompt":` branch - the one built-in whose
shape genuinely differs from the other 7 (a branch-point CHILD of
parent_node_id, one MESSAGE_VERTICAL_SPACING below it):

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
        run_ctx.notifications.show(
            "Please select a valid node to branch from before adding a System Prompt node.",
            "warning",
        )
        return None
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
        created = document.add_note(root.x, root.y - 150, is_system_prompt=True)
        document.connect(created.id, root.id)
        return created

    note, _command = document.record_command(
        "pluginSystemPrompt", "user", _create_system_prompt_note,
        node_ids=[root.id],
    )
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
    )
