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
import time
from pathlib import Path

from api_provider import RequestCancelledError
from graphlink_execution_guard import (
    assign_guarded_subprocess, create_execution_guard, spawn_guarded_subprocess,
)
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
_VENV_POLL_SECONDS = 0.1
_VENV_OUTPUT_CAP = 2_000


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



def _check_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise RequestCancelledError("stopped")


def _bootstrap_managed_venv(
    target: Path, cancel_event: threading.Event | None,
) -> tuple[int, str]:
    """Create a venv in a guarded process tree, reaped on Stop/timeout.

    The worker thread is not cancelled by asyncio.to_thread's caller: it
    must observe the same Stop event itself. Read output concurrently so a
    noisy bootstrap cannot block on a full stdout pipe.
    """
    _check_cancelled(cancel_event)
    guard = create_execution_guard()
    process = None
    reader = None
    output: list[str] = []
    try:
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        process = spawn_guarded_subprocess(
            guard, [sys.executable, "-m", "venv", str(target)],
            env=safe_subprocess_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            **guard.popen_kwargs(),
            **kwargs,
        )
        try:
            assign_guarded_subprocess(guard, process)
        except BaseException:
            process.kill()
            process.wait(timeout=3)
            raise

        def drain() -> None:
            stream = process.stdout
            if stream is None:
                return
            retained = 0
            try:
                while True:
                    piece = stream.readline(4096)
                    if not piece:
                        break
                    if retained < _VENV_OUTPUT_CAP:
                        kept = piece[:_VENV_OUTPUT_CAP - retained]
                        output.append(kept)
                        retained += len(kept)
            except (OSError, ValueError):
                pass

        reader = threading.Thread(target=drain, name="python-venv-output", daemon=True)
        reader.start()
        deadline = time.monotonic() + _VENV_CREATE_TIMEOUT_SECONDS
        while process.poll() is None:
            _check_cancelled(cancel_event)
            if time.monotonic() >= deadline:
                raise PythonEnvironmentError(
                    f"venv creation timed out after {_VENV_CREATE_TIMEOUT_SECONDS}s"
                )
            time.sleep(_VENV_POLL_SECONDS)
        _check_cancelled(cancel_event)
        reader.join(timeout=2)
        return process.returncode, "".join(output)
    finally:
        # Must run even when the waiting asyncio task disappears on Stop.
        # A guard closes the entire process group/job, not just its parent.
        guard.close()
        if process is not None:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            if reader is not None:
                reader.join(timeout=2)
            if process.stdout is not None:
                process.stdout.close()


def ensure_python_environment(
    workspace_id: str, cwd: Path, *, is_scratch: bool,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Select an existing project venv or lazily create/reuse a managed one.

    Never modify a user's project directory to create a venv. Missing or
    incomplete .venv/venv folders are not environments, so they do not
    prevent a fallback. Creation uses the same allowlisted subprocess env as
    executed Python and is deliberately off the asyncio event loop.
    """
    _check_cancelled(cancel_event)
    for name in (".venv", "venv"):
        candidate = cwd / name
        if _usable_venv(candidate):
            return venv_python(candidate)

    target = managed_venv_dir(workspace_id, cwd, is_scratch=is_scratch)
    # Do not park indefinitely behind another workspace's bootstrap.
    while not _CREATION_LOCK.acquire(timeout=_VENV_POLL_SECONDS):
        _check_cancelled(cancel_event)
    try:
        _check_cancelled(cancel_event)
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
                code, output = _bootstrap_managed_venv(target, cancel_event)
            except RequestCancelledError:
                shutil.rmtree(target, ignore_errors=True)
                raise
            except (OSError, PythonEnvironmentError) as exc:
                shutil.rmtree(target, ignore_errors=True)
                raise PythonEnvironmentError(
                    f"could not create the managed Python environment: {exc}"
                ) from exc
            if code != 0 or not _usable_venv(target):
                shutil.rmtree(target, ignore_errors=True)
                detail = output.strip()[-_VENV_OUTPUT_CAP:]
                raise PythonEnvironmentError(
                    f"could not create the managed Python environment "
                    f"(exit {code}): {detail or 'missing venv interpreter'}"
                )
        _check_cancelled(cancel_event)
    finally:
        _CREATION_LOCK.release()

    return venv_python(target)
