"""ADR-002 stage 2.6: Chart node generate/resize/aspect-lock.

Relocated VERBATIM from backend/canvas.py's former register_canvas
(closures at lines 420-489 and 715-721; registration calls from the former
tail block at lines 1151-1153 and 1160-1161) - pure code motion, no
behavior change.

Unlike every branch-point-child kind (Web Research/Artifact/Gitlink/
Py-Coder/Execution Sandbox), Chart has no separate "create an empty node,
then run generation on it" split - generateChart is a single combined
create+generate action, so there is no addChartNode intent at all: the
SceneNode is only ever created (by document.add_chart_node, in
generate_chart's own _on_success below) once real chart data actually
exists, mirroring legacy's own ChartWorkerThread flow (a transient loading
state anchored on the SOURCE node, not a pre-created placeholder chart node
- see graphlink_window_actions.py's generate_chart/handle_chart_data).
"""

from __future__ import annotations

from graphlink_chart_data import ChartDataError, canonicalize_chart_data, SUPPORTED_CHART_TYPES

from backend.agents import AgentDispatcher
from backend.api._shared import make_publish_scene
from backend.domain.graph import SceneDocument
from backend.domain.model import MESSAGE_VERTICAL_SPACING
from backend.events import SessionBus
from backend.notifications import NotificationState


def register_chart_intents(
    bus: SessionBus,
    document: SceneDocument,
    notifications: NotificationState,
    agent_dispatcher: AgentDispatcher,
) -> None:
    # Deferred import - see backend/api/intents_web_research.py's own
    # comment for why a back-reference to backend.canvas cannot be a
    # module-level import here.
    from backend.canvas import _chart_source_text, _placeholder_chart_data

    publish_scene = make_publish_scene(bus)

    async def generate_chart(parent_node_id, chart_type):
        if not parent_node_id or parent_node_id not in document.nodes:
            notifications.show(
                "Please select a valid node to branch from before generating a chart.",
                "warning",
            )
            await bus.publish("notification")
            return None

        normalized_chart_type = str(chart_type or "").strip().lower()
        if normalized_chart_type not in SUPPORTED_CHART_TYPES:
            notifications.show(
                "Please choose a valid chart type before generating a chart.",
                "warning",
            )
            await bus.publish("notification")
            return None

        parent = document.nodes[parent_node_id]
        branch_history = document.chat_branch_history(parent_node_id)
        source_text = _chart_source_text(branch_history)

        result_holder: dict[str, str] = {}

        async def _on_success(result):
            if parent_node_id not in document.nodes:
                # 6.2 review fix: parent deleted mid-generation - silent
                # no-op, the same liveness posture every other agent-produced
                # surface already takes (_generate_note_from_node,
                # compare_branches, synthesize_branches, _dispatch_image).
                # Newly reachable from a SINGLE connection now that the
                # generation no longer blocks the WS read loop: the user can
                # freely delete the parent while the chart generates, and
                # add_chart_node would raise SceneError into a spurious
                # "Chart generation failed" toast.
                return
            try:
                chart_data = canonicalize_chart_data(result, normalized_chart_type)
                chart_error = ""
            except ChartDataError as exc:
                # R6.2 contract: ChartDataAgent's own validate_chart_data
                # pipeline (repair round trip, then heuristic fallback)
                # already tries hard to guarantee canonical output before
                # ever returning successfully - this is the rare defensive
                # case where it still somehow didn't. Never a silent no-op:
                # still create a real chart node with a minimal placeholder
                # shape and chart_error set, same "degrade gracefully, never
                # drop the request" contract as the agent's own internal
                # fallback chain.
                chart_data = _placeholder_chart_data(normalized_chart_type)
                chart_error = f"The generated chart data could not be validated: {exc}"
            # ADR-010 stage 10.1: agent provenance - this node is produced by
            # a model generation, not a direct user action (stage 10.5's
            # "undo this build" is what will consume that distinction).
            # add_chart_node also mints chart asset bytes into image_assets;
            # record_command captures those alongside the node, so undoing a
            # generated chart does not strand its PNG.
            node, _command = document.record_command(
                "generateChart", "agent",
                lambda: document.add_chart_node(
                    parent.x + MESSAGE_VERTICAL_SPACING,
                    parent.y,
                    parent_node_id,
                    normalized_chart_type,
                    chart_data,
                    chart_error=chart_error,
                ),
                node_ids=[parent_node_id],
            )
            result_holder["node_id"] = node.id
            await bus.publish("scene")

        def _on_failure(message):
            # Matches ChartWorkerThread's own error path (and this feature's
            # explicit contract): a genuinely unrecoverable agent-side
            # failure (get_response's own top-level "error" key, or a
            # timeout/exception) shows a notification and creates nothing -
            # start_chart_generation above already shows that notification,
            # so there is nothing left for this callback to do.
            pass

        await agent_dispatcher.start_chart_generation(
            bus=bus,
            notifications_state=notifications,
            node_id=parent_node_id,
            chart_type=normalized_chart_type,
            source_text=source_text,
            on_success=_on_success,
            on_failure=_on_failure,
        )
        return result_holder.get("node_id")

    async def resize_chart(node_id, width, height):
        document.record_command(
            "resizeChart", "user", lambda: document.resize_chart(node_id, width, height),
            node_ids=[node_id],
        )
        await publish_scene()

    async def toggle_chart_aspect_lock(node_id):
        document.record_command(
            "toggleChartAspectLock", "user",
            lambda: document.toggle_chart_aspect_lock(node_id),
            node_ids=[node_id],
        )
        await publish_scene()

    # R6.2: a single combined create+generate action, unlike every
    # node-creation flow above - see generate_chart's own docstring.
    bus.register_intent("scene", "generateChart", generate_chart)
    bus.register_intent("scene", "resizeChart", resize_chart)
    bus.register_intent("scene", "toggleChartAspectLock", toggle_chart_aspect_lock)
