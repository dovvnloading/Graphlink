"""ADR-014 stage 14.3: first-party migration of the pre-SDK "HTML
Renderer" picker action. A byte-faithful relocation of
backend/plugins.py's old hardcoded `if name == "HTML Renderer":` branch -
same command_type string ("pluginHtmlRenderer"), same parent-validation
warning text, same factory call (SceneDocument.add_html_node, starting
with empty html_content - the plugin picker has no field to source
initial HTML from), same undo/parent-validation behavior. See
plugins/web_research/plugin.py's own docstring for the shared
register_builtin_plugin escape-hatch rationale. _execute itself is
backend/plugin_sdk.py's make_simple_child_node_handler - see that
factory's own docstring for the shared validate/record_command/
return-id shape it replaces here."""

from __future__ import annotations

from backend.plugin_sdk import HostContext, make_simple_child_node_handler

_execute = make_simple_child_node_handler(
    command_type="pluginHtmlRenderer",
    warning_suffix="an HTML Renderer node",
    create=lambda document, parent_node_id: document.add_html_node(
        *document.place_child(parent_node_id, "html"), "", parent_node_id
    ),
)


def register(host: HostContext) -> None:
    host.register_builtin_plugin(
        name="HTML Renderer",
        description="Adds a node to render HTML code from a parent node.",
        category="Build & Execution",
        handler=_execute,
    )
