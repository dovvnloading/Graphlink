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

import asyncio
import base64
import json
import re

from graphlink_chart_data import ChartDataError, canonicalize_chart_data, scientific_result_chart, SUPPORTED_CHART_TYPES, validate_publication_export
from graphlink_chart_rendering import render_chart_publication

from backend.agents import AgentDispatcher
from backend.api._shared import make_publish_scene
from backend.domain.graph import SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.domain.node_states import ChartState


def _make_publication_export_handler(document: SceneDocument):
    """Build the bounded scene export intent without inflating register_chart_intents."""

    async def export_publication_chart(node_id, options):
        """Stream a bounded, authenticated chart file over the existing scene
        intent channel. backend/assets.py's legacy PNG/SVG route is unchanged:
        this dedicated path supports selectable page dimensions and vector PDF
        without widening that out-of-scope route.
        """
        node = document.nodes.get(node_id) if isinstance(node_id, str) else None
        if node is None or node.kind != "chart" or not isinstance(node.state, ChartState):
            raise ChartDataError("Unknown chart node")
        state = node.state
        if not isinstance(state.chart_data, dict):
            raise ChartDataError("Chart has no data to export")
        aspect = state.chart_height / state.chart_width
        settings = validate_publication_export(options, aspect_ratio=aspect)
        content = await asyncio.to_thread(
            render_chart_publication,
            state.chart_type,
            state.chart_data,
            width_mm=settings["widthMm"],
            height_mm=settings["heightMm"],
            fmt=settings["format"],
            dpi=settings["dpi"],
            colorblind_safe=settings["colorblindSafe"],
            grayscale_safe=settings["grayscaleSafe"],
        )
        # Avoid sending arbitrarily large messages on the shared WS channel
        # even if a new renderer or pathological chart becomes less compact.
        if len(content) > 24_000_000:
            raise ChartDataError("Publication export exceeds the 24 MB response limit")
        # Delayed import: backend.assets -> session_context -> canvas ->
        # intents_chart is a real application startup cycle.
        from backend.assets import _sanitize_chart_filename

        title = state.chart_data.get("title", "")
        name = _sanitize_chart_filename(str(title or ""))
        return {
            "filename": f"{name}.{settings['format']}",
            "mimeType": {
                "png": "image/png", "svg": "image/svg+xml",
                "pdf": "application/pdf",
            }[settings["format"]],
            "base64": base64.b64encode(content).decode("ascii"),
            "widthMm": settings["widthMm"],
            "heightMm": settings["heightMm"],
            "dpi": settings["dpi"],
        }

    return export_publication_chart


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

    def scientific_chart_source(node):
        """Read a pack-granted, receipt-verified result, not untrusted content.

        The workbench's serializer verifies the persisted receipt before
        emitting its latest result. A revoked/damaged pack emits no usable
        science payload, so a chart cannot bypass the source grant.
        """
        wire = document._plugin_state_wire(node).get("science")
        if not isinstance(wire, str):
            raise ChartDataError("Enable the scientific pack before charting its result")
        try:
            state = json.loads(wire)
            result = state["latest"]
            node_key = state["nodeKey"]
            if (state.get("error") or not isinstance(result, dict)
                    or not re.fullmatch(r"[0-9a-f]{32}", node_key)):
                raise ChartDataError("Run or restore a valid scientific result before charting")
            provenance = result["provenance"]
            receipt = {"sourceKey": node_key, "resultHash": provenance["resultHash"],
                       "receiptHash": provenance.get("receiptHash") or provenance["resultHash"]}
            return result, receipt
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ChartDataError):
                raise
            raise ChartDataError("The scientific result is unavailable or damaged") from exc

    async def generate_scientific_chart(parent_id, normalized_chart_type, chart_options):
        chart = document.nodes[parent_id]
        refreshing = chart.kind == "chart" and isinstance(chart.state, ChartState) and (
            isinstance(chart.state.chart_data, dict)
            and isinstance(chart.state.chart_data.get("scienceReceipt"), dict)
        )
        if refreshing:
            source_id = chart.state.chart_source_node_id
            source = document.nodes.get(source_id)
            expected = chart.state.chart_data["scienceReceipt"]
            # A saved source id can be reminted on reload, but the stable
            # workbench node key must still match the receipt pinned here.
            if (source is None or not re.fullmatch(r"science_[a-z0-9_]+\.workbench", source.kind)
                    or not any(edge.source == source_id and edge.target == chart.id
                               for edge in document.edges.values())):
                raise ChartDataError("Reconnect the original scientific source before refreshing")
            if normalized_chart_type != chart.state.chart_type:
                raise ChartDataError("Refresh using this chart's original type")
        else:
            source = chart
            if not re.fullmatch(r"science_[a-z0-9_]+\.workbench", source.kind):
                return None

        if chart_options not in (None, {}):
            raise ChartDataError("Scientific result charts use the saved data, not extraction options")
        if normalized_chart_type not in ("scatter", "line", "boxplot", "heatmap"):
            raise ChartDataError("Choose scatter, fitted line, box plot or heatmap for a scientific result")
        result, receipt = scientific_chart_source(source)
        if refreshing and expected.get("sourceKey") != receipt["sourceKey"]:
            raise ChartDataError("This chart belongs to a different scientific result")
        try:
            data = scientific_result_chart(result, normalized_chart_type)
        except ChartDataError:
            raise
        except (ArithmeticError, ValueError, TypeError, IndexError, KeyError) as exc:
            raise ChartDataError(f"The scientific result cannot be charted: {exc}") from exc
        # Re-canonicalize after inserting the strict receipt, so validation
        # is exactly the same for create, refresh, save and reload.
        data["scienceReceipt"] = receipt
        data = canonicalize_chart_data(data, normalized_chart_type)
        if refreshing:
            def update():
                chart.state.chart_data = data
                chart.state.chart_error = ""
                chart.title = data["title"]

            document.record_command("refreshScientificChart", "user", update,
                                    node_ids=[chart.id, source.id])
            node_id = chart.id
        else:
            node, _ = document.record_command(
                "chartScientificResult", "user",
                lambda: document.add_chart_node(
                    *document.place_child(source.id, "chart", prefer="right"),
                    source.id, normalized_chart_type, data,
                ),
                node_ids=[source.id],
            )
            node_id = node.id
        await publish_scene()
        return node_id

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

        # A scientific result charts deterministically, without sending its
        # verified numerical measurements through model extraction. The same
        # existing generateChart action refreshes one of those charts by id.
        parent = document.nodes[parent_node_id]
        is_science = bool(re.fullmatch(r"science_[a-z0-9_]+\.workbench", parent.kind))
        is_science_chart = (parent.kind == "chart" and isinstance(parent.state, ChartState)
                            and isinstance(parent.state.chart_data, dict)
                            and isinstance(parent.state.chart_data.get("scienceReceipt"), dict))
        if is_science or is_science_chart:
            try:
                return await generate_scientific_chart(parent_node_id, normalized_chart_type, chartOptions)
            except ChartDataError as exc:
                notifications.show(str(exc), "warning")
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
    bus.register_intent("scene", "exportPublicationChart", _make_publication_export_handler(document))
