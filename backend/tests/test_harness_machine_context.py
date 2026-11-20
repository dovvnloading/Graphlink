"""The workspace agent's machine snapshot is stable, accurate and session-scoped."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import time

from backend.agent_dispatch.harness import HarnessDispatchOps
from backend.harness import context as context_module


def test_machine_snapshot_describes_execution_environment_without_running_commands(
    tmp_path, monkeypatch,
):
    # PATH must match the allowlisted environment passed to shell subprocesses,
    # not a hard-coded set of commands or a guessed PATH.
    monkeypatch.setattr(
        context_module, "safe_subprocess_env", lambda: {"Path": "/toolchain/bin"},
    )
    checks = []

    def fake_which(command, *, path):
        checks.append((command, path))
        return {"git": "/toolchain/bin/git", "uv": "/toolchain/bin/uv"}.get(command)

    monkeypatch.setattr(context_module.shutil, "which", fake_which)
    snapshot = context_module.describe_machine(tmp_path)

    assert f"OS: {context_module.platform.platform()}" in snapshot
    assert context_module.platform.machine() in snapshot
    assert f"Python (python.exec): {context_module.platform.python_version()}" in snapshot
    assert str(tmp_path.resolve()) in snapshot
    assert "Command shell (shell.exec / shell.session):" in snapshot
    assert "- git: /toolchain/bin/git" in snapshot
    assert "- uv: /toolchain/bin/uv" in snapshot
    for absent in ("node", "npm", "docker", "podman", "rg"):
        assert f"- {absent}: not on PATH" in snapshot
    assert checks == [(name, "/toolchain/bin") for name in context_module._COMMON_COMMANDS]


def test_command_shell_is_the_execution_shell_not_the_login_shell(monkeypatch):
    monkeypatch.setattr(context_module.shutil, "which", lambda _name, *, path: None)
    assert context_module._command_shell(
        {"SHELL": "/bin/zsh"}, os_name="posix",
    ) == "/bin/bash --noprofile --norc -c"
    assert context_module._command_shell(
        {"COMSPEC": r"C:\Windows\System32\cmd.exe"}, os_name="nt", shell="cmd",
    ) == r"C:\Windows\System32\cmd.exe /c"
    assert context_module._command_shell(
        {"ComSpec": r"C:\Windows\cmd.exe"}, os_name="nt", shell="cmd",
    ) == r"C:\Windows\cmd.exe /c"
    powershell_command = context_module._command_shell({}, os_name="nt")
    assert "pwsh.exe" in powershell_command.lower() or "powershell.exe" in powershell_command.lower()
    assert "-NoLogo -NoProfile -NonInteractive -Command" in powershell_command


def test_machine_tier_is_cacheable_and_precedes_untrusted_workspace_instructions(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Prefer tabs.", encoding="utf-8")
    machine = "## Workspace execution environment (session snapshot)\nOS: sample-os"
    first = context_module.build_system_prompt(tmp_path, machine_context=machine)
    second = context_module.build_system_prompt(tmp_path, machine_context=machine)

    assert first == second
    assert first.count(machine) == 1
    assert first.index(machine) < first.index("## Workspace instructions (AGENTS.md)")
    assert "Prefer tabs." in first


def test_dispatcher_collects_once_per_binding_and_recollects_on_rebind_or_disposal(
    tmp_path, monkeypatch,
):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    calls = []

    def spy(root, *, shell="auto"):
        calls.append((root, shell))
        time.sleep(0.01)  # Exercise simultaneous first-use probes.
        return f"machine snapshot {len(calls)} at {root}"

    monkeypatch.setattr(context_module, "describe_machine", spy)
    dispatcher = HarnessDispatchOps()

    with ThreadPoolExecutor(max_workers=5) as workers:
        snapshots = list(workers.map(
            lambda _: dispatcher.harness_machine_snapshot("workspace-1", one),
            range(5),
        ))

    assert len(calls) == 1
    assert len(set(snapshots)) == 1
    assert dispatcher.harness_machine_snapshot("workspace-1", one) == snapshots[0]
    assert len(calls) == 1

    changed = dispatcher.harness_machine_snapshot("workspace-1", two)
    assert changed != snapshots[0]
    assert len(calls) == 2

    other_shell = dispatcher.harness_machine_snapshot("workspace-1", two, "cmd")
    assert other_shell != changed
    assert len(calls) == 3

    dispatcher.dispose_harness_workspace("workspace-1")
    assert dispatcher.harness_machine_snapshot("workspace-1", two) != changed
    assert len(calls) == 4

    dispatcher.dispose_all_harness_processes()
    dispatcher.harness_machine_snapshot("workspace-1", two)
    assert len(calls) == 5

    # Two app sessions must not share a cached snapshot.
    another_session = HarnessDispatchOps()
    another_session.harness_machine_snapshot("workspace-1", two)
    assert len(calls) == 6
