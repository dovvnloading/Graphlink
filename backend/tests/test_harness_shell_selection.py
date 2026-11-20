"""Workspace agent shell preferences: platform defaults and subprocess argv."""

from __future__ import annotations

import os
import subprocess
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
    assert args[-1] == (
        "& {\nWrite-Output 'hi'\n"
        "if ($?) { exit 0 }\n"
        "if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
        "exit 1\n}"
    )
    assert "-NoProfile" in args

    with patch("backend.harness.shell_selection.Path.is_file", return_value=False), \
         patch("backend.harness.shell_selection.shutil.which", side_effect=[None, None, r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"]):
        args, use_shell = shell_invocation("Get-Location", "powershell", env={"PATH": "tools"}, os_name="nt")
    assert args[0].endswith("powershell.exe")
    assert not use_shell


@pytest.mark.skipif(os.name != "nt", reason="PowerShell command exit codes require Windows")
@pytest.mark.parametrize(("command", "expected"), [
    ("cmd /c exit 42", 42),
    ("Write-Error 'failed'", 1),
    ("cmd /c exit 0; Write-Error 'failed'", 1),
    ("cmd /c exit 42; Write-Output 'continued'", 0),
    ("Write-Output 'starting'; cmd /c exit 42", 42),
])
def test_windows_powershell_process_exit_code(command, expected):
    args, use_shell = shell_invocation(command, "powershell", os_name="nt")
    assert not use_shell
    result = subprocess.run(args, capture_output=True, text=True, timeout=20)
    assert result.returncode == expected, (result.stdout, result.stderr)


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


@pytest.mark.parametrize("git_subdir", ("ucrt64/bin", "mingw64/bin", "usr/bin"))
@pytest.mark.parametrize("bash_subdir", ("bin", "usr/bin"))
def test_git_bash_discovers_portable_git_real_executable(tmp_path, git_subdir, bash_subdir):
    git_home = tmp_path / "PortableGit"
    git = git_home / git_subdir / "git.exe"
    git.parent.mkdir(parents=True)
    git.touch()
    bash = git_home / bash_subdir / "bash.exe"
    bash.parent.mkdir(parents=True, exist_ok=True)
    bash.touch()
    with patch("backend.harness.shell_selection.shutil.which", return_value=str(git)):
        args, use_shell = shell_invocation(
            "pwd", "git-bash", env={"PATH": str(git.parent)}, os_name="nt",
        )
    assert args == [str(bash), "--noprofile", "--norc", "-c", "pwd"]
    assert not use_shell


def test_missing_git_bash_is_a_clear_error(monkeypatch):
    monkeypatch.setattr("backend.harness.shell_selection.shutil.which", lambda *args, **kwargs: None)
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    with pytest.raises(ShellUnavailableError, match="Git Bash was selected"):
        shell_invocation("pwd", "git-bash", env={"PATH": ""}, os_name="nt")


def test_posix_resolves_bash_from_execution_path():
    executable = "/run/current-system/sw/bin/bash"
    with patch("backend.harness.shell_selection.shutil.which", return_value=executable) as which:
        args, use_shell = shell_invocation(
            "printf '%s' hello", "auto",
            env={"PATH": "/run/current-system/sw/bin", "SHELL": "/bin/zsh"},
            os_name="posix",
        )
    which.assert_called_once_with("bash", path="/run/current-system/sw/bin")
    assert args == [executable, "--noprofile", "--norc", "-c", "printf '%s' hello"]
    assert not use_shell


def test_posix_reports_missing_bash_instead_of_using_an_unapproved_path():
    with patch("backend.harness.shell_selection.shutil.which", return_value=None) as which:
        with pytest.raises(ShellUnavailableError, match="Bash is not available"):
            shell_invocation("pwd", "auto", env={"PATH": "/restricted/bin"}, os_name="posix")
    which.assert_called_once_with("bash", path="/restricted/bin")


def test_posix_empty_execution_path_does_not_probe_current_directory():
    with patch("backend.harness.shell_selection.shutil.which") as which:
        with pytest.raises(ShellUnavailableError, match="Bash is not available"):
            shell_invocation("pwd", "auto", env={"PATH": ""}, os_name="posix")
    which.assert_not_called()


def test_posix_without_path_uses_subprocess_default_path():
    with patch("backend.harness.shell_selection.shutil.which", return_value="/bin/bash") as which:
        shell_invocation("pwd", "auto", env={}, os_name="posix")
    which.assert_called_once_with("bash", path=os.defpath)

