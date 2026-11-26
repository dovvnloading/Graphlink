"""Regeneration tears down only the reply's own artifacts (#1101)."""

from __future__ import annotations

import pytest

from backend.canvas import SceneDocument
from backend.session_load import restore_chat_into_document
from backend.session_save import build_chat_data

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


def _artifact(doc, kind, parent_id):
    if kind == "code":
        return doc.add_code_node(0, 0, "return 'owned by B'", "python", parent_id=parent_id)
    if kind == "thinking":
        return doc.add_thinking_node(0, 0, "B's reasoning", parent_id=parent_id)
    if kind == "document":
        return doc.add_document_node(0, 0, "notes.txt", "B's document", "text", parent_id)
    return doc.add_image_node(0, 0, PNG, "B's image", parent_id)


def _scene(kind, role):
    doc = SceneDocument()
    user = doc.add_chat_node(0, 0, "question", True)
    a = doc.add_chat_node(0, 0, "answer A", False, parent_id=user.id)
    b = doc.add_chat_node(0, 0, "answer B", False, parent_id=user.id)
    shared = _artifact(doc, kind, b.id)
    owning = next(e for e in doc.edges.values() if e.source == b.id and e.target == shared.id)
    owning.role = "primary"
    link = doc.connect(a.id, shared.id)
    link.role = role
    return doc, a, b, shared, owning


@pytest.mark.parametrize("kind", ["code", "thinking", "document", "image"])
@pytest.mark.parametrize("role", ["reference", "secondary", "association"])
def test_non_owning_links_never_delete_another_replys_artifact(kind, role):
    doc, a, _b, shared, owning = _scene(kind, role)
    content = shared.content
    doc.record_command("regenerateResponse", "agent",
                       lambda: doc.remove_associated_content_children(a.id), node_ids=[a.id, shared.id])
    assert shared.id in doc.nodes and doc.nodes[shared.id].content == content
    assert owning.id in doc.edges


def test_a_shared_legacy_artifact_is_detached_not_deleted():
    doc = SceneDocument()
    user = doc.add_chat_node(0, 0, "question", True)
    a = doc.add_chat_node(0, 0, "answer A", False, parent_id=user.id)
    b = doc.add_chat_node(0, 0, "answer B", False, parent_id=user.id)
    shared = doc.add_code_node(0, 0, "shared", "python", parent_id=a.id)  # A is the canonical parent
    doc.connect(b.id, shared.id)
    doc.remove_associated_content_children(a.id)
    assert shared.id in doc.nodes
    assert not any(e.source == a.id and e.target == shared.id for e in doc.edges.values())
    assert any(e.source == b.id and e.target == shared.id for e in doc.edges.values())


def test_the_replys_own_artifacts_still_refresh_and_undo_restores_them():
    doc = SceneDocument()
    user = doc.add_chat_node(0, 0, "question", True)
    a = doc.add_chat_node(0, 0, "answer A", False, parent_id=user.id)
    code = doc.add_code_node(0, 0, "old", "python", parent_id=a.id)
    thought = doc.add_thinking_node(0, 0, "old reasoning", parent_id=a.id)
    doc.record_command("regenerateResponse", "agent",
                       lambda: doc.remove_associated_content_children(a.id), node_ids=[a.id, code.id, thought.id])
    assert code.id not in doc.nodes and thought.id not in doc.nodes
    doc.undo()
    assert code.id in doc.nodes and thought.id in doc.nodes


def test_ownership_survives_save_and_reload():
    doc, _a, _b, _shared, _owning = _scene("code", "reference")
    raw = build_chat_data(doc)
    reopened = SceneDocument()
    restore_chat_into_document(reopened, {"data": raw}, raw.get("notes_data", []), [])
    a = next(n for n in reopened.nodes.values() if n.content == "answer A")
    reopened.remove_associated_content_children(a.id)
    assert any(n.kind == "code" and n.state.code == "return 'owned by B'" for n in reopened.nodes.values())
