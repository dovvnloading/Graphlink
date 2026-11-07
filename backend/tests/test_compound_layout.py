"""Compound layout and semantic group boundary regression suite (#473).

Every failing preview stays read-only. When a group contains descendants of
different structural trees, content is packed into disjoint compound islands
so unrelated roots/parents cannot be painted inside its background.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given, settings, strategies as st

from backend.canvas import SceneDocument
from backend.domain.layout_invariants import (
    validate_no_new_false_containment,
    visual_membership_violations,
)
from backend.domain.model import SceneError
from backend.domain.organization import (
    apply_organization_plan,
    make_organization_plan,
)


def _cross_tree_frame() -> tuple[SceneDocument, str, str, str, str]:
    doc = SceneDocument()
    ancestor = doc.add_chat_node(-2000, 0, "root A", True)
    descendant = doc.add_chat_node(1800, 0, "framed descendant", False, parent_id=ancestor.id)
    unrelated = doc.add_chat_node(0, 0, "unrelated root", True)
    peer = doc.add_chat_node(2000, 0, "framed root", True)
    group = doc.create_frame([descendant.id, peer.id])
    return doc, ancestor.id, descendant.id, unrelated.id, group.id


def test_issue_473_frame_with_members_across_two_structural_trees() -> None:
    doc, ancestor, descendant, unrelated, group_id = _cross_tree_frame()
    before = copy.deepcopy(doc.nodes)
    before_edges = copy.deepcopy(doc.edges)
    before_sizes = dict(doc.measured_sizes)
    assert (group_id, unrelated) not in visual_membership_violations(doc)
    assert (group_id, ancestor) not in visual_membership_violations(doc)

    plan = make_organization_plan(doc, "all")
    assert doc.nodes == before, "a preview cannot mutate the original graph"
    assert doc.edges == before_edges
    assert doc.measured_sizes == before_sizes, "fallback observations are shadow-only"
    projected = copy.copy(doc)
    projected.nodes = plan.nodes
    assert (group_id, unrelated) not in visual_membership_violations(projected)
    assert (group_id, ancestor) not in visual_membership_violations(projected)

    apply_organization_plan(doc, plan)
    assert doc.measured_sizes == before_sizes
    assert not visual_membership_violations(doc)
    group = doc.nodes[group_id]
    assert descendant in group.item_ids and len(group.item_ids) == 2
    # The group's own footprint must enclose real placement-size cards,
    # not only GroupOps' smaller historical no-measurement placeholders.
    gx, gy, gw, gh = group.x, group.y, *doc.node_footprint(group)
    for mid in group.item_ids:
        member = doc.nodes[mid]
        mw, mh = doc.node_footprint(member)
        assert gx <= member.x and gy <= member.y
        assert member.x + mw <= gx + gw
        assert member.y + mh <= gy + gh
    assert doc.edges == before_edges
    assert any(
        edge.source == ancestor and edge.target == descendant
        for edge in doc.edges.values()
    )
    after = {nid: (n.x, n.y) for nid, n in doc.nodes.items()}
    doc.organize()
    assert after == {nid: (n.x, n.y) for nid, n in doc.nodes.items()}


def test_cross_tree_nested_container_refits_without_engulfing_bystanders() -> None:
    doc = SceneDocument()
    parent = doc.add_chat_node(-1800, 0, "parent", True)
    leaf = doc.add_chat_node(1000, 0, "member", False, parent_id=parent.id)
    outsider = doc.add_chat_node(0, 0, "bystander", True)
    another = doc.add_chat_node(1400, 0, "another group member", True)
    frame = doc.create_frame([leaf.id])
    outer = doc.create_container([frame.id, another.id])
    before_edges = copy.deepcopy(doc.edges)
    before_members = (list(frame.item_ids), list(outer.item_ids))

    plan = make_organization_plan(doc, "all")
    apply_organization_plan(doc, plan)
    assert (outer.id, outsider.id) not in visual_membership_violations(doc)
    assert (outer.id, parent.id) not in visual_membership_violations(doc)
    assert not visual_membership_violations(doc)
    assert doc.edges == before_edges
    assert (frame.item_ids, outer.item_ids) == before_members
    assert outer.x <= frame.x
    assert outer.y <= frame.y


def test_dual_membership_hinge_preserves_both_frame_and_container_semantics() -> None:
    doc = SceneDocument()
    primary_root = doc.add_chat_node(-1000, 0, "parent", True)
    shared = doc.add_chat_node(50, 0, "hinge", False, parent_id=primary_root.id)
    frame_only = doc.add_chat_node(500, 0, "frame exclusive", True)
    container_only = doc.add_chat_node(1000, 0, "container exclusive", True)
    frame = doc.create_frame([shared.id, frame_only.id])
    container = doc.create_container([shared.id, container_only.id])

    doc.organize()
    violations = visual_membership_violations(doc)
    assert (frame.id, container_only.id) not in violations
    assert (container.id, frame_only.id) not in violations
    assert (frame.id, primary_root.id) not in violations
    assert (container.id, primary_root.id) not in violations


def test_new_false_group_membership_rejects_projection_atomically() -> None:
    doc = SceneDocument()
    a = doc.add_chat_node(0, 0, "a", True)
    b = doc.add_chat_node(1000, 0, "b", True)
    outsider = doc.add_chat_node(2000, 0, "outside", True)
    frame = doc.create_frame([a.id, b.id])
    before = copy.deepcopy(doc.nodes)
    projected = copy.copy(doc)
    projected.nodes = copy.deepcopy(doc.nodes)
    projected.nodes[outsider.id].x = frame.x + 40
    projected.nodes[outsider.id].y = frame.y + 60
    with pytest.raises(SceneError, match="visually enclose unrelated"):
        validate_no_new_false_containment(doc, projected)
    assert doc.nodes == before


@settings(max_examples=40, deadline=None)
@given(
    xs=st.lists(st.integers(min_value=-3000, max_value=3000), min_size=4, max_size=4),
)
def test_random_cross_tree_group_island_containment(xs: list[int]) -> None:
    doc = SceneDocument()
    parent = doc.add_chat_node(xs[0], 0, "parent", True)
    member = doc.add_chat_node(xs[1], 0, "child member", False, parent_id=parent.id)
    outsider = doc.add_chat_node(xs[2], 0, "bystander", True)
    separate = doc.add_chat_node(xs[3], 0, "separate member", True)
    group = doc.create_container([member.id, separate.id])
    before = copy.deepcopy(doc.nodes)
    before_edges = copy.deepcopy(doc.edges)

    plan = make_organization_plan(doc, "all")
    assert doc.nodes == before
    assert doc.edges == before_edges
    apply_organization_plan(doc, plan)
    violations = visual_membership_violations(doc)
    assert (group.id, parent.id) not in violations
    assert (group.id, outsider.id) not in violations
    assert doc.edges == before_edges
    assert group.item_ids == [member.id, separate.id]
    after = {nid: (n.x, n.y) for nid, n in doc.nodes.items()}
    doc.organize()
    assert after == {nid: (n.x, n.y) for nid, n in doc.nodes.items()}


def test_fixed_group_bounds_are_never_silently_refitted() -> None:
    """A fixed frame is a hard constraint even when its members are selected."""
    doc = SceneDocument()
    first = doc.add_chat_node(0, 0, "first", True)
    second = doc.add_chat_node(2500, 0, "second", True)
    frame = doc.create_frame([first.id, second.id])
    before = copy.deepcopy(doc.nodes)
    with pytest.raises(SceneError, match="fixed node"):
        make_organization_plan(
            doc, "selection", node_ids=[first.id, second.id],
            fixed_node_ids=[frame.id],
        )
    assert doc.nodes == before


def test_cross_tree_group_island_undo_redo_restores_exact_geometry() -> None:
    doc, _ancestor, _descendant, _unrelated, group_id = _cross_tree_frame()
    original = copy.deepcopy(doc.nodes)
    original_edges = copy.deepcopy(doc.edges)
    plan = make_organization_plan(doc)
    doc.record_command(
        "organizeNodes", "user",
        lambda: apply_organization_plan(doc, plan),
        node_ids=plan.changed_ids,
    )
    after = copy.deepcopy(doc.nodes)
    assert after != original
    assert not visual_membership_violations(doc)
    doc.undo()
    assert doc.nodes == original
    doc.redo()
    assert doc.nodes == after
    assert doc.edges == original_edges
    assert group_id in doc.nodes
