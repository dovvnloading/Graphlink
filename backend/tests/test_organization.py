"""Organization-plan safety and scope regressions.

The live scene must be byte-for-byte unchanged by preview. Applying a plan
is a separately authorized, undoable mutation tied to the previewed graph.
"""

from __future__ import annotations

import copy

import pytest

from backend.canvas import SceneDocument
from backend.domain.model import SceneError
from backend.domain.node_states import ChatState, FrameState
from backend.domain.organization import (
    _layout_geometry,
    apply_organization_plan,
    layout_fingerprint,
    make_organization_plan,
)


def _sample() -> tuple[SceneDocument, str, str, str]:
    doc = SceneDocument()
    root = doc.add_chat_node(200, 100, "root", True)
    child = doc.add_chat_node(350, 120, "child", False, parent_id=root.id)
    another = doc.add_chat_node(440, 115, "another", False, parent_id=root.id)
    doc.set_measured_node_sizes([(root.id, 422, 690), (child.id, 422, 430)])
    return doc, root.id, child.id, another.id


def test_preview_does_not_mutate_nodes_edges_sizes_or_undo_history():
    doc, root, child, other = _sample()
    frame = doc.create_frame([child, other])
    container = doc.create_container([root, frame.id])
    original_nodes = copy.deepcopy(doc.nodes)
    original_edges = copy.deepcopy(doc.edges)
    original_sizes = dict(doc.measured_sizes)
    original_fingerprint = layout_fingerprint(doc)
    original_commands = len(doc.command_log)

    whole = make_organization_plan(doc, "all")
    partial = make_organization_plan(doc, "branch", root_id=root)
    group = make_organization_plan(doc, "group", root_id=container.id)

    assert whole.preview(doc)["planId"]
    assert partial.preview(doc)["scope"] == "branch"
    assert group.preview(doc)["selectedCount"] == 3
    assert doc.nodes == original_nodes
    assert doc.edges == original_edges
    assert doc.measured_sizes == original_sizes
    assert layout_fingerprint(doc) == original_fingerprint
    assert len(doc.command_log) == original_commands



def test_preview_contains_precise_before_after_geometry_and_existing_edges():
    doc, root, child, other = _sample()
    snapshot = copy.deepcopy(doc.nodes)
    edges_before = copy.deepcopy(doc.edges)
    plan = make_organization_plan(doc, "all")
    result = plan.preview(doc)
    rows = {row["id"]: row for row in result["nodes"]}

    assert set(rows) == set(doc.nodes)
    for nid, row in rows.items():
        original = doc.nodes[nid]
        assert (row["previousX"], row["previousY"]) == (original.x, original.y)
        assert (row["previousWidth"], row["previousHeight"]) == doc.node_footprint(original)
        proposed = plan.nodes[nid]
        assert (row["x"], row["y"]) == (proposed.x, proposed.y)
        assert row["changed"] == (nid in plan.changed_ids)
    assert result["edges"] == [
        {"id": edge.id, "source": edge.source, "target": edge.target}
        for edge in doc.edges.values()
    ]
    for mode, area_field in (("previous", "areaBefore"), ("", "areaAfter")):
        prefix = mode
        x_key = prefix + "X" if prefix else "x"
        y_key = prefix + "Y" if prefix else "y"
        width_key = prefix + "Width" if prefix else "width"
        height_key = prefix + "Height" if prefix else "height"
        left = min(row[x_key] for row in rows.values())
        top = min(row[y_key] for row in rows.values())
        right = max(row[x_key] + row[width_key] for row in rows.values())
        bottom = max(row[y_key] + row[height_key] for row in rows.values())
        assert result[area_field] == (right - left) * (bottom - top)
    assert doc.nodes == snapshot
    assert doc.edges == edges_before
    assert child in rows and other in rows and root in rows


