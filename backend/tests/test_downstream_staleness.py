"""Generation-context staleness is branch-local, persistent and cheap to publish."""

from __future__ import annotations

from unittest.mock import patch

from backend.domain.graph import SceneDocument
from backend.domain.staleness import fingerprint_for_reply, scene_staleness
from backend.session_load import _restore_chat_payload
from backend.session_save import _serialize_chat_node


def _answer(doc, parent, text="Answer", *, model="model-a", knowledge=False):
    reply = doc.add_chat_node(0, 120, text, False, parent_id=parent.id)
    reply.state.provider = "Test"
    reply.state.model = model
    reply.state.reply_knowledge_enabled = knowledge
    reply.state.reply_history_fingerprint = fingerprint_for_reply(
        doc, parent.id, "Test", model, knowledge,
    )
    return reply


def _states(doc):
    return {row["id"]: row.get("staleness", "current") for row in doc.scene_payload()["nodes"]}


def test_ancestor_edit_marks_only_affected_branch_and_derived_nonchat():
    doc = SceneDocument()
    root = doc.add_chat_node(0, 0, "Root", True)
    left = doc.add_chat_node(0, 60, "Left", True, parent_id=root.id)
    right = doc.add_chat_node(200, 60, "Right", True, parent_id=root.id)
    left_reply = _answer(doc, left)
    right_reply = _answer(doc, right)
    child = doc.add_node(0, 240, "Derived code")
    nested = doc.add_node(0, 300, "Derived note")
    doc.connect(left_reply.id, child.id)
    doc.connect(child.id, nested.id)
    assert set(_states(doc).values()) == {"current"}

    doc.update_chat_node_content(left.id, "Left revised")
    statuses = _states(doc)
    assert statuses[left_reply.id] == "out_of_date"
    assert statuses[right_reply.id] == "current"
    assert statuses[child.id] == "upstream_changed"
    assert statuses[nested.id] == "upstream_changed"
    doc.take_dirty_patch_ops()
    doc.update_chat_node_content(left.id, "Left")
    ops = doc.take_dirty_patch_ops()
    assert any(
        op["op"] == "upsertNode" and op["node"]["id"] == left_reply.id
        and "staleness" not in op["node"] for op in ops
    )
    assert set(_states(doc).values()) == {"current"}


def test_model_knowledge_and_multimodal_content_invalidate_but_geometry_does_not():
    doc = SceneDocument()
    parent = doc.add_chat_node(
        0, 0, "What is here?", True,
        content_parts=[
            {"type": "text", "text": "What is here?"},
            {"type": "image_bytes", "data": b"image-a"},
        ],
    )
    reply = _answer(doc, parent, knowledge=True)
    assert _states(doc)[reply.id] == "current"

    parent.x = 999.0
    parent.title = "renamed without touching context"
    assert _states(doc)[reply.id] == "current"
    parent.state.content_parts[1]["data"] = b"image-b"
    assert _states(doc)[reply.id] == "out_of_date"
    parent.state.content_parts[1]["data"] = b"image-a"
    assert _states(doc)[reply.id] == "current"

    doc.set_model_override(parent.id, "Other", "model-b")
    assert _states(doc)[reply.id] == "out_of_date"
    doc.clear_model_override(parent.id)
    assert _states(doc)[reply.id] == "current"
    doc.set_use_knowledge_override(parent.id, False)
    assert _states(doc)[reply.id] == "out_of_date"
    doc.set_use_knowledge_override(parent.id, None)
    assert _states(doc)[reply.id] == "current"


def test_reference_edges_do_not_make_an_unrelated_branch_stale():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "Input", True)
    foreign = doc.add_chat_node(300, 0, "Unrelated", True)
    reply = _answer(doc, parent)
    edge = doc.connect(foreign.id, reply.id)
    doc.set_edge_role(edge.id, "reference")
    doc.update_chat_node_content(foreign.id, "Unrelated edited")
    assert _states(doc)[reply.id] == "current"
    doc.update_chat_node_content(parent.id, "Input edited")
    assert _states(doc)[reply.id] == "out_of_date"


def test_interrupted_reply_and_regeneration_keep_request_time_context():
    from backend.api.intents_chat import _land_partial_reply_node, _land_partial_regenerate

    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "original question", True)
    proof = fingerprint_for_reply(doc, parent.id, "Test", "model-a", False)
    _land_partial_reply_node(doc, parent, "partial", proof, False, "Test", "model-a")
    reply = doc.nodes[doc.last_chat_node_id]
    assert reply.state.response_incomplete is True
    assert reply.state.reply_history_fingerprint == proof
    assert _states(doc)[reply.id] == "current"

    doc.update_chat_node_content(parent.id, "edited question")
    assert _states(doc)[reply.id] == "out_of_date"
    retry_proof = fingerprint_for_reply(doc, parent.id, "Test", "model-b", False)
    _land_partial_regenerate(doc, reply, "retry partial", retry_proof, False, "Test", "model-b")
    assert reply.state.reply_history_fingerprint == retry_proof
    assert reply.state.model == "model-b"
    assert _states(doc)[reply.id] == "current"


def test_pre_feature_graphs_and_malformed_saved_proofs_never_flag_stale():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "Old user", True)
    old_reply = _restore_chat_payload({"raw_content": "Old answer", "is_user": False})
    doc.register_restored_node(old_reply)
    doc.connect(parent.id, old_reply.id)
    doc.update_chat_node_content(parent.id, "Old user updated")
    assert scene_staleness(doc) == {}
    assert _states(doc)[old_reply.id] == "current"
    bad = _restore_chat_payload({
        "raw_content": "a", "is_user": False,
        "reply_history_fingerprint": "not-a-versioned-sha256",
    })
    assert bad.state.reply_history_fingerprint is None


def test_saved_proofs_survive_new_ids_and_detect_real_content_edits():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "Question", True)
    reply = _answer(doc, parent, knowledge=True)
    saved_parent = _serialize_chat_node(parent)
    saved_reply = _serialize_chat_node(reply)
    assert saved_reply["reply_history_fingerprint"] == reply.state.reply_history_fingerprint

    loaded = SceneDocument()
    loaded.add_node(0, 0)
    loaded_parent = loaded.register_restored_node(_restore_chat_payload(saved_parent))
    loaded_reply = loaded.register_restored_node(_restore_chat_payload(saved_reply))
    loaded.connect(loaded_parent.id, loaded_reply.id)
    assert _states(loaded)[loaded_reply.id] == "current"
    loaded.update_chat_node_content(loaded_parent.id, "New question")
    assert _states(loaded)[loaded_reply.id] == "out_of_date"


def test_publish_hashes_each_message_once_even_on_a_deep_branch():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "Start", True)
    for i in range(250):
        parent = doc.add_chat_node(0, 0, f"Turn {i}", i % 2 == 0, parent_id=parent.id)
    reply = _answer(doc, parent)
    import backend.domain.staleness as staleness
    with patch.object(staleness, "_message_input", wraps=staleness._message_input) as hash_message:
        doc.scene_payload()
        assert hash_message.call_count == len(doc.nodes)
        doc.take_dirty_patch_ops()
        assert hash_message.call_count == 2 * len(doc.nodes)
    # The existing 500-node event-loop watchdog additionally guards wall time.
    doc.update_chat_node_content(parent.id, "Different")
    assert _states(doc)[reply.id] == "out_of_date"
