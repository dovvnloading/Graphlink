"""Workspace agent shell preferences: platform defaults and subprocess argv."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.harness.shell_selection import (
    ShellUnavailableError,
    effective_shell,
    normalize_shell,
    shell_invocation,
)


def test_defaults_follow_host_and_preserve_cross_platform_graphs():
    assert effective_shell("auto", os_name="nt") == "powershell"
    assert effective_shell("bash", os_name="nt") == "powershell"
    assert effective_shell("cmd", os_name="nt") == "cmd"
    assert effective_shell("git-bash", os_name="nt") == "git-bash"
    for selected in ("auto", "cmd", "git-bash", "powershell", "bash"):
        assert effective_shell(selected, os_name="posix") == "bash"
    assert normalize_shell("not-a-shell") == "auto"
    assert normalize_shell(None) == "auto"


def test_windows_prefers_powershell_7_then_windows_powershell():
    with patch("backend.harness.shell_selection.shutil.which", side_effect=[
        r"C:\Program Files\PowerShell\7\pwsh.exe",
    ]):
        args, use_shell = shell_invocation("Write-Output 'hi'", "auto", env={"PATH": "tools"}, os_name="nt")
    assert not use_shell
    assert args[0].endswith("pwsh.exe")
    assert args[-1] == "Write-Output 'hi'"
    assert "-NoProfile" in args

    with patch("backend.harness.shell_selection.shutil.which", side_effect=[None, None, r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"]):
        args, use_shell = shell_invocation("Get-Location", "powershell", env={"PATH": "tools"}, os_name="nt")
    assert args[0].endswith("powershell.exe")
    assert not use_shell


def test_powershell_7_found_in_program_files_without_path(tmp_path, monkeypatch):
    root = tmp_path / "Program Files"
    binary = root / "PowerShell" / "7" / "pwsh.exe"
    binary.parent.mkdir(parents=True)
    binary.touch()
    monkeypatch.setattr("backend.harness.shell_selection.shutil.which", lambda *a, **kw: None)
    args, use_shell = shell_invocation(
        "Get-Date", "auto",
        env={"PATH": "", "PROGRAMFILES": str(root)}, os_name="nt",
    )
    assert not use_shell
    assert args[0] == str(binary)


def test_windows_cmd_keeps_raw_command_and_shell_true():
    raw = '"C:\\Program Files\\Python\\python.exe" -c "print(42)"'
    args, use_shell = shell_invocation(raw, "cmd", env={"PATH": "tools"}, os_name="nt")
    assert args == raw
    assert use_shell


def test_git_bash_uses_git_for_windows_not_wsl_bash(tmp_path):
    git_home = tmp_path / "Git"
    (git_home / "cmd").mkdir(parents=True)
    (git_home / "bin").mkdir()
    bash = git_home / "bin" / "bash.exe"
    bash.touch()
    git = git_home / "cmd" / "git.exe"
    git.touch()
    with patch("backend.harness.shell_selection.shutil.which", return_value=str(git)):
        args, use_shell = shell_invocation("pwd", "git-bash", env={"PATH": str(git_home)}, os_name="nt")
    assert args == [str(bash), "--noprofile", "--norc", "-c", "pwd"]
    assert not use_shell


def test_missing_git_bash_is_a_clear_error(monkeypatch):
    monkeypatch.setattr("backend.harness.shell_selection.shutil.which", lambda *args, **kwargs: None)
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    with pytest.raises(ShellUnavailableError, match="Git Bash was selected"):
        shell_invocation("pwd", "git-bash", env={"PATH": ""}, os_name="nt")


def test_posix_runs_bash_explicitly_not_login_shell():
    args, use_shell = shell_invocation("printf '%s' hello", "auto", env={"SHELL": "/bin/zsh"}, os_name="posix")
    assert args == ["/bin/bash", "--noprofile", "--norc", "-c", "printf '%s' hello"]
    assert not use_shell
