"""Container-native Python environments for the isolated workspace profile.

The *selection rules* mirror host Python: prefer a usable project .venv/venv,
otherwise use an app-owned environment scoped to workspace id and project root.
Container environments are deliberately distinct from host environments; a
Windows/macOS host venv must never be executed or repaired on the host.
All interpreter startup and virtualenv creation occur inside the container.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from backend.harness import python_env
from backend.harness.shell_isolation import cleanup_container, container_invocation
from graphlink_process_env import safe_subprocess_env
from graphlink_scratch_dirs import prepare_scratch_dir, safe_scratch_id

CONTAINER_PYTHON = "/usr/local/bin/python"
CONTAINER_WHEEL_CACHE = "/graphlink-wheel-cache"
CONTAINER_MANAGED_ENVS = "/graphlink-python-env"

# The script is harness-owned, not model-authored. Both python.exec and
# python.install use it, so they cannot create different first-use venvs.
# The optional fourth argument is the REPL protocol script to exec into.
VENV_BOOTSTRAP_SCRIPT = """
import os
from pathlib import Path
import shutil
import subprocess
import sys

venv = Path(sys.argv[1])
managed = sys.argv[2] == "1"
python = venv / "bin" / "python"
if venv.is_symlink():
    sys.exit("Refusing symlinked Python environment: " + str(venv))
if not ((venv / "pyvenv.cfg").is_file() and python.is_file()):
    if not managed:
        sys.exit("Project Python environment cannot run inside the container: " + str(venv))
    if venv.exists():
        if not venv.is_dir():
            sys.exit("Managed Python environment is not a directory: " + str(venv))
        shutil.rmtree(venv)
    venv.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "venv", "--copies", str(venv)], check=True)
if len(sys.argv) > 3:
    os.execv(str(python), [str(python), "-u", "-c", sys.argv[3]])
"""


@dataclass(frozen=True)
class IsolatedPythonPlan:
    interpreter: PurePosixPath        # Linux path inside container, even on Windows hosts
    host_venv: Path                   # Used for symlink checks, never executed
    managed: bool
    mounts: tuple[tuple[Path, str], ...] = ()

    @property
    def container_venv(self) -> PurePosixPath:
        return self.interpreter.parent.parent


def _project_container_venv_exists(venv: Path) -> bool:
    python = venv / "bin" / "python"
    return (venv / "pyvenv.cfg").is_file() and (
        python.is_file() or python.is_symlink()
    )


def planned_isolated_python_environment(
    workspace_id: str, cwd: Path, *, is_scratch: bool,
) -> IsolatedPythonPlan:
    """Pure selection for approval: no image pull, daemon call or file writes."""
    for name in (".venv", "venv"):
        candidate = cwd / name
        if _project_container_venv_exists(candidate):
            return IsolatedPythonPlan(
                PurePosixPath("/workspace") / name / "bin" / "python", candidate, False,
            )

    if is_scratch:
        host_venv = cwd / ".graphlink-isolated-python-venv"
        return IsolatedPythonPlan(
            PurePosixPath("/workspace/.graphlink-isolated-python-venv/bin/python"),
            host_venv, True,
        )

    # Match the host's project/workspace key and node-deletion lifecycle,
    # but use a separate container-only virtualenv in that app-owned bucket.
    host_venv = python_env.managed_venv_dir(
        workspace_id, cwd, is_scratch=False,
    ).with_name("isolated-venv")
    project_key = host_venv.parent.name
    mount_at = f"{CONTAINER_MANAGED_ENVS}/{safe_scratch_id(workspace_id)}/{project_key}"
    return IsolatedPythonPlan(
        PurePosixPath(mount_at) / "isolated-venv" / "bin" / "python",
        host_venv, True, ((host_venv.parent, mount_at),),
    )


def prepare_isolated_python_environment(plan: IsolatedPythonPlan) -> None:
    """Prepare only app-owned host mount points, *never* host Python or a venv."""
    if plan.managed and plan.host_venv.is_symlink():
        raise python_env.PythonEnvironmentError(
            f"managed Python environment is a symlink: {plan.host_venv}"
        )
    for host_dir, _target in plan.mounts:
        if host_dir.is_symlink():
            raise python_env.PythonEnvironmentError(
                f"managed Python environment directory is a symlink: {host_dir}"
            )
        prepare_scratch_dir(host_dir)


def isolated_python_container_env(plan: IsolatedPythonPlan) -> dict[str, str]:
    venv = str(plan.container_venv)
    return {
        "VIRTUAL_ENV": venv,
        "PATH": venv + "/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }


def isolated_repl_command(
    plan: IsolatedPythonPlan, cwd: Path, script: str,
) -> tuple[list[str], Callable[[], None]]:
    """A persistent, network-disabled container serving the REPL protocol."""
    env = safe_subprocess_env()
    argv, engine, name = container_invocation(
        [CONTAINER_PYTHON, "-u", "-c", VENV_BOOTSTRAP_SCRIPT,
         str(plan.container_venv), "1" if plan.managed else "0", script],
        cwd, env=env, extra_mounts=plan.mounts,
        container_env=isolated_python_container_env(plan),
    )
    return argv, lambda: cleanup_container(engine, name, env=env)
