"""A streamed reply is complete only when the server SAID it finished.

AnthropicProvider.stream() refuses a stream that ends without message_stop
(proxy truncation, a gracefully closed connection mid-answer), and the
dispatch layer then lands the streamed partial flagged response_incomplete
instead of committing it as the finished reply. These tests pin the same
posture for the two providers whose streams carry their own finish marker:

- Gemini: every finished candidate carries candidates[].finishReason (STOP,
  MAX_TOKENS, SAFETY, ... - a function-call turn finishes with STOP too).
- OpenAI-compatible: the last content chunk carries choices[].finish_reason
  (stop, length, tool_calls, content_filter, function_call); with
  stream_options.include_usage a usage-only chunk with EMPTY choices
  follows it, so the marker is not necessarily on the final chunk.

The OpenAI fixtures are real openai SDK ChatCompletionChunk objects, and
the Gemini fixtures are raw `data:` lines run through the real
_gemini_stream_sse transport, so a normal complete stream here has exactly
the shape the production parsers see.
"""

from __future__ import annotations

import json
import types

import pytest
from openai.types.chat import ChatCompletionChunk

import api_provider
import graphlink_task_config as config
from backend.providers import CancelToken, ChatRequest, GeminiProvider, OpenAIProvider
from backend.providers.base import ToolSpec

# Captured at import time, before the autouse conftest fixture swaps
# api_provider.chat_stream for its one-chunk stub - see test_providers.py's
# own identical pattern/comment.
_REAL_CHAT_STREAM = api_provider.chat_stream

_TRUNCATED = "ended unexpectedly before completion"
_WEATHER_TOOL = ToolSpec(
    name="get_weather",
    description="Look up the weather.",
    input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
)


def _request(*, tools=()):
    return ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}], tools=tools)


# -- OpenAI-compatible --------------------------------------------------------


def _openai_chunk(delta=None, finish_reason=None):
    return ChatCompletionChunk.model_validate({
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "gpt-4o",
        "choices": [{"index": 0, "delta": delta if delta is not None else {}, "finish_reason": finish_reason}],
        "usage": None,
    })


def _openai_usage_chunk(prompt_tokens=12, completion_tokens=5):
    # stream_options.include_usage's trailing chunk: EMPTY choices, real usage.
    return ChatCompletionChunk.model_validate({
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "gpt-4o",
        "choices": [],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    })


def _openai_text_stream(*texts, finish_reason="stop", usage=True):
    chunks = [_openai_chunk({"role": "assistant", "content": ""})]
    chunks += [_openai_chunk({"content": text}) for text in texts]
    if finish_reason is not None:
        chunks.append(_openai_chunk({}, finish_reason=finish_reason))
        if usage:
            chunks.append(_openai_usage_chunk())
    return chunks


def _openai_tool_call_stream(*argument_fragments, finish_reason="tool_calls"):
    chunks = [_openai_chunk({
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "index": 0, "id": "call_abc", "type": "function",
            "function": {"name": "get_weather", "arguments": ""},
        }],
    })]
    chunks += [
        _openai_chunk({"tool_calls": [{"index": 0, "function": {"arguments": fragment}}]})
        for fragment in argument_fragments
    ]
    if finish_reason is not None:
        chunks.append(_openai_chunk({}, finish_reason=finish_reason))
        chunks.append(_openai_usage_chunk())
    return chunks


class _FakeOpenAIStream:
    """The SDK Stream's iterate-and-close() surface, nothing more."""

    def __init__(self, chunks):
        self._chunks = iter(chunks)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._chunks)

    def close(self):
        self.closed = True


def _openai_client(chunks):
    def create(**kwargs):
        assert kwargs.get("stream") is True
        return _FakeOpenAIStream(chunks)

    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))


def _openai_events(chunks, *, tools=()):
    provider = OpenAIProvider(client=_openai_client(chunks), model="gpt-4o")
    return list(provider.stream(_request(tools=tools), CancelToken()))


def test_openai_truncated_text_stream_raises_instead_of_committing_the_fragment():
    with pytest.raises(RuntimeError, match=_TRUNCATED):
        _openai_events(_openai_text_stream("The answer is", " half a sen", finish_reason=None))


