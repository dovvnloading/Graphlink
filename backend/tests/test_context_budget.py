"""ADR-006 stage 6.6: model-derived context budget + summarizing truncation.

Covers, in order:
- ProviderRuntime.context_window() across all three modes (llama.cpp n_ctx
  exact truth, Ollama show() metadata, the documented API-family table).
- graphlink_memory.trim_history basics (this module had ZERO tests before).
- ChatWorker.run's budget plumb-through - the stage's exit criterion: a
  1M-window runtime keeps well beyond the legacy 8k of history.
- The summarize-dropped-turns behavior: summary injected as the FIRST kept
  message, silent degradation when the summarizer fails, on_context_trimmed
  signal, and the dispatcher-level notification.
"""

from __future__ import annotations

import sys
import threading
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import api_provider
import graphlink_task_config as config
from graphlink_chat_agent import ChatWorker
from graphlink_memory import trim_history
from graphlink_token_estimator import TokenEstimator


# -- ProviderRuntime.context_window -------------------------------------------


def test_llama_cpp_mode_context_window_is_the_configured_n_ctx():
    runtime = api_provider.ProviderRuntime()
    runtime._write(
        use_api_mode=False,
        local_provider_type=config.LOCAL_PROVIDER_LLAMACPP,
        llama_cpp_settings={"chat_model_path": "m.gguf", "n_ctx": 2048},
    )
    assert runtime.context_window(config.TASK_CHAT) == 2048


def test_api_mode_context_window_uses_the_documented_family_table():
    runtime = api_provider.ProviderRuntime()
    for model, expected in [
        ("claude-sonnet-4-5", 200_000),
        ("gemini-2.5-pro", 1_048_576),
        ("gpt-4.1-mini", 1_047_576),
        ("gpt-4o", 128_000),
        ("totally-unknown-model", 8_192),  # conservative pre-6.6 default
    ]:
        runtime._write(use_api_mode=True, api_models={config.TASK_CHAT: model})
        assert runtime.context_window(config.TASK_CHAT) == expected, model


def test_ollama_mode_context_window_comes_from_show_model_info(monkeypatch):
    api_provider.invalidate_ollama_capability_cache()

    def fake_show(model):
        assert model == "windowed-model:7b"
        return {
            "model_info": {
                "general.architecture": "llama",
                "llama.context_length": 32_768,
            }
        }

    monkeypatch.setattr(api_provider, "ollama", types.SimpleNamespace(show=fake_show))
    runtime = api_provider.ProviderRuntime()
    runtime.set_ollama_models({config.TASK_CHAT: "windowed-model:7b"})
    assert runtime.context_window(config.TASK_CHAT) == 32_768
    api_provider.invalidate_ollama_capability_cache()


def test_ollama_mode_context_window_falls_back_when_show_is_unavailable(monkeypatch):
    api_provider.invalidate_ollama_capability_cache()

    def failing_show(model):
        raise RuntimeError("server down")

    monkeypatch.setattr(api_provider, "ollama", types.SimpleNamespace(show=failing_show))
    runtime = api_provider.ProviderRuntime()
    runtime.set_ollama_models({config.TASK_CHAT: "unreachable-model:7b"})
    assert runtime.context_window(config.TASK_CHAT) == 8_192
    api_provider.invalidate_ollama_capability_cache()


def test_invalidate_capability_cache_also_clears_the_context_window_cache(monkeypatch):
    api_provider.invalidate_ollama_capability_cache()
    calls = []

    def counting_show(model):
        calls.append(model)
        return {"model_info": {"general.architecture": "llama", "llama.context_length": 4096}}

    monkeypatch.setattr(api_provider, "ollama", types.SimpleNamespace(show=counting_show))
    assert api_provider._get_ollama_context_window("cached-model") == 4096
    assert api_provider._get_ollama_context_window("cached-model") == 4096
    assert len(calls) == 1  # second hit served from cache
    api_provider.invalidate_ollama_capability_cache("cached-model")
    assert api_provider._get_ollama_context_window("cached-model") == 4096
    assert len(calls) == 2  # invalidation forced a re-fetch


# -- trim_history basics (first-ever coverage) --------------------------------


def _history(count: int, chars: int = 400, role: str = "user") -> list[dict]:
    return [{"role": role, "content": f"m{i} " + "x" * chars} for i in range(count)]


def test_trim_history_keeps_everything_when_it_fits():
    history = _history(4)
    trimmed, tokens = trim_history(history, TokenEstimator(), max_tokens=1_000_000)
    assert trimmed == history
    assert tokens > 0


def test_trim_history_drops_the_oldest_turns_first_and_keeps_a_contiguous_suffix():
    history = _history(20)
    trimmed, _ = trim_history(history, TokenEstimator(), max_tokens=1200, system_prompt_estimate=0)
    assert 0 < len(trimmed) < 20
    assert trimmed == history[-len(trimmed):]  # contiguous suffix, newest kept


