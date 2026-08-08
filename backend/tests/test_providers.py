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
4. The 6.1 EXIT CRITERION - api_provider.chat_stream's REAL machinery
   (not the suite-wide conftest stub, which this file explicitly restores
   the real function over) streams multiple incremental deltas end to end
   through the provider seam, with only the network call faked. Before this
   stage, that path was untestable without a live Ollama server - the seam
   is what makes it testable, which is the point of the stage.
5. Stage 6.3 - the four remaining providers (OpenAI/Anthropic/Gemini/
   llama.cpp): the pinned per-provider capability matrix (that stage's exit
   criterion as data), the C4 multimodal conversion (image_bytes ->
   image_url data URI, audio_file -> input_audio), per-provider port
   fidelity against faked SDK clients/HTTP helpers, and seam-wiring proof
   that chat()'s API branches construct and invoke the provider classes.
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
    # Review finding: endswith alone would let attempt 1's discarded output
    # leak into the final text unnoticed - assert the discard actually held.
    assert "only thoughts" not in events[-1].text
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


def test_bool_reasoning_models_get_the_budget_hint_prepended_as_a_system_message(ollama_chat):
    """Review finding: this invariant was claimed in the module doc but never
    asserted. qwen3 at a non-off level must get reasoning_budget_hint()'s text
    prepended as a leading system message; gpt-oss (string-think family) must
    NOT - it steers via the think kwarg alone."""
    ollama_chat.streams = [
        FakeOllamaStream([_part(content="a", done=True)]),
        FakeOllamaStream([_part(content="b", done=True)]),
    ]

    list(OllamaProvider(model="qwen3:8b", reasoning_level="high").stream(
        ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "x"}]),
        CancelToken(),
    ))
    sent = ollama_chat.calls[0]["messages"]
    assert sent[0]["role"] == "system"
    assert api_provider.reasoning_budget_hint("high") in sent[0]["content"]
    assert ollama_chat.calls[0]["think"] is True  # bool family

    list(OllamaProvider(model="gpt-oss:20b", reasoning_level="high").stream(
        ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "x"}]),
        CancelToken(),
    ))
    sent = ollama_chat.calls[1]["messages"]
    assert all(m["role"] != "system" for m in sent)  # no hint for the string family


def test_extra_kwargs_pass_through_and_cancellation_event_is_stripped(ollama_chat):
    """Review finding: the passthrough surface (e.g. the chart agent's format
    kwarg) and the cancellation_event strip were untested in BOTH paths."""
    ollama_chat.streams = [FakeOllamaStream([_part(content="a", done=True)])]
    ollama_chat.responses = [{"message": {"content": "b"}}]
    provider = OllamaProvider(model="llava:13b")
    sentinel_event = threading.Event()
    request = ChatRequest(
        task=config.TASK_TITLE,
        messages=[{"role": "user", "content": "x"}],
        extra_kwargs={"format": "json", "cancellation_event": sentinel_event},
    )

    list(provider.stream(request, CancelToken()))
    provider.complete(request, CancelToken())

    for call in ollama_chat.calls:
        assert call["format"] == "json"
        assert "cancellation_event" not in call


def test_image_bytes_parts_flow_into_ollamas_images_field(ollama_chat):
    """Review finding: the media-flattening invariant (image/audio parts land
    in ollama's `images` field via _prepare_ollama_messages) was claimed but
    never exercised through the provider path."""
    ollama_chat.streams = [FakeOllamaStream([_part(content="seen", done=True)])]
    provider = OllamaProvider(model="llava:13b")
    list(provider.stream(
        ChatRequest(
            task=config.TASK_CHAT,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is this"},
                    {"type": "image_bytes", "data": b"\x89PNG-fake"},
                ],
            }],
        ),
        CancelToken(),
    ))
    sent = ollama_chat.calls[0]["messages"]
    assert sent[-1]["images"] == [b"\x89PNG-fake"]
    assert sent[-1]["content"] == "what is this"


def test_complete_honors_cancellation(ollama_chat):
    """Review finding: only stream()'s cancellation was tested."""
    cancel_event = threading.Event()
    cancel_event.set()
    provider = OllamaProvider(model="llava:13b")
    with pytest.raises(api_provider.RequestCancelledError):
        provider.complete(
            ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "x"}]),
            CancelToken(cancel_event),
        )
    assert ollama_chat.calls == []  # cancelled before any network call


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
    assert response == {"message": {"content": "one two three", "role": "assistant"}, "usage": None}


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
    assert response == {"message": {"content": "Plain answer.", "role": "assistant"}, "usage": None}


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


def test_a_fake_provider_can_substitute_for_ollama_at_the_real_chat_stream_seam(
    monkeypatch, ollama_mode
):
    """Review finding (HIGH): the whole design promises the seam is
    provider-agnostic, but nothing proved a protocol-conforming double can
    stand in for OllamaProvider under the REAL chat_stream. Here the seam's
    provider construction is swapped for a FakeProvider factory and the real
    adapter runs end to end against it - scripted deltas reach on_chunk, the
    scripted done becomes the return value, no ollama.chat involved at all."""
    from backend.providers import ollama_provider as op_module

    fake = FakeProvider([
        ProviderEvent("text", "from "),
        ProviderEvent("text", "the "),
        ProviderEvent("text", "fake"),
    ])

    class FakeFactory:
        def __init__(self, **_kwargs):
            self.capabilities = fake.capabilities

        def stream(self, request, cancel):
            return fake.stream(request, cancel)

    monkeypatch.setattr(op_module, "OllamaProvider", FakeFactory)
    monkeypatch.setattr(api_provider, "chat_stream", _REAL_CHAT_STREAM)

    chunks: list[tuple[str, bool]] = []
    response = api_provider.chat_stream(
        config.TASK_CHAT,
        [{"role": "user", "content": "hi"}],
        lambda delta, reset: chunks.append((delta, reset)),
    )

    assert chunks == [("from ", False), ("the ", False), ("fake", False)]
    assert response == {"message": {"content": "from the fake", "role": "assistant"}, "usage": None}
    assert len(fake.requests) == 1


def test_api_provider_never_imports_the_providers_package_at_module_level():
    """Review finding: the circular-import structure is sound ONLY while
    api_provider keeps its backend.providers imports function-local (the
    providers package imports api_provider's helpers at ITS module level).
    Pin the invariant so a future convenience refactor that hoists the import
    to the top of api_provider.py fails here instead of at app boot."""
    import ast
    from pathlib import Path

    source = (Path(api_provider.__file__)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders = [
        node.lineno
        for node in tree.body  # module level only - function-local imports are the sanctioned form
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and "backend.providers" in ast.dump(node)
    ]
    assert not offenders, (
        "api_provider.py imports backend.providers at module level (lines "
        f"{offenders}) - that direction must stay function-local; the providers "
        "package imports api_provider's helpers at its own module level, so a "
        "top-level import here is a genuine import cycle."
    )


# -- stage 6.3: the four remaining providers ---------------------------------


def _fake_openai_client(response_text="ok"):
    import types

    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=response_text))]
        )

    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )
    return client, captured


def test_the_capability_matrix_is_pinned_per_provider():
    """The 6.3 exit criterion's matrix, as data: what each provider+model
    pair can actually do. A capability flipping here must be a deliberate
    stage (6.5b flipped streaming True for the four non-Ollama providers),
    never a drive-by."""
    from backend.providers import (
        AnthropicProvider,
        GeminiProvider,
        LlamaCppProvider,
        OpenAIProvider,
    )

    matrix = {
        "ollama": OllamaProvider(model="qwen3:8b").capabilities,
        "openai": OpenAIProvider(client=None, model="gpt-5").capabilities,
        "anthropic": AnthropicProvider(client=None, api_key="k", model="claude-opus-5").capabilities,
        "gemini": GeminiProvider(api_key="k", model="gemini-2.5-pro").capabilities,
        "llama_cpp": LlamaCppProvider(settings={"chat_model_path": "m.gguf"}).capabilities,
    }
    expected = {
        #            streaming, reasoning, vision, audio, image_gen
        "ollama":    (True,  True,  True,  True,  False),  # media rides the images field; audio model-gated at request time
        "openai":    (True,  True,  True,  True,  False),  # C4: vision+audio real; image_gen probed from the client (None here)
        "anthropic": (True,  True,  True,  False, False),
        "gemini":    (True,  True,  True,  True,  True),
        "llama_cpp": (True,  False, False, False, False),
    }
    actual = {
        name: (c.streaming, c.reasoning, c.vision, c.audio, c.image_generation)
        for name, c in matrix.items()
    }
    assert actual == expected

    # OpenAI's image_generation is client-derived, not asserted: an endpoint
    # actually exposing images.generate reports True.
    from backend.providers import OpenAIProvider as _OP

    client_with_images, _ = _fake_openai_client()
    import types as _types

    client_with_images.images = _types.SimpleNamespace(generate=lambda **kw: None)
    assert _OP(client=client_with_images, model="gpt-5").capabilities.image_generation is True


def test_openai_c4_image_bytes_become_a_data_uri_image_url_part():
    from backend.providers.openai_provider import prepare_openai_messages

    png = b"\x89PNG\r\n\x1a\nfake"
    jpg = b"\xff\xd8\xfffake"
    prepared = prepare_openai_messages([{
        "role": "user",
        "content": [
            {"type": "text", "text": "what are these"},
            {"type": "image_bytes", "data": png},
            {"type": "image_bytes", "data": jpg},
        ],
    }])
    parts = prepared[0]["content"]
    assert parts[0] == {"type": "text", "text": "what are these"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert parts[2]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_openai_c4_audio_file_becomes_input_audio_and_odd_containers_error(monkeypatch, tmp_path):
    from backend.providers import openai_provider as op

    monkeypatch.setattr(op, "_read_attachment_bytes", lambda path, kind: b"fake-mp3-bytes")
    prepared = op.prepare_openai_messages([{
        "role": "user",
        "content": [{"type": "audio_file", "path": str(tmp_path / "clip.mp3")}],
    }])
    part = prepared[0]["content"][0]
    assert part["type"] == "input_audio"
    assert part["input_audio"]["format"] == "mp3"
    import base64
    assert base64.b64decode(part["input_audio"]["data"]) == b"fake-mp3-bytes"

    with pytest.raises(RuntimeError, match="WAV and MP3"):
        op.prepare_openai_messages([{
            "role": "user",
            "content": [{"type": "audio_file", "path": str(tmp_path / "clip.m4a")}],
        }])


def test_openai_plain_string_messages_pass_through_byte_identical():
    from backend.providers.openai_provider import prepare_openai_messages

    messages = [{"role": "user", "content": "just text"}]
    assert prepare_openai_messages(messages)[0] is messages[0]


def test_openai_complete_sends_model_prepared_messages_and_gates_reasoning_on_task():
    from backend.providers import CancelToken as CT, ChatRequest as CR, OpenAIProvider

    client, captured = _fake_openai_client("answer")
    provider = OpenAIProvider(client=client, model="gpt-5", reasoning_level="high")

    content = provider.complete(
        CR(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]), CT()
    )
    assert content == "answer"
    assert captured["model"] == "gpt-5"
    assert captured["messages"] == [{"role": "user", "content": "hi"}]
    chat_keys = set(captured) - {"model", "messages"}
    assert chat_keys == set(api_provider.openai_reasoning_kwargs("gpt-5", "high"))

    captured.clear()
    provider.complete(
        CR(task=config.TASK_TITLE, messages=[{"role": "user", "content": "hi"}]), CT()
    )
    assert set(captured) == {"model", "messages"}  # non-chat tasks never reason


