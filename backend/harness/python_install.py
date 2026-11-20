"""Wheel-only, approval-bound package installation for harness Python environments.

The wheelhouse is shared by all workspaces and deliberately not part of any
workspace's node-delete or age-sweep lifecycle. Pip's normal HTTP cache alone
cannot satisfy an offline install: the wheelhouse is the local package index
used for *every* installation. Only a download from the canonical PyPI index
may add wheels to it; source archives, direct references and pip options are
not accepted as model arguments.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path, PurePosixPath

from api_provider import RequestCancelledError
from backend.harness.python_env import (
    PythonEnvironmentError, _usable_venv,
    ensure_python_environment,
    managed_venv_dir,
    venv_python,
)
from backend.harness.python_isolation import (
    CONTAINER_PYTHON, CONTAINER_WHEEL_CACHE, VENV_BOOTSTRAP_SCRIPT,
    IsolatedPythonPlan, isolated_python_container_env,
    planned_isolated_python_environment, prepare_isolated_python_environment,
)
from backend.harness.shell_isolation import (
    IsolationUnavailableError, cleanup_container, container_invocation,
    normalize_execution_profile,
)
from backend.harness.workspace import WorkspaceError, workspace_dir
from backend.tools import RunContext, ToolResult
from graphlink_execution_guard import (
    assign_guarded_subprocess,
    create_execution_guard,
    spawn_guarded_subprocess,
)
from graphlink_process_env import safe_subprocess_env
from graphlink_scratch_dirs import prepare_scratch_dir

DEFAULT_PYPI_INDEX = "https://pypi.org/simple/"
# Stable across application restarts and workspace deletion, even when the
# operating system clears temporary directories. Never age-swept.
PYTHON_WHEEL_CACHE_ROOT = Path.home() / ".graphlink" / "python-wheel-cache"
MAX_INSTALL_PACKAGES = 32
MAX_PACKAGE_CHARS = 180
_PIP_TIMEOUT_SECONDS = 300
_POLL_SECONDS = 0.2
_OUTPUT_CAP = 12_000
_CACHE_LOCK = threading.Lock()

# Deliberately narrower than PEP 508: index names, optional extras, and
# version constraints only. No URL/path/VCS reference, requirement file,
# environment marker, editable flag, or pip option can escape the PyPI-only
# and wheel-only subprocess policy. Pip itself validates the version grammar.
_VERSION = r"(?:===|==|!=|~=|<=|>=|<|>)\s*[A-Za-z0-9][A-Za-z0-9*.+!_-]*"
_REQUIREMENT = re.compile(
    rf"[A-Za-z0-9][A-Za-z0-9._-]*"
    rf"(?:\[[A-Za-z0-9][A-Za-z0-9.,_-]*\])?"
    rf"(?:\s*{_VERSION}(?:\s*,\s*{_VERSION})*)?\Z",
    re.ASCII,
)


def install_requirements_error(arguments: dict) -> str | None:
    """Check before approval, so the human is not asked to bless a non-install."""
    packages = arguments.get("packages")
    if not isinstance(packages, list) or not 1 <= len(packages) <= MAX_INSTALL_PACKAGES:
        return f"python.install needs 1 to {MAX_INSTALL_PACKAGES} packages in a list."
    for package in packages:
        if (
            not isinstance(package, str)
            or not package
            or len(package) > MAX_PACKAGE_CHARS
            or _REQUIREMENT.fullmatch(package) is None
        ):
            return (
                "python.install accepts only package names with optional extras and "
                "version specifiers (no paths, URLs, markers or pip options)."
            )
    # Unknown keys are not installation options. Reject them rather than
    # accepting a misleading approval that silently ignores their effects.
    if set(arguments) != {"packages"}:
        return "python.install accepts only the packages argument."
    return None


def workspace_install_binding(ctx: RunContext) -> tuple[str, Path, bool]:
    """Use the run-bound root; never trust an agent-supplied environment path."""
    workspace_id = getattr(ctx, "harness_workspace_id", None)
    if not isinstance(workspace_id, str) or not workspace_id:
        raise WorkspaceError("No harness workspace is bound to this run.")
    bound = getattr(ctx, "harness_workspace_dir", None)
    if isinstance(bound, Path):
        is_scratch = (
            getattr(ctx, "harness_workspace_is_user_dir", None) is False
            or bound.resolve() == workspace_dir(workspace_id).resolve()
        )
        return workspace_id, bound, is_scratch
    return workspace_id, workspace_dir(workspace_id), True


def planned_install_interpreter(
    workspace_id: str, root: Path, *, is_scratch: bool, profile: str = "host",
) -> Path | PurePosixPath:
    """Select without creating a venv, for the exact approval disclosure."""
    if normalize_execution_profile(profile) == "isolated":
        return planned_isolated_python_environment(
            workspace_id, root, is_scratch=is_scratch,
        ).interpreter
    for name in (".venv", "venv"):
        candidate = root / name
        if _usable_venv(candidate):
            return venv_python(candidate)
    return venv_python(managed_venv_dir(workspace_id, root, is_scratch=is_scratch))



def install_interpreter_identity(interpreter: Path) -> tuple:
    """Fingerprint the backing venv, not merely its disclosed pathname.

    Project .venv and the interpreter inside it may be symlinks. Pin the
    real backing directory, pyvenv.cfg and executable identities at
    disclosure time so a rename or symlink swap during consent cannot
    quietly redirect package installation. Missing components are recorded
    explicitly for an as-yet-uncreated managed environment.
    """
    venv_dir = interpreter.parent.parent

    def identity(path: Path):
        try:
            stat = path.stat()
        except (FileNotFoundError, NotADirectoryError):
            return None
        return (
            path.resolve(strict=True), stat.st_dev, stat.st_ino, stat.st_mode,
            stat.st_size, stat.st_mtime_ns,
        )

    return (
        identity(venv_dir),
        identity(venv_dir / "pyvenv.cfg"),
        identity(interpreter),
    )


def install_approval_summary(
    packages: list[str], interpreter: Path | PurePosixPath | None, *, isolated: bool = False,
) -> str:
    target = str(interpreter) if interpreter is not None else "(no workspace bound)"
    summary = "python.install\n--- environment\n" + target + "\n--- packages\n" + "\n".join(packages)
    if isolated:
        summary += (
            "\n--- network permission\n"
            "This install may access the network to download binary wheels from PyPI "
            "inside the container. Only its pip download step has network access; "
            "python.exec remains offline."
        )
    return summary


def _run_pip(argv: list[str], cwd: Path, cancel_event) -> tuple[int | None, str, str]:
    """Run argv without a shell, a secret-bearing environment, or an orphaned tree."""
    if cancel_event is not None and cancel_event.is_set():
        return None, "", "cancelled"
    env = safe_subprocess_env()
    # Disables all pip configuration files, including global extra-index-url
    # and find-links settings. The allowlisted parent env has no PIP_* vars.
    env["PIP_CONFIG_FILE"] = os.devnull
    kwargs: dict = {"env": env}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    guard = create_execution_guard()
    try:
        process = spawn_guarded_subprocess(
            guard, argv,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            **guard.popen_kwargs(),
            **kwargs,
        )
        try:
            assign_guarded_subprocess(guard, process)
        except BaseException:
            process.kill()
            process.wait(timeout=3)
            raise

        fragments: list[str] = []
        retained = [0]

        def drain() -> None:
            stream = process.stdout
            if stream is None:
                return
            try:
                while True:
                    chunk = stream.readline(4096)
                    if not chunk:
                        break
                    if retained[0] < _OUTPUT_CAP:
                        part = chunk[: _OUTPUT_CAP - retained[0]]
                        fragments.append(part)
                        retained[0] += len(part)
            except (OSError, ValueError):
                pass

        reader = threading.Thread(target=drain, name="python-install-output", daemon=True)
        reader.start()
        deadline = time.monotonic() + _PIP_TIMEOUT_SECONDS
        while process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                return None, "".join(fragments), "cancelled"
            if time.monotonic() >= deadline:
                return None, "".join(fragments), "timeout"
            time.sleep(_POLL_SECONDS)
        reader.join(timeout=2)
        output = "".join(fragments)
        if retained[0] >= _OUTPUT_CAP:
            output += "\n[output truncated]"
        return process.returncode, output, "ok"
    finally:
        guard.close()


def _checked_pip(argv: list[str], cwd: Path, cancel_event) -> tuple[int, str]:
    code, output, ended = _run_pip(argv, cwd, cancel_event)
    if ended == "cancelled":
        raise RequestCancelledError("stopped")
    if ended == "timeout":
        raise TimeoutError(f"Package operation timed out after {_PIP_TIMEOUT_SECONDS}s.\n{output}")
    if code is None:
        raise RuntimeError("Package subprocess returned no exit code.")
    return code, output


def _prepare_cache(cache_root: Path) -> tuple[Path, Path]:
    wheels, http = cache_root / "wheels", cache_root / "pip-http"
    if any(path.is_symlink() for path in (cache_root, wheels, http)):
        raise OSError("Refusing to use a symlinked Python wheel cache.")
    # prepare_scratch_dir secures the shared parent before creating leaves.
    prepare_scratch_dir(wheels)
    prepare_scratch_dir(http)
    return wheels, http


def _checked_isolated_command(
    command: list[str], cwd: Path, cancel_event, plan: IsolatedPythonPlan,
    cache_root: Path, *, network: bool = False,
) -> tuple[int, str]:
    """Run an approved pip/bootstrap command in the container, never on host.

    The only caller enabling network is the PyPI *download* phase. Every
    other command, including venv creation and wheel installation, is
    explicitly --network=none. Remove daemon-side containers even when a
    cancellation or timeout kills their local Docker/Podman CLI.
    """
    # The project (or a managed-env bind) might itself contain the cache:
    # then /workspace/... could still write to it despite a separate RO
    # /graphlink-wheel-cache mount. Never expose the cache through any
    # writable offline mount, including a parent or child of that directory.
    if not network:
        cache_path = cache_root.resolve()
        for source in (cwd, *(host for host, _target in plan.mounts)):
            writable_path = source.resolve()
            if (
                writable_path == cache_path
                or writable_path in cache_path.parents
                or cache_path in writable_path.parents
            ):
                raise PythonEnvironmentError(
                    "Isolated workspace overlaps the shared Python wheel cache; "
                    "offline install refused."
                )
    env = safe_subprocess_env()
    # Networked pip runs from the trusted image without the project. Only
    # that download stage may write to the shared wheel cache. Offline pip
    # can import code from a writable project venv, even with Python -I.
    container_workspace = cache_root if network else cwd
    mounts = ((cache_root, CONTAINER_WHEEL_CACHE),) if network else plan.mounts
    read_only_mounts = () if network else ((cache_root, CONTAINER_WHEEL_CACHE),)
    container_env = {"PIP_CONFIG_FILE": "/dev/null"}
    if not network:
        container_env.update(isolated_python_container_env(plan))
    argv, engine, name = container_invocation(
        command, container_workspace, env=env, network=network,
        extra_mounts=mounts, read_only_mounts=read_only_mounts,
        container_env=container_env,
    )
    try:
        return _checked_pip(argv, container_workspace, cancel_event)
    finally:
        cleanup_container(engine, name, env=env)


OFFLINE_WHEELS_UNAVAILABLE = "Cached wheels unavailable; network download needs approval."


def _install_in_worker(
    interpreter: Path | PurePosixPath, cwd: Path, packages: list[str], cancel_event,
    cache_root: Path, *, isolated_plan: IsolatedPythonPlan | None = None,
    offline_only: bool = False,
    approved_identity: tuple | None = None,
) -> ToolResult:
    # Concurrent tools cannot download into the same wheel filename at once.
    while not _CACHE_LOCK.acquire(timeout=_POLL_SECONDS):
        if cancel_event is not None and cancel_event.is_set():
            raise RequestCancelledError("stopped")
    try:
        wheels, http = _prepare_cache(cache_root)
        if isolated_plan is not None:
            prepare_isolated_python_environment(isolated_plan)
            # The image provides Python and ensurepip. Only the container
            # may create/repair its own Linux virtualenv.
            setup_code, setup_output = _checked_isolated_command(
                [CONTAINER_PYTHON, "-u", "-c", VENV_BOOTSTRAP_SCRIPT,
                 str(isolated_plan.container_venv),
                 "1" if isolated_plan.managed else "0"],
                cwd, cancel_event, isolated_plan, cache_root,
            )
            if setup_code:
                return ToolResult(
                    content=f"Could not prepare isolated Python environment:\n{setup_output}",
                    is_error=True,
                )
            pip_wheels = f"{CONTAINER_WHEEL_CACHE}/wheels"
            pip_http = f"{CONTAINER_WHEEL_CACHE}/pip-http"

        else:
            pip_wheels, pip_http = str(wheels), str(http)

        def run_pip(argv: list[str], *, network: bool = False) -> tuple[int, str]:
            if isolated_plan is None and approved_identity is not None and (
                install_interpreter_identity(Path(interpreter)) != approved_identity
            ):
                raise PythonEnvironmentError(
                    "Python environment changed after approval; ask again."
                )
            if isolated_plan is not None:
                return _checked_isolated_command(
                    argv, cwd, cancel_event, isolated_plan, cache_root, network=network,
                )
            return _checked_pip(argv, cwd, cancel_event)

        # Invoke the backing venv directly, not a mutable .venv symlink.
        # The displayed path remains the user's approved selection.
        executable = (
            venv_python(approved_identity[0][0])
            if approved_identity is not None else interpreter
        )
        # -I isolates pip from workspace-controlled modules. The networked
        # download must use the image Python, not a mutable project venv.
        # Keep the approved, pinned executable for host-profile installs.
        pip_args = [
            "-I", "-m", "pip", "--no-input",
            "--disable-pip-version-check",
        ]
        common = [str(executable), *pip_args]
        offline = [
            *common, "install", "--no-cache-dir", "--only-binary=:all:", "--no-index",
            f"--find-links={pip_wheels}", *packages,
        ]

        code, output = run_pip(offline)
        if code and "No module named pip" in output:
            # Some externally created project virtualenvs omit pip. This
            # bootstraps only bundled ensurepip wheels, after authorization.
            setup_code, setup_output = run_pip(
                [str(executable), "-I", "-m", "ensurepip", "--default-pip"],
            )
            if setup_code:
                return ToolResult(
                    content=f"Could not prepare pip in {interpreter}:\n{setup_output}",
                    is_error=True,
                )
            code, output = run_pip(offline)

        downloaded = False
        if code:
            if offline_only:
                # An unprompted Autonomous install may never start pip
                # download or turn on container networking. Its caller
                # requests a fresh approval before retrying with PyPI.
                return ToolResult(content=OFFLINE_WHEELS_UNAVAILABLE, is_error=True)
            # Cache was missing at least one wheel. Add only binary wheels
            # from the canonical default index, then install OFFLINE from
            # that same wheelhouse. A failed download leaves prior wheels
            # reusable in a future offline call.
            download = [
                CONTAINER_PYTHON if isolated_plan is not None else str(executable),
                *pip_args, f"--cache-dir={pip_http}", "download", "--only-binary=:all:",
                f"--index-url={DEFAULT_PYPI_INDEX}",
                f"--dest={pip_wheels}", "--retries=0", "--timeout=8",
                *packages,
            ]
            dl_code, dl_output = run_pip(download, network=isolated_plan is not None)
            if dl_code:
                return ToolResult(
                    content=(
                        f"No compatible cached wheels for {', '.join(packages)}. "
                        f"Download from the default PyPI index also failed.\n"
                        f"Cached install: {output}\nDownload: {dl_output}"
                    )[:_OUTPUT_CAP],
                    is_error=True,
                )
            downloaded = True
            code, output = run_pip(offline)

        if code:
            return ToolResult(
                content=f"Wheel-only installation failed in {interpreter}:\n{output}",
                is_error=True,
            )
        source = "newly downloaded PyPI wheels" if downloaded else "shared cached wheels"
        return ToolResult(
            content=(
                f"Installed (or already satisfied) {', '.join(packages)} into "
                f"{interpreter} using {source}.\n"
                f"Shared wheel cache: {wheels}\n{output}"
            )[:_OUTPUT_CAP],
        )
    finally:
        _CACHE_LOCK.release()


async def install_packages(
    ctx: RunContext, packages: list[str], *,
    cache_root: Path | None = None,
    offline_only: bool = False,
) -> ToolResult:
    """Install into the pinned environment. Offline-only calls never download."""
    import asyncio

    try:
        workspace_id, root, is_scratch = workspace_install_binding(ctx)
        profile = normalize_execution_profile(
            getattr(ctx, "harness_execution_profile", "host")
        )
        planned = planned_install_interpreter(
            workspace_id, root, is_scratch=is_scratch, profile=profile,
        )
        approved = getattr(ctx, "python_install_approved_interpreter", None)
        cancel_event = ctx.cancel.event if ctx.cancel is not None else None
        if cancel_event is not None and cancel_event.is_set():
            raise RequestCancelledError("stopped")
        if not isinstance(approved, (Path, PurePosixPath)) or approved != planned:
            return ToolResult(
                content="Python environment changed or was not bound to this install approval; ask again.",
                is_error=True,
            )
        binding = (workspace_id, root.resolve(), is_scratch, profile, planned)
        if profile == "isolated" and getattr(
            ctx, "python_install_approved_binding", None
        ) != binding:
            return ToolResult(
                content="Isolated Python workspace changed since approval; ask again.",
                is_error=True,
            )
        approved_identity = None
        managed_needs_creation = False
        if profile == "host":
            approved_identity = getattr(ctx, "python_install_approved_identity", None)
            if (
                not isinstance(approved_identity, tuple)
                or approved_identity != install_interpreter_identity(Path(planned))
            ):
                return ToolResult(
                    content="Python environment changed since approval; ask again.",
                    is_error=True,
                )
            managed = venv_python(
                managed_venv_dir(workspace_id, root, is_scratch=is_scratch)
            )
            managed_needs_creation = (
                planned == managed and not _usable_venv(managed.parent.parent)
            )
        interpreter: Path | PurePosixPath
        if profile == "isolated":
            plan = planned_isolated_python_environment(
                workspace_id, root, is_scratch=is_scratch,
            )
            interpreter = plan.interpreter
        else:
            interpreter = await asyncio.to_thread(
                lambda: ensure_python_environment(
                    workspace_id, root, is_scratch=is_scratch,
                    cancel_event=cancel_event,
                ),
            )
            if interpreter != approved:
                return ToolResult(
                    content="Python environment changed after approval; ask again.",
                    is_error=True,
                )
            # A missing/broken managed venv is created on first use;
            # a previously usable venv must still have the exact identity
            # that was disclosed. Pin the new managed identity for pip.
            prepared_identity = install_interpreter_identity(Path(interpreter))
            if not managed_needs_creation and prepared_identity != approved_identity:
                return ToolResult(
                    content="Python environment changed after approval; ask again.",
                    is_error=True,
                )
            if prepared_identity[0] is None or prepared_identity[2] is None:
                return ToolResult(content="Python environment is unavailable.", is_error=True)
            approved_identity = prepared_identity
            plan = None
        wheel_cache = cache_root if cache_root is not None else PYTHON_WHEEL_CACHE_ROOT
        if plan is not None:
            return await asyncio.to_thread(
                _install_in_worker, interpreter, root, packages, cancel_event,
                wheel_cache, isolated_plan=plan, offline_only=offline_only,
            )
        return await asyncio.to_thread(
            _install_in_worker, interpreter, root, packages, cancel_event, wheel_cache,
            approved_identity=approved_identity,
        )
    except (OSError, WorkspaceError, TimeoutError, IsolationUnavailableError,
            PythonEnvironmentError) as exc:
        return ToolResult(content=f"Could not install Python packages: {exc}", is_error=True)
    finally:
        # A future install can never borrow a previously approved target.
        setattr(ctx, "python_install_approved_interpreter", None)
        setattr(ctx, "python_install_approved_binding", None)
        setattr(ctx, "python_install_approved_identity", None)
