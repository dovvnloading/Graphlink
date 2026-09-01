"""ADR-014 stage 14.3: first-party migration of the pre-SDK "Gitlink"
picker action. A byte-faithful relocation of backend/plugins.py's old
hardcoded `if name == "Gitlink":` branch - same command_type string
("pluginGitlink"), same parent-validation warning text, same factory call
(SceneDocument.add_gitlink_node), same undo/parent-validation behavior.
See plugins/web_research/plugin.py's own docstring for the shared
register_builtin_plugin escape-hatch rationale. _execute itself is
backend/plugin_sdk.py's make_simple_child_node_handler - see that
factory's own docstring for the shared validate/record_command/
return-id shape it replaces here."""

from __future__ import annotations

from backend.plugin_sdk import HostContext, make_simple_child_node_handler

_execute = make_simple_child_node_handler(
    command_type="pluginGitlink",
    warning_suffix="a Gitlink node",
    create=lambda document, parent_node_id: document.add_gitlink_node(
        *document.place_child(parent_node_id, "gitlink"), parent_node_id
    ),
    # Creatable with nothing selected: this kind never reads the parent's
    # content, so the parent was only ever a place_child anchor and an edge.
    create_standalone=lambda document, x, y: document.add_gitlink_node(x, y, None),
)


def register(host: HostContext) -> None:
    host.register_builtin_plugin(
        name="Gitlink",
        description=(
            "Loads a GitHub repository into structured XML context, prepares "
            "file-level changes, and only writes after explicit approval."
        ),
        category="Build & Execution",
        handler=_execute,
        requires_parent=False,
    )
