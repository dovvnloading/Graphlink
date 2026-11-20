"""Adversarial regression cases for PR 721's permission modes."""
from __future__ import annotations

import asyncio

import pytest

from backend.canvas import SceneDocument
from backend.chat_library import _disarm_imported_harness_permissions
from backend.harness.loop import HarnessRunContext
from backend.harness.permission_modes import (
    may_skip_workspace_approval, requires_explicit_workspace_approval,
)
from backend.providers.base import ToolCall, ToolSpec
from backend.session_load import _restore_harness_payload
from backend.session_save import _serialize_harness_node
from backend.tools import FS_WRITE, CODE_EXECUTE, ToolRegistry, ToolResult


def _ctx(root, mode="autonomous", profile="isolated"):
    prompts = []

    async def approve(call):
        prompts.append(call)
        return True

    return HarnessRunContext(
        granted_scopes=frozenset({FS_WRITE, CODE_EXECUTE}),
        request_approval=approve,
        harness_node_id="agent", harness_workspace_id="scratch",
        harness_workspace_dir=root, harness_shell="bash",
        harness_execution_profile=profile, harness_permission_mode=mode,
    ), prompts


@pytest.mark.parametrize("command", [
    "bash ./run.sh", "sh ./run.sh", "python ./build.py",
    "python3 -m http.server", "git status", "git -c core.pager=sh status",
    "find . -exec sh", "xargs sh", "make all", "busybox wget",
    "./script", "env python main.py", "sed -i src/file.txt",
])
def test_autonomous_does_not_auto_run_interpreters_wrappers_or_repo_helpers(tmp_path, command):
    ctx, _ = _ctx(tmp_path)
    call = ToolCall(id="cmd", name="shell.exec", arguments={"command": command})
    assert not may_skip_workspace_approval(call, ctx)
    assert requires_explicit_workspace_approval(call, ctx)


@pytest.mark.parametrize("command", [
    "echo hello", "pwd", "ls", "ls -la", "ls src",
    "cat src/notes.txt", "head src/notes.txt", "touch src/file.txt",
    "mkdir -p src/subdir",
])
def test_narrow_literal_shell_operations_still_work(tmp_path, command):
    (tmp_path / "src").mkdir()
    ctx, _ = _ctx(tmp_path)
    call = ToolCall(id="safe", name="shell.exec", arguments={"command": command})
    assert may_skip_workspace_approval(call, ctx)


@pytest.mark.parametrize("command", [
    "cat escape", "touch escape/new.txt", "ls escape",
    "cat ../outside/secret.txt", "touch /tmp/not-workspace",
    "mkdir -p ../outside", "cat --help",
])
def test_bare_or_explicit_file_operands_cannot_escape_workspace(tmp_path, command):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    ctx, _ = _ctx(root)
    call = ToolCall(id="path", name="shell.exec", arguments={"command": command})
    assert not may_skip_workspace_approval(call, ctx)
    assert requires_explicit_workspace_approval(call, ctx)


@pytest.mark.parametrize("code", [
    "print(1 + 2)",
    "import math\nprint(math.sqrt(9))",
    "open('notes.txt', 'w').write('ok')",
    # A source allowlist can miss this indirect outside-workspace file write:
    "import io\np = bytes([47,116,109,112,47,111,117,116]).decode()\n"
    "writer = io.open\nwriter(p, 'w').write('unsafe')",
    # Earlier REPL turns can shadow builtins or safe-looking callables.
    "print('previously shadowed function')",
])
def test_stateful_python_execution_requires_fresh_approval(tmp_path, code):
    ctx, _ = _ctx(tmp_path)
    call = ToolCall(id="py", name="python.exec", arguments={"code": code})
    assert not may_skip_workspace_approval(call, ctx)
    assert requires_explicit_workspace_approval(call, ctx)


def test_saved_autonomous_mode_and_grants_do_not_rearm():
    node = SceneDocument().add_harness_node(0, 0, "Review")
    state = node.state
    state.harness_execution_profile = "isolated"
    state.harness_permission_mode = "autonomous"
    state.harness_task_permission_mode = "autonomous"
    state.harness_network_always = True
    state.harness_shell_allow_rules = ["bash\techo"]
    payload = _serialize_harness_node(node)
    restored = _restore_harness_payload(payload).state
    assert payload["permission_mode"] == "autonomous"
    assert restored.harness_execution_profile == "isolated"
    assert restored.harness_permission_mode == "ask"
    assert restored.harness_task_permission_mode == "autonomous"
    assert restored.harness_network_always is False
    assert restored.harness_shell_allow_rules == []


def test_archive_import_sanitizes_harness_grants_without_editing_other_nodes():
    data = {"nodes": [
        {"node_type": "harness", "permission_mode": "autonomous",
         "task_permission_mode": "autonomous", "shell_allow_rules": ["bash\techo"],
         "network_always": True},
        {"node_type": "chat", "content": "preserved"},
    ]}
    assert _disarm_imported_harness_permissions(data) is data
    harness, chat = data["nodes"]
    assert harness["permission_mode"] == "ask"
    assert harness["shell_allow_rules"] == []
    assert harness["network_always"] is False
    assert harness["task_permission_mode"] == "autonomous"
    assert chat == {"node_type": "chat", "content": "preserved"}


def test_pending_file_approval_rejects_changed_path_and_content(tmp_path):
    state = {"calls": 0}
    pending = ToolCall(id="file", name="fs.write",
                       arguments={"path": "safe.txt", "content": "original"})

    async def approve(disclosed):
        assert disclosed.arguments == {"path": "safe.txt", "content": "original"}
        pending.arguments["path"] = "other.txt"
        pending.arguments["content"] = "different"
        return True

    async def handler(call, ctx):
        state["calls"] += 1
        return ToolResult(content="wrote")

    ctx, _ = _ctx(tmp_path, mode="ask", profile="host")
    ctx.request_approval = approve
    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="fs.write", description="write", input_schema={"type": "object"}),
        handler, scopes={FS_WRITE}, approval="once",
    )
    result = asyncio.run(registry.invoke(pending, ctx))
    assert result.is_error
    assert "changed while approval" in result.content
    assert state["calls"] == 0


def test_auto_edit_uses_immutable_file_arguments(tmp_path):
    pending = ToolCall(id="file", name="fs.write",
                       arguments={"path": "safe.txt", "content": "original"})
    observed = []

    async def handler(call, ctx):
        pending.arguments["path"] = "../outside.txt"
        pending.arguments["content"] = "changed"
        observed.append(dict(call.arguments))
        return ToolResult(content="wrote")

    ctx, prompts = _ctx(tmp_path, mode="auto_edit", profile="host")
    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="fs.write", description="write", input_schema={"type": "object"}),
        handler, scopes={FS_WRITE}, approval="once",
    )
    result = asyncio.run(registry.invoke(pending, ctx))
    assert not result.is_error
    assert not prompts
    assert observed == [{"path": "safe.txt", "content": "original"}]
