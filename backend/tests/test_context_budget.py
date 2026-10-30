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

import pytest

import api_provider
import graphlink_task_config as config
from graphlink_chat_agent import ChatWorker, clear_summary_cache
from graphlink_memory import trim_history
from graphlink_token_estimator import TokenEstimator


@pytest.fixture(autouse=True)
def _fresh_summary_cache():
    # 6.8 review fix: the dropped-turn summary cache is module-global and
    # keyed by exact message content - identical fixture histories across
    # tests would otherwise cross-pollinate hits.
    clear_summary_cache()
    yield
    clear_summary_cache()


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


def test_ollama_mode_trained_max_is_capped_to_the_served_cap(monkeypatch):
    # 6.8 review fix (HIGH): the trained max ("<arch>.context_length") is
    # NOT what the daemon serves - without an explicit Modelfile num_ctx we
    # budget (and request, via options.num_ctx) the KV-cache-safe cap, not
    # the trained 32k/131k.
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
    assert runtime.context_window(config.TASK_CHAT) == api_provider._OLLAMA_SERVED_CONTEXT_CAP
    api_provider.invalidate_ollama_capability_cache()


def test_ollama_mode_explicit_modelfile_num_ctx_wins_over_the_cap(monkeypatch):
    api_provider.invalidate_ollama_capability_cache()

    def fake_show(model):
        return {
            "parameters": "stop \"<|end|>\"\nnum_ctx 16384\ntemperature 0.7",
            "model_info": {
                "general.architecture": "llama",
                "llama.context_length": 131_072,
            },
        }

    monkeypatch.setattr(api_provider, "ollama", types.SimpleNamespace(show=fake_show))
    runtime = api_provider.ProviderRuntime()
    runtime.set_ollama_models({config.TASK_CHAT: "modelfile-ctx-model:7b"})
    assert runtime.context_window(config.TASK_CHAT) == 16_384
    api_provider.invalidate_ollama_capability_cache()


def test_ollama_mode_small_trained_max_passes_through_uncapped(monkeypatch):
    api_provider.invalidate_ollama_capability_cache()

    def fake_show(model):
        return {"model_info": {"general.architecture": "llama", "llama.context_length": 4096}}

    monkeypatch.setattr(api_provider, "ollama", types.SimpleNamespace(show=fake_show))
    runtime = api_provider.ProviderRuntime()
    runtime.set_ollama_models({config.TASK_CHAT: "small-ctx-model:3b"})
    assert runtime.context_window(config.TASK_CHAT) == 4096
    api_provider.invalidate_ollama_capability_cache()


def test_ollama_request_asks_the_daemon_to_serve_the_budgeted_window(monkeypatch):
    # 6.8 review fix (HIGH), request side: the budgeted window is passed as
    # options.num_ctx so the daemon can never silently truncate below it.
    from backend.providers.base import CancelToken, ChatRequest
    from backend.providers.ollama_provider import OllamaProvider

    captured = {}

    def fake_chat(*, model, messages, stream=False, **kwargs):
        captured.update(kwargs)
        if stream:
            return iter([{"message": {"content": "ok"}, "done": True}])
        return {"message": {"content": "ok"}}

    import backend.providers.ollama_provider as op_module
    monkeypatch.setattr(op_module, "ollama", types.SimpleNamespace(chat=fake_chat))

    provider = OllamaProvider(model="llava:13b", context_window=8192)
    provider.complete(
        ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]),
        CancelToken(),
    )
    assert captured["options"] == {"num_ctx": 8192}

    # None (older direct constructions) omits the option entirely.
    captured.clear()
    provider = OllamaProvider(model="llava:13b")
    provider.complete(
        ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]),
        CancelToken(),
    )
    assert "options" not in captured


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


# -- 6.8 review fixes: summary cache + keep-newest guarantee -------------------


def test_repeat_drops_reuse_the_cached_summary_and_signal_only_once(monkeypatch):
    # Review fix (toast spam): the SAME dropped prefix on a later turn is a
    # cache hit - no second summarizer call, no second on_context_trimmed.
    captured = {}
    signals = []
    monkeypatch.setattr(api_provider, "chat", _fake_chat_capturing(captured))
    history = _history(40, chars=2000)
    small_runtime = types.SimpleNamespace(context_window=lambda task: 2_000)
    worker = ChatWorker("")

    for _ in range(2):
        worker.run(
            history, None, resolved_system_prompt="", runtime=small_runtime,
            on_context_trimmed=lambda dropped, summarized: signals.append((dropped, summarized)),
        )

    assert len(captured["summarize_calls"]) == 1  # second run was a cache hit
    assert len(signals) == 1  # the toast fires once, not per message forever
    # The cached summary is still INJECTED on the hit - only the model call
    # and the signal are skipped.
    assert captured["messages"][0]["content"].startswith("[Summary of earlier conversation]\n")


