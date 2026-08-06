"""ADR-006 stage 6.1: the Provider protocol, FakeProvider, and the Ollama port.

Layer map:
1. Protocol conformance - both implementations satisfy the runtime-checkable
   Provider protocol.
2. FakeProvider semantics - scripted events, synthesized "done", mid-stream
   error, request recording, cooperative cancellation.
3. OllamaProvider unit tests against a fake ollama.chat - event ordering,
   thinking-as-its-own-channel, <think> composition, the 3-attempt
   reasoning retry with its "reset" event, cancellation closing the live
   stream, think-kwarg/system-hint gating on TASK_CHAT, and the
   empty-vs-reasoning-only error distinction.
4. The stage's EXIT CRITERION - api_provider.chat_stream's REAL machinery
   (not the suite-wide conftest stub, which this file explicitly restores
   the real function over) streams multiple incremental deltas end to end
   through the provider seam, with only the network call faked. Before this
   stage, that path was untestable without a live Ollama server - the seam
   is what makes it testable, which is the point of the stage.
"""

from __future__ import annotations

import threading
import time

import pytest

import api_provider
import graphlink_task_config as config
from backend.providers import (
    CancelToken,
    ChatRequest,
    FakeProvider,
    OllamaProvider,
    Provider,
    ProviderEvent,
)

# Captured at import time - module-level code runs at collection, BEFORE the
# autouse conftest fixture swaps api_provider.chat_stream for its one-chunk
# stub - so this is the genuine function object, restorable per-test.
_REAL_CHAT_STREAM = api_provider.chat_stream
_REAL_CHAT = api_provider.chat


# -- fakes --------------------------------------------------------------------


class FakeOllamaStream:
    """Stands in for the generator ollama.chat(stream=True) returns: iterable
    parts plus the close() the cancellation path calls on the live HTTP
    stream."""

    def __init__(self, parts):
        self._iter = iter(parts)
        self.close_calls = 0

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._iter)

    def close(self):
        self.close_calls += 1


def _part(content="", thinking="", done=False):
    message = {"content": content}
    if thinking:
        message["thinking"] = thinking
    return {"message": message, "done": done}


class FakeOllamaChat:
    """Callable installed as ollama.chat. Streaming calls pop the next scripted
    FakeOllamaStream (one per retry attempt); non-streaming calls pop the next
    scripted response dict. Records every call's kwargs."""

    def __init__(self, streams=None, responses=None):
        self.streams = list(streams or [])
        self.responses = list(responses or [])
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return self.streams.pop(0)
        return self.responses.pop(0)


@pytest.fixture
def no_backoff(monkeypatch):
    """The retry loop's 1 s inter-attempt sleep, skipped for test speed."""
    monkeypatch.setattr(time, "sleep", lambda _s: None)


@pytest.fixture
def ollama_chat(monkeypatch):
    fake = FakeOllamaChat()
    import ollama

    monkeypatch.setattr(ollama, "chat", fake)
    return fake


# -- layer 1: protocol conformance -------------------------------------------


def test_both_implementations_satisfy_the_provider_protocol():
    assert isinstance(FakeProvider(), Provider)
    assert isinstance(OllamaProvider(model="m"), Provider)


def test_ollama_capabilities_derive_from_the_think_kwarg_family_without_network():
    # gpt-oss and qwen3 families take a think kwarg -> reasoning-capable;
    # an unknown family does not. Pure string checks - no ollama.show probe.
    assert OllamaProvider(model="gpt-oss:20b").capabilities.reasoning is True
    assert OllamaProvider(model="qwen3:8b").capabilities.reasoning is True
    assert OllamaProvider(model="llava:13b").capabilities.reasoning is False
    assert OllamaProvider(model="llava:13b").capabilities.streaming is True


# -- layer 2: FakeProvider ----------------------------------------------------


def test_fake_provider_yields_scripted_events_and_synthesizes_done():
    fake = FakeProvider([ProviderEvent("text", "a"), ProviderEvent("text", "b")])
    events = list(fake.stream(ChatRequest(task="t", messages=[]), CancelToken()))
    assert [e.type for e in events] == ["text", "text", "done"]
    assert events[-1].text == "ab"
    assert len(fake.requests) == 1


def test_fake_provider_raises_its_scripted_error_after_the_events():
    fake = FakeProvider([ProviderEvent("text", "partial")], error=RuntimeError("boom"))
    events = []
    with pytest.raises(RuntimeError, match="boom"):
        for event in fake.stream(ChatRequest(task="t", messages=[]), CancelToken()):
            events.append(event)
    assert [e.type for e in events] == ["text"]  # partial output was delivered first


def test_fake_provider_honors_cancellation_between_events():
    fake = FakeProvider([ProviderEvent("text", "a"), ProviderEvent("text", "b")])
    event = threading.Event()
    token = CancelToken(event)
    stream = fake.stream(ChatRequest(task="t", messages=[]), token)
    assert next(stream).text == "a"
    event.set()
    with pytest.raises(api_provider.RequestCancelledError):
        next(stream)


