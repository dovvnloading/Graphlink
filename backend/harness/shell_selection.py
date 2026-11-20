"""Resolve the workspace agent's selected shell for guarded subprocesses.

Both one-shot commands and long-running sessions use this module. The
subprocess environment, execution guard, workspace boundary, and approval
policy are unchanged by the shell preference.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

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
        root = Path(git).parent.parent
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
    path = _env_value(environment, "PATH") or os.defpath
    if selected == "cmd":
        return command, True
    if selected == "powershell":
        executable = (shutil.which("pwsh.exe", path=path)
                      or shutil.which("pwsh", path=path)
                      or shutil.which("powershell.exe", path=path)
                      or "powershell.exe")
        return [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command], False
    if selected == "git-bash":
        return [_git_bash_executable(environment), "--noprofile", "--norc", "-c", command], False
    return ["/bin/bash", "--noprofile", "--norc", "-c", command], False
