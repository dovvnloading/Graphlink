"""Deferred revocation must not retire processes from a renewed folder grant."""

from __future__ import annotations

import asyncio

import pytest

from backend.agent_dispatch.harness import HarnessDispatchOps


class _Settings:
    def __init__(self):
        self.trusted = []

    def get_harness_trusted_dirs(self):
        return list(self.trusted)


class _Processes:
    def __init__(self, root):
        self.by_workspace = {"old-workspace": root}
        self.stopped = []

    def stop_root(self, root, *, excluding=None):
        protected = excluding or set()
        for workspace_id, bound in list(self.by_workspace.items()):
            if bound == root and workspace_id not in protected:
                self.stopped.append(workspace_id)
                self.by_workspace.pop(workspace_id)


def test_deferred_revocation_does_not_stop_new_shells_or_repls_after_regrant(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    root = root.resolve()
    settings = _Settings()
    shells, repls = _Processes(root), _Processes(root)
    dispatcher = HarnessDispatchOps()
    dispatcher._settings_manager = settings
    dispatcher._harness_shell_sessions = shells
    dispatcher._harness_python_repls = repls
    dispatcher._harness_active_workspaces = {"old-run": "old-workspace"}
    dispatcher._harness_revoked_roots = {root}
    dispatcher._harness_machine_snapshots = {
        "old-workspace": (str(root), "auto", "host", "old snapshot"),
    }

    # Revocation cannot retire processes of the old still-unwinding worker.
    asyncio.run(dispatcher._retire_revoked_harness_roots())
    assert root in dispatcher._harness_revoked_roots
    assert shells.by_workspace == {"old-workspace": root}
    assert repls.by_workspace == {"old-workspace": root}

    # The Settings grant is renewed while the old worker is still active.
    # Even without a picker notification, cleanup must re-check actual trust.
    settings.trusted.append(str(root))
    shells.by_workspace["new-workspace"] = root
    repls.by_workspace["new-workspace"] = root
    dispatcher._harness_machine_snapshots["new-workspace"] = (
        str(root), "auto", "host", "new snapshot",
    )
    dispatcher._harness_active_workspaces.clear()
    asyncio.run(dispatcher._retire_revoked_harness_roots())

    assert not dispatcher._harness_revoked_roots
    assert shells.stopped == []
    assert repls.stopped == []
    assert "new-workspace" in shells.by_workspace
    assert "new-workspace" in repls.by_workspace
    assert "new-workspace" in dispatcher._harness_machine_snapshots

    # A fresh revocation of the new grant still cleans up idle resources.
    settings.trusted.clear()
    asyncio.run(dispatcher.retire_revoked_harness_root(str(root)))
    assert shells.stopped == ["old-workspace", "new-workspace"]
    assert repls.stopped == ["old-workspace", "new-workspace"]
    assert not dispatcher._harness_revoked_roots


def test_picker_regrant_cancels_old_pending_cleanup(tmp_path):
    root = (tmp_path / "project").resolve()
    dispatcher = HarnessDispatchOps()
    dispatcher._harness_revoked_roots = {root}

    dispatcher.harness_root_granted(str(root))

    assert dispatcher._harness_revoked_roots == set()


def test_inflight_stop_cannot_retire_newly_registered_sessions_after_regrant(
    tmp_path, monkeypatch,
):
    """A settings-side regrant can appear before the picker callback runs.

    New shell sessions and Python REPLs must not enter a registry while a
    revocation is blocked inside stop_root with a stale trust snapshot.
    """
    import concurrent.futures
    import threading

    from backend.harness import shell_sessions, tools_python

    root = (tmp_path / "project").resolve()
    root.mkdir()
    settings = _Settings()
    dispatcher = HarnessDispatchOps()
    dispatcher._settings_manager = settings
    dispatcher._harness_active_workspaces = {}
    dispatcher._harness_revoked_roots = {root}
    shells = dispatcher.harness_shell_sessions()
    repls = dispatcher.harness_python_repls()

    entered = threading.Event()
    release = threading.Event()
    shell_started = threading.Event()
    python_started = threading.Event()
    errors = []

    class FakeShellSession:
        def __init__(self, *_args, **_kwargs):
            self.stopped = False
            shell_started.set()

        @property
        def running(self):
            return not self.stopped

        def stop(self):
            self.stopped = True

    class FakePythonREPL:
        def __init__(self, *, python_executable=None, **_kwargs):
            from pathlib import Path
            import sys
            self.python_executable = python_executable or Path(sys.executable)
            self.stopped = False
            python_started.set()

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(shell_sessions, "ShellSession", FakeShellSession)
    monkeypatch.setattr(tools_python, "PythonREPL", FakePythonREPL)
    original_stop = shells.stop_root

    def stop_after_pause(root_path, *, excluding=None):
        entered.set()
        assert release.wait(5), "test did not release blocked revocation"
        return original_stop(root_path, excluding=excluding)

    monkeypatch.setattr(shells, "stop_root", stop_after_pause)

    def retire():
        try:
            asyncio.run(dispatcher._retire_revoked_harness_roots())
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=retire)
    worker.start()
    try:
        assert entered.wait(5), "revocation did not reach stop_root"
        # Simulates another settings writer publishing the grant before
        # harness_root_granted can acquire the revocation lock.
        settings.trusted.append(str(root))
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            shell_future = pool.submit(shells.start, "new-shell", "dev", "echo ok", root)
            python_future = pool.submit(repls.get, "new-python", root, manage_cwd=False)
            assert not shell_started.wait(0.1)
            assert not python_started.wait(0.1)
            release.set()
            shell = shell_future.result(timeout=5)
            repl = python_future.result(timeout=5)
    finally:
        release.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert not errors, errors
    assert shell_started.is_set() and python_started.is_set()
    assert not shell.stopped
    assert not repl.stopped
    assert shells.get("new-shell", "dev", expected_root=root) is shell
    assert repls.get("new-python", root, manage_cwd=False) is repl

