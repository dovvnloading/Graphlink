"""Offline integration tests for Isolated shell.exec network consent.

The real harness loop, registry, disclosure future and shell tool are used.
Only the model response and the child process are faked; no Docker or
network connections are required.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

import api_provider
from backend.agent_dispatch.harness import HarnessDispatchOps
from backend.domain.graph import SceneDocument
from backend.harness import workspace as workspace_module
from backend.harness.loop import run_harness
from backend.harness.tools_shell import register_harness_shell_tool
from backend.providers.base import ToolCall
from backend.run_lifecycle import RunRegistry
from backend.tools import ToolRegistry


class _Dispatcher:
    approve_harness_network = HarnessDispatchOps.approve_harness_network

    def __init__(self):
        self._runs = RunRegistry()


class _Bus:
    async def publish(self, topic: str):
        pass


def _script(monkeypatch, turns):
    remaining = list(turns)

    def fake_turn(task, messages, tools=(), **kwargs):
        if not remaining:
            raise AssertionError("model called after scripted turns ended")
        return {
            "message": {"role": "assistant", "content": ""},
            "tool_calls": remaining.pop(0),
            "usage": None,
        }

    monkeypatch.setattr(api_provider, "chat_turn_with_tools", fake_turn)


async def _run_with_decisions(document, node, dispatcher, registry, *, choice):
    event = threading.Event()
    handle = dispatcher._runs.claim("harness", node_id=node.id, cancel_event=event)
    approvals = []

    async def run():
        try:
            await run_harness(
                document=document, dispatcher=dispatcher, registry=registry,
                bus=_Bus(), notifications=None, harness_node_id=node.id,
                user_text="run the command", request_id=handle.request_id,
                handle=handle, cancel_event=event,
            )
        finally:
            dispatcher._runs.release(handle.request_id)

    task = asyncio.create_task(run())
    try:
        while not task.done():
            future = handle.approval_future
            if node.state.harness_awaiting_approval and future is not None and not future.done():
                if node.state.harness_approval_network_offered:
                    token = node.state.harness_approval_id
                    assert token
                    assert "Why network is needed: Read dependency metadata" in node.state.harness_approval_summary
                    # Replayed and stale approval ids must fail closed.
                    assert not dispatcher.approve_harness_network(handle.request_id, "wrong-token")
                    if choice == "once":
                        assert dispatcher.approve_harness_network(handle.request_id, token)
                    elif choice == "task":
                        assert dispatcher.approve_harness_network(handle.request_id, token, for_task=True)
                    elif choice == "forged":
                        future.set_result(True)  # A normal tool approval is NOT network consent.
                    else:
                        future.set_result(False)
                    approvals.append("network")
                else:
                    future.set_result(True)  # Ordinary execution approval is still required.
                    approvals.append("ordinary")
            await asyncio.sleep(0)
        await task
    finally:
        if not task.done():
            event.set()
            await task
    return approvals


@pytest.mark.parametrize("choice,expected_network", [
    ("once", False),
    ("task", True),
])
def test_network_once_or_task_grant_scopes_container_launch(
    tmp_path, monkeypatch, choice, expected_network,
):
    monkeypatch.setattr(workspace_module, "HARNESS_WORKSPACE_ROOT", tmp_path)
    document = SceneDocument()
    node = document.add_harness_node(0, 0, "fetch dependencies")
    node.state.harness_execution_profile = "isolated"
    registry = ToolRegistry()
    register_harness_shell_tool(registry)
    dispatcher = _Dispatcher()
    calls = []

    def fake_run(command, cwd, cancel, output=None, shell="auto", profile="host", allow_network=False):
        calls.append((command, profile, allow_network))
        return "", 0, "ok"

    monkeypatch.setattr("backend.harness.tools_shell._run_command", fake_run)
    _script(monkeypatch, [
        [ToolCall(id="fetch-1", name="shell.exec", arguments={
            "command": "curl example.org", "network": True,
            "network_reason": "Read dependency metadata",
        })],
        [ToolCall(id="check-2", name="shell.exec", arguments={"command": "ls"})],
        [],
    ])
    approvals = asyncio.run(_run_with_decisions(document, node, dispatcher, registry, choice=choice))
    assert approvals == ["network", "ordinary"]
    assert calls == [
        ("curl example.org", "isolated", True),
        ("ls", "isolated", expected_network),
    ]
    assert node.state.harness_approval_network_offered is False
    assert node.state.harness_network_always is False


@pytest.mark.parametrize("choice", ["forged", "deny"])
def test_network_denial_never_spawns_networked_command(tmp_path, monkeypatch, choice):
    monkeypatch.setattr(workspace_module, "HARNESS_WORKSPACE_ROOT", tmp_path)
    document = SceneDocument()
    node = document.add_harness_node(0, 0, "no remote access")
    node.state.harness_execution_profile = "isolated"
    registry = ToolRegistry()
    register_harness_shell_tool(registry)
    dispatcher = _Dispatcher()
    calls = []
    monkeypatch.setattr(
        "backend.harness.tools_shell._run_command",
        lambda *args, **kwargs: calls.append(args) or ("", 0, "ok"),
    )
    _script(monkeypatch, [
        [ToolCall(id="fetch", name="shell.exec", arguments={
            "command": "curl example.org", "network": True,
            "network_reason": "Read dependency metadata",
        })],
        [],
    ])
    approvals = asyncio.run(_run_with_decisions(document, node, dispatcher, registry, choice=choice))
    assert approvals == ["network"]
    assert not calls


def test_persistent_node_opt_in_still_requires_command_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_module, "HARNESS_WORKSPACE_ROOT", tmp_path)
    document = SceneDocument()
    node = document.add_harness_node(0, 0, "run builds")
    node.state.harness_execution_profile = "isolated"
    node.state.harness_network_always = True
    registry = ToolRegistry()
    register_harness_shell_tool(registry)
    dispatcher = _Dispatcher()
    calls = []

    def fake_run(command, cwd, cancel, output=None, shell="auto", profile="host", allow_network=False):
        calls.append((profile, allow_network))
        return "", 0, "ok"

    monkeypatch.setattr("backend.harness.tools_shell._run_command", fake_run)
    _script(monkeypatch, [
        [ToolCall(id="build", name="shell.exec", arguments={"command": "echo build"})],
        [],
    ])
    approvals = asyncio.run(_run_with_decisions(document, node, dispatcher, registry, choice="once"))
    assert approvals == ["ordinary"]
    assert calls == [("isolated", True)]

def test_task_network_grant_does_not_cross_task_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_module, "HARNESS_WORKSPACE_ROOT", tmp_path)
    document = SceneDocument()
    node = document.add_harness_node(0, 0, "two tasks")
    node.state.harness_execution_profile = "isolated"
    registry = ToolRegistry()
    register_harness_shell_tool(registry)
    dispatcher = _Dispatcher()
    calls = []

    def fake_run(command, cwd, cancel, output=None, shell="auto", profile="host", allow_network=False):
        calls.append(allow_network)
        return "", 0, "ok"

    monkeypatch.setattr("backend.harness.tools_shell._run_command", fake_run)
    _script(monkeypatch, [
        [ToolCall(id="fetch-1", name="shell.exec", arguments={
            "command": "curl example.org", "network": True,
            "network_reason": "Read dependency metadata",
        })],
        [],
    ])
    assert asyncio.run(_run_with_decisions(document, node, dispatcher, registry, choice="task")) == ["network"]

    # Same node and registry, new RunContext; temporary consent has expired.
    _script(monkeypatch, [
        [ToolCall(id="next-1", name="shell.exec", arguments={"command": "ls"})],
        [],
    ])
    assert asyncio.run(_run_with_decisions(document, node, dispatcher, registry, choice="once")) == ["ordinary"]
    assert calls == [True, False]
