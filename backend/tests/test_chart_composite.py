"""ChartSpec V2 composable figures: numerical fidelity, security, export, persistence."""
from copy import deepcopy
from xml.etree import ElementTree as ET

import pytest

from graphlink_chart_data import ChartDataError, CHART_JSON_SCHEMAS, canonicalize_chart_data
from graphlink_chart_rendering import render_chart_publication
from backend.domain.graph import SceneDocument
from backend.session_save import build_chat_data
from backend.session_load import restore_chat_into_document

RAW = {
    "type": "composite", "version": 2, "title": "Experiment A",
    "xAxis": "Elapsed time", "yAxis": "Response", "xUnit": "hours", "yUnit": "AU",
    "composite": {
        "numbers": {
            "time": [0, 1, 2, 3, 4, 5],
            "response": [1.2, 1.8, 2.1, 2.5, 3.0, 3.1],
            "low": [0.9, 1.5, 1.8, 2.2, 2.7, 2.8],
            "high": [1.5, 2.1, 2.4, 2.8, 3.3, 3.4],
        },
        "categories": {"treatment": ["A", "A", "A", "B", "B", "B"]},
        "facetBy": "treatment",
        "layers": [
            {"mark": "band", "x": "time", "y": "low", "y2": "high",
             "colorBy": "treatment", "label": "Uncertainty"},
            {"mark": "line", "x": "time", "y": "response",
             "colorBy": "treatment", "label": "Trend"},
            {"mark": "point", "x": "time", "y": "response", "colorBy": "treatment"},
        ],
    },
}


def test_v2_composition_is_canonical_idempotent_and_nonmutating():
    original = deepcopy(RAW)
    canonical = canonicalize_chart_data(RAW, "composite")
    assert canonical["version"] == 2
    assert canonical["type"] == "composite"
    assert canonical["composite"]["numbers"] == RAW["composite"]["numbers"]
    assert canonical["composite"]["layers"] == RAW["composite"]["layers"]
    assert canonical["composite"]["categories"] == RAW["composite"]["categories"]
    assert canonical["composite"]["facetBy"] == "treatment"
    assert canonicalize_chart_data(canonical, "composite") == canonical
    assert RAW == original
    assert canonicalize_chart_data({"type": "line", "labels": ["a", "b"], "values": [2, 3]})["version"] == 1


def test_v2_generation_schema_describes_composable_marks():
    schema = CHART_JSON_SCHEMAS["composite"]
    assert schema["required"] == ["type", "composite"]
    assert schema["properties"]["composite"]["properties"]["layers"]["items"]["properties"]["mark"]["enum"] == [
        "point", "line", "band", "area",
    ]


@pytest.mark.parametrize("fmt,signature", [
    ("svg", b"<svg"), ("pdf", b"%PDF"), ("png", b"\x89PNG"),
])
def test_composite_figures_export_all_original_data_in_multiple_facets(fmt, signature):
    spec = canonicalize_chart_data(RAW)
    before = deepcopy(spec)
    result = render_chart_publication("composite", spec, width_mm=180, height_mm=100, fmt=fmt)
    assert signature in result[:300]
    assert b"Chart rendering issue" not in result
    assert spec == before
    if fmt == "svg":
        root = ET.fromstring(result)
        text = " ".join("".join(item.itertext()) for item in root.iter()
                        if item.tag.endswith("}text"))
        assert "Experiment A" in text
        assert "treatment: A" in text
        assert "treatment: B" in text
        assert "Uncertainty" in text


