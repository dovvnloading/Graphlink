"""Use-knowledge defaults, branch overrides, and saved-chat compatibility."""

import asyncio

import pytest

from backend import knowledge_store
from backend.api.intents_chat import _use_knowledge_for_node
from backend.domain.graph import SceneDocument
from backend.domain.model import SceneError
from backend.knowledge_chunking import chunk_text
from backend.knowledge_retrieval import chat_use_knowledge, workspace_has_chat_knowledge
from backend.knowledge_store import add_document_with_chunks, get_or_create_workspace_collection
from backend.session_load import _restore_chat_payload
from backend.session_save import _serialize_chat_node


def _branches():
    document = SceneDocument()
    root = document.add_chat_node(0, 0, "root", True)
    left = document.add_chat_node(0, 150, "left", False, parent_id=root.id)
    right = document.add_chat_node(200, 150, "right", False, parent_id=root.id)
    return document, root, left, right


def test_defaults_reflect_only_documents_in_current_workspace(tmp_path, monkeypatch):
    db = tmp_path / "knowledge.db"
    monkeypatch.setattr(knowledge_store, "DEFAULT_DB_PATH", db)
    document, root, _, _ = _branches()
    document.current_workspace_id = 12

    assert workspace_has_chat_knowledge(12) is False
    assert chat_use_knowledge(document, root.id) is False
    assert asyncio.run(_use_knowledge_for_node(document, root.id)) is False
    assert not db.exists()

    collection = get_or_create_workspace_collection(db, 12)
    assert workspace_has_chat_knowledge(12) is False  # collection without docs
    content = "A verification procedure for service restore."
    add_document_with_chunks(
        db, source_uri="runbook.md", title="Runbook",
        mime="text/markdown", text=content,
        chunks=chunk_text(content), collection_id=collection,
    )
    assert workspace_has_chat_knowledge(12) is True
    assert workspace_has_chat_knowledge(13) is False
    assert workspace_has_chat_knowledge(None) is False
    assert chat_use_knowledge(document, root.id) is True
    assert asyncio.run(_use_knowledge_for_node(document, root.id)) is True

    document.current_workspace_id = 13
    assert chat_use_knowledge(document, root.id) is False


def test_nearest_branch_override_isolated_from_siblings(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge_store, "DEFAULT_DB_PATH", tmp_path / "absent.db")
    document, root, left, right = _branches()
    assert document.resolve_use_knowledge_override(left.id) is None
    document.set_use_knowledge_override(root.id, True)
    assert chat_use_knowledge(document, left.id) is True
    assert chat_use_knowledge(document, right.id) is True

    document.set_use_knowledge_override(left.id, False)
    assert chat_use_knowledge(document, left.id) is False
    assert chat_use_knowledge(document, right.id) is True
    assert asyncio.run(_use_knowledge_for_node(document, left.id)) is False
    document.set_use_knowledge_override(left.id, None)
    assert document.resolve_use_knowledge_override(left.id) is True

    with pytest.raises(SceneError, match="boolean"):
        document.set_use_knowledge_override(left.id, "off")
    with pytest.raises(SceneError, match="chat"):
        document.set_use_knowledge_override("not-a-node", False)


def test_override_survives_save_reload_and_legacy_values_are_auto():
    document, root, _, _ = _branches()
    document.set_use_knowledge_override(root.id, False)
    payload = _serialize_chat_node(root)
    assert payload["use_knowledge"] is False
    assert _restore_chat_payload(payload).state.use_knowledge is False
    assert _restore_chat_payload({**payload, "use_knowledge": True}).state.use_knowledge is True
    assert _restore_chat_payload({**payload, "use_knowledge": "false"}).state.use_knowledge is None
    legacy = dict(payload)
    legacy.pop("use_knowledge")
    assert _restore_chat_payload(legacy).state.use_knowledge is None
