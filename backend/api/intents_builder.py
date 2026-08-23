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

import asyncio

from backend.api._settings_shared import run_locked
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

_RESUMABLE_STATUSES = ("awaiting_start", "paused", "interrupted", "failed")
# "failed" review-fix: a transient provider fault (rate limit, a 5xx past
# the retry cap, a network blip) used to land here as a PERMANENT dead
# end - the plan node's goal/checklist/spent budgets stay right there on
# the canvas, so refusing to resume from them contradicted this app's own
# "state lives on the canvas" design for the single most common mid-build
# fault class. run_build resets the wedged in-flight step back to
# "pending" before landing "failed" so resume picks up exactly there.


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

    async def start(goal, mode=None, max_steps=None, max_tokens=None, max_wall_seconds=None, recipe=None):
        from backend.builder import recipe_by_name

        goal_text = str(goal or "").strip()
        seeded = None
        if recipe:
            seeded = recipe_by_name(agent_dispatcher._settings_manager, str(recipe))
            if seeded is None:
                notifications.show(f"Unknown recipe: {recipe}", "warning")
                await bus.publish("notification")
                return None
            # The recipe's goal is the frame; the user's text (optional
            # here) is the specific task inside it.
            goal_text = f"{seeded['goal']}\n\nTask:\n{goal_text}" if goal_text else seeded["goal"]
        if not goal_text:
            notifications.show("Give the Builder a goal first.", "info")
            await bus.publish("notification")
            return None
        chosen_mode = str(mode or (seeded["mode"] if seeded else "copilot"))
        if chosen_mode not in ("copilot", "autopilot"):
            chosen_mode = "copilot"
        x, y = _place_plan_node(document)

        def clamp(value, low, high, default):
            try:
                return min(max(int(value), low), high)
            except (TypeError, ValueError):
                return default

        def mutator():
            node = document.add_plan_node(
                x, y, goal_text, mode=chosen_mode,
                max_steps=clamp(max_steps, _MIN_STEPS_BUDGET, _MAX_STEPS_BUDGET, 12),
                max_tokens=clamp(max_tokens, _MIN_TOKENS_BUDGET, _MAX_TOKENS_BUDGET, 150_000),
                max_wall_seconds=clamp(max_wall_seconds, _MIN_WALL_BUDGET, _MAX_WALL_BUDGET, 900),
            )
            if seeded is not None and seeded["steps"]:
                document.set_plan_steps(node.id, [
                    {"id": f"s{i+1}", "title": t, "status": "pending", "detail": ""}
                    for i, t in enumerate(seeded["steps"])
                ])
                node.state.builder_status = "awaiting_start"
            return node

        node, command = document.record_command("builderPlan", "agent", mutator)
        await publish_scene()

        if seeded is not None and seeded["steps"]:
            # A recipe-seeded plan needs no planning run at all - the
            # checklist is already there, awaiting review/Start. Its
            # creation is a normal Ctrl+Z-undoable command; undo_run covers
            # the EXECUTION run's own mutations once one starts.
            return node.id

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

    async def list_recipes():
        from backend.builder import list_all_recipes

        return {"recipes": list_all_recipes(agent_dispatcher._settings_manager)}

    async def save_recipe(node_id, name):
        from backend.builder import BUILT_IN_RECIPES, recipe_from_plan_node

        node = document.nodes.get(node_id)
        if node is None or not isinstance(node.state, PlanState):
            notifications.show("This plan node no longer exists.", "warning")
            await bus.publish("notification")
            return None
        clean_name = str(name or "").strip() or node.state.plan_goal[:40]
        if any(r["name"] == clean_name for r in BUILT_IN_RECIPES):
            notifications.show(f'"{clean_name}" is a built-in recipe name - pick another.', "warning")
            await bus.publish("notification")
            return None
        if not node.state.plan_steps:
            notifications.show("This plan has no steps to save.", "info")
            await bus.publish("notification")
            return None
        settings = agent_dispatcher._settings_manager
        new_recipe = recipe_from_plan_node(node, clean_name)

        def _persist() -> None:
            existing = [r for r in settings.get_recipes() if r["name"] != clean_name]
            settings.set_recipes(existing + [new_recipe])

        # REVIEW-FIX: was an unlocked settings.get_recipes()/set_recipes()
        # pair called directly on the event loop - every other settings-
        # mutating intent in this codebase runs its SettingsManager writes
        # through asyncio.to_thread(run_locked, ...) instead (backend/api/
        # _settings_shared.py's own module docstring: _save_state's
        # open()/json.dump()/fsync()/os.replace() sequence releases the GIL
        # mid-write, so an unlocked writer here could land in the middle of
        # another settings save's own write to the SAME state file).
        # _save_state's except clause swallows the resulting IOError (logs
        # and returns, never raises), so this used to report a successful
        # save even when the disk write silently lost the change. The read
        # and the write stay inside one run_locked closure rather than two
        # separate to_thread calls, matching apply_ollama_chat_model's own
        # "read-modify-write in a single locked section" precedent.
        await asyncio.to_thread(run_locked, _persist)
        notifications.show(f'Saved recipe "{clean_name}".', "info")
        await bus.publish("notification")
        return clean_name

    async def delete_recipe(name):
        """stage 8.7: rounds out the recipe lifecycle - saveRecipe already
        creates one from a finished build, but nothing could ever remove
        one. Mirrors save_recipe's own name-guard against built-ins and its
        own settings.get_recipes()/set_recipes() replace-the-whole-list
        posture."""
        from backend.builder import BUILT_IN_RECIPES

        clean_name = str(name or "").strip()
        if any(r["name"] == clean_name for r in BUILT_IN_RECIPES):
            notifications.show(f'"{clean_name}" is a built-in recipe - it cannot be deleted.', "warning")
            await bus.publish("notification")
            return False
        settings = agent_dispatcher._settings_manager
        found = {"value": False}

        def _persist() -> None:
            existing = settings.get_recipes()
            remaining = [r for r in existing if r["name"] != clean_name]
            if len(remaining) != len(existing):
                found["value"] = True
                settings.set_recipes(remaining)

        # REVIEW-FIX: same unlocked-write hazard save_recipe closed above -
        # see its own comment for the full mechanism.
        await asyncio.to_thread(run_locked, _persist)
        if not found["value"]:
            notifications.show(f'No saved recipe named "{clean_name}".', "info")
            await bus.publish("notification")
            return False
        notifications.show(f'Deleted recipe "{clean_name}".', "info")
        await bus.publish("notification")
        return True

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
        node = document.nodes.get(node_id)
        # REVIEW-FIX: a live run_build holds `step = _current_step(node)`
        # (backend/builder.py) as a direct reference into the OLD dict
        # object living inside node.state.plan_steps, for the whole step -
        # document.set_plan_steps always REPLACES the list with fresh dicts
        # (graph.py), even for unchanged entries. Letting this intent land
        # while the node is busy detaches run_build's own reference: its
        # later `step["status"] = "done"` writes to an orphaned object, and
        # the real (frozen, non-pending) row in plan_steps can never be
        # touched again. Only run_build's own replan path re-resolves the
        # reference after a replacement (builder.py's `_apply_replan`
        # caller); this externally-triggered mutation has no such recovery,
        # so it must be refused outright - mirrors undo/redo's own
        # _guard_live_runs (backend/domain/commands.py) checking the same
        # node.pending_request_id for the identical hazard class.
        if node is not None and node.pending_request_id:
            notifications.show(
                f"Can't edit \"{node.title}\"'s steps while it is still running - "
                "cancel or wait for it to finish.",
                "info",
            )
            await bus.publish("notification")
            return
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
    bus.register_intent("builder", "listRecipes", list_recipes)
    bus.register_intent("builder", "saveRecipe", save_recipe)
    bus.register_intent("builder", "deleteRecipe", delete_recipe)
    bus.register_intent("scene", "setPlanSteps", set_plan_steps)
