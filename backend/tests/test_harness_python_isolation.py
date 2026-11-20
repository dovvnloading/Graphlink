"""Regression tests for Isolated workspace Python containment and approval."""
from __future__ import annotations

import asyncio
import io
import os
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

import pytest
from unittest.mock import Mock

from backend.harness import python_env, python_install, python_isolation, tool_catalog, tools_python
from backend.harness.loop import HarnessRunContext
from backend.harness.python_isolation import (
    CONTAINER_PYTHON, isolated_repl_command, planned_isolated_python_environment,
)
from backend.harness.shell_isolation import CONTAINER_IMAGE, container_invocation
from backend.harness.tools_python import PythonReplRegistry, register_harness_python_tool
from backend.harness.tools_python_install import register_harness_python_install_tool
from backend.providers.base import ToolCall
from backend.tools import CODE_EXECUTE, ToolRegistry, ToolResult
from graphlink_plugins.common import python_repl
from graphlink_scratch_dirs import gc_stale_by_age


def _isolated_context(project: Path, approve, workspace_id="isolated-test"):
    return HarnessRunContext(
        granted_scopes=frozenset({CODE_EXECUTE}),
        request_approval=approve,
        harness_workspace_id=workspace_id,
        harness_workspace_dir=project,
        harness_workspace_is_user_dir=True,
        harness_execution_profile="isolated",
    )


def test_container_argv_and_mounts_respect_network_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "backend.harness.shell_isolation.shutil.which",
        lambda name, *, path: "/usr/bin/docker" if name == "docker" else None,
    )
    args = [CONTAINER_PYTHON, "-u", "-c", "print('container')"]
    extra = tmp_path / "managed"
    extra.mkdir()
    offline, _, _ = container_invocation(
        args, tmp_path, env={"PATH": "/usr/bin"},
        extra_mounts=((extra, "/graphlink-python-env/test"),),
        container_env={"VIRTUAL_ENV": "/graphlink-python-env/test/venv"},
    )
    online, _, _ = container_invocation(
        args, tmp_path, env={"PATH": "/usr/bin"}, network=True,
    )
    assert "--network=none" in offline
    assert "--network=bridge" in online
    assert "--network=bridge" not in offline
    assert "--network=none" not in online
    assert offline[-len(args) - 1:] == [CONTAINER_IMAGE, *args]
    assert "/bin/sh" not in offline
    assert f"type=bind,source={extra.resolve()},target=/graphlink-python-env/test" in offline
    for flag in ("--read-only", "--cap-drop=ALL", "--pids-limit=128",
                 "--memory=512m", "--security-opt=no-new-privileges"):
        assert flag in offline and flag in online



def test_isolated_offline_project_pip_cannot_write_shared_wheel_cache(
    tmp_path, monkeypatch,
):
    """#864: untrusted project pip gets RO cache; only image download gets RW."""
    from backend.harness.python_isolation import IsolatedPythonPlan, CONTAINER_WHEEL_CACHE

    project, cache = tmp_path / "project", tmp_path / "wheel-cache"
    project.mkdir()
    cache.mkdir()
    # Deliberately select a project-controlled interpreter, whose -m pip
    # can import arbitrary modules from its writable site-packages.
    plan = IsolatedPythonPlan(
        PurePosixPath("/workspace/.venv/bin/python"), project / ".venv", False,
    )
    monkeypatch.setattr(
        "backend.harness.shell_isolation.shutil.which",
        lambda name, *, path: "/usr/bin/docker" if name == "docker" else None,
    )
    commands = []
    monkeypatch.setattr(
        python_install, "_checked_pip",
        lambda argv, cwd, cancel_event: (commands.append((argv, cwd)) or 0, ""),
    )
    monkeypatch.setattr(python_install, "cleanup_container", lambda *args, **kwargs: None)

    command = [str(plan.interpreter), "-I", "-m", "pip", "install", "demo"]
    python_install._checked_isolated_command(command, project, None, plan, cache)
    offline_argv, offline_cwd = commands.pop()
    offline_mounts = [
        offline_argv[i + 1] for i, arg in enumerate(offline_argv[:-1]) if arg == "--mount"
    ]
    assert "--network=none" in offline_argv and offline_cwd == project
    assert f"type=bind,source={project.resolve()},target=/workspace" in offline_mounts
    assert (
        f"type=bind,source={cache.resolve()},target={CONTAINER_WHEEL_CACHE},readonly"
        in offline_mounts
    )
    assert not any(
        mount.endswith(f"target={CONTAINER_WHEEL_CACHE}") for mount in offline_mounts
    ), "Project pip must never receive a writable cache mount"

    python_install._checked_isolated_command(command, project, None, plan, cache, network=True)
    download_argv, download_cwd = commands.pop()
    download_mounts = [
        download_argv[i + 1] for i, arg in enumerate(download_argv[:-1]) if arg == "--mount"
    ]
    assert "--network=bridge" in download_argv and download_cwd == cache
    assert f"type=bind,source={cache.resolve()},target={CONTAINER_WHEEL_CACHE}" in download_mounts
    assert not any("readonly" in mount for mount in download_mounts)
    assert str(project) not in " ".join(download_argv)




