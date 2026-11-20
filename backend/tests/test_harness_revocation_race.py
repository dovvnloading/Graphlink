"""Deferred revocation must not retire processes from a renewed folder grant."""

from __future__ import annotations

import asyncio

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