def test_grown_drop_summarizes_incrementally_from_the_cached_prefix(monkeypatch):
    # Review fix (bounded incremental work): when the dropped tuple grows,
    # the new summarizer input is (cached prefix summary + remainder), not
    # everything from scratch.
    captured = {}
    monkeypatch.setattr(api_provider, "chat", _fake_chat_capturing(captured))
    small_runtime = types.SimpleNamespace(context_window=lambda task: 2_000)
    worker = ChatWorker("")

    history = _history(40, chars=2000)
    worker.run(history, None, resolved_system_prompt="", runtime=small_runtime)
    assert len(captured["summarize_calls"]) == 1

    # Two MORE turns arrive; the drop grows past the cached prefix.
    grown = history + [
        {"role": "user", "content": "newer question " + "y" * 2000},
        {"role": "assistant", "content": "newer answer " + "z" * 2000},
    ]
    worker.run(grown, None, resolved_system_prompt="", runtime=small_runtime)
    assert len(captured["summarize_calls"]) == 2
    incremental_input = captured["summarize_calls"][1][1]["content"]
    # The second summarizer call starts from the cached prefix summary...
    assert "[Summary of earlier conversation]" in incremental_input
    assert "a compact summary" in incremental_input
    # ...and does NOT re-feed the full original prefix from scratch.
    assert "m0 " not in incremental_input


def test_trim_history_always_keeps_an_oversized_newest_message():
    # Review fix: the provider seeing an over-budget final question beats
    # the model never seeing the question.
    history = _history(1, chars=50_000)
    trimmed, tokens = trim_history(history, TokenEstimator(), max_tokens=500)
    assert trimmed == history
    assert tokens > 500  # honestly over budget, still kept


def test_oversized_newest_message_is_kept_and_excluded_from_the_summarizer(monkeypatch):
    captured = {}
    monkeypatch.setattr(api_provider, "chat", _fake_chat_capturing(captured))
    history = _history(5, chars=800) + [
        {"role": "user", "content": "THE ACTUAL QUESTION " + "q" * 40_000}
    ]
    tiny_runtime = types.SimpleNamespace(context_window=lambda task: 2_000)
    worker = ChatWorker("")
    worker.run(history, None, resolved_system_prompt="", runtime=tiny_runtime)

    # The newest message reached the wire in full...
    assert captured["messages"][-1]["content"].startswith("THE ACTUAL QUESTION")
    # ...and the summarizer input covered only the OLDER dropped turns,
    # never the question itself.
    assert len(captured["summarize_calls"]) == 1
    summarizer_input = captured["summarize_calls"][0][1]["content"]
    assert "THE ACTUAL QUESTION" not in summarizer_input


# -- image vision-token budgeting + exact context_tokens ---------------------


def _png_bytes(width: int, height: int, *, noisy: bool = False) -> bytes:
    """A real PNG of the given dimensions. `noisy` fills it with random
    pixels so the encoded payload is large (an incompressible image is
    what makes a payload-as-text count blow up)."""
    import io
    import os

    from PIL import Image

    if noisy:
        image = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    else:
        image = Image.new("RGB", (width, height), (120, 30, 200))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _image_message(data, text: str = "what is in this picture?", role: str = "user") -> dict:
    return {"role": role, "content": [
        {"type": "text", "text": text},
        {"type": "image_bytes", "data": data},
    ]}


def _text_tokens(message: dict) -> int:
    import json

    return TokenEstimator().count_tokens(json.dumps(message))


def test_estimate_image_tokens_follows_the_documented_vision_formula():
    import graphlink_token_estimator as estimator_module

    # Within every limit: width * height / 750, rounded up.
    assert estimator_module.estimate_image_tokens(_png_bytes(1000, 800)) == 1067
    # Long edge over 1568px: scaled to a 1568px long edge first
    # (2000x500 -> 1568x392 = 614,656 px -> 820 tokens).
    assert estimator_module.estimate_image_tokens(_png_bytes(2000, 500)) == 820
    # Over the per-image token ceiling after the long-edge scale: capped.
    assert (
        estimator_module.estimate_image_tokens(_png_bytes(1600, 1600))
        == estimator_module.IMAGE_TOKENS_MAX
    )


def test_estimate_image_tokens_falls_back_to_the_conservative_constant():
    import graphlink_token_estimator as estimator_module

    fallback = estimator_module.IMAGE_TOKENS_FALLBACK
    # Never under-budget an image whose size cannot be read: the fallback
    # is the most any single image can cost under the formula.
    assert fallback == estimator_module.IMAGE_TOKENS_MAX
    assert estimator_module.estimate_image_tokens(b"definitely not an image") == fallback
    assert estimator_module.estimate_image_tokens(b"") == fallback
    assert estimator_module.estimate_image_tokens(None) == fallback


def test_trim_history_budgets_an_image_at_its_vision_cost_not_a_placeholder():
    message = _image_message(_png_bytes(1000, 800))
    trimmed, tokens = trim_history([message], TokenEstimator(), max_tokens=1_000_000)
    assert trimmed == [message]
    # ~1,067 vision tokens plus a little text - the old placeholder-only
    # count budgeted this at a few dozen.
    assert 1067 <= tokens <= 1067 + 60