# -- layer 3: OllamaProvider ---------------------------------------------------


def test_stream_yields_incremental_text_events_and_a_composed_done(ollama_chat):
    ollama_chat.streams = [FakeOllamaStream([
        _part(content="Hel"),
        _part(content="lo "),
        _part(content="world", done=True),
    ])]
    provider = OllamaProvider(model="llava:13b")
    events = list(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]),
        CancelToken(),
    ))
    assert [e.type for e in events] == ["text", "text", "text", "done"]
    assert [e.text for e in events[:3]] == ["Hel", "lo ", "world"]
    assert events[-1].text == "Hello world"


def test_thinking_deltas_are_their_own_event_channel_and_compose_into_the_think_block(ollama_chat):
    ollama_chat.streams = [FakeOllamaStream([
        _part(thinking="pondering..."),
        _part(content="Answer.", done=True),
    ])]
    provider = OllamaProvider(model="qwen3:8b")
    events = list(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]),
        CancelToken(),
    ))
    assert [e.type for e in events] == ["reasoning", "text", "done"]
    final = events[-1].text
    # The exact shape backend/response_parsing.py depends on.
    assert final.startswith("<think>")
    assert "pondering..." in final
    assert final.endswith("Answer.")


def test_reasoning_without_answer_retries_with_a_reset_event(ollama_chat, no_backoff):
    ollama_chat.streams = [
        FakeOllamaStream([_part(thinking="only thoughts", done=True)]),   # attempt 1: no answer
        FakeOllamaStream([_part(content="Real answer.", done=True)]),     # attempt 2: succeeds
    ]
    provider = OllamaProvider(model="qwen3:8b")
    events = list(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]),
        CancelToken(),
    ))
    types = [e.type for e in events]
    assert types == ["reasoning", "reset", "text", "done"]
    assert events[-1].text.endswith("Real answer.")
    assert len(ollama_chat.calls) == 2


def test_three_reasoning_only_attempts_exhaust_into_the_exact_legacy_error(ollama_chat, no_backoff):
    ollama_chat.streams = [
        FakeOllamaStream([_part(thinking="t", done=True)]) for _ in range(3)
    ]
    provider = OllamaProvider(model="qwen3:8b")
    with pytest.raises(RuntimeError, match="reasoning but no final answer after 3 attempts"):
        list(provider.stream(
            ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]),
            CancelToken(),
        ))
    assert len(ollama_chat.calls) == 3


def test_an_empty_response_is_a_distinct_error_not_a_retry(ollama_chat):
    ollama_chat.streams = [FakeOllamaStream([_part(done=True)])]
    provider = OllamaProvider(model="llava:13b")
    with pytest.raises(RuntimeError, match="empty response"):
        list(provider.stream(
            ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]),
            CancelToken(),
        ))
    assert len(ollama_chat.calls) == 1  # no retry - empty is not reasoning-without-answer


def test_cancellation_mid_stream_closes_the_live_stream_and_raises(ollama_chat):
    cancel_event = threading.Event()

    class CancellingStream(FakeOllamaStream):
        def __next__(self):
            part = super().__next__()
            cancel_event.set()  # cancel lands after the first part is delivered
            return part

    live = CancellingStream([_part(content="par"), _part(content="tial", done=True)])
    ollama_chat.streams = [live]
    provider = OllamaProvider(model="llava:13b")
    stream = provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]),
        CancelToken(cancel_event),
    )
    with pytest.raises(api_provider.RequestCancelledError):
        list(stream)
    assert live.close_calls >= 1  # the live HTTP stream was actively closed


def test_think_kwarg_and_hint_apply_only_for_the_chat_task(ollama_chat):
    ollama_chat.streams = [
        FakeOllamaStream([_part(content="a", done=True)]),
        FakeOllamaStream([_part(content="b", done=True)]),
    ]
    provider = OllamaProvider(model="gpt-oss:20b", reasoning_level="high")

    list(provider.stream(ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "x"}]), CancelToken()))
    assert ollama_chat.calls[0]["think"] == "high"  # gpt-oss family: string level

    list(provider.stream(ChatRequest(task=config.TASK_TITLE, messages=[{"role": "user", "content": "x"}]), CancelToken()))
    assert "think" not in ollama_chat.calls[1]  # non-chat tasks never reason


def test_complete_returns_the_same_composed_content_as_the_streamed_path(ollama_chat):
    ollama_chat.responses = [
        {"message": {"content": "Answer.", "thinking": "pondering..."}}
    ]
    provider = OllamaProvider(model="qwen3:8b")
    content = provider.complete(
        ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]),
        CancelToken(),
    )
    assert content.startswith("<think>")
    assert "pondering..." in content
    assert content.endswith("Answer.")
    assert "stream" not in ollama_chat.calls[0]  # transitional non-streaming call


# -- layer 4: the stage exit criterion ----------------------------------------


