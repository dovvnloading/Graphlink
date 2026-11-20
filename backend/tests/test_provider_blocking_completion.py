"""Blocking completions require terminal markers and reject incomplete stops."""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

import graphlink_task_config as config
from backend.providers import (
    AnthropicProvider, CancelToken, ChatRequest, GeminiProvider,
    LlamaCppProvider, OllamaProvider, OpenAIProvider, ToolSpec,
)
from backend.providers import anthropic_provider, gemini_provider, llama_cpp_provider, ollama_provider


def _request(*, tool_choice=False):
    return ChatRequest(
        task=config.TASK_TITLE,
        messages=[{"role": "user", "content": "Respond completely"}],
        tools=(ToolSpec("extract", "Return a result", {"type": "object"}),) if tool_choice else (),
        tool_choice="extract" if tool_choice else None,
    )


def _openai_provider(reason):
    response = SimpleNamespace(
        choices=[SimpleNamespace(
            finish_reason=reason, message=SimpleNamespace(content="partial but valid JSON"),
        )],
        usage=None,
    )
    client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=lambda **_kwargs: response),
    ))
    return OpenAIProvider(client=client, model="gpt-4o")


@pytest.mark.parametrize("reason", [None, "length", "content_filter", "other_stop"])
def test_openai_blocking_rejects_incomplete_finish(reason):
    with pytest.raises(RuntimeError, match="OpenAI-compatible generation incomplete"):
        _openai_provider(reason).complete(_request(), CancelToken())


@pytest.mark.parametrize("reason", ["stop", "tool_calls", "function_call"])
def test_openai_blocking_accepts_successful_finish(reason):
    assert _openai_provider(reason).complete(_request(), CancelToken()) == "partial but valid JSON"


def test_openai_blocking_rejects_empty_choices():
    provider = _openai_provider("stop")
    provider.client.chat.completions.create = lambda **_kwargs: SimpleNamespace(choices=[], usage=None)
    with pytest.raises(RuntimeError, match="OpenAI-compatible generation incomplete"):
        provider.complete(_request(), CancelToken())


def _llama_provider(monkeypatch, reason, *, has_choices=True):
    class FakeLlama:
        def create_chat_completion(self, **_kwargs):
            return {"choices": [{
                "finish_reason": reason,
                "message": {"content": "llama answer"},
            }] if has_choices else []}

    monkeypatch.setattr(llama_cpp_provider, "_get_llama_cpp_client", lambda _task, _settings: FakeLlama())
    monkeypatch.setattr(llama_cpp_provider, "_prepare_llama_cpp_messages", lambda messages, _task, _settings: messages)
    monkeypatch.setattr(llama_cpp_provider, "_prepare_llama_cpp_kwargs", lambda kwargs, _settings: kwargs)
    monkeypatch.setattr(llama_cpp_provider, "_filter_kwargs_for_callable", lambda _fn, kwargs: kwargs)
    monkeypatch.setattr(llama_cpp_provider, "llama_cpp_inference_lock", lambda _client: threading.Lock())
    return LlamaCppProvider(settings={"chat_model_path": "stub.gguf"})


@pytest.mark.parametrize("reason", [None, "length", "content_filter", "other_stop"])
def test_llama_cpp_blocking_rejects_incomplete_finish(monkeypatch, reason):
    with pytest.raises(RuntimeError, match="llama.cpp generation incomplete"):
        _llama_provider(monkeypatch, reason).complete(_request(), CancelToken())


def test_llama_cpp_blocking_accepts_stop(monkeypatch):
    assert _llama_provider(monkeypatch, "stop").complete(_request(), CancelToken()) == "llama answer"


def test_llama_cpp_blocking_rejects_empty_choices(monkeypatch):
    with pytest.raises(RuntimeError, match="llama.cpp generation incomplete"):
        _llama_provider(monkeypatch, "stop", has_choices=False).complete(_request(), CancelToken())


def _anthropic_provider(monkeypatch, response, transport):
    if transport == "sdk":
        client = SimpleNamespace(messages=SimpleNamespace(create=lambda **_kwargs: response))
    else:
        monkeypatch.setattr(anthropic_provider, "_anthropic_post_json", lambda *_args, **_kwargs: response)
        client = {"provider": "anthropic", "transport": "rest"}
    return AnthropicProvider(client=client, api_key="k", model="claude-sonnet-4-5")


@pytest.mark.parametrize("transport", ["sdk", "rest"])
@pytest.mark.parametrize("reason", ["max_tokens", "refusal", "model_context_window_exceeded"])
def test_anthropic_blocking_rejects_incomplete_finish(monkeypatch, reason, transport):
    response = {"stop_reason": reason, "content": [{"type": "text", "text": "partial"}]}
    with pytest.raises(RuntimeError, match="Anthropic generation incomplete"):
        _anthropic_provider(monkeypatch, response, transport).complete(_request(), CancelToken())


