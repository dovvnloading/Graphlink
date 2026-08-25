"""ADR-014 stage 14.3: first-party migration of the pre-SDK "Conversation
Node" picker action. A byte-faithful relocation of backend/plugins.py's
old hardcoded `if name == "Conversation Node":` branch - same
command_type string ("pluginConversationNode"), same parent-validation
warning text, same factory call (SceneDocument.add_conversation_node),
same undo/parent-validation behavior. See plugins/web_research/plugin.py's
own docstring for the shared register_builtin_plugin escape-hatch
rationale."""

from __future__ import annotations

from backend.canvas import SceneDocument
from backend.plugin_sdk import HostContext, PluginRunContext


def _execute(
    document: SceneDocument, run_ctx: PluginRunContext, parent_node_id: "str | None",
) -> "str | None":
    if not parent_node_id or parent_node_id not in document.nodes:
        run_ctx.notifications.show(
            "Please select a valid node to branch from before adding a Conversation Node.",
            "warning",
        )
        return None
    node, _command = document.record_command(
        "pluginConversationNode", "user",
        lambda: document.add_conversation_node(
            *document.place_child(parent_node_id, "conversation"), parent_node_id
        ),
        node_ids=[parent_node_id],
    )
    return node.id


def register(host: HostContext) -> None:
    host.register_builtin_plugin(
        name="Conversation Node",
        description="Adds a node for a self-contained, linear chat conversation.",
        category="Branch Foundations",
        handler=_execute,
    )
