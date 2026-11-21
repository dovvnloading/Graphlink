"""Resolve the workspace agent's selected shell for guarded subprocesses.

Both one-shot commands and long-running sessions use this module. The
subprocess environment, execution guard, workspace boundary, and approval
policy are unchanged by the shell preference.
"""

from __future__ import annotations

import base64
import os
import shutil
from pathlib import Path, PurePosixPath

from graphlink_process_env import safe_subprocess_env

VALID_SHELLS = frozenset({"auto", "powershell", "cmd", "git-bash", "bash"})


class ShellUnavailableError(RuntimeError):
    """The optional shell selected by the user is unavailable."""


def normalize_shell(value: object) -> str:
    return value if isinstance(value, str) and value in VALID_SHELLS else "auto"


def effective_shell(value: object, *, os_name: str | None = None) -> str:
    system = os.name if os_name is None else os_name
    if system != "nt":
        return "bash"
    shell = normalize_shell(value)
    return shell if shell in {"powershell", "cmd", "git-bash"} else "powershell"


def _env_value(env: dict[str, str], name: str) -> str | None:
    return next((v for k, v in env.items() if k.upper() == name.upper()), None)


def _git_bash_executable(env: dict[str, str]) -> str:
    """Find Git for Windows, not the unrelated WSL bash.exe."""
    path = _env_value(env, "PATH") or os.defpath
    git = shutil.which("git.exe", path=path)
    candidates: list[Path] = []
    if git:
        git_dir = Path(git).parent
        root = git_dir.parent
        # PATH can expose the real git.exe under ucrt64/bin (or mingw64/bin),
        # not just the wrapper under cmd/. Go up another level in that case.
        if git_dir.name.casefold() == "bin" and root.name.casefold() in {
            "ucrt64", "mingw64", "mingw32", "clang64", "clangarm64", "usr",
        }:
            root = root.parent
        candidates.extend((root / "bin" / "bash.exe", root / "usr" / "bin" / "bash.exe"))
    for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432"):
        base = _env_value(env, key) or os.environ.get(key)
        if base:
            candidates.append(Path(base) / "Git" / "bin" / "bash.exe")
    local = _env_value(env, "LOCALAPPDATA") or os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Programs" / "Git" / "bin" / "bash.exe")
    candidates.extend((Path("C:/Program Files/Git/bin/bash.exe"),
                       Path("C:/Program Files (x86)/Git/bin/bash.exe")))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise ShellUnavailableError(
        "Git Bash was selected but Git for Windows was not found. "
        "Install Git for Windows or choose PowerShell or cmd in Agent settings."
    )


def _powershell_executable(env: dict[str, str]) -> str:
    """Prefer installed PowerShell 7, including the default MSI location.

    The desktop process can have a restricted PATH, so pwsh may be installed
    even when which() cannot find it. Windows PowerShell is the fallback.
    """
    path = _env_value(env, "PATH") or os.defpath
    for name in ("pwsh.exe", "pwsh"):
        found = shutil.which(name, path=path)
        if found:
            return found
    for key in ("PROGRAMFILES", "PROGRAMW6432", "PROGRAMFILES(X86)"):
        base = _env_value(env, key) or os.environ.get(key)
        if base:
            candidate = Path(base) / "PowerShell" / "7" / "pwsh.exe"
            if candidate.is_file():
                return str(candidate)
    for root in ("C:/Program Files", "C:/Program Files (x86)"):
        candidate = Path(root) / "PowerShell" / "7" / "pwsh.exe"
        if candidate.is_file():
            return str(candidate)
    return shutil.which("powershell.exe", path=path) or "powershell.exe"


