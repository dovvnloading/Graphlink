"""Reload keeps the saved flat edge order, so legacy chat parents survive (#1092)."""

from __future__ import annotations

from backend.canvas import SceneDocument
from backend.domain.structure import preferred_parent_edge, primary_parent_edges
from backend.session_load import restore_chat_into_document
from backend.session_save import build_chat_data


def _reload(doc):
    raw = build_chat_data(doc)
    reopened = SceneDocument()
    restore_chat_into_document(reopened, {"data": raw}, raw.get("notes_data", []), [])
    return reopened, raw


def _by_content(doc, text):
    return next(node for node in doc.nodes.values() if node.content == text)


def _view(doc):
    answer = _by_content(doc, "Shared answer")
    parent = preferred_parent_edge(doc.nodes, doc.edges, answer.id)
    layout = primary_parent_edges(doc.nodes, doc.edges, set(doc.nodes))[answer.id]
    order = [(doc.nodes[e.source].content, doc.nodes[e.target].content, e.role) for e in doc.edges.values()]
    history = [row.get("content") for row in doc.chat_branch_history(answer.id)]
    return doc.nodes[parent.source].content, doc.nodes[layout.source].content, order, history


def _graph():
    doc = SceneDocument()
    a = doc.add_chat_node(0, 0, "Alpha question", True)
    b = doc.add_chat_node(0, 200, "Beta question", True)
    c = doc.add_chat_node(300, 0, "Shared answer", False)
    # Connection order (B first) is the reverse of node order (A first).
    doc.connect(b.id, c.id)
    doc.connect(a.id, c.id)
    return doc


def test_connection_order_wins_over_node_order_across_repeated_reloads():
    doc = _graph()
    expected = _view(doc)
    assert expected[0] == expected[1] == "Beta question"
    for _ in range(3):
        doc, _raw = _reload(doc)
        assert _view(doc) == expected


def test_mixed_typed_and_legacy_roles_round_trip_in_order():
    doc = _graph()
    d = doc.add_chat_node(0, 400, "Delta note", True)
    reference = doc.connect(d.id, _by_content(doc, "Shared answer").id)
    reference.role = "reference"
    expected = _view(doc)
    reopened, _raw = _reload(doc)
    assert _view(reopened) == expected


def test_legacy_only_payload_and_missing_endpoints_still_load():
    doc = _graph()
    raw = build_chat_data(doc)
    legacy = {key: value for key, value in raw.items() if key != "edges"}
    reopened = SceneDocument()
    restore_chat_into_document(reopened, {"data": legacy}, legacy.get("notes_data", []), [])
    answer = _by_content(reopened, "Shared answer")
    assert preferred_parent_edge(reopened.nodes, reopened.edges, answer.id) is not None
    broken = dict(raw, edges=[{"source": "missing", "target": "also-missing"}, *raw["edges"]])
    reopened = SceneDocument()
    restore_chat_into_document(reopened, {"data": broken}, broken.get("notes_data", []), [])
    assert _view(reopened)[0] == "Beta question"