@pytest.mark.parametrize("invalidator", ["revoke", "graph_reset"])
def test_late_session_approval_cannot_rearm_revoked_active_run(tmp_path, invalidator):
    """A parked approval resolving after revocation cannot revive its old set."""
    from backend.providers.base import ToolCall, ToolSpec
    from backend.tools import FS_WRITE, RunContext, ToolRegistry, ToolResult

    root = (tmp_path / "trusted").resolve()
    root.mkdir()
    dispatcher = HarnessDispatchOps()
    grants = dispatcher.bind_harness_session_grants("agent", root)
    # This simulates an in-flight harness task with a frozen root.
    dispatcher._harness_active_workspaces = {"run": "agent"}
    registry = ToolRegistry()
    executed = []

    async def handler(call, _ctx):
        executed.append(call.arguments["path"])
        return ToolResult(content="wrote")

    registry.register(
        ToolSpec(name="fs.write", description="test", input_schema={"type": "object"}),
        handler, scopes={FS_WRITE}, approval="once",
    )

    async def run():
        pending = asyncio.get_running_loop().create_future()
        first_prompted = asyncio.Event()
        prompts = []

        async def request_approval(call):
            prompts.append(call.arguments["path"])
            if len(prompts) == 1:
                first_prompted.set()
                return await pending
            return True

        ctx = RunContext(
            granted_scopes=frozenset({FS_WRITE}),
            request_approval=request_approval, session_grants=grants,
        )
        ctx.harness_workspace_dir = root
        ctx.harness_node_id = "agent"

        first = asyncio.create_task(registry.invoke(
            ToolCall(id="one", name="fs.write", arguments={"path": "one.txt"}), ctx,
        ))
        await first_prompted.wait()
        if invalidator == "revoke":
            await dispatcher.retire_revoked_harness_root(str(root))
        else:
            dispatcher.clear_harness_session_grants()
        assert not grants
        assert dispatcher.harness_session_grants("agent") is not grants

        pending.set_result("session")  # stale approval for the already-running task
        assert not (await first).is_error
        assert "fs.write" not in grants

        second = await registry.invoke(
            ToolCall(id="two", name="fs.write", arguments={"path": "two.txt"}), ctx,
        )
        assert not second.is_error
        assert prompts == ["one.txt", "two.txt"], "the revoked run must ask again"
        assert executed == ["one.txt", "two.txt"]
        assert "fs.write" not in grants

    asyncio.run(run())
