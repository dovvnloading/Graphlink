"""First-class local Model cards in the dependency-ordered Graph Runner.

The transport is substituted, but every test drives the actual SessionBus,
SQLite save/load, model dispatch fence and run intents.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from backend.api import intents_graph_model as local_model
from backend.canvas import SceneDocument
from backend.chat_library import load_chat_row
from backend.domain.model import SceneError
from backend.domain.node_states import ModelState
from backend.domain.graph_runner import plan_graph_run
from backend.events import IntentValidationError, SessionBus
from backend.tests.test_graph_model_intents import session
from backend import chat_library
from graphlink_task_config import LOCAL_PROVIDER_OLLAMA


def test_model_node_is_distinct_and_validates_cross_kind_cycles():
    doc = SceneDocument()
    source = doc.add_note(0, 0)
    first = doc.add_model_node(source.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
    assert first.kind == "model"
    assert isinstance(first.state, ModelState)
    assert first.state.model_binding.source_id == source.id
    assert first.item_ids == [source.id]
    assert first.content == ""
    assert not doc.edges  # A typed data connection never changes ancestry.
    second = doc.add_model_node(first.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
    final = doc.add_note(0, 0)
    doc.bind_note_text_input(second.id, final.id, "word_count")
    with pytest.raises(SceneError, match="cycle"):
        doc.configure_model_node(first.id, final.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
    with pytest.raises(SceneError, match="cycle"):
        doc.bind_note_text_input(final.id, source.id)
    assert first.state.model_binding.source_id == source.id
    doc.configure_model_node(first.id, source.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
    assert first.state.model_binding.source_id == source.id
    with pytest.raises(SceneError, match="local Ollama"):
        doc.add_model_node(source.id, "OpenAI", "gpt-6")


def test_preview_defers_downstream_steps_without_speculative_inference():
    doc = SceneDocument()
    source = doc.add_chat_node(0, 0, "model input", True)
    first = doc.add_model_node(source.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
    second = doc.add_model_node(first.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
    note = doc.add_note(0, 0)
    doc.bind_note_text_input(second.id, note.id, "word_count")
    preview = plan_graph_run(doc, [])
    assert [(s["nodeId"], s["status"]) for s in preview] == [
        (first.id, "ready"), (second.id, "waiting"), (note.id, "waiting"),
    ]
    assert not preview[1]["inputHash"] and not preview[2]["inputHash"]
    assert second.content == "" and note.content == "Add note..."
    assert not doc.graph_runs


def test_two_model_steps_then_transform_commit_in_dependency_order(tmp_path, monkeypatch):
    async def scenario():
        path, bus, doc, source, target, send = session(tmp_path)
        first_id = await send("addModelNode", source.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
        second_id = await send("addModelNode", first_id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
        await send("bindNoteTextInput", second_id, target.id, "word_count")
        first, second = doc.nodes[first_id], doc.nodes[second_id]
        assert first.kind == second.kind == "model"
        assert doc.nodes[target.id].content == "Add note..."
        preview = await send("previewGraphRun", [])
        assert [x["status"] for x in preview["steps"]] == ["ready", "waiting", "waiting"]
        assert not path.exists() or not doc.graph_runs
        observed = []

        def fake_ollama(*, prompt, model_id, endpoint):
            row = load_chat_row(path, doc.current_chat_id)
            saved_run = row["data"]["graph_runs"][-1]
            dispatched = [s for s in saved_run["steps"] if s["status"] == "running"]
            assert len(dispatched) == 1
            attempt = dispatched[0]["externalAttempt"]
            assert attempt["phase"] == "dispatched"
            assert attempt["model"] == model_id == "qwen3:8b"
            observed.append((prompt, dispatched[0]["nodeId"], attempt["requestId"]))
            return {"message": {"content": "First model reply" if len(observed) == 1 else "Second model reply with four words"}}

        monkeypatch.setattr(local_model, "local_ollama_complete", fake_ollama)
        result = await send("executeGraphRun", [], preview["previewToken"], "first-class-graph-run")
        assert result["status"] == "completed"
        assert [row["status"] for row in result["steps"]] == ["completed", "completed", "completed"]
        assert len(observed) == 2
        assert observed[0][0] == source.content
        assert observed[1][0] == "First model reply"
        assert first.content == "First model reply"
        assert second.content == "Second model reply with four words"
        assert doc.nodes[target.id].content == "6"
        assert doc.model_input_status(first.id) == doc.model_input_status(second.id) == "current"
        assert result["steps"][0]["externalAttempt"]["requestId"] == observed[0][2]
        assert result["steps"][1]["externalAttempt"]["requestId"] == observed[1][2]
        assert (await send("executeGraphRun", [], preview["previewToken"], "first-class-graph-run"))["status"] == "completed"
        assert len(observed) == 2

        reopened = SceneDocument()
        other = SessionBus("graph-model-after-restart")
        other.register_topic("scene", reopened.scene_payload)
        chat_library.register_chat_library(other, path, reopened, autosave_interval_seconds=None)
        await other.dispatch_intent("app-chat-library", "loadChat", [doc.current_chat_id])
        restored_models = [n for n in reopened.nodes.values() if n.kind == "model"]
        assert len(restored_models) == 2
        assert [n.content for n in restored_models] == [first.content, second.content]
        assert reopened.model_input_status(restored_models[0].id) == "current"
        assert reopened.model_input_status(restored_models[1].id) == "current"
        restored_run = await other.dispatch_intent("scene", "getGraphRun", [])
        assert restored_run["status"] == "completed"
        assert all(step["status"] == "completed" for step in restored_run["steps"])

    asyncio.run(scenario())


def test_an_ambiguous_model_call_blocks_dependents_but_other_branch_finishes(tmp_path, monkeypatch):
    async def scenario():
        _path, _bus, doc, source, target, send = session(tmp_path)
        model_id = await send("addModelNode", source.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
        await send("bindNoteTextInput", model_id, target.id, "word_count")
        independent = doc.add_note(0, 0)
        doc.bind_note_text_input(source.id, independent.id)
        preview = await send("previewGraphRun", [])
        assert [x["status"] for x in preview["steps"]].count("waiting") == 1
        monkeypatch.setattr(local_model, "local_ollama_complete", lambda **kw: (_ for _ in ()).throw(TimeoutError("timeout")))
        result = await send("executeGraphRun", [], preview["previewToken"], "failed-model-graph")
        assert result["status"] == "reconciliation-required"
        assert len([x for x in result["steps"] if x["status"] == "reconciliation-required"]) == 1
        assert any(x["status"] == "completed" for x in result["steps"])
        assert doc.nodes[independent.id].content == source.content
        assert doc.nodes[target.id].content == "Add note..."
        old = result["steps"][0]["externalAttempt"]["requestId"] if "externalAttempt" in result["steps"][0] else ""
        resume = await send("resumeGraphRun", "failed-model-graph")
        assert resume["status"] == "reconciliation-required"
        assert len([s for s in resume["steps"] if s.get("externalAttempt")]) == 1
        assert old
        assert doc.nodes[target.id].content == "Add note..."

    asyncio.run(scenario())


def test_canvas_port_rebinding_is_undoable_and_rejects_cycles(tmp_path):
    async def scenario():
        _path, _bus, doc, source, _target, send = session(tmp_path)
        first = await send("addModelNode", source.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
        second = await send("addModelNode", first, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
        configured = await send("connectModelInput", source.id, second)
        assert doc.nodes[second].state.model_binding.source_id == source.id
        assert next(row for row in configured if row["targetId"] == second)["sourceId"] == source.id
        assert doc.nodes[second].item_ids == [source.id]
        doc.undo()
        assert doc.nodes[second].state.model_binding.source_id == first
        doc.redo()
        assert doc.nodes[second].state.model_binding.source_id == source.id
        await send("connectModelInput", first, second)
        with pytest.raises(IntentValidationError, match="cycle"):
            await send("connectModelInput", second, first)
        assert doc.nodes[first].state.model_binding.source_id == source.id
        assert doc.nodes[second].state.model_binding.source_id == first
        assert not doc.edges

    asyncio.run(scenario())


def test_model_output_edited_mid_inference_is_not_overwritten(tmp_path, monkeypatch):
    async def scenario():
        _path, _bus, doc, source, _target, send = session(tmp_path)
        node_id = await send("addModelNode", source.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
        preview = await send("previewGraphRun", [])
        started, release = threading.Event(), threading.Event()

        def slow_model(**kwargs):
            started.set()
            assert release.wait(8)
            return {"message": {"content": "Unwanted stale model reply"}}

        monkeypatch.setattr(local_model, "local_ollama_complete", slow_model)
        running = asyncio.create_task(send("executeGraphRun", [], preview["previewToken"], "model-mid-edit-run"))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            doc.nodes[node_id].content = "I edited this while the model was running"
        finally:
            release.set()
        result = await running
        assert result["status"] == "reconciliation-required"
        assert doc.nodes[node_id].content == "I edited this while the model was running"
        assert result["steps"][0]["status"] == "reconciliation-required"

    asyncio.run(scenario())
