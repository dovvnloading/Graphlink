"""First-class local Model cards in the dependency-ordered Graph Runner.

The transport is substituted, but every test drives the actual SessionBus,
SQLite save/load, model dispatch fence and run intents.
"""

from __future__ import annotations

import asyncio
import sqlite3
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


def test_model_run_fingerprint_survives_saved_node_id_remapping():
    from backend.domain.graph_runner import _inspect
    from backend.session_load import restore_chat_into_document
    from backend.session_save import build_chat_data

    original = SceneDocument()
    for _ in range(3):
        original.remove_nodes([original.add_note(0, 0).id])  # Force nonmatching restore IDs.
    source = original.add_note(0, 0)
    original.set_note_content(source.id, "Stable source prompt")
    model = original.add_model_node(source.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
    step, = plan_graph_run(original, [])
    assert step["status"] == "ready"
    original.graph_runs.append({
        "runId": "resume-safe-model-01", "previewHash": "a" * 64,
        "selectionHash": "b" * 64, "startedAt": "2026-11-14T00:00:00Z",
        "status": "interrupted", "steps": [{**step, "status": "pending"}],
    })
    saved = build_chat_data(original)
    reopened = SceneDocument()
    restore_chat_into_document(reopened, {"data": saved}, saved["notes_data"], [])
    restored_model = next(n for n in reopened.nodes.values() if n.kind == "model")
    assert restored_model.id != model.id
    assert restored_model.state.model_binding.binding_id == model.state.model_binding.binding_id
    assert restored_model.state.model_binding.source_id != source.id
    pending, = reopened.graph_runs[0]["steps"]
    assert pending["nodeId"] == restored_model.id
    assert pending["sourceId"] == restored_model.state.model_binding.source_id
    live = pending.copy()
    _inspect(reopened, live)
    assert live["status"] == "ready"
    assert live["inputHash"] == pending["inputHash"]
    assert live["configHash"] == pending["configHash"]
    assert live["targetHash"] == pending["targetHash"]


def test_two_model_steps_then_transform_commit_in_dependency_order(tmp_path, monkeypatch):
    async def scenario():
        path, bus, doc, source, target, send = session(tmp_path)
        first_id = await send("addModelNode", source.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
        second_id = await send("addModelNode", first_id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
        doc.bind_note_text_input(second_id, target.id, "word_count")
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
        doc.bind_note_text_input(model_id, target.id, "word_count")
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


def test_committed_model_text_can_feed_local_steps_while_ollama_is_offline(tmp_path, monkeypatch):
    from backend.domain.nodes_content import _text_digest

    async def scenario():
        _path, _bus, doc, source, target, send = session(tmp_path)
        model_id = await send("addModelNode", source.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
        node = doc.nodes[model_id]
        node.content = "Saved model answer"
        node.state.model_binding.last_input_hash = _text_digest(source.content)
        node.state.model_binding.last_output_hash = _text_digest(node.content)
        doc.bind_note_text_input(model_id, target.id, "word_count")
        # A remote/unusable OLLAMA_HOST must not block purely deterministic
        # work that consumes a *committed*, current model output.
        monkeypatch.setenv("OLLAMA_HOST", "http://provider.example:11434")
        preview = await send("previewGraphRun", [])
        assert [step["status"] for step in preview["steps"]] == ["current", "ready"]
        run = await send("executeGraphRun", [], preview["previewToken"], "offline-local-transform")
        assert run["status"] == "completed"
        assert [step["status"] for step in run["steps"]] == ["current", "completed"]
        assert doc.nodes[target.id].content == "3"
        assert not any("externalAttempt" in step for step in run["steps"])

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



@pytest.mark.parametrize("limit,expected", [(None, 3), (2, 2)])
def test_independent_model_steps_respect_default_and_configured_parallel_limit(tmp_path, monkeypatch, limit, expected):
    async def scenario():
        path, _bus, doc, source, _target, send = session(tmp_path)
        for _ in range(5):
            doc.add_model_node(source.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
        token = (await send("previewGraphRun", []))["previewToken"]
        lock = threading.Lock()
        entered, release = threading.Event(), threading.Event()
        in_flight = peak = calls = 0

        def answer(**_kwargs):
            nonlocal in_flight, peak, calls
            with lock:
                calls += 1
                invocation = calls
                in_flight += 1
                peak = max(peak, in_flight)
                if in_flight == expected:
                    entered.set()
            try:
                assert release.wait(10), "model gate timed out"
                return {"message": {"content": f"answer {invocation}"}}
            finally:
                with lock:
                    in_flight -= 1

        monkeypatch.setattr(local_model, "local_ollama_complete", answer)
        args = [[], token, f"bounded-models-{expected}"]
        if limit is not None:
            args.append(limit)
        running = asyncio.create_task(send("executeGraphRun", *args))
        try:
            assert await asyncio.to_thread(entered.wait, 6)
            # Every running model has already durably reserved a request ID.
            saved = load_chat_row(path, doc.current_chat_id)["data"]["graph_runs"][-1]
            outbound = [s for s in saved["steps"] if s["status"] == "running"]
            assert len(outbound) == expected
            assert all(s["externalAttempt"]["phase"] == "dispatched" for s in outbound)
            assert (await send("getGraphRun"))["status"] == "running"
            with lock:
                assert calls == expected
        finally:
            release.set()
        result = await asyncio.wait_for(running, timeout=10)
        assert peak == expected and calls == 5
        assert result["maxConcurrency"] == expected
        assert result["status"] == "completed"
        assert all(s["status"] == "completed" for s in result["steps"])
        times = sorted(result["trace"]["steps"], key=lambda x: x["startedAt"])
        assert times[0]["startedAt"] <= times[1]["startedAt"] < times[0]["finishedAt"]
        assert all(s["startedAt"] <= s["finishedAt"] and s["durationMs"] is not None for s in times)
        assert (await send("executeGraphRun", *args)) == result
        if limit is not None:
            with pytest.raises(IntentValidationError, match="concurrency limit"):
                await send("executeGraphRun", [], token, args[2], 3)

        reopened = SceneDocument()
        other = SessionBus("bounded-reopen")
        other.register_topic("scene", reopened.scene_payload)
        chat_library.register_chat_library(other, path, reopened, autosave_interval_seconds=None)
        await other.dispatch_intent("app-chat-library", "loadChat", [doc.current_chat_id])
        persisted = await other.dispatch_intent("scene", "getGraphRun", [])
        assert persisted["maxConcurrency"] == expected
        assert [s["startedAt"] for s in persisted["trace"]["steps"]] == [s["startedAt"] for s in result["trace"]["steps"]]
        assert [s["finishedAt"] for s in persisted["trace"]["steps"]] == [s["finishedAt"] for s in result["trace"]["steps"]]

    asyncio.run(scenario())


def test_model_fan_out_starts_children_together_after_parent_receipt(tmp_path, monkeypatch):
    async def scenario():
        _path, _bus, doc, source, _target, send = session(tmp_path)
        parent = doc.add_model_node(source.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
        children = [doc.add_model_node(parent.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b") for _ in range(2)]
        leaves = [doc.add_note(0, 0) for _ in range(2)]
        for child, leaf in zip(children, leaves):
            doc.bind_note_text_input(child.id, leaf.id)
        token = (await send("previewGraphRun", []))["previewToken"]
        entered, release = threading.Event(), threading.Event()
        lock = threading.Lock()
        child_count = 0

        def answer(*, prompt, **_kwargs):
            nonlocal child_count
            if prompt == source.content:
                return {"message": {"content": "shared parent output"}}
            assert prompt == "shared parent output"
            with lock:
                child_count += 1
                if child_count == 2:
                    entered.set()
            assert release.wait(10)
            return {"message": {"content": "child answer"}}

        monkeypatch.setattr(local_model, "local_ollama_complete", answer)
        pending = asyncio.create_task(send("executeGraphRun", [], token, "parallel-fan-out"))
        try:
            assert await asyncio.to_thread(entered.wait, 6)
            state = await send("getGraphRun")
            by_id = {step["nodeId"]: step for step in state["steps"]}
            assert by_id[parent.id]["status"] == "completed"
            assert all(by_id[child.id]["status"] == "running" for child in children)
            assert all(by_id[leaf.id]["status"] == "pending" for leaf in leaves)
        finally:
            release.set()
        finished = await asyncio.wait_for(pending, timeout=10)
        by_id = {step["destinationNodeId"]: step for step in finished["trace"]["steps"]}
        for child, leaf in zip(children, leaves):
            assert by_id[child.id]["startedAt"] >= by_id[parent.id]["finishedAt"]
            assert by_id[leaf.id]["startedAt"] >= by_id[child.id]["finishedAt"]
            assert doc.nodes[leaf.id].content == "child answer"
        assert finished["status"] == "completed" and child_count == 2

    asyncio.run(scenario())


def test_failed_parallel_branch_blocks_only_descendants(tmp_path, monkeypatch):
    async def scenario():
        _path, _bus, doc, source, target, send = session(tmp_path)
        source.content = "FAIL this branch"
        good_input = doc.add_note(0, 0)
        good_input.content = "Healthy independent branch"
        failed = doc.add_model_node(source.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
        doc.bind_note_text_input(failed.id, target.id)
        healthy = doc.add_model_node(good_input.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
        healthy_leaf = doc.add_note(0, 0)
        doc.bind_note_text_input(healthy.id, healthy_leaf.id)
        calls = []

        def answer(*, prompt, **_kwargs):
            calls.append(prompt)
            if prompt.startswith("FAIL"):
                raise TimeoutError("The remote outcome is uncertain")
            return {"message": {"content": "Independent success"}}

        monkeypatch.setattr(local_model, "local_ollama_complete", answer)
        token = (await send("previewGraphRun", []))["previewToken"]
        result = await send("executeGraphRun", [], token, "isolated-branch-failure", 2)
        states = {s["nodeId"]: s for s in result["steps"]}
        assert result["status"] == "reconciliation-required"
        assert states[failed.id]["status"] == "reconciliation-required"
        assert states[target.id]["status"] == "blocked"
        assert "startedAt" not in states[target.id]  # Never dispatched.
        assert states[healthy.id]["status"] == "completed"
        assert states[healthy_leaf.id]["status"] == "completed"
        assert doc.nodes[healthy_leaf.id].content == "Independent success"
        assert target.content == "Add note..."
        assert sorted(calls) == sorted([source.content, good_input.content])
        assert (await send("resumeGraphRun", "isolated-branch-failure"))["status"] == "reconciliation-required"
        assert len(calls) == 2

    asyncio.run(scenario())


def test_cancel_parallel_run_reaches_all_active_steps_without_replaying_them(tmp_path, monkeypatch):
    async def scenario():
        path, _bus, doc, source, _target, send = session(tmp_path)
        for _ in range(3):
            doc.add_model_node(source.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
        token = (await send("previewGraphRun", []))["previewToken"]
        lock = threading.Lock()
        entered, release = threading.Event(), threading.Event()
        calls = 0

        def blocked(**_kwargs):
            nonlocal calls
            with lock:
                calls += 1
                if calls == 2:
                    entered.set()
            assert release.wait(10)
            return {"message": {"content": "arrived after cancel"}}

        monkeypatch.setattr(local_model, "local_ollama_complete", blocked)
        pending = asyncio.create_task(send("executeGraphRun", [], token, "cancel-two-models", 2))
        try:
            assert await asyncio.to_thread(entered.wait, 6)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, timeout=10)
        finally:
            release.set()
        saved = load_chat_row(path, doc.current_chat_id)["data"]["graph_runs"][-1]
        assert saved["status"] == "running"
        assert [s["status"] for s in saved["steps"]].count("reconciliation-required") == 2
        assert [s["status"] for s in saved["steps"]].count("pending") == 1
        assert all(s["externalAttempt"]["phase"] == "reconciliation-required" for s in saved["steps"][:2])
        assert (await send("getGraphRun"))["status"] == "interrupted"
        result = await send("resumeGraphRun", "cancel-two-models")
        assert result["status"] == "reconciliation-required"
        assert result["steps"][-1]["status"] == "completed"
        assert calls == 3  # The original two requests were never replayed.
        assert all(s["startedAt"] and s["finishedAt"] for s in result["steps"])

    asyncio.run(scenario())


def test_restart_with_two_external_steps_in_flight_preserves_both_fences(tmp_path, monkeypatch):
    async def scenario():
        path, _bus, doc, source, _target, send = session(tmp_path)
        for _ in range(3):
            doc.add_model_node(source.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
        token = (await send("previewGraphRun", []))["previewToken"]
        lock = threading.Lock()
        entered, release = threading.Event(), threading.Event()
        calls = 0

        def answer(**_kwargs):
            nonlocal calls
            with lock:
                calls += 1
                if calls == 2:
                    entered.set()
            assert release.wait(10)
            return {"message": {"content": "received"}}

        monkeypatch.setattr(local_model, "local_ollama_complete", answer)
        pending = asyncio.create_task(send("executeGraphRun", [], token, "crash-two-models", 2))
        snapshot = tmp_path / "restart.db"
        try:
            assert await asyncio.to_thread(entered.wait, 6)
            before = load_chat_row(path, doc.current_chat_id)["data"]["graph_runs"][-1]
            requests = [s["externalAttempt"]["requestId"] for s in before["steps"] if "externalAttempt" in s]
            assert len(requests) == 2 and len(set(requests)) == 2
            # Copy the committed SQLite state *before* cancelling the live
            # workers, emulating a process killed with both calls in flight.
            with sqlite3.connect(path) as original, sqlite3.connect(snapshot) as copy:
                original.backup(copy)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, timeout=10)
        finally:
            release.set()

        reopened = SceneDocument()
        other = SessionBus("parallel-crash-reopen")
        other.register_topic("scene", reopened.scene_payload)
        chat_library.register_chat_library(other, snapshot, reopened, autosave_interval_seconds=None)
        await other.dispatch_intent("app-chat-library", "loadChat", [doc.current_chat_id])
        recovered = await other.dispatch_intent("scene", "getGraphRun", [])
        assert recovered["status"] == "reconciliation-required"
        assert recovered["maxConcurrency"] == 2
        assert [s["status"] for s in recovered["steps"]] == [
            "reconciliation-required", "reconciliation-required", "pending",
        ]
        assert [s["externalAttempt"]["requestId"] for s in recovered["steps"][:2]] == requests
        resumed = await other.dispatch_intent("scene", "resumeGraphRun", ["crash-two-models"])
        assert resumed["status"] == "reconciliation-required"
        assert [s["status"] for s in resumed["steps"]] == [
            "reconciliation-required", "reconciliation-required", "completed",
        ]
        assert calls == 3  # Only the never-sent third step dispatched.
        # Crash recovery knows when dispatch began, not when the lost
        # process's external calls ended. Never invent terminal times.
        assert all(s["startedAt"] and s["finishedAt"] is None for s in resumed["trace"]["steps"][:2])
        assert all(s["durationMs"] is None for s in resumed["trace"]["steps"][:2])
        persisted = load_chat_row(snapshot, reopened.current_chat_id)["data"]["graph_runs"][-1]
        assert [s["externalAttempt"]["requestId"] for s in persisted["steps"][:2]] == requests

    asyncio.run(scenario())
