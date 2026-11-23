"""Offline Linux/amd64 Docker fixture runner. This module does not issue proof verdicts."""

from __future__ import annotations

import json
import os
import platform
import re
import selectors
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from threading import Event

ARTIFACT_LIMIT = 16 * 1024**2
DIAGNOSTIC_LIMIT = 256 * 1024
FIXTURES = {b"positive", b"changed_target", b"sorry", b"new_axiom", b"imported_axiom", b"imported_sorry"}
STAGES = {"probe", "wait", "export", "replay"}
ENTRYPOINT = ["/usr/local/bin/python3", "-I", "/opt/graphlink/isolation_entrypoint.py"]
TMPFS = "rw,nosuid,nodev,noexec,size=268435456,mode=1777"
LABEL = "org.graphlink.formal-lean-spike.job"


class IsolationError(RuntimeError):
    """A setup, isolation, protocol, or engine failure."""


class StageTimeout(IsolationError):
    """The stage ran past its wall clock budget."""

    def __init__(self, message, stdout=b""):
        super().__init__(message)
        self.stdout = stdout


class StageCancelled(IsolationError):
    """The caller cancelled this attempt."""


class OutputLimit(IsolationError):
    """The caller cannot use partial stage output."""


class CleanupError(IsolationError):
    """Removal from the Docker daemon could not be confirmed."""


@dataclass(frozen=True)
class StageResult:
    exit_code: int
    stdout: bytes
    stderr: bytes


def _exchange(argv, env, payload, timeout, stdout_limit, cancel=None):
    """Pump all three pipes concurrently, bounding memory before accepting output."""
    deadline = time.monotonic() + timeout
    view = memoryview(payload)
    with subprocess.Popen(argv, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, bufsize=0) as proc:
        try:
            out = bytearray()
            err = bytearray()
            with selectors.DefaultSelector() as selector:
                streams = (proc.stdout, proc.stderr, proc.stdin)
                for stream in streams:
                    os.set_blocking(stream.fileno(), False)
                selector.register(proc.stdout, selectors.EVENT_READ, (out, stdout_limit))
                selector.register(proc.stderr, selectors.EVENT_READ, (err, DIAGNOSTIC_LIMIT))
                if view:
                    selector.register(proc.stdin, selectors.EVENT_WRITE)
                else:
                    proc.stdin.close()
                while selector.get_map() or proc.poll() is None:
                    if cancel is not None and cancel.is_set():
                        raise StageCancelled("Fixture cancelled")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise StageTimeout("Fixture deadline exceeded", bytes(out))
                    for key, _ in selector.select(min(remaining, 0.1)):
                        if key.fileobj is proc.stdin:
                            try:
                                view = view[os.write(key.fd, view[:65536]):]
                            except BrokenPipeError:
                                view = view[len(view):]
                            if view:
                                continue
                        else:
                            buffer, limit = key.data
                            chunk = os.read(key.fd, min(65536, limit - len(buffer) + 1))
                            if chunk:
                                if len(chunk) + len(buffer) > limit:
                                    raise OutputLimit("Fixture output exceeded its limit")
                                buffer.extend(chunk)
                                continue
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
            return StageResult(proc.returncode, bytes(out), bytes(err))
        finally:
            if proc.poll() is None:
                proc.kill()


def _create_args(image_id, stage, job):
    return [
        "create", "--interactive", "--rm", "--pull=never", "--name", job, "--label", f"{LABEL}={job}",
        "--platform=linux/amd64", "--runtime=runc", "--network=none", "--ipc=none",
        "--cgroupns=private", "--user=65534:65534", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges=true", "--security-opt=apparmor=docker-default",
        "--memory=4g", "--memory-swap=4g", "--pids-limit=64", "--cpus=1",
        "--ulimit=core=0:0", "--ulimit=fsize=268435456:268435456", "--no-healthcheck",
        "--tmpfs", "/tmp:" + TMPFS, "--log-driver=none", "--workdir=/tmp", "--env=HOME=/tmp",
        image_id, stage,
    ]


def _validate_container(spec, image_id, stage, job):
    host = spec["HostConfig"]
    config = spec["Config"]
    require = {
        "AutoRemove": True, "Privileged": False, "ReadonlyRootfs": True,
        "NetworkMode": "none", "IpcMode": "none", "CgroupnsMode": "private",
        "PidMode": "", "UTSMode": "", "Runtime": "runc", "CapDrop": ["ALL"],
        "NanoCpus": 10**9, "Memory": 4 * 1024**3, "MemorySwap": 4 * 1024**3,
        "PidsLimit": 64, "Tmpfs": {"/tmp": TMPFS},
    }
    if any(host.get(key) != value for key, value in require.items()):
        raise IsolationError("Created container has an unexpected security or resource setting")
    if any(host.get(key) for key in ("CapAdd", "Devices", "DeviceRequests", "Binds", "Mounts",
                                      "VolumesFrom", "GroupAdd", "Links", "PortBindings")):
        raise IsolationError("Unexpected capabilities, devices, mounts, or network configuration")
    if any(m.get("Type") != "tmpfs" or m.get("Destination") != "/tmp" for m in spec["Mounts"]):
        raise IsolationError("Unexpected container mount")
    if (host["LogConfig"]["Type"] != "none" or config.get("Volumes")
            or set(host["SecurityOpt"]) != {"no-new-privileges=true", "apparmor=docker-default"}
            or config["User"] != "65534:65534" or config["WorkingDir"] != "/tmp"
            or config["Entrypoint"] != ENTRYPOINT or config["Cmd"] != [stage]
            or config["Labels"].get(LABEL) != job or spec["Name"] != "/" + job
            or spec["Image"] != image_id or spec["State"]["Status"] != "created"):
        raise IsolationError("Unexpected container image, entrypoint, identity, or state")


