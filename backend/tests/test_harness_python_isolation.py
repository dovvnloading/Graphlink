"""Regression tests for Isolated workspace Python containment and approval."""
from __future__ import annotations

import asyncio
import io
from pathlib import Path
from unittest.mock import Mock

from backend.harness import python_env, python_install, python_isolation, tools_python
from backend.harness.python_isolation import (
    CONTAINER_PYTHON, IsolatedPythonPlan,
    isolated_repl_command, planned_isolated_python_environment,
)
from backend.harness.shell_isolation import CONTAINER_IMAGE, container_invocation
from backend.harness.tools_python import PythonReplRegistry, register_harness_python_tool
from backend.harness.tools_python_install import register_harness_python_install_tool
from backend.providers.base import ToolCall
from backend.tools import CODE_EXECUTE, RunContext, ToolRegistry, ToolResult
from graphlink_plugins.common import python_repl


def _isolated_context(project: Path, approve, workspace_id="isolated-test"):
    ctx = RunContext(
        granted_scopes=frozenset({CODE_EXECUTE}), request_approval=approve,
    )
    ctx.harness_workspace_id = workspace_id
    ctx.harness_workspace_dir = project
    ctx.harness_workspace_is_user_dir = True
    ctx.harness_execution_profile = "isolated"
    return ctx


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


def test_isolated_env_picks_project_venv_and_scopes_managed_fallback(tmp_path, monkeypatch):
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
    assert scratch.interpreter == Path("/workspace/.graphlink-isolated-python-venv/bin/python")
    assert not (tmp_path / "env-root").exists(), "Approval planning cannot create a venv"

    supplied = project / ".venv"
    (supplied / "bin").mkdir(parents=True)
    (supplied / "pyvenv.cfg").write_text("home = /usr/local/bin\n")
    (supplied / "bin/python").write_text("test sentinel")
    chosen = planned_isolated_python_environment("workspace-a", project, is_scratch=False)
    assert chosen.interpreter == Path("/workspace/.venv/bin/python")
    assert not chosen.managed and chosen.mounts == ()


def test_repl_command_is_offline_and_cleanup_reaps_daemon_container(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.harness.shell_isolation.shutil.which", return_value="/usr/bin/docker")
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
    monkeypatch.setattr("backend.harness.shell_isolation.shutil.which", return_value="/usr/bin/docker")

    def forbid_host_python(*args, **kwargs):
        raise AssertionError("Isolated python.exec must never prepare host Python")
    monkeypatch.setattr(tools_python, "ensure_python_environment", forbid_host_python)
    executed = []

    class FakeREPL:
        def __init__(self, *, python_executable, command_builder, **kwargs):
            self.python_executable = Path(python_executable)
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
    monkeypatch.setattr("backend.harness.shell_isolation.shutil.which", return_value="/usr/bin/docker")
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
        if "download" in argv:
            assert "--network=bridge" in argv
            assert f"--index-url={python_install.DEFAULT_PYPI_INDEX}" in argv
            return 0, "downloaded"
        assert "--network=none" in argv
        if "install" in argv:
            installs += 1
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
    monkeypatch.setattr("backend.harness.shell_isolation.shutil.which", return_value=None)

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
        assert not (tmp_path / "managed").joinpath("isolated-test").exists()
    finally:
        repls.stop_all()
