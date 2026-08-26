"""ADR-014 stage 14.3: first-party migration of the pre-SDK "Artifact /
Drafter" picker action. A byte-faithful relocation of backend/plugins.py's
old hardcoded `if name == "Artifact / Drafter":` branch - same
command_type string ("pluginArtifact"), same parent-validation warning
text, same factory call (SceneDocument.add_artifact_node), same
undo/parent-validation behavior. See plugins/web_research/plugin.py's own
docstring for the shared register_builtin_plugin escape-hatch rationale.
_execute itself is backend/plugin_sdk.py's make_simple_child_node_handler -
see that factory's own docstring for the shared validate/record_command/
return-id shape it replaces here."""

from __future__ import annotations

from backend.plugin_sdk import HostContext, make_simple_child_node_handler

_execute = make_simple_child_node_handler(
    command_type="pluginArtifact",
    warning_suffix="an Artifact node",
    create=lambda document, parent_node_id: document.add_artifact_node(
        *document.place_child(parent_node_id, "artifact"), parent_node_id
    ),
)


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
