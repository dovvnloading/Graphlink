"""ADR-014 stage 14.3: first-party migration of the pre-SDK "Gitlink"
picker action. A byte-faithful relocation of backend/plugins.py's old
hardcoded `if name == "Gitlink":` branch - same command_type string
("pluginGitlink"), same parent-validation warning text, same factory call
(SceneDocument.add_gitlink_node), same undo/parent-validation behavior.
See plugins/web_research/plugin.py's own docstring for the shared
register_builtin_plugin escape-hatch rationale."""

from __future__ import annotations

from backend.canvas import SceneDocument
from backend.plugin_sdk import HostContext, PluginRunContext


def _execute(
    document: SceneDocument, run_ctx: PluginRunContext, parent_node_id: "str | None",
) -> "str | None":
    if not parent_node_id or parent_node_id not in document.nodes:
        run_ctx.notifications.show(
            "Please select a valid node to branch from before adding a Gitlink node.",
            "warning",
        )
        return None
    node, _command = document.record_command(
        "pluginGitlink", "user",
        lambda: document.add_gitlink_node(
            *document.place_child(parent_node_id, "gitlink"), parent_node_id
        ),
        node_ids=[parent_node_id],
    )
    return node.id


def register(host: HostContext) -> None:
    host.register_builtin_plugin(
        name="Gitlink",
        description=(
            "Loads a GitHub repository into structured XML context, prepares "
            "file-level changes, and only writes after explicit approval."
        ),
        category="Build & Execution",
        handler=_execute,
    )
