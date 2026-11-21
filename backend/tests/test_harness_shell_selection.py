"""Workspace agent shell preferences: platform defaults and subprocess argv."""

from __future__ import annotations

import os
import shutil
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
    launcher = args[-1]
    assert launcher.startswith(
        "if ($null -ne $PSStyle) { $PSStyle.OutputRendering = 'PlainText' }\n"
        "$__gl_status = [ref]0\n& {\nWrite-Output 'hi'\n"
        "$__gl_success = $?\n"
    )
    assert "Parser]::ParseInput" in launcher
    assert "CommandType -notin @('Application', 'ExternalScript')" in launcher
    assert launcher.endswith("} | Out-Default\nexit $__gl_status.Value")
    assert "exit " not in launcher.split("} | Out-Default", 1)[0]
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
    ("cmd /c exit 42; Write-Error 'cmdlet failed'", 1),
    ("Write-Error 'cmdlet failed'; cmd /c exit 42", 42),
    ("$program = 'cmd.exe'; & $program /c exit 37", 37),
    ("cmd /c exit 42; Write-Output 'continued'", 0),
    ("Write-Output 'starting'; cmd /c exit 42", 42),
    ("cmd /c exit 42; Get-Item -LiteralPath Z:\\no-such-graphlink-file", 1),
])
def test_windows_powershell_process_exit_code(command, expected):
    args, use_shell = shell_invocation(command, "powershell", os_name="nt")
    assert not use_shell
    result = subprocess.run(args, capture_output=True, text=True, timeout=20)
    assert result.returncode == expected, (result.stdout, result.stderr)


@pytest.mark.skipif(os.name != "nt", reason="PowerShell object formatting requires Windows")
@pytest.mark.parametrize(("command", "markers"), [
    ("Get-Location", ("Path",)),
    ("[pscustomobject]@{a=1;b=2}", ("a", "b", "1", "2")),
    (r"Get-Item C:\Windows | Select-Object Name,Mode", ("Name", "Mode", "Windows")),
    ("Write-Output 'plain text survives'", ("plain text survives",)),
])
def test_windows_powershell_shell_exec_flushes_formatted_output_without_ansi(command, markers):
    args, use_shell = shell_invocation(command, "powershell", os_name="nt")
    assert not use_shell
    result = subprocess.run(args, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout.strip(), f"lost output for {command!r}"
    assert all(marker in result.stdout for marker in markers), result.stdout
    assert "\x1b[" not in result.stdout, result.stdout


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


def test_posix_absolute_bash_is_not_rebased_by_host_path_rules():
    # Windows abspath("/bin/bash") turns the POSIX path into a drive path.
    # Check this on every host so Linux CI also catches the regression.
    with patch("backend.harness.shell_selection.shutil.which", return_value="/bin/bash") as which:
        with patch("backend.harness.shell_selection.os.path.abspath") as abspath:
            args, use_shell = shell_invocation(
                "pwd", "auto", env={"PATH": "/bin"}, os_name="posix",
            )
    which.assert_called_once_with("bash", path="/bin")
    abspath.assert_not_called()
    assert args == ["/bin/bash", "--noprofile", "--norc", "-c", "pwd"]
    assert not use_shell


def test_posix_relative_bash_is_absolutized_on_every_host():
    # A relative result from which() must still survive Popen's cwd change.
    discovered = os.path.join("tools", "bash")
    with patch("backend.harness.shell_selection.shutil.which", return_value=discovered):
        args, use_shell = shell_invocation(
            "pwd", "auto", env={"PATH": "tools"}, os_name="posix",
        )
    assert args[0] == os.path.abspath(discovered)
    assert not use_shell


@pytest.mark.skipif(os.name == "nt", reason="POSIX relative PATH resolution")
def test_posix_relative_path_bash_still_launches_from_workspace(tmp_path, monkeypatch):
    backend_dir = tmp_path / "backend"
    workspace = tmp_path / "workspace"
    tools = backend_dir / "tools"
    tools.mkdir(parents=True)
    workspace.mkdir()
    bash = shutil.which("bash")
    assert bash is not None
    (tools / "bash").symlink_to(Path(bash).resolve())
    monkeypatch.chdir(backend_dir)
    argv, via_shell = shell_invocation(
        "printf correct", "auto", env={"PATH": "tools"}, os_name="posix",
    )
    assert not via_shell
    assert argv[0] == str(tools / "bash")
    result = subprocess.run(
        argv, cwd=workspace, env={"PATH": "tools"},
        capture_output=True, text=True, timeout=10, check=True,
    )
    assert result.stdout == "correct"


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

