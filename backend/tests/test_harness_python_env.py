"""Real virtualenv integration tests for the workspace agent's python.exec."""

from __future__ import annotations

import asyncio
import io
import subprocess
import sys
import threading
import venv
from pathlib import Path

import pytest

from api_provider import RequestCancelledError
from backend.harness import python_env, workspace
from backend.harness.loop import HarnessRunContext
from backend.harness.tools_python import PythonReplRegistry, register_harness_python_tool
from backend.providers.base import ToolCall
from backend.tools import CODE_EXECUTE, ToolRegistry
from graphlink_scratch_dirs import remove_scratch_dir_for_id


def _setup():
    registry = ToolRegistry()
    repls = PythonReplRegistry()
    register_harness_python_tool(registry, repls)
    return registry, repls


def _context(path: Path, workspace_id: str = "env-test", *, is_scratch: bool = False):
    async def approve(_call):
        return True

    return HarnessRunContext(
        granted_scopes=frozenset({CODE_EXECUTE}),
        request_approval=approve,
        harness_workspace_id=workspace_id,
        harness_workspace_dir=path,
        harness_workspace_is_user_dir=not is_scratch,
    )


def _run(registry, ctx, code):
    return asyncio.run(registry.invoke(
        ToolCall(id="env-test-call", name="python.exec", arguments={"code": code}), ctx,
    ))


def test_project_dot_venv_takes_priority_and_is_used_by_running_python(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    dot_venv = project / ".venv"
    other_venv = project / "venv"
    venv.EnvBuilder(with_pip=False).create(dot_venv)
    venv.EnvBuilder(with_pip=False).create(other_venv)
    managed_root = tmp_path / "managed"
    monkeypatch.setattr(python_env, "HARNESS_PYTHON_ENV_ROOT", managed_root)

    assert python_env.ensure_python_environment(
        "project-1", project, is_scratch=False,
    ) == python_env.venv_python(dot_venv)
    assert not managed_root.exists(), "an existing project venv must not create a managed one"

    registry, repls = _setup()
    try:
        result = _run(registry, _context(project), (
            "import os, sys; print(sys.prefix); print(os.environ['VIRTUAL_ENV']); "
            "print(os.environ['PATH'].split(os.pathsep)[0]); print(os.getcwd())"
        ))
        assert not result.is_error, result.content
        prefix, virtual_env, path_entry, cwd = result.content.splitlines()
        assert Path(prefix).resolve() == dot_venv.resolve()
        assert Path(virtual_env).resolve() == dot_venv.resolve()
        assert Path(path_entry).resolve() == python_env.venv_python(dot_venv).parent.resolve()
        assert Path(cwd).resolve() == project.resolve()
    finally:
        repls.stop_all()

    # A broken .venv is not a usable project environment; try venv next.
    (dot_venv / "pyvenv.cfg").unlink()
    assert python_env.ensure_python_environment(
        "project-1", project, is_scratch=False,
    ) == python_env.venv_python(other_venv)
    assert dot_venv.is_dir(), "never repair/delete a user-supplied venv"


def test_managed_project_venv_created_on_first_call_and_reused(tmp_path, monkeypatch):
    project = tmp_path / "user-project"
    project.mkdir()
    (project / "data.txt").write_text("workspace file", encoding="utf-8")
    managed_root = tmp_path / "app-managed-envs"
    monkeypatch.setattr(python_env, "HARNESS_PYTHON_ENV_ROOT", managed_root)
    target = python_env.managed_venv_dir("agent-1", project, is_scratch=False)

    assert not managed_root.exists(), "selecting a workspace must not eagerly make a venv"

    registry, repls = _setup()
    try:
        result = _run(registry, _context(project, "agent-1"), (
            "import pip, sys; print(sys.prefix); "
            "print(sys.prefix != sys.base_prefix); "
            "print(open('data.txt').read())"
        ))
        assert not result.is_error, result.content
        assert result.content.splitlines() == [
            str(target), "True", "workspace file",
        ]
        assert python_env.venv_python(target).is_file()
        assert not (project / ".venv").exists() and not (project / "venv").exists()
        # Treat a file in site-packages as an installed, persistent package.
        installed = _run(registry, _context(project, "agent-1"), (
            "import pathlib, sysconfig; "
            "pathlib.Path(sysconfig.get_path('purelib'), 'graphlink_env_marker.py')"
            ".write_text('VALUE = 73\\n', encoding='utf-8')"
        ))
        assert not installed.is_error, installed.content
    finally:
        repls.stop_all()

    # The agent may be stopped/restarted without losing its dependencies.
    other_registry, other_repls = _setup()
    try:
        recovered = _run(other_registry, _context(project, "agent-1"), (
            "import graphlink_env_marker; print(graphlink_env_marker.VALUE)"
        ))
        assert recovered.content.strip() == "73", recovered.content
    finally:
        other_repls.stop_all()

    # Node deletion removes the app-managed bucket, not any project file.
    remove_scratch_dir_for_id(managed_root, "agent-1")
    assert not target.exists()
    assert (project / "data.txt").read_text(encoding="utf-8") == "workspace file"


def test_scratch_workspace_venv_is_inside_the_scratch_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "HARNESS_WORKSPACE_ROOT", tmp_path / "scratch-roots")
    monkeypatch.setattr(python_env, "HARNESS_PYTHON_ENV_ROOT", tmp_path / "project-envs")
    scratch = workspace.ensure_workspace("scratch-1")
    target = scratch / ".graphlink-python-venv"

    registry, repls = _setup()
    try:
        result = _run(registry, _context(scratch, "scratch-1", is_scratch=True), (
            "import pip, sys; print(sys.prefix); print(sys.prefix != sys.base_prefix)"
        ))
        assert not result.is_error, result.content
        assert result.content.splitlines() == [str(target), "True"]
        assert python_env.venv_python(target).is_file()
        assert not (tmp_path / "project-envs").exists()
    finally:
        repls.stop_all()