def test_openai_truncated_tool_call_stream_raises_the_same_error():
    # Cut off mid-arguments: the sentinel, not a JSON parse error on the
    # torn fragment (or worse, a tool call built from partial arguments).
    with pytest.raises(RuntimeError, match=_TRUNCATED):
        _openai_events(_openai_tool_call_stream('{"ci', 'ty": "Par', finish_reason=None), tools=(_WEATHER_TOOL,))


def test_openai_complete_text_stream_with_a_trailing_usage_chunk_passes():
    events = _openai_events(_openai_text_stream("Hello", " world"))

    assert [e.type for e in events] == ["text", "text", "done"]
    assert events[-1].text == "Hello world"
    assert events[-1].usage == {"prompt_tokens": 12, "completion_tokens": 5}


def test_openai_complete_stream_from_a_server_that_rejects_stream_options_passes():
    """A server that rejects stream_options gets the request retried
    without it and never sends the trailing usage chunk; the finish_reason
    chunk alone marks that stream complete."""
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if "stream_options" in kwargs:
            raise TypeError("create() got an unexpected keyword argument 'stream_options'")
        return _FakeOpenAIStream(_openai_text_stream("Hello", usage=False))

    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
    events = list(OpenAIProvider(client=client, model="gpt-4o").stream(_request(), CancelToken()))

    assert [("stream_options" in call) for call in calls] == [True, False]
    assert events[-1].type == "done" and events[-1].text == "Hello"
    assert events[-1].usage is None


def test_openai_finish_reason_on_the_last_content_chunk_itself_passes():
    # Several OpenAI-compatible servers put finish_reason on the chunk that
    # carries the final content delta rather than on a separate empty one.
    chunks = [_openai_chunk({"content": "Hel"}), _openai_chunk({"content": "lo"}, finish_reason="stop")]
    assert _openai_events(chunks)[-1].text == "Hello"


def test_openai_tool_call_only_stream_passes():
    events = _openai_events(_openai_tool_call_stream('{"ci', 'ty": "Paris"}'), tools=(_WEATHER_TOOL,))

    assert [e.type for e in events] == ["tool_call", "done"]
    assert events[0].tool_call.name == "get_weather"
    assert events[0].tool_call.arguments == {"city": "Paris"}


def test_openai_length_finish_passes():
    # A max_tokens cut-off is a FINISHED generation the server reported -
    # not a transport truncation.
    events = _openai_events(_openai_text_stream("A long answer that", finish_reason="length"))
    assert events[-1].text == "A long answer that"


@pytest.mark.parametrize("finish_reason", ["content_filter", "function_call"])
def test_openai_every_other_finish_reason_counts_as_complete(finish_reason):
    events = _openai_events(_openai_text_stream("text", finish_reason=finish_reason))
    assert events[-1].type == "done"


def test_openai_truncation_through_chat_stream_delivers_the_partial_then_raises(monkeypatch):
    """The dispatch-level contract the incomplete-reply landing relies on:
    the streamed text reached on_chunk, and chat_stream RAISES rather than
    returning the fragment as the finished message."""
    monkeypatch.setattr(api_provider, "USE_API_MODE", True)
    monkeypatch.setattr(api_provider, "API_PROVIDER_TYPE", config.API_PROVIDER_OPENAI)
    monkeypatch.setattr(api_provider, "API_KEY", "sk-fake")
    monkeypatch.setitem(api_provider.API_MODELS, config.TASK_CHAT, "gpt-4o")
    monkeypatch.setattr(
        api_provider, "API_CLIENT", _openai_client(_openai_text_stream("half a sen", finish_reason=None)),
    )

    chunks = []
    with pytest.raises(RuntimeError, match=_TRUNCATED):
        _REAL_CHAT_STREAM(
            config.TASK_CHAT, [{"role": "user", "content": "hi"}],
            lambda delta, reset: chunks.append((delta, reset)),
        )
    assert chunks == [("half a sen", False)]


# -- Gemini -------------------------------------------------------------------


def _gemini_frame(*parts, finish_reason=None, usage=None) -> dict:
    candidate: dict = {"content": {"parts": list(parts), "role": "model"}, "index": 0}
    if finish_reason is not None:
        candidate["finishReason"] = finish_reason
    frame: dict = {"candidates": [candidate], "modelVersion": "gemini-2.5-pro"}
    if usage is not None:
        frame["usageMetadata"] = usage
    return frame


_GEMINI_USAGE = {"promptTokenCount": 8, "candidatesTokenCount": 4, "totalTokenCount": 12}


