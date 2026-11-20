"""Discover workspace-agent tools by file, with no central import/registration list.

To add a workspace-agent tool, add one backend/harness/tools_*.py module:
  * USAGE_HINT: a single-line summary of when to use its tool(s);
  * register_workspace_tools(registry, dispatcher, document): register each
    ToolSpec and handler on the passed ToolRegistry with explicit scopes and
    approval policy. Use dispatcher for session-lived resources if needed.

Modules are loaded in filename order, so both registration and the system
prompt's tool guide are stable between turns. The Agent's fork of the Builder
registry reuses the existing graph, knowledge and Fal tool registrations
without rebuilding their handlers or exposing workspace-only tools to Builder.
"""

from __future__ import annotations

from importlib import import_module
from pkgutil import iter_modules
from types import ModuleType

from backend.tools import ToolRegistry


def tool_modules() -> tuple[ModuleType, ...]:
    package = import_module("backend.harness")
    names = sorted(
        entry.name for entry in iter_modules(package.__path__)
        if not entry.ispkg and entry.name.startswith("tools_")
    )
    modules = tuple(import_module(f"{package.__name__}.{name}") for name in names)
    for module in modules:
        hint = getattr(module, "USAGE_HINT", None)
        register = getattr(module, "register_workspace_tools", None)
        if not isinstance(hint, str) or not hint.strip() or hint != hint.strip() or "\n" in hint:
            raise ValueError(f"{module.__name__} must expose a one-line USAGE_HINT")
        if not callable(register):
            raise ValueError(f"{module.__name__} must expose register_workspace_tools")
    return modules


def register_tool_modules(registry: ToolRegistry, dispatcher, document) -> None:
    for module in tool_modules():
        module.register_workspace_tools(registry, dispatcher, document)


def tool_guide() -> str:
    """Stable, file-driven guidance, included in the system prompt once/run."""
    return (
        "Tool guide (only tools offered in your tool schemas are available):\n"
        + "\n".join(f"- {module.USAGE_HINT}" for module in tool_modules())
    )
