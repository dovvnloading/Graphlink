"""Generation-context staleness is branch-local, persistent and cheap to publish."""

from __future__ import annotations

from unittest.mock import patch

import backend.agents as agents_module  # noqa: F401 - preserve canvas/session import order
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


def test_nonchat_attached_directly_to_a_message_tracks_that_message():
    doc = SceneDocument()
    message = doc.add_chat_node(0, 0, "first input", True)
    code = doc.add_code_node(0, 100, "print(1)", "python", message.id)
    assert code.id in doc.nonchat_upstream_fingerprints
    assert scene_staleness(doc) == {}
    doc.update_chat_node_content(message.id, "edited input")
    assert _states(doc)[code.id] == "upstream_changed"
    # A new derived node is based on the new content, not the old input.
    fresh = doc.add_code_node(100, 100, "print(2)", "python", message.id)
    assert _states(doc)[fresh.id] == "current"
    doc.update_chat_node_content(message.id, "first input")
    assert _states(doc)[code.id] == "current"
    assert _states(doc)[fresh.id] == "upstream_changed"


def test_direct_nonchat_context_round_trips_but_pre_feature_graph_has_no_baseline():
    from backend.session_save import build_chat_data
    from backend.session_load import restore_chat_into_document

    doc = SceneDocument()
    message = doc.add_chat_node(0, 0, "original", True)
    code = doc.add_code_node(0, 120, "print(1)", "python", message.id)
    saved = build_chat_data(doc)
    assert code.id in saved["nonchat_upstream_fingerprints"]
    notes, pins = saved.pop("notes_data"), saved.pop("pins_data")
    restored = SceneDocument()
    restore_chat_into_document(restored, {"data": saved}, notes, pins)
    loaded_message = next(n for n in restored.nodes.values() if n.kind == "chat")
    loaded_code = next(n for n in restored.nodes.values() if n.kind == "code")
    assert loaded_code.id in restored.nonchat_upstream_fingerprints
    assert _states(restored)[loaded_code.id] == "current"
    restored.update_chat_node_content(loaded_message.id, "different")
    assert _states(restored)[loaded_code.id] == "upstream_changed"

    saved.pop("nonchat_upstream_fingerprints")
    old = SceneDocument()
    restore_chat_into_document(old, {"data": saved}, notes, pins)
    old_message = next(n for n in old.nodes.values() if n.kind == "chat")
    old_code = next(n for n in old.nodes.values() if n.kind == "code")
    old.update_chat_node_content(old_message.id, "different")
    assert scene_staleness(old) == {}
    assert _states(old)[old_code.id] == "current"


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


def test_only_selected_version_changes_downstream_staleness():
    doc = SceneDocument()
    question = doc.add_chat_node(0, 0, "first question", True)
    answer = _answer(doc, question, text="first answer")
    downstream = _answer(doc, answer, text="dependent")
    assert _states(doc)[downstream.id] == "current"

    # Regeneration appends a new current version, leaving a hidden original.
    doc.update_chat_node_content(answer.id, "second answer")
    answer.state.provider = "Test"
    answer.state.model = "model-a"
    answer.state.reply_history_fingerprint = fingerprint_for_reply(
        doc, question.id, "Test", "model-a", False,
    )
    answer.state.reply_knowledge_enabled = False
    doc.sync_chat_version(answer.id)
    assert _states(doc)[downstream.id] == "out_of_date"

    doc.set_chat_version(answer.id, 0)
    assert _states(doc)[downstream.id] == "current"
    # The unselected stored answer's text must not enter the hash.
    answer.state.versions[1].content = "hidden history changed"
    assert _states(doc)[downstream.id] == "current"
    doc.set_chat_version(answer.id, 1)
    assert _states(doc)[downstream.id] == "out_of_date"


def test_reply_versions_restore_their_own_fingerprint_after_switch_and_reload():
    doc = SceneDocument()
    question = doc.add_chat_node(0, 0, "original question", True)
    answer = _answer(doc, question, text="old answer")
    old_proof = answer.state.reply_history_fingerprint
    doc.sync_chat_version(answer.id)
    doc.update_chat_node_content(question.id, "edited question")
    doc.update_chat_node_content(answer.id, "new answer")
    new_proof = fingerprint_for_reply(doc, question.id, "Test", "model-b", True)
    answer.state.provider = "Test"
    answer.state.model = "model-b"
    answer.state.reply_history_fingerprint = new_proof
    answer.state.reply_knowledge_enabled = True
    doc.sync_chat_version(answer.id)
    assert _states(doc)[answer.id] == "current"

    doc.set_chat_version(answer.id, 0)
    assert answer.state.reply_history_fingerprint == old_proof
    assert _states(doc)[answer.id] == "out_of_date"
    doc.set_chat_version(answer.id, 1)
    assert answer.state.reply_history_fingerprint == new_proof
    assert _states(doc)[answer.id] == "current"

    saved = _serialize_chat_node(answer)
    assert [row["reply_history_fingerprint"] for row in saved["versions"]] == [
        old_proof, new_proof,
    ]
    restored = _restore_chat_payload(saved)
    assert restored.state.reply_history_fingerprint == new_proof
    assert restored.state.versions[0].history_fingerprint == old_proof
    assert restored.state.versions[1].knowledge_enabled is True
