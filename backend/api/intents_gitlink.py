"""ADR-002 stage 2.6: Gitlink node - repo browse, snapshot import, context
build, change-set run/apply.

Relocated VERBATIM from backend/canvas.py's former register_canvas
(closures at lines 732-885; registration calls from the former tail block
at lines 887-903) - pure code motion, no behavior change.

Reuses the existing generic pending_request_id field as the busy/in-flight
marker for every Gitlink action (list repos, load tree, import, build
context, run, apply) - this is exactly that field's documented purpose,
and critically it is what makes the fingerprint-recheck race-proof: a Run
cannot start while an Apply request_id occupies this node's slot, and vice
versa.
"""

from __future__ import annotations

import os

from backend import native_dialogs
from backend.agents import _GITLINK_RUN_CLAIM_PLACEHOLDER, AgentDispatcher
from backend.api._shared import make_publish_scene
from backend.domain.graph import SceneDocument
from backend.domain.model import SceneError
from backend.events import SessionBus
from backend.notifications import NotificationState


def register_gitlink_intents(
    bus: SessionBus,
    document: SceneDocument,
    notifications: NotificationState,
    agent_dispatcher: AgentDispatcher,
) -> None:
    publish_scene = make_publish_scene(bus)

    async def fetch_gitlink_repositories(node_id):
        node = document.nodes.get(node_id)
        if node is None or node.pending_request_id:
            notifications.show("Gitlink is busy for this node.", "info")
            await bus.publish("notification")
            return []
        return await agent_dispatcher.fetch_gitlink_repositories(
            bus=bus, notifications_state=notifications, node=node,
        )

    async def load_gitlink_repo_tree(node_id, repo, branch):
        node = document.nodes.get(node_id)
        if node is None or node.pending_request_id:
            notifications.show("Gitlink is busy for this node.", "info")
            await bus.publish("notification")
            return None
        result = await agent_dispatcher.load_gitlink_repo_tree(
            bus=bus, notifications_state=notifications, node=node, repo=repo, branch=branch,
        )
        if result is not None:
            document.store_gitlink_repo_tree(node_id, *result)
            await publish_scene()
        return node_id

    async def set_gitlink_local_root(node_id, local_root):
        document.record_command(
            "setGitlinkLocalRoot", "user",
            lambda: document.set_gitlink_local_root(node_id, local_root),
            node_ids=[node_id],
        )
        await publish_scene()

    async def pick_gitlink_local_root(node_id):
        # R8a (UI/UX audit POLISH finding #1): the field's own label used to
        # say "no browse - deferred", dating from before native_dialogs.py
        # existed. pick_folder is already generic and already used by
        # Settings' Ollama/Llama.cpp Scan Folder buttons - this just wires
        # the same primitive to Gitlink's own local-root field instead of
        # requiring the user to type a path by hand. A cancelled dialog is
        # a quiet no-op, matching every other pick_folder call site.
        node = document.nodes.get(node_id)
        if node is None or node.kind != "gitlink":
            return
        directory = node.state.gitlink_local_root or os.path.expanduser("~")
        try:
            folder = await native_dialogs.pick_folder(directory=directory)
        except Exception as exc:  # noqa: BLE001 - a local folder path, not a credential
            notifications.show(f"Could not open the folder picker: {exc}", "error")
            await bus.publish("notification")
            return
        if not folder:
            return
        document.record_command(
            "setGitlinkLocalRoot", "user",
            lambda: document.set_gitlink_local_root(node_id, folder),
            node_ids=[node_id],
        )
        await publish_scene()

    async def import_gitlink_snapshot(node_id, repo, branch):
        node = document.nodes.get(node_id)
        if node is None or node.kind != "gitlink":
            notifications.show("This node no longer exists.", "warning")
            await bus.publish("notification")
            return None
        if node.pending_request_id:
            notifications.show("Gitlink is busy for this node.", "info")
            await bus.publish("notification")
            return None
        result = await agent_dispatcher.import_gitlink_snapshot(
            bus=bus, notifications_state=notifications, node=node, repo=repo, branch=branch,
            local_root_hint=node.state.gitlink_local_root, imported_root_hint=node.state.gitlink_imported_root,
        )
        if result is not None:
            document.store_gitlink_snapshot_root(node_id, *result)
            await publish_scene()
        return node_id

    async def build_gitlink_context(node_id, scope_mode, selected_paths):
        node = document.nodes.get(node_id)
        if node is None or node.kind != "gitlink":
            notifications.show("This node no longer exists.", "warning")
            await bus.publish("notification")
            return None
        if node.pending_request_id:
            notifications.show("Gitlink is busy for this node.", "info")
            await bus.publish("notification")
            return None
        result = await agent_dispatcher.build_gitlink_context(
            bus=bus, notifications_state=notifications, node=node,
            scope_mode=scope_mode, selected_paths=list(selected_paths),
        )
        if result is not None:
            document.store_gitlink_context(node_id, scope_mode=scope_mode,
                                            selected_paths=selected_paths, **result)
            await publish_scene()
        return node_id

    async def fetch_gitlink_context(node_id):
        return document.fetch_gitlink_context_xml(node_id)

    async def run_gitlink_change_set(node_id, task_prompt):
        node_for_check = document.nodes.get(node_id)
        if node_for_check is not None and node_for_check.pending_request_id:
            notifications.show("Gitlink is already busy for this node.", "info")
            await bus.publish("notification")
            return None
        # R5.3 post-review FIX 4(b): claim the busy slot with a placeholder
        # SYNCHRONOUSLY, in the same stretch as the busy pre-check just
        # above - before document.start_gitlink_run or any await - so a
        # second concurrent call for this SAME node_id can never pass that
        # same pre-check during the `await publish_scene()` gap below.
        # agent_dispatcher.start_gitlink_run (the ONLY caller of this dict
        # entry for this node_id, invoked just below) recognizes this exact
        # placeholder and overwrites it with the real request_id, still
        # synchronously - see that method's own docstring.
        if node_for_check is not None:
            node_for_check.pending_request_id = _GITLINK_RUN_CLAIM_PLACEHOLDER
        try:
            node = document.start_gitlink_run(node_id, task_prompt)
        except SceneError:
            # Node deleted (or wrong-kind) concurrently with the claim above -
            # the placeholder must not linger on a node this handler is
            # about to give up on.
            if node_for_check is not None:
                node_for_check.pending_request_id = None
            notifications.show("This node no longer exists.", "warning")
            await bus.publish("notification")
            return None
        await publish_scene()

        def _on_success(proposal_markdown, pending_changes, preview_text, fingerprint, local_root):
            document.complete_gitlink_run(node_id, proposal_markdown, pending_changes,
                                           preview_text, fingerprint, local_root)

        def _on_failure(message):
            document.fail_gitlink_run(node_id, message)

        await agent_dispatcher.start_gitlink_run(
            bus=bus, notifications_state=notifications, node=node, node_id=node_id,
            repo=node.state.gitlink_repo, branch=node.state.gitlink_branch,
            scope_mode=node.state.gitlink_scope_mode, task_prompt=task_prompt,
            context_xml=node.state.gitlink_context_xml, context_summary=node.state.gitlink_context_summary,
            local_root=node.state.gitlink_local_root,
            on_success=_on_success, on_failure=_on_failure,
        )
        return node_id

    async def cancel_gitlink_request(request_id):
        agent_dispatcher.cancel_gitlink(request_id)

    async def apply_gitlink_changes(node_id, fingerprint):
        node = document.nodes.get(node_id)
        if node is None or node.kind != "gitlink":
            notifications.show("This node no longer exists.", "warning")
            await bus.publish("notification")
            return None

        def _on_success(written_files):
            document.complete_gitlink_apply(node_id, written_files)

        def _on_failure(message):
            document.fail_gitlink_apply(node_id, message)

        await agent_dispatcher.start_gitlink_apply(
            bus=bus, notifications_state=notifications, node=node, node_id=node_id,
            client_fingerprint=fingerprint, local_root=node.state.gitlink_local_root,
            on_success=_on_success, on_failure=_on_failure,
        )
        return node_id

    bus.register_intent("scene", "fetchGitlinkRepositories", fetch_gitlink_repositories)
    bus.register_intent("scene", "loadGitlinkRepoTree", load_gitlink_repo_tree)
    bus.register_intent("scene", "setGitlinkLocalRoot", set_gitlink_local_root)
    bus.register_intent("scene", "pickGitlinkLocalRoot", pick_gitlink_local_root)
    bus.register_intent("scene", "importGitlinkSnapshot", import_gitlink_snapshot)
    bus.register_intent("scene", "buildGitlinkContext", build_gitlink_context)
    bus.register_intent("scene", "fetchGitlinkContext", fetch_gitlink_context)
    bus.register_intent("scene", "runGitlinkChangeSet", run_gitlink_change_set)
    bus.register_intent("scene", "cancelGitlinkRequest", cancel_gitlink_request)
    # CRITICAL, load-bearing property: applyGitlinkChanges takes ONLY
    # (node_id, fingerprint) as WS intent arguments - there must be NO
    # changes/pending_changes parameter anywhere in this signature or the
    # dispatcher method it calls. This closes the most obvious
    # content-injection bypass by construction, not by a runtime check: the
    # only content that ever reaches apply_change_set is server-held,
    # already-normalized node.state.gitlink_pending_changes.
    bus.register_intent("scene", "applyGitlinkChanges", apply_gitlink_changes)
