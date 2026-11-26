"""Undo/redo restore edge order, so legacy chat ancestry is unchanged (#1091)."""

from __future__ import annotations

from backend.canvas import SceneDocument
from backend.domain.structure import preferred_parent_edge, primary_parent_edges


def _graph():
    doc = SceneDocument()
    a = doc.add_chat_node(0, 0, "Alpha question", True)
    b = doc.add_chat_node(0, 200, "Beta question", True)
    c = doc.add_chat_node(300, 0, "Shared answer", False)
    first = doc.connect(a.id, c.id)
    second = doc.connect(b.id, c.id)
    return doc, a, b, c, first, second


def _state(doc, c):
    parent = preferred_parent_edge(doc.nodes, doc.edges, c.id)
    layout = primary_parent_edges(doc.nodes, doc.edges, set(doc.nodes))[c.id]
    history = [row.get("content") for row in doc.chat_branch_history(c.id)]
    return list(doc.edges), parent.source, layout.source, history


def test_undoing_removal_of_the_first_legacy_edge_restores_the_parent():
    doc, a, b, c, first, _ = _graph()
    before = _state(doc, c)
    assert before[1] == before[2] == a.id
    doc.record_command("removeEdges", "user", lambda: doc.remove_edges([first.id]), edge_ids=[first.id])
    assert _state(doc, c)[1] == b.id
    doc.undo()
    assert _state(doc, c) == before
    doc.redo()
    assert _state(doc, c)[1] == b.id
    doc.undo()
    assert _state(doc, c) == before


def test_undoing_removal_of_both_edges_restores_their_order():
    doc, a, _, c, first, second = _graph()
    before = _state(doc, c)
    doc.record_command("removeEdges", "user", lambda: doc.remove_edges([second.id, first.id]),
                       edge_ids=[first.id, second.id])
    doc.undo()
    assert _state(doc, c) == before


def test_undoing_node_deletion_restores_the_original_parent():
    doc, a, _, c, _, _ = _graph()
    before = _state(doc, c)
    doc.record_command("removeNodes", "user", lambda: doc.remove_nodes([a.id]), node_ids=[a.id])
    doc.undo()
    assert _state(doc, c) == before
    assert _state(doc, c)[1] == a.id


def test_explicit_primary_still_wins_after_undo():
    doc, a, b, c, first, second = _graph()
    second.role = "primary"
    doc.record_command("removeEdges", "user", lambda: doc.remove_edges([first.id]), edge_ids=[first.id])
    doc.undo()
    assert preferred_parent_edge(doc.nodes, doc.edges, c.id).source == b.id


def test_save_and_reload_after_undo_keeps_the_restored_parent():
    from backend.session_load import restore_chat_into_document
    from backend.session_save import build_chat_data

    doc, a, _, c, first, _ = _graph()
    doc.record_command("removeEdges", "user", lambda: doc.remove_edges([first.id]), edge_ids=[first.id])
    doc.undo()
    raw = build_chat_data(doc)
    reopened = SceneDocument()
    restore_chat_into_document(reopened, {"data": raw}, raw.get("notes_data", []), [])
    answer = next(node for node in reopened.nodes.values() if node.content == "Shared answer")
    parent = preferred_parent_edge(reopened.nodes, reopened.edges, answer.id)
    assert reopened.nodes[parent.source].content == "Alpha question"