def _powershell_command(command: str) -> str:
    """Preserve native status without reusing it for a later cmdlet failure.

    PowerShell retains $LASTEXITCODE even after a cmdlet fails. If the
    final command can be resolved as a cmdlet/function, discard any stale
    native status. Keep native codes for applications, and for dynamic
    calls whose command kind cannot be determined from the syntax.
    Flush formatted objects before exit and suppress PowerShell 7's ANSI styling.
    """
    encoded_command = base64.b64encode(command.encode("utf-8")).decode("ascii")
    # Out-Default must finish formatting before the process exits. Carry the
    # command status through a reference so Out-Default cannot overwrite it.
    return (
        "if ($null -ne $PSStyle) { $PSStyle.OutputRendering = 'PlainText' }\n"
        "$__gl_status = [ref]0\n"
        f"& {{\n{command}\n"
        "$__gl_success = $?\n"
        "$__gl_native_exit_code = $LASTEXITCODE\n"
        "if (-not $__gl_success) {\n"
        "    $__gl_status.Value = 1\n"
        "    if ($null -ne $__gl_native_exit_code -and $__gl_native_exit_code -ne 0) {\n"
        "        $__gl_status.Value = $__gl_native_exit_code\n"
        "        $__gl_tokens = $null; $__gl_errors = $null\n"
        "        $__gl_ast = [System.Management.Automation.Language.Parser]::ParseInput(\n"
        f"            [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded_command}')),\n"
        "            [ref]$__gl_tokens, [ref]$__gl_errors)\n"
        "        $__gl_last = $__gl_ast.EndBlock.Statements[-1]\n"
        "        if ($__gl_last -is [System.Management.Automation.Language.PipelineAst]) {\n"
        "            $__gl_tail = $__gl_last.PipelineElements[-1]\n"
        "            if ($__gl_tail -is [System.Management.Automation.Language.CommandAst]) {\n"
        "                $__gl_name = $__gl_tail.GetCommandName()\n"
        "                if ($__gl_name) {\n"
        "                    $__gl_resolved = Microsoft.PowerShell.Core\\Get-Command $__gl_name -ErrorAction SilentlyContinue\n"
        "                    if (-not $__gl_resolved -or $__gl_resolved.CommandType -notin @('Application', 'ExternalScript')) {\n"
        "                        $__gl_status.Value = 1\n"
        "                    }\n"
        "                }\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "}\n"
        "} | Out-Default\n"
        "exit $__gl_status.Value"
    )


def _posix_bash_executable(env: dict[str, str]) -> str:
    """Look up Bash on the same allowed PATH passed to the subprocess."""
    path = _env_value(env, "PATH")
    if path is None:
        path = os.defpath
    # Do not search the working directory when the allowed PATH is empty.
    bash = shutil.which("bash", path=path) if path else None
    if bash:
        # which() can return a POSIX-absolute path even when the caller
        # simulates POSIX on Windows. Windows abspath() would rewrite
        # /bin/bash as a drive-prefixed path; keep absolute results intact.
        if os.path.isabs(bash) or PurePosixPath(bash).is_absolute():
            return bash
        # Relative PATH entries must be bound before Popen changes cwd.
        return os.path.abspath(bash)
    raise ShellUnavailableError(
        "Bash is not available on the workspace execution PATH. "
        "Install Bash or add it to PATH before starting the Agent."
    )


def shell_invocation(
    command: str,
    shell: str = "auto",
    *,
    env: dict[str, str] | None = None,
    os_name: str | None = None,
) -> tuple[str | list[str], bool]:
    """Return subprocess args and shell flag; keep command as a single arg.

    cmd uses shell=True to preserve Windows cmd.exe quoting behavior;
    PowerShell and Bash receive an explicit argument vector.
    """
    environment = safe_subprocess_env() if env is None else env
    selected = effective_shell(shell, os_name=os_name)
    if selected == "cmd":
        return command, True
    if selected == "powershell":
        executable = _powershell_executable(environment)
        return [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", _powershell_command(command)], False
    if selected == "git-bash":
        return [_git_bash_executable(environment), "--noprofile", "--norc", "-c", command], False
    return [_posix_bash_executable(environment), "--noprofile", "--norc", "-c", command], False
