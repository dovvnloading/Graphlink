"""ADR-002 stage 2.6: Chart node generate/resize/aspect-lock.

Relocated VERBATIM from backend/canvas.py's former register_canvas
(closures at lines 420-489 and 715-721; registration calls from the former
tail block at lines 1151-1153 and 1160-1161) - pure code motion, no
behavior change.

Unlike every branch-point-child kind (Web Research/Artifact/Gitlink/
Execution Sandbox), Chart has no separate "create an empty node,
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

    def chart_source_options(chart_type, options):
        """Only the four scientific capabilities are requestable. Options
        steer extraction; they never supply, fabricate or override data."""
        if options is None or options == {}:
            return ""
        if not isinstance(options, dict):
            raise ChartDataError("chartOptions must be an object")
        allowed = {"multipleSeries", "errorBars", "xScale", "yScale", "xUnit", "yUnit"}
        unknown = set(options) - allowed
        if unknown:
            raise ChartDataError("Unsupported chartOptions: " + ", ".join(sorted(map(str, unknown))))
        if chart_type not in ("line", "scatter"):
            raise ChartDataError("Scientific chartOptions require line or scatter")
        lines = []
        for key in ("multipleSeries", "errorBars"):
            if key in options:
                if not isinstance(options[key], bool):
                    raise ChartDataError(f"{key} must be a boolean")
                if options[key]:
                    lines.append("Extract " + ("multiple named series" if key == "multipleSeries"
                                               else "per-point error magnitudes") +
                                 " only where they are explicitly stated in the source.")
        for key in ("xScale", "yScale"):
            if key in options:
                if options[key] not in ("linear", "log10"):
                    raise ChartDataError(f"{key} must be linear or log10")
                lines.append(f"Use {key}={options[key]} exactly.")
        for key in ("xUnit", "yUnit"):
            if key in options:
                value = options[key]
                if not isinstance(value, str) or not value.strip() or len(value.strip()) > 160:
                    raise ChartDataError(f"{key} must be a nonempty unit of at most 160 characters")
                lines.append(f"Use {key}={value.strip()!r} exactly.")
        return "\n".join(lines)

    async def generate_chart(parent_node_id, chart_type, chartOptions=None):
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

        try:
            option_prompt = chart_source_options(normalized_chart_type, chartOptions)
        except ChartDataError as exc:
            notifications.show(str(exc), "warning")
            await bus.publish("notification")
            return None

        branch_history = document.chat_branch_history(parent_node_id)
        source_text = _chart_source_text(branch_history)
        if option_prompt:
            source_text = (
                "Requested scientific chart options (only extract values present in the source):\n"
                + option_prompt + "\n\n" + source_text
            )

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
                # R6.2 contract, ADR-013 stage 13.3: _call_chart_agent's own
                # respond_json call (one repair round trip against the real
                # JSON Schema, then StructuredOutputError) already tries
                # hard to guarantee schema-conforming output before ever
                # returning successfully - this is the rare defensive case
                # where it still somehow didn't. Never a silent no-op:
                # still create a real chart node with a minimal placeholder
                # shape and chart_error set, same "degrade gracefully, never
                # drop the request" contract as the agent's own internal
                # fallback chain.
                if normalized_chart_type == "scatter":
                    # The legacy helper lives in backend/canvas.py (outside
                    # this feature's edit scope) and only constructs
                    # categorical placeholders. Scatter needs real numeric
                    # x coordinates even for an error-state chart.
                    chart_data = canonicalize_chart_data({
                        "type": "scatter", "title": "Chart",
                        "xValues": [1.0, 2.0], "values": [1.0, 1.0],
                    }, "scatter")
                elif normalized_chart_type == "boxplot":
                    chart_data = canonicalize_chart_data({
                        "type": "boxplot", "groups": [
                            {"label": "No valid data", "values": [0, 0]}
                        ],
                    }, "boxplot")
                elif normalized_chart_type == "heatmap":
                    chart_data = canonicalize_chart_data({
                        "type": "heatmap", "rowLabels": ["No valid data"],
                        "columnLabels": ["Value"], "matrix": [[0]],
                    }, "heatmap")
                else:
                    chart_data = _placeholder_chart_data(normalized_chart_type)
                chart_error = f"The generated chart data could not be validated: {exc}"
            # ADR-010 stage 10.1: agent provenance - this node is produced by
            # a model generation, not a direct user action (stage 10.5's
            # "undo this build" is what will consume that distinction).
            # ADR-013 stage 13.4 retired add_chart_node's own PNG render (see
            # ChartState's own docstring) - there is no longer an asset for
            # record_command to snapshot alongside the node.
            node, _command = document.record_command(
                "generateChart", "agent",
                lambda: document.add_chart_node(
                    *document.place_child(parent_node_id, "chart", prefer="right"),
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