@pytest.mark.parametrize("bad", [
    {"version": 1},
    {"composite": {"numbers": {"x": [0, 1]}, "layers": [{"mark": "point", "x": "x", "y": "x"}]}},
    {"composite": {"numbers": {"x": [0, 1], "y": [1]}, "layers": [{"mark": "point", "x": "x", "y": "y"}]}},
    {"composite": {"numbers": {"x": [0, 1], "y": [1, float("nan")]},
                   "layers": [{"mark": "point", "x": "x", "y": "y"}]}},
    {"composite": {"numbers": {"x": [0, 1], "y": [0, 1]},
                   "layers": [{"mark": "javascript", "x": "x", "y": "y"}]}},
    {"composite": {"numbers": {"x": [0, 1], "y": [0, 1]},
                   "layers": [{"mark": "point", "x": "missing", "y": "y"}]}},
    {"composite": {"numbers": {"x": [0, 1], "y": [0, 1]},
                   "layers": [{"mark": "point", "x": "x", "y": "y", "url": "https://bad.example"}]}},
    {"composite": {"numbers": {"x": [0, 1], "y": [0, 1], "__proto__": [0, 1]},
                   "layers": [{"mark": "point", "x": "x", "y": "y"}]}},
    {"composite": {"numbers": {"x": [0, 1], "low": [2, 3], "upper": [1, 2]},
                   "layers": [{"mark": "band", "x": "x", "y": "low", "y2": "upper"}]}},
    {"xScale": "log10"},
    {"yScale": "log10", "composite": {"numbers": {"x": [1, 2], "y": [-1, 2]},
                                      "layers": [{"mark": "point", "x": "x", "y": "y"}]}},
    {"composite": {"numbers": {"x": list(range(2001)), "y": list(range(2001))},
                   "layers": [{"mark": "point", "x": "x", "y": "y"}]}},
])
def test_rejects_invalid_or_insecure_v2_specs(bad):
    raw = deepcopy(RAW)
    raw.update(bad)
    if "composite" in bad:
        raw["composite"] = bad["composite"]
    with pytest.raises(ChartDataError):
        canonicalize_chart_data(raw, "composite")


def test_v2_can_render_multiple_layer_families_on_the_same_axis():
    raw = deepcopy(RAW)
    raw["composite"]["facetBy"] = None
    raw["composite"].pop("facetBy")
    raw["composite"]["layers"].append({
        "mark": "area", "x": "time", "y": "response",
    })
    result = render_chart_publication("composite", canonicalize_chart_data(raw),
                                      width_mm=180, height_mm=100, fmt="svg")
    assert b"Chart rendering issue" not in result


def test_v2_save_reload_fidelity():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "Original", is_user=True)
    canonical = canonicalize_chart_data(RAW)
    doc.add_chart_node(500, 0, parent.id, "composite", canonical)
    saved = build_chat_data(doc)
    notes = saved.pop("notes_data")
    pins = saved.pop("pins_data")
    restored = SceneDocument()
    restore_chat_into_document(restored, {"data": saved}, notes, pins)
    restored_chart = next(node for node in restored.nodes.values() if node.kind == "chart")
    assert restored_chart.state.chart_type == "composite"
    assert restored_chart.state.chart_data == canonical

def test_v2_shared_palette_is_field_aware_and_bounded():
    raw = {
        "type": "composite", "version": 2, "composite": {
            "numbers": {"x": list(range(4)), "y": [1, 2, 3, 4]},
            "categories": {"treatment": ["A", "B", "C", "D"], "batch": ["A", "B", "C", "D"]},
            "layers": [
                {"mark": "point", "x": "x", "y": "y", "colorBy": "treatment"},
                {"mark": "point", "x": "x", "y": "y", "colorBy": "batch"},
                {"mark": "line", "x": "x", "y": "y", "colorBy": "treatment"},
            ],
        },
    }
    canonical = canonicalize_chart_data(raw)
    assert canonicalize_chart_data(canonical) == canonical
    result = render_chart_publication("composite", canonical, width_mm=180, height_mm=100, fmt="svg")
    assert b"Chart rendering issue" not in result
    root = ET.fromstring(result)
    text = " ".join("".join(item.itertext()) for item in root.iter() if item.tag.endswith("}text"))
    for field in ("treatment", "batch"):
        for value in ("A", "B", "C", "D"):
            assert f"{field}: {value}" in text
    # Repeated field bindings share slots, but an ungrouped layer needs one.
    raw["composite"]["layers"].append({"mark": "area", "x": "x", "y": "y"})
    with pytest.raises(ChartDataError, match="eight color legend entries"):
        canonicalize_chart_data(raw)


def test_v2_rejects_too_many_groups_across_color_fields():
    raw = {
        "type": "composite", "composite": {
            "numbers": {"x": list(range(5)), "y": list(range(5))},
            "categories": {"treatment": list("ABCDE"), "batch": list("ABCDE")},
            "layers": [
                {"mark": "point", "x": "x", "y": "y", "colorBy": "treatment"},
                {"mark": "point", "x": "x", "y": "y", "colorBy": "batch"},
            ],
        },
    }
    with pytest.raises(ChartDataError, match="eight color legend entries"):
        canonicalize_chart_data(raw)