@pytest.mark.parametrize("transport", ["sdk", "rest"])
@pytest.mark.parametrize("reason", ["end_turn", "stop_sequence"])
def test_anthropic_blocking_accepts_text_stops(monkeypatch, reason, transport):
    response = {"stop_reason": reason, "content": [{"type": "text", "text": "complete"}]}
    assert _anthropic_provider(monkeypatch, response, transport).complete(_request(), CancelToken()) == "complete"


@pytest.mark.parametrize("transport", ["sdk", "rest"])
def test_anthropic_blocking_allows_optional_missing_reason(monkeypatch, transport):
    response = {"content": [{"type": "text", "text": "complete"}]}
    assert _anthropic_provider(monkeypatch, response, transport).complete(
        _request(), CancelToken(),
    ) == "complete"


@pytest.mark.parametrize("transport", ["sdk", "rest"])
def test_anthropic_blocking_validates_stop_before_returning_forced_tool_json(monkeypatch, transport):
    response = {
        "stop_reason": "max_tokens",
        "content": [{"type": "tool_use", "name": "extract", "input": {"looks_complete": True}}],
    }
    provider = _anthropic_provider(monkeypatch, response, transport)
    with pytest.raises(RuntimeError, match="generation incomplete"):
        provider.complete(_request(tool_choice=True), CancelToken())

    response["stop_reason"] = "tool_use"
    assert json.loads(provider.complete(_request(tool_choice=True), CancelToken())) == {"looks_complete": True}


@pytest.mark.parametrize("reason", ["MAX_TOKENS", "SAFETY", "RECITATION", "STOP", None, "missing"])
def test_gemini_blocking_validates_candidate_reason_and_cleans_uploads(monkeypatch, reason):
    deleted = []
    monkeypatch.setattr(
        gemini_provider, "_prepare_gemini_contents",
        lambda *_args, **_kwargs: (None, [{"parts": [{"text": "hello"}]}], ["files/temp"]),
    )
    monkeypatch.setattr(gemini_provider, "_gemini_delete_file", lambda name, **_kwargs: deleted.append(name))
    monkeypatch.setattr(
        gemini_provider, "_gemini_post_json",
        lambda *_args, **_kwargs: {
            "candidates": [{
                **({} if reason == "missing" else {"finishReason": reason}),
                "content": {"parts": [{"text": "partial"}]},
            }],
        },
    )
    provider = GeminiProvider(api_key="k", model="gemini-2.5-pro")
    if reason == "STOP":
        assert provider.complete(_request(), CancelToken()) == "partial"
    else:
        with pytest.raises(RuntimeError, match="Gemini generation incomplete"):
            provider.complete(_request(), CancelToken())
    assert deleted == ["files/temp"]


def test_gemini_blocking_rejects_missing_candidates_and_cleans_uploads(monkeypatch):
    deleted = []
    monkeypatch.setattr(
        gemini_provider, "_prepare_gemini_contents",
        lambda *_args, **_kwargs: (None, [], ["files/temp"]),
    )
    monkeypatch.setattr(gemini_provider, "_gemini_delete_file", lambda name, **_kwargs: deleted.append(name))
    monkeypatch.setattr(gemini_provider, "_gemini_post_json", lambda *_args, **_kwargs: {"candidates": []})
    with pytest.raises(RuntimeError, match="Gemini generation incomplete"):
        GeminiProvider(api_key="k", model="gemini-2.5-pro").complete(_request(), CancelToken())
    assert deleted == ["files/temp"]


@pytest.mark.parametrize("reason", ["length", "content_filter", "stop"])
def test_ollama_blocking_validates_done_reason(monkeypatch, reason):
    monkeypatch.setattr(ollama_provider, "ollama_supports_tools", lambda _model: False)
    monkeypatch.setattr(ollama_provider, "ollama_supports_embedding", lambda _model: False)
    monkeypatch.setattr(
        ollama_provider.ollama, "chat",
        lambda **_kwargs: {"message": {"content": "partial"}, "done": True, "done_reason": reason},
    )
    provider = OllamaProvider(model="llava:13b")
    if reason == "stop":
        assert provider.complete(_request(), CancelToken()) == "partial"
    else:
        with pytest.raises(RuntimeError, match="Ollama generation incomplete"):
            provider.complete(_request(), CancelToken())


@pytest.mark.parametrize("done", [False, None, "missing"])
def test_ollama_blocking_rejects_missing_or_false_done(monkeypatch, done):
    monkeypatch.setattr(ollama_provider, "ollama_supports_tools", lambda _model: False)
    monkeypatch.setattr(ollama_provider, "ollama_supports_embedding", lambda _model: False)
    payload = {"message": {"content": "partial"}, "done_reason": "stop"}
    if done != "missing":
        payload["done"] = done
    monkeypatch.setattr(ollama_provider.ollama, "chat", lambda **_kwargs: payload)
    with pytest.raises(RuntimeError, match="Ollama generation incomplete"):
        OllamaProvider(model="llava:13b").complete(_request(), CancelToken())