class _FakeSSEResponse:
    """urllib's HTTPResponse as _gemini_stream_sse consumes it: byte lines
    (alt=sse: `data: <json>` frames separated by blank lines) and close()."""

    def __init__(self, frames):
        self._lines = []
        for frame in frames:
            self._lines += [f"data: {json.dumps(frame)}\r\n".encode(), b"\r\n"]
        self.closed = False

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        self.closed = True


def _gemini_events(monkeypatch, frames, *, tools=()):
    monkeypatch.setattr(
        api_provider.urllib.request, "urlopen", lambda request, timeout=None: _FakeSSEResponse(frames),
    )
    provider = GeminiProvider(api_key="k", model="gemini-2.5-pro")
    return list(provider.stream(_request(tools=tools), CancelToken()))


def test_gemini_truncated_text_stream_raises_instead_of_committing_the_fragment(monkeypatch):
    frames = [_gemini_frame({"text": "The answer is"}), _gemini_frame({"text": " half a sen"})]
    with pytest.raises(RuntimeError, match=_TRUNCATED):
        _gemini_events(monkeypatch, frames)


def test_gemini_complete_text_stream_passes(monkeypatch):
    frames = [
        _gemini_frame({"text": "Hello"}, usage={"promptTokenCount": 8, "totalTokenCount": 8}),
        _gemini_frame({"text": " world"}, finish_reason="STOP", usage=_GEMINI_USAGE),
    ]
    events = _gemini_events(monkeypatch, frames)

    assert [e.type for e in events] == ["text", "text", "done"]
    assert events[-1].text == "Hello world"
    assert events[-1].usage == {"prompt_tokens": 8, "completion_tokens": 4}


def test_gemini_finish_reason_on_a_trailing_frame_with_no_text_passes(monkeypatch):
    frames = [_gemini_frame({"text": "Hello"}), _gemini_frame({"text": ""}, finish_reason="STOP")]
    assert _gemini_events(monkeypatch, frames)[-1].text == "Hello"


def test_gemini_tool_call_only_stream_passes(monkeypatch):
    # Gemini finishes a function-call turn with finishReason STOP.
    frames = [_gemini_frame(
        {"functionCall": {"name": "get_weather", "args": {"city": "Paris"}}},
        finish_reason="STOP", usage=_GEMINI_USAGE,
    )]
    events = _gemini_events(monkeypatch, frames, tools=(_WEATHER_TOOL,))

    assert [e.type for e in events] == ["tool_call", "done"]
    assert events[0].tool_call.arguments == {"city": "Paris"}


def test_gemini_max_tokens_finish_passes(monkeypatch):
    frames = [_gemini_frame({"text": "A long answer"}), _gemini_frame({"text": " that"}, finish_reason="MAX_TOKENS")]
    assert _gemini_events(monkeypatch, frames)[-1].text == "A long answer that"


def test_gemini_safety_finish_with_partial_text_counts_as_complete(monkeypatch):
    # The server reported WHY it stopped; that is a finished generation,
    # not a dropped connection.
    frames = [_gemini_frame({"text": "Partial"}), _gemini_frame(finish_reason="SAFETY")]
    assert _gemini_events(monkeypatch, frames)[-1].text == "Partial"


def test_gemini_truncation_through_chat_stream_delivers_the_partial_then_raises(monkeypatch):
    monkeypatch.setattr(api_provider, "USE_API_MODE", True)
    monkeypatch.setattr(api_provider, "API_PROVIDER_TYPE", config.API_PROVIDER_GEMINI)
    monkeypatch.setattr(api_provider, "API_KEY", "k")
    monkeypatch.setattr(api_provider, "API_CLIENT", {"provider": "gemini"})
    monkeypatch.setitem(api_provider.API_MODELS, config.TASK_CHAT, "gemini-2.5-pro")
    monkeypatch.setattr(
        api_provider.urllib.request, "urlopen",
        lambda request, timeout=None: _FakeSSEResponse([_gemini_frame({"text": "half a sen"})]),
    )

    chunks = []
    with pytest.raises(RuntimeError, match=_TRUNCATED):
        _REAL_CHAT_STREAM(
            config.TASK_CHAT, [{"role": "user", "content": "hi"}],
            lambda delta, reset: chunks.append((delta, reset)),
        )
    assert chunks == [("half a sen", False)]
