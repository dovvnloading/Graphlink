"""Gemini usage accounting: offline REST fixtures and downstream consumers."""
from __future__ import annotations

import pytest

import graphlink_task_config as config
from backend.harness.loop import _spend_from_turn
from backend.providers import CancelToken, ChatRequest, GeminiProvider, ToolSpec
from backend.providers import gemini_provider
from backend.providers.gemini_provider import _usage_from_metadata
from backend.token_counter import TokenCounterState


@pytest.mark.parametrize(("metadata", "expected"), [
    (None, None),
    ([], None),
    ({}, None),
    ({"totalTokenCount": 99, "cachedContentTokenCount": 20}, None),
    ({"promptTokenCount": 10, "candidatesTokenCount": 3}, (10, 3)),
    ({"promptTokenCount": 10, "candidatesTokenCount": 3, "thoughtsTokenCount": 0}, (10, 3)),
    ({"promptTokenCount": 1000, "candidatesTokenCount": 200,
      "thoughtsTokenCount": 800, "totalTokenCount": 2000}, (1000, 1000)),
    ({"promptTokenCount": 100, "cachedContentTokenCount": 80,
      "toolUsePromptTokenCount": 10, "candidatesTokenCount": 20,
      "thoughtsTokenCount": 30, "totalTokenCount": 160}, (110, 50)),
    ({"thoughtsTokenCount": 8}, (None, 8)),
    ({"toolUsePromptTokenCount": 7}, (7, None)),
    ({"thoughtsTokenCount": 0}, (None, 0)),
    ({"toolUsePromptTokenCount": 0}, (0, None)),
    ({"promptTokenCount": 0, "candidatesTokenCount": 0}, (0, 0)),
    ({"promptTokenCount": "10", "candidatesTokenCount": 3.0,
      "thoughtsTokenCount": "7", "toolUsePromptTokenCount": "2"}, (12, 10)),
    ({"promptTokenCount": "bad", "toolUsePromptTokenCount": 9,
      "candidatesTokenCount": 4, "thoughtsTokenCount": float("inf")}, (9, 4)),
    ({"promptTokenCount": 10, "toolUsePromptTokenCount": [],
      "candidatesTokenCount": float("nan"), "thoughtsTokenCount": 8}, (10, 8)),
    ({"promptTokenCount": 10, "toolUsePromptTokenCount": -4,
      "candidatesTokenCount": -9, "thoughtsTokenCount": 8}, (10, 8)),
    ({"promptTokenCount": -1, "candidatesTokenCount": {},
      "thoughtsTokenCount": float("inf"), "toolUsePromptTokenCount": "bad"}, None),
])
def test_usage_metadata(metadata, expected):
    assert _usage_from_metadata(metadata) == (
        None if expected is None else {"prompt_tokens": expected[0], "completion_tokens": expected[1]}
    )


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(
        gemini_provider, "_prepare_gemini_contents",
        lambda *_args, **_kwargs: (None, [{"parts": [{"text": "hi"}]}], []),
    )
    return GeminiProvider(api_key="k", model="gemini-2.5-pro")


def test_blocking_usage_includes_thoughts_even_without_visible_thought_parts(provider, monkeypatch):
    payload = {
        "candidates": [{"content": {"parts": [{"text": "Answer."}]}, "finishReason": "STOP"}],
        "usageMetadata": {
            "promptTokenCount": 1000, "candidatesTokenCount": 200,
            "thoughtsTokenCount": 800, "totalTokenCount": 2000,
        },
    }
    monkeypatch.setattr(gemini_provider, "_gemini_post_json", lambda *_args, **_kwargs: payload)
    request = ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}])

    assert provider.complete(request, CancelToken()) == "Answer."
    assert provider.last_usage == {"prompt_tokens": 1000, "completion_tokens": 1000}
    # Reusing the same provider for a response without metadata must clear usage.
    del payload["usageMetadata"]
    assert provider.complete(request, CancelToken()) == "Answer."
    assert provider.last_usage is None


@pytest.mark.parametrize("tool_turn", [False, True])
def test_stream_usage_reaches_token_counter_and_harness(provider, monkeypatch, tool_turn):
    part = (
        {"functionCall": {"name": "echo", "args": {"message": "hi"}}}
        if tool_turn else {"text": "Answer."}
    )
    tool_prompt = 30 if tool_turn else 0
    frames = [
        {"usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 1, "thoughtsTokenCount": 2}},
        {"candidates": [{"content": {"parts": [part]}, "finishReason": "STOP"}]},
        {"usageMetadata": {
            "promptTokenCount": 1000, "cachedContentTokenCount": 100,
            "toolUsePromptTokenCount": tool_prompt, "candidatesTokenCount": 200,
            "thoughtsTokenCount": 800, "totalTokenCount": 2000 + tool_prompt,
        }},
        {"usageMetadata": {}},
    ]
    monkeypatch.setattr(
        gemini_provider, "_gemini_stream_sse",
        lambda *_args, **_kwargs: (frame for frame in frames),
    )
    tools = (ToolSpec("echo", "Echo a message", {"type": "object"}),) if tool_turn else ()
    request = ChatRequest(
        task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}], tools=tools,
    )

    events = list(provider.stream(request, CancelToken()))
    assert [event.type for event in events] == (["tool_call", "done"] if tool_turn else ["text", "done"])
    usage = events[-1].usage
    assert usage == {"prompt_tokens": 1000 + tool_prompt, "completion_tokens": 1000}

    # Use explicit fixture rates, independent of the changing provider price list.
    counter = TokenCounterState(
        pricing_overrides_fn=lambda: {"gemini-2.5-pro": {"input": 2.0, "output": 5.0}},
    )
    counter.set_real_usage(**usage, provider="gemini", model="gemini-2.5-pro")
    payload = counter.payload()
    expected_cost = (1000 + tool_prompt) * 2 / 1_000_000 + 1000 * 5 / 1_000_000
    assert payload["totalTokens"] == 2000 + tool_prompt
    assert payload["sessionPromptTokens"] == 1000 + tool_prompt
    assert payload["sessionCompletionTokens"] == 1000
    assert payload["estimatedCostUsd"] == pytest.approx(expected_cost)
    assert payload["sessionEstimatedCostUsd"] == pytest.approx(expected_cost)
    assert _spend_from_turn({"usage": usage}, "ignored fallback") == 2000 + tool_prompt
