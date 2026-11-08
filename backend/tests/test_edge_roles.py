"""Backwards-compatible explicit graph role semantics for #476.

No existing edge disappears, old flat lists stay byte-for-byte compatible,
and ancestry can stop depending on creation order when the owner explicitly
classifies an incoming link as primary.
"""

from __future__ import annotations

import copy

import pytest

from backend.canvas import SceneDocument
from backend.domain.model import SceneError
from backend.domain.organization import layout_fingerprint, make_organization_plan
from backend.domain.structure import primary_parent_edges
from backend.session_load import restore_chat_into_document
from backend.session_save import build_chat_data


def _two_parents():
    doc = SceneDocument()
    first = doc.add_chat_node(0, 0, "first", True)
    second = doc.add_chat_node(800, 0, "second", True)
    child = doc.add_chat_node(450, 300, "child", False)
    early = doc.connect(first.id, child.id)
    late = doc.connect(second.id, child.id)
    return doc, first, second, child, early, late


def test_legacy_edges_keep_first_created_incoming_parent():
    doc, first, second, child, early, late = _two_parents()
    assert early.role == late.role == "legacy"
    assert doc._branch_parent_edge(child.id) is early
    parents = primary_parent_edges(doc.nodes, doc.edges, set(doc.nodes))
    assert parents[child.id] is early
    assert child.id in make_organization_plan(doc, "branch", root_id=first.id).selected_ids
    assert child.id not in make_organization_plan(doc, "branch", root_id=second.id).selected_ids


def test_explicit_primary_overrides_legacy_without_discarding_links():
    doc, first, second, child, early, late = _two_parents()
    original_edges = set(doc.edges)
    fingerprint = layout_fingerprint(doc)
    doc.set_edge_role(late.id, "primary")
    assert layout_fingerprint(doc) != fingerprint
    assert set(doc.edges) == original_edges
    assert doc._branch_parent_edge(child.id) is late
    parents = primary_parent_edges(doc.nodes, doc.edges, set(doc.nodes))
    assert parents[child.id] is late
    assert child.id in make_organization_plan(doc, "branch", root_id=second.id).selected_ids
    assert child.id not in make_organization_plan(doc, "branch", root_id=first.id).selected_ids
    doc.set_edge_role(late.id, "secondary")
    assert doc._branch_parent_edge(child.id) is early


@pytest.mark.parametrize("role", ["secondary", "reference", "association"])
def test_nonprimary_typed_edges_never_become_legacy_ancestry(role):
    doc, _first, _second, child, early, late = _two_parents()
    doc.set_edge_role(early.id, role)
    doc.set_edge_role(late.id, role)
    assert doc._branch_parent_edge(child.id) is None
    assert child.id not in primary_parent_edges(doc.nodes, doc.edges, set(doc.nodes))
    assert doc.edges[early.id].role == role
    assert len(doc.edges) == 2


def test_conflicting_primary_roles_and_note_ancestry_fail_atomically():
    doc, _first, _second, child, early, late = _two_parents()
    note = doc.add_note(0, 900)
    metadata = doc.connect(note.id, child.id)
    doc.set_edge_role(late.id, "primary")
    snapshot = copy.deepcopy(doc.edges)
    with pytest.raises(SceneError, match="already has an explicit primary"):
        doc.set_edge_role(early.id, "primary")
    with pytest.raises(SceneError, match="note cannot"):
        doc.set_edge_role(metadata.id, "primary")
    with pytest.raises(SceneError, match="invalid edge role"):
        doc.set_edge_role(late.id, "imaginary")
    with pytest.raises(SceneError, match="unknown edge"):
        doc.set_edge_role("missing", "primary")
    assert doc.edges == snapshot


