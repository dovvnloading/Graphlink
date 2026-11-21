"""User-defined workspace hook integration and security regressions."""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

import api_provider
from backend.domain.graph import SceneDocument
from backend.harness import tools_shell, workspace as workspace_module
from backend.harness.hooks import HOOK_TIMEOUT_SECONDS, validated_hooks
from backend.harness.loop import run_harness
from backend.harness.tools_fs import register_harness_fs_tools
from backend.harness.tools_shell import register_harness_shell_tool
from backend.harness.transcript import flush, transcript_path
from backend.harness.workspace import ensure_workspace
from backend.providers.base import ToolCall
from backend.run_lifecycle import RunRegistry
from backend.session_load import _restore_harness_payload
from backend.session_save import _serialize_harness_node
from backend.tools import ToolRegistry
from graphlink_process_env import safe_subprocess_env


class Bus:
    async def publish(self, topic):
        assert topic == "scene"


class Dispatcher:
    def __init__(self):
        self._runs = RunRegistry()

    def harness_machine_snapshot(self, workspace_id, root, shell="auto", profile="host"):
        return "test machine"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_module, "HARNESS_WORKSPACE_ROOT", tmp_path / "harness")
    document = SceneDocument()
    node = document.add_harness_node(0, 0, "inspect the workspace")
    workspace = ensure_workspace(node.state.harness_workspace_id)
    (workspace / "note.txt").write_text("workspace content", encoding="utf-8")
    registry = ToolRegistry()
    register_harness_fs_tools(registry)
    register_harness_shell_tool(registry)
    return document, node, workspace, registry


def configure(node, phase="before_tool", command="echo hook", enabled=True):
    node.state.harness_hooks = validated_hooks([
        {"phase": phase, "command": command, "enabled": enabled},
    ], strict=True)


def script(monkeypatch, tools=True):
    turns = [{
        "role": "assistant", "content": "",
        "tool_calls": [ToolCall(id="real-tool-1", name="fs.read", arguments={"path": "note.txt"})],
    }] if tools else []
    turns.append({"role": "assistant", "content": "Finished", "tool_calls": []})
    seen = []

    def provider(task, messages, tools=(), **kwargs):
        seen.append(list(messages))
        entry = turns.pop(0)
        return {"message": {"role": "assistant", "content": entry["content"]},
                "tool_calls": entry["tool_calls"], "usage": None}

    monkeypatch.setattr(api_provider, "chat_turn_with_tools", provider)
    return seen


def run(document, node, registry, *, approve=True):
    dispatcher = Dispatcher()

    async def main():
        event = threading.Event()
        handle = dispatcher._runs.claim("harness", node_id=node.id, cancel_event=event)

        async def task_body():
            try:
                await run_harness(
                    document=document, dispatcher=dispatcher, registry=registry,
                    bus=Bus(), notifications=None, harness_node_id=node.id,
                    user_text="Read the note", request_id=handle.request_id,
                    handle=handle, cancel_event=event,
                )
            finally:
                dispatcher._runs.release(handle.request_id)

        task = asyncio.create_task(task_body())
        prompts = []
        for _ in range(5000):
            if task.done():
                break
            future = handle.approval_future
            if node.state.harness_awaiting_approval and future is not None and not future.done():
                prompts.append((
                    node.state.harness_approval_summary,
                    node.state.harness_approval_session_offered,
                ))
                future.set_result(approve)
            await asyncio.sleep(0.001)
        await asyncio.wait_for(task, 4)
        return prompts

    return asyncio.run(main())


def audit_lines(workspace):
    assert flush(workspace)
    return [json.loads(line) for line in transcript_path(workspace).read_text(
        encoding="utf-8",
    ).splitlines()]


