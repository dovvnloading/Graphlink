"""Executable text inputs through domain commands, graph storage, and archive transport."""

import pytest

import backend.agents  # noqa: F401 - initialize canvas/agents in the supported import order
from backend.chat_library import get_all_workspaces, import_workspace_rows_atomically, load_chat_snapshot, save_chat_atomically_row
from backend.domain.graph import SceneDocument
from backend.domain.model import SceneError
from backend.session_load import restore_chat_into_document
from backend.session_save import build_chat_data
from backend.workspace_archive import export_archive


def test_explicit_runs_copy_exact_text_one_hop_and_count_words():
    doc = SceneDocument()
    chat = doc.add_chat_node(0, 0, " \tfirst\u2003second\r\n🦊  ", True)
    note = doc.add_note(0, 0)
    count = doc.add_note(0, 0)
    doc.connect(note.id, chat.id)  # Opposite structural direction is valid.
    doc.bind_note_text_input(chat.id, note.id)
    doc.bind_note_text_input(note.id, count.id, "word_count")
    assert doc._node_wire(note)["textInputSourceId"] == chat.id
    assert doc._node_wire(count)["textInputOperation"] == "word_count"
    assert [(e.source, e.target) for e in doc.edges.values()] == [(note.id, chat.id)]
    assert note.content == count.content == "Add note..."

    doc.run_note_text_input(note.id)
    assert note.content == chat.content
    assert count.content == "Add note..."
    doc.run_note_text_input(count.id)
    assert count.content == "3"

    chat.content = ""
    doc.run_note_text_input(note.id)
    assert note.content == ""
    assert count.content == "3"
    doc.run_note_text_input(count.id)
    assert count.content == "0"
    doc.unbind_note_text_input(count.id)
    assert count.content == "0"
    assert "textInputSourceId" not in doc._node_wire(count)
    assert "textInputOperation" not in doc._node_wire(count)
    with pytest.raises(SceneError):
        doc.run_note_text_input(count.id)


def test_binding_validation_is_atomic_and_existing_binding_idempotent():
    doc = SceneDocument()
    first, second, third = [doc.add_note(0, 0) for _ in range(3)]
    doc.bind_note_text_input(first.id, second.id)
    doc.bind_note_text_input(second.id, third.id)
    doc.bind_note_text_input(second.id, third.id)
    for source, target, op in [
        (first.id, first.id, "copy"), (third.id, first.id, "copy"),
        (first.id, third.id, "copy"), (second.id, third.id, "unknown"),
        ("missing", first.id, "copy"), (first.id, "missing", "copy"),
    ]:
        with pytest.raises(SceneError):
            doc.bind_note_text_input(source, target, op)
    assert first.state.text_input_source_id == ""
    assert third.state.text_input_source_id == second.id
    assert third.state.text_input_operation == "copy"
    doc.bind_note_text_input(second.id, third.id, "word_count")
    assert third.state.text_input_operation == "word_count"
    assert third.content == "Add note..."


def test_only_plain_notes_can_receive_and_only_supported_nodes_can_supply_text():
    doc = SceneDocument()
    plain = doc.add_note(0, 0)
    prompt = doc.add_note(0, 0, is_system_prompt=True)
    summary = doc.add_note(0, 0, is_summary_note=True)
    comparison = doc.add_note(0, 0)
    doc.mark_branch_comparison_note(comparison.id, [])
    chat = doc.add_chat_node(0, 0, "message", True)
    attachment = doc.add_document_node(0, 0, "attached", "file", "document", chat.id)
    artifact = doc.add_artifact_node(0, 0, chat.id)
    artifact.content = "draft instruction"
    artifact.state.artifact_proposal_content = "unaccepted candidate"
    doc.set_artifact_content(artifact.id, "committed draft", "")
    for target in (prompt, summary, comparison, chat, attachment, artifact):
        with pytest.raises(SceneError):
            doc.bind_note_text_input(plain.id, target.id)
    for source in (prompt, summary, comparison, attachment):
        with pytest.raises(SceneError):
            doc.bind_note_text_input(source.id, plain.id)
    doc.bind_note_text_input(artifact.id, plain.id)
    doc.run_note_text_input(plain.id)
    assert plain.content == "committed draft"


@pytest.mark.parametrize("delete_chat", [False, True])
def test_source_deletion_disconnects_all_readers_and_undo_restores_them(delete_chat):
    doc = SceneDocument()
    source = doc.add_chat_node(0, 0, "one two", True)
    a, b = [doc.add_note(0, 0) for _ in range(2)]
    doc.bind_note_text_input(source.id, a.id, "word_count")
    doc.bind_note_text_input(source.id, b.id)
    doc.run_note_text_input(a.id)
    delete = lambda: doc.delete_chat_node(source.id) if delete_chat else doc.remove_nodes([source.id])
    doc.record_command("deleteChatNode" if delete_chat else "removeNodes", "user", delete, node_ids=[source.id])
    assert a.content == "2"
    assert a.state.text_input_source_id == b.state.text_input_source_id == ""
    assert a.state.text_input_operation == "copy"
    doc.undo()
    assert doc.nodes[a.id].state.text_input_source_id == doc.nodes[b.id].state.text_input_source_id == source.id
    assert doc.nodes[a.id].state.text_input_operation == "word_count"
    doc.redo()
    assert doc.nodes[a.id].state.text_input_source_id == doc.nodes[b.id].state.text_input_source_id == ""
    assert doc.nodes[a.id].content == "2"


