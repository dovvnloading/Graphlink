"""Permission-mode regression checks: scopes, paths, process/network prompts and task audits."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from backend.harness.loop import HarnessRunContext
from backend.harness.permission_modes import (
    ASK, AUTO_EDIT, AUTONOMOUS, effective_permission_mode,
    may_skip_workspace_approval, normalize_permission_mode,
)
from backend.harness.transcript import append_task_start, flush, load_messages, transcript_path
from backend.providers.base import ToolCall, ToolSpec
from backend.tools import CODE_EXECUTE, FS_WRITE, NET_FETCH, ToolRegistry, ToolResult


def context(root: Path, *, mode: str = ASK, profile: str = "host"):
    prompts = []

    async def approve(call):
        prompts.append(call)
        return True

    ctx = HarnessRunContext(
        granted_scopes=frozenset({FS_WRITE, CODE_EXECUTE, NET_FETCH}),
        request_approval=approve,
        harness_workspace_dir=root,
        harness_workspace_id="test-scratch",
        harness_node_id="agent-1",
        harness_execution_profile=profile,
        harness_permission_mode=mode,
        harness_shell="bash",
    )
    return ctx, prompts


def registry_for(name: str):
    registry = ToolRegistry()

    async def handler(call, _ctx):
        return ToolResult(content="handler ran")

    scope = FS_WRITE if name.startswith("fs.") else NET_FETCH if name.startswith("web.") else CODE_EXECUTE
    registry.register(
        ToolSpec(name=name, description=name, input_schema={"type": "object"}),
        handler, scopes={scope}, approval="always" if name not in ("fs.write", "fs.edit") else "once",
    )
    return registry


def invoke(name, ctx, **arguments):
    return asyncio.run(registry_for(name).invoke(
        ToolCall(id="call-1", name=name, arguments=arguments), ctx,
    ))


@pytest.mark.parametrize("mode,profile,expected", [
    ("ask", "host", "ask"),
    ("auto_edit", "host", "auto_edit"),
    ("auto_edit", "isolated", "auto_edit"),
    ("autonomous", "isolated", "autonomous"),
    ("autonomous", "host", "ask"),
    ("made-up", "isolated", "ask"),
    (True, "isolated", "ask"),
])
def test_mode_validation_and_isolation_ceiling(mode, profile, expected):
    assert effective_permission_mode(mode, profile) == expected
    assert normalize_permission_mode("unknown") == ASK


@pytest.mark.parametrize("mode,profile,prompts_expected", [
    (ASK, "host", 1),
    (AUTO_EDIT, "host", 0),
    (AUTO_EDIT, "isolated", 0),
    (AUTONOMOUS, "host", 1),
    (AUTONOMOUS, "isolated", 0),
])
def test_workspace_writes_only_bypass_in_permitted_modes(tmp_path, mode, profile, prompts_expected):
    ctx, prompts = context(tmp_path, mode=mode, profile=profile)
    result = invoke("fs.write", ctx, path="src/hello.txt", content="new content")
    assert result == ToolResult(content="handler ran")
    assert len(prompts) == prompts_expected


def test_outside_paths_and_symlink_escapes_still_prompt(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    for path in ("../outside/private", "escape/secret", str(outside / "secret")):
        ctx, prompts = context(root, mode=AUTONOMOUS, profile="isolated")
        invoke("fs.edit", ctx, path=path, old_string="before", new_string="after")
        assert len(prompts) == 1


@pytest.mark.parametrize("command,should_prompt", [
    ("echo hello", False),
    ("git status", False),
    ("touch src/file.txt", False),
    ("rm -rf src", True),
    ("git push origin main", True),
    ("curl https://example.org", True),
    ("git fetch origin", True),
    ("npm install", True),
    ("echo safe; rm -rf src", True),
    ("cat ../secret", True),
    ("touch /tmp/elsewhere", True),
    ("python -c print(1)", True),
])
def test_autonomous_shell_requires_prompt_for_danger_paths_or_network(tmp_path, command, should_prompt):
    (tmp_path / "src").mkdir()
    ctx, prompts = context(tmp_path, mode=AUTONOMOUS, profile="isolated")
    invoke("shell.exec", ctx, command=command)
    assert bool(prompts) == should_prompt


def test_shell_network_flag_always_prompts_even_for_harmless_command(tmp_path):
    ctx, prompts = context(tmp_path, mode=AUTONOMOUS, profile="isolated")
    invoke("shell.exec", ctx, command="echo hello", network=True,
           network_reason="need to send the result")
    assert len(prompts) == 1


@pytest.mark.parametrize("source,should_prompt", [
    ("print(1 + 2)", False),
    ("import math\nprint(math.sqrt(9))", False),
    ("open('notes.txt', 'w').write('ok')", False),
    ("open('../private', 'w').write('not ok')", True),
    ("import requests\nrequests.get('https://example.org')", True),
    ("import subprocess\nsubprocess.run(['ls'])", True),
    ("Path('/etc/passwd').read_text()", True),
    ("exec('print(1)')", True),
])
def test_autonomous_python_source_inspection(tmp_path, source, should_prompt):
    ctx, prompts = context(tmp_path, mode=AUTONOMOUS, profile="isolated")
    invoke("python.exec", ctx, code=source)
    assert bool(prompts) == should_prompt


def test_sensitive_calls_defeat_saved_session_and_shell_prefix_grants(tmp_path):
    ctx, prompts = context(tmp_path, mode=AUTO_EDIT, profile="host")
    ctx.session_grants = {"fs.write"}
    invoke("fs.write", ctx, path="../outside.txt", content="unsafe")
    assert len(prompts) == 1, "a saved file-tool grant cannot approve outside paths"

    ctx, prompts = context(tmp_path, mode=ASK, profile="isolated")
    ctx.shell_allow_rules = ["bash\\ttouch"]
    invoke("shell.exec", ctx, command="touch ../outside.txt")
    assert len(prompts) == 1, "a saved shell prefix cannot approve an outside path"

    ctx, prompts = context(tmp_path, mode=ASK, profile="isolated")
    ctx.shell_allow_rules = ["bash\\techo"]
    invoke("shell.exec", ctx, command="echo hello", network=True, network_reason="test")
    assert len(prompts) == 1, "a saved shell prefix cannot approve networking"


def test_network_tools_and_graph_tools_are_never_exempted(tmp_path):
    ctx, prompts = context(tmp_path, mode=AUTONOMOUS, profile="isolated")
    invoke("web.search", ctx, query="library")
    assert len(prompts) == 1
    assert not may_skip_workspace_approval(
        ToolCall(id="x", name="graph.delete_node", arguments={}), ctx,
    )


def test_unbound_context_cannot_inherit_a_permission_mode(tmp_path):
    ctx, _ = context(tmp_path, mode=AUTONOMOUS, profile="isolated")
    ctx.harness_workspace_dir = None
    assert not may_skip_workspace_approval(
        ToolCall(id="x", name="fs.write", arguments={"path": "safe.txt"}), ctx,
    )


def test_per_task_mode_is_recorded_without_entering_model_history(tmp_path):
    profile = {"root": str(tmp_path.resolve()), "isUserDir": False}
    append_task_start(
        tmp_path, run_id="task-one", permission_mode=AUTO_EDIT,
        execution_profile="host", profile=profile, root=tmp_path,
    )
    append_task_start(
        tmp_path, run_id="task-two", permission_mode=AUTONOMOUS,
        execution_profile="isolated", profile=profile, root=tmp_path,
    )
    assert flush(tmp_path)
    records = [json.loads(line) for line in transcript_path(tmp_path).read_text().splitlines()]
    task_rows = [row for row in records if row.get("t") == "task"]
    assert [(row["run_id"], row["permission_mode"]) for row in task_rows] == [
        ("task-one", AUTO_EDIT), ("task-two", AUTONOMOUS),
    ]
    assert load_messages(tmp_path) == []