def test_anthropic_sdk_path_and_rest_fallback_both_route_through_the_provider(monkeypatch):
    import types

    from backend.providers import AnthropicProvider, CancelToken as CT, ChatRequest as CR

    # SDK-shaped client: messages.create is callable.
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return {"content": [{"type": "text", "text": "sdk answer"}]}

    sdk_client = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
    provider = AnthropicProvider(client=sdk_client, api_key="k", model="claude-opus-5")
    content = provider.complete(
        CR(task=config.TASK_CHAT, messages=[
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ]),
        CT(),
    )
    assert content == "sdk answer"
    assert captured["model"] == "claude-opus-5"
    # ADR-006 stage 6.7: system goes out as a cache_control block list.
    assert captured["system"] == [
        {"type": "text", "text": "be brief", "cache_control": {"type": "ephemeral"}}
    ]

    # Dict-sentinel client (SDK not installed): falls back to the REST helper.
    rest_calls = {}

    def fake_post(url, body, **kwargs):
        rest_calls["url"] = url
        rest_calls["body"] = body
        return {"content": [{"type": "text", "text": "rest answer"}]}

    monkeypatch.setattr("backend.providers.anthropic_provider._anthropic_post_json", fake_post)
    provider = AnthropicProvider(
        client={"provider": "anthropic", "transport": "rest"}, api_key="k", model="claude-opus-5"
    )
    content = provider.complete(
        CR(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]), CT()
    )
    assert content == "rest answer"
    assert rest_calls["url"].endswith("/v1/messages")
    assert rest_calls["body"]["model"] == "claude-opus-5"


def test_anthropic_system_prompt_carries_cache_control_on_the_rest_transport(monkeypatch):
    # ADR-006 stage 6.7 exit criterion (request shape): when a system prompt
    # is present, BOTH transports send it as a content-block list carrying
    # cache_control - the SDK-side assertions live in the two tests above
    # (blocking and streaming); this one pins the REST fallback on both the
    # blocking and streaming paths, plus the no-system-prompt case.
    from backend.providers import AnthropicProvider, CancelToken as CT, ChatRequest as CR

    expected_system = [
        {"type": "text", "text": "be brief", "cache_control": {"type": "ephemeral"}}
    ]
    messages_with_system = [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
    ]

    # Blocking REST path.
    rest_calls = {}

    def fake_post(url, body, **kwargs):
        rest_calls["body"] = body
        return {"content": [{"type": "text", "text": "rest answer"}]}

    monkeypatch.setattr("backend.providers.anthropic_provider._anthropic_post_json", fake_post)
    provider = AnthropicProvider(
        client={"provider": "anthropic", "transport": "rest"}, api_key="k", model="claude-opus-5"
    )
    provider.complete(CR(task=config.TASK_CHAT, messages=messages_with_system), CT())
    assert rest_calls["body"]["system"] == expected_system

    # Streaming REST path.
    sse_calls = {}

    def fake_stream_sse(url, body, timeout=180, cancel_event=None, api_key=None):
        sse_calls["body"] = body
        yield from _anthropic_raw_events(with_thinking=False)

    monkeypatch.setattr(
        "backend.providers.anthropic_provider._anthropic_stream_sse", fake_stream_sse
    )
    list(provider.stream(CR(task=config.TASK_CHAT, messages=messages_with_system), CT()))
    assert sse_calls["body"]["system"] == expected_system

    # No system message: the key is absent entirely, exactly as before.
    provider.complete(CR(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]), CT())
    assert "system" not in rest_calls["body"]


def test_gemini_deletes_uploaded_files_even_when_the_generation_call_fails(monkeypatch):
    from backend.providers import CancelToken as CT, ChatRequest as CR, GeminiProvider

    deleted = []
    monkeypatch.setattr(
        "backend.providers.gemini_provider._prepare_gemini_contents",
        lambda messages, cancel_event=None, api_key=None: (None, [{"parts": [{"text": "hi"}]}], ["files/abc"]),
    )
    monkeypatch.setattr(
        "backend.providers.gemini_provider._gemini_delete_file",
        lambda name, api_key=None: deleted.append(name),
    )

    def failing_post(url, body, **kwargs):
        raise RuntimeError("503 from Gemini")

    monkeypatch.setattr("backend.providers.gemini_provider._gemini_post_json", failing_post)
    provider = GeminiProvider(api_key="k", model="gemini-2.5-pro")
    with pytest.raises(RuntimeError, match="503"):
        provider.complete(CR(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]), CT())
    assert deleted == ["files/abc"]  # the load-bearing finally survived the port


def test_llama_cpp_rejects_media_up_front_with_the_actionable_message():
    from backend.providers import CancelToken as CT, ChatRequest as CR, LlamaCppProvider

    provider = LlamaCppProvider(settings={"chat_model_path": "m.gguf"})
    with pytest.raises(RuntimeError, match="Use Ollama or Gemini"):
        provider.complete(
            CR(task=config.TASK_CHAT, messages=[{
                "role": "user",
                "content": [{"type": "image_bytes", "data": b"\x89PNG"}],
            }]),
            CT(),
        )


def test_chat_routes_every_api_provider_through_its_provider_class(monkeypatch):
    """Seam-wiring for the three API-mode branches - the same not-just-
    behavior-parity proof the Ollama port got."""
    import types

    calls = []
    for module_name, class_name in [
        ("backend.providers.openai_provider", "OpenAIProvider"),
        ("backend.providers.anthropic_provider", "AnthropicProvider"),
        ("backend.providers.gemini_provider", "GeminiProvider"),
    ]:
        module = __import__(module_name, fromlist=[class_name])
        real = getattr(module, class_name)

        def make_counting(real_cls, label):
            class Counting(real_cls):
                def complete(self, request, cancel):
                    calls.append(label)
                    return f"{label} answer"
            return Counting

        monkeypatch.setattr(module, class_name, make_counting(real, class_name))

    monkeypatch.setattr(api_provider, "USE_API_MODE", True)
    monkeypatch.setattr(api_provider, "API_KEY", "k")
    monkeypatch.setattr(api_provider, "API_CLIENT", _fake_openai_client()[0])
    monkeypatch.setitem(api_provider.API_MODELS, config.TASK_CHAT, "some-model")

    for provider_type, label in [
        (config.API_PROVIDER_OPENAI, "OpenAIProvider"),
        (config.API_PROVIDER_ANTHROPIC, "AnthropicProvider"),
        (config.API_PROVIDER_GEMINI, "GeminiProvider"),
    ]:
        monkeypatch.setattr(api_provider, "API_PROVIDER_TYPE", provider_type)
        response = _REAL_CHAT(config.TASK_CHAT, [{"role": "user", "content": "hi"}])
        # ADR-006 stage 6.8 scope note: the API-mode BLOCKING branches
        # deliberately do not surface usage (the chat UI streams everywhere
        # since 6.5b) - no "usage" key here, unlike the local branches.
        assert response == {"message": {"content": f"{label} answer", "role": "assistant"}}
    assert calls == ["OpenAIProvider", "AnthropicProvider", "GeminiProvider"]


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


@pytest.fixture
def openai_api_mode(monkeypatch):
    """6.3 review fix: API-mode state pointing at the OpenAI branch, client
    installable per-test. Mirrors the ollama_mode fixture pattern."""
    monkeypatch.setattr(api_provider, "USE_API_MODE", True)
    monkeypatch.setattr(api_provider, "API_PROVIDER_TYPE", config.API_PROVIDER_OPENAI)
    monkeypatch.setattr(api_provider, "API_KEY", "k")
    monkeypatch.setitem(api_provider.API_MODELS, config.TASK_CHAT, "some-model")

    def install_client(client):
        monkeypatch.setattr(api_provider, "API_CLIENT", client)

    return install_client


def test_openai_complete_routes_multimodal_messages_through_the_c4_conversion():
    """6.3 review fix: the pure-function tests above prove prepare_openai_messages
    works; nothing proved complete() actually CALLS it. A port that passed
    request.messages raw to the SDK (the exact pre-C4 bug) would have passed
    every prior test - here the fake client's captured messages must contain
    the converted image_url data-URI part."""
    import base64

    from backend.providers import CancelToken as CT, ChatRequest as CR, OpenAIProvider

    client, captured = _fake_openai_client("seen")
    provider = OpenAIProvider(client=client, model="gpt-5")
    png = b"\x89PNG\r\n\x1a\nfake"

    content = provider.complete(
        CR(task=config.TASK_CHAT, messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "what is this"},
                {"type": "image_bytes", "data": png},
            ],
        }]),
        CT(),
    )

    assert content == "seen"
    sent_parts = captured["messages"][0]["content"]
    assert sent_parts[0] == {"type": "text", "text": "what is this"}
    assert sent_parts[1]["type"] == "image_url"
    expected_uri = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    assert sent_parts[1]["image_url"]["url"] == expected_uri


def test_llama_cpp_complete_happy_path_wires_prep_client_kwargs_and_extraction(monkeypatch):
    """6.3 review fix: the llama.cpp port only had its media-rejection guard
    tested - the happy path (prep -> cached-client lookup -> kwargs filter ->
    create_chat_completion -> text extraction) never ran. All api_provider
    helpers are faked at the provider module's namespace, so this pins the
    provider's ORCHESTRATION of them, not their internals."""
    from backend.providers import CancelToken as CT, ChatRequest as CR, LlamaCppProvider
    from backend.providers import llama_cpp_provider as lp

    prepared_marker = [{"role": "user", "content": "prepared"}]
    captured = {}

    class FakeLlamaClient:
        def create_chat_completion(self, messages=None, **kwargs):
            captured["messages"] = messages
            captured["kwargs"] = kwargs
            return {"choices": [{"message": {"content": "llama answer"}}]}

    fake_client = FakeLlamaClient()
    settings = {"chat_model_path": "m.gguf"}
    monkeypatch.setattr(lp, "_prepare_llama_cpp_messages", lambda messages, task, s: prepared_marker)
    monkeypatch.setattr(lp, "_get_llama_cpp_client", lambda task, s: fake_client)
    monkeypatch.setattr(lp, "_prepare_llama_cpp_kwargs", lambda kwargs, s: dict(kwargs))
    monkeypatch.setattr(lp, "_filter_kwargs_for_callable", lambda fn, kwargs: dict(kwargs))
    monkeypatch.setattr(
        lp, "_extract_llama_cpp_text",
        lambda response: response["choices"][0]["message"]["content"],
    )

    provider = LlamaCppProvider(settings=settings)
    content = provider.complete(
        CR(
            task=config.TASK_CHAT,
            messages=[{"role": "user", "content": "hi"}],
            extra_kwargs={"temperature": 0.5, "cancellation_event": threading.Event()},
        ),
        CT(),
    )

    assert content == "llama answer"
    assert captured["messages"] is prepared_marker  # prepared, not raw, messages reached the client
    assert captured["kwargs"] == {"temperature": 0.5}  # cancellation_event stripped


