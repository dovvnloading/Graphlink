"""End-to-end local model calls through Graph Runner's SQLite run journal."""

from __future__ import annotations

import asyncio
import threading

import pytest

import api_provider
from backend import chat_library
from backend.canvas import SceneDocument
from backend.events import IntentValidationError, SessionBus
from graphlink_task_config import LOCAL_PROVIDER_OLLAMA


def session(tmp_path):
    path = tmp_path / "model.db"
    doc = SceneDocument()
    bus = SessionBus("graph-model")
    bus.register_topic("scene", doc.scene_payload)
    chat_library.register_chat_library(bus, path, doc, autosave_interval_seconds=None)

    async def send(name, *args):
        return await bus.dispatch_intent("scene", name, list(args))

    source = doc.add_note(0, 0)
    target = doc.add_note(200, 0)
    source.content = "Summarize this message in five words."
    return path, bus, doc, source, target, send


def test_model_preview_is_read_only_and_dispatch_checkpoints_before_request(tmp_path, monkeypatch):
    async def scenario():
        path, _bus, doc, source, target, send = session(tmp_path)
        selected = [source.id, target.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b"]
        preview = await send("previewGraphModel", *selected)
        assert not path.exists()
        assert doc.graph_runs == [] and doc.command_log == []
        assert preview["steps"][0]["kind"] == "model"
        assert "Summarize this message" not in str(preview)
        observed = []

        def fake_chat(task, messages, **kwargs):
            assert task == "task_chat"
            assert messages == [{"role": "user", "content": source.content}]
            assert kwargs["model_ref"].provider == LOCAL_PROVIDER_OLLAMA
            assert kwargs["model_ref"].model_id == "qwen3:8b"
            row = chat_library.load_chat_row(path, doc.current_chat_id)
            saved_step = row["data"]["graph_runs"][-1]["steps"][0]
            assert saved_step["externalAttempt"]["phase"] == "dispatched"
            assert saved_step["externalAttempt"]["model"] == "qwen3:8b"
            assert saved_step["status"] == "running"
            observed.append(saved_step["externalAttempt"]["requestId"])
            return {"message": {"role": "assistant", "content": "Ship safely with clear results."}}

        monkeypatch.setattr(api_provider, "chat", fake_chat)
        args = [*selected, preview["previewToken"], "local-model-run"]
        result = await send("executeGraphModel", *args)
        assert result["status"] == "completed"
        assert target.content == "Ship safely with clear results."
        assert len(observed) == 1
        assert result["steps"][0]["externalAttempt"]["requestId"] == observed[0]
        saved = chat_library.load_chat_row(path, doc.current_chat_id)["data"]["graph_runs"][0]
        assert saved["status"] == "completed" and saved["steps"][0]["status"] == "completed"
        assert saved["steps"][0]["externalAttempt"]["phase"] == "completed"
        assert not any("Summarize" in str(k) for k in saved["steps"][0].values())
        assert (await send("executeGraphModel", *args))["status"] == "completed"
        assert len(observed) == 1

        reopened = SceneDocument()
        bus2 = SessionBus("graph-model-reopened")
        bus2.register_topic("scene", reopened.scene_payload)
        chat_library.register_chat_library(bus2, path, reopened, autosave_interval_seconds=None)
        await bus2.dispatch_intent("app-chat-library", "loadChat", [doc.current_chat_id])
        current = await bus2.dispatch_intent("scene", "getGraphRun", [])
        assert current["status"] == "completed"
        assert current["steps"][0]["externalAttempt"]["requestId"] == observed[0]
        assert reopened.nodes[current["steps"][0]["nodeId"]].content == target.content
        assert len(reopened.command_log) == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("changed", ["source", "destination", "provider", "model"])
def test_preview_staleness_refuses_model_calls(tmp_path, monkeypatch, changed):
    async def scenario():
        _path, _bus, doc, source, target, send = session(tmp_path)
        args = [source.id, target.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b"]
        preview = await send("previewGraphModel", *args)
        calls = []
        monkeypatch.setattr(api_provider, "chat", lambda *_a, **_kw: calls.append(1))
        if changed == "source":
            source.content = "edited prompt"
        elif changed == "destination":
            target.content = "hand-written content"
        elif changed == "provider":
            args[2] = "OpenAI"
        else:
            args[3] = "other-model"
        with pytest.raises(IntentValidationError):
            await send("executeGraphModel", *args, preview["previewToken"], "stale-model-run")
        assert not calls and doc.graph_runs == []

    asyncio.run(scenario())


def test_cloud_model_and_unbound_destination_are_enforced_before_preview(tmp_path):
    async def scenario():
        _path, _bus, doc, source, target, send = session(tmp_path)
        with pytest.raises(IntentValidationError, match="local Ollama"):
            await send("previewGraphModel", source.id, target.id, "OpenAI", "gpt-6")
        with pytest.raises(IntentValidationError, match="different nodes"):
            await send("previewGraphModel", source.id, source.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
        doc.bind_note_text_input(source.id, target.id)
        with pytest.raises(IntentValidationError, match="unbound"):
            await send("previewGraphModel", source.id, target.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
        doc.unbind_note_text_input(target.id)
        source.content = "a" * 70000
        with pytest.raises(IntentValidationError, match="64 KiB"):
            await send("previewGraphModel", source.id, target.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b")
        assert doc.graph_runs == []

    asyncio.run(scenario())


def test_remote_timeout_records_uncertain_request_and_never_replays(tmp_path, monkeypatch):
    async def scenario():
        path, _bus, doc, source, target, send = session(tmp_path)
        selected = [source.id, target.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b"]
        token = (await send("previewGraphModel", *selected))["previewToken"]
        calls = []

        def timeout(_task, _messages, **kwargs):
            calls.append(kwargs["model_ref"].model_id)
            raise TimeoutError("Ollama may have processed this prompt")

        monkeypatch.setattr(api_provider, "chat", timeout)
        with pytest.raises(TimeoutError):
            await send("executeGraphModel", *selected, token, "uncertain-local-run")
        assert calls == ["qwen3:8b"]
        assert target.content == "Add note..."
        status = await send("getGraphRun")
        assert status["status"] == "reconciliation-required"
        request_id = status["steps"][0]["externalAttempt"]["requestId"]
        persisted = chat_library.load_chat_row(path, doc.current_chat_id)["data"]["graph_runs"][0]
        assert persisted["steps"][0]["externalAttempt"]["requestId"] == request_id
        assert persisted["steps"][0]["externalAttempt"]["phase"] == "reconciliation-required"
        assert (await send("resumeGraphRun", "uncertain-local-run"))["status"] == "reconciliation-required"
        assert not doc.command_log
        # Retrying with a new preview and operation ID must not bypass
        # this unresolved request by overwriting its destination.
        again = await send("previewGraphModel", *selected)
        with pytest.raises(IntentValidationError, match="unresolved external request"):
            await send("executeGraphModel", *selected, again["previewToken"], "another-local-run")
        assert calls == ["qwen3:8b"]

    asyncio.run(scenario())


def test_inflight_cancellation_keeps_durable_remote_fence(tmp_path, monkeypatch):
    async def scenario():
        path, _bus, doc, source, target, send = session(tmp_path)
        selected = [source.id, target.id, LOCAL_PROVIDER_OLLAMA, "qwen3:8b"]
        token = (await send("previewGraphModel", *selected))["previewToken"]
        started, release = threading.Event(), threading.Event()

        def blocked(_task, _messages, **_kwargs):
            started.set()
            assert release.wait(10)
            return {"message": {"role": "assistant", "content": "arrived too late"}}

        monkeypatch.setattr(api_provider, "chat", blocked)
        task = asyncio.create_task(send("executeGraphModel", *selected, token, "cancelled-local-run"))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
        saved = chat_library.load_chat_row(path, doc.current_chat_id)["data"]["graph_runs"][0]
        assert saved["steps"][0]["externalAttempt"]["phase"] == "reconciliation-required"
        assert target.content == "Add note..."

    asyncio.run(scenario())
