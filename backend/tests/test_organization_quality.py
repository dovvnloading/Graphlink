"""Objective organization quality smoke gates for the #476 engine program."""

from __future__ import annotations

import math

from backend.canvas import SceneDocument
from backend.domain.layout_invariants import visual_membership_violations
from backend.domain.organization import make_organization_plan
from backend.tests.perf.graph_factory import SMALL, TYPICAL
from backend.tests.perf.measure_organization import layout_quality, measure


def test_layout_quality_has_finite_reproducible_nonnegative_metrics() -> None:
    doc = SMALL.build()
    before = len(doc.command_log)
    plan = make_organization_plan(doc)
    metrics = layout_quality(doc, plan)
    assert len(doc.command_log) == before
    for key in (
        "diagram_area", "edge_crossings", "mean_edge_length",
        "p95_edge_length", "mean_displacement", "p95_displacement",
        "moved_node_count",
    ):
        assert math.isfinite(metrics[key])
        assert metrics[key] >= 0
    assert metrics["diagram_area"] > 0
    assert metrics["mean_edge_length"] > 0
    assert metrics["moved_node_count"] == len(plan.changed_ids)


def test_layout_benchmark_smoke_repeats_on_real_graph_factory() -> None:
    result = measure(TYPICAL, repeats=2)
    assert result["nodes"] == TYPICAL.node_count
    assert isinstance(result["edges"], int) and result["edges"] > 0
    for field in ("preview_p50_ms", "preview_p95_ms", "preview_peak_mib"):
        value = result[field]
        assert isinstance(value, (int, float)) and value >= 0
    assert result["preview_peak_mib"] != 0


def test_layout_quality_counts_no_phantom_group_membership() -> None:
    doc = SceneDocument()
    root = doc.add_chat_node(-1000, 0, "root", True)
    child = doc.add_chat_node(1000, 0, "child", False, parent_id=root.id)
    outsider = doc.add_chat_node(0, 0, "outsider", True)
    peer = doc.add_chat_node(1400, 0, "group peer", True)
    frame = doc.create_frame([child.id, peer.id])
    plan = make_organization_plan(doc)
    import copy
    projected = copy.copy(doc)
    projected.nodes = plan.nodes
    assert (frame.id, outsider.id) not in visual_membership_violations(projected)
    assert (frame.id, root.id) not in visual_membership_violations(projected)