def test_isolated_install_rejects_a_writable_workspace_covering_shared_cache(tmp_path):
    """A RO mount alone cannot protect cache reachable via /workspace."""
    from backend.harness.python_isolation import IsolatedPythonPlan

    cache = tmp_path / "wheel-cache"
    cache.mkdir()
    plan = IsolatedPythonPlan(
        PurePosixPath("/workspace/.venv/bin/python"), tmp_path / ".venv", False,
    )
    with pytest.raises(python_install.PythonEnvironmentError, match="overlaps"):
        python_install._checked_isolated_command(
            [str(plan.interpreter), "-I", "-m", "pip", "install", "demo"],
            tmp_path, None, plan, cache,
        )




def test_isolated_env_uses_scoped_managed_venv_for_unverified_project_venv(tmp_path, monkeypatch):
    monkeypatch.setattr(python_env, "HARNESS_PYTHON_ENV_ROOT", tmp_path / "env-root")
    project, other = tmp_path / "project", tmp_path / "other"
    project.mkdir()
    other.mkdir()

    a = planned_isolated_python_environment("workspace-a", project, is_scratch=False)
    b = planned_isolated_python_environment("workspace-a", other, is_scratch=False)
    c = planned_isolated_python_environment("workspace-b", project, is_scratch=False)
    scratch = planned_isolated_python_environment("workspace-a", project, is_scratch=True)
    assert a.managed and b.managed and c.managed
    assert a.interpreter != b.interpreter != c.interpreter
    assert a.host_venv != python_env.managed_venv_dir(
        "workspace-a", project, is_scratch=False,
    ), "Host and container venvs must not overlap"
    assert a.host_venv.parent == python_env.managed_venv_dir(
        "workspace-a", project, is_scratch=False,
    ).parent, "Retain the existing node-delete cleanup bucket"
    assert scratch.host_venv == project / ".graphlink-isolated-python-venv"
    assert scratch.interpreter == PurePosixPath("/workspace/.graphlink-isolated-python-venv/bin/python")
    assert not (tmp_path / "env-root").exists(), "Approval planning cannot create a venv"

    supplied = project / ".venv"
    (supplied / "bin").mkdir(parents=True)
    (supplied / "pyvenv.cfg").write_text("home = /usr/local/bin\n")
    (supplied / "bin/python").write_text("test sentinel")
    chosen = planned_isolated_python_environment("workspace-a", project, is_scratch=False)
    assert chosen.managed
    assert chosen.interpreter == a.interpreter
    assert chosen.host_venv == a.host_venv
    assert chosen.mounts == a.mounts
    assert (supplied / "bin/python").read_text() == "test sentinel"


