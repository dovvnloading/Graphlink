"""ADR-008 stage 8.3: the "builder" topic's WS intents + the plan-step
editor.

Run-lifecycle intents (start/startExecution/cancel/approve/deny) are
B-classified: they start/steer/stop a run, and the CONTENT a run produces
is undoable through its own run_id-stamped commands (the runPyCoder
precedent exactly). `scene/setPlanSteps` is the one A-classified intent
here - a human editing the checklist is document content a Ctrl+Z must
reach.

The plan-node CREATION command is stamped with the planning run's id
AFTER the claim (via the returned Command object - the run does not exist
yet when the node must), so "Undo this build" reverts the plan node
itself along with everything the build made.
"""

from __future__ import annotations

from backend.api._shared import make_publish_scene
from backend.domain.graph import SceneDocument, SceneError
from backend.domain.node_states import PlanState
from backend.events import SessionBus
from backend.notifications import NotificationState

_MIN_STEPS_BUDGET = 1
_MAX_STEPS_BUDGET = 50
_MIN_TOKENS_BUDGET = 1_000
_MAX_TOKENS_BUDGET = 2_000_000
_MIN_WALL_BUDGET = 30
_MAX_WALL_BUDGET = 7_200

_RESUMABLE_STATUSES = ("awaiting_start", "paused", "interrupted")


def _place_plan_node(document: SceneDocument) -> tuple[float, float]:
    """Right of the current scene's extent - the launcher has no canvas
    anchor, and stacking new plans on (0,0) over existing work would be
    worse than a simple fan to the right."""
    if not document.nodes:
        return 120.0, 120.0
    max_x = max(n.x for n in document.nodes.values())
    min_y = min(n.y for n in document.nodes.values())
    return max_x + 420.0, max(min_y, 120.0)


def register_builder_intents(
    bus: SessionBus,
    document: SceneDocument,
    notifications: NotificationState,
    agent_dispatcher,
) -> None:
    publish_scene = make_publish_scene(bus)

    async def start(goal, mode=None, max_steps=None, max_tokens=None, max_wall_seconds=None):
        goal_text = str(goal or "").strip()
        if not goal_text:
            notifications.show("Give the Builder a goal first.", "info")
            await bus.publish("notification")
            return None
        chosen_mode = str(mode or "copilot")
        if chosen_mode not in ("copilot", "autopilot"):
            chosen_mode = "copilot"
        x, y = _place_plan_node(document)

        def clamp(value, low, high, default):
            try:
                return min(max(int(value), low), high)
            except (TypeError, ValueError):
                return default

        node, command = document.record_command(
            "builderPlan", "agent",
            lambda: document.add_plan_node(
                x, y, goal_text, mode=chosen_mode,
                max_steps=clamp(max_steps, _MIN_STEPS_BUDGET, _MAX_STEPS_BUDGET, 12),
                max_tokens=clamp(max_tokens, _MIN_TOKENS_BUDGET, _MAX_TOKENS_BUDGET, 150_000),
                max_wall_seconds=clamp(max_wall_seconds, _MIN_WALL_BUDGET, _MAX_WALL_BUDGET, 900),
            ),
        )
        await publish_scene()

        request_id = await agent_dispatcher.start_builder_run(
            bus=bus, notifications_state=notifications, document=document,
            plan_node_id=node.id, phase="plan",
        )
        if request_id is not None:
            # Stamp the creation command with the run that now owns it -
            # undo_run must revert the plan node too, and the run id could
            # not exist before the node did (the claim needs a node_id).
            command.run_id = request_id
        return node.id

    async def start_execution(node_id):
        node = document.nodes.get(node_id)
        if node is None or not isinstance(node.state, PlanState):
            notifications.show("This plan node no longer exists.", "warning")
            await bus.publish("notification")
            return None
        if node.state.builder_status not in _RESUMABLE_STATUSES:
            notifications.show(
                f"This build cannot start from status '{node.state.builder_status}'.", "info",
            )
            await bus.publish("notification")
            return None
        if not any(s.get("status") == "pending" for s in node.state.plan_steps):
            notifications.show("This plan has no pending steps left.", "info")
            await bus.publish("notification")
            return None
        return await agent_dispatcher.start_builder_run(
            bus=bus, notifications_state=notifications, document=document,
            plan_node_id=node_id, phase="execute",
        )

    async def cancel(request_id):
        agent_dispatcher.cancel_builder(request_id)

    async def approve_tool(request_id):
        agent_dispatcher.approve_code_execution(request_id)

    async def deny_tool(request_id):
        agent_dispatcher.deny_code_execution(request_id)

    async def set_plan_steps(node_id, steps):
        try:
            document.record_command(
                "setPlanSteps", "user",
                lambda: document.set_plan_steps(node_id, list(steps or [])),
                node_ids=[node_id],
            )
        except SceneError as exc:
            notifications.show(str(exc), "warning")
            await bus.publish("notification")
            return
        await publish_scene()

    bus.register_intent("builder", "start", start)
    bus.register_intent("builder", "startExecution", start_execution)
    bus.register_intent("builder", "cancel", cancel)
    bus.register_intent("builder", "approveTool", approve_tool)
    bus.register_intent("builder", "denyTool", deny_tool)
    bus.register_intent("scene", "setPlanSteps", set_plan_steps)