def test_chat_routes_the_llama_cpp_local_branch_through_its_provider_class(monkeypatch):
    """6.3 review fix: the seam-wiring proof covered the three API branches
    but not the fourth ported branch - chat()'s llama.cpp local path. Same
    counting-subclass pattern; also pins that the provider is constructed
    from the request snapshot's settings dict."""
    from backend.providers import llama_cpp_provider as lp

    seen = {"complete": 0}
    real = lp.LlamaCppProvider

    class Counting(real):
        def __init__(self, **kwargs):
            seen["init"] = dict(kwargs)
            super().__init__(**kwargs)

        def complete(self, request, cancel):
            seen["complete"] += 1
            return "LlamaCppProvider answer"

    monkeypatch.setattr(lp, "LlamaCppProvider", Counting)
    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_LLAMACPP)
    settings = {"chat_model_path": "m.gguf", "reasoning_level": "off"}
    monkeypatch.setattr(api_provider, "LLAMA_CPP_SETTINGS", settings)

    response = _REAL_CHAT(config.TASK_CHAT, [{"role": "user", "content": "hi"}])

    assert response == {"message": {"content": "LlamaCppProvider answer", "role": "assistant"}, "usage": None}
    assert seen["complete"] == 1
    assert seen["init"] == {"settings": settings}  # the snapshot's dict copy, values intact


def test_a_preset_cancellation_event_escapes_chat_untranslated_in_api_mode(openai_api_mode):
    """6.3 review fix: the cancellation sentinel was only proven through the
    Ollama streaming path. chat() checks the event BEFORE dispatching to any
    API branch, and _translate_chat_exception must re-raise the sentinel
    untouched - so the fake client must never be reached."""
    client, captured = _fake_openai_client()
    openai_api_mode(client)
    cancel_event = threading.Event()
    cancel_event.set()  # cancelled before dispatch

    with pytest.raises(api_provider.RequestCancelledError):
        _REAL_CHAT(
            config.TASK_CHAT,
            [{"role": "user", "content": "hi"}],
            cancellation_event=cancel_event,
        )
    assert captured == {}  # the provider/client was never invoked


def test_connection_refused_in_api_mode_surfaces_the_friendly_endpoint_message(openai_api_mode):
    """6.3 review fix: error translation was untested through the ported API
    branches. A raw connection-refused from the OpenAI-compatible client must
    surface as _translate_chat_exception's actionable Base-URL message (with
    the raw detail appended), not as the raw exception text."""
    import types

    def refusing_create(**kwargs):
        raise Exception("connection refused by endpoint")

    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=refusing_create))
    )
    openai_api_mode(client)

    with pytest.raises(ConnectionError) as excinfo:
        _REAL_CHAT(config.TASK_CHAT, [{"role": "user", "content": "hi"}])

    message = str(excinfo.value)
    assert message.startswith(
        "Failed to connect to the API endpoint. "
        "Please verify your Base URL in settings and your network connection."
    )
    assert "Details: connection refused by endpoint" in message  # raw cause kept, as detail only


def test_translated_provider_failure_is_recorded_in_diagnostics(openai_api_mode):
    """ADR-016 stage 16.3: _translate_chat_exception is the one choke point
    both chat() and chat_stream() funnel every non-cancel failure through -
    prove a real translated failure actually reaches
    backend.diagnostics.record_provider_error, not just that the friendly
    message comes out right (already pinned above)."""
    import types

    from backend.diagnostics import provider_errors, reset_provider_errors

    reset_provider_errors()
    try:
        def refusing_create(**kwargs):
            raise Exception("connection refused by endpoint")

        client = types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=refusing_create))
        )
        openai_api_mode(client)

        with pytest.raises(ConnectionError):
            _REAL_CHAT(config.TASK_CHAT, [{"role": "user", "content": "hi"}])

        errors = provider_errors()
        assert len(errors) == 1
        assert errors[0]["provider"] == config.API_PROVIDER_OPENAI
        assert errors[0]["message"] == "connection refused by endpoint"
    finally:
        reset_provider_errors()


def test_cancelled_requests_are_not_recorded_as_provider_errors(openai_api_mode):
    """The RequestCancelledError sentinel re-raises before the diagnostics
    recording line - a user-initiated cancel must never show up next to real
    provider failures in the diagnostics panel."""
    from backend.diagnostics import provider_errors, reset_provider_errors

    reset_provider_errors()
    try:
        client, _ = _fake_openai_client()
        openai_api_mode(client)
        cancel_event = threading.Event()
        cancel_event.set()

        with pytest.raises(api_provider.RequestCancelledError):
            _REAL_CHAT(
                config.TASK_CHAT,
                [{"role": "user", "content": "hi"}],
                cancellation_event=cancel_event,
            )

        assert provider_errors() == []
    finally:
        reset_provider_errors()


def test_all_four_new_providers_satisfy_the_protocol():
    """6.3: protocol conformance for the non-Ollama four. (The transitional
    stream()-wraps-complete() single-"done" assertion that used to live here
    died with 6.5b - real per-provider streaming is pinned in the dedicated
    6.5b section below.)"""
    from backend.providers import (
        AnthropicProvider,
        GeminiProvider,
        LlamaCppProvider,
        OpenAIProvider,
    )

    assert isinstance(OpenAIProvider(client=None, model="gpt-5"), Provider)
    assert isinstance(AnthropicProvider(client=None, api_key="k", model="claude-opus-5"), Provider)
    assert isinstance(GeminiProvider(api_key="k", model="gemini-2.5-pro"), Provider)
    assert isinstance(LlamaCppProvider(settings={"chat_model_path": "m.gguf"}), Provider)


# -- ADR-006 stage 6.5b: real streaming for the non-Ollama four ---------------
# -- Only the SDK/transport layer is faked in each of these; the provider's
# -- own stream() machinery (prep, event mapping, cancellation, composition)
# -- runs for real. The bar per provider: multiple incremental text deltas,
# -- reasoning events where the wire carries them, a final "done" whose text
# -- matches what complete() composes for the same data, and mid-stream
# -- cancellation that closes the live stream and raises the untranslated
# -- RequestCancelledError sentinel.


class FakeSDKStream:
    """Iterable-with-close() stand-in for openai's Stream / anthropic's raw
    event Stream / a llama.cpp chunk generator - anything the providers
    iterate and must close."""

    def __init__(self, items, cancel_event=None, cancel_after=None):
        self._items = list(items)
        self._index = 0
        self.close_calls = 0
        self._cancel_event = cancel_event
        self._cancel_after = cancel_after

    def __iter__(self):
        return self

    def __next__(self):
        if self._index >= len(self._items):
            raise StopIteration
        item = self._items[self._index]
        self._index += 1
        if self._cancel_event is not None and self._index == self._cancel_after:
            self._cancel_event.set()  # cancel lands after this item is delivered
        return item

    def close(self):
        self.close_calls += 1


def _openai_chunk(content=None, reasoning_content=None, choices_empty=False):
    import types

    if choices_empty:
        return types.SimpleNamespace(choices=[])  # usage-only chunk shape
    delta_fields = {"content": content}
    if reasoning_content is not None:
        delta_fields["reasoning_content"] = reasoning_content
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(delta=types.SimpleNamespace(**delta_fields), finish_reason=None)]
    )


def _fake_openai_streaming_client(chunks_or_stream):
    import types

    captured = {}
    stream = (
        chunks_or_stream
        if isinstance(chunks_or_stream, FakeSDKStream)
        else FakeSDKStream(chunks_or_stream)
    )

    def create(**kwargs):
        captured.update(kwargs)
        assert kwargs.get("stream") is True
        return stream

    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )
    return client, stream, captured


def test_openai_stream_yields_incremental_deltas_reasoning_and_a_done_matching_complete():
    from backend.providers import CancelToken as CT, ChatRequest as CR, OpenAIProvider

    client, stream, captured = _fake_openai_streaming_client([
        _openai_chunk(content="Hel"),
        _openai_chunk(choices_empty=True),        # usage-only chunk must be skipped
        _openai_chunk(reasoning_content="hmm "),  # compatible-server thinking delta
        _openai_chunk(content="lo "),
        _openai_chunk(content="world"),
    ])
    provider = OpenAIProvider(client=client, model="gpt-5", reasoning_level="high")
    events = list(provider.stream(
        CR(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]), CT()
    ))

    assert [e.type for e in events] == ["text", "reasoning", "text", "text", "done"]
    # Parity with complete(): raw concatenated content, no <think> composition
    # (the OpenAI blocking path returns message.content untouched).
    assert events[-1].text == "Hello world"
    assert stream.close_calls >= 1  # the finally closed the exhausted stream
    # Same request prep as complete(): reasoning kwargs applied for TASK_CHAT.
    chat_keys = set(captured) - {"model", "messages", "stream", "stream_options"}
    assert chat_keys == set(api_provider.openai_reasoning_kwargs("gpt-5", "high"))


def test_openai_stream_cancellation_mid_stream_closes_the_live_stream_and_raises():
    from backend.providers import CancelToken as CT, ChatRequest as CR, OpenAIProvider

    cancel_event = threading.Event()
    live = FakeSDKStream(
        [_openai_chunk(content="par"), _openai_chunk(content="tial")],
        cancel_event=cancel_event,
        cancel_after=1,
    )
    client, _, _ = _fake_openai_streaming_client(live)
    provider = OpenAIProvider(client=client, model="gpt-5")

    with pytest.raises(api_provider.RequestCancelledError):
        list(provider.stream(
            CR(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]),
            CT(cancel_event),
        ))
    assert live.close_calls >= 1  # the live HTTP stream was actively closed