def test_host_only_project_venv_never_replaces_managed_isolated_exec_or_install(tmp_path, monkeypatch):
    """A host Python in /opt may work locally but cannot be resolved in python:3.12-slim."""
    project = tmp_path / "project"
    supplied = project / ".venv"
    (supplied / "bin").mkdir(parents=True)
    (supplied / "pyvenv.cfg").write_text("home = /opt/python311/bin\n")
    host_python = supplied / "bin" / "python"
    if os.name == "nt":
        host_python.write_text("host-only interpreter")
    else:
        host_python.symlink_to("/opt/python311/bin/python3.11")
    monkeypatch.setattr(python_env, "HARNESS_PYTHON_ENV_ROOT", tmp_path / "env-root")

    plan = planned_isolated_python_environment("ws", project, is_scratch=False)
    assert plan.managed and plan.mounts
    assert plan.interpreter != PurePosixPath("/workspace/.venv/bin/python")
    assert plan.host_venv != supplied
    assert python_install.planned_install_interpreter(
        "ws", project, is_scratch=False, profile="isolated",
    ) == plan.interpreter

    monkeypatch.setattr("backend.harness.shell_isolation.shutil.which", Mock(return_value="/usr/bin/docker"))
    argv, _cleanup = isolated_repl_command(plan, project, "print('hello')")
    assert argv[-3:] == [str(plan.container_venv), "1", "print('hello')"]
    assert (supplied / "pyvenv.cfg").is_file()
    assert host_python.exists() or host_python.is_symlink()


