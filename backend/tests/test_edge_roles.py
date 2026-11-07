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
    doc, first, _second, child, early, late = _two_parents()
    doc.set_edge_role(early.id, role)
    doc.set_edge_role(late.id, role)
    assert doc._branch_parent_edge(child.id) is None
    assert child.id not in primary_parent_edges(doc.nodes, doc.edges, set(doc.nodes))
    assert doc.edges[early.id].role == role
    assert len(doc.edges) == 2


def test_conflicting_primary_roles_and_note_ancestry_fail_atomically():
    doc, first, second, child, early, late = _two_parents()
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
    doc, first, _second, child, early, _late = _two_parents()
    saved = build_chat_data(doc)
    saved["edges"][0]["role"] = ["not", "a", "string"]
    saved["edges"][1]["role"] = "unknown-future-role"
    notes = saved.pop("notes_data")
    pins = saved.pop("pins_data")
    loaded = SceneDocument()
    restore_chat_into_document(loaded, {"data": saved}, notes, pins)
    assert len(loaded.edges) == len(doc.edges)
    assert all(edge.role == "legacy" for edge in loaded.edges.values())
