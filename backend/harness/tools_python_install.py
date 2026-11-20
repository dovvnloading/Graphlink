"""The approval-gated python.install workspace tool."""

from __future__ import annotations

from backend.harness.python_install import (
    OFFLINE_WHEELS_UNAVAILABLE, install_packages, install_requirements_error,
    planned_install_interpreter, workspace_install_binding,
)
from backend.harness.permission_modes import may_skip_workspace_approval
from backend.providers.base import ToolCall, ToolSpec
from backend.tools import CODE_EXECUTE, RunContext, ToolRegistry, ToolResult

PYTHON_INSTALL_SPEC = ToolSpec(
    name="python.install",
    description=(
        "Install packages into this workspace's Python environment (the same "
        "environment used by python.exec). Supply a list of package names "
        "with optional extras/version constraints, not pip flags, URLs, "
        "requirements files, or paths. Each install requires fresh user "
        "approval displaying the exact environment and packages. Only binary "
        "wheels from the default PyPI index are downloaded; compatible wheels "
        "are kept in a shared cache for future offline installs. In Isolated "
        "mode Python runs in a container: network is enabled only for the "
        "approved pip download step, never for python.exec."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "packages": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 32,
                "description": "Index-resolved package requirements, e.g. ['pandas>=2,<3'].",
            },
        },
        "required": ["packages"],
        "additionalProperties": False,
    },
)


def register_harness_python_install_tool(registry: ToolRegistry) -> None:
    async def python_install(call: ToolCall, ctx: RunContext) -> ToolResult:
        error = install_requirements_error(call.arguments)
        if error is not None:
            return ToolResult(content=error, is_error=True)
        if may_skip_workspace_approval(call, ctx):
            # Autonomous is Isolated-only. Pin the workspace interpreter for
            # a no-network cached-wheel attempt, even without a human prompt.
            workspace_id, root, is_scratch = workspace_install_binding(ctx)
            target = planned_install_interpreter(
                workspace_id, root, is_scratch=is_scratch, profile="isolated",
            )
            ctx.python_install_approved_interpreter = target
            ctx.python_install_approved_binding = (
                workspace_id, root.resolve(), is_scratch, "isolated", target,
            )
            offline = await install_packages(
                ctx, call.arguments["packages"], offline_only=True,
            )
            if offline.content != OFFLINE_WHEELS_UNAVAILABLE:
                return offline
            # A cache miss is a network request. Use the real approval panel,
            # with its fresh token and pinned interpreter, before retrying.
            if not await ctx.request_approval(call):
                return ToolResult(
                    content="PyPI download was denied approval; cached wheels were insufficient.",
                    is_error=True,
                )
        return await install_packages(ctx, call.arguments["packages"])

    registry.register(
        PYTHON_INSTALL_SPEC,
        python_install,
        scopes={CODE_EXECUTE},
        approval="always",
        # Each install is a new environment mutation, even if the package
        # names are identical. Session grants and fingerprints never suffice.
        always_reprompt=lambda _call: True,
    )


USAGE_HINT = (
    "python.install adds named packages to the same workspace virtualenv "
    "as python.exec, using PyPI binary wheels and a shared offline wheel cache; "
    "each install needs fresh approval for the environment and packages. "
    "Isolated installs use the container; only PyPI downloads get network access."
)


def register_workspace_tools(registry: ToolRegistry, dispatcher, document) -> None:
    register_harness_python_install_tool(registry)