def _anthropic_raw_events(with_thinking=True):
    """The raw wire shape shared by messages.create(stream=True) and the REST
    SSE - dicts here, exactly what the REST path yields; the provider reads
    both through _extract_response_field."""
    events = [
        {"type": "message_start", "message": {"role": "assistant"}},
        {"type": "content_block_start", "index": 0},
    ]
    if with_thinking:
        events += [
            {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "pondering"}},
            {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "..."}},
        ]
    events += [
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Ans"}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "wer."}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
        {"type": "message_stop"},
    ]
    return events


def test_anthropic_sdk_stream_yields_deltas_and_composes_done_like_the_blocking_path():
    import types

    from backend.providers import AnthropicProvider, CancelToken as CT, ChatRequest as CR

    captured = {}
    live = FakeSDKStream(_anthropic_raw_events())

    def create(**kwargs):
        captured.update(kwargs)
        assert kwargs.get("stream") is True  # passed explicitly, outside the filter
        return live

    sdk_client = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
    provider = AnthropicProvider(client=sdk_client, api_key="k", model="claude-opus-5")
    events = list(provider.stream(
        CR(task=config.TASK_CHAT, messages=[
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ]),
        CT(),
    ))

    assert [e.type for e in events] == ["reasoning", "reasoning", "text", "text", "done"]
    # Identical composition to _extract_anthropic_text's blocking contract.
    assert events[-1].text == "<think>pondering...</think>\nAnswer."
    assert captured["model"] == "claude-opus-5"
    # ADR-006 stage 6.7: system goes out as a cache_control block list.
    assert captured["system"] == [
        {"type": "text", "text": "be brief", "cache_control": {"type": "ephemeral"}}
    ]
    assert live.close_calls >= 1


def test_anthropic_rest_fallback_streams_through_the_new_sse_reader(monkeypatch):
    from backend.providers import AnthropicProvider, CancelToken as CT, ChatRequest as CR

    sse_calls = {}

    def fake_stream_sse(url, body, timeout=180, cancel_event=None, api_key=None):
        sse_calls.update(url=url, body=body, api_key=api_key)
        yield from _anthropic_raw_events(with_thinking=False)

    monkeypatch.setattr(
        "backend.providers.anthropic_provider._anthropic_stream_sse", fake_stream_sse
    )
    provider = AnthropicProvider(
        client={"provider": "anthropic", "transport": "rest"}, api_key="k", model="claude-opus-5"
    )
    events = list(provider.stream(
        CR(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]), CT()
    ))

    assert [e.type for e in events] == ["text", "text", "done"]
    assert events[-1].text == "Answer."
    assert sse_calls["url"].endswith("/v1/messages")
    assert sse_calls["body"]["model"] == "claude-opus-5"
    assert sse_calls["api_key"] == "k"


def test_anthropic_stream_cancellation_mid_stream_closes_the_live_stream_and_raises():
    import types

    from backend.providers import AnthropicProvider, CancelToken as CT, ChatRequest as CR

    cancel_event = threading.Event()
    live = FakeSDKStream(_anthropic_raw_events(), cancel_event=cancel_event, cancel_after=3)

    def create(**kwargs):
        return live

    sdk_client = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
    provider = AnthropicProvider(client=sdk_client, api_key="k", model="claude-opus-5")

    with pytest.raises(api_provider.RequestCancelledError):
        list(provider.stream(
            CR(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]),
            CT(cancel_event),
        ))
    assert live.close_calls >= 1


def _consume_expecting(provider_stream, exc_type, match):
    """Drain a provider stream expecting it to raise; return the events that
    made it out first, so callers can assert no \"done\" was emitted."""
    events = []
    with pytest.raises(exc_type, match=match):
        for event in provider_stream:
            events.append(event)
    return events


def _sdk_client_returning(live):
    import types

    return types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **kwargs: live))


_ANTHROPIC_ERROR_EVENT = {
    "type": "error",
    "error": {"type": "overloaded_error", "message": "Overloaded"},
}


def test_anthropic_mid_stream_error_event_raises_on_both_transports(monkeypatch):
    """6.5b review (HIGH): the streaming API can send an error event on a 200
    stream and close - it must raise with the API's type+message (the
    _anthropic_post_json posture), never compose the partial text as done."""
    from backend.providers import AnthropicProvider, CancelToken as CT, ChatRequest as CR

    request = CR(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}])
    wire = _anthropic_raw_events(with_thinking=False)[:4] + [_ANTHROPIC_ERROR_EVENT]

    # SDK transport: the fake stream yields the error-shaped event itself.
    provider = AnthropicProvider(
        client=_sdk_client_returning(FakeSDKStream(wire)), api_key="k", model="claude-opus-5"
    )
    events = _consume_expecting(
        provider.stream(request, CT()), RuntimeError, "overloaded_error: Overloaded"
    )
    assert events and all(e.type != "done" for e in events)  # deltas out, no done

    # REST transport: the SSE reader yields the same wire-shaped dict.
    def fake_stream_sse(url, body, timeout=180, cancel_event=None, api_key=None):
        yield from wire

    monkeypatch.setattr(
        "backend.providers.anthropic_provider._anthropic_stream_sse", fake_stream_sse
    )
    provider = AnthropicProvider(
        client={"provider": "anthropic", "transport": "rest"}, api_key="k", model="claude-opus-5"
    )
    events = _consume_expecting(
        provider.stream(request, CT()), RuntimeError, "overloaded_error: Overloaded"
    )
    assert events and all(e.type != "done" for e in events)


def test_anthropic_stream_ending_without_message_stop_raises_on_both_transports(monkeypatch):
    """6.5b review: a successful Anthropic stream always ends with
    message_stop - an iterator that just stops (proxy truncation, silent
    close) delivered a fragment, and composing it would present a truncated
    answer as complete."""
    from backend.providers import AnthropicProvider, CancelToken as CT, ChatRequest as CR

    request = CR(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}])
    truncated = _anthropic_raw_events(with_thinking=False)[:-1]  # everything but message_stop

    provider = AnthropicProvider(
        client=_sdk_client_returning(FakeSDKStream(truncated)), api_key="k", model="claude-opus-5"
    )
    events = _consume_expecting(
        provider.stream(request, CT()), RuntimeError, "ended unexpectedly before completion"
    )
    assert events and all(e.type != "done" for e in events)

    def fake_stream_sse(url, body, timeout=180, cancel_event=None, api_key=None):
        yield from truncated

    monkeypatch.setattr(
        "backend.providers.anthropic_provider._anthropic_stream_sse", fake_stream_sse
    )
    provider = AnthropicProvider(
        client={"provider": "anthropic", "transport": "rest"}, api_key="k", model="claude-opus-5"
    )
    events = _consume_expecting(
        provider.stream(request, CT()), RuntimeError, "ended unexpectedly before completion"
    )
    assert events and all(e.type != "done" for e in events)


def test_anthropic_sse_reader_parses_data_lines_and_always_closes(monkeypatch):
    """The urllib-level unit for _anthropic_stream_sse itself: `event:` naming
    lines and blank separators are skipped, each data: line parses to its
    event dict, the request body carries \"stream\": true, and the response is
    closed even when the consumer abandons the generator mid-stream."""
    import io
    import json as json_module

    class FakeHTTPResponse:
        def __init__(self, lines):
            self._lines = lines
            self.closed = False

        def __iter__(self):
            return iter(self._lines)

        def close(self):
            self.closed = True

    wire = [
        b"event: content_block_delta\n",
        b"data: " + json_module.dumps(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}}
        ).encode() + b"\n",
        b"\n",
        b"data: " + json_module.dumps({"type": "message_stop"}).encode() + b"\n",
    ]
    response = FakeHTTPResponse(wire)
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["body"] = json_module.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return response

    monkeypatch.setattr(api_provider.urllib.request, "urlopen", fake_urlopen)

    events = list(api_provider._anthropic_stream_sse(
        "https://api.anthropic.com/v1/messages", {"model": "m"}, api_key="k"
    ))
    assert [e["type"] for e in events] == ["content_block_delta", "message_stop"]
    assert captured["body"]["stream"] is True
    assert response.closed is True

    # Abandonment: close() on a mid-flight generator still closes the response.
    response2 = FakeHTTPResponse(wire)
    monkeypatch.setattr(
        api_provider.urllib.request, "urlopen", lambda request, timeout=None: response2
    )
    gen = api_provider._anthropic_stream_sse(
        "https://api.anthropic.com/v1/messages", {"model": "m"}, api_key="k"
    )
    next(gen)
    gen.close()
    assert response2.closed is True


def _gemini_sse_payload(*parts):
    return {"candidates": [{"content": {"parts": list(parts)}}]}


def test_gemini_stream_maps_thought_parts_to_reasoning_and_keeps_concatenation_parity(monkeypatch):
    from backend.providers import CancelToken as CT, ChatRequest as CR, GeminiProvider

    sse_calls = {}

    def fake_stream_sse(url, body, timeout=120, cancel_event=None, api_key=None):
        sse_calls.update(url=url, body=body, timeout=timeout)
        yield _gemini_sse_payload({"text": "pondering... ", "thought": True})
        yield _gemini_sse_payload({"text": "Ans"})
        yield _gemini_sse_payload({"text": "wer."})

    monkeypatch.setattr(
        "backend.providers.gemini_provider._prepare_gemini_contents",
        lambda messages, cancel_event=None, api_key=None: (None, [{"parts": [{"text": "hi"}]}], []),
    )
    monkeypatch.setattr("backend.providers.gemini_provider._gemini_stream_sse", fake_stream_sse)

    provider = GeminiProvider(api_key="k", model="gemini-2.5-pro")
    events = list(provider.stream(
        CR(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]), CT()
    ))

    assert [e.type for e in events] == ["reasoning", "text", "text", "done"]
    # Parity with complete(): _extract_gemini_text concatenates EVERY text
    # part, thought parts included - no <think> composition for Gemini yet.
    assert events[-1].text == "pondering... Answer."
    assert ":streamGenerateContent?alt=sse" in sse_calls["url"]
    assert sse_calls["body"]["contents"] == [{"parts": [{"text": "hi"}]}]