def test_before_hook_blocks_tool_with_reason_visible_to_model_and_user(setup, monkeypatch):
    document, node, workspace, registry = setup
    configure(node, command="echo reject")
    seen = script(monkeypatch)
    shell_calls = []

    def fake_run(command, root, cancel, output, shell, profile, network, timeout):
        shell_calls.append((command, root, profile, network, timeout))
        return "Blocked: policy requires a human review", 13, "ok"

    monkeypatch.setattr(tools_shell, "_run_command", fake_run)
    prompts = run(document, node, registry)
    assert node.state.harness_status == "done"
    assert len(prompts) == 1 and "Workspace before tool hook" in prompts[0][0]
    assert prompts[0][1] is False, "hook approval cannot save an always-allow rule"
    assert len(shell_calls) == 1
    assert shell_calls[0][1] == workspace
    assert shell_calls[0][-1] == HOOK_TIMEOUT_SECONDS
    assert any(row["tool"] == "hook.before_tool" and row["outcome"] == "error"
               and "Blocked: policy" in row["summary"] for row in node.state.harness_activity)
    assert any(row["tool"] == "fs.read" and row["outcome"] == "error"
               and "Blocked by workspace" in row["summary"] for row in node.state.harness_activity)
    hook_events = [row for row in audit_lines(workspace) if row["t"] == "hook"]
    assert [row["outcome"] for row in hook_events] == ["running", "error"]
    assert "policy" in hook_events[-1]["result"]
    real_result = next(row for row in audit_lines(workspace)
                       if row["t"] == "msg" and row.get("tool_call_id") == "real-tool-1")
    assert "Blocked by workspace before-tool hook" in real_result["content"]
    assert "Blocked by workspace" in str(seen[1])


def test_denied_hook_approval_blocks_tool_without_starting_shell(setup, monkeypatch):
    document, node, workspace, registry = setup
    configure(node, command="echo no")
    script(monkeypatch)
    monkeypatch.setattr(tools_shell, "_run_command",
                        lambda *args: pytest.fail("denied hook launched a command"))
    prompts = run(document, node, registry, approve=False)
    assert len(prompts) == 1
    tool_row = next(row for row in audit_lines(workspace)
                    if row["t"] == "msg" and row.get("tool_call_id") == "real-tool-1")
    assert "denied approval" in tool_row["content"]
    assert [row["outcome"] for row in audit_lines(workspace) if row["t"] == "hook"] == ["running", "error"]


def test_after_hook_adds_transcript_note_and_does_not_replace_tool_result(setup, monkeypatch):
    document, node, workspace, registry = setup
    configure(node, phase="after_tool", command="echo add context")
    seen = script(monkeypatch)
    monkeypatch.setattr(tools_shell, "_run_command", lambda *args: ("Result verified by hook", 0, "ok"))
    run(document, node, registry)
    assert node.state.harness_status == "done"
    tool_row = next(row for row in audit_lines(workspace)
                    if row["t"] == "msg" and row.get("tool_call_id") == "real-tool-1")
    assert "workspace content" in tool_row["content"]
    assert "[Workspace after-tool hook note]\nResult verified by hook" in tool_row["content"]
    assert "Result verified by hook" in str(seen[1])
    assert any(row["tool"] == "hook.after_tool" and row["outcome"] == "ok"
               for row in node.state.harness_activity)


def test_failed_after_hook_does_not_change_the_original_tool_result(setup, monkeypatch):
    document, node, workspace, registry = setup
    configure(node, phase="after_tool", command="exit 1")
    script(monkeypatch)
    monkeypatch.setattr(tools_shell, "_run_command", lambda *args: ("bad hook", 1, "ok"))
    run(document, node, registry)
    tool_row = next(row for row in audit_lines(workspace)
                    if row["t"] == "msg" and row.get("tool_call_id") == "real-tool-1")
    assert "workspace content" in tool_row["content"]
    assert "bad hook" not in tool_row["content"]
    assert any(row["tool"] == "hook.after_tool" and row["outcome"] == "error"
               for row in node.state.harness_activity)