def test_commands_reverse_connection_evaluation_and_disconnection_independently():
    doc = SceneDocument()
    a, b = [doc.add_note(0, 0) for _ in range(2)]
    doc.set_note_content(a.id, "two words")
    doc.record_command("bindNoteTextInput", "user", lambda: doc.bind_note_text_input(a.id, b.id, "word_count"), node_ids=[b.id])
    doc.record_command("runNoteTextInput", "user", lambda: doc.run_note_text_input(b.id), node_ids=[b.id])
    assert doc.nodes[b.id].content == "2"
    doc.record_command("unbindNoteTextInput", "user", lambda: doc.unbind_note_text_input(b.id), node_ids=[b.id])
    doc.undo()
    assert doc.nodes[b.id].state.text_input_operation == "word_count"
    doc.undo()
    assert doc.nodes[b.id].content == "Add note..."
    assert doc.nodes[b.id].state.text_input_source_id == a.id
    doc.undo()
    assert doc.nodes[b.id].state.text_input_source_id == ""
    for _ in range(3):
        doc.redo()
    assert doc.nodes[b.id].state.text_input_source_id == ""
    assert doc.nodes[b.id].content == "2"


def test_sqlite_and_archive_roundtrip_remap_all_sources_and_protect_comparisons(tmp_path):
    doc = SceneDocument()
    doc.remove_nodes([doc.add_note(0, 0).id])  # Force runtime and persisted IDs to differ.
    source = doc.add_note(0, 0)
    doc.set_note_content(source.id, "a note")
    chat = doc.add_chat_node(0, 0, "a chat message", True)
    artifact = doc.add_artifact_node(0, 0, chat.id)
    doc.set_artifact_content(artifact.id, "a committed draft document", "")
    for node in (source, chat, artifact):
        target = doc.add_note(0, 0)
        doc.set_note_content(target.id, node.kind)
        doc.bind_note_text_input(node.id, target.id, "word_count")
    comparison = doc.add_note(0, 0)
    doc.set_note_content(comparison.id, "comparison")
    doc.mark_branch_comparison_note(comparison.id, [chat.id, "missing member"])
    data = build_chat_data(doc)
    notes, pins = data.pop("notes_data"), data.pop("pins_data")
    db = tmp_path / "graphs.db"
    graph_id, _ = save_chat_atomically_row(db, None, "Text inputs", data, notes, pins)
    row, notes, pins = load_chat_snapshot(db, graph_id)
    archive = tmp_path / "text.graphlink"
    export_archive(archive, [{**row, "notes": notes, "pins": pins}])
    imported_db = tmp_path / "imported.db"
    workspace_id = get_all_workspaces(imported_db)[0]["id"]
    imported_id, = import_workspace_rows_atomically(imported_db, archive, workspace_id)
    for row, notes, pins in [(row, notes, pins), load_chat_snapshot(imported_db, imported_id)]:
        restored = SceneDocument()
        restore_chat_into_document(restored, row, notes, pins)
        by_content = {node.content: node for node in restored.nodes.values() if node.kind == "note"}
        for kind, words in (("note", "2"), ("chat", "3"), ("artifact", "4")):
            target = by_content[kind]
            assert restored.nodes[target.state.text_input_source_id].kind == kind
            restored.run_note_text_input(target.id)
            assert target.content == words
        protected = by_content["comparison"]
        new_chat = next(n for n in restored.nodes.values() if n.kind == "chat")
        assert new_chat.id != chat.id
        assert protected.item_ids == [new_chat.id]
        with pytest.raises(SceneError):
            restored.bind_note_text_input(new_chat.id, protected.id)


def test_loader_skips_cycles_and_malformed_bindings_but_retains_independent_work():
    doc = SceneDocument()
    a, b, c, d, e, f = [doc.add_note(0, 0) for _ in range(6)]
    for node in doc.nodes.values():
        doc.set_note_content(node.id, node.id)
    data = build_chat_data(doc)
    assert "note_text_inputs" not in data
    data["note_text_inputs"] = {
        a.id: {"source": b.id}, b.id: {"source": a.id}, c.id: {"source": []},
        d.id: {"source": "missing"}, e.id: {"source": a.id, "operation": ["bad"]},
        f.id: {"source": a.id, "operation": "copy"}, "missing": {"source": a.id},
    }
    restored = SceneDocument()
    restore_chat_into_document(restored, {"data": data}, data["notes_data"], [])
    by_original = {n.content: n for n in restored.nodes.values()}
    assert all(by_original[n.id].state.text_input_source_id == "" for n in (a, b, c, d, e))
    assert by_original[f.id].state.text_input_source_id == by_original[a.id].id


