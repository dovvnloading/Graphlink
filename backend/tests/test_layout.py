"""LayoutOps (backend/domain/layout.py): spawn placement + organize.

Unit tests pin the placement shapes (below/right/left/above, footprint
fallback chain, scene-edge placement); the hypothesis properties pin the
two invariants the whole module exists for - a spawned child never lands
on an existing node, and organize() always produces an overlap-free,
deterministic layout - across arbitrary scene shapes, sizes, and edge
structures (including cycles and multi-parent nodes).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from backend.canvas import SceneDocument
from backend.domain.layout import (
    DEFAULT_FALLBACK_FOOTPRINT,
    KIND_FALLBACK_FOOTPRINTS,
    NODE_GAP_X,
    NODE_GAP_Y,
)
from backend.domain.model import (
    GROUP_COLLAPSED_HEIGHT,
    GROUP_COLLAPSED_WIDTH,
    GROUP_PADDING,
    GROUP_PADDING_TOP,
    SceneError,
)


def _rects_overlap(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)


def _assert_no_overlaps(doc: SceneDocument, *, skip_kinds=("frame", "container")):
    rects = {
        n.id: (n.x, n.y, *doc.node_footprint(n))
        for n in doc.nodes.values()
        if n.kind not in skip_kinds and not n.is_docked
    }
    ids = sorted(rects)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            assert not _rects_overlap(rects[a], rects[b]), f"{a} and {b} overlap"


# -- footprints -------------------------------------------------------------

def test_footprint_prefers_the_frontend_measured_size():
    doc = SceneDocument()
    node = doc.add_chat_node(0, 0, "hi", True)
    assert doc.node_footprint(node) == KIND_FALLBACK_FOOTPRINTS["chat"]
    doc.set_measured_node_sizes([(node.id, 500.0, 750.0)])
    assert doc.node_footprint(node) == (500.0, 750.0)


def test_footprint_falls_back_per_kind_then_to_the_generic_default():
    doc = SceneDocument()
    note = doc.add_note(0, 0)
    assert doc.node_footprint(note) == KIND_FALLBACK_FOOTPRINTS["note"]
    node = doc.add_node(0, 0, "x")
    node.kind = "some-unknown-kind"
    assert doc.node_footprint(node) == DEFAULT_FALLBACK_FOOTPRINT


# -- place_child ------------------------------------------------------------

def test_place_child_lands_below_the_parents_real_measured_bottom_edge():
    doc = SceneDocument()
    parent = doc.add_chat_node(100, 50, "a very tall reply", False)
    doc.set_measured_node_sizes([(parent.id, 422.0, 900.0)])
    x, y = doc.place_child(parent.id, "chat")
    assert (x, y) == (100.0, 50.0 + 900.0 + NODE_GAP_Y), (
        "the old fixed 160px offset buried children inside a tall parent"
    )


def test_place_child_fans_a_second_sibling_right_past_the_first():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "root", True)
    first = doc.add_chat_node(*doc.place_child(parent.id, "chat"), "a", False, parent_id=parent.id)
    second = doc.add_chat_node(*doc.place_child(parent.id, "chat"), "b", False, parent_id=parent.id)
    assert first.y == second.y
    assert second.x >= first.x + doc.node_footprint(first)[0] + NODE_GAP_X
    _assert_no_overlaps(doc)


def test_place_child_right_left_and_above_clear_the_parents_footprint():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "root", True)
    doc.set_measured_node_sizes([(parent.id, 600.0, 400.0)])
    rx, ry = doc.place_child(parent.id, "note", prefer="right")
    assert (rx, ry) == (600.0 + NODE_GAP_X, 0.0)
    lx, _ly = doc.place_child(parent.id, "thinking", prefer="left")
    assert lx == -NODE_GAP_X - KIND_FALLBACK_FOOTPRINTS["thinking"][0]
    _ax, ay = doc.place_child(parent.id, "note", prefer="above")
    assert ay == -NODE_GAP_Y - KIND_FALLBACK_FOOTPRINTS["note"][1]


def test_place_child_with_unknown_parent_falls_back_to_place_root():
    doc = SceneDocument()
    existing = doc.add_chat_node(0, 0, "x", True)
    x, y = doc.place_child("nope", "chat")
    assert y >= existing.y + doc.node_footprint(existing)[1]


def test_place_at_scene_right_clears_the_widest_nodes_real_edge():
    doc = SceneDocument()
    node = doc.add_chat_node(0, 0, "wide", True)
    doc.set_measured_node_sizes([(node.id, 1500.0, 300.0)])
    x, _y = doc.place_at_scene_right("plan")
    assert x >= 1500.0 + NODE_GAP_X, (
        "max-x placement ignored width and could land on a wide node"
    )


def test_organize_keeps_the_creation_edge_as_structural_parent():
    """A node's FIRST edge (its creation edge, in insertion order - never a
    lexicographic id sort, where e10 < e9) stays its structural parent; a
    later cross-link (e.g. a note attached to the same node) must not steal
    the subtree."""
    doc = SceneDocument()
    root = doc.add_chat_node(0, 0, "root", True)
    child = doc.add_chat_node(0, 500, "child", False, parent_id=root.id)
    note = doc.add_note(900, 0)
    doc.connect(note.id, child.id)

    doc.organize()

    assert child.y >= root.y + doc.node_footprint(root)[1], (
        "the child must hang below its chat parent, not below the later-linked note"
    )
    assert abs((child.x + doc.node_footprint(child)[0] / 2)
               - (root.x + doc.node_footprint(root)[0] / 2)) < 1e-6


def test_footprint_of_a_collapsed_group_is_the_pill_not_a_stale_measurement():
    doc = SceneDocument()
    member = doc.add_chat_node(0, 0, "m", True)
    frame = doc.create_frame([member.id])
    doc.set_measured_node_sizes([(frame.id, 1200.0, 800.0)])
    doc.set_chat_collapsed(frame.id, True)
    from backend.domain.model import GROUP_COLLAPSED_HEIGHT, GROUP_COLLAPSED_WIDTH
    assert doc.node_footprint(frame) == (GROUP_COLLAPSED_WIDTH, GROUP_COLLAPSED_HEIGHT)


# -- properties -------------------------------------------------------------

@settings(max_examples=50, deadline=None)
@given(
    heights=st.lists(st.floats(min_value=1.0, max_value=1200.0), min_size=1, max_size=12),
    branch=st.lists(st.booleans(), min_size=0, max_size=11),
)
def test_property_spawned_chat_nodes_never_overlap(heights, branch):
    """Grow a conversation with arbitrary measured reply heights, randomly
    continuing the tip or branching from the root - no two nodes may ever
    overlap, whatever their real rendered sizes."""
    doc = SceneDocument()
    root = doc.send_message("root")
    doc.set_measured_node_sizes([(root.id, 422.0, heights[0])])
    for index, height in enumerate(heights[1:]):
        from_root = index < len(branch) and branch[index]
        node = doc.send_message(
            f"m{index}", branch_from_node_id=root.id if from_root else None,
        )
        doc.set_measured_node_sizes([(node.id, 422.0, height)])
    _assert_no_overlaps(doc)


@settings(max_examples=50, deadline=None)
@given(data=st.data())
def test_property_organize_produces_an_overlap_free_deterministic_layout(data):
    """Any scene shape - random positions, random measured sizes, random
    edges (cycles and multi-parent included) - must organize into a layout
    with no overlapping nodes, and a second organize must be a no-op."""
    doc = SceneDocument()
    count = data.draw(st.integers(min_value=1, max_value=15))
    nodes = []
    for i in range(count):
        node = doc.add_chat_node(
            data.draw(st.floats(min_value=-2000, max_value=2000)),
            data.draw(st.floats(min_value=-2000, max_value=2000)),
            f"m{i}", True,
        )
        if data.draw(st.booleans()):
            doc.set_measured_node_sizes([(
                node.id,
                data.draw(st.floats(min_value=1.0, max_value=1000.0)),
                data.draw(st.floats(min_value=1.0, max_value=1000.0)),
            )])
        nodes.append(node)
    edge_count = data.draw(st.integers(min_value=0, max_value=count * 2))
    for _ in range(edge_count):
        source = data.draw(st.sampled_from(nodes))
        target = data.draw(st.sampled_from(nodes))
        if source.id != target.id and not doc._reaches(target.id, source.id):
            doc.connect(source.id, target.id)

    doc.organize()
    _assert_no_overlaps(doc)

    positions = {n.id: (n.x, n.y) for n in doc.nodes.values()}
    doc.organize()
    assert positions == {n.id: (n.x, n.y) for n in doc.nodes.values()}


# -- cross-module: groups.py's OWN footprint chain must agree with layout.py

def test_organize_shrinks_an_outer_container_when_its_member_frame_collapses():
    """Regression: groups.py's _member_footprint (used by _bbox_of_members
    -> _recompute_group_bounds to size an OUTER group) lacked layout.py's
    node_footprint's own is_collapsed-first fast path, so an outer
    container wrapping a just-collapsed inner frame kept sizing itself to
    the frame's STALE pre-collapse measured_sizes entry - forever, since
    nothing else ever re-measures a collapsed node. organize()'s new
    _organize_groups (backend/domain/layout.py) is what made this
    reachable via a plain "click Organize": the old flat-grid organize()
    never called _recompute_group_bounds on anything."""
    doc = SceneDocument()
    member = doc.add_chat_node(0, 0, "m", True)
    inner_frame = doc.create_frame([member.id])
    # The frontend reported the frame's real EXPANDED rendered size before
    # it was ever collapsed - this stays in measured_sizes untouched by
    # the collapse (only re-reported on the node's next dimensions change).
    doc.set_measured_node_sizes([(inner_frame.id, 1200.0, 800.0)])
    outer = doc.create_container([inner_frame.id])
    doc.set_chat_collapsed(inner_frame.id, True)

    doc.organize()

    expected_width = GROUP_COLLAPSED_WIDTH + GROUP_PADDING * 2
    expected_height = GROUP_COLLAPSED_HEIGHT + GROUP_PADDING_TOP + GROUP_PADDING
    assert outer.state.group_width == expected_width, (
        "outer container must shrink to wrap the collapsed pill, not stay "
        "sized to the frame's stale pre-collapse measurement"
    )
    assert outer.state.group_height == expected_height


def test_member_footprint_of_a_collapsed_group_matches_node_footprint():
    """groups.py's _member_footprint and layout.py's node_footprint must
    agree on a collapsed group's size - two independent "how big is this
    node" functions that silently diverged is exactly what let the bug
    above happen."""
    doc = SceneDocument()
    member = doc.add_chat_node(0, 0, "m", True)
    frame = doc.create_frame([member.id])
    doc.set_measured_node_sizes([(frame.id, 900.0, 700.0)])
    doc.set_chat_collapsed(frame.id, True)
    assert doc._member_footprint(frame) == doc.node_footprint(frame) == (
        GROUP_COLLAPSED_WIDTH, GROUP_COLLAPSED_HEIGHT,
    )


# -- organize: dual frame+container membership must not scatter a group's
# -- own members across an unrelated node

def test_organize_does_not_let_a_dual_membership_root_split_a_groups_box():
    """A root node (no parent edge) may legally belong to one frame AND
    one container simultaneously (SceneNode.item_ids's own field comment).
    organize_layout's owner clustering must keep BOTH groups' members
    contiguous - not silently drop one group's claim on the shared node
    and strand its other member on the far side of an unrelated node,
    which used to make the stranded group's re-wrapped bbox stretch across
    (and visually engulf) that unrelated node."""
    doc = SceneDocument()
    m = doc.add_chat_node(0, 0, "dual member", True)
    a = doc.add_chat_node(100, 0, "frame sibling", True)
    b = doc.add_chat_node(200, 0, "container sibling", True)

    frame = doc.create_frame([m.id, a.id])
    container = doc.create_container([m.id, b.id])

    doc.organize()

    container_rect = (
        container.x, container.y,
        container.state.group_width, container.state.group_height,
    )
    a_rect = (a.x, a.y, *doc.node_footprint(a))
    assert not _rects_overlap(container_rect, a_rect), (
        "container's re-wrapped bbox engulfs node 'a', which is not one "
        "of its members"
    )
    frame_rect = (frame.x, frame.y, frame.state.group_width, frame.state.group_height)
    b_rect = (b.x, b.y, *doc.node_footprint(b))
    assert not _rects_overlap(frame_rect, b_rect), (
        "frame's re-wrapped bbox engulfs node 'b', which is not one of "
        "its members"
    )


def test_organize_correctly_nests_a_container_wrapped_around_a_frame():
    """A container may itself hold another frame/container as a member
    (create_container explicitly allows nesting). After organize(), the
    outer container's re-wrapped bbox must still fully enclose the inner
    frame's re-wrapped bbox - _organize_groups' innermost-first ordering
    (nesting_height) exists specifically for this."""
    doc = SceneDocument()
    leaf = doc.add_chat_node(0, 0, "leaf", True)
    inner_frame = doc.create_frame([leaf.id])
    outer_container = doc.create_container([inner_frame.id])

    doc.organize()

    ox, oy = outer_container.x, outer_container.y
    ow, oh = outer_container.state.group_width, outer_container.state.group_height
    ix, iy = inner_frame.x, inner_frame.y
    iw, ih = inner_frame.state.group_width, inner_frame.state.group_height
    assert ox <= ix and oy <= iy
    assert ox + ow >= ix + iw
    assert oy + oh >= iy + ih

    before = {nid: (n.x, n.y) for nid, n in doc.nodes.items()}
    doc.organize()
    assert before == {nid: (n.x, n.y) for nid, n in doc.nodes.items()}, (
        "organize must be idempotent for nested groups too"
    )


def test_organize_lines_up_multiple_collapsed_groups_without_overlap():
    """Several independent collapsed frames/containers must all land on
    the same pill row below the laid-out content, each clear of the
    others - not stacked on top of each other."""
    doc = SceneDocument()
    m1 = doc.add_chat_node(0, 500, "m1", True)
    m2 = doc.add_chat_node(500, 500, "m2", True)
    m3 = doc.add_chat_node(1000, 500, "m3", True)
    g1 = doc.create_frame([m1.id])
    g2 = doc.create_container([m2.id])
    g3 = doc.create_frame([m3.id])
    for g in (g1, g2, g3):
        doc.set_chat_collapsed(g.id, True)

    doc.organize()

    rects = {g.id: (g.x, g.y, *doc.node_footprint(g)) for g in (g1, g2, g3)}
    ids = sorted(rects)
    for i, a_id in enumerate(ids):
        for b_id in ids[i + 1:]:
            assert not _rects_overlap(rects[a_id], rects[b_id]), f"{a_id} and {b_id} overlap"
    ys = {r[1] for r in rects.values()}
    assert len(ys) == 1, "all collapsed pills should share one row"


def test_create_frame_and_create_container_reject_an_empty_member_list():
    """The frontend's own create-frame/create-container commands gate on
    2+ selected nodes, but that is a UI-layer convenience, not an
    invariant this layer enforced - any other caller (a plugin, the
    Builder tool, a malformed WS message) could construct a zero-member
    group with nothing to catch it. Two independently-created empty
    groups would land on the exact same default rect (_bbox_of_members([])
    has nowhere else to derive a position from) and stay glued together
    through every subsequent Organize."""
    doc = SceneDocument()
    with pytest.raises(SceneError):
        doc.create_frame([])
    with pytest.raises(SceneError):
        doc.create_container([])


# -- node_footprint's fallback-chain priority tiers

def test_node_footprint_intrinsic_size_wins_over_kind_fallback_but_loses_to_measured():
    """node_footprint's middle priority tier - a kind's own intrinsic
    geometry (chart_width/height, group_width/height) - sits strictly
    between measured (wins) and the flat per-kind fallback (loses); only
    the top and bottom tiers were previously pinned by any test."""
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "p", True)
    chart = doc.add_chart_node(0, 0, parent.id, "bar", {"labels": ["a"], "values": [1.0]})
    intrinsic = (chart.state.chart_width, chart.state.chart_height)
    assert intrinsic != KIND_FALLBACK_FOOTPRINTS["chart"], (
        "fixture invalid: intrinsic and fallback must differ to prove which wins"
    )
    assert doc.node_footprint(chart) == intrinsic, (
        "no measured size yet - must use the chart's own intrinsic size, "
        "not the flat per-kind fallback"
    )
    doc.set_measured_node_sizes([(chart.id, 111.0, 222.0)])
    assert doc.node_footprint(chart) == (111.0, 222.0), (
        "a real measured size must still win over the intrinsic chart size"
    )


# -- place_root / place_at_scene_right: docked-node exclusion

def test_place_root_and_place_at_scene_right_ignore_a_docked_nodes_extent():
    """A docked node renders inside its (former) parent, not at its own
    x/y - place_root's bottom-extent scan and place_at_scene_right's
    right-extent scan must both exclude it, even when it sits far past
    every anchored node."""
    doc = SceneDocument()
    doc.add_chat_node(0, 0, "a", True)
    far_right = doc.add_chat_node(2000, 0, "far right", True)
    doc.set_measured_node_sizes([(far_right.id, 3000.0, 300.0)])

    x_before, _ = doc.place_at_scene_right("note")
    doc.set_node_docked(far_right.id, True)
    x_after, _ = doc.place_at_scene_right("note")
    assert x_after < x_before, "docking the far-right node must shrink the scene-right extent"

    doc2 = SceneDocument()
    doc2.add_chat_node(0, 0, "a", True)
    far_bottom = doc2.add_chat_node(0, 2000, "far bottom", True)
    doc2.set_measured_node_sizes([(far_bottom.id, 400.0, 3000.0)])

    _, y_before = doc2.place_root("note")
    doc2.set_node_docked(far_bottom.id, True)
    _, y_after = doc2.place_root("note")
    assert y_after < y_before, "docking the far-bottom node must shrink place_root's bottom extent"


# -- first-edge-wins parent selection across the double-digit id boundary

def test_organize_first_edge_wins_across_a_double_digit_edge_id_boundary():
    """Edge ids are 'e<counter>' strings sharing the id counter with
    nodes - a lexicographic sort (not the insertion-order iteration the
    code actually uses) would put 'e11' before 'e9'. A fixture that only
    ever reaches single-digit edge ids can't tell insertion-order
    iteration apart from an accidental id-sort regression, since the two
    coincide by luck at that scale."""
    doc = SceneDocument()
    root = doc.add_chat_node(0, 0, "root", True)
    child = doc.add_chat_node(0, 500, "child", False)
    for i in range(7):
        doc.add_chat_node(0, 0, f"filler{i}", True)
    doc.connect(root.id, child.id)
    note = doc.add_note(900, 0)
    doc.connect(note.id, child.id)

    edge_ids = list(doc.edges.keys())
    assert sorted(edge_ids) != edge_ids, (
        "fixture invalid: need the real edge's id to sort AFTER the "
        "cross-link's id lexicographically to actually exercise the "
        "insertion-order-not-id-order guarantee"
    )

    doc.organize()

    root_bottom = root.y + doc.node_footprint(root)[1]
    assert child.y >= root_bottom, (
        "the child must hang below its real (chronologically first) chat "
        "parent, not below the later, lexicographically-smaller-id cross-link"
    )
    assert abs(
        (child.x + doc.node_footprint(child)[0] / 2)
        - (root.x + doc.node_footprint(root)[0] / 2)
    ) < 1e-6


def test_organize_orders_ties_by_creation_not_lexicographic_id():
    """order_key's tie-break (used when several siblings share an x, a
    real condition for legacy saves missing position data - see
    session_load.py's _position()) must use creation/insertion order, not
    a lexicographic string compare on the node id ('n11' sorts before
    'n7', which would scramble sibling order past 10 nodes)."""
    doc = SceneDocument()
    root = doc.add_chat_node(0, 0, "root", True)
    parent = doc.add_chat_node(0, 0, "parent", True, parent_id=root.id)
    widths = [200.0, 900.0, 350.0, 1400.0, 250.0]
    grandkids = []
    for i, w in enumerate(widths):
        g = doc.add_chat_node(0, 0, f"grandchild {i}", True, parent_id=parent.id)
        doc.set_measured_node_sizes([(g.id, w, 220.0)])
        grandkids.append(g)

    doc.organize()

    visual_order = [g.id for g in sorted(grandkids, key=lambda g: g.x)]
    creation_order = [g.id for g in grandkids]
    assert visual_order == creation_order, (
        f"tied siblings must lay out in creation order {creation_order}, "
        f"not {visual_order}"
    )


# -- find_free_position: convergence must scale with real obstacle count,
# -- not a fixed step budget

def test_find_free_position_clears_many_more_obstacles_than_the_old_fixed_cap():
    """find_free_position used to cap at a fixed 300 steps - each step only
    clears the single nearest blocker, so the number of steps needed
    scales with the number of distinct staggered obstacles, not a
    constant. A scene with more than 300 obstacles along one sweep line
    used to silently return a position that still overlapped one of
    them. The bound is now len(obstacles) + 1 (proven sufficient: each
    step permanently clears at least one obstacle and the candidate
    position never decreases), so this must still find a genuinely clear
    spot well past the old cap."""
    doc = SceneDocument()
    for i in range(400):
        node = doc.add_chat_node(float(i), 0.0, f"o{i}", True)
        doc.set_measured_node_sizes([(node.id, 250.0, 100.0)])

    x, y = doc.find_free_position(0.0, 0.0, 420.0, 300.0, advance="right")

    for obstacle_node in doc.nodes.values():
        ow, oh = doc.node_footprint(obstacle_node)
        assert not _rects_overlap((x, y, 420.0, 300.0), (obstacle_node.x, obstacle_node.y, ow, oh))


# -- place_at_scene_right / place_root: the two placement shapes
# -- launcher-created nodes (Builder plans, Agent harness nodes) actually
# -- flow through had no property coverage, unlike every other shape here.

@settings(max_examples=50, deadline=None)
@given(data=st.data())
def test_property_place_at_scene_right_and_place_root_never_overlap_existing_nodes(data):
    doc = SceneDocument()
    count = data.draw(st.integers(min_value=0, max_value=12))
    for i in range(count):
        node = doc.add_chat_node(
            data.draw(st.floats(min_value=-2000, max_value=2000)),
            data.draw(st.floats(min_value=-2000, max_value=2000)),
            f"m{i}", True,
        )
        if data.draw(st.booleans()):
            doc.set_measured_node_sizes([(
                node.id,
                data.draw(st.floats(min_value=1.0, max_value=1000.0)),
                data.draw(st.floats(min_value=1.0, max_value=1000.0)),
            )])
        if data.draw(st.booleans()):
            doc.set_node_docked(node.id, True)

    kind = data.draw(st.sampled_from(["chat", "note", "plan", "harness"]))
    if data.draw(st.booleans()):
        x, y = doc.place_at_scene_right(kind)
    else:
        x, y = doc.place_root(kind)
    w, h = doc.kind_fallback_footprint(kind)

    for n in doc.nodes.values():
        if n.is_docked:
            continue
        nw, nh = doc.node_footprint(n)
        assert not _rects_overlap((x, y, w, h), (n.x, n.y, nw, nh))
