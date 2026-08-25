"""LayoutOps (backend/domain/layout.py): spawn placement + organize.

Unit tests pin the placement shapes (below/right/left/above, footprint
fallback chain, scene-edge placement); the hypothesis properties pin the
two invariants the whole module exists for - a spawned child never lands
on an existing node, and organize() always produces an overlap-free,
deterministic layout - across arbitrary scene shapes, sizes, and edge
structures (including cycles and multi-parent nodes).
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from backend.canvas import SceneDocument
from backend.domain.layout import (
    DEFAULT_FALLBACK_FOOTPRINT,
    KIND_FALLBACK_FOOTPRINTS,
    NODE_GAP_X,
    NODE_GAP_Y,
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
