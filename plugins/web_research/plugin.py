"""ADR-014 stage 14.3: first-party migration of the pre-SDK "Web Research"
picker action. A byte-faithful relocation of backend/plugins.py's old
hardcoded `if name == "Web Research":` branch - same command_type string
("pluginWebResearch"), same parent-validation warning text, same factory
call (SceneDocument.add_web_research_node), same undo/parent-validation
behavior. Registered via HostContext.register_builtin_plugin, NOT
register_node_kind/register_picker_entry: the "web_research" kind string
is already baked into web_ui's NODE_TYPES map, the wire contract, and
session_save.py/session_load.py's hand-written serializer - renaming it to
a namespaced "web_research.web_research" via the generic PluginNodeSeed
path would be an invasive, unnecessary breaking change. See
HostContext.register_builtin_plugin's own docstring (backend/plugin_sdk.py)
for the full escape-hatch rationale."""

from __future__ import annotations

from backend.canvas import MESSAGE_VERTICAL_SPACING, SceneDocument
from backend.plugin_sdk import HostContext, PluginRunContext


def _execute(
    document: SceneDocument, run_ctx: PluginRunContext, parent_node_id: "str | None",
) -> "str | None":
    if not parent_node_id or parent_node_id not in document.nodes:
        run_ctx.notifications.show(
            "Please select a valid node to branch from before adding a Web Node.",
            "warning",
        )
        return None
    parent = document.nodes[parent_node_id]
    node, _command = document.record_command(
        "pluginWebResearch", "user",
        lambda: document.add_web_research_node(
            parent.x, parent.y + MESSAGE_VERTICAL_SPACING, parent_node_id
        ),
        node_ids=[parent_node_id],
    )
    return node.id


def register(host: HostContext) -> None:
    host.register_builtin_plugin(
        name="Web Research",
        description=(
            "Searches, retrieves, and summarizes cited web sources under a bounded "
            "network policy."
        ),
        category="Reasoning & Research",
        handler=_execute,
    )
