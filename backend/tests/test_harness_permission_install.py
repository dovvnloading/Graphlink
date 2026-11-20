"""Autonomous installs must not quietly turn a cache miss into network access."""
from __future__ import annotations

import asyncio
from pathlib import PurePosixPath

import pytest

from backend.harness.loop import HarnessRunContext
from backend.harness.python_install import OFFLINE_WHEELS_UNAVAILABLE
from backend.harness.tools_python_install import register_harness_python_install_tool
from backend.providers.base import ToolCall
from backend.tools import CODE_EXECUTE, ToolRegistry, ToolResult
import backend.harness.tools_python_install as install_tool


@pytest.mark.parametrize("cache_hit,approve_network,expected_calls", [
    (True, True, [True]),
    (False, True, [True, False]),
    (False, False, [True]),
])
def test_installer_only_downloads_after_fresh_approval(
    tmp_path, monkeypatch, cache_hit, approve_network, expected_calls,
):
    prompts = []
    calls = []

    async def approve(call):
        prompts.append(call)
        return approve_network

    ctx = HarnessRunContext(
        granted_scopes=frozenset({CODE_EXECUTE}),
        request_approval=approve,
        harness_workspace_dir=tmp_path,
        harness_workspace_id="ws-one",
        harness_node_id="agent-one",
        harness_execution_profile="isolated",
        harness_permission_mode="autonomous",
    )
    monkeypatch.setattr(
        install_tool, "workspace_install_binding",
        lambda _ctx: ("ws-one", tmp_path, True),
    )
    monkeypatch.setattr(
        install_tool, "planned_install_interpreter",
        lambda *args, **kwargs: PurePosixPath("/workspace/.venv/bin/python"),
    )

    async def fake_install(ctx, packages, *, offline_only=False):
        calls.append(offline_only)
        if offline_only and not cache_hit:
            return ToolResult(content=OFFLINE_WHEELS_UNAVAILABLE, is_error=True)
        return ToolResult(content="installed")

    monkeypatch.setattr(install_tool, "install_packages", fake_install)
    registry = ToolRegistry()
    register_harness_python_install_tool(registry)
    result = asyncio.run(registry.invoke(
        ToolCall(id="install-one", name="python.install", arguments={"packages": ["six==1.16.0"]}),
        ctx,
    ))
    assert calls == expected_calls
    assert len(prompts) == (0 if cache_hit else 1)
    assert result.is_error == (not cache_hit and not approve_network)


def test_host_install_does_not_bypass_approval(tmp_path, monkeypatch):
    prompts = []

    async def deny(call):
        prompts.append(call)
        return False

    ctx = HarnessRunContext(
        granted_scopes=frozenset({CODE_EXECUTE}), request_approval=deny,
        harness_workspace_dir=tmp_path, harness_workspace_id="ws-one",
        harness_node_id="agent-one", harness_execution_profile="host",
        harness_permission_mode="autonomous",
    )
    registry = ToolRegistry()
    register_harness_python_install_tool(registry)
    result = asyncio.run(registry.invoke(
        ToolCall(id="i", name="python.install", arguments={"packages": ["six"]}),
        ctx,
    ))
    assert result.is_error
    assert len(prompts) == 1
