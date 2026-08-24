"""PLAN-2026-08-24 H1: the "harness" topic's WS intents.

All three are B-classified run-lifecycle intents (start/steer/stop a run;
the CONTENT a run produces lives in the workspace transcript, outside the
undo domain entirely) - except the node CREATION inside `start`, which is
an ordinary Ctrl+Z-undoable command, the builder-plan precedent exactly.
"""

from __future__ import annotations

from backend.api._shared import make_publish_scene
from backend.domain.graph import SceneDocument
from backend.domain.node_states import HarnessState
from backend.events import SessionBus
from backend.notifications import NotificationState

_MIN_TURNS = 1
_MAX_TURNS = 64
_DEFAULT_TURNS = 16
# One user message's own bound - the same wire-input-is-untrusted posture
# every other intent that accepts free text takes.
_MESSAGE_CAP = 20_000


def _place_harness_node(document: SceneDocument) -> tuple[float, float]:
    """Right of the current scene's extent - the builder-plan placement
    exactly (no canvas anchor exists for a fresh agent either)."""
    if not document.nodes:
        return 120.0, 120.0
    max_x = max(n.x for n in document.nodes.values())
    min_y = min(n.y for n in document.nodes.values())
    return max_x + 420.0, max(min_y, 120.0)


def register_harness_intents(
    bus: SessionBus,
    document: SceneDocument,
    notifications: NotificationState,
    agent_dispatcher,
) -> None:
    publish_scene = make_publish_scene(bus)

    async def start(goal, max_turns=None):
        goal_text = str(goal or "").strip()[:_MESSAGE_CAP]
        if not goal_text:
            notifications.show("Give the agent a task first.", "info")
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
        await publish_scene()

        request_id = await agent_dispatcher.start_harness_run(
            bus=bus, notifications_state=notifications, document=document,
            harness_node_id=node.id, user_text=goal_text,
        )
        if request_id is not None:
            # Stamp the creation command with the run that now owns it -
            # the builder-plan precedent: the run id cannot exist before
            # the node does (the claim needs a node_id).
            command.run_id = request_id
        return node.id

    async def send(node_id, text):
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
            harness_node_id=node_id, user_text=message,
        )

    async def cancel(request_id):
        agent_dispatcher.cancel_harness(request_id)

    async def approve_tool(request_id):
        agent_dispatcher.approve_code_execution(request_id)

    async def deny_tool(request_id):
        agent_dispatcher.deny_code_execution(request_id)

    bus.register_intent("harness", "start", start)
    bus.register_intent("harness", "send", send)
    bus.register_intent("harness", "cancel", cancel)
    bus.register_intent("harness", "approveTool", approve_tool)
    bus.register_intent("harness", "denyTool", deny_tool)
