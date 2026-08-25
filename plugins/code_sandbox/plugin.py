"""ADR-014 stage 14.3: first-party migration of the pre-SDK "Virtual
Environment Runner" picker action. A byte-faithful relocation of
backend/plugins.py's old hardcoded `if name == "Virtual Environment
Runner":` branch - same command_type string ("pluginCodeSandbox"), same
parent-validation warning text, same factory call
(SceneDocument.add_code_sandbox_node), same undo/parent-validation
behavior. See plugins/web_research/plugin.py's own docstring for the
shared register_builtin_plugin escape-hatch rationale."""

from __future__ import annotations

from backend.canvas import SceneDocument
from backend.plugin_sdk import HostContext, PluginRunContext


def _execute(
    document: SceneDocument, run_ctx: PluginRunContext, parent_node_id: "str | None",
) -> "str | None":
    if not parent_node_id or parent_node_id not in document.nodes:
        run_ctx.notifications.show(
            "Please select a valid node to branch from before adding a Virtual "
            "Environment Runner node.",
            "warning",
        )
        return None
    node, _command = document.record_command(
        "pluginCodeSandbox", "user",
        lambda: document.add_code_sandbox_node(
            *document.place_child(parent_node_id, "code_sandbox"), parent_node_id
        ),
        node_ids=[parent_node_id],
    )
    return node.id


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
    )
