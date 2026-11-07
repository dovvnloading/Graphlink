"""Regression and property gates for organization geometry safety (#475/#477)."""

from __future__ import annotations

import math
import copy

import pytest
from hypothesis import given, settings, strategies as st

from backend.canvas import SceneDocument
from backend.domain.geometry_safety import MAX_MEASURED_DIMENSION, validate_layout_geometry
from backend.domain.model import SceneError
from backend.domain.organization import make_organization_plan, apply_organization_plan


def test_non_finite_report_does_not_corrupt_layout() -> None:
    doc = SceneDocument()
    root = doc.add_chat_node(0, 0, "root", True)
    for value in (float("inf"), float("-inf"), float("nan"), 0, -5, 1e300, "not a number"):
        assert not doc.set_measured_node_sizes([(root.id, value, 500)])
        assert root.id not in doc.measured_sizes
    doc.organize()
    assert math.isfinite(root.x) and math.isfinite(root.y)
    assert all(map(math.isfinite, doc.node_footprint(root)))


@settings(max_examples=65, deadline=None)
@given(size=st.floats(allow_nan=True, allow_infinity=True, width=64))
def test_measured_report_property_never_stores_invalid_sizes(size: float) -> None:
    doc = SceneDocument()
    node = doc.add_chat_node(0, 0, "probe", True)
    doc.set_measured_node_sizes([(node.id, size, 500)])
    expected_valid = math.isfinite(size) and 0 < size <= MAX_MEASURED_DIMENSION
    assert (node.id in doc.measured_sizes) is expected_valid
    if expected_valid:
        assert doc.measured_sizes[node.id] == (size, 500)


def test_size_report_skips_invalid_entries_without_undoing_valid_ones() -> None:
    doc = SceneDocument()
    a = doc.add_chat_node(0, 0, "a", True)
    b = doc.add_chat_node(500, 0, "b", True)
    c = doc.add_chat_node(1000, 0, "c", True)
    doc.set_measured_node_sizes([
        (a.id, float("nan"), 550), (b.id, 500, 1000),
        (c.id, float("inf"), 300),
    ])
    assert a.id not in doc.measured_sizes and c.id not in doc.measured_sizes
    assert doc.measured_sizes[b.id] == (500, 1000)
    assert all(map(math.isfinite, doc.node_footprint(b)))


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), 1e300])
def test_single_and_batch_moves_reject_nonfinite_without_partial_mutation(invalid: float) -> None:
    doc = SceneDocument()
    first = doc.add_chat_node(0, 0, "a", True)
    second = doc.add_chat_node(500, 0, "b", True)
    before = copy.deepcopy(doc.nodes)
    with pytest.raises(SceneError, match="finite"):
        doc.move_node(first.id, invalid, 10)
    assert doc.nodes == before
    with pytest.raises(SceneError, match="finite"):
        doc.move_nodes([(first.id, 400, 300), (second.id, invalid, 0)])
    assert doc.nodes == before


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), -1, 0, 1e300])
def test_frame_and_chart_resize_reject_invalid_sizes_atomically(invalid: float) -> None:
    doc = SceneDocument()
    leaf = doc.add_chat_node(0, 0, "leaf", True)
    frame = doc.create_frame([leaf.id])
    chart = doc.add_chart_node(500, 0, leaf.id, "bar", {"labels": ["x"], "values": [1]})
    before = copy.deepcopy(doc.nodes)
    with pytest.raises(SceneError, match="finite"):
        doc.resize_frame(frame.id, invalid, 800)
    with pytest.raises(SceneError, match="finite"):
        doc.resize_chart(chart.id, 1000, invalid)
    assert doc.nodes == before


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), 1e300])
def test_invalid_existing_scene_fails_to_preview_or_organize_without_writes(invalid: float) -> None:
    doc = SceneDocument()
    root = doc.add_chat_node(0, 0, "root", True)
    child = doc.add_chat_node(300, 0, "child", False, parent_id=root.id)
    child.x = invalid  # simulate a legacy/plugin-authored corrupt coordinate
    before = {node.id: (node.x, node.y) for node in doc.nodes.values()}
    with pytest.raises(SceneError, match="finite"):
        make_organization_plan(doc)
    with pytest.raises(SceneError, match="finite"):
        doc.organize()
    for node in doc.nodes.values():
        expected_x, expected_y = before[node.id]
        if math.isnan(expected_x):
            assert math.isnan(node.x)
        else:
            assert node.x == expected_x
        assert node.y == expected_y


def test_corrupted_stored_measurement_is_rejected_during_preview() -> None:
    doc = SceneDocument()
    node = doc.add_chat_node(0, 0, "root", True)
    doc.measured_sizes[node.id] = (float("inf"), 200)
    with pytest.raises(SceneError, match="invalid measured geometry"):
        make_organization_plan(doc)


def test_chart_resize_immediately_refits_nested_groups_and_preview() -> None:
    doc = SceneDocument()
    chat = doc.add_chat_node(0, 0, "chat", True)
    chart = doc.add_chart_node(0, 500, chat.id, "bar", {"labels": ["x"], "values": [1]})
    frame = doc.create_frame([chart.id])
    outer = doc.create_container([frame.id])
    old_chart_size = doc.node_footprint(chart)
    doc.set_measured_node_sizes([(chart.id, *old_chart_size)])
    doc.resize_chart(chart.id, 1600, 1000)

    actual = (chart.state.chart_width, chart.state.chart_height)
    assert actual != old_chart_size
    assert doc.node_footprint(chart) == actual
    assert doc._member_footprint(chart) == actual
    assert frame.x + frame.state.group_width >= chart.x + actual[0]
    assert outer.x + outer.state.group_width >= frame.x + frame.state.group_width
    plan = make_organization_plan(doc)
    for candidate in plan.nodes.values():
        assert math.isfinite(candidate.x) and math.isfinite(candidate.y)


def test_tiny_extreme_chart_aspect_ratio_never_divides_by_zero() -> None:
    doc = SceneDocument()
    root = doc.add_chat_node(0, 0, "root", True)
    chart = doc.add_chart_node(600, 0, root.id, "bar", {"labels": ["x"], "values": [1]})
    doc.resize_chart(chart.id, 1e-320, 1e12)
    validate_layout_geometry(doc)
    doc.resize_chart(chart.id, 1e12, 1e-320)
    validate_layout_geometry(doc)


def test_preview_plan_apply_rechecks_invalid_projection_without_modifying_scene() -> None:
    doc = SceneDocument()
    root = doc.add_chat_node(450, 800, "root", True)
    doc.add_chat_node(500, 1300, "child", False, parent_id=root.id)
    plan = make_organization_plan(doc)
    before = {nid: (n.x, n.y) for nid, n in doc.nodes.items()}
    assert plan.changed_ids
    plan.nodes[next(iter(plan.changed_ids))].x = float("nan")
    with pytest.raises(SceneError, match="non-finite"):
        apply_organization_plan(doc, plan)
    assert {nid: (n.x, n.y) for nid, n in doc.nodes.items()} == before
