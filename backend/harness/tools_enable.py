"""On-demand schemas for small-window workspace models.

The full ToolRegistry still owns handlers, scopes and approvals. Enabling a
schema only changes what the next model turn may request; it never invokes
or authorizes the selected tool.
"""

from __future__ import annotations

from backend.providers.base import ToolSpec
from backend.tools import FS_READ, ToolRegistry, ToolResult

USAGE_HINT = (
    "tools.enable exposes up to three other workspace tools on the next turn "
    "when running with a small local model; it does not execute them."
)

ENABLE_SPEC = ToolSpec(
    name="tools.enable",
    description=(
        "Replace the extra tools exposed on the next turn with up to three "
        "names from the system prompt's available-tool list. Does not run "
        "a tool or grant approval; select tools first, then call them."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "names": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 3,
            },
        },
        "required": ["names"],
    },
)


def register_workspace_tools(registry: ToolRegistry, dispatcher, document) -> None:
    async def enable(call, ctx):
        names = call.arguments.get("names")
        if (
            not isinstance(names, list) or not 1 <= len(names) <= 3
            or any(not isinstance(name, str) or not name for name in names)
            or len(set(names)) != len(names)
        ):
            return ToolResult("Provide one to three distinct tool names.", is_error=True)
        available = getattr(ctx, "harness_available_tool_names", frozenset())
        if any(name not in available for name in names):
            return ToolResult("An unknown or unavailable tool was requested.", is_error=True)
        fits = getattr(ctx, "harness_enable_fits", None)
        if not callable(fits) or not fits(tuple(names)):
            return ToolResult(
                "Those tool schemas leave too little room for model context. "
                "Enable fewer tools or increase the served context window.",
                is_error=True,
            )
        ctx.harness_enabled_tool_names = tuple(names)
        return ToolResult(
            "Available starting next model turn: " + ", ".join(names)
            + ". Use their normal arguments and approval rules."
        )

    registry.register(ENABLE_SPEC, enable, scopes={FS_READ}, approval="auto")