def test_repl_command_is_offline_and_cleanup_reaps_daemon_container(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.harness.shell_isolation.shutil.which", Mock(return_value="/usr/bin/docker"))
    cleaned = []
    monkeypatch.setattr(
        python_isolation, "cleanup_container",
        lambda engine, name, *, env: cleaned.append((engine, name)),
    )
    plan = planned_isolated_python_environment("ws", tmp_path, is_scratch=True)
    argv, cleanup = isolated_repl_command(plan, tmp_path, "print('code')")
    assert "--network=none" in argv and "--network=bridge" not in argv
    assert argv[argv.index(CONTAINER_IMAGE) + 1] == CONTAINER_PYTHON
    assert "print('code')" == argv[-1]
    assert "--env" in argv
    cleanup()
    assert len(cleaned) == 1 and cleaned[0][0] == "docker"


def test_python_repl_closes_remote_container_on_stop_and_startup_error(tmp_path, monkeypatch):
    cleanup = Mock()
    guard = Mock()
    guard.popen_kwargs.return_value = {}
    monkeypatch.setattr(python_repl, "create_execution_guard", lambda: guard)
    monkeypatch.setattr(python_repl, "assign_guarded_subprocess", lambda g, p: None)
    process = Mock()
    process.stdin, process.stdout, process.stderr = io.StringIO(), io.StringIO(), None
    monkeypatch.setattr(
        python_repl, "spawn_guarded_subprocess",
        lambda guard, argv, **kwargs: process,
    )
    builder = lambda script: (["docker", "run", script], cleanup)
    repl = python_repl.PythonREPL(
        cwd=tmp_path, manage_cwd=False, python_executable=Path("/workspace/venv/bin/python"),
        command_builder=builder,
    )
    repl.start()
    repl.stop()
    repl.stop()
    cleanup.assert_called_once()

    failed_cleanup = Mock()
    failing = python_repl.PythonREPL(
        cwd=tmp_path, manage_cwd=False, command_builder=lambda script: (
            ["docker", "run", script], failed_cleanup,
        ),
    )
    def failed_spawn(*args, **kwargs):
        raise OSError("Docker engine unavailable")
    monkeypatch.setattr(python_repl, "spawn_guarded_subprocess", failed_spawn)
    try:
        failing.start()
    except OSError:
        pass
    else:
        raise AssertionError("Container startup failure must surface")
    failed_cleanup.assert_called_once()


def test_python_exec_uses_container_not_host_interpreter(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(python_env, "HARNESS_PYTHON_ENV_ROOT", tmp_path / "managed")
    monkeypatch.setattr("backend.harness.shell_isolation.shutil.which", Mock(return_value="/usr/bin/docker"))

    def forbid_host_python(*args, **kwargs):
        raise AssertionError("Isolated python.exec must never prepare host Python")
    monkeypatch.setattr(tools_python, "ensure_python_environment", forbid_host_python)
    executed = []

    class FakeREPL:
        def __init__(self, *, python_executable, command_builder, **kwargs):
            self.python_executable = python_executable
            self.last_run_failed = False
            argv, cleanup = command_builder("print('stub')")
            assert "--network=none" in argv
            assert argv[argv.index(CONTAINER_IMAGE) + 1] == CONTAINER_PYTHON
            self.cleanup = cleanup

        def execute(self, code, *args):
            executed.append(code)
            return "inside container"

        def stop(self):
            pass

    monkeypatch.setattr(tools_python, "PythonREPL", FakeREPL)
    async def approve(call):
        return True
    ctx = _isolated_context(project, approve)
    registry, repls = ToolRegistry(), PythonReplRegistry()
    register_harness_python_tool(registry, repls)
    result = asyncio.run(registry.invoke(
        ToolCall("1", "python.exec", {"code": "print('hello')"}), ctx,
    ))
    assert not result.is_error and result.content == "inside container"
    assert executed == ["print('hello')"]
    assert repls._profiles["isolated-test"] == "isolated"
    repls.stop_all()


def test_isolated_install_only_download_has_network_and_all_steps_use_container(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(python_env, "HARNESS_PYTHON_ENV_ROOT", tmp_path / "envs")
    monkeypatch.setattr("backend.harness.shell_isolation.shutil.which", Mock(return_value="/usr/bin/docker"))
    monkeypatch.setattr(python_install, "cleanup_container", lambda *a, **k: None)
    plan = planned_isolated_python_environment("ws", project, is_scratch=False)
    calls = []
    installs = 0

    def fake_checked(argv, cwd, cancel_event):
        nonlocal installs
        calls.append(argv)
        assert argv[:2] == ["/usr/bin/docker", "run"]
        assert CONTAINER_IMAGE in argv
        assert "--mount" in argv
        command = argv[argv.index(CONTAINER_IMAGE) + 1:]
        if "download" in command:
            assert "--network=bridge" in argv
            assert command[:4] == [CONTAINER_PYTHON, "-I", "-m", "pip"]
            assert f"--index-url={python_install.DEFAULT_PYPI_INDEX}" in argv
            assert "--only-binary=:all:" in argv
            # Bridge networking must never expose workspace-controlled pip,
            # its site-packages, or other project files to this process.
            cache_mount = f"type=bind,source={(tmp_path / 'cache').resolve()},target=/workspace"
            project_mount = f"type=bind,source={project.resolve()},target=/workspace"
            managed_mount = (
                f"type=bind,source={plan.host_venv.parent.resolve()},target={plan.mounts[0][1]}"
            )
            assert cache_mount in argv
            assert project_mount not in argv
            assert managed_mount not in argv
            assert not any(arg.startswith("VIRTUAL_ENV=") for arg in argv)
            return 0, "downloaded"
        assert "--network=none" in argv
        if "install" in command:
            installs += 1
            assert command[:4] == [str(plan.interpreter), "-I", "-m", "pip"]
            assert "--no-index" in argv
            return (1, "cache miss") if installs == 1 else (0, "installed")
        return 0, "created venv"

    monkeypatch.setattr(python_install, "_checked_pip", fake_checked)
    result = python_install._install_in_worker(
        plan.interpreter, project, ["example==1.0"], None, tmp_path / "cache",
        isolated_plan=plan,
    )
    assert not result.is_error, result.content
    assert installs == 2
    assert len(calls) == 4  # venv bootstrap, offline miss, online download, offline install
    assert ["--network=bridge" in argv for argv in calls] == [False, False, True, False]
    assert not python_env._usable_venv(plan.host_venv), "Never create the host venv"


def test_isolated_install_requires_bound_approval_and_discloses_network(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(python_env, "HARNESS_PYTHON_ENV_ROOT", tmp_path / "envs")
    planned = python_install.planned_install_interpreter(
        "isolated-test", project, is_scratch=False, profile="isolated",
    )
    summary = python_install.install_approval_summary(["example==1"], planned, isolated=True)
    assert str(planned) in summary
    assert "network permission" in summary and "pip download" in summary

    async def approve(call):
        ctx.python_install_approved_interpreter = planned
        # Deliberately omit the binding: a path alone is never enough.
        return True

    ctx = _isolated_context(project, approve)
    registry = ToolRegistry()
    register_harness_python_install_tool(registry)
    result = asyncio.run(registry.invoke(
        ToolCall("install", "python.install", {"packages": ["example==1"]}), ctx,
    ))
    assert result.is_error and "changed" in result.content
    assert not (tmp_path / "envs").exists()

    def fake_worker(*args, **kwargs):
        assert kwargs["isolated_plan"].interpreter == planned
        return ToolResult("installed")

    monkeypatch.setattr(python_install, "_install_in_worker", fake_worker)
    async def approved(call):
        ctx.python_install_approved_interpreter = planned
        ctx.python_install_approved_binding = (
            "isolated-test", project.resolve(), False, "isolated", planned,
        )
        return True
    ctx.request_approval = approved
    result = asyncio.run(registry.invoke(
        ToolCall("install-again", "python.install", {"packages": ["example==1"]}), ctx,
    ))
    assert not result.is_error and result.content == "installed"
    assert ctx.python_install_approved_interpreter is None
    assert ctx.python_install_approved_binding is None


def test_profile_switch_retires_live_host_python_even_without_next_exec(tmp_path):
    repls = PythonReplRegistry()
    old = Mock()
    repls._repls["ws"] = (old, tmp_path)
    repls._profiles["ws"] = "host"
    repls.bind_root("ws", tmp_path, "isolated")
    old.stop.assert_called_once()
    assert "ws" not in repls._repls
    assert "ws" not in repls._profiles


def test_missing_container_engine_fails_closed_without_host_python(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(python_env, "HARNESS_PYTHON_ENV_ROOT", tmp_path / "managed")
    monkeypatch.setattr("backend.harness.shell_isolation.shutil.which", Mock(return_value=None))

    def forbid_host_python(*args, **kwargs):
        raise AssertionError("Missing Docker/Podman must not fall back to host Python")
    monkeypatch.setattr(tools_python, "ensure_python_environment", forbid_host_python)
    async def approve(_call):
        return True

    registry, repls = ToolRegistry(), PythonReplRegistry()
    register_harness_python_tool(registry, repls)
    try:
        result = asyncio.run(registry.invoke(
            ToolCall("missing-engine", "python.exec", {"code": "print('hi')"}),
            _isolated_context(project, approve),
        ))
        assert result.is_error
        assert "Docker or Podman" in result.content
        assert not (project / ".venv").exists()
        plan = planned_isolated_python_environment(
            "isolated-test", project, is_scratch=False,
        )
        assert not plan.host_venv.exists(), "No virtualenv is created on the host"
    finally:
        repls.stop_all()


def _container_native_stub(project_venv: Path) -> None:
    """A --copies Python 3.12 venv's host-visible layout (never run on host)."""
    (project_venv / "bin").mkdir(parents=True)
    (project_venv / "pyvenv.cfg").write_text(
        "home = /usr/local/bin\ninclude-system-site-packages = false\nversion = 3.12.9\n",
        encoding="utf-8",
    )
    (project_venv / "bin/python").write_bytes(b"container-only Python placeholder")


@pytest.mark.parametrize("name", [".venv", "venv"])
def test_isolated_selects_container_native_project_venv_for_exec_and_install(tmp_path, monkeypatch, name):
    project = tmp_path / "project"
    project.mkdir()
    supplied = project / name
    _container_native_stub(supplied)
    monkeypatch.setattr(python_env, "HARNESS_PYTHON_ENV_ROOT", tmp_path / "env-root")
    monkeypatch.setattr("backend.harness.shell_isolation.shutil.which", Mock(return_value="/usr/bin/docker"))

    plan = planned_isolated_python_environment("agent", project, is_scratch=False)
    expected = PurePosixPath("/workspace") / name / "bin" / "python"
    assert not plan.managed
    assert plan.host_venv == supplied
    assert plan.interpreter == expected
    assert plan.mounts == ()
    assert python_install.planned_install_interpreter(
        "agent", project, is_scratch=False, profile="isolated",
    ) == expected
    argv, _cleanup = isolated_repl_command(plan, project, "print('hello')")
    assert "--network=none" in argv
    assert argv[-3:] == [str(plan.container_venv), "0", "print('hello')"]
    assert "VIRTUAL_ENV=/workspace/" + name in argv
    assert not (tmp_path / "env-root").exists(), "Planning must not create host Python"


def test_isolated_prefers_dot_venv_then_compatible_venv_and_skips_host_symlinks(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(python_env, "HARNESS_PYTHON_ENV_ROOT", tmp_path / "env-root")
    _container_native_stub(project / "venv")
    assert planned_isolated_python_environment("ws", project, is_scratch=False).host_venv == project / "venv"

    _container_native_stub(project / ".venv")
    assert planned_isolated_python_environment("ws", project, is_scratch=False).host_venv == project / ".venv"

    (project / ".venv" / "bin/python").unlink()
    if os.name == "nt":
        # Symlink creation can require Windows privileges; test the fallback
        # with incompatible metadata on this host instead.
        (project / ".venv" / "bin/python").write_text("host-only Python")
        (project / ".venv" / "pyvenv.cfg").write_text("home = /opt/python\nversion = 3.11.9\n")
    else:
        (project / ".venv" / "bin/python").symlink_to("/usr/local/bin/python")
    assert planned_isolated_python_environment("ws", project, is_scratch=False).host_venv == project / "venv"

    # Even a plausible home path is insufficient for the wrong Python minor.
    (project / "venv" / "pyvenv.cfg").write_text("home = /usr/local/bin\nversion = 3.11.9\n")
    fallback = planned_isolated_python_environment("ws", project, is_scratch=False)
    assert fallback.managed and fallback.interpreter != PurePosixPath("/workspace/venv/bin/python")


def test_isolated_project_install_targets_selected_venv_and_bootstraps_unmanaged(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    _container_native_stub(project / ".venv")
    monkeypatch.setattr(python_env, "HARNESS_PYTHON_ENV_ROOT", tmp_path / "env-root")
    monkeypatch.setattr("backend.harness.shell_isolation.shutil.which", Mock(return_value="/usr/bin/docker"))
    monkeypatch.setattr(python_install, "cleanup_container", lambda *_args, **_kwargs: None)
    calls = []

    def fake_checked(argv, _cwd, _cancel):
        calls.append(argv)
        assert "--network=none" in argv
        return 0, "ok"

    monkeypatch.setattr(python_install, "_checked_pip", fake_checked)
    plan = planned_isolated_python_environment("ws", project, is_scratch=False)
    result = python_install._install_in_worker(
        plan.interpreter, project, ["example==1"], None, tmp_path / "cache",
        isolated_plan=plan,
    )
    assert not result.is_error, result.content
    assert len(calls) == 2  # bootstrap probe, then cached offline pip install
    assert calls[0][-2:] == ["/workspace/.venv", "0"]
    assert calls[1][calls[1].index(CONTAINER_IMAGE) + 1] == "/workspace/.venv/bin/python"
    assert not (tmp_path / "env-root").exists()
    assert (project / ".venv/bin/python").read_bytes() == b"container-only Python placeholder"


@pytest.mark.skipif(os.name == "nt", reason="Container-style virtualenvs have POSIX bin/python")
def test_project_venv_bootstrap_probes_real_interpreter_and_rejects_broken_one(tmp_path):
    """Exercise the same preflight used in the image without a Docker dependency."""
    actual = tmp_path / "real"
    subprocess.run(
        [sys.executable, "-m", "venv", "--copies", "--without-pip", str(actual)],
        check=True, capture_output=True, text=True,
    )
    command = [sys.executable, "-u", "-c", python_isolation.VENV_BOOTSTRAP_SCRIPT]
    success = subprocess.run(
        [*command, str(actual), "0", "import sys; print(sys.prefix)"],
        capture_output=True, text=True, check=False,
    )
    assert success.returncode == 0, success.stderr
    assert success.stdout.strip() == str(actual)

    broken = tmp_path / "broken"
    (broken / "bin").mkdir(parents=True)
    (broken / "pyvenv.cfg").write_text("home = /usr/local/bin\nversion = 3.12.9\n")
    (broken / "bin/python").write_text("not executable Python")
    failed = subprocess.run([*command, str(broken), "0"], capture_output=True, text=True, check=False)
    assert failed.returncode != 0
    assert "cannot run inside the container" in failed.stderr
    assert (broken / "bin/python").read_text() == "not executable Python"


def test_isolated_python_guidance_matches_selected_project_and_fallback_envs(tmp_path, monkeypatch):
    project = tmp_path / "trusted-project"
    project.mkdir()
    _container_native_stub(project / ".venv")
    monkeypatch.setattr(python_env, "HARNESS_PYTHON_ENV_ROOT", tmp_path / "managed")

    selected = planned_isolated_python_environment("ws", project, is_scratch=False)
    assert not selected.managed
    assert selected.interpreter == PurePosixPath("/workspace/.venv/bin/python")
    assert python_install.planned_install_interpreter(
        "ws", project, is_scratch=False, profile="isolated",
    ) == selected.interpreter
    fallback = planned_isolated_python_environment("ws", project, is_scratch=True)
    assert fallback.managed
    assert fallback.interpreter != selected.interpreter

    # Both the tool schema and tool_guide reach the model's live prompts.
    for guidance in (tools_python.PYTHON_EXEC_SPEC.description,
                     tools_python.USAGE_HINT, tool_catalog.tool_guide()):
        assert "compatible project .venv/venv" in guidance
        assert "managed" in guidance and "fallback" in guidance
        assert "always uses a separate managed" not in guidance


def test_isolated_managed_env_refreshes_bucket_used_by_startup_sweep(tmp_path, monkeypatch):
    """Daily use of a nested project venv must keep its agent bucket alive."""
    project = tmp_path / "project"
    project.mkdir()
    root = tmp_path / "managed-envs"
    monkeypatch.setattr(python_env, "HARNESS_PYTHON_ENV_ROOT", root)
    plan = planned_isolated_python_environment("active-agent", project, is_scratch=False)

    # Also enforce the root-before-leaf preparation ordering on Windows,
    # where the separate POSIX ownership-rejection test is skipped.
    prepared = []
    real_prepare = python_isolation.prepare_scratch_dir

    def record_prepare(path):
        prepared.append(path)
        real_prepare(path)

    monkeypatch.setattr(python_isolation, "prepare_scratch_dir", record_prepare)
    python_isolation.prepare_isolated_python_environment(plan)
    bucket = plan.host_venv.parent.parent
    assert prepared[:2] == [bucket, plan.host_venv.parent]
    marker = plan.host_venv.parent / "installed-dependency.txt"
    marker.write_text("keep me", encoding="utf-8")
    old = time.time() - 8 * 24 * 60 * 60
    os.utime(bucket, (old, old))

    python_isolation.prepare_isolated_python_environment(plan)
    assert bucket.stat().st_mtime > time.time() - 60
    assert gc_stale_by_age(root) == []
    assert marker.read_text(encoding="utf-8") == "keep me"


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership of the shared temp root")
def test_isolated_managed_env_rejects_foreign_owned_shared_root(tmp_path, monkeypatch):
    """A 0700 agent bucket cannot be safe under an attacker-owned parent."""
    project = tmp_path / "project"
    project.mkdir()
    root = tmp_path / "managed-envs"
    root.mkdir()
    monkeypatch.setattr(python_env, "HARNESS_PYTHON_ENV_ROOT", root)
    plan = planned_isolated_python_environment("agent", project, is_scratch=False)

    # Simulate a root precreated by another UID without needing privileges.
    our_uid = os.getuid()
    monkeypatch.setattr("graphlink_scratch_dirs.os.getuid", lambda: our_uid + 1)
    with pytest.raises(OSError, match="owned by uid"):
        python_isolation.prepare_isolated_python_environment(plan)
    assert not plan.host_venv.parent.exists()