def test_gemini_stream_cancellation_still_deletes_uploaded_files(monkeypatch):
    from backend.providers import CancelToken as CT, ChatRequest as CR, GeminiProvider

    cancel_event = threading.Event()
    deleted = []
    sse_closed = {"count": 0}

    def fake_stream_sse(url, body, timeout=120, cancel_event=None, api_key=None):
        try:
            yield _gemini_sse_payload({"text": "par"})
            cancel_event_outer.set()
            yield _gemini_sse_payload({"text": "tial"})
        finally:
            sse_closed["count"] += 1

    cancel_event_outer = cancel_event
    monkeypatch.setattr(
        "backend.providers.gemini_provider._prepare_gemini_contents",
        lambda messages, cancel_event=None, api_key=None: (None, [{"parts": [{"text": "hi"}]}], ["files/abc"]),
    )
    monkeypatch.setattr("backend.providers.gemini_provider._gemini_stream_sse", fake_stream_sse)
    monkeypatch.setattr(
        "backend.providers.gemini_provider._gemini_delete_file",
        lambda name, api_key=None: deleted.append(name),
    )

    provider = GeminiProvider(api_key="k", model="gemini-2.5-pro")
    with pytest.raises(api_provider.RequestCancelledError):
        list(provider.stream(
            CR(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]),
            CT(cancel_event),
        ))
    assert deleted == ["files/abc"]  # the load-bearing cleanup ran on the cancel path
    assert sse_closed["count"] >= 1  # and the live SSE generator was closed


def test_gemini_stream_surfaces_the_safety_block_as_the_exact_blocking_path_error(monkeypatch):
    from backend.providers import CancelToken as CT, ChatRequest as CR, GeminiProvider

    def fake_stream_sse(url, body, timeout=120, cancel_event=None, api_key=None):
        yield {"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []}

    monkeypatch.setattr(
        "backend.providers.gemini_provider._prepare_gemini_contents",
        lambda messages, cancel_event=None, api_key=None: (None, [{"parts": [{"text": "hi"}]}], []),
    )
    monkeypatch.setattr("backend.providers.gemini_provider._gemini_stream_sse", fake_stream_sse)

    provider = GeminiProvider(api_key="k", model="gemini-2.5-pro")
    with pytest.raises(RuntimeError, match=r"Safety Filters \(SAFETY\)"):
        list(provider.stream(
            CR(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]), CT()
        ))


def test_gemini_mid_stream_error_frame_raises_with_the_apis_message(monkeypatch):
    """6.5b review (MEDIUM): an error frame ({\"error\": {\"code\": ...,
    \"message\": ...}}) matches neither promptFeedback nor candidates - it
    must raise with the parsed message (same extraction _gemini_post_json
    applies to that payload shape), never let partial text return as done."""
    from backend.providers import CancelToken as CT, ChatRequest as CR, GeminiProvider

    def fake_stream_sse(url, body, timeout=120, cancel_event=None, api_key=None):
        yield _gemini_sse_payload({"text": "par"})
        yield {"error": {"code": 503, "message": "The model is overloaded.", "status": "UNAVAILABLE"}}
        yield _gemini_sse_payload({"text": "tial"})  # never reached

    monkeypatch.setattr(
        "backend.providers.gemini_provider._prepare_gemini_contents",
        lambda messages, cancel_event=None, api_key=None: (None, [{"parts": [{"text": "hi"}]}], []),
    )
    monkeypatch.setattr("backend.providers.gemini_provider._gemini_stream_sse", fake_stream_sse)

    provider = GeminiProvider(api_key="k", model="gemini-2.5-pro")
    events = _consume_expecting(
        provider.stream(
            CR(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]), CT()
        ),
        RuntimeError,
        "The model is overloaded",
    )
    assert [e.type for e in events] == ["text"]  # the pre-error delta got out; no done followed


def _llama_chunk(content=None, reasoning_key=None, reasoning=None, finish=None):
    delta = {}
    if content is not None:
        delta["content"] = content
    if reasoning_key is not None:
        delta[reasoning_key] = reasoning
    return {"choices": [{"delta": delta, "finish_reason": finish}]}


def _llama_streaming_setup(monkeypatch, chunks):
    """Fake ONLY the client/prep seams; the provider's own stream() runs for
    real. The fake create_chat_completion has a strict no-**kwargs signature
    (what _filter_kwargs_for_callable filters passthrough kwargs against);
    the drop-trap test below has its own signature-narrowed fake."""
    from backend.providers import llama_cpp_provider as lp

    live = FakeSDKStream(chunks) if not isinstance(chunks, FakeSDKStream) else chunks
    captured = {}

    class FakeLlamaClient:
        def create_chat_completion(self, messages=None, stream=False, temperature=None):
            captured.update(messages=messages, stream=stream, temperature=temperature)
            return live

    monkeypatch.setattr(lp, "_prepare_llama_cpp_messages", lambda messages, task, s: messages)
    monkeypatch.setattr(lp, "_get_llama_cpp_client", lambda task, s: FakeLlamaClient())
    monkeypatch.setattr(lp, "_prepare_llama_cpp_kwargs", lambda kwargs, s: dict(kwargs))
    return live, captured


def test_llama_cpp_stream_yields_deltas_reasoning_and_a_done_matching_completes_composition(monkeypatch):
    from backend.providers import CancelToken as CT, ChatRequest as CR, LlamaCppProvider

    live, captured = _llama_streaming_setup(monkeypatch, [
        _llama_chunk(reasoning_key="reasoning_content", reasoning="pondering..."),
        {"choices": []},  # defensive: empty-choices chunk must be skipped
        _llama_chunk(content="Ans"),
        _llama_chunk(content="wer.", finish="stop"),
    ])

    provider = LlamaCppProvider(settings={"chat_model_path": "m.gguf"})
    events = list(provider.stream(
        CR(
            task=config.TASK_CHAT,
            messages=[{"role": "user", "content": "hi"}],
            extra_kwargs={"temperature": 0.5, "cancellation_event": threading.Event()},
        ),
        CT(),
    ))

    assert [e.type for e in events] == ["reasoning", "text", "text", "done"]
    # Composed through _extract_llama_cpp_text, exactly as complete() would
    # for the same content+reasoning - the shared-extraction parity contract.
    assert events[-1].text == "<think>pondering...</think>\nAnswer."
    assert captured["stream"] is True
    assert captured["temperature"] == 0.5  # passthrough kwargs survived the filter
    assert live.close_calls >= 1  # the generator over the shared client was closed


def test_llama_cpp_stream_true_survives_the_kwargs_filter_and_beats_a_passthrough_stream(monkeypatch):
    """THE 6.5b drop-trap test (reworked per adversarial review - the first
    version's fake DECLARED `stream`, so filter-routing would have passed it
    anyway and the test couldn't fail on the wrong implementation). Genuine
    discriminator: the fake's INSPECTABLE signature (a narrowed
    __signature__, which inspect.signature honors and therefore what
    _filter_kwargs_for_callable sees) declares neither `stream` nor
    **kwargs - filter-routing WOULD drop stream=True (asserted directly
    below), and the fake then returns a NON-generator blocking dict, so the
    wrong path fails loudly on both the captured flag and the events. The
    explicit out-of-band pass is what makes it arrive. Bonus half: a
    passthrough extra_kwarg trying to force stream=False loses to ours."""
    import inspect

    from backend.providers import CancelToken as CT, ChatRequest as CR, LlamaCppProvider
    from backend.providers import llama_cpp_provider as lp

    captured = {}
    live = FakeSDKStream([_llama_chunk(content="ok", finish="stop")])

    def create_chat_completion(messages=None, temperature=None, **kwargs):
        captured.update(messages=messages, temperature=temperature, **kwargs)
        if not kwargs.get("stream"):
            return {"choices": [{"message": {"content": "BLOCKING RESPONSE"}}]}
        return live

    create_chat_completion.__signature__ = inspect.Signature([
        inspect.Parameter("messages", inspect.Parameter.POSITIONAL_OR_KEYWORD, default=None),
        inspect.Parameter("temperature", inspect.Parameter.POSITIONAL_OR_KEYWORD, default=None),
    ])

    class FakeLlamaClient:
        pass

    client = FakeLlamaClient()
    client.create_chat_completion = create_chat_completion
    monkeypatch.setattr(lp, "_prepare_llama_cpp_messages", lambda messages, task, s: messages)
    monkeypatch.setattr(lp, "_get_llama_cpp_client", lambda task, s: client)
    monkeypatch.setattr(lp, "_prepare_llama_cpp_kwargs", lambda kwargs, s: dict(kwargs))

    # The discriminator's premise, asserted directly: routed through the
    # filter, stream would never reach the callable.
    assert "stream" not in api_provider._filter_kwargs_for_callable(
        create_chat_completion, {"stream": True, "temperature": 0.5}
    )

    provider = LlamaCppProvider(settings={"chat_model_path": "m.gguf"})
    events = list(provider.stream(
        CR(
            task=config.TASK_CHAT,
            messages=[{"role": "user", "content": "hi"}],
            extra_kwargs={"stream": False, "temperature": 0.5},
        ),
        CT(),
    ))
    assert captured["stream"] is True  # explicit pass survived where the filter would drop it
    assert captured["temperature"] == 0.5  # declared passthrough kwargs still flow
    assert events[-1] == ProviderEvent("done", "ok")


def test_llama_cpp_stream_cancellation_closes_the_generator_and_raises(monkeypatch):
    from backend.providers import CancelToken as CT, ChatRequest as CR, LlamaCppProvider

    cancel_event = threading.Event()
    live = FakeSDKStream(
        [_llama_chunk(content="par"), _llama_chunk(content="tial", finish="stop")],
        cancel_event=cancel_event,
        cancel_after=1,
    )
    _llama_streaming_setup(monkeypatch, live)

    provider = LlamaCppProvider(settings={"chat_model_path": "m.gguf"})
    with pytest.raises(api_provider.RequestCancelledError):
        list(provider.stream(
            CR(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]),
            CT(cancel_event),
        ))
    # Cooperative-only, but the shared cached client's generator must never
    # be left partially consumed.
    assert live.close_calls >= 1


# -- 6.5b: the chat_stream seam - real dispatch replaces the fallback ---------
# -- The old non-Ollama short-circuit (one blocking chat() call + one
# -- synthetic full-text chunk) is gone: chat_stream now constructs every
# -- provider exactly as chat() does and consumes its stream(), with the
# -- consuming loop inside chat_stream's own _translate_chat_exception try
# -- (the lazy-generator contract means the old path's free-via-recursion
# -- translation no longer exists).


def _install_scripted_provider(monkeypatch, module_name, class_name, events):
    """Swap a provider class (at the module chat_stream lazily imports from)
    for a protocol-shaped double that records its __init__ kwargs and yields
    scripted events - the FakeFactory pattern from the 6.1 seam tests."""
    module = __import__(module_name, fromlist=[class_name])
    seen = {}

    class Scripted:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def stream(self, request, cancel):
            seen["task"] = request.task
            return iter(events)

    monkeypatch.setattr(module, class_name, Scripted)
    return seen


_SCRIPTED_STREAM_EVENTS = [
    ProviderEvent("text", "one "),
    ProviderEvent("reasoning", "never forwarded"),
    ProviderEvent("text", "two "),
    ProviderEvent("text", "three"),
    ProviderEvent("done", "<think>never forwarded</think>\none two three"),
]


