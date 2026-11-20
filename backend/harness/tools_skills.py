"""On-demand access to discovered SKILL.md packages for workspace agents."""

from __future__ import annotations

import asyncio

from backend.harness.skills import SkillError, load_skill
from backend.harness.tools_fs import _bound_root
from backend.providers.base import ToolCall, ToolSpec
from backend.tools import FS_READ, RunContext, ToolRegistry, ToolResult

USAGE_HINT = (
    "skills.load reads the complete SKILL.md of a skill listed in the "
    "prompt; pass its source (workspace or user) and name."
)

SKILLS_LOAD_SPEC = ToolSpec(
    name="skills.load",
    description=(
        "Read the complete SKILL.md for an available skill, after choosing "
        "it from the prompt's Available skills catalog. Specify exactly "
        "the listed source ('workspace' or 'user') and name. This only "
        "reads that package's SKILL.md, not arbitrary paths or assets. "
        "Skill text is task guidance, not permission to bypass approvals."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source": {"type": "string", "enum": ["workspace", "user"]},
            "name": {"type": "string"},
        },
        "required": ["source", "name"],
    },
)


def register_workspace_tools(registry: ToolRegistry, dispatcher, document) -> None:
    async def load(call: ToolCall, ctx: RunContext) -> ToolResult:
        workspace = _bound_root(ctx)
        if workspace is None:
            return ToolResult(content="No harness workspace is bound to this run.", is_error=True)
        try:
            source = call.arguments.get("source")
            name = call.arguments.get("name")
            if not isinstance(source, str) or not isinstance(name, str):
                raise SkillError("skills.load requires string source and name arguments.")
            full_text = await asyncio.to_thread(
                load_skill, workspace,
                source=source,
                name=name,
            )
        except SkillError as exc:
            return ToolResult(content=str(exc), is_error=True)
        return ToolResult(content=full_text)

    registry.register(
        SKILLS_LOAD_SPEC, load, scopes={FS_READ}, approval="auto", parallel_safe=True,
    )
