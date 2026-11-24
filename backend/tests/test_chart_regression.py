"""Numerical and export parity for opt-in scientific chart fit layers."""
from copy import deepcopy
from xml.etree import ElementTree as ET

import pytest

from graphlink_chart_data import ChartDataError, canonicalize_chart_data, chart_regression_fits
from graphlink_chart_rendering import render_chart_publication


SOURCE = {"type": "scatter", "xValues": [0, 1, 2, 3, 4], "values": [1, 3, 2, 5, 4]}
OVERLAY = {"regression": "ols95"}


def test_ols_reference_and_canonical_round_trip():
    raw = {**deepcopy(SOURCE), "analysis": OVERLAY}
    snapshot = deepcopy(raw)
    spec = canonicalize_chart_data(raw)
    fit = chart_regression_fits(spec)[0]
    assert fit["r2"] == pytest.approx(0.64, abs=1e-12)
    assert len(fit["points"]) == 65
    # Independent SciPy linregress and Student-t quantile reference.
    assert fit["points"][0][1] == pytest.approx(1.4)
    assert fit["points"][0][3] == pytest.approx(4.10039524, abs=1e-7)
    assert fit["points"][32][2] == pytest.approx(1.44092608, abs=1e-7)
    assert fit["points"][64][1] == pytest.approx(4.6)
    assert spec["values"] == SOURCE["values"]
    assert canonicalize_chart_data(spec) == spec
    assert raw == snapshot
    assert "analysis" not in canonicalize_chart_data(SOURCE)


@pytest.mark.parametrize("raw", [
    {**SOURCE, "xScale": "log10", "xValues": [1, 2, 3, 4, 5]},
    {**SOURCE, "xValues": [1, 1, 1, 1, 1]},
    {"type": "line", "labels": ["a", "b", "c"], "values": [1, 2, 3]},
    {**SOURCE, "xValues": [1, 2], "values": [3, 4]},
])
def test_invalid_or_degenerate_fit_is_rejected(raw):
    with pytest.raises(ChartDataError, match="Linear regression"):
        canonicalize_chart_data({**raw, "analysis": OVERLAY})


def test_constant_y_and_multiple_series():
    spec = canonicalize_chart_data({
        "type": "scatter", "xValues": [0, 1, 2],
        "series": [{"name": "flat", "values": [7, 7, 7]}, {"name": "slope", "values": [1, 3, 5]}],
        "analysis": OVERLAY,
    })
    flat, slope = chart_regression_fits(spec)
    assert flat["r2"] is None
    assert flat["points"][32] == (1, 7, 7, 7)
    assert slope["r2"] == pytest.approx(1)
    assert slope["points"][-1][1] == pytest.approx(5)


@pytest.mark.parametrize("fmt,signature", [("svg", b"<svg"), ("pdf", b"%PDF"), ("png", b"\x89PNG")])
def test_export_keeps_fit_and_source(fmt, signature):
    spec = canonicalize_chart_data({**SOURCE, "analysis": OVERLAY})
    original = deepcopy(spec)
    exported = render_chart_publication("scatter", spec, width_mm=180, height_mm=100, fmt=fmt)
    assert signature in exported[:300]
    assert b"Chart rendering issue" not in exported
    assert spec == original
    if fmt == "svg":
        root = ET.fromstring(exported)
        labels = " ".join("".join(e.itertext()) for e in root.iter() if e.tag.endswith("}text"))
        assert "95% mean CI" in labels
        assert "OLS" in labels