@pytest.mark.parametrize("provider_type, module_name, class_name, expected_init", [
    (
        config.API_PROVIDER_OPENAI,
        "backend.providers.openai_provider", "OpenAIProvider",
        {"model": "some-model", "reasoning_level": "high"},
    ),
    (
        config.API_PROVIDER_ANTHROPIC,
        "backend.providers.anthropic_provider", "AnthropicProvider",
        {"api_key": "secret-key", "model": "some-model", "reasoning_level": "medium"},
    ),
    (
        config.API_PROVIDER_GEMINI,
        "backend.providers.gemini_provider", "GeminiProvider",
        {"api_key": "secret-key", "model": "some-model", "reasoning_level": "low"},
    ),
])
def test_chat_stream_streams_real_deltas_through_each_api_provider_branch(
    monkeypatch, provider_type, module_name, class_name, expected_init
):
    """THE 6.5b EXIT CRITERION at the seam, per API provider: chat_stream's
    real machinery constructs the provider from the snapshot credentials
    (chat()'s exact kwargs) and forwards its incremental deltas through
    on_chunk - multiple chunks, reasoning dropped, done as the return."""
    monkeypatch.setattr(api_provider, "chat_stream", _REAL_CHAT_STREAM)
    seen = _install_scripted_provider(monkeypatch, module_name, class_name, _SCRIPTED_STREAM_EVENTS)

    client = _fake_openai_client()[0]
    monkeypatch.setattr(api_provider, "USE_API_MODE", True)
    monkeypatch.setattr(api_provider, "API_PROVIDER_TYPE", provider_type)
    monkeypatch.setattr(api_provider, "API_KEY", "secret-key")
    monkeypatch.setattr(api_provider, "API_CLIENT", client)
    monkeypatch.setattr(api_provider, "OPENAI_REASONING_LEVEL", "high")
    monkeypatch.setattr(api_provider, "ANTHROPIC_REASONING_LEVEL", "medium")
    monkeypatch.setattr(api_provider, "GEMINI_REASONING_LEVEL", "low")
    monkeypatch.setitem(api_provider.API_MODELS, config.TASK_CHAT, "some-model")

    chunks: list[tuple[str, bool]] = []
    response = api_provider.chat_stream(
        config.TASK_CHAT,
        [{"role": "user", "content": "count"}],
        lambda delta, reset: chunks.append((delta, reset)),
    )

    assert chunks == [("one ", False), ("two ", False), ("three", False)]  # reasoning never forwarded
    assert response == {
        "message": {"content": "<think>never forwarded</think>\none two three", "role": "assistant"},
        "usage": None,
    }
    seen.pop("task")
    if class_name in ("OpenAIProvider", "AnthropicProvider"):
        expected_init = {**expected_init, "client": client}
    assert seen == expected_init  # constructed from the snapshot, chat()'s exact kwargs


def test_chat_stream_streams_real_deltas_through_the_llama_cpp_local_branch(monkeypatch):
    """llama.cpp local mode sat in the same fallback short-circuit until
    6.5b - it must now stream through LlamaCppProvider.stream()."""
    monkeypatch.setattr(api_provider, "chat_stream", _REAL_CHAT_STREAM)
    seen = _install_scripted_provider(
        monkeypatch, "backend.providers.llama_cpp_provider", "LlamaCppProvider",
        _SCRIPTED_STREAM_EVENTS,
    )
    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_LLAMACPP)
    settings = {"chat_model_path": "m.gguf", "reasoning_level": "off"}
    monkeypatch.setattr(api_provider, "LLAMA_CPP_SETTINGS", settings)

    chunks: list[tuple[str, bool]] = []
    response = api_provider.chat_stream(
        config.TASK_CHAT,
        [{"role": "user", "content": "count"}],
        lambda delta, reset: chunks.append((delta, reset)),
    )

    assert chunks == [("one ", False), ("two ", False), ("three", False)]
    assert response["message"]["content"].endswith("one two three")
    assert seen["settings"] == settings


def test_chat_stream_translates_a_mid_stream_connection_error_to_the_friendly_message(
    monkeypatch, openai_api_mode
):
    """The lazy-generator contract's teeth: the old short-circuit got error
    translation for free by recursing into chat(); real streaming must own
    it. A connection failure surfacing MID-stream (after deltas already
    reached on_chunk) must still come out as _translate_chat_exception's
    actionable Base-URL message, not raw exception text."""
    monkeypatch.setattr(api_provider, "chat_stream", _REAL_CHAT_STREAM)

    class DyingStream(FakeSDKStream):
        def __next__(self):
            if self._index >= 1:
                raise Exception("connection refused by endpoint")
            return super().__next__()

    live = DyingStream([_openai_chunk(content="par")])
    client, _, _ = _fake_openai_streaming_client(live)
    openai_api_mode(client)

    chunks: list[tuple[str, bool]] = []
    with pytest.raises(ConnectionError) as excinfo:
        api_provider.chat_stream(
            config.TASK_CHAT,
            [{"role": "user", "content": "hi"}],
            lambda delta, reset: chunks.append((delta, reset)),
        )

    assert chunks == [("par", False)]  # the failure was genuinely mid-stream
    message = str(excinfo.value)
    assert message.startswith(
        "Failed to connect to the API endpoint. "
        "Please verify your Base URL in settings and your network connection."
    )
    assert "Details: connection refused by endpoint" in message


def test_chat_stream_cancellation_in_api_mode_escapes_untranslated(monkeypatch, openai_api_mode):
    """The RequestCancelledError sentinel contract, now on the API-mode
    streaming path: backend/agents.py catches exactly this type."""
    monkeypatch.setattr(api_provider, "chat_stream", _REAL_CHAT_STREAM)
    cancel_event = threading.Event()
    live = FakeSDKStream(
        [_openai_chunk(content="par"), _openai_chunk(content="tial")],
        cancel_event=cancel_event,
        cancel_after=1,
    )
    client, _, _ = _fake_openai_streaming_client(live)
    openai_api_mode(client)

    with pytest.raises(api_provider.RequestCancelledError):
        api_provider.chat_stream(
            config.TASK_CHAT,
            [{"role": "user", "content": "hi"}],
            lambda delta, reset: None,
            cancellation_event=cancel_event,
        )
    assert live.close_calls >= 1  # closed on the way out


def test_chat_constructs_each_api_provider_from_the_snapshot_credentials(monkeypatch):
    """6.3 review fix: the routing test proves the classes are INVOKED but not
    that they're constructed correctly - a branch passing the wrong key/client/
    level would still pass it. Recording subclasses pin the exact __init__
    kwargs each branch draws from the request snapshot."""
    init_kwargs = {}

    for module_name, class_name in [
        ("backend.providers.openai_provider", "OpenAIProvider"),
        ("backend.providers.anthropic_provider", "AnthropicProvider"),
        ("backend.providers.gemini_provider", "GeminiProvider"),
    ]:
        module = __import__(module_name, fromlist=[class_name])
        real = getattr(module, class_name)

        def make_recording(real_cls, label):
            class Recording(real_cls):
                def __init__(self, **kwargs):
                    init_kwargs[label] = dict(kwargs)
                    super().__init__(**kwargs)

                def complete(self, request, cancel):
                    return f"{label} answer"
            return Recording

        monkeypatch.setattr(module, class_name, make_recording(real, class_name))

    client = _fake_openai_client()[0]
    monkeypatch.setattr(api_provider, "USE_API_MODE", True)
    monkeypatch.setattr(api_provider, "API_KEY", "secret-key")
    monkeypatch.setattr(api_provider, "API_CLIENT", client)
    monkeypatch.setattr(api_provider, "OPENAI_REASONING_LEVEL", "high")
    monkeypatch.setattr(api_provider, "ANTHROPIC_REASONING_LEVEL", "medium")
    monkeypatch.setattr(api_provider, "GEMINI_REASONING_LEVEL", "low")
    monkeypatch.setitem(api_provider.API_MODELS, config.TASK_CHAT, "some-model")

    for provider_type in [
        config.API_PROVIDER_OPENAI,
        config.API_PROVIDER_ANTHROPIC,
        config.API_PROVIDER_GEMINI,
    ]:
        monkeypatch.setattr(api_provider, "API_PROVIDER_TYPE", provider_type)
        _REAL_CHAT(config.TASK_CHAT, [{"role": "user", "content": "hi"}])

    assert init_kwargs["OpenAIProvider"] == {
        "client": client, "model": "some-model", "reasoning_level": "high",
    }
    assert init_kwargs["AnthropicProvider"] == {
        "client": client, "api_key": "secret-key", "model": "some-model",
        "reasoning_level": "medium",
    }
    assert init_kwargs["GeminiProvider"] == {
        "api_key": "secret-key", "model": "some-model", "reasoning_level": "low",
    }


def test_anthropic_reasoning_level_gates_on_the_chat_task_at_the_provider(monkeypatch):
    """6.3 review fix: the OpenAI port's task gating is pinned above; the
    Anthropic port's equivalent (reasoning_level forwarded to
    _prepare_anthropic_kwargs only for TASK_CHAT, "off" otherwise) was not."""
    import types

    from backend.providers import AnthropicProvider, CancelToken as CT, ChatRequest as CR

    recorded = []

    def recording_prepare(task, kwargs, model_id="", reasoning_level="off"):
        recorded.append((task, reasoning_level))
        return {"max_tokens": 64}

    monkeypatch.setattr(
        "backend.providers.anthropic_provider._prepare_anthropic_kwargs", recording_prepare
    )

    def create(**kwargs):
        return {"content": [{"type": "text", "text": "a"}]}

    sdk_client = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
    provider = AnthropicProvider(
        client=sdk_client, api_key="k", model="claude-opus-5", reasoning_level="high"
    )

    provider.complete(CR(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]), CT())
    provider.complete(CR(task=config.TASK_TITLE, messages=[{"role": "user", "content": "hi"}]), CT())

    assert recorded == [(config.TASK_CHAT, "high"), (config.TASK_TITLE, "off")]


def test_gemini_thinking_config_gates_on_the_chat_task_at_the_provider(monkeypatch):
    """6.3 review fix: same gating pin for the Gemini port - thinkingConfig
    lands in the request body's generationConfig only for TASK_CHAT, and
    gemini_thinking_config is never even consulted for other tasks."""
    from backend.providers import CancelToken as CT, ChatRequest as CR, GeminiProvider

    bodies = []
    thinking_calls = []
    monkeypatch.setattr(
        "backend.providers.gemini_provider._prepare_gemini_contents",
        lambda messages, cancel_event=None, api_key=None: (None, [{"parts": [{"text": "hi"}]}], []),
    )
    monkeypatch.setattr(
        "backend.providers.gemini_provider.gemini_thinking_config",
        lambda model_id, level: (thinking_calls.append((model_id, level)) or {"thinkingBudget": 128}),
    )

    def fake_post(url, body, **kwargs):
        bodies.append(body)
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    monkeypatch.setattr("backend.providers.gemini_provider._gemini_post_json", fake_post)

    provider = GeminiProvider(api_key="k", model="gemini-2.5-pro", reasoning_level="high")
    provider.complete(CR(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]), CT())
    provider.complete(CR(task=config.TASK_TITLE, messages=[{"role": "user", "content": "hi"}]), CT())

    assert thinking_calls == [("gemini-2.5-pro", "high")]  # only the chat task consults it
    assert bodies[0]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 128}
    assert "generationConfig" not in bodies[1]  # no stray config key for non-chat tasks