def test_preview_does_not_expose_hidden_group_descendant_edges():
    doc = SceneDocument()
    root = doc.add_chat_node(0, 0, "root", True)
    hidden = doc.add_chat_node(400, 100, "hidden", False, parent_id=root.id)
    group = doc.create_container([hidden.id])
    doc.toggle_group_collapsed(group.id)
    preview = make_organization_plan(doc, "all").preview(doc)
    node_ids = {row["id"] for row in preview["nodes"]}
    assert group.id in node_ids
    assert hidden.id not in node_ids
    assert all(
        edge["source"] in node_ids and edge["target"] in node_ids
        for edge in preview["edges"]
    )
    assert not any(hidden.id in (edge["source"], edge["target"]) for edge in preview["edges"])


def test_collapsed_group_scope_is_rejected_and_hidden_leaves_stay_fixed():
    """A collapsed proxy is not an invitation to move invisible members."""
    doc = SceneDocument()
    hidden = doc.add_chat_node(800, 900, "member", True)
    group = doc.create_container([hidden.id])
    doc.toggle_group_collapsed(group.id)
    other = doc.add_chat_node(0, 0, "visible outsider", True)
    before = (hidden.x, hidden.y, group.x, group.y)

    with pytest.raises(SceneError, match="expand the collapsed group"):
        make_organization_plan(doc, "group", root_id=group.id)
    plan = make_organization_plan(doc, "all")
    assert hidden.id not in plan.selected_ids
    preview_ids = [node["id"] for node in plan.preview(doc)["nodes"]]
    assert hidden.id not in preview_ids
    assert group.id in preview_ids
    apply_organization_plan(doc, plan)
    assert (hidden.x, hidden.y, group.x, group.y) == before
    assert other.id in doc.nodes


def test_collapsed_frame_preserves_manual_anchor_after_preview_and_expansion():
    doc = SceneDocument()
    leaf = doc.add_chat_node(500, 500, "member", True)
    frame = doc.create_frame([leaf.id])
    doc.resize_frame(frame.id, 1600, 1100)
    doc.move_node(frame.id, 600, 600)
    frame_before = (frame.x, frame.y)
    manual_before = (frame.state.group_manual_x, frame.state.group_manual_y)
    doc.toggle_group_collapsed(frame.id)

    plan = make_organization_plan(doc, "all")
    apply_organization_plan(doc, plan)
    assert (frame.state.group_manual_x, frame.state.group_manual_y) == manual_before
    doc.toggle_group_collapsed(frame.id)
    assert (frame.x, frame.y) == frame_before


def test_metadata_note_link_never_becomes_a_structural_parent():
    doc = SceneDocument()
    root = doc.add_chat_node(0, 0, "root", True)
    child = doc.add_chat_node(0, 0, "child", False)
    prompt = doc.add_note(1000, 100)
    doc.connect(prompt.id, child.id)  # deliberately BEFORE the real parent
    doc.connect(root.id, child.id)

    parent = doc._branch_parent_edge(child.id)
    assert parent is not None
    assert parent.source == root.id
    plan = make_organization_plan(doc, "branch", root_id=root.id)
    assert child.id in plan.selected_ids
    assert prompt.id not in plan.selected_ids

    doc.organize()
    assert child.y >= root.y + doc.node_footprint(root)[1]


def test_selection_only_repositions_its_leaves_and_enclosing_groups():
    doc, root, child, other = _sample()
    outside = doc.add_chat_node(2500, 2500, "outside", True)
    frame = doc.create_frame([child, other])
    container = doc.create_container([frame.id])
    old_root = (doc.nodes[root].x, doc.nodes[root].y)
    old_outside = (outside.x, outside.y)
    members_before = (tuple(frame.item_ids), tuple(container.item_ids))
    edges_before = copy.deepcopy(doc.edges)

    plan = make_organization_plan(doc, "selection", node_ids=[child, other])
    apply_organization_plan(doc, plan)

    assert (doc.nodes[root].x, doc.nodes[root].y) == old_root
    assert (outside.x, outside.y) == old_outside
    assert (tuple(frame.item_ids), tuple(container.item_ids)) == members_before
    assert doc.edges == edges_before
    assert child in plan.selected_ids and other in plan.selected_ids


