"""Workspace-bound virtual environments for the harness python.exec tool.

A trusted project can provide its own .venv or venv. Otherwise python.exec
uses an app-owned venv: inside managed scratch workspaces, or under a separate
private scratch root for user projects. The latter is keyed by durable agent
workspace id AND resolved project root, so rebinding cannot reuse another
project's installed packages. No venv is created until Python is executed.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from graphlink_process_env import safe_subprocess_env
from graphlink_scratch_dirs import (
    HARNESS_PYTHON_ENV_ROOT,
    prepare_scratch_dir,
    safe_scratch_id,
    touch_scratch_dir_usage,
)

# Serializes first-use creation and repair in this process. A failed or
# interrupted creation may leave a partial managed venv; the next use repairs
# it. This lock never guards execution of user code.
_CREATION_LOCK = threading.Lock()
_VENV_CREATE_TIMEOUT_SECONDS = 180


class PythonEnvironmentError(RuntimeError):
    """The workspace's Python environment could not be prepared."""


def venv_python(venv_dir: Path) -> Path:
    """Return the standard interpreter path on Windows or POSIX."""
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _usable_venv(venv_dir: Path) -> bool:
    python = venv_python(venv_dir)
    return (
        (venv_dir / "pyvenv.cfg").is_file()
        and python.is_file()
        and (sys.platform == "win32" or os.access(python, os.X_OK))
    )


def managed_venv_dir(workspace_id: str, cwd: Path, *, is_scratch: bool) -> Path:
    """Locate this workspace's app-owned environment without creating it."""
    if is_scratch:
        return cwd / ".graphlink-python-venv"

    # The id alone is not enough: the same agent can be rebound to another
    # project. Hash the real root so two projects never share site-packages,
    # while the id bucket retains the existing node-delete GC convention.
    project_key = hashlib.sha256(os.fsencode(str(cwd.resolve()))).hexdigest()[:24]
    return HARNESS_PYTHON_ENV_ROOT / safe_scratch_id(workspace_id) / project_key / "venv"


def ensure_python_environment(
    workspace_id: str, cwd: Path, *, is_scratch: bool,
) -> Path:
    """Select an existing project venv or lazily create/reuse a managed one.

    Never modify a user's project directory to create a venv. Missing or
    incomplete .venv/venv folders are not environments, so they do not
    prevent a fallback. Creation uses the same allowlisted subprocess env as
    executed Python and is deliberately off the asyncio event loop.
    """
    for name in (".venv", "venv"):
        candidate = cwd / name
        if _usable_venv(candidate):
            return venv_python(candidate)

    target = managed_venv_dir(workspace_id, cwd, is_scratch=is_scratch)
    with _CREATION_LOCK:
        if is_scratch:
            # The workspace was created by ensure_workspace at run start.
            touch_scratch_dir_usage(cwd)
        else:
            bucket = HARNESS_PYTHON_ENV_ROOT / safe_scratch_id(workspace_id)
            prepare_scratch_dir(bucket)
            touch_scratch_dir_usage(bucket)

        # Do not follow an attacker-controlled symlink when creating or
        # repairing an app-managed environment.
        if target.is_symlink():
            raise PythonEnvironmentError(
                f"managed Python environment is a symlink: {target}"
            )

        if not _usable_venv(target):
            # A crash during first-use setup can leave an incomplete venv.
            # Only app-owned directories are repaired; project venvs are
            # never deleted or modified here.
            if target.exists():
                if not target.is_dir():
                    raise PythonEnvironmentError(
                        f"managed Python environment is not a directory: {target}"
                    )
                shutil.rmtree(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "venv", str(target)],
                    env=safe_subprocess_env(),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=_VENV_CREATE_TIMEOUT_SECONDS,
                    creationflags=(
                        subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                    ),
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                shutil.rmtree(target, ignore_errors=True)
                raise PythonEnvironmentError(
                    f"could not create the managed Python environment: {exc}"
                ) from exc
            if result.returncode != 0 or not _usable_venv(target):
                shutil.rmtree(target, ignore_errors=True)
                detail = (result.stdout or "").strip()[-2000:]
                raise PythonEnvironmentError(
                    f"could not create the managed Python environment "
                    f"(exit {result.returncode}): {detail or 'missing venv interpreter'}"
                )

    return venv_python(target)
