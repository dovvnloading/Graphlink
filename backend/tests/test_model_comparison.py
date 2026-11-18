"""End-to-end compare-on-send: sibling isolation, streaming, metadata, guards.

Mocks the final worker only; every selected request still goes through the
production AgentDispatcher._dispatch and its cancellation/run registry.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

import api_provider
import backend.agents as agents_module
import graphlink_task_config as config
from backend.agents import AgentDispatcher
from backend.canvas import register_canvas
from backend.composer import ComposerDocument
from backend.context_engine.model_comparison import (
    available_comparison_models, validate_comparison_models,
)
from backend.events import SessionBus
from backend.notifications import NotificationState
from graphlink_model_catalog import ModelRef


class ComparisonSettings:
    def __init__(self):
        self.cloud_models = {}

    def get_enable_system_prompt(self):
        return True

    def get_ollama_scanned_models(self):
        return ["alpha", "beta", "gamma", "delta", "epsilon"]

    def get_llama_cpp_settings(self):
        return {
            "chat_model_path": "/models/current.gguf",
            "title_model_path": "",
            "n_ctx": 2048,
        }

    def get_api_base_url(self, provider):
        return "https://api.example.invalid/v1"

    def get_provider_key(self, provider):
        return "configured-key" if provider in self.cloud_models else ""

    def get_api_model_catalog(self, provider):
        return [{"model_id": model, "ready": True, "available": True}
                for model in self.cloud_models.get(provider, [])]

    def get_api_models(self, provider):
        models = self.cloud_models.get(provider, [])
        return {config.TASK_CHAT: models[0]} if models else {}


def _environment(name):
    settings = ComparisonSettings()
    bus = SessionBus(name)
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    composer = ComposerDocument()
    bus.register_topic("app-composer", composer.payload)
    dispatcher = AgentDispatcher(settings)
    document = register_canvas(bus, notifications, dispatcher, composer)
    return settings, bus, composer, dispatcher, document


def _selection(*models):
    return [{"provider": "Ollama", "modelId": model} for model in models]


def test_catalog_lists_only_configured_models_and_validates_duplicates():
    settings = ComparisonSettings()
    settings.cloud_models["Anthropic Claude"] = ["claude-test"]
    options = available_comparison_models(settings)
    pairs = {(o["provider"], o["modelId"]) for o in options}
    assert ("Ollama", "alpha") in pairs
    assert ("Llama.cpp", "current.gguf") in pairs
    assert ("Anthropic Claude", "claude-test") in pairs
    assert ("Google Gemini", "unconfigured") not in pairs

    with pytest.raises(ValueError, match="two to four"):
        validate_comparison_models(_selection("alpha"), settings)
    with pytest.raises(ValueError, match="duplicated"):
        validate_comparison_models(_selection("alpha", "alpha"), settings)
    with pytest.raises(ValueError, match="unavailable"):
        validate_comparison_models(_selection("alpha", "unknown"), settings)
    with pytest.raises(ValueError, match="two to four"):
        validate_comparison_models(_selection("alpha", "beta", "gamma", "delta", "epsilon"), settings)


def test_comparison_provider_runtime_never_mutates_session_default(monkeypatch):
    settings = ComparisonSettings()
    session = api_provider.ProviderRuntime()
    session.set_ollama_models({config.TASK_CHAT: "original"})
    a = api_provider.comparison_runtime(ModelRef("Ollama", "alpha"), settings, runtime=session)
    b = api_provider.comparison_runtime(ModelRef("Llama.cpp", "current.gguf"), settings, runtime=session)

    assert session.snapshot().ollama_models[config.TASK_CHAT] == "original"
    assert a.snapshot().ollama_models[config.TASK_CHAT] == "alpha"
    assert b.snapshot().llama_cpp_settings["chat_model_path"] == "/models/current.gguf"
    assert a.is_configured() and b.is_configured()

    settings.cloud_models["Anthropic Claude"] = ["claude-test"]
    client = object()
    monkeypatch.setattr(api_provider, "_build_api_client", lambda provider, key, endpoint=None:
                        (client, key, endpoint))
    cloud = api_provider.comparison_runtime(
        ModelRef("Anthropic Claude", "claude-test"), settings, runtime=session,
    )
    assert cloud.snapshot().api_client is client
    assert cloud.snapshot().api_provider_type == "Anthropic Claude"
    assert cloud.snapshot().api_models[config.TASK_CHAT] == "claude-test"
    assert session.snapshot().use_api_mode is False


def test_comparison_streams_into_three_sibling_nodes_without_composer_request(monkeypatch):
    started = threading.Event()
    allow_finish = threading.Event()
    lock = threading.Lock()
    running = set()
    seen_histories = {}

    def stream(history, persona, cancellation_event, on_chunk, **kwargs):
        ref = kwargs["model_ref"]
        with lock:
            running.add(ref.model_id)
            seen_histories[ref.model_id] = history
            if len(running) == 3:
                started.set()
        on_chunk(f"{ref.model_id} first", False)
        allow_finish.wait(5)
        if cancellation_event.is_set():
            return "suppressed"
        on_chunk(" and last", False)
        kwargs["on_usage"]({"prompt_tokens": 11, "completion_tokens": 6})
        return f"{ref.model_id} first and last"

    monkeypatch.setattr(agents_module, "_call_chat_agent_stream", stream)

    async def run():
        settings, bus, composer, dispatcher, document = _environment("comparison-three-streams")
        user_id = await bus.dispatch_intent(
            "scene", "compareModels", ["Find the answer", _selection("alpha", "beta", "gamma"), None],
        )
        replies = [node for node in document.nodes.values()
                   if node.kind == "chat" and not node.state.is_user]
        assert len(replies) == 3
        assert document.last_chat_node_id == user_id
        assert len({node.state.model for node in replies}) == 3
        assert all(document._branch_parent_edge(node.id).source == user_id for node in replies)
        assert composer.request_id is None and composer.request_state == "idle"
        assert len([h for h in dispatcher._runs.values() if h.kind.startswith("chat_comparison:")]) == 3
        handles = list(dispatcher._runs.values())
        await asyncio.to_thread(started.wait, 5)
        assert running == {"alpha", "beta", "gamma"}
        assert all(node.pending_request_id for node in replies)
        allow_finish.set()
        await asyncio.gather(*(h.task for h in handles))
        assert all(node.pending_request_id is None for node in replies)
        assert all("Latency:" in node.content for node in replies)
        assert all("11 input / 6 output" in node.content for node in replies)
        assert all("$0.000000 estimated" in node.content for node in replies)
        assert all(node.state.prompt_tokens == 11 for node in replies)
        assert all(node.state.completion_tokens == 6 for node in replies)
        assert all(node.state.estimated_cost_usd == 0 for node in replies)
        assert all(history[-1]["content"] == "Find the answer" for history in seen_histories.values())
        assert not list(dispatcher._runs.values())

    asyncio.run(run())


def test_failed_and_cancelled_siblings_do_not_stop_healthy_reply(monkeypatch):
    started = threading.Event()
    allow_finish = threading.Event()
    lock = threading.Lock()
    running = set()

    def stream(history, persona, cancellation_event, on_chunk, **kwargs):
        model = kwargs["model_ref"].model_id
        with lock:
            running.add(model)
            if len(running) == 3:
                started.set()
        on_chunk(f"partial {model}", False)
        allow_finish.wait(5)
        if model == "beta":
            raise RuntimeError("backend unavailable")
        if cancellation_event.is_set():
            return "discarded canceled result"
        return f"complete {model}"

    monkeypatch.setattr(agents_module, "_call_chat_agent_stream", stream)

    async def run():
        _, bus, composer, dispatcher, document = _environment("comparison-cancel-test")
        user_id = await bus.dispatch_intent(
            "scene", "compareModels", ["hello", _selection("alpha", "beta", "gamma"), None],
        )
        handles = list(dispatcher._runs.values())
        await asyncio.to_thread(started.wait, 5)
        replies = {node.state.override_model_id: node for node in document.nodes.values()
                   if node.kind == "chat" and not node.state.is_user}
        assert all(node.pending_request_id for node in replies.values())
        assert await bus.dispatch_intent(
            "scene", "cancelChatRequest", [replies["gamma"].pending_request_id],
        ) is True
        allow_finish.set()
        await asyncio.gather(*(h.task for h in handles))
        await asyncio.sleep(0)
        assert document.last_chat_node_id == user_id
        assert "Complete" in replies["alpha"].content
        assert "complete alpha" in replies["alpha"].content
        assert not replies["alpha"].state.response_incomplete
        assert replies["beta"].state.response_incomplete
        assert "partial beta" in replies["beta"].content
        assert replies["gamma"].state.response_incomplete
        assert "Canceled" in replies["gamma"].content
        assert "discarded canceled result" not in replies["gamma"].content
        assert all(node.pending_request_id is None for node in replies.values())
        assert composer.request_id is None

    asyncio.run(run())


def test_ordinary_single_send_still_uses_original_chat_slot(monkeypatch):
    def stream(history, persona, cancellation_event, on_chunk, **kwargs):
        on_chunk("unchanged", False)
        return "unchanged"
    monkeypatch.setattr(agents_module, "_call_chat_agent_stream", stream)

    async def run():
        _, bus, composer, dispatcher, document = _environment("comparison-single-parity")
        # Existing provider gate remains active. The default module state
        # is configured only for this test, without altering other sessions.
        monkeypatch.setattr(api_provider, "is_configured", lambda: True)
        user_id = await bus.dispatch_intent("scene", "sendMessage", ["ordinary"])
        handles = list(dispatcher._runs.values())
        assert len(handles) == 1 and handles[0].kind == "chat"
        await asyncio.gather(*(h.task for h in handles))
        assert composer.request_id is None
        assert any(n.kind == "chat" and not n.state.is_user and "unchanged" in n.content
                   for n in document.nodes.values())
        assert user_id in document.nodes

    asyncio.run(run())