def test_end_hook_once_at_terminal_run_without_tools(setup, monkeypatch):
    document, node, workspace, registry = setup
    configure(node, phase="end_run", command="echo finished")
    script(monkeypatch, tools=False)
    commands = []
    def fake_run(command, *args):
        commands.append(command)
        return "", 0, "ok"
    monkeypatch.setattr(tools_shell, "_run_command", fake_run)
    run(document, node, registry)
    assert commands == ["echo finished"]
    assert node.state.harness_status == "done"
    assert [row["outcome"] for row in audit_lines(workspace) if row["t"] == "hook"] == ["running", "ok"]


def test_timeout_is_bounded_and_blocks_before_tool(setup, monkeypatch):
    document, node, workspace, registry = setup
    configure(node, command="echo never-ending")
    script(monkeypatch)
    timeout_values = []

    def fake_run(*args):
        timeout_values.append(args[-1])
        return "partial output", None, "timeout"

    monkeypatch.setattr(tools_shell, "_run_command", fake_run)
    run(document, node, registry)
    assert timeout_values == [10]
    result = next(row for row in audit_lines(workspace)
                  if row["t"] == "msg" and row.get("tool_call_id") == "real-tool-1")
    assert "Command timed out after 10s" in result["content"]
    assert "partial output" in result["content"]


def test_isolated_hook_never_changes_to_host_or_inherits_network_or_secrets(setup, monkeypatch):
    document, node, workspace, registry = setup
    node.state.harness_execution_profile = "isolated"
    node.state.harness_permission_mode = "autonomous"
    node.state.harness_network_always = True
    node.state.harness_shell_allow_rules = ["untrusted\tunsafe"]
    configure(node, command="echo isolated")
    script(monkeypatch)
    monkeypatch.setenv("GRAPH_LINK_SECRET_FOR_TEST", "never-leak")
    assert "GRAPH_LINK_SECRET_FOR_TEST" not in safe_subprocess_env()
    observed = []
    original_invoke = registry.invoke

    async def inspect(call, ctx):
        if call.id.startswith("hook-"):
            observed.append((ctx.harness_execution_profile,
                             ctx.harness_permission_mode, ctx.harness_network_always,
                             ctx.shell_allow_rules, ctx.runtime, ctx.model_ref,
                             ctx.settings_manager, dict(call.arguments)))
        return await original_invoke(call, ctx)

    registry.invoke = inspect

    def fake_run(command, root, cancel, output, shell, profile, network, timeout):
        assert profile == "isolated" and root == workspace
        assert network is False
        return "ok", 0, "ok"

    monkeypatch.setattr(tools_shell, "_run_command", fake_run)
    run(document, node, registry)
    assert observed == [(
        "isolated", "ask", False, [], None, None, None,
        {"command": "echo isolated", "network": False},
    )]


def test_missing_isolation_engine_is_reported_and_blocks_hook(setup, monkeypatch):
    from backend.harness.shell_isolation import IsolationUnavailableError

    document, node, workspace, registry = setup
    node.state.harness_execution_profile = "isolated"
    configure(node, command="echo isolated")
    seen = script(monkeypatch)

    def no_engine(*args, **kwargs):
        raise IsolationUnavailableError("No container engine; Host execution was NOT attempted.")

    monkeypatch.setattr(tools_shell, "container_invocation", no_engine)
    monkeypatch.setattr(tools_shell, "spawn_guarded_subprocess",
                        lambda *args, **kwargs: pytest.fail("host subprocess launched"))

    prompts = run(document, node, registry)
    assert len(prompts) == 1
    assert node.state.harness_status == "done"
    hook_rows = [row for row in node.state.harness_activity if row["tool"] == "hook.before_tool"]
    assert len(hook_rows) == 1 and hook_rows[0]["outcome"] == "error"
    assert "No container engine" in hook_rows[0]["summary"]
    tool_result = next(row for row in audit_lines(workspace)
                       if row["t"] == "msg" and row.get("tool_call_id") == "real-tool-1")
    assert "Blocked by workspace before-tool hook" in tool_result["content"]
    assert "No container engine" in tool_result["content"]
    assert "Host execution was NOT attempted" in tool_result["content"]
    assert "No container engine" in str(seen[1])