def test_input_freshness_tracks_upstream_bytes_on_patch_snapshot_and_undo():
    doc = SceneDocument()
    source, target = [doc.add_note(0, 0) for _ in range(2)]
    doc.bind_note_text_input(source.id, target.id)
    assert doc._node_wire(target)["textInputStatus"] == "unrun"
    # Source and target already contain identical text. Recording an evaluation
    # still changes provenance and must create a reversible command.
    doc.record_command("runNoteTextInput", "user", lambda: doc.run_note_text_input(target.id), node_ids=[target.id])
    assert doc._node_wire(target)["textInputStatus"] == "current"
    doc.undo()
    assert doc._node_wire(doc.nodes[target.id])["textInputStatus"] == "unrun"
    doc.redo()
    target = doc.nodes[target.id]
    doc.bind_note_text_input(source.id, target.id)
    doc.set_note_content(target.id, "manual edit")
    assert doc._node_wire(target)["textInputStatus"] == "current"

    assert doc.take_dirty_patch_ops() is None
    doc.record_command("setNoteContent", "user", lambda: doc.set_note_content(source.id, "changed"), node_ids=[source.id])
    patches = doc.take_dirty_patch_ops()
    assert any(op.get("node", {}).get("textInputStatus") == "stale" for op in patches)
    assert target.content == "manual edit"
    doc.undo()
    assert doc._node_wire(target)["textInputStatus"] == "current"
    doc.bind_note_text_input(source.id, target.id, "word_count")
    assert doc._node_wire(target)["textInputStatus"] == "unrun"


def test_input_freshness_persists_even_when_source_and_output_have_diverged(tmp_path):
    doc = SceneDocument()
    chat = doc.add_chat_node(0, 0, "one two", True)
    note, count = [doc.add_note(0, 0) for _ in range(2)]
    for node in (note, count):
        doc.bind_note_text_input(chat.id, node.id, "word_count")
        doc.run_note_text_input(node.id)
    doc.set_note_content(note.id, "my annotation")
    data = build_chat_data(doc)
    # Both targets first used the same bytes. The loaded bodies differ from
    # the source; status must be based on the recorded upstream digest.
    assert data["note_text_inputs"][note.id]["last_source_sha256"] == data["note_text_inputs"][count.id]["last_source_sha256"]
    db = tmp_path / "freshness.db"
    notes, pins = data.pop("notes_data"), data.pop("pins_data")
    graph_id, _ = save_chat_atomically_row(db, None, "Freshness", data, notes, pins)
    restored = SceneDocument()
    row, notes, pins = load_chat_snapshot(db, graph_id)
    restore_chat_into_document(restored, row, notes, pins)
    for n in restored.nodes.values():
        if n.kind == "note":
            assert restored._node_wire(n)["textInputStatus"] == "current"
    chat.content = "one  two"
    data = build_chat_data(doc)
    data["note_text_inputs"][note.id]["last_source_sha256"] = "invalid digest"
    restore_chat_into_document(restored, {"data": data}, data["notes_data"], [])
    by_content = {n.content: n for n in restored.nodes.values() if n.kind == "note"}
    assert restored._node_wire(by_content["2"])["textInputStatus"] == "stale"
    assert restored._node_wire(by_content["my annotation"])["textInputStatus"] == "unrun"


def test_many_readers_hash_the_source_once_per_publish(monkeypatch):
    import backend.domain.nodes_content as content_ops

    doc = SceneDocument()
    source = doc.add_chat_node(0, 0, "A shared source", True)
    for _ in range(32):
        note = doc.add_note(0, 0)
        doc.bind_note_text_input(source.id, note.id, "word_count")
        doc.run_note_text_input(note.id)
    calls = 0
    original = content_ops._text_digest

    def counted(text):
        nonlocal calls
        calls += 1
        return original(text)

    monkeypatch.setattr(content_ops, "_text_digest", counted)
    doc.scene_payload()
    assert calls == 1
    doc.take_dirty_patch_ops()
    assert calls == 2


def test_a_note_promoted_to_a_comparison_loses_both_ports_and_undo_restores_them():
    doc = SceneDocument()
    source = doc.add_chat_node(0, 0, "response", False)
    middle, downstream = [doc.add_note(0, 0) for _ in range(2)]
    doc.bind_note_text_input(source.id, middle.id)
    doc.bind_note_text_input(middle.id, downstream.id)
    doc.run_note_text_input(downstream.id)

    doc.record_command(
        "compareBranches", "user", lambda: doc.mark_branch_comparison_note(middle.id, [source.id]),
        node_ids=[middle.id],
    )
    assert all("textInputStatus" not in doc._node_wire(n) for n in (middle, downstream))
    doc.undo()
    assert doc.nodes[middle.id].state.text_input_source_id == source.id
    assert doc._node_wire(doc.nodes[downstream.id])["textInputStatus"] == "current"
