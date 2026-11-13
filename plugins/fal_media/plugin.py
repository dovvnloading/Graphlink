"""Built-in Fal Media Studio entry in the existing Graphlink plugin picker.

Uses the same undoable, collision-aware placement as Artifact/Web Research.
A Fal studio is a native, persistent kind with a dedicated ReactFlow view,
not an ungrantable third-party plugin node.
"""
from __future__ import annotations

from backend.domain.node_states import FalMediaState
from backend.plugin_sdk import HostContext, make_simple_child_node_handler


_execute = make_simple_child_node_handler(
    command_type="pluginFalMedia",
    warning_suffix="a Fal Media Studio node",
    create=lambda document, parent_node_id: document.add_plugin_node(
        "fal_media", *document.place_child(parent_node_id, "fal_media"),
        parent_node_id, title="Fal Media Studio", state=FalMediaState(),
    ),
    create_standalone=lambda document, x, y: document.add_plugin_node(
        "fal_media", x, y, None, title="Fal Media Studio", state=FalMediaState(),
    ),
)


def register(host: HostContext) -> None:
    host.register_builtin_plugin(
        name="Fal Media Studio",
        description="Search the Fal model catalog, compare prices, and run models with schema-specific inputs.",
        category="Build & Execution",
        handler=_execute,
        requires_parent=False,
    )
