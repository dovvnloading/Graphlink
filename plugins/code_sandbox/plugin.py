"""ADR-014 stage 14.3: first-party migration of the pre-SDK "Virtual
Environment Runner" picker action. A byte-faithful relocation of
backend/plugins.py's old hardcoded `if name == "Virtual Environment
Runner":` branch - same command_type string ("pluginCodeSandbox"), same
parent-validation warning text, same factory call
(SceneDocument.add_code_sandbox_node), same undo/parent-validation
behavior. See plugins/web_research/plugin.py's own docstring for the
shared register_builtin_plugin escape-hatch rationale. _execute itself is
backend/plugin_sdk.py's make_simple_child_node_handler - see that
factory's own docstring for the shared validate/record_command/
return-id shape it replaces here."""

from __future__ import annotations

from backend.plugin_sdk import HostContext, make_simple_child_node_handler

_execute = make_simple_child_node_handler(
    command_type="pluginCodeSandbox",
    warning_suffix="a Virtual Environment Runner node",
    create=lambda document, parent_node_id: document.add_code_sandbox_node(
        *document.place_child(parent_node_id, "code_sandbox"), parent_node_id
    ),
    # Creatable with nothing selected: this kind never reads the parent's
    # content, so the parent was only ever a place_child anchor and an edge.
    create_standalone=lambda document, x, y: document.add_code_sandbox_node(x, y, None),
)


def register(host: HostContext) -> None:
    host.register_builtin_plugin(
        name="Virtual Environment Runner",
        description=(
            "Runs Python inside an isolated virtualenv with your full user-account "
            "privileges (isolates installed packages, not the operating system) and "
            "lets you declare per-node requirements.txt dependencies."
        ),
        category="Build & Execution",
        handler=_execute,
        requires_parent=False,
    )
