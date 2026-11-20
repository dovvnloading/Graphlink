"""Container-backed workspace shell execution (opt-in, fail closed).

Both shell.exec and shell.session use the same command builder. Container CLI
arguments are an argv list, never interpolated through a host shell. The
image must be pulled explicitly: running an approved shell command must not
silently trigger a host-network image download.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from graphlink_process_env import safe_subprocess_env

VALID_EXECUTION_PROFILES = frozenset({"host", "isolated"})
CONTAINER_IMAGE = "python:3.12-slim"
CONTAINER_WORKSPACE = "/workspace"
CONTAINER_MEMORY = "512m"
CONTAINER_CPUS = "1"


class IsolationUnavailableError(RuntimeError):
    """Requested isolation cannot be established; NEVER fall back to host."""


def normalize_execution_profile(value: object) -> str:
    return value if isinstance(value, str) and value in VALID_EXECUTION_PROFILES else "host"


def _path(env: dict[str, str]) -> str:
    return next((value for key, value in env.items() if key.upper() == "PATH"), os.defpath)


def available_engine(env: dict[str, str] | None = None) -> str | None:
    """Name of an installed CLI on the sanitized subprocess PATH (not a daemon probe)."""
    environment = safe_subprocess_env() if env is None else env
    for name in ("docker", "podman"):
        if shutil.which(name, path=_path(environment)):
            return name
    return None


def container_invocation(
    command: str, workspace: Path, *, env: dict[str, str] | None = None,
    allow_network: bool = False,
) -> tuple[list[str], str, str]:
    """Return (argv, engine, unique name) for a restricted Linux container.

    A fixed image, no privileged caps, read-only image filesystem and
    bounded resources are non-optional. Networking is off unless the caller
    explicitly passes an authorized grant. Only the bound workspace is
    writable; /tmp is a small scratch tmpfs.
    """
    environment = safe_subprocess_env() if env is None else env
    engine = available_engine(environment)
    if engine is None:
        raise IsolationUnavailableError(
            "Isolated execution requires Docker or Podman. Install Docker "
            "Desktop (Windows/macOS), Docker Engine (Linux), or Podman, "
            "then reopen Agent settings. Host execution was NOT attempted."
        )
    executable = shutil.which(engine, path=_path(environment))
    if not executable:
        raise IsolationUnavailableError(f"{engine} is no longer available; Host execution was NOT attempted.")
    mount_source = str(workspace.resolve())
    # --mount parses comma-separated fields *inside* this argv element.
    # A comma in a directory name can inject extra mount options even
    # though we never interpolate commands through the host shell.
    if any(char in mount_source for char in (",", "\r", "\n")):
        raise IsolationUnavailableError(
            "Isolated execution cannot mount workspace paths containing "
            "commas or newlines. Host execution was NOT attempted."
        )
    name = f"graphlink-agent-{uuid.uuid4().hex}"
    args = [
        executable, "run", "--rm", "--interactive", "--pull=never",
        "--name", name,
        "--network=bridge" if allow_network is True else "--network=none",
        "--cpus=" + CONTAINER_CPUS,
        "--memory=" + CONTAINER_MEMORY, "--memory-swap=" + CONTAINER_MEMORY,
        "--pids-limit=128", "--read-only",
        "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
        "--mount", f"type=bind,source={mount_source},target={CONTAINER_WORKSPACE}",
        "--workdir", CONTAINER_WORKSPACE,
        "--env", "HOME=/tmp", "--env", "PYTHONDONTWRITEBYTECODE=1",
    ]
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if os.name == "posix" and callable(getuid) and callable(getgid):
        if engine == "podman":
            args.append("--userns=keep-id")
        else:
            args.extend(["--user", f"{getuid()}:{getgid()}"])
    args.extend([CONTAINER_IMAGE, "/bin/sh", "-c", command])
    return args, engine, name


def network_request_error(arguments: dict) -> str | None:
    """Reject malformed network requests before exposing an approval panel."""
    requested = arguments.get("network", False)
    if not isinstance(requested, bool):
        return "shell.exec 'network' must be a boolean."
    if requested:
        reason = arguments.get("network_reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            return "shell.exec network access requires a non-empty reason (at most 1000 characters)."
    return None


def cleanup_container(engine: str, name: str, *, env: dict[str, str] | None = None) -> None:
    """Force-remove a daemon-managed child after CLI cancellation/stop.

    Killing the docker/podman CLIENT alone need not kill the container.
    Names are generated by us (not model input); no shell or host environment
    with provider credentials is involved. --rm makes normal exit cheap.
    """
    environment = safe_subprocess_env() if env is None else env
    executable = shutil.which(engine, path=_path(environment))
    if executable is None:
        return
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        subprocess.run(
            [executable, "rm", "--force", name],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=environment, timeout=5, check=False, **kwargs,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
