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
    assert response == {"message": {"content": "from the fake", "role": "assistant"}}
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
    stage (6.4 flips streaming), never a drive-by."""
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
        "openai":    (False, True,  True,  True,  False),  # C4: vision+audio real; image_gen probed from the client (None here)
        "anthropic": (False, True,  True,  False, False),
        "gemini":    (False, True,  True,  True,  True),
        "llama_cpp": (False, False, False, False, False),
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
    assert captured["system"] == "be brief"

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

    assert response == {"message": {"content": "LlamaCppProvider answer", "role": "assistant"}}
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


def test_all_four_new_providers_satisfy_the_protocol_and_stream_exactly_one_done():
    """6.3 review fix: protocol conformance was only pinned for FakeProvider
    and Ollama; the transitional stream()-wraps-complete() shape (chat_stream's
    documented one-full-chunk fallback) was never asserted for the new four."""
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

    client, _ = _fake_openai_client("full text")
    provider = OpenAIProvider(client=client, model="gpt-5")
    events = list(provider.stream(
        ChatRequest(task=config.TASK_TITLE, messages=[{"role": "user", "content": "hi"}]),
        CancelToken(),
    ))
    assert events == [ProviderEvent("done", "full text")]  # exactly one terminal event


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
