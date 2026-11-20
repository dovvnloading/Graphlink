"""Isolated shell profile: no host fallback, restricted CLI, session binding."""
from __future__ import annotations

import io
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from backend.harness.loop import _offered_harness_specs, _retire_host_repl_for_isolated
from backend.harness.shell_isolation import (
    CONTAINER_IMAGE, IsolationUnavailableError, available_engine,
    container_invocation, normalize_execution_profile, network_request_error,
)
from backend.harness.shell_sessions import ShellSessionRegistry
from backend.providers.base import ToolSpec
from backend.tools import CODE_EXECUTE, GRAPH_READ, NET_FETCH, ToolRegistry, ToolResult


def test_profile_validation_defaults_old_or_tampered_graphs_to_host():
    assert normalize_execution_profile(None) == "host"
    assert normalize_execution_profile("bogus") == "host"
    assert normalize_execution_profile("isolated") == "isolated"


def test_engine_detection_uses_sanitized_path_and_falls_back_to_podman():
    with patch("backend.harness.shell_isolation.shutil.which",
               side_effect=lambda name, *, path: "/trusted/podman" if name == "podman" and path == "/trusted" else None):
        assert available_engine({"PATH": "/trusted"}) == "podman"


def test_isolated_argv_enforces_no_network_and_resource_caps(tmp_path):
    with patch("backend.harness.shell_isolation.shutil.which", return_value="/usr/bin/docker"):
        argv, engine, name = container_invocation("echo hi; touch pwned", tmp_path, env={"PATH": "/usr/bin"})
    assert engine == "docker"
    assert name.startswith("graphlink-agent-")
    assert argv[:2] == ["/usr/bin/docker", "run"]
    for flag in ("--rm", "--interactive", "--pull=never", "--network=none",
                 "--cpus=1", "--memory=512m", "--memory-swap=512m",
                 "--pids-limit=128", "--read-only", "--cap-drop=ALL",
                 "--security-opt=no-new-privileges"):
        assert flag in argv
    assert argv[argv.index("--mount") + 1] == f"type=bind,source={tmp_path.resolve()},target=/workspace"
    assert argv[-4:] == [CONTAINER_IMAGE, "/bin/sh", "-c", "echo hi; touch pwned"]
    assert "echo hi; touch pwned" not in argv[:-1]
    assert not any(item.startswith("--network=host") for item in argv)


