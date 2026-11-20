"""Workspace-agent adapter for tools already shared with the Builder.

The Builder populates a base ToolRegistry first; the workspace Agent forks
that catalog so it can reuse handlers without publishing Agent-only tools to
Builder. The conditional fallbacks also support standalone registries.
Implementation, scope and approval policy remain in the shared modules.
"""

from __future__ import annotations

from backend.tools import ToolRegistry

USAGE_HINT = (
    "knowledge.search reads ingested knowledge; graph.* reads and builds on "
    "the canvas; for fal.* media tasks, search models, inspect model_io and "
    "pricing, submit approved jobs, then check status/result before retrying "
    "(use pointers and bindings for large or chained results)."
)


def register_workspace_tools(registry: ToolRegistry, dispatcher, document) -> None:
    # No duplicates on the ordinary path: builder_tool_registry already
    # registered each of these with its original scope and consent policy.
    if registry.scopes_for("graph.list_nodes") is None:
        from backend.tools_graph import register_graph_tools, register_run_node_tool

        register_graph_tools(registry, document, getattr(dispatcher, "_settings_manager", None))
        register_run_node_tool(registry, document, dispatcher)
    if registry.scopes_for("knowledge.search") is None:
        from backend.tools_knowledge import register_knowledge_tools

        register_knowledge_tools(registry, document=document)
    if registry.scopes_for("fal.search_models") is None:
        from backend.tools_fal import register_fal_tools

        register_fal_tools(registry, getattr(dispatcher, "_settings_manager", None))