def test_branch_scope_cannot_capture_unrelated_reference_or_component():
    doc, root, child, other = _sample()
    unrelated = doc.add_chat_node(1900, 0, "unrelated", True)
    doc.connect(unrelated.id, child)  # later cross-link, not a new parent

    plan = make_organization_plan(doc, "branch", root_id=root)
    assert root in plan.selected_ids
    assert child in plan.selected_ids
    assert other in plan.selected_ids
    assert unrelated.id not in plan.selected_ids


def test_group_scope_expands_nested_container_and_preserves_membership():
    doc, root, child, other = _sample()
    frame = doc.create_frame([child, other])
    container = doc.create_container([frame.id, root])
    plan = make_organization_plan(doc, "group", root_id=container.id)
    assert plan.selected_ids == {root, child, other}
    apply_organization_plan(doc, plan)
    assert frame.item_ids == [child, other]
    assert container.item_ids == [frame.id, root]


def test_scoped_layout_cannot_place_ungrouped_nodes_inside_an_unrelated_frame():
    """Only groups that contain selected leaves may be repacked around them.

    A manually widened frame is a large visible background even when its
    own content occupies one corner. Without treating its full rect as an
    obstacle, a second selected root can appear inside the frame despite
    never belonging to it. This changes the *apparent* organization while
    the membership data stays unchanged.
    """
    from backend.domain.layout import _rects_clear

    doc = SceneDocument()
    first = doc.add_node(0, 0, "first")
    second = doc.add_node(70, 0, "second")
    private_member = doc.add_node(1000, 0, "private")
    frame = doc.create_frame([private_member.id])
    doc.resize_frame(frame.id, 1350, 360)
    assert isinstance(frame.state, FrameState)

    # Neither selected node currently intersects this unrelated frame.
    before_group = (
        frame.x, frame.y, frame.state.group_width, frame.state.group_height
    )
    assert all(
        _rects_clear(doc.nodes[nid].x, doc.nodes[nid].y, *doc.node_footprint(doc.nodes[nid]), *before_group)
        for nid in (first.id, second.id)
    )

    plan = make_organization_plan(doc, "selection", node_ids=[first.id, second.id])
    apply_organization_plan(doc, plan)

    # A frame is a semantic visual boundary, not an inert decoration.
    # The selected nodes must remain outside the *full* background, not
    # merely clear of its one private member.
    assert all(
        _rects_clear(doc.nodes[nid].x, doc.nodes[nid].y, *doc.node_footprint(doc.nodes[nid]), *before_group)
        for nid in (first.id, second.id)
    )
    assert (
        frame.x, frame.y, frame.state.group_width, frame.state.group_height
    ) == before_group
    assert frame.item_ids == [private_member.id]


def test_fixed_node_stays_unchanged_when_the_rest_reflows():
    doc, _root, child, _other = _sample()
    initial = (doc.nodes[child].x, doc.nodes[child].y)
    plan = make_organization_plan(doc, "all", fixed_node_ids=[child])
    apply_organization_plan(doc, plan)
    assert (doc.nodes[child].x, doc.nodes[child].y) == initial
    assert child not in plan.changed_ids



def test_pin_nested_container_freezes_its_entire_membership_region():
    doc, _root, child, other = _sample()
    frame = doc.create_frame([child, other])
    container = doc.create_container([frame.id])
    doc.add_chat_node(2400, 800, "outside", True)

    pinned = {container.id, frame.id, child, other}
    before = {nid: _layout_geometry(doc.nodes[nid]) for nid in pinned}
    plan = make_organization_plan(doc, "all", fixed_node_ids=[container.id])

    assert pinned.isdisjoint(plan.changed_ids)
    assert {nid: _layout_geometry(plan.nodes[nid]) for nid in pinned} == before
    apply_organization_plan(doc, plan)
    assert {nid: _layout_geometry(doc.nodes[nid]) for nid in pinned} == before



def test_stale_plan_rejected_without_partial_writes():
    doc, root, _child, _other = _sample()
    plan = make_organization_plan(doc)
    doc.move_node(root, 340, 777)
    before = {nid: _layout_geometry(n) for nid, n in doc.nodes.items()}
    with pytest.raises(SceneError, match="graph changed"):
        apply_organization_plan(doc, plan)
    assert {nid: _layout_geometry(n) for nid, n in doc.nodes.items()} == before