def test_save_load_preserves_roles_and_legacy_files_default_correctly():
    doc, first, second, child, early, late = _two_parents()
    doc.set_edge_role(late.id, "primary")
    doc.set_edge_role(early.id, "reference")
    saved = build_chat_data(doc)
    rows = {
        (row["source"], row["target"]): row for row in saved["edges"]
    }
    assert rows[(first.id, child.id)]["role"] == "reference"
    assert rows[(second.id, child.id)]["role"] == "primary"
    notes = saved.pop("notes_data")
    pins = saved.pop("pins_data")
    restored = SceneDocument()
    restore_chat_into_document(restored, {"data": saved}, notes, pins)
    by_content = {n.content: n.id for n in restored.nodes.values()}
    assert len(restored.edges) == len(doc.edges)
    assert {
        (restored.nodes[e.source].content, restored.nodes[e.target].content): e.role
        for e in restored.edges.values()
    } == {("first", "child"): "reference", ("second", "child"): "primary"}
    assert restored._branch_parent_edge(by_content["child"]).source == by_content["second"]

    # Remove only the new additive attribute: an older save still restores
    # both connections and chooses its historical first-eligible parent.
    for row in saved["edges"]:
        row.pop("role", None)
    legacy = SceneDocument()
    restore_chat_into_document(legacy, {"data": saved}, notes, pins)
    legacy_child = next(n for n in legacy.nodes.values() if n.content == "child")
    assert len(legacy.edges) == len(doc.edges)
    assert all(e.role == "legacy" for e in legacy.edges.values())
    assert legacy._branch_parent_edge(legacy_child.id) is not None


def test_invalid_persisted_roles_fall_back_to_legacy_without_losing_link():
    doc, _first, _second, _child, _early, _late = _two_parents()
    saved = build_chat_data(doc)
    saved["edges"][0]["role"] = ["not", "a", "string"]
    saved["edges"][1]["role"] = "unknown-future-role"
    notes = saved.pop("notes_data")
    pins = saved.pop("pins_data")
    loaded = SceneDocument()
    restore_chat_into_document(loaded, {"data": saved}, notes, pins)
    assert len(loaded.edges) == len(doc.edges)
    assert all(edge.role == "legacy" for edge in loaded.edges.values())


def test_edge_role_edit_is_one_undoable_command():
    doc, _first, _second, child, _early, late = _two_parents()
    original_edges = copy.deepcopy(doc.edges)
    doc.record_command(
        "setEdgeRole", "user",
        lambda: doc.set_edge_role(late.id, "primary"),
        edge_ids=[late.id],
    )
    assert doc.edges[late.id].role == "primary"
    assert doc._branch_parent_edge(child.id).role == "primary"
    doc.undo()
    assert doc.edges == original_edges
    doc.redo()
    assert doc.edges[late.id].role == "primary"

@pytest.mark.parametrize("role", ["secondary", "reference", "association"])
def test_collapse_branch_excludes_nonstructural_chat_cross_links(role):
    doc = SceneDocument()
    root = doc.add_chat_node(0, 0, "root", True)
    child = doc.add_chat_node(0, 300, "child", False, parent_id=root.id)
    separate = doc.add_chat_node(900, 0, "other branch", True)
    cross_link = doc.connect(root.id, separate.id)
    doc.set_edge_role(cross_link.id, role)

    doc.collapse_branch(root.id, True)

    assert doc.nodes[root.id].is_collapsed
    assert doc.nodes[child.id].is_collapsed
    assert not doc.nodes[separate.id].is_collapsed


def test_collapse_branch_follows_explicit_primary_instead_of_earlier_legacy_link():
    doc, first, second, child, _early, late = _two_parents()
    doc.set_edge_role(late.id, "primary")

    doc.collapse_branch(first.id, True)
    assert not doc.nodes[child.id].is_collapsed

    doc.collapse_branch(second.id, True)
    assert doc.nodes[child.id].is_collapsed


def test_delete_chat_does_not_promote_reference_or_secondary_links():
    doc = SceneDocument()
    root = doc.add_chat_node(0, 0, "root", True)
    victim = doc.add_chat_node(0, 300, "victim", False, parent_id=root.id)
    child = doc.add_chat_node(0, 600, "real child", True, parent_id=victim.id)
    reference = doc.add_chat_node(900, 0, "referenced chat", True)
    secondary = doc.add_chat_node(1200, 0, "secondary", True)
    link1 = doc.connect(victim.id, reference.id)
    link2 = doc.connect(victim.id, secondary.id)
    doc.set_edge_role(link1.id, "reference")
    doc.set_edge_role(link2.id, "secondary")

    doc.delete_chat_node(victim.id)

    assert doc._branch_parent_edge(child.id).source == root.id
    assert not any(e.source == root.id and e.target in (reference.id, secondary.id)
                   for e in doc.edges.values())
    assert not any(e.source == victim.id or e.target == victim.id for e in doc.edges.values())


