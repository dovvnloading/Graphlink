"""Regression coverage for user-authored chart analysis and publication export.

Only the opt-in chart analysis overlay is mutable: source data, legacy
canonicalization, and the default SVG/Matplotlib domains stay unchanged.
"""
import asyncio
from copy import deepcopy
from xml.etree import ElementTree as ET

import pytest

from graphlink_chart_data import (
    ChartDataError, canonicalize_chart_analysis, canonicalize_chart_data,
)
from graphlink_chart_rendering import render_chart_publication
from backend.api.intents_chart import register_chart_intents
from backend.domain.graph import SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState
import backend.agents as agents_module  # noqa: F401 - session import order
from backend.session_save import build_chat_data
from backend.session_load import restore_chat_into_document


SPECS = {
    "line": {"type": "line", "title": "Trend", "labels": ["a", "b", "c"], "values": [2, 4, 8]},
    "scatter": {
        "type": "scatter", "title": "Log sample",
        "xValues": [1, 10, 100], "values": [2, 4, 8],
        "xScale": "log10", "yScale": "log10",
        "xUnit": "mg/L", "yUnit": "mV",
    },
    "histogram": {"type": "histogram", "title": "Distribution", "values": [1, 2, 2, 3, 4, 5], "bins": 3},
    "boxplot": {"type": "boxplot", "title": "Samples", "groups": [
        {"label": "A", "values": [1, 2, 3, 4]},
        {"label": "B", "values": [2, 4, 6, 8]},
    ]},
}
OVERLAY = {
    "lockX": True,
    "lockY": True,
    "xRange": [2, 50],
    "yRange": [2, 10],
    "annotations": [
        {"kind": "label", "x": 10, "y": 4, "text": "Peak response"},
        {"kind": "arrow", "x": 2, "y": 3, "x2": 10, "y2": 4},
        {"kind": "region", "axis": "x", "from": 2, "to": 20},
        {"kind": "region", "axis": "y", "from": 3, "to": 6},
    ],
}


@pytest.mark.parametrize("chart_type", sorted(SPECS))
def test_analysis_is_additive_and_never_mutates_source_or_default(chart_type):
    original = deepcopy(SPECS[chart_type])
    default = canonicalize_chart_data(original, chart_type)
    assert "analysis" not in default
    edited = {**original, "analysis": deepcopy(OVERLAY)}
    result = canonicalize_chart_data(edited, chart_type)
    assert "analysis" not in default
    assert result == {**default, "analysis": canonicalize_chart_analysis(
        OVERLAY, chart_type,
        x_scale=default.get("xScale", "linear"),
        y_scale=default.get("yScale", "linear"),
    )}
    assert original == SPECS[chart_type]
    assert edited["analysis"] == OVERLAY
    assert canonicalize_chart_data(result, chart_type) == result


@pytest.mark.parametrize("invalid", [
    {"lockX": True, "annotations": []},
    {"lockY": 1, "annotations": []},
    {"xRange": [3, 2], "annotations": []},
    {"xRange": [1, float("nan")], "annotations": []},
    {"unrecognized": True, "annotations": []},
    {"annotations": "not an array"},
    {"annotations": [{}]},
    {"annotations": [1]},
    {"annotations": [{"kind": [], "x": 1, "y": 2}]},
    {"annotations": [{"kind": "label", "x": 1, "y": 2, "text": ""}]},
    {"annotations": [{"kind": "arrow", "x": 1, "y": 2, "x2": float("inf"), "y2": 3}]},
    {"annotations": [{"kind": "region", "axis": "x", "from": 5, "to": 5}]},
    {"annotations": [{"kind": "region", "axis": "y", "from": 0, "to": 2}]},
    {"annotations": [{"kind": "region", "axis": "x", "from": 1, "to": 2}] * 61},
])
def test_strict_log_axis_validation_rejects_malformed_analysis(invalid):
    # The scatter base chart uses positive log10 x and y axes.
    with pytest.raises(ChartDataError):
        canonicalize_chart_data({**SPECS["scatter"], "analysis": invalid}, "scatter")


@pytest.mark.parametrize("kind", ["bar", "pie", "sankey", "heatmap"])
def test_analysis_is_not_silently_accepted_on_other_chart_types(kind):
    with pytest.raises(ChartDataError, match="only supported"):
        canonicalize_chart_analysis({"annotations": []}, kind)


@pytest.mark.parametrize("chart_type", sorted(SPECS))
@pytest.mark.parametrize("fmt,header", [
    ("svg", b"<svg"), ("pdf", b"%PDF-"), ("png", b"\x89PNG\r\n\x1a\n"),
])
def test_publication_export_keeps_editable_annotations_and_original_samples(chart_type, fmt, header):
    raw = deepcopy(SPECS[chart_type])
    canonical = canonicalize_chart_data({**raw, "analysis": OVERLAY}, chart_type)
    before = deepcopy(canonical)
    exported = render_chart_publication(
        chart_type, canonical, width_mm=85, height_mm=65, fmt=fmt,
    )
    assert header in (exported if fmt == "svg" else exported[:250])
    assert b"Chart rendering issue" not in exported
    assert canonical == before
    assert raw == SPECS[chart_type]
    if fmt == "svg":
        tree = ET.fromstring(exported)
        labels = " ".join("".join(el.itertext()) for el in tree.iter() if el.tag.endswith("}text"))
        assert "Peak response" in labels
    if fmt == "pdf":
        assert b"/ToUnicode" in exported


def test_chart_analysis_intent_save_reload_undo_redo_and_publication_round_trip():
    async def run():
        bus = SessionBus("analysis-regression")
        document = SceneDocument()
        notifications = NotificationState()
        bus.register_topic("scene", document.scene_payload)
        bus.register_topic("notification", notifications.payload)
        register_chart_intents(bus, document, notifications, object())
        source = document.add_chat_node(0, 0, "Source", is_user=True)
        base = canonicalize_chart_data(SPECS["scatter"], "scatter")
        chart = document.add_chart_node(400, 0, source.id, "scatter", base)
        initial = deepcopy(chart.state.chart_data)
        assert chart.state.chart_data == base

        await bus.dispatch_intent("scene", "setChartAnalysis", [chart.id, OVERLAY])
        assert chart.state.chart_data["analysis"]["annotations"][0]["text"] == "Peak response"
        assert {key: value for key, value in chart.state.chart_data.items() if key != "analysis"} == initial
        await bus.dispatch_intent("scene", "setChartAnalysis", [chart.id, OVERLAY])
        assert document.undo().command_type == "setChartAnalysis"
        assert document.nodes[chart.id].state.chart_data == initial
        assert document.redo().command_type == "setChartAnalysis"
        assert document.nodes[chart.id].state.chart_data["analysis"]["lockX"] is True

        saved = build_chat_data(document)
        notes = saved.pop("notes_data")
        pins = saved.pop("pins_data")
        restored = SceneDocument()
        restore_chat_into_document(restored, {"data": saved}, notes, pins)
        actual = next(n for n in restored.nodes.values() if n.kind == "chart")
        assert actual.state.chart_data == document.nodes[chart.id].state.chart_data

        pub = await bus.dispatch_intent("scene", "exportPublicationChart", [
            chart.id, {"format": "svg", "size": "single"},
        ])
        assert pub["mimeType"] == "image/svg+xml"
        import base64
        assert b"Peak response" in base64.b64decode(pub["base64"])

    asyncio.run(run())
