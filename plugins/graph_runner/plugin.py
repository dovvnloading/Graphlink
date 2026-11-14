"""Open the workflow runner from the first-party plugin picker."""

from backend.canvas import SceneDocument
from backend.plugin_sdk import HostContext, OpenPluginPanel, PluginRunContext


def _open_runner(
    document: SceneDocument, context: PluginRunContext, parent_node_id: str | None,
) -> OpenPluginPanel:
    return OpenPluginPanel("graph-runner")


def register(host: HostContext) -> None:
    host.register_builtin_plugin(
        name="Graph Runner",
        description="Preview connected steps, run changed outputs, and resume a saved run.",
        category="Workflow & Drafting",
        handler=_open_runner,
        requires_parent=False,
    )
