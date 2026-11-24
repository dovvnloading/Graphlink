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