@pytest.mark.parametrize("explicit", [False, True])
def test_delete_chat_preserves_child_ancestry_amid_other_legacy_links(explicit):
    doc = SceneDocument()
    root = doc.add_chat_node(0, 0, "root", True)
    victim = doc.add_chat_node(0, 300, "victim", False, parent_id=root.id)
    child = doc.add_chat_node(0, 600, "real child", True, parent_id=victim.id)
    other = doc.add_chat_node(900, 0, "other parent", True)
    victim_edge = doc._branch_parent_edge(child.id)
    other_edge = doc.connect(other.id, child.id)
    if explicit:
        doc.set_edge_role(victim_edge.id, "primary")

    doc.delete_chat_node(victim.id)

    chosen = doc._branch_parent_edge(child.id)
    assert chosen is not None
    assert chosen.source == root.id
    assert chosen.role == "primary"
    assert other_edge.id in doc.edges


@pytest.mark.parametrize("root_already_collapsed", [False, True])
def test_collapse_branch_intent_undo_redo_tracks_all_descendants(root_already_collapsed):
    import asyncio
    from backend.tests.test_canvas import make_bus_with_dispatcher

    bus, doc, _recorder, _dispatcher = make_bus_with_dispatcher()
    root = doc.add_chat_node(0, 0, "root", True)
    child = doc.add_chat_node(0, 250, "child", False, parent_id=root.id)
    grandchild = doc.add_chat_node(0, 500, "grandchild", True, parent_id=child.id)
    doc.nodes[root.id].is_collapsed = root_already_collapsed
    before = {node.id: node.is_collapsed for node in (root, child, grandchild)}

    asyncio.run(bus.dispatch_intent("scene", "collapseBranch", [root.id, True]))
    assert all(doc.nodes[nid].is_collapsed for nid in before)
    assert doc.command_log and doc.command_log[-1].command_type == "collapseBranch"

    doc.undo()
    assert {nid: doc.nodes[nid].is_collapsed for nid in before} == before
    doc.redo()
    assert all(doc.nodes[nid].is_collapsed for nid in before)


def test_organize_docked_child_follows_explicit_primary_parent():
    doc = SceneDocument()
    first = doc.add_chat_node(0, 0, "first", True)
    second = doc.add_chat_node(1000, 0, "second", True)
    thinking = doc.add_thinking_node(0, 300, "draft thinking", first.id)
    new_parent = doc.connect(second.id, thinking.id)
    doc.set_edge_role(new_parent.id, "primary")
    doc.set_node_docked(thinking.id, True)

    doc.organize()

    assert (doc.nodes[thinking.id].x, doc.nodes[thinking.id].y) == (
        doc.nodes[second.id].x, doc.nodes[second.id].y
    )

def test_delete_chat_node_intent_rejects_an_image_without_losing_asset():
    import asyncio

    from backend.tests.test_canvas import make_bus

    bus, document, recorder = make_bus()
    chat = document.add_chat_node(0, 0, "user", True)
    image = document.add_image_node(0, 240, b"image bytes", "test prompt", chat.id)
    asset_id = image.state.image_asset_id
    original_edges = copy.deepcopy(document.edges)
    original_assets = dict(document.image_assets)
    recorder.messages.clear()

    with pytest.raises(SceneError, match="not a chat node"):
        asyncio.run(bus.dispatch_intent("scene", "deleteChatNode", [image.id]))

    assert image.id in document.nodes
    assert document.edges == original_edges
    assert document.image_assets == original_assets
    assert asset_id in document.image_assets
    assert recorder.topics_seen() == []


def test_delete_chat_node_domain_rejects_other_node_kinds():
    doc = SceneDocument()
    root = doc.add_chat_node(0, 0, "root", True)
    thinking = doc.add_thinking_node(0, 260, "notes", root.id)
    with pytest.raises(SceneError, match="not a chat node"):
        doc.delete_chat_node(thinking.id)
    assert thinking.id in doc.nodes
