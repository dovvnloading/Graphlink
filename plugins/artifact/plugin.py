"""ADR-014 stage 14.3: first-party migration of the pre-SDK "Artifact /
Drafter" picker action. A byte-faithful relocation of backend/plugins.py's
old hardcoded `if name == "Artifact / Drafter":` branch - same
command_type string ("pluginArtifact"), same parent-validation warning
text, same factory call (SceneDocument.add_artifact_node), same
undo/parent-validation behavior. See plugins/web_research/plugin.py's own
docstring for the shared register_builtin_plugin escape-hatch rationale."""

from __future__ import annotations

from backend.canvas import MESSAGE_VERTICAL_SPACING, SceneDocument
from backend.plugin_sdk import HostContext, PluginRunContext


def _execute(
    document: SceneDocument, run_ctx: PluginRunContext, parent_node_id: "str | None",
) -> "str | None":
    if not parent_node_id or parent_node_id not in document.nodes:
        run_ctx.notifications.show(
            "Please select a valid node to branch from before adding an Artifact node.",
            "warning",
        )
        return None
    parent = document.nodes[parent_node_id]
    node, _command = document.record_command(
        "pluginArtifact", "user",
        lambda: document.add_artifact_node(
            parent.x, parent.y + MESSAGE_VERTICAL_SPACING, parent_node_id
        ),
        node_ids=[parent_node_id],
    )
    return node.id


def register(host: HostContext) -> None:
    host.register_builtin_plugin(
        name="Artifact / Drafter",
        description=(
            "A split-pane node for iteratively drafting and refining living documents "
            "(Markdown)."
        ),
        category="Workflow & Drafting",
        handler=_execute,
    )