def test_network_enabled_container_preserves_isolation_and_defaults_off(tmp_path):
    with patch("backend.harness.shell_isolation.shutil.which", return_value="/usr/bin/docker"):
        enabled, _, _ = container_invocation("echo hi", tmp_path, env={"PATH": "/usr/bin"}, network=True)
        offline, _, _ = container_invocation("echo hi", tmp_path, env={"PATH": "/usr/bin"})
    assert "--network=bridge" in enabled
    assert "--network=none" not in enabled
    assert "--network=none" in offline
    assert "--network=bridge" not in offline
    for flag in ("--pull=never", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges"):
        assert flag in enabled


@pytest.mark.parametrize("args,expected", [
    ({"command": "curl https://example.org", "network": True}, "reason"),
    ({"command": "curl https://example.org", "network": True, "network_reason": "   "}, "reason"),
    ({"command": "curl https://example.org", "network": True, "network_reason": "x" * 1001}, "reason"),
    ({"command": "echo hi", "network": "true"}, "boolean"),
])
def test_network_request_requires_explicit_boolean_and_reason(args, expected):
    assert expected in network_request_error(args)


def test_network_request_allows_offline_or_explained_access():
    assert network_request_error({"command": "ls"}) is None
    assert network_request_error({"command": "ls", "network": False}) is None
    assert network_request_error({"command": "curl example.org", "network": True, "network_reason": "Fetch build dependencies"}) is None


def test_missing_engine_fails_closed_before_host_spawn(tmp_path):
    from backend.harness.tools_shell import _run_command

    with patch("backend.harness.shell_isolation.shutil.which", return_value=None), \
         patch("backend.harness.tools_shell.spawn_guarded_subprocess") as launch:
        with pytest.raises(IsolationUnavailableError, match="Install Docker"):
            _run_command("echo would-run-on-host", tmp_path, None, profile="isolated")
        launch.assert_not_called()


def test_shell_exec_uses_container_argv_not_the_host_shell(tmp_path):
    from backend.harness.tools_shell import _run_command

    guard = Mock()
    guard.popen_kwargs.return_value = {}
    process = Mock()
    process.stdout = io.StringIO("hello inside\n")
    process.poll.return_value = 0
    process.returncode = 0
    launched = []

    def fake_spawn(_guard, argv, **kwargs):
        launched.append((argv, kwargs))
        return process

    with patch("backend.harness.shell_isolation.shutil.which", return_value="/usr/bin/docker"), \
         patch("backend.harness.tools_shell.create_execution_guard", return_value=guard), \
         patch("backend.harness.tools_shell.spawn_guarded_subprocess", side_effect=fake_spawn), \
         patch("backend.harness.tools_shell.assign_guarded_subprocess"), \
         patch("backend.harness.tools_shell.cleanup_container") as cleanup:
        output, code, ended = _run_command("echo hello", tmp_path, None, profile="isolated")
    assert output == "hello inside\n" and code == 0 and ended == "ok"
    assert launched[0][0][:2] == ["/usr/bin/docker", "run"]
    assert launched[0][1]["shell"] is False
    assert launched[0][0][-1] == "echo hello"
    guard.close.assert_called_once()
    cleanup.assert_called_once()


def test_podman_keeps_host_user_id_for_bind_mount(tmp_path):
    with patch("backend.harness.shell_isolation.shutil.which",
               side_effect=lambda name, *, path: "/usr/bin/podman" if name == "podman" else None):
        argv, engine, _ = container_invocation("pwd", tmp_path, env={"PATH": "/usr/bin"})
    assert engine == "podman"
    if os.name == "posix":
        assert "--userns=keep-id" in argv


def test_shell_session_selects_isolated_backend_and_pipes_stdin(tmp_path):
    class FakeProcess:
        def __init__(self):
            self.stdin = io.StringIO()
            self.stdout = io.StringIO()
            self.stderr = None
            self.exit_code = None
        def poll(self):
            return self.exit_code
        def kill(self):
            self.exit_code = -9
        def wait(self, timeout=None):
            return self.exit_code

    class StubBackend:
        def __init__(self):
            self.commands = []
            self.stopped = []
        def spawn(self, command, cwd):
            self.commands.append((command, cwd))
            return FakeProcess()
        def teardown(self, handle):
            self.stopped.append(handle)

    host, isolated = StubBackend(), StubBackend()
    registry = ShellSessionRegistry(backend=host, isolated_backend=isolated)
    registry.bind_root("ws", tmp_path, "isolated")
    session = registry.start("ws", "interactive", "cat", tmp_path, profile="isolated")
    try:
        session.write("hello")
        assert "hello\n" in session._process.stdin.getvalue()
        assert isolated.commands == [("cat", tmp_path)]
        assert not host.commands
    finally:
        registry.stop_all()
    assert len(isolated.stopped) == 1


def test_rebinding_same_directory_to_isolated_retires_host_sessions(tmp_path):
    class Existing:
        def __init__(self):
            self.stopped = False
        def stop(self):
            self.stopped = True

    registry = ShellSessionRegistry()
    session = Existing()
    registry.bind_root("ws", tmp_path, "host")
    registry._by_workspace["ws"] = {"host-session": session}
    registry.bind_root("ws", tmp_path, "isolated")
    assert session.stopped
    assert registry.names("ws") == []
    assert registry._profiles["ws"] == "isolated"


def test_ambiguous_mount_path_fails_closed_before_container_launch(tmp_path):
    # Docker/Podman --mount parses commas inside a single argv element.
    for folder in ("workspace,with-comma", "workspace,source=/other"):
        with patch("backend.harness.shell_isolation.shutil.which", return_value="/usr/bin/docker"):
            with pytest.raises(IsolationUnavailableError, match="commas or newlines"):
                container_invocation("echo safe", tmp_path / folder, env={"PATH": "/usr/bin"})


def test_extra_container_mounts_reject_commas_and_newlines(tmp_path):
    # App-owned mounts must not bypass the workspace's --mount validation.
    with patch("backend.harness.shell_isolation.shutil.which",
               return_value="/usr/bin/docker"):
        for folder in ("managed,venv", "managed\nvenv"):
            with pytest.raises(IsolationUnavailableError, match="commas or newlines"):
                container_invocation(
                    "echo safe", tmp_path, env={"PATH": "/usr/bin"},
                    extra_mounts=((tmp_path / folder, "/graphlink-python-env/test"),),
                )


def test_isolated_tool_catalog_includes_container_python_and_scoped_web():
    registry = ToolRegistry()

    async def harmless(_call, _ctx):
        return ToolResult(content="ok")

    for name, scope in (
        ("shell.exec", CODE_EXECUTE),
        ("shell.session", CODE_EXECUTE),
        ("python.exec", CODE_EXECUTE),
        ("python.install", CODE_EXECUTE),
        ("web.search", NET_FETCH),
        ("web.fetch", NET_FETCH),
        ("other.network", NET_FETCH),
        ("run_node", GRAPH_READ),
        ("builder.complete_step", GRAPH_READ),
    ):
        registry.register(
            ToolSpec(name=name, description=name, input_schema={"type": "object"}),
            harmless, scopes={scope}, approval="auto",
        )

    host = {spec.name for spec in _offered_harness_specs(registry, "host")}
    isolated = {spec.name for spec in _offered_harness_specs(registry, "isolated")}
    assert host == {
        "shell.exec", "shell.session", "python.exec", "python.install",
        "web.search", "web.fetch", "run_node",
    }
    assert isolated == host  # Both Python tools execute in the container.


# Container Python no-host-fallback and approval coverage is in
# test_harness_python_isolation.py.


def test_isolated_transition_retires_existing_host_repl():
    repl = Mock()
    _retire_host_repl_for_isolated(SimpleNamespace(_harness_python_repls=repl), "workspace-1")
    repl.stop_workspace.assert_called_once_with("workspace-1")
    _retire_host_repl_for_isolated(SimpleNamespace(), "workspace-2")

