"""Image PID 1. Verify kernel-visible restrictions before invoking any fixture code."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

MEMORY = 4 * 1024**3
TMPFS_SIZE = 256 * 1024**2


def runtime_profile():
    import resource  # Linux-only; the host never executes this probe.

    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines())
    label = Path("/proc/self/attr/current").read_text().strip()
    if not (os.getpid() == 1 and os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 65534
            and label == "docker-default (enforce)" and status["NoNewPrivs"].strip() == "1"
            and status["Seccomp"].strip() == "2"):
        raise ValueError("Identity or process restrictions differ from the Docker profile")
    if any(int(status[key], 16) for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")):
        raise ValueError("Capabilities available to fixture")
    interfaces = sorted(path.name for path in Path("/sys/class/net").iterdir())
    if interfaces != ["lo"]:
        raise ValueError("Unexpected network interface")
    mounts = {}
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        before, after = line.split(" - ", 1)
        _, _, _, _, dest, options, *_ = before.split()
        mounts[dest] = (set(options.split(",")), after.split()[0])
    tmp_options, tmp_type = mounts["/tmp"]
    tmp = os.statvfs("/tmp")
    if not ("ro" in mounts["/"][0] and tmp_type == "tmpfs"
            and {"rw", "noexec", "nosuid", "nodev"} <= tmp_options
            and 0 < tmp.f_frsize * tmp.f_blocks <= TMPFS_SIZE):
        raise ValueError("Unexpected writable filesystem policy")
    cgroup = Path("/sys/fs/cgroup")
    memory = int((cgroup / "memory.max").read_text())
    swap = int((cgroup / "memory.swap.max").read_text())
    pids = int((cgroup / "pids.max").read_text())
    quota, period = map(int, (cgroup / "cpu.max").read_text().split())
    if not (0 < memory <= MEMORY and swap == 0 and 0 < pids <= 64 and 0 < quota <= period):
        raise ValueError("Missing or unexpected kernel resource limit")
    if (resource.getrlimit(resource.RLIMIT_CORE) != (0, 0)
            or resource.getrlimit(resource.RLIMIT_FSIZE) != (TMPFS_SIZE, TMPFS_SIZE)):
        raise ValueError("Missing file size or core limit")
    return {"apparmor": label, "noNewPrivileges": True, "seccompMode": 2, "capabilities": [],
            "uid": os.getuid(), "interfaces": interfaces, "readOnlyRoot": True,
            "memoryBytes": memory, "swapBytes": swap, "pids": pids,
            "cpuQuota": quota, "cpuPeriod": period, "tmpfsBytes": tmp.f_frsize * tmp.f_blocks}


def main(args):
    if len(args) != 1 or args[0] not in {"probe", "wait", "export", "replay"}:
        raise ValueError("Unknown isolation stage")
    profile = runtime_profile()
    if args[0] == "probe":
        toolchain = json.loads(Path("/opt/graphlink/toolchain-evidence.json").read_text())
        sys.stdout.write(json.dumps({"runtime": profile, "toolchain": toolchain}, sort_keys=True) + "\n")
        return 0
    if args[0] == "wait":
        # Use a harmless child so the timeout fixture also exercises descendant cleanup.
        with subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(30)"]) as child:
            sys.stdout.write("wait-started\n")
            sys.stdout.flush()
            return child.wait()
    # Keep an image-side finite deadline even if the client vanishes. Exiting namespace
    # PID 1 terminates remaining descendants; Docker AutoRemove reclaims its container.
    return subprocess.run(
        [sys.executable, "-I", "/opt/graphlink/checker/stage.py", args[0]], timeout=120, check=False
    ).returncode


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        sys.stderr.write(f"Isolation preflight or watchdog failed: {exc}\n")
        sys.exit(125)
