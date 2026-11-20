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
    assert f"Backend Python (Graphlink process): {context_module.platform.python_version()}" in snapshot
    assert f"({context_module.sys.executable})" in snapshot
    assert "python.exec: selects a usable workspace .venv or venv" in snapshot
    assert "Its interpreter/version may differ from backend Python." in snapshot
    assert "Python (python.exec):" not in snapshot
    assert str(tmp_path.resolve()) in snapshot
    assert "Command shell (shell.exec / shell.session):" in snapshot
    assert "- git: /toolchain/bin/git" in snapshot
    assert "- uv: /toolchain/bin/uv" in snapshot
    for absent in ("node", "npm", "docker", "podman", "rg"):
        assert f"- {absent}: not on PATH" in snapshot
    # Resolving PowerShell on Windows probes pwsh/powershell on this same PATH.
    assert all(used_path == "/toolchain/bin" for _, used_path in checks)
    assert [item for item in checks if item[0] in context_module._COMMON_COMMANDS] == [
        (name, "/toolchain/bin") for name in context_module._COMMON_COMMANDS
    ]


def test_existing_workspace_venv_is_not_misidentified_as_backend_python(tmp_path):
    from backend.harness.python_env import ensure_python_environment, venv_python

    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("version = 3.11\n", encoding="utf-8")
    selected = venv_python(venv)
    selected.parent.mkdir(parents=True)
    selected.write_text("fake project interpreter", encoding="utf-8")
    selected.chmod(0o755)
    assert ensure_python_environment("workspace", tmp_path, is_scratch=False) == selected

    snapshot = context_module.describe_machine(tmp_path)
    assert "Backend Python (Graphlink process):" in snapshot
    assert "python.exec: selects a usable workspace .venv or venv" in snapshot
    assert "Python (python.exec):" not in snapshot


def test_isolated_machine_tier_describes_container_not_host_shell(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.harness.shell_isolation.available_engine", lambda: "podman")
    snapshot = context_module.describe_machine(tmp_path, profile="isolated")
    assert "podman" in snapshot
    assert "/bin/sh -c in isolated" in snapshot
    assert "Container network: disabled by default" in snapshot
    assert "shell.exec can request per-command or per-task user approval" in snapshot
    assert "512 MiB RAM" in snapshot
    assert "Host executables are NOT automatically available" in snapshot
    assert "python.exec: persistent container REPL" in snapshot
    assert "python.install: container binary wheels" in snapshot
    assert "web.search / web.fetch: backend-host HTTPS" in snapshot


def test_isolated_engine_cli_absence_is_reported_as_a_path_problem(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.harness.shell_isolation.available_engine", lambda: None)
    snapshot = context_module.describe_machine(tmp_path, profile="isolated")
    assert "Container engine: Docker/Podman CLI not on execution PATH" in snapshot
    assert "isolation unavailable until configured" in snapshot
    assert "none installed" not in snapshot


def test_command_shell_is_the_execution_shell_not_the_login_shell(monkeypatch):
    monkeypatch.setattr(
        context_module.shutil, "which",
        lambda name, *, path: "/bin/bash" if name == "bash" else None,
    )
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


def test_command_shell_reports_bash_missing_from_execution_path(monkeypatch):
    monkeypatch.setattr(context_module.shutil, "which", lambda _name, *, path: None)
    assert context_module._command_shell(
        {"SHELL": "/bin/zsh", "PATH": "/restricted/bin"}, os_name="posix",
    ) == "Bash (not found on this machine)"


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
