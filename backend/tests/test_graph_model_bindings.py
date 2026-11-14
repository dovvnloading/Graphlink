"""Versioned model-text connections atop Graph Runner's explicit Ollama dispatch."""

from __future__ import annotations

import asyncio
import threading

import pytest

from backend.api import intents_graph_model as graph_model
from backend.canvas import SceneDocument
from backend.domain.graph_runner import plan_graph_run
from backend.domain.model import SceneError
from backend.events import IntentValidationError
from backend.session_load import restore_chat_into_document
from backend.session_save import build_chat_data
from backend.tests.test_graph_model_intents import session
from graphlink_task_config import LOCAL_PROVIDER_OLLAMA


def test_model_port_is_typed_idempotent_undoable_and_cycle_safe():
    doc = SceneDocument()
    source, relay, output, downstream = [doc.add_note(0, 0) for _ in range(4)]
    doc.bind_note_text_input(source.id, relay.id)
    doc.record_command(
        "bindGraphModel", "user",
        lambda: doc.bind_note_model_input(relay.id, output.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b"),
        node_ids=[output.id],
    )
    binding = doc.nodes[output.id].state.model_text_input
    assert binding is not None
    assert binding.version == 1
    assert doc.model_input_status(output.id) == "unrun"
    doc.bind_note_model_input(relay.id, output.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
    assert doc.nodes[output.id].state.model_text_input.binding_id == binding.binding_id
    doc.bind_note_text_input(output.id, downstream.id)
    plan = plan_graph_run(doc, [])
    assert len(plan) == 2
    assert next(row for row in plan if row["nodeId"] == downstream.id)["status"] == "blocked"

    for source_id, target_id, provider, model in [
        (output.id, source.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b"),
        (output.id, output.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b"),
        (source.id, output.id, "OpenAI", "gpt-6"),
        (source.id, output.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b-cloud"),
        (source.id, output.id, LOCAL_PROVIDER_OLLAMA, "invalid model id"),
    ]:
        with pytest.raises(SceneError):
            doc.bind_note_model_input(source_id, target_id, provider, model)
    with pytest.raises(SceneError, match="model input"):
        doc.bind_note_text_input(source.id, output.id)
    assert doc.nodes[output.id].state.model_text_input.binding_id == binding.binding_id

    doc.undo()
    assert doc.nodes[output.id].state.model_text_input is None
    doc.redo()
    assert doc.nodes[output.id].state.model_text_input.binding_id == binding.binding_id


def test_model_binding_roundtrips_with_saved_id_remapping_and_defensive_restore():
    doc = SceneDocument()
    doc.remove_nodes([doc.add_note(0, 0).id])
    source, destination = [doc.add_note(0, 0) for _ in range(2)]
    doc.set_note_content(source.id, "the saved prompt")
    doc.set_note_content(destination.id, "keep this draft")
    doc.bind_note_model_input(source.id, destination.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
    binding = destination.state.model_text_input
    binding.last_input_hash = "a" * 64
    binding.last_output_hash = "b" * 64
    payload = build_chat_data(doc)
    assert payload["note_model_inputs"][destination.id]["source"] == source.id

    def restore(raw):
        reopened = SceneDocument()
        restore_chat_into_document(reopened, {"data": raw}, raw["notes_data"], [])
        notes = {node.content: node for node in reopened.nodes.values() if node.kind == "note"}
        return reopened, notes

    reopened, notes = restore(payload)
    restored = notes["keep this draft"].state.model_text_input
    assert restored is not None
    assert restored.source_id == notes["the saved prompt"].id
    assert restored.binding_id == binding.binding_id
    assert restored.version == 1
    assert restored.last_input_hash == "a" * 64
    assert reopened.model_input_status(notes["keep this draft"].id) == "stale"

    for mutation in [
        {"version": 2}, {"binding_id": "not-a-uuid"},
        {"source": "missing"}, {"provider": "OpenAI"}, {"model": "remote-cloud"},
    ]:
        corrupted = dict(payload)
        corrupted["note_model_inputs"] = {
            destination.id: {**payload["note_model_inputs"][destination.id], **mutation}
        }
        _, notes = restore(corrupted)
        assert notes["keep this draft"].state.model_text_input is None
        assert notes["keep this draft"].content == "keep this draft"


def test_model_port_marks_transitive_staleness_and_blocks_stale_downstream_reads():
    from backend.domain.nodes_content import _text_digest

    doc = SceneDocument()
    source, relay, model_output, downstream = [doc.add_note(0, 0) for _ in range(4)]
    doc.set_note_content(source.id, "first")
    doc.bind_note_text_input(source.id, relay.id)
    doc.run_note_text_input(relay.id)
    doc.bind_note_model_input(relay.id, model_output.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
    doc.set_note_content(model_output.id, "generated text")
    binding = model_output.state.model_text_input
    binding.last_input_hash = _text_digest("first")
    binding.last_output_hash = _text_digest("generated text")
    doc.bind_note_text_input(model_output.id, downstream.id)
    assert doc.model_input_status(model_output.id) == "current"
    doc.run_note_text_input(downstream.id)
    assert downstream.content == "generated text"

    # The relay still contains the same cached output, but it is stale
    # relative to its own source. This must invalidate its model descendant.
    doc.set_note_content(source.id, "changed")
    assert doc.model_input_status(model_output.id) == "stale"
    step = next(row for row in plan_graph_run(doc, []) if row["nodeId"] == downstream.id)
    assert step["status"] == "blocked"
    assert "upstream model input" in step["detail"]
    with pytest.raises(SceneError, match="upstream text input"):
        doc.run_note_text_input(downstream.id)
    assert downstream.content == "generated text"


def test_promotion_to_comparison_detaches_model_input_and_undo_restores_it():
    doc = SceneDocument()
    prompt = doc.add_chat_node(0, 0, "compare these", True)
    destination = doc.add_note(0, 0)
    downstream = doc.add_note(0, 0)
    doc.bind_note_model_input(prompt.id, destination.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
    binding_id = destination.state.model_text_input.binding_id
    doc.bind_note_text_input(destination.id, downstream.id)
    doc.record_command(
        "compareBranches", "user",
        lambda: doc.mark_branch_comparison_note(destination.id, [prompt.id]),
        node_ids=[destination.id],
    )
    assert doc.nodes[destination.id].state.is_branch_comparison
    assert doc.nodes[destination.id].state.model_text_input is None
    assert not doc.nodes[downstream.id].state.text_input_source_id
    assert not [n for n in doc.nodes.values() if n.kind == "note"
                and n.state.model_text_input is not None]
    doc.undo()
    assert doc.nodes[destination.id].state.model_text_input.binding_id == binding_id
    assert doc.nodes[downstream.id].state.text_input_source_id == destination.id


def test_source_deletion_disconnects_model_port_and_undo_restores_it():
    doc = SceneDocument()
    source = doc.add_chat_node(0, 0, "source", True)
    target = doc.add_note(0, 0)
    doc.bind_note_model_input(source.id, target.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
    identity = target.state.model_text_input.binding_id
    doc.record_command("removeNodes", "user", lambda: doc.remove_nodes([source.id]), node_ids=[source.id])
    assert doc.nodes[target.id].state.model_text_input is None
    doc.undo()
    assert doc.nodes[target.id].state.model_text_input.binding_id == identity
    assert doc.nodes[target.id].state.model_text_input.source_id == source.id


def test_bound_model_runs_only_after_preview_and_persists_provenance(tmp_path, monkeypatch):
    async def scenario():
        _path, _bus, doc, source, target, send = session(tmp_path)
        rows = await send("bindGraphModel", source.id, target.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
        assert rows[0]["status"] == "unrun"
        assert rows[0]["inputType"] == rows[0]["outputType"] == "text"
        assert rows[0]["version"] == 1
        assert not doc.graph_runs
        calls = []

        def answer(*, prompt, model_id, endpoint):
            calls.append((prompt, model_id, endpoint))
            return {"message": {"content": "A model answer."}}

        monkeypatch.setattr(graph_model, "local_ollama_complete", answer)
        args = [source.id, target.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b"]
        preview = await send("previewGraphModel", *args)
        assert not calls and target.content == "Add note..."
        run = await send("executeGraphModel", *args, preview["previewToken"], "bound-model-run-1")
        assert run["status"] == "completed" and len(calls) == 1
        assert target.content == "A model answer."
        assert (await send("getGraphModelBindings"))[0]["status"] == "current"
        doc.undo()  # The output text and provenance are one command.
        assert target.id in doc.nodes
        assert doc.model_input_status(target.id) == "unrun"
        doc.redo()
        assert doc.model_input_status(target.id) == "current"

        doc.set_note_content(source.id, "a new prompt")
        assert doc.model_input_status(target.id) == "stale"
        changed = (await send("getGraphModelBindings"))[0]
        assert changed["status"] == "stale"
        assert len(calls) == 1  # Source changes never implicitly call Ollama.

        with pytest.raises(IntentValidationError, match="saved model connection"):
            await send("previewGraphModel", source.id, target.id, LOCAL_PROVIDER_OLLAMA, "different:1")
        new_preview = await send("previewGraphModel", *args)
        await send("executeGraphModel", *args, new_preview["previewToken"], "bound-model-run-2")
        assert len(calls) == 2
        assert doc.model_input_status(target.id) == "current"
        doc.set_note_content(target.id, "my manual edits")
        assert doc.model_input_status(target.id) == "edited"

        data = build_chat_data(doc)
        restored = SceneDocument()
        restore_chat_into_document(restored, {"data": data}, data["notes_data"], [])
        rebound = next(n for n in restored.nodes.values() if n.content == "my manual edits")
        assert rebound.state.model_text_input.model_id == "qwen3:8b"
        assert restored.model_input_status(rebound.id) == "edited"
        assert len(restored.graph_runs) == 2

        await send("unbindGraphModel", target.id)
        assert not doc.nodes[target.id].state.model_text_input
        assert doc.nodes[target.id].content == "my manual edits"

    asyncio.run(scenario())


def test_rebound_output_cannot_accept_an_inflight_previous_model_reply(tmp_path, monkeypatch):
    async def scenario():
        _path, _bus, doc, source, target, send = session(tmp_path)
        await send("bindGraphModel", source.id, target.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
        args = [source.id, target.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b"]
        token = (await send("previewGraphModel", *args))["previewToken"]
        started, released = threading.Event(), threading.Event()

        def slow_answer(**_kwargs):
            started.set()
            assert released.wait(8)
            return {"message": {"content": "Stale reply must not win"}}

        monkeypatch.setattr(graph_model, "local_ollama_complete", slow_answer)
        pending = asyncio.create_task(send("executeGraphModel", *args, token, "bound-race-run"))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            changed = await send("bindGraphModel", source.id, target.id, LOCAL_PROVIDER_OLLAMA, "different:1")
            assert changed[0]["modelId"] == "different:1"
        finally:
            released.set()

        with pytest.raises(SceneError, match="connection changed"):
            await pending
        assert target.content == "Add note..."
        assert doc.model_input_status(target.id) == "unrun"
        latest = await send("getGraphRun")
        assert latest["status"] == "reconciliation-required"

    asyncio.run(scenario())