# -- ADR-006 stage 6.5: per-session ProviderRuntime ---------------------------
# -- The stage exit criterion: two runtimes hold two different provider
# -- configurations CONCURRENTLY - a chat routed with runtime=r2 constructs
# -- its provider from r2's snapshot while a default-path call keeps using
# -- the module globals, and neither leaks into the other.


def _recording_ollama_provider(monkeypatch):
    """Install a recording subclass over backend.providers.ollama_provider.
    OllamaProvider (the class chat() lazily imports) - same construction-
    fidelity pattern as test_chat_routes_every_api_provider_through_its_
    provider_class above. Returns the list of (model, reasoning_level)
    pairs each completed request was constructed with."""
    import backend.providers.ollama_provider as ollama_provider_module

    constructed = []

    class Recording(ollama_provider_module.OllamaProvider):
        def complete(self, request, cancel):
            constructed.append((self.model_id, self.reasoning_level))
            return f"answer from {self.model_id}"

    monkeypatch.setattr(ollama_provider_module, "OllamaProvider", Recording)
    return constructed


def test_two_runtimes_hold_different_providers_concurrently(monkeypatch, ollama_mode):
    """THE 6.5 EXIT CRITERION (api_provider level): the same chat() function,
    called with and without runtime=, serves two different provider
    configurations side by side without either bleeding into the other."""
    monkeypatch.setattr(api_provider, "chat", _REAL_CHAT)
    constructed = _recording_ollama_provider(monkeypatch)

    # Session two: starts as a copy of the default configuration
    # (from_snapshot - exactly how backend/app.py seeds a non-default
    # session), then diverges through its own public mutators.
    r2 = api_provider.ProviderRuntime.from_snapshot(api_provider.DEFAULT_RUNTIME.snapshot())
    r2.set_ollama_models({config.TASK_CHAT: "session-two-model:7b"})
    r2.set_ollama_reasoning_level("high")

    messages = [{"role": "user", "content": "hi"}]
    default_response = api_provider.chat(config.TASK_CHAT, messages)
    r2_response = api_provider.chat(config.TASK_CHAT, messages, runtime=r2)
    default_again = api_provider.chat(config.TASK_CHAT, messages)

    # Each call constructed its provider from ITS runtime's snapshot.
    assert constructed == [
        ("fake-model:1b", "off"),  # default session - the module globals
        ("session-two-model:7b", "high"),  # session two - r2's own state
        ("fake-model:1b", "off"),  # default again: r2 never leaked back
    ]
    assert default_response["message"]["content"] == "answer from fake-model:1b"
    assert r2_response["message"]["content"] == "answer from session-two-model:7b"
    assert default_again == default_response

    # And r2's divergence never touched the default session's authoritative
    # state - the module globals and the shared Ollama table.
    assert config.OLLAMA_MODELS[config.TASK_CHAT] == "fake-model:1b"
    assert api_provider.OLLAMA_REASONING_LEVEL == "off"


def test_from_snapshot_seeds_a_faithful_independent_copy(monkeypatch, ollama_mode):
    r2 = api_provider.ProviderRuntime.from_snapshot(api_provider.DEFAULT_RUNTIME.snapshot())

    assert r2.snapshot() == api_provider.DEFAULT_RUNTIME.snapshot()

    # Independence in BOTH directions: mutating the copy leaves the default
    # untouched, and mutating the default leaves the copy untouched.
    r2.set_ollama_models({config.TASK_CHAT: "diverged:1b"})
    assert config.OLLAMA_MODELS[config.TASK_CHAT] == "fake-model:1b"
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, "default-moved:1b")
    assert r2.snapshot().ollama_models[config.TASK_CHAT] == "diverged:1b"


def test_is_configured_accepts_a_text_only_api_endpoint_without_an_image_model(monkeypatch):
    """H6: TASK_IMAGE_GEN is optional for EVERY API provider - a text-only
    OpenAI-compatible endpoint (vLLM, LM Studio, llama-server) counts as
    configured; image generation raises its own actionable error at call
    time instead."""
    monkeypatch.setattr(api_provider, "USE_API_MODE", True)
    monkeypatch.setattr(api_provider, "API_PROVIDER_TYPE", config.API_PROVIDER_OPENAI)
    monkeypatch.setattr(api_provider, "API_CLIENT", object())
    monkeypatch.setattr(
        api_provider,
        "API_MODELS",
        {
            config.TASK_TITLE: "m",
            config.TASK_CHAT: "m",
            config.TASK_CHART: "m",
            config.TASK_IMAGE_GEN: None,  # deliberately absent
            config.TASK_WEB_VALIDATE: "m",
            config.TASK_WEB_SUMMARIZE: "m",
        },
    )

    assert api_provider.is_configured() is True


def test_snapshot_ollama_models_is_a_copy_not_a_live_view(monkeypatch, ollama_mode):
    """H6 pin: the snapshot's Ollama model table is copied UNDER the provider
    lock at snapshot time - mutating config.OLLAMA_MODELS afterward (the
    mid-request model-assignment race H6 closed) cannot change what an
    in-flight request already captured."""
    snapshot = api_provider._snapshot_provider_state()
    assert snapshot.ollama_models[config.TASK_CHAT] == "fake-model:1b"

    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, "swapped-mid-request:1b")

    assert snapshot.ollama_models[config.TASK_CHAT] == "fake-model:1b"


# -- ADR-006 stage 6.5 review fix: llama.cpp preload write-ordering ----------


def test_llama_cpp_preload_failure_never_makes_the_new_settings_snapshot_visible(monkeypatch, tmp_path):
    """A LOW-severity finding from the 6.5 adversarial review: the old
    write-then-preload-then-rollback-on-failure ordering had a window where
    a concurrent snapshot() (e.g. a new session's ProviderRuntime.
    from_snapshot) could observe and permanently copy settings that were
    about to be rolled back. Preloading BEFORE writing means a failed
    preload never makes the new settings visible to any snapshot at all -
    not even transiently."""
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"not a real gguf, just needs to exist")

    runtime = api_provider.ProviderRuntime()
    original_snapshot = runtime.snapshot()

    def failing_preload(task, settings):
        # Prove the write has NOT happened yet when the preload runs.
        assert runtime.snapshot() == original_snapshot
        raise RuntimeError("out of memory")

    monkeypatch.setattr(api_provider, "_get_llama_cpp_client", failing_preload)

    with pytest.raises(RuntimeError, match="out of memory"):
        runtime.initialize_local_provider(
            config.LOCAL_PROVIDER_LLAMACPP,
            {"chat_model_path": str(model_path)},
            preload_model=True,
        )

    # Nothing changed - not even transiently, and there was no rollback to
    # perform because there was nothing to roll back.
    assert runtime.snapshot() == original_snapshot


def test_llama_cpp_preload_success_writes_settings_after_the_preload_completes(monkeypatch, tmp_path):
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"not a real gguf, just needs to exist")

    runtime = api_provider.ProviderRuntime()
    order = []

    def recording_preload(task, settings):
        order.append("preload")
        return object()

    monkeypatch.setattr(api_provider, "_get_llama_cpp_client", recording_preload)
    original_write = api_provider.ProviderRuntime._write

    def recording_write(self, **updates):
        order.append("write")
        return original_write(self, **updates)

    monkeypatch.setattr(api_provider.ProviderRuntime, "_write", recording_write)

    result = runtime.initialize_local_provider(
        config.LOCAL_PROVIDER_LLAMACPP,
        {"chat_model_path": str(model_path)},
        preload_model=True,
    )

    assert order == ["preload", "write"]
    assert result["preloaded"] is True
    assert runtime.snapshot().llama_cpp_settings["chat_model_path"] == str(model_path)


# -- ADR-006 stage 6.8: real usage rides the done event ------------------------
#
# Convention (backend/providers/base.py): providers never emit a standalone
# "usage" event - normalized {"prompt_tokens", "completion_tokens"} counts
# attach to the terminal "done" event, and chat_stream surfaces them in its
# return dict's additive "usage" key.


def test_ollama_stream_done_event_carries_normalized_usage(ollama_chat):
    done_part = _part(content="Answer.", done=True)
    done_part["prompt_eval_count"] = 12
    done_part["eval_count"] = 34
    ollama_chat.streams = [FakeOllamaStream([done_part])]
    provider = OllamaProvider(model="llava:13b")
    events = list(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]),
        CancelToken(),
    ))
    assert events[-1].type == "done"
    assert events[-1].usage == {"prompt_tokens": 12, "completion_tokens": 34}


def test_ollama_blocking_chat_surfaces_usage_in_the_return_dict(ollama_mode, ollama_chat):
    ollama_chat.responses = [{
        "message": {"content": "Plain answer."},
        "prompt_eval_count": 7,
        "eval_count": 9,
    }]
    response = _REAL_CHAT(config.TASK_CHAT, [{"role": "user", "content": "hi"}])
    assert response["usage"] == {"prompt_tokens": 7, "completion_tokens": 9}


def test_openai_stream_requests_include_usage_and_captures_the_usage_chunk():
    import types

    usage_chunk = types.SimpleNamespace(
        choices=[],
        usage=types.SimpleNamespace(prompt_tokens=100, completion_tokens=25),
    )
    stream = FakeSDKStream([_openai_chunk(content="Hi"), usage_chunk])
    client, stream, captured = _fake_openai_streaming_client(stream)
    from backend.providers import OpenAIProvider

    provider = OpenAIProvider(client=client, model="gpt-4o")
    events = list(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]),
        CancelToken(),
    ))
    assert captured["stream_options"] == {"include_usage": True}
    assert events[-1].type == "done"
    assert events[-1].usage == {"prompt_tokens": 100, "completion_tokens": 25}


def test_openai_stream_retries_once_without_stream_options_when_the_server_rejects_it():
    import types

    calls = []
    stream = FakeSDKStream([_openai_chunk(content="Hi")])

    def create(**kwargs):
        calls.append(kwargs)
        if "stream_options" in kwargs:
            raise TypeError("create() got an unexpected keyword argument 'stream_options'")
        return stream

    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )
    from backend.providers import OpenAIProvider

    provider = OpenAIProvider(client=client, model="gpt-4o")
    events = list(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]),
        CancelToken(),
    ))
    assert len(calls) == 2
    assert "stream_options" not in calls[1]  # degraded retry, no usage
    assert events[-1].type == "done"
    assert events[-1].usage is None


