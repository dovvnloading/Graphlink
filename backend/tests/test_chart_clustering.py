"""Deterministic hierarchical heatmap clustering, fidelity and exports."""
from copy import deepcopy
from xml.etree import ElementTree as ET

import pytest

from graphlink_chart_data import (ChartDataError, canonicalize_chart_data,
                                  heatmap_cluster_axis, heatmap_cluster_plan)
from graphlink_chart_rendering import render_chart_publication

MATRIX = [
    [0, 0, 0.1, 0.2],
    [9, 9, 8.9, 9.1],
    [0.1, 0, 0, 0.2],
    [9.2, 9.2, 9.1, 9],
]
SOURCE = {"type": "heatmap", "matrix": MATRIX,
          "rowLabels": ["A", "B", "C", "D"],
          "columnLabels": ["T1", "T2", "T3", "T4"],
          "analysis": {"clustering": "both"}}


def test_clustering_keeps_original_matrix_and_pairwise_branches():
    original = deepcopy(SOURCE)
    spec = canonicalize_chart_data(SOURCE)
    plan = heatmap_cluster_plan(spec["matrix"], "both")
    assert plan["rowOrder"] == [0, 2, 1, 3]
    assert sorted(plan["columnOrder"]) == [0, 1, 2, 3]
    assert len(plan["rowOrder"]) == len(MATRIX)
    assert canonicalize_chart_data(spec) == spec
    assert spec["matrix"] == MATRIX
    assert SOURCE == original
    assert heatmap_cluster_plan(MATRIX)["rowOrder"] == [0, 1, 2, 3]


@pytest.mark.parametrize("factor", [1e-150, 1e150])
def test_near_extreme_finite_values(factor):
    plan = heatmap_cluster_axis([[0], [factor], [factor * 1.1], [factor * 0.1]])
    assert plan["order"] == [0, 3, 1, 2]


@pytest.mark.parametrize("analysis", [
    {"clustering": "ward"},
    {"clustering": "rows", "lockX": True, "xRange": [0, 4]},
    {"clustering": "both", "annotations": [{"kind": "label", "x": 1, "y": 2, "text": "x"}]},
])
def test_invalid_heatmap_analysis_is_rejected(analysis):
    with pytest.raises(ChartDataError):
        canonicalize_chart_data({**SOURCE, "analysis": analysis})


@pytest.mark.parametrize("fmt,signature", [("svg", b"<svg"), ("pdf", b"%PDF"), ("png", b"\x89PNG")])
def test_clustered_heatmap_export(fmt, signature):
    spec = canonicalize_chart_data(SOURCE)
    before = deepcopy(spec)
    exported = render_chart_publication("heatmap", spec, width_mm=180, height_mm=100, fmt=fmt)
    assert signature in exported[:300]
    assert b"Chart rendering issue" not in exported
    assert spec == before
    if fmt == "svg":
        labels = " ".join("".join(el.itertext()) for el in ET.fromstring(exported).iter()
                          if el.tag.endswith("}text"))
        assert "clustered heatmap" in labels
