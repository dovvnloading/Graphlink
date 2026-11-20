"""PLAN-2026-08-24 H1: the "harness" topic's WS intents.

All three are B-classified run-lifecycle intents (start/steer/stop a run;
the CONTENT a run produces lives in the workspace transcript, outside the
undo domain entirely) - except the node CREATION inside `start`, which is
an ordinary Ctrl+Z-undoable command, the builder-plan precedent exactly.
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
from backend.harness.workspace import WorkspaceError, bound_root
from backend.harness.workspace_files import list_workspace_files, read_workspace_file, reveal_workspace
from backend.harness.shell_selection import VALID_SHELLS
from backend.harness.shell_isolation import VALID_EXECUTION_PROFILES, available_engine
from backend.events import SessionBus
from backend.notifications import NotificationState
from graphlink_cloud_providers import CLOUD_PROVIDERS
from graphlink_model_catalog import ModelRef
import graphlink_task_config as config

_MIN_TURNS = 1
_MAX_TURNS = 64
_DEFAULT_TURNS = 16
# One user message's own bound - the same wire-input-is-untrusted posture
# every other intent that accepts free text takes.
_MESSAGE_CAP = 20_000
# A filesystem path's own bound. Same posture as _MESSAGE_CAP: the launcher
# hands this across the wire, so it is untrusted length as well as untrusted
# content (the trust list is what settles the content, at run time).
_PATH_CAP = 4_000


def _place_harness_node(document: SceneDocument) -> tuple[float, float]:
    """Right of the current scene's real extent - the builder-plan
    placement exactly (no canvas anchor exists for a fresh agent either).
    See place_at_scene_right (backend/domain/layout.py)."""
    return document.place_at_scene_right("harness")


def register_harness_composer_intent(
    bus: SessionBus,
    document: SceneDocument,
    notifications: NotificationState,
    composer_document,
    *,
    start,
    send,
) -> None:
    """Register the composer entrypoint separately from harness run controls.

    The existing start/send handlers remain the single owners of run
    validation, agent creation, and dispatch for all harness callers.
    """

    async def composer_send(text, selected_node_id=None):
        """Route the composer to the selected agent, or create one if none is selected.

        The model is resolved from THIS session's live composer route rather
        than trusting a client-supplied model identifier. Every task uses the
        exact model shown in the composer at dispatch time; the regular
        launcher and per-card follow-up retain their existing defaults.
        """
        message = str(text or "").strip()[:_MESSAGE_CAP]
        if not message:
            notifications.show("Type a message for the agent first.", "info")
            await bus.publish("notification")
            return None

        route = composer_document.route() if composer_document is not None else {}
        model_id = str(route.get("modelId") or "").strip()
        mode = route.get("mode")
        if mode == "ollama":
            provider = config.LOCAL_PROVIDER_OLLAMA
        elif mode == "llama_cpp":
            provider = config.LOCAL_PROVIDER_LLAMACPP
        elif mode == "api" and route.get("provider") in CLOUD_PROVIDERS:
            provider = route["provider"]
        else:
            provider = ""
        if not provider or not model_id or not route.get("available", False):
            notifications.show("Choose an available composer model before starting an agent.", "warning")
            await bus.publish("notification")
            return None
        model_ref = ModelRef(provider=provider, model_id=model_id)

        # Selection is advisory: an absent, deleted, or non-agent node means
        # "new agent", never an attempt to send to the wrong kind of node.
        selected = document.nodes.get(selected_node_id) if isinstance(selected_node_id, str) else None
        if selected is not None and isinstance(selected.state, HarnessState):
            request_id = await send(selected.id, message, model_ref=model_ref)
            return selected.id if request_id is not None else None
        return await start(message, model_ref=model_ref)

    bus.register_intent("harness", "composerSend", composer_send)


def register_harness_intents(
    bus: SessionBus,
    document: SceneDocument,
    notifications: NotificationState,
    agent_dispatcher,
    composer_document=None,
) -> None:
    publish_scene = make_publish_scene(bus)

    async def start(goal, max_turns=None, workspace_path=None, *, model_ref=None):
        goal_text = str(goal or "").strip()[:_MESSAGE_CAP]
        if not goal_text:
            notifications.show("Give the agent a task first.", "info")
            await bus.publish("notification")
            return None
        # Avoid creating a stranded agent node when a task is already running.
        runs = getattr(agent_dispatcher, "_runs", None)
        if runs is not None and runs.is_busy("harness"):
            notifications.show("An agent run is already in progress.", "info")
            await bus.publish("notification")
            return None
        x, y = _place_harness_node(document)

        def clamp(value):
            try:
                return min(max(int(value), _MIN_TURNS), _MAX_TURNS)
            except (TypeError, ValueError):
                return _DEFAULT_TURNS

        node, command = document.record_command(
            "harnessCreate", "agent",
            lambda: document.add_harness_node(x, y, goal_text, max_turns=clamp(max_turns)),
        )
        # The launcher's folder choice, bound BEFORE the run starts so the
        # first task already works where the person meant it to - without
        # this they had to let a scratch run finish, rebind the node, and
        # re-send the same task. Wire input is a REQUEST, never a grant:
        # bound_root re-checks the trust list at run time and silently
        # falls back to scratch, so a path that was never picked here
        # (a hand-edited session file, a replayed intent) buys nothing.
        requested_workspace = str(workspace_path or "").strip()[:_PATH_CAP]
        if requested_workspace:
            node.state.harness_workspace_path = requested_workspace
        await publish_scene()

        request_id = await agent_dispatcher.start_harness_run(
            bus=bus, notifications_state=notifications, document=document,
            harness_node_id=node.id, user_text=goal_text, model_ref=model_ref,
        )
        if request_id is not None:
            # Stamp the creation command with the run that now owns it -
            # the builder-plan precedent: the run id cannot exist before
            # the node does (the claim needs a node_id).
            command.run_id = request_id
        return node.id if request_id is not None else None

    async def send(node_id, text, allow_session_grants=True, *, model_ref=None):
        message = str(text or "").strip()[:_MESSAGE_CAP]
        node = document.nodes.get(node_id)
        if node is None or not isinstance(node.state, HarnessState):
            notifications.show("This agent node no longer exists.", "warning")
            await bus.publish("notification")
            return None
        if not message:
            notifications.show("Type a message for the agent first.", "info")
            await bus.publish("notification")
            return None
        if node.pending_request_id:
            notifications.show("This agent is still working - stop it or wait.", "info")
            await bus.publish("notification")
            return None
        return await agent_dispatcher.start_harness_run(
            bus=bus, notifications_state=notifications, document=document,
            harness_node_id=node_id, user_text=message, model_ref=model_ref,
            # MCP task starts opt out; UI/composer sends retain normal grants.
            allow_session_grants=allow_session_grants is True,
        )

    async def cancel(request_id):
        agent_dispatcher.cancel_harness(request_id)

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

    async def set_shell(node_id, shell):
        """Save a node preference for subsequent tasks, not the live run."""
        node = document.nodes.get(node_id)
        if node is None or not isinstance(node.state, HarnessState):
            return
        if not isinstance(shell, str) or shell not in VALID_SHELLS:
            notifications.show("Unsupported agent shell.", "warning")
            await bus.publish("notification")
            return
        node.state.harness_shell = shell
        await publish_scene()

    async def set_execution_profile(node_id, profile):
        """Save for future tasks. The shell tools enforce it at launch time."""
        node = document.nodes.get(node_id)
        if node is None or not isinstance(node.state, HarnessState):
            return
        if not isinstance(profile, str) or profile not in VALID_EXECUTION_PROFILES:
            notifications.show("Unsupported agent execution profile.", "warning")
            await bus.publish("notification")
            return
        changed = node.state.harness_execution_profile != profile
        node.state.harness_execution_profile = profile
        if changed and node.state.harness_status != "running":
            # A stopped task has no reason to retain interactive processes
            # from its previous execution profile.
            sessions = getattr(agent_dispatcher, "_harness_shell_sessions", None)
            if sessions is not None:
                await asyncio.to_thread(sessions.stop_workspace, node.state.harness_workspace_id)
        await publish_scene()

    async def get_isolation_engine():
        """Probe the BACKEND host, not the browser or a saved graph."""
        return available_engine()

    async def approve_tool(request_id, approval_id=""):
        if approval_id:
            agent_dispatcher.approve_code_execution(request_id, approval_id)
        else:
            agent_dispatcher.approve_code_execution(request_id)

    async def deny_tool(request_id, approval_id=""):
        if approval_id:
            agent_dispatcher.deny_code_execution(request_id, approval_id)
        else:
            agent_dispatcher.deny_code_execution(request_id)

    async def approve_tool_for_session(request_id, approval_id=""):
        """§2.4's graded consent - approve and stop asking for this tool for
        the rest of this agent's session. Deliberately a SEPARATE intent
        rather than a flag on approveTool: the two are different decisions
        with different blast radii, and a UI that can accidentally send the
        broader one in place of the narrower one is a UI that will."""
        agent_dispatcher.approve_harness_tool_for_session(request_id, approval_id)

    async def remove_shell_allow_rule(node_id, rule):
        """Revoke one persisted prefix from this node, effective immediately."""
        node = document.nodes.get(node_id)
        if node is None or not isinstance(node.state, HarnessState):
            return
        if not isinstance(rule, str):
            return
        # In-place mutation preserves the live run's rule-list reference.
        if rule in node.state.harness_shell_allow_rules:
            node.state.harness_shell_allow_rules.remove(rule)
            await publish_scene()

    async def answer_question(request_id, answer):
        """Resolve a run parked on user.ask (§2.3). Distinct from
        approveTool because the payload is the user's TEXT, not a boolean -
        the run needs what they said, not merely that they responded. An
        empty/blank answer is a dismissal, which the tool reports to the
        model as "declined to answer" rather than as an error."""
        agent_dispatcher.answer_harness_question(request_id, answer)

    bus.register_intent("harness", "start", start)
    bus.register_intent("harness", "send", send)
    register_harness_composer_intent(
        bus, document, notifications, composer_document, start=start, send=send,
    )
    bus.register_intent("harness", "cancel", cancel)
    bus.register_intent("harness", "approveTool", approve_tool)
    bus.register_intent("harness", "denyTool", deny_tool)
    bus.register_intent("harness", "approveToolForSession", approve_tool_for_session)
    bus.register_intent("harness", "answer", answer_question)
    bus.register_intent("harness", "pickLaunchWorkspace", pick_launch_workspace)
    bus.register_intent("harness", "pickWorkspace", pick_workspace)
    bus.register_intent("harness", "useScratch", use_scratch)
    bus.register_intent("harness", "listWorkspaceFiles", list_files)
    bus.register_intent("harness", "readWorkspaceFile", read_file)
    bus.register_intent("harness", "revealWorkspace", reveal_folder)
    bus.register_intent("harness", "setShell", set_shell)
    bus.register_intent("harness", "removeShellAllowRule", remove_shell_allow_rule)
    bus.register_intent("harness", "setExecutionProfile", set_execution_profile)
    bus.register_intent("harness", "getIsolationEngine", get_isolation_engine)