@pytest.mark.parametrize("mutation", ("stream", "history", "state", "asset"))
def test_content_changes_expire_a_preview_even_before_resize_reporting(mutation):
    doc, root, _child, _other = _sample()
    plan = make_organization_plan(doc)
    old_positions = {nid: (node.x, node.y) for nid, node in doc.nodes.items()}
    if mutation == "stream":
        doc.nodes[root].content += " an in-progress assistant token"
    elif mutation == "history":
        doc.nodes[root].history.append({"role": "assistant", "content": "new turn"})
    elif mutation == "state":
        chat = doc.nodes[root]
        assert isinstance(chat.state, ChatState)
        chat.state.branch_status = "accepted"
    else:
        doc.image_assets["new"] = (b"updated png", "image/png")
    with pytest.raises(SceneError, match="graph changed"):
        apply_organization_plan(doc, plan)
    assert {nid: (node.x, node.y) for nid, node in doc.nodes.items()} == old_positions


def test_topology_change_invalidates_preview():
    doc, _root, _child, other = _sample()
    plan = make_organization_plan(doc)
    new = doc.add_chat_node(0, 0, "new", True)
    doc.connect(new.id, other)
    with pytest.raises(SceneError, match="graph changed"):
        apply_organization_plan(doc, plan)


def test_undo_redo_restores_exact_prior_coordinates_and_group_dimensions():
    doc, _root, child, other = _sample()
    frame = doc.create_frame([child, other])
    doc.resize_frame(frame.id, 1200, 900)
    before = {nid: _layout_geometry(n) for nid, n in doc.nodes.items()}
    edges_before = copy.deepcopy(doc.edges)
    plan = make_organization_plan(doc, "all", preserve_frame_sizes=True)
    doc.record_command(
        "organizeNodes", "user",
        lambda: apply_organization_plan(doc, plan),
        node_ids=list(doc.nodes),
    )
    after = {nid: _layout_geometry(n) for nid, n in doc.nodes.items()}
    frame_node = doc.nodes[frame.id]
    assert isinstance(frame_node.state, FrameState)
    assert frame_node.state.group_manual_width == 1200
    assert before != after
    doc.undo()
    assert {nid: _layout_geometry(n) for nid, n in doc.nodes.items()} == before
    doc.redo()
    assert {nid: _layout_geometry(n) for nid, n in doc.nodes.items()} == after
    assert doc.edges == edges_before


def test_legacy_whole_canvas_layout_is_unchanged_when_frame_preservation_off():
    doc, _root, child, other = _sample()
    doc.create_frame([child, other])
    plan = make_organization_plan(doc, "all", preserve_frame_sizes=False)
    apply_organization_plan(doc, plan)
    actual = {nid: _layout_geometry(n) for nid, n in doc.nodes.items()}
    # The existing legacy command must still produce precisely the same geometry.
    doc2, _root2, child2, other2 = _sample()
    doc2.create_frame([child2, other2])
    doc2.organize()
    expected = list(_layout_geometry(n) for n in doc2.nodes.values())
    assert list(actual.values()) == expected


def test_bad_scopes_are_rejected_before_a_mutation():
    doc, root, _child, _other = _sample()
    before = layout_fingerprint(doc)
    with pytest.raises(SceneError, match="unknown organization scope"):
        make_organization_plan(doc, "unrecognised")  # type: ignore[arg-type]
    with pytest.raises(SceneError, match="requires at least one"):
        make_organization_plan(doc, "selection", node_ids=[])
    with pytest.raises(SceneError, match="requires a frame"):
        make_organization_plan(doc, "group", root_id=root)
    assert layout_fingerprint(doc) == before


def test_previews_are_deterministic_and_second_apply_is_a_noop():
    doc, _root, _child, _other = _sample()
    first = make_organization_plan(doc, "all")
    same = make_organization_plan(doc, "all")
    assert first.plan_id == same.plan_id
    assert first.preview(doc) == same.preview(doc)
    apply_organization_plan(doc, first)
    second = make_organization_plan(doc, "all")
    assert not second.changed_ids