@pytest.fixture
def ollama_mode(monkeypatch):
    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_OLLAMA)
    monkeypatch.setattr(api_provider, "OLLAMA_REASONING_LEVEL", "off")
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, "fake-model:1b")


def test_the_real_chat_stream_streams_multiple_incremental_deltas_through_the_seam(
    monkeypatch, ollama_mode, ollama_chat
):
    """THE 6.1 EXIT CRITERION: api_provider.chat_stream's real machinery (the
    conftest stub explicitly swapped back out for the genuine function) routes
    through OllamaProvider and delivers REAL incremental streaming - multiple
    deltas in order, not the stub's single synthetic chunk."""
    monkeypatch.setattr(api_provider, "chat_stream", _REAL_CHAT_STREAM)
    ollama_chat.streams = [FakeOllamaStream([
        _part(content="one "),
        _part(content="two "),
        _part(content="three", done=True),
    ])]

    chunks: list[tuple[str, bool]] = []
    response = api_provider.chat_stream(
        config.TASK_CHAT,
        [{"role": "user", "content": "count"}],
        lambda delta, reset: chunks.append((delta, reset)),
    )

    assert chunks == [("one ", False), ("two ", False), ("three", False)]
    assert response == {"message": {"content": "one two three", "role": "assistant"}}


def test_the_real_chat_stream_forwards_the_retry_reset_to_on_chunk(
    monkeypatch, ollama_mode, ollama_chat, no_backoff
):
    monkeypatch.setattr(api_provider, "chat_stream", _REAL_CHAT_STREAM)
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, "qwen3:8b")
    ollama_chat.streams = [
        FakeOllamaStream([_part(thinking="hmm", done=True)]),
        FakeOllamaStream([_part(content="Second try.", done=True)]),
    ]

    chunks: list[tuple[str, bool]] = []
    response = api_provider.chat_stream(
        config.TASK_CHAT,
        [{"role": "user", "content": "hi"}],
        lambda delta, reset: chunks.append((delta, reset)),
    )

    # The reset frame tells the caller to discard attempt 1's partial text;
    # thinking deltas were never forwarded to on_chunk (only the reset was).
    assert chunks == [("", True), ("Second try.", False)]
    assert response["message"]["content"].endswith("Second try.")


def test_the_real_chat_via_the_seam_matches_the_legacy_return_shape(ollama_mode, ollama_chat):
    ollama_chat.responses = [{"message": {"content": "Plain answer."}}]
    response = _REAL_CHAT(config.TASK_CHAT, [{"role": "user", "content": "hi"}])
    assert response == {"message": {"content": "Plain answer.", "role": "assistant"}}


def test_chat_and_chat_stream_actually_route_through_the_provider_seam(
    monkeypatch, ollama_mode, ollama_chat
):
    """Not just behavior parity - the SEAM itself is wired. A faithful inline
    re-implementation of the Ollama branch would pass every behavioral test in
    this file while silently abandoning the protocol; this catches that by
    proving both entry points invoke OllamaProvider."""
    from backend.providers import ollama_provider as op_module

    calls = {"stream": 0, "complete": 0}
    real_stream, real_complete = op_module.OllamaProvider.stream, op_module.OllamaProvider.complete

    def counting_stream(self, request, cancel):
        calls["stream"] += 1
        return real_stream(self, request, cancel)

    def counting_complete(self, request, cancel):
        calls["complete"] += 1
        return real_complete(self, request, cancel)

    monkeypatch.setattr(op_module.OllamaProvider, "stream", counting_stream)
    monkeypatch.setattr(op_module.OllamaProvider, "complete", counting_complete)
    monkeypatch.setattr(api_provider, "chat_stream", _REAL_CHAT_STREAM)

    ollama_chat.streams = [FakeOllamaStream([_part(content="s", done=True)])]
    ollama_chat.responses = [{"message": {"content": "c"}}]

    api_provider.chat_stream(config.TASK_CHAT, [{"role": "user", "content": "x"}], lambda d, r: None)
    _REAL_CHAT(config.TASK_CHAT, [{"role": "user", "content": "x"}])

    assert calls == {"stream": 1, "complete": 1}


def test_cancellation_through_the_real_chat_stream_is_the_untranslated_sentinel(
    monkeypatch, ollama_mode, ollama_chat
):
    """RequestCancelledError must escape _translate_chat_exception untouched -
    backend/agents.py catches exactly this type to end a run quietly."""
    monkeypatch.setattr(api_provider, "chat_stream", _REAL_CHAT_STREAM)
    cancel_event = threading.Event()
    cancel_event.set()  # cancelled before the first chunk
    ollama_chat.streams = [FakeOllamaStream([_part(content="never", done=True)])]

    with pytest.raises(api_provider.RequestCancelledError):
        api_provider.chat_stream(
            config.TASK_CHAT,
            [{"role": "user", "content": "hi"}],
            lambda delta, reset: None,
            cancellation_event=cancel_event,
        )
