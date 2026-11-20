"""PLAN-2026-08-24 H1: the "harness" topic's WS intents.

All three are B-classified run-lifecycle intents (start/steer/stop a run;
the CONTENT a run produces lives in the workspace transcript, outside the
undo domain entirely) - except the node CREATION inside `start`, which is
an ordinary Ctrl+Z-undoable command, the builder-plan precedent exactly.
"""

from __future__ import annotations

import asyncio
import re
from collections import OrderedDict

from backend.api._shared import make_publish_scene
from backend.api.intents_harness_workspace import register_harness_workspace_intents
from backend.domain.graph import SceneDocument
from backend.domain.node_states import HarnessState
from backend.harness.shell_selection import VALID_SHELLS
from backend.harness.shell_isolation import VALID_EXECUTION_PROFILES, available_engine
from backend.harness.permission_modes import AUTONOMOUS, VALID_PERMISSION_MODES
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
# The same bounded, session-local receipt posture as MCP agent task control.
_MAX_SEND_RECEIPTS = 2_048


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

    async def composer_send(text, selected_node_id=None, execution_profile="host"):
        """Route the composer to the selected agent, or create one if none is selected.

        The model is resolved from THIS session's live composer route rather
        than trusting a client-supplied model identifier. Every task uses the
        exact model shown in the composer at dispatch time; the regular
        launcher and per-card follow-up retain their existing defaults.
        """
        message = str(text or "").strip()
        if not message:
            notifications.show("Type a message for the agent first.", "info")
            await bus.publish("notification")
            return None
        if len(message) > _MESSAGE_CAP:
            notifications.show("Agent tasks cannot exceed 20,000 characters. Shorten the draft and retry.", "warning")
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
        return await start(message, execution_profile=execution_profile, model_ref=model_ref)

    bus.register_intent("harness", "composerSend", composer_send)


def register_harness_preferences_intents(
    bus: SessionBus,
    document: SceneDocument,
    notifications: NotificationState,
    agent_dispatcher,
    publish_scene,
) -> None:
    """Register saved agent preferences separately from run lifecycle controls."""

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
        if profile != "isolated" and node.state.harness_permission_mode == AUTONOMOUS:
            # Autonomous cannot remain selected under a Host profile. A
            # running task retains its already-snapshotted isolated profile.
            node.state.harness_permission_mode = "ask"
        if changed and node.state.harness_status != "running":
            # A stopped task has no reason to retain interactive processes
            # from its previous execution profile.
            sessions = getattr(agent_dispatcher, "_harness_shell_sessions", None)
            if sessions is not None:
                await asyncio.to_thread(sessions.stop_workspace, node.state.harness_workspace_id)
        await publish_scene()

    async def set_permission_mode(node_id, mode):
        """The next task captures this preference at claim time."""
        node = document.nodes.get(node_id)
        if node is None or not isinstance(node.state, HarnessState):
            return
        if not isinstance(mode, str) or mode not in VALID_PERMISSION_MODES:
            notifications.show("Unsupported workspace permission mode.", "warning")
            await bus.publish("notification")
            return
        if mode == AUTONOMOUS and node.state.harness_execution_profile != "isolated":
            notifications.show("Autonomous requires the Isolated execution profile.", "warning")
            await bus.publish("notification")
            return
        node.state.harness_permission_mode = mode
        if mode == AUTONOMOUS:
            # The old broad network preference must not silently revive on
            # a later task or be mistaken for per-request network consent.
            node.state.harness_network_always = False
        await publish_scene()

    async def set_network_always(node_id, enabled):
        """Persist explicit opt-in; the active task retains its snapshot."""
        node = document.nodes.get(node_id)
        if node is None or not isinstance(node.state, HarnessState):
            return
        if not isinstance(enabled, bool):
            notifications.show("Network setting must be true or false.", "warning")
            await bus.publish("notification")
            return
        if enabled and node.state.harness_permission_mode == AUTONOMOUS:
            notifications.show("Autonomous always asks before network access.", "warning")
            await bus.publish("notification")
            return
        node.state.harness_network_always = enabled
        await publish_scene()

    async def get_isolation_engine():
        """Probe the BACKEND host, not the browser or a saved graph."""
        return available_engine()

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

    bus.register_intent("harness", "setShell", set_shell)
    bus.register_intent("harness", "removeShellAllowRule", remove_shell_allow_rule)
    bus.register_intent("harness", "setExecutionProfile", set_execution_profile)
    bus.register_intent("harness", "setPermissionMode", set_permission_mode)
    bus.register_intent("harness", "setNetworkAlways", set_network_always)
    bus.register_intent("harness", "getIsolationEngine", get_isolation_engine)

