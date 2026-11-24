"""Open the workflow runner from the first-party plugin picker."""

from backend.canvas import SceneDocument
from backend.plugin_sdk import HostContext, OpenPluginPanel, PluginRunContext, PluginNodeSeed
from backend.domain.node_states import PluginStateSnapshot, PersistedPluginState
from backend.workflow.library import canonical


def _open_runner(
    document: SceneDocument, context: PluginRunContext, parent_node_id: str | None,
) -> OpenPluginPanel:
    return OpenPluginPanel("graph-runner")


def register(host: HostContext) -> None:
    def saved(node):
        return node.state.data if isinstance(node.state, (PluginStateSnapshot, PersistedPluginState)) else {}

    def seed(document, context, parent):
        return PluginNodeSeed(title="Workflow", state=PluginStateSnapshot.from_data({}))

    host.register_node_kind("workflow", seed, requires_parent=False,
                            serialize=lambda node: {"workflow": canonical(saved(node))},
                            serialize_saved=saved, deserialize=PluginStateSnapshot.from_data, externalize_state=True)
    host.register_builtin_plugin(
        name="Graph Runner",
        description="Preview connected steps, run changed outputs, and resume a saved run.",
        category="Workflow & Drafting",
        handler=_open_runner,
        requires_parent=False,
    )