class _Docker:
    def __init__(self, executable, directory):
        self.prefix = [executable, "--config", directory, "--host", "unix:///var/run/docker.sock"]
        self.env = {"PATH": "/usr/bin:/bin", "HOME": directory, "LANG": "C"}

    def command(self, args):
        try:
            result = _exchange(self.prefix + args, self.env, b"", 15, DIAGNOSTIC_LIMIT)
        except StageTimeout as exc:
            raise IsolationError(f"Docker {args[0]} did not complete") from exc
        if result.exit_code:
            # Management commands only. Never include fixture streams in these errors.
            detail = result.stderr.decode("utf-8", "replace").strip()[:512]
            raise IsolationError(f"Docker {args[0]} failed ({result.exit_code}): {detail}")
        return result.stdout

    def cleanup(self, job):
        # Resolve the daemon-owned ID even if create succeeded but its CLI was interrupted.
        query = ["ps", "-aq", "--no-trunc", "--filter", f"label={LABEL}={job}",
                 "--filter", f"name=^/{job}$"]
        try:
            for _ in range(3):
                ids = self.command(query).decode("ascii").splitlines()
                if not ids:
                    return
                if len(ids) != 1 or not re.fullmatch(r"[0-9a-f]{64}", ids[0]):
                    raise IsolationError("Unexpected cleanup identity")
                try:
                    self.command(["rm", "--force", ids[0]])
                except IsolationError:
                    # AutoRemove may win the race against rm. Confirm by querying again.
                    pass
            if self.command(query).strip():
                raise IsolationError("Container still present")
        except (IsolationError, OSError, ValueError) as exc:
            raise CleanupError(f"Could not confirm removal of {job}") from exc


def _run(docker, image_id, stage, payload, timeout, cancel):
    job = "graphlink-lean-" + uuid.uuid4().hex
    try:
        container_id = docker.command(_create_args(image_id, stage, job)).decode("ascii").strip()
        if not re.fullmatch(r"[0-9a-f]{64}", container_id):
            raise IsolationError("Docker did not return a full container ID")
        try:
            spec, = json.loads(docker.command(["container", "inspect", container_id]))
            if spec["Id"] != container_id:
                raise IsolationError("Container inspection returned a different ID")
            _validate_container(spec, image_id, stage, job)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise IsolationError("Invalid inspection response from Docker") from exc
        if cancel is not None and cancel.is_set():
            raise StageCancelled("Fixture cancelled before start")
        stdout_limit = ARTIFACT_LIMIT if stage == "export" else DIAGNOSTIC_LIMIT
        return _exchange(docker.prefix + ["start", "--attach", "--interactive", container_id],
                         docker.env, payload, timeout, stdout_limit, cancel)
    finally:
        docker.cleanup(job)


def run_stage(image_id: str, stage: str, payload: bytes, timeout: float = 120,
              cancel: Event | None = None) -> StageResult:
    """Run one fixture stage in a fresh sandbox. Pass the provisioning build's --iidfile value."""
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("Expected a full immutable local Docker image ID")
    if stage not in STAGES or not isinstance(payload, bytes) or len(payload) > ARTIFACT_LIMIT:
        raise ValueError("Unknown stage or invalid payload")
    if stage == "export" and payload not in FIXTURES:
        raise ValueError("Unknown reviewed fixture")
    if stage in {"probe", "wait"} and payload:
        raise ValueError("Probe stages take no input")
    if not 0 < timeout <= 120:
        raise ValueError("Fixture timeout must be between zero and 120 seconds")
    if cancel is not None and cancel.is_set():
        raise StageCancelled("Fixture cancelled before creation")
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise IsolationError("The P0 isolation profile requires Linux/amd64")
    executable = shutil.which("docker", path="/usr/bin:/bin")
    if not executable:
        raise IsolationError("The local Docker CLI is unavailable")
    with tempfile.TemporaryDirectory(prefix="graphlink-lean-client-") as directory:
        docker = _Docker(executable, directory)
        # Check daemon availability before entering the create/cleanup transaction.
        options = json.loads(docker.command(["info", "--format", "{{json .SecurityOptions}}"]))
        if not (isinstance(options, list) and "name=apparmor" in options and
                "name=seccomp,profile=builtin" in options):
            raise IsolationError("Docker must enforce AppArmor and its built-in seccomp profile")
        return _run(docker, image_id, stage, payload, timeout, cancel)
