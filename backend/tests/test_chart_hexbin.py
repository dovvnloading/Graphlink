"""Hexbin validation, data fidelity and publication export."""
from copy import deepcopy
from xml.etree import ElementTree as ET

import pytest

from graphlink_chart_data import ChartDataError, canonicalize_chart_data
from graphlink_chart_rendering import render_chart_publication

SOURCE = {"type": "scatter", "xValues": [0, 1, 2, 3, 4],
          "values": [1, 2, 2, 5, 4], "analysis": {"density": "hexbin"}}


def test_hexbin_contract_is_roundtrip_and_preserves_measurements():
    original = deepcopy(SOURCE)
    spec = canonicalize_chart_data(SOURCE)
    assert spec["analysis"]["density"] == "hexbin"
    assert spec["values"] == SOURCE["values"]
    assert canonicalize_chart_data(spec) == spec
    assert SOURCE == original


@pytest.mark.parametrize("change", [
    {"type": "line", "labels": ["a", "b", "c", "d", "e"]},
    {"xScale": "log10", "xValues": [1, 2, 3, 4, 5]},
    {"series": [{"name": "A", "values": [1, 2, 3, 4, 5]},
                {"name": "B", "values": [2, 3, 4, 5, 6]}], "values": None},
    {"analysis": {"density": "unknown"}},
])
def test_invalid_hexbin_sources_are_rejected(change):
    spec = {**SOURCE, **change}
    if "values" in change and change["values"] is None:
        spec.pop("values", None)
    with pytest.raises(ChartDataError):
        canonicalize_chart_data(spec)


@pytest.mark.parametrize("fmt,signature", [("svg", b"<svg"), ("pdf", b"%PDF"), ("png", b"\x89PNG")])
def test_hexbin_publication_formats(fmt, signature):
    spec = canonicalize_chart_data(SOURCE)
    before = deepcopy(spec)
    data = render_chart_publication("scatter", spec, width_mm=180, height_mm=100, fmt=fmt)
    assert signature in data[:300]
    assert b"Chart rendering issue" not in data
    assert spec == before
    if fmt == "svg":
        root = ET.fromstring(data)
        labels = " ".join("".join(item.itertext()) for item in root.iter() if item.tag.endswith("}text"))
        assert "Hexbin density" in labels
        assert "Observations per bin" in labels


def test_locked_hexbin_export_filters_out_offscreen_observations(monkeypatch):
    from matplotlib.axes import Axes

    captured = []
    original_hexbin = Axes.hexbin

    def recording_hexbin(self, xs, ys, *args, **kwargs):
        captured.append((list(xs), list(ys)))
        return original_hexbin(self, xs, ys, *args, **kwargs)

    monkeypatch.setattr(Axes, "hexbin", recording_hexbin)
    raw = {**SOURCE, "analysis": {
        "density": "hexbin", "lockX": True, "xRange": [1.5, 3.5],
        "lockY": True, "yRange": [1.5, 2.5],
    }}
    rendered = render_chart_publication("scatter", canonicalize_chart_data(raw),
                                        width_mm=180, height_mm=100, fmt="svg")
    assert b"Chart rendering issue" not in rendered
    assert captured == [([2.0], [2.0])]


def test_locked_hexbin_empty_viewport_is_a_valid_empty_render(monkeypatch):
    from matplotlib.axes import Axes

    def fail_if_called(self, *args, **kwargs):
        raise AssertionError("No invisible observations may be counted")

    monkeypatch.setattr(Axes, "hexbin", fail_if_called)
    raw = {**SOURCE, "analysis": {
        "density": "hexbin", "lockX": True, "xRange": [10, 20],
    }}
    rendered = render_chart_publication("scatter", canonicalize_chart_data(raw),
                                        width_mm=180, height_mm=100, fmt="svg")
    assert b"No observations in selected range" in rendered
    assert b"Chart rendering issue" not in rendered