def register_harness_intents(
    bus: SessionBus,
    document: SceneDocument,
    notifications: NotificationState,
    agent_dispatcher,
    composer_document=None,
) -> None:
    publish_scene = make_publish_scene(bus)

    async def start(goal, max_turns=None, workspace_path=None, execution_profile="host", *, model_ref=None):
        goal_text = str(goal or "").strip()
        if not goal_text:
            notifications.show("Give the agent a task first.", "info")
            await bus.publish("notification")
            return None
        if len(goal_text) > _MESSAGE_CAP:
            notifications.show("Agent tasks cannot exceed 20,000 characters. Shorten the draft and retry.", "warning")
            await bus.publish("notification")
            return None
        if not isinstance(execution_profile, str) or execution_profile not in VALID_EXECUTION_PROFILES:
            notifications.show("Unsupported agent execution profile.", "warning")
            await bus.publish("notification")
            return None
        if execution_profile == "isolated" and available_engine() is None:
            notifications.show("Isolated requires Docker or Podman on the backend. Agent not started.", "warning")
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

        # The launcher's folder choice is bound BEFORE the first run, not
        # trusted by naming alone: bound_root re-checks the trusted-directory
        # list at run time and falls back to scratch if it was not granted.
        requested_workspace = str(workspace_path or "").strip()[:_PATH_CAP]

        def create_with_launch_options():
            created = document.add_harness_node(x, y, goal_text, max_turns=clamp(max_turns))
            if requested_workspace:
                created.state.harness_workspace_path = requested_workspace
            # record_command deep-copies the node as soon as this mutator
            # returns. Both options must be present in the redo snapshot.
            created.state.harness_execution_profile = execution_profile
            return created

        node, command = document.record_command(
            "harnessCreate", "agent", create_with_launch_options,
        )

        # The busy guard above and the dispatcher's synchronous run claim
        # must not yield to a second start. Publishing here previously let
        # both callers commit a creation before one of their runs was denied.
        request_id = await agent_dispatcher.start_harness_run(
            bus=bus, notifications_state=notifications, document=document,
            harness_node_id=node.id, user_text=goal_text, model_ref=model_ref,
        )
        if request_id is not None:
            # Stamp the creation command with the run that now owns it -
            # the builder-plan precedent: the run id cannot exist before
            # the node does (the claim needs a node_id).
            command.run_id = request_id
            await publish_scene()
        return node.id if request_id is not None else None

    async def send(node_id, text, allow_session_grants=True, operation_id=None, *, model_ref=None):
        message = str(text or "").strip()
        node = document.nodes.get(node_id)
        if node is None or not isinstance(node.state, HarnessState):
            notifications.show("This agent node no longer exists.", "warning")
            await bus.publish("notification")
            return None
        if not message:
            notifications.show("Type a message for the agent first.", "info")
            await bus.publish("notification")
            return None
        if len(message) > _MESSAGE_CAP:
            notifications.show("Agent tasks cannot exceed 20,000 characters. Shorten the draft and retry.", "warning")
            await bus.publish("notification")
            return None
        # A WebSocket reply can be lost AFTER start_harness_run has claimed
        # the task. A card retries that uncertain send using the same client
        # operation ID; check its receipt BEFORE the busy guard, even when the
        # original run has already finished. Explicit refusals have no receipt.
        receipts = None
        fingerprint = None
        if operation_id is not None:
            if (not isinstance(operation_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", operation_id)):
                notifications.show("Invalid agent send operation ID.", "warning")
                await bus.publish("notification")
                return None
            if getattr(agent_dispatcher, "_harness_send_document", None) is not document:
                agent_dispatcher._harness_send_document = document
                agent_dispatcher._harness_send_receipts = OrderedDict()
            receipts = agent_dispatcher._harness_send_receipts
            fingerprint = (
                node_id, message, allow_session_grants is True,
                # ModelRef is frozen: equality covers provider and model ID.
                model_ref,
                node.state.harness_workspace_id,
            )
            previous = receipts.get(operation_id)
            if previous is not None:
                if previous[0] != fingerprint:
                    notifications.show("Agent send operation ID was reused for a different task.", "warning")
                    await bus.publish("notification")
                    return None
                receipts.move_to_end(operation_id)
                return previous[1]
        if node.pending_request_id:
            notifications.show("This agent is still working - stop it or wait.", "info")
            await bus.publish("notification")
            return None
        request_id = await agent_dispatcher.start_harness_run(
            bus=bus, notifications_state=notifications, document=document,
            harness_node_id=node_id, user_text=message, model_ref=model_ref,
            # MCP task starts opt out; UI/composer sends retain normal grants.
            allow_session_grants=allow_session_grants is True,
        )
        if request_id is not None and receipts is not None:
            receipts[operation_id] = (fingerprint, request_id)
            if len(receipts) > _MAX_SEND_RECEIPTS:
                receipts.popitem(last=False)
        return request_id

    async def cancel(request_id):
        agent_dispatcher.cancel_harness(request_id)

    async def approve_tool(request_id, approval_id=""):
        if approval_id:
            agent_dispatcher.approve_code_execution(request_id, approval_id)
        else:
            agent_dispatcher.approve_code_execution(request_id)

    async def approve_network_once(request_id, approval_id):
        agent_dispatcher.approve_harness_network(request_id, approval_id)

    async def approve_network_for_task(request_id, approval_id):
        agent_dispatcher.approve_harness_network(request_id, approval_id, for_task=True)

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
    bus.register_intent("harness", "approveNetworkOnce", approve_network_once)
    bus.register_intent("harness", "approveNetworkForTask", approve_network_for_task)
    bus.register_intent("harness", "denyTool", deny_tool)
    bus.register_intent("harness", "approveToolForSession", approve_tool_for_session)
    bus.register_intent("harness", "answer", answer_question)
    register_harness_workspace_intents(bus, document, notifications, agent_dispatcher)
    register_harness_preferences_intents(
        bus, document, notifications, agent_dispatcher, publish_scene,
    )