def test_anthropic_stream_captures_input_and_output_tokens_from_the_event_flow():
    import types

    from backend.providers import AnthropicProvider

    events_in = [
        {"type": "message_start", "message": {"role": "assistant", "usage": {"input_tokens": 55}}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Answer."}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 21}},
        {"type": "message_stop"},
    ]
    live = FakeSDKStream(events_in)
    sdk_client = types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kwargs: live)
    )
    provider = AnthropicProvider(client=sdk_client, api_key="k", model="claude-opus-5")
    events = list(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]),
        CancelToken(),
    ))
    assert events[-1].type == "done"
    assert events[-1].usage == {"prompt_tokens": 55, "completion_tokens": 21}


def test_gemini_stream_captures_usage_metadata_from_the_trailing_frame(monkeypatch):
    from backend.providers import GeminiProvider

    def fake_stream_sse(url, body, timeout=120, cancel_event=None, api_key=None):
        yield _gemini_sse_payload({"text": "Answer."})
        yield {
            "candidates": [{"content": {"parts": []}}],
            "usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 8},
        }

    monkeypatch.setattr(
        "backend.providers.gemini_provider._prepare_gemini_contents",
        lambda messages, cancel_event=None, api_key=None: (None, [{"parts": [{"text": "hi"}]}], []),
    )
    monkeypatch.setattr("backend.providers.gemini_provider._gemini_stream_sse", fake_stream_sse)
    provider = GeminiProvider(api_key="k", model="gemini-2.5-pro")
    events = list(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]),
        CancelToken(),
    ))
    assert events[-1].type == "done"
    assert events[-1].usage == {"prompt_tokens": 40, "completion_tokens": 8}


def test_llama_cpp_stream_usage_stays_none_by_design(monkeypatch):
    # llama.cpp stream chunks don't reliably carry usage - the done event's
    # usage is deliberately None (the counter falls back to its estimator).
    from backend.providers import LlamaCppProvider

    chunks = FakeSDKStream([
        {"choices": [{"delta": {"content": "Answer."}}]},
    ])
    import types

    fake_client = types.SimpleNamespace(
        create_chat_completion=lambda messages, stream=False, **kwargs: chunks
    )
    monkeypatch.setattr(
        "backend.providers.llama_cpp_provider._get_llama_cpp_client",
        lambda task, settings: fake_client,
    )
    monkeypatch.setattr(
        "backend.providers.llama_cpp_provider._assert_llama_cpp_message_support",
        lambda messages: None,
    )
    provider = LlamaCppProvider(settings={"chat_model_path": "m.gguf"})
    events = list(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}]),
        CancelToken(),
    ))
    assert events[-1].type == "done"
    assert events[-1].usage is None


# -- ADR-006 stage 6.8: transient-transport retry ------------------------------
#
# Distinct from Ollama's own reasoning-content retry: the transport layer
# wraps the WHOLE provider stream/complete call, retries only 429/5xx/
# connection-shaped failures, honors Retry-After, and never retries after
# the first delta reached on_chunk (or a cancellation, ever).


def _transient_error(message="transient boom", status_code=None, retry_after=None):
    error = RuntimeError(message)
    if status_code is not None:
        error.status_code = status_code
    if retry_after is not None:
        error.retry_after = retry_after
    return error


def _install_flaky_ollama_factory(monkeypatch, failures, events=None, fail_after_first_delta=False):
    """Swap chat_stream's OllamaProvider for a factory whose stream raises
    the scripted failures (one per attempt) before finally succeeding."""
    from backend.providers import ollama_provider as op_module

    remaining = list(failures)
    calls = {"streams": 0}

    class FlakyFactory:
        def __init__(self, **_kwargs):
            from backend.providers.base import ProviderCapabilities

            self.capabilities = ProviderCapabilities(streaming=True)

        def stream(self, request, cancel):
            calls["streams"] += 1
            if fail_after_first_delta:
                yield ProviderEvent("text", "partial ")
                raise remaining.pop(0)
            if remaining:
                raise remaining.pop(0)
                yield  # pragma: no cover - keeps this a generator
            for event in events or [ProviderEvent("text", "ok"), ProviderEvent("done", "ok")]:
                yield event

    monkeypatch.setattr(op_module, "OllamaProvider", FlakyFactory)
    monkeypatch.setattr(api_provider, "chat_stream", _REAL_CHAT_STREAM)
    return calls


def _capture_transport_sleeps(monkeypatch):
    sleeps = []
    monkeypatch.setattr(api_provider.time, "sleep", lambda seconds: sleeps.append(seconds))
    return sleeps


def test_429_with_retry_after_sleeps_exactly_that_long_then_succeeds(monkeypatch, ollama_mode):
    # THE stage exit criterion: Retry-After wins over the (smaller) jittered
    # backoff, exactly one sleep, then the retried stream succeeds.
    sleeps = _capture_transport_sleeps(monkeypatch)
    calls = _install_flaky_ollama_factory(
        monkeypatch, [_transient_error(status_code=429, retry_after=5.0)]
    )

    chunks = []
    response = api_provider.chat_stream(
        config.TASK_CHAT, [{"role": "user", "content": "hi"}],
        lambda delta, reset: chunks.append(delta),
    )

    assert response["message"]["content"] == "ok"
    assert chunks == ["ok"]
    assert sleeps == [5.0]
    assert calls["streams"] == 2


def test_500_is_retried_with_jittered_backoff(monkeypatch, ollama_mode):
    sleeps = _capture_transport_sleeps(monkeypatch)
    _install_flaky_ollama_factory(monkeypatch, [_transient_error(status_code=500)])

    response = api_provider.chat_stream(
        config.TASK_CHAT, [{"role": "user", "content": "hi"}], lambda d, r: None
    )

    assert response["message"]["content"] == "ok"
    assert len(sleeps) == 1
    assert 0.5 <= sleeps[0] <= 1.5  # base 1.0s with +/-50% jitter


def test_transport_errors_after_the_first_delta_are_never_retried(monkeypatch, ollama_mode):
    sleeps = _capture_transport_sleeps(monkeypatch)
    calls = _install_flaky_ollama_factory(
        monkeypatch, [_transient_error("server exploded", status_code=429)],
        fail_after_first_delta=True,
    )

    with pytest.raises(RuntimeError, match="server exploded"):
        api_provider.chat_stream(
            config.TASK_CHAT, [{"role": "user", "content": "hi"}], lambda d, r: None
        )
    assert sleeps == []
    assert calls["streams"] == 1


def test_request_cancelled_error_is_never_retried(monkeypatch, ollama_mode):
    sleeps = _capture_transport_sleeps(monkeypatch)
    calls = _install_flaky_ollama_factory(
        monkeypatch, [api_provider.RequestCancelledError("cancelled")]
    )

    with pytest.raises(api_provider.RequestCancelledError):
        api_provider.chat_stream(
            config.TASK_CHAT, [{"role": "user", "content": "hi"}], lambda d, r: None
        )
    assert sleeps == []
    assert calls["streams"] == 1


def test_non_transient_errors_are_never_retried(monkeypatch, ollama_mode):
    sleeps = _capture_transport_sleeps(monkeypatch)
    calls = _install_flaky_ollama_factory(monkeypatch, [_transient_error("a schema error")])

    with pytest.raises(RuntimeError, match="a schema error"):
        api_provider.chat_stream(
            config.TASK_CHAT, [{"role": "user", "content": "hi"}], lambda d, r: None
        )
    assert sleeps == []
    assert calls["streams"] == 1


def test_retries_are_capped_at_the_max_attempt_count(monkeypatch, ollama_mode):
    sleeps = _capture_transport_sleeps(monkeypatch)
    calls = _install_flaky_ollama_factory(
        monkeypatch,
        [_transient_error(status_code=503) for _ in range(5)],  # more than the cap
    )

    with pytest.raises(RuntimeError):
        api_provider.chat_stream(
            config.TASK_CHAT, [{"role": "user", "content": "hi"}], lambda d, r: None
        )
    assert len(sleeps) == api_provider._TRANSPORT_RETRY_MAX_ATTEMPTS  # 2 retries, 3 tries
    assert calls["streams"] == 1 + api_provider._TRANSPORT_RETRY_MAX_ATTEMPTS


def test_cancel_during_backoff_aborts_promptly(monkeypatch, ollama_mode):
    import time as time_module

    _install_flaky_ollama_factory(
        monkeypatch, [_transient_error(status_code=429, retry_after=10.0)]
    )
    cancel_event = threading.Event()
    timer = threading.Timer(0.05, cancel_event.set)
    timer.start()
    started = time_module.monotonic()
    try:
        with pytest.raises(api_provider.RequestCancelledError):
            api_provider.chat_stream(
                config.TASK_CHAT, [{"role": "user", "content": "hi"}], lambda d, r: None,
                cancellation_event=cancel_event,
            )
    finally:
        timer.cancel()
    # A 10s Retry-After must NOT be slept out - Event.wait wakes on cancel.
    assert time_module.monotonic() - started < 2.0


def test_blocking_complete_rides_the_same_transport_retry(monkeypatch):
    sleeps = _capture_transport_sleeps(monkeypatch)

    class FlakyBlocking:
        def __init__(self):
            self.calls = 0

        def complete(self, request, token):
            self.calls += 1
            if self.calls == 1:
                raise _transient_error(status_code=502)
            return "recovered"

    provider = FlakyBlocking()
    result = api_provider._complete_with_transport_retry(provider, None, None, None)
    assert result == "recovered"
    assert provider.calls == 2
    assert len(sleeps) == 1


def test_rest_http_errors_preserve_status_and_retry_after(monkeypatch):
    import email.message
    import io
    import urllib.error

    headers = email.message.Message()
    headers["Retry-After"] = "7"
    http_error = urllib.error.HTTPError(
        "https://api.anthropic.com/v1/messages", 429, "Too Many Requests",
        headers, io.BytesIO(b'{"error": {"message": "rate limited"}}'),
    )

    def raising_urlopen(request, timeout=None):
        raise http_error

    monkeypatch.setattr(api_provider.urllib.request, "urlopen", raising_urlopen)
    with pytest.raises(RuntimeError, match="rate limited") as excinfo:
        api_provider._anthropic_post_json("https://api.anthropic.com/v1/messages", {}, api_key="k")
    assert excinfo.value.status_code == 429
    assert excinfo.value.retry_after == 7.0
    assert api_provider._is_transient_transport_error(excinfo.value) is True
