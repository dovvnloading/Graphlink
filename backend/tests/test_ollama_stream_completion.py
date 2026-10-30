"""OllamaProvider.stream() commits a reply only when the server said it
finished: a done:true chunk, or the tool_calls chunk the stream loop treats
as terminal. A stream that just stops is a truncation and must raise the
same unexpected-end error AnthropicProvider/GeminiProvider/OpenAIProvider
raise, so the dispatch layer lands the partial flagged incomplete."""

from __future__ import annotations

import ollama
import pytest

import graphlink_task_config as config
from backend.providers import CancelToken, ChatRequest, OllamaProvider

_TRUNCATED = "Ollama stream ended unexpectedly before completion"


def _part(content="", *, thinking=None, tool_calls=None, done=False, done_reason=None):
    message: dict = {"role": "assistant", "content": content}
    if thinking is not None:
        message["thinking"] = thinking
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    part: dict = {"model": "llama3", "message": message, "done": done}
    if done:
        part.update(done_reason=done_reason or "stop", prompt_eval_count=9, eval_count=4)
    return part


def _events(monkeypatch, *streams, model="llama3", tools=(), calls=None):
    # `calls` is filled even when stream() raises, so a test can still
    # count how many requests went out before the error.
    calls = [] if calls is None else calls

    def fake_chat(**kwargs):
        calls.append(kwargs)
        parts = streams[len(calls) - 1]

        def generate():
            yield from parts

        return generate()

    monkeypatch.setattr(ollama, "chat", fake_chat)
    provider = OllamaProvider(model=model)
    request = ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}], tools=tools)
    return list(provider.stream(request, CancelToken())), calls


def test_truncated_text_stream_raises_instead_of_committing_the_fragment(monkeypatch):
    with pytest.raises(RuntimeError, match=_TRUNCATED):
        _events(monkeypatch, [_part("The answer is"), _part(" half a sen")])


def test_truncated_thinking_only_stream_raises_rather_than_retrying(monkeypatch):
    # A reasoning model cut off mid-thought is a truncation, not a model
    # that finished without an answer - so no reasoning retry goes out.
    calls: list = []
    with pytest.raises(RuntimeError, match=_TRUNCATED):
        _events(
            monkeypatch, [_part(thinking="Let me think")], [_part("x", done=True)],
            model="qwen3:8b", calls=calls,
        )
    assert len(calls) == 1


def test_complete_text_stream_passes(monkeypatch):
    events, _ = _events(monkeypatch, [_part("Hello"), _part(" world"), _part("", done=True)])
    assert [e.type for e in events] == ["text", "text", "done"]
    assert events[-1].text == "Hello world"
    assert events[-1].usage == {"prompt_tokens": 9, "completion_tokens": 4}


def test_tool_call_only_stream_passes(monkeypatch):
    call = {"function": {"name": "get_weather", "arguments": {"city": "Paris"}}}
    events, _ = _events(monkeypatch, [_part(tool_calls=[call]), _part("", done=True)])
    assert [e.type for e in events] == ["tool_call", "done"]


def test_length_finish_passes(monkeypatch):
    events, _ = _events(monkeypatch, [_part("A long answer"), _part(" that", done=True, done_reason="length")])
    assert events[-1].text == "A long answer that"