def test_trim_history_pops_a_leading_assistant_message():
    history = _history(6)
    history[0]["role"] = "assistant"
    trimmed, _ = trim_history(history, TokenEstimator(), max_tokens=1_000_000)
    assert trimmed[0]["role"] == "user"
    assert len(trimmed) == 5


def test_trim_history_skips_malformed_entries():
    history = [{"role": "user", "content": "ok"}, "not a dict", {"no_role": True}]
    trimmed, _ = trim_history(history, TokenEstimator(), max_tokens=1_000_000)
    assert trimmed == [{"role": "user", "content": "ok"}]


# -- ChatWorker budget plumb-through + summarize-on-drop ----------------------


def _fake_chat_capturing(captured: dict, *, summary_text="a compact summary",
                         summarizer_raises=False):
    def fake_chat(*, task, messages, cancellation_event=None, **kwargs):
        if task == config.TASK_WEB_SUMMARIZE:
            captured.setdefault("summarize_calls", []).append(messages)
            if summarizer_raises:
                raise RuntimeError("summarizer died")
            return {"message": {"content": summary_text}}
        captured["task"] = task
        captured["messages"] = messages
        return {"message": {"content": "main reply"}}

    return fake_chat


def test_huge_context_window_keeps_more_history_than_the_legacy_8k_budget(monkeypatch):
    # THE stage exit criterion: a 1M-window runtime keeps >8k tokens of
    # history where the legacy fixed budget would have truncated.
    captured = {}
    monkeypatch.setattr(api_provider, "chat", _fake_chat_capturing(captured))
    history = _history(40, chars=2000)  # ~20k tokens, far beyond the old 8k
    huge_runtime = types.SimpleNamespace(context_window=lambda task: 1_000_000)

    worker = ChatWorker("")
    reply = worker.run(history, None, resolved_system_prompt="", runtime=huge_runtime)

    assert reply == "main reply"
    assert len(captured["messages"]) == 40  # nothing dropped
    assert "summarize_calls" not in captured
    estimator = TokenEstimator()
    total = sum(estimator.count_tokens(str(m)) for m in captured["messages"])
    assert total > 8_000  # genuinely beyond the legacy budget


def test_small_window_drops_summarizes_and_signals(monkeypatch):
    captured = {}
    signals = []
    monkeypatch.setattr(api_provider, "chat", _fake_chat_capturing(captured))
    history = _history(40, chars=2000)
    small_runtime = types.SimpleNamespace(context_window=lambda task: 2_000)

    worker = ChatWorker("")
    worker.run(
        history, None, resolved_system_prompt="", runtime=small_runtime,
        on_context_trimmed=lambda dropped, summarized: signals.append((dropped, summarized)),
    )

    kept = captured["messages"]
    assert len(kept) < 41
    # Summary injected as the FIRST message, user role, ahead of kept turns.
    assert kept[0]["role"] == "user"
    assert kept[0]["content"].startswith("[Summary of earlier conversation]\n")
    assert "a compact summary" in kept[0]["content"]
    assert len(captured["summarize_calls"]) == 1
    assert signals and signals[0][0] > 0 and signals[0][1] is True


def test_summarizer_failure_degrades_to_silent_drop(monkeypatch):
    captured = {}
    signals = []
    monkeypatch.setattr(
        api_provider, "chat", _fake_chat_capturing(captured, summarizer_raises=True)
    )
    history = _history(40, chars=2000)
    small_runtime = types.SimpleNamespace(context_window=lambda task: 2_000)

    worker = ChatWorker("")
    reply = worker.run(
        history, None, resolved_system_prompt="", runtime=small_runtime,
        on_context_trimmed=lambda dropped, summarized: signals.append((dropped, summarized)),
    )

    assert reply == "main reply"  # the main request NEVER fails with the summarizer
    assert not captured["messages"][0]["content"].startswith("[Summary")
    assert signals and signals[0][1] is False


def test_cancellation_before_summarization_skips_the_summarizer(monkeypatch):
    captured = {}
    monkeypatch.setattr(api_provider, "chat", _fake_chat_capturing(captured))
    history = _history(40, chars=2000)
    small_runtime = types.SimpleNamespace(context_window=lambda task: 2_000)
    cancel_event = threading.Event()
    cancel_event.set()

    worker = ChatWorker("")
    worker.run(
        history, None, cancellation_event=cancel_event,
        resolved_system_prompt="", runtime=small_runtime,
    )

    assert "summarize_calls" not in captured


def test_window_lookup_failure_falls_back_to_the_legacy_budget(monkeypatch):
    captured = {}
    monkeypatch.setattr(api_provider, "chat", _fake_chat_capturing(captured))

    def broken_window(task):
        raise RuntimeError("no lookup")

    runtime = types.SimpleNamespace(context_window=broken_window)
    worker = ChatWorker("")
    reply = worker.run(_history(3), None, resolved_system_prompt="", runtime=runtime)
    assert reply == "main reply"
    assert len(captured["messages"]) == 3
