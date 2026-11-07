"""Compound organization quality gates beyond content-card overlap (#473/#476)."""

from __future__ import annotations

import copy

import pytest
from hypothesis import given, settings, strategies as st

from backend.canvas import SceneDocument
from backend.domain.model import SceneError
from backend.domain.node_states import ContainerState
from backend.domain.organization import apply_organization_plan, make_organization_plan
from backend.tests.perf.graph_factory import build_graph
from backend.tests.perf.measure_organization import layout_quality


def test_layout_quality_metrics_include_crossings_area_and_displacement() -> None:
    doc = build_graph(16, extra_edges=4)
    plan = make_organization_plan(doc)
    metrics = layout_quality(doc, plan)
    assert metrics["diagram_area"] > 0
    assert metrics["edge_crossings"] >= 0
    assert metrics["mean_edge_length"] > 0
    assert metrics["p95_displacement"] >= 0
    assert metrics["moved_node_count"] == len(plan.changed_ids)


@settings(max_examples=45, deadline=None)
@given(xs=st.lists(st.integers(min_value=-3000, max_value=3000), min_size=5, max_size=5))
def test_group_membership_property_layout_or_rejects_without_mutation(xs: list[int]) -> None:
    doc = SceneDocument()
    a = doc.add_chat_node(xs[0], 0, "A", True)
    framed_child = doc.add_chat_node(xs[1], 0, "child", False, parent_id=a.id)
    unrelated = doc.add_chat_node(xs[2], 0, "outsider", True)
    b = doc.add_chat_node(xs[3], 0, "B", True)
    other = doc.add_chat_node(xs[4], 0, "other", True)
    frame = doc.create_frame([framed_child.id, b.id])
    container = doc.create_container([framed_child.id, other.id])
    before_nodes = copy.deepcopy(doc.nodes)
    before_edges = copy.deepcopy(doc.edges)
    try:
        plan = make_organization_plan(doc, "all")
    except SceneError:
        # A physically impossible compound constraint must fail on the
        # read-only shadow instead of publishing a visually false group.
        assert doc.nodes == before_nodes
        assert doc.edges == before_edges
        return

    assert doc.nodes == before_nodes
    assert doc.edges == before_edges
    apply_organization_plan(doc, plan)
    assert doc.edges == before_edges
    assert frame.item_ids == [framed_child.id, b.id]
    assert container.item_ids == [framed_child.id, other.id]
    assert unrelated.id in doc.nodes
    assert all(group.id in doc.nodes for group in (frame, container))


def test_deep_container_dependency_order_is_iterative() -> None:
    doc = SceneDocument()
    member = doc.add_chat_node(0, 0, "deep", True)
    innermost = doc.create_container([member.id])
    latest = innermost
    # Exceed CPython's recursion default: old nesting_height() could never
    # organize this legitimate container hierarchy safely.
    for _ in range(1020):
        latest = doc.create_container([latest.id])
    plan = make_organization_plan(doc, "all")
    assert latest.id in plan.nodes
    assert innermost.id in plan.nodes
    assert isinstance(latest.state, ContainerState)
    assert latest.state.group_width is not None
    apply_organization_plan(doc, plan)
    assert latest.id in doc.nodes


def test_cyclic_legacy_group_membership_is_rejected_atomically() -> None:
    doc = SceneDocument()
    leaf = doc.add_chat_node(0, 0, "leaf", True)
    first = doc.create_container([leaf.id])
    second = doc.create_container([first.id])
    first.item_ids = [second.id]  # malformed restored cycle
    before = {nid: (node.x, node.y) for nid, node in doc.nodes.items()}
    with pytest.raises(SceneError, match="cyclic"):
        make_organization_plan(doc)
    assert {nid: (node.x, node.y) for nid, node in doc.nodes.items()} == before