def test_rebinding_project_changes_venv_as_well_as_interpreter_state(tmp_path, monkeypatch):
    monkeypatch.setattr(python_env, "HARNESS_PYTHON_ENV_ROOT", tmp_path / "envs")
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    first_env = python_env.managed_venv_dir("same-agent", first, is_scratch=False)
    second_env = python_env.managed_venv_dir("same-agent", second, is_scratch=False)
    assert first_env != second_env

    registry, repls = _setup()
    try:
        first_result = _run(
            registry, _context(first, "same-agent"),
            "import sys; previous_project_value = 42; print(sys.prefix)",
        )
        second_result = _run(
            registry, _context(second, "same-agent"),
            "import sys; print(sys.prefix); print('previous_project_value' in globals())",
        )
        assert first_result.content.strip() == str(first_env)
        assert second_result.content.splitlines() == [str(second_env), "False"]
        assert first_env.is_dir() and second_env.is_dir()
    finally:
        repls.stop_all()


def test_adding_project_venv_switches_interpreter_on_next_execution(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(python_env, "HARNESS_PYTHON_ENV_ROOT", tmp_path / "envs")
    registry, repls = _setup()
    ctx = _context(project, "agent-switch")
    try:
        initial = _run(registry, ctx, "import sys; old_state = 10; print(sys.prefix)")
        assert not initial.is_error, initial.content

        dot_venv = project / ".venv"
        venv.EnvBuilder(with_pip=False).create(dot_venv)
        changed = _run(registry, ctx, (
            "import sys; print(sys.prefix); print('old_state' in globals())"
        ))
        assert changed.content.splitlines() == [str(dot_venv), "False"]
    finally:
        repls.stop_all()


def test_managed_env_refuses_symlinks_and_does_not_mutate_target(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(python_env, "HARNESS_PYTHON_ENV_ROOT", tmp_path / "envs")
    target = python_env.managed_venv_dir("agent-symlink", project, is_scratch=False)
    target.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "safe.txt").write_text("untouched", encoding="utf-8")
    try:
        target.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks require platform privileges")
    with pytest.raises(python_env.PythonEnvironmentError, match="symlink"):
        python_env.ensure_python_environment("agent-symlink", project, is_scratch=False)
    assert (outside / "safe.txt").read_text(encoding="utf-8") == "untouched"


def test_managed_venv_creation_uses_secret_free_env_and_reports_failures(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(python_env, "HARNESS_PYTHON_ENV_ROOT", tmp_path / "envs")
    monkeypatch.setenv("GRAPHLINK_FAKE_PROVIDER_SECRET", "must-not-leak")
    passed_envs = []

    class FailedProcess:
        returncode = 1
        stdout = io.StringIO("venv setup failed\n")

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    class Guard:
        def popen_kwargs(self):
            return {}

        def close(self):
            pass

    def fail_create(guard, argv, **kwargs):
        passed_envs.append(kwargs["env"])
        assert argv[:3] == [sys.executable, "-m", "venv"]
        return FailedProcess()

    monkeypatch.setattr(python_env, "create_execution_guard", Guard)
    monkeypatch.setattr(python_env, "spawn_guarded_subprocess", fail_create)
    monkeypatch.setattr(python_env, "assign_guarded_subprocess", lambda *args: None)
    with pytest.raises(python_env.PythonEnvironmentError, match="venv setup failed"):
        python_env.ensure_python_environment("failed-agent", project, is_scratch=False)
    assert len(passed_envs) == 1
    assert "GRAPHLINK_FAKE_PROVIDER_SECRET" not in passed_envs[0]
    assert not python_env.managed_venv_dir(
        "failed-agent", project, is_scratch=False,
    ).exists()


def test_stop_kills_and_reaps_an_inflight_first_use_venv_creation(tmp_path, monkeypatch):
    """to_thread may outlive the task, but its guarded subprocess must not."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(python_env, "HARNESS_PYTHON_ENV_ROOT", tmp_path / "envs")
    real_spawn = python_env.spawn_guarded_subprocess
    launched = threading.Event()
    cancel = threading.Event()
    processes = []
    errors = []

    def slow_bootstrap(guard, argv, **kwargs):
        assert argv[:3] == [sys.executable, "-m", "venv"]
        process = real_spawn(
            guard,
            [sys.executable, "-u", "-c", "import time; time.sleep(30)"],
            **kwargs,
        )
        processes.append(process)
        launched.set()
        return process

    monkeypatch.setattr(python_env, "spawn_guarded_subprocess", slow_bootstrap)

    def create():
        try:
            python_env.ensure_python_environment(
                "stop-bootstrap", project, is_scratch=False, cancel_event=cancel,
            )
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=create, daemon=True)
    worker.start()
    try:
        assert launched.wait(10), "the venv child must be running when Stop occurs"
        assert processes[0].poll() is None
        cancel.set()
        worker.join(timeout=8)
        assert not worker.is_alive(), "Stop must interrupt environment creation"
        assert len(errors) == 1 and isinstance(errors[0], RequestCancelledError)
        assert processes[0].poll() is not None, "Stop must reap the child process"
        assert not python_env.managed_venv_dir(
            "stop-bootstrap", project, is_scratch=False,
        ).exists()
    finally:
        cancel.set()
        worker.join(timeout=8)
