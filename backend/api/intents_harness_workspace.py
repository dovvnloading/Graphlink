"""Harness workspace folder grants, bindings, and file-browsing intents.

Split from the run lifecycle and approval handlers in intents_harness.py.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from backend import native_dialogs
from backend.api._settings_shared import run_locked
from backend.api._shared import make_publish_scene
from backend.domain.graph import SceneDocument
from backend.domain.node_states import HarnessState
from backend.events import SessionBus
from backend.harness.workspace import WorkspaceError, bound_root
from backend.harness.workspace_files import list_workspace_files, read_workspace_file, reveal_workspace
from backend.notifications import NotificationState


def register_harness_workspace_intents(
    bus: SessionBus,
    document: SceneDocument,
    notifications: NotificationState,
    agent_dispatcher,
) -> None:
    publish_scene = make_publish_scene(bus)

    async def _pick_and_grant(start_in: str) -> "str | None":
        """Open the folder picker and, on a real choice, grant that folder.

        The pick IS the consent (the gitlink local-root precedent), so the
        two steps are one operation and live in one place - both the
        per-node binding and the launcher below call this rather than each
        implementing "picker, then trust list" their own way.
        """
        try:
            folder = await native_dialogs.pick_folder(directory=start_in)
        except Exception as exc:  # noqa: BLE001 - a local folder path, not a credential
            notifications.show(f"Could not open the folder picker: {exc}", "error")
            await bus.publish("notification")
            return None
        if not folder:
            return None
        resolved = str(Path(folder).resolve())
        settings = agent_dispatcher._settings_manager
        if settings is not None:
            # The grant write goes through the same locked writer every
            # other settings mutation uses (save_recipe's precedent), so it
            # cannot land mid-write against a concurrent settings save.
            await asyncio.to_thread(run_locked, lambda: settings.add_harness_trusted_dir(resolved))
            # Serialize the re-grant with any deferred process retirement
            # before the picker returns and the renewed root can start work.
            on_grant = getattr(agent_dispatcher, "harness_root_granted", None)
            if on_grant is not None:
                await asyncio.to_thread(on_grant, resolved)
        # A Settings dialog may already be subscribed. Publish the newly
        # recorded grant so its trusted-folder list cannot stay stale until
        # some unrelated settings change.
        if bus.has_topic("app-settings"):
            await bus.publish("app-settings")
        return resolved

    async def pick_launch_workspace():
        """The launcher's folder choice, before any node exists.

        Same picker and same grant as pick_workspace below; it just has no
        node to write to, so it ANSWERS with the folder and lets `start`
        carry it in. Split from pick_workspace rather than made to accept a
        null node id: "which folder should the new agent use" and "rebind
        this existing agent" are different operations, and one of them
        must not silently become the other on a stale node id.
        """
        return await _pick_and_grant(os.path.expanduser("~"))

    async def pick_workspace(node_id):
        """Bind this agent node to a real project directory. The pick IS
        the consent (the gitlink local-root precedent): on success we add
        the folder to the settings trust list AND set it as this node's
        requested workspace. B-classified run-config, not document content -
        a workspace binding is a run setting like the budget, and it also
        writes the settings store, which is never an undo target."""
        node = document.nodes.get(node_id)
        if node is None or not isinstance(node.state, HarnessState):
            return
        directory = node.state.harness_workspace_path or os.path.expanduser("~")
        resolved = await _pick_and_grant(directory)
        if resolved is None:
            return
        node.state.harness_workspace_path = resolved
        # The previous run's bound root says nothing about THIS binding -
        # left in place it reads as "the grant did not apply", which is
        # exactly the warning the card shows for a refused folder.
        node.state.harness_workspace_active = ""
        await publish_scene()

    async def use_scratch(node_id):
        """Unbind from the user directory, reverting to the managed scratch
        workspace. Leaves the trust grant in place (revoking trust is a
        settings concern, not a per-node one)."""
        node = document.nodes.get(node_id)
        if node is None or not isinstance(node.state, HarnessState):
            return
        node.state.harness_workspace_path = ""
        node.state.harness_workspace_active = ""
        await publish_scene()

    def _workspace_for_node(node_id: str):
        """Resolve the current local grant on EACH request. A serialized node
        path (or its last-run active path) never conveys permission by itself.
        This is also the run's scratch fallback for an untrusted user folder.
        """
        node = document.nodes.get(node_id)
        if node is None or not isinstance(node.state, HarnessState):
            raise WorkspaceError("This agent node no longer exists.")
        return bound_root(
            node.state.harness_workspace_id,
            node.state.harness_workspace_path,
            settings_manager=agent_dispatcher._settings_manager,
        )

    async def list_files(node_id: str, relative_path: str = ""):
        root, user_dir = _workspace_for_node(node_id)
        listing = await asyncio.to_thread(list_workspace_files, root, relative_path)
        return {"root": str(root), "isScratch": not user_dir, **listing}

    async def read_file(node_id: str, relative_path: str):
        root, _ = _workspace_for_node(node_id)
        return await asyncio.to_thread(read_workspace_file, root, relative_path)

    async def reveal_folder(node_id: str):
        root, _ = _workspace_for_node(node_id)
        try:
            await asyncio.to_thread(reveal_workspace, root)
        except OSError as exc:
            notifications.show(f"Could not open the workspace folder: {exc}", "error")
            await bus.publish("notification")

    bus.register_intent("harness", "pickLaunchWorkspace", pick_launch_workspace)
    bus.register_intent("harness", "pickWorkspace", pick_workspace)
    bus.register_intent("harness", "useScratch", use_scratch)
    bus.register_intent("harness", "listWorkspaceFiles", list_files)
    bus.register_intent("harness", "readWorkspaceFile", read_file)
    bus.register_intent("harness", "revealWorkspace", reveal_folder)
