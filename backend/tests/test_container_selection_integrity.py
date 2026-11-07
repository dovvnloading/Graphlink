"""Issue #470: overlapping container selections must never corrupt membership.

Creation is a topology-changing operation, not just a visual bbox update:
normalization and validation must finish before any old owner is detached.
"""

from __future__ import annotations

import copy

import pytest

from backend.canvas import SceneDocument
from backend.domain.model import SceneError
from backend.session_load import restore_chat_into_document
from backend.session_save import build_chat_data


def _assert_live_unique_group_members(doc: SceneDocument) -> None:
    for group in doc.nodes.values():
        if group.kind not in ("frame", "container"):
            continue
        assert len(group.item_ids) == len(set(group.item_ids))
        assert all(member_id in doc.nodes for member_id in group.item_ids)


@pytest.mark.parametrize("ancestor_first", [True, False])
def test_selecting_a_container_and_its_direct_member_keeps_the_container(ancestor_first: bool) -> None:
    doc = SceneDocument()
    member = doc.add_node(0, 0, "member")
    other = doc.add_node(800, 0, "other")
    inner = doc.create_container([member.id])
    selection = (
        [inner.id, member.id, other.id]
        if ancestor_first else [member.id, inner.id, other.id]
    )

    wrapper = doc.create_container(selection)

    assert inner.id in doc.nodes
    assert doc.nodes[inner.id] is inner
    assert wrapper.item_ids == [inner.id, other.id]
    assert inner.item_ids == [member.id]
    _assert_live_unique_group_members(doc)


def test_descendant_at_arbitrary_nesting_depth_is_not_reparented() -> None:
    doc = SceneDocument()
    member = doc.add_node(0, 0, "member")
    other = doc.add_node(900, 0, "other")
    nested = [doc.create_container([member.id])]
    for _ in range(7):
        nested.append(doc.create_container([nested[-1].id]))

    # Descendant-first and out-of-order intermediate selected containers.
    selection = [member.id, nested[3].id, nested[-1].id, nested[0].id, other.id]
    wrapper = doc.create_container(selection)

    assert wrapper.item_ids == [nested[-1].id, other.id]
    assert all(group.id in doc.nodes for group in nested)
    for child, parent in zip(nested, nested[1:]):
        assert parent.item_ids == [child.id]
    assert nested[0].item_ids == [member.id]
    _assert_live_unique_group_members(doc)


def test_duplicate_selected_ids_are_deduplicated_in_stable_order() -> None:
    doc = SceneDocument()
    first = doc.add_node(0, 0, "one")
    second = doc.add_node(500, 0, "two")
    wrapper = doc.create_container([second.id, first.id, second.id, first.id])
    assert wrapper.item_ids == [second.id, first.id]
    _assert_live_unique_group_members(doc)


def test_independent_frame_membership_is_not_a_container_ancestor() -> None:
    doc = SceneDocument()
    leaf = doc.add_node(0, 0, "leaf")
    frame = doc.create_frame([leaf.id])

    wrapper = doc.create_container([frame.id, leaf.id])

    # A leaf can still be in both one frame and one container: the new
    # normalizer only follows CONTAINER membership, not frame ancestry.
    assert wrapper.item_ids == [frame.id, leaf.id]
    assert doc.nodes[frame.id].item_ids == [leaf.id]
    _assert_live_unique_group_members(doc)


@pytest.mark.parametrize("invalid_members", [
    ("ghost",),
    ("ghost", "ghost"),
])
def test_unknown_selected_member_rejects_atomically(invalid_members: tuple[str, ...]) -> None:
    doc = SceneDocument()
    leaf = doc.add_node(0, 0, "leaf")
    old_group = doc.create_container([leaf.id])
    before = copy.deepcopy(doc.nodes)
    edges_before = copy.deepcopy(doc.edges)
    with pytest.raises(SceneError, match="unknown member node"):
        doc.create_container([old_group.id, leaf.id, *invalid_members])
    assert doc.nodes == before
    assert doc.edges == edges_before


def test_preexisting_corrupt_container_subtree_rejects_before_detach() -> None:
    doc = SceneDocument()
    leaf = doc.add_node(0, 0, "leaf")
    other = doc.add_node(600, 0, "other")
    root = doc.create_container([leaf.id])
    root.item_ids.append("ghost")  # legacy/corrupt membership
    before = copy.deepcopy(doc.nodes)
    with pytest.raises(SceneError, match="unknown nested member"):
        doc.create_container([other.id, root.id])
    assert doc.nodes == before


def test_cyclic_legacy_container_membership_rejects_without_mutation() -> None:
    doc = SceneDocument()
    leaf = doc.add_node(0, 0, "leaf")
    inner = doc.create_container([leaf.id])
    outer = doc.create_container([inner.id])
    inner.item_ids = [outer.id]  # malformed legacy cycle
    before = copy.deepcopy(doc.nodes)
    with pytest.raises(SceneError, match="cyclic container membership"):
        doc.create_container([outer.id])
    assert doc.nodes == before


def test_overlapping_selection_is_one_undoable_operation() -> None:
    doc = SceneDocument()
    leaf = doc.add_node(0, 0, "leaf")
    other = doc.add_node(800, 0, "other")
    inner = doc.create_container([leaf.id])
    before = copy.deepcopy(doc.nodes)

    wrapper, _ = doc.record_command(
        "createContainer", "user",
        lambda: doc.create_container([leaf.id, inner.id, other.id]),
        node_ids=[leaf.id, inner.id, other.id],
    )
    wrapper_id = wrapper.id
    assert wrapper.item_ids == [inner.id, other.id]
    assert inner.id in doc.nodes
    doc.undo()
    assert doc.nodes == before
    assert wrapper_id not in doc.nodes
    doc.redo()
    assert wrapper_id in doc.nodes
    assert doc.nodes[wrapper_id].item_ids == [inner.id, other.id]
    _assert_live_unique_group_members(doc)


def test_normalized_nested_container_selection_survives_save_load() -> None:
    doc = SceneDocument()
    leaf = doc.add_note(0, 0)
    doc.set_note_content(leaf.id, "Nested leaf")
    extra = doc.add_note(900, 0)
    doc.set_note_content(extra.id, "Separate note")
    inner = doc.create_container([leaf.id])
    doc.set_group_label(inner.id, "Inner")
    ancestor = doc.create_container([inner.id])
    doc.set_group_label(ancestor.id, "Ancestor")

    wrapper = doc.create_container([leaf.id, inner.id, ancestor.id, extra.id])
    doc.set_group_label(wrapper.id, "Wrapper")
    assert wrapper.item_ids == [ancestor.id, extra.id]

    saved = build_chat_data(doc)
    notes_data = saved.pop("notes_data")
    pins_data = saved.pop("pins_data")
    restored = SceneDocument()
    restore_chat_into_document(restored, {"data": saved}, notes_data, pins_data)

    groups = {
        n.content: n for n in restored.nodes.values()
        if n.kind == "container"
    }
    restored_leaf = next(n for n in restored.nodes.values() if n.content == "Nested leaf")
    restored_extra = next(n for n in restored.nodes.values() if n.content == "Separate note")
    assert groups["Inner"].item_ids == [restored_leaf.id]
    assert groups["Ancestor"].item_ids == [groups["Inner"].id]
    assert groups["Wrapper"].item_ids == [groups["Ancestor"].id, restored_extra.id]
    _assert_live_unique_group_members(restored)