def test_trim_history_excludes_a_base64_image_payload_from_the_text_count():
    import base64

    # The provider transports also accept image_bytes data as base64 text;
    # that payload is pixels, not prose, and must cost its vision estimate
    # (400x300 -> 160 tokens), never ~1 token per 4 characters of base64.
    encoded = base64.b64encode(_png_bytes(400, 300, noisy=True)).decode("ascii")
    assert len(encoded) > 100_000
    trimmed, tokens = trim_history(
        [_image_message(encoded)], TokenEstimator(), max_tokens=10_000_000
    )
    assert len(trimmed) == 1
    assert 160 <= tokens <= 160 + 60


def test_trim_history_unreadable_image_costs_the_conservative_fallback():
    import graphlink_token_estimator as estimator_module

    message = _image_message(b"\x89PNG but truncated garbage")
    _, tokens = trim_history([message], TokenEstimator(), max_tokens=1_000_000)
    fallback = estimator_module.IMAGE_TOKENS_FALLBACK
    assert fallback <= tokens <= fallback + 60


def test_trim_history_trims_an_image_heavy_history_to_fit_a_small_budget():
    image = _png_bytes(1000, 800)  # ~1,067 vision tokens each
    history = [
        _image_message(image, text=f"picture {i}", role="user" if i % 2 == 0 else "assistant")
        for i in range(8)
    ]
    trimmed, tokens = trim_history(
        history, TokenEstimator(), max_tokens=4_000, system_prompt_estimate=0
    )
    # Eight images (~8.5k tokens) cannot fit 4k: only a contiguous newest
    # suffix survives, and what it reports is genuinely within budget.
    assert 0 < len(trimmed) < len(history)
    assert trimmed == history[-len(trimmed):]
    assert tokens <= 4_000
    assert tokens >= 1067 * len(trimmed)


def test_small_window_chat_worker_drops_images_that_would_overflow(monkeypatch):
    # The production gate: an 8k-window local model's history budget is
    # window - reserve (6,000 here). Eight ~1,067-token images must not all
    # reach the wire.
    captured = {}
    monkeypatch.setattr(api_provider, "chat", _fake_chat_capturing(captured))
    image = _png_bytes(1000, 800)
    history = [_image_message(image, text=f"picture {i}") for i in range(8)]
    small_runtime = types.SimpleNamespace(context_window=lambda task: 8_000)

    worker = ChatWorker("")
    worker.run(history, None, resolved_system_prompt="", runtime=small_runtime)

    sent_images = [
        part
        for message in captured["messages"]
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if part.get("type") == "image_bytes"
    ]
    assert 0 < len(sent_images) <= 6_000 // 1067


def test_trim_history_single_assistant_message_reports_its_own_tokens_once():
    message = {"role": "assistant", "content": "a lone assistant turn " + "x" * 160}
    trimmed, tokens = trim_history([message], TokenEstimator(), max_tokens=1_000_000)
    # Popped as a leading assistant, then restored by the keep-newest
    # guarantee - counted exactly once, not once per pass.
    assert trimmed == [message]
    assert tokens == _text_tokens(message)


def test_trim_history_popped_leading_assistant_is_not_counted():
    assistant = {"role": "assistant", "content": "an earlier answer " + "a" * 200}
    user = {"role": "user", "content": "the newest question " + "q" * 40}
    trimmed, tokens = trim_history([assistant, user], TokenEstimator(), max_tokens=1_000_000)
    assert trimmed == [user]
    assert tokens == _text_tokens(user)


def test_summary_cache_key_digests_image_payloads_instead_of_stringifying_them():
    # Image turns are now dropped (and summarized) much sooner on small
    # windows, so a key built from the payload's repr would grow with every
    # dropped image - the digest keeps it small without merging two images.
    from graphlink_chat_agent import _summary_cache_key

    big = _png_bytes(1200, 900, noisy=True)
    other = _png_bytes(1200, 900)
    key = _summary_cache_key([_image_message(big)])

    assert len(str(key)) < 1_000
    assert key == _summary_cache_key([_image_message(big)])
    assert key != _summary_cache_key([_image_message(other)])
    assert _summary_cache_key([{"role": "user", "content": "plain"}]) == (("user", "plain"),)


def test_context_meter_prices_branch_images_like_the_budget_does():
    from backend.canvas import _history_image_tokens, _history_token_text
    from backend.token_counter import TokenCounterState, estimate_tokens
    from graphlink_token_estimator import estimate_image_tokens

    image = _png_bytes(800, 600)
    history = [{"role": "user", "content": "hello"}, _image_message(image, text="look")]
    state = TokenCounterState()
    state.set_context_text(_history_token_text(history), image_tokens=_history_image_tokens(history))

    assert _history_image_tokens(history) == estimate_image_tokens(image) > 100
    assert state.context_tokens == estimate_tokens(_history_token_text(history)) + estimate_image_tokens(image)
