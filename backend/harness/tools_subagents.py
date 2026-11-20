"""Workspace-agent registration and usage hint for the existing subagent tool.

The implementation stays in backend.harness.subagents, so the child's
read-only grant, depth-one limit and auto approval policy are unchanged.
"""

from __future__ import annotations

from backend.harness.subagents import register_subagent_tool
from backend.tools import ToolRegistry

USAGE_HINT = (
    "subagent.spawn delegates a focused read-only investigation to a helper "
    "with its own context; it returns one summary, and you make changes yourself."
)


def register_workspace_tools(registry: ToolRegistry, dispatcher, document) -> None:
    register_subagent_tool(registry)