def test_missing_isolation_engine_fails_closed_before_host_spawn(tmp_path, monkeypatch):
    from backend.harness.shell_isolation import IsolationUnavailableError

    def no_engine(*args, **kwargs):
        raise IsolationUnavailableError("No container engine")

    monkeypatch.setattr(tools_shell, "container_invocation", no_engine)
    monkeypatch.setattr(tools_shell, "spawn_guarded_subprocess",
                        lambda *args, **kwargs: pytest.fail("host subprocess launched"))
    with pytest.raises(IsolationUnavailableError, match="container engine"):
        tools_shell._run_command("echo isolated", tmp_path, None, profile="isolated", timeout_seconds=10)


def test_disabled_hooks_never_request_approval_or_spawn_commands(setup, monkeypatch):
    document, node, workspace, registry = setup
    configure(node, phase="end_run", command="echo disabled", enabled=False)
    script(monkeypatch, tools=False)
    monkeypatch.setattr(tools_shell, "_run_command",
                        lambda *args: pytest.fail("disabled hook launched"))
    assert run(document, node, registry) == []
    assert not [row for row in audit_lines(workspace) if row["t"] == "hook"]


def test_hook_settings_intents_validate_and_persist_without_running(setup):
    from backend.api.intents_harness import register_harness_preferences_intents
    from backend.events import SessionBus

    document, node, workspace, registry = setup
    bus = SessionBus("test-hooks")
    published = []

    async def publish_scene():
        published.append(True)

    register_harness_preferences_intents(bus, document, None, None, publish_scene)

    async def main():
        assert await bus.dispatch_intent("harness", "getHooks", [node.id]) == []
        hooks = [{"phase": "before_tool", "command": "echo safe", "enabled": True}]
        assert await bus.dispatch_intent("harness", "setHooks", [node.id, hooks]) == hooks
        assert await bus.dispatch_intent("harness", "getHooks", [node.id]) == hooks
        with pytest.raises(ValueError):
            await bus.dispatch_intent("harness", "setHooks", [node.id, [
                {"phase": "before_tool", "command": "echo not safe", "enabled": "yes"},
            ]])
        assert await bus.dispatch_intent("harness", "getHooks", [node.id]) == hooks

    asyncio.run(main())
    assert len(published) == 1


def test_hooks_persist_without_preserving_any_execution_approval(setup):
    document, node, workspace, registry = setup
    configure(node, phase="after_tool", command="echo saved")
    payload = _serialize_harness_node(node)
    restored = _restore_harness_payload(payload).state
    assert restored.harness_hooks == node.state.harness_hooks
    assert restored.harness_hooks is not node.state.harness_hooks
    assert restored.harness_shell_allow_rules == []
    assert restored.harness_permission_mode == "ask"
    payload["hooks"] = [{"phase": "before_tool", "command": "echo bad", "enabled": "true"}]
    assert _restore_harness_payload(payload).state.harness_hooks == []


@pytest.mark.parametrize("invalid", [
    None,
    [{}],
    [{"phase": "before_tool", "command": "echo ok", "enabled": 1}],
    [{"phase": "before_tool", "command": "  ", "enabled": True}],
    [{"phase": "before_tool", "command": "hi\x00there", "enabled": True}],
    [{"phase": "unknown", "command": "echo ok", "enabled": True}],
    [{"phase": "end_run", "command": "echo ok", "enabled": True}] * 9,
])
def test_invalid_hook_config_is_never_executed(invalid):
    with pytest.raises(ValueError):
        validated_hooks(invalid, strict=True)
    assert validated_hooks(invalid) == []
