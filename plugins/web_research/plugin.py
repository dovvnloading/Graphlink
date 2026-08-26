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
for the full escape-hatch rationale. _execute itself is
backend/plugin_sdk.py's make_simple_child_node_handler - see that
factory's own docstring for the shared validate/record_command/
return-id shape it replaces here."""

from __future__ import annotations

from backend.plugin_sdk import HostContext, make_simple_child_node_handler

_execute = make_simple_child_node_handler(
    command_type="pluginWebResearch",
    warning_suffix="a Web Node",
    create=lambda document, parent_node_id: document.add_web_research_node(
        *document.place_child(parent_node_id, "web_research"), parent_node_id
    ),
)


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
