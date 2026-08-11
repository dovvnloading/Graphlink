"""ADR-007 stage 7.1: the provider-neutral tool interface - ToolSpec/
ToolCall/the "tool_call" event, native tool calling on OpenAI, Anthropic,
Gemini, and Ollama (llama.cpp is deliberately out of this stage's scope -
see ProviderCapabilities.tools' own comment), and the two new generic
message roles (assistant `tool_calls`, `role: "tool"`) each provider
translates to its native shape.

The exit criterion this file proves per provider: a registered echo tool
round-trips - the model calls it, the (test-scripted) result is fed back as
a second turn, and the final answer reflects it - with only the SDK/
transport faked, mirroring the construction-fidelity pattern established in
test_providers.py.
"""

from __future__ import annotations

import json
import types
from unittest.mock import patch


import api_provider
import graphlink_task_config as config
from backend.providers import (
    AnthropicProvider,
    CancelToken,
    ChatRequest,
    GeminiProvider,
    OllamaProvider,
    OpenAIProvider,
    ToolCall,
    ToolSpec,
)

# The one tool every round-trip test registers: reflects its input back,
# proving the call's arguments and the fed-back result both actually
# traveled through the provider, not just that SOME tool_call fired.
ECHO_TOOL = ToolSpec(
    name="echo",
    description="Echoes the given message back.",
    input_schema={
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    },
)


def _collect(stream):
    events = list(stream)
    assert events[-1].type == "done", "every stream() call must end with exactly one done event"
    assert sum(1 for e in events if e.type == "done") == 1
    return events


class FakeOllamaStream:
    """Mirrors ollama's real chat(stream=True) return value closely enough
    for OllamaProvider.stream()'s `finally: stream.close()` - a bare
    iter([...]) has no .close() and blows up there, matching
    test_providers.py's own FakeOllamaStream convention."""

    def __init__(self, parts):
        self._iter = iter(parts)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._iter)

    def close(self):
        pass


# -- Ollama --------------------------------------------------------------------


def test_ollama_capabilities_tools_is_a_real_per_model_probe(monkeypatch):
    with patch("api_provider.ollama.show", return_value={"capabilities": ["completion", "tools"]}):
        assert OllamaProvider(model="tool-capable-model").capabilities.tools is True
    monkeypatch.setattr(api_provider, "_OLLAMA_CAPABILITY_CACHE", {})
    with patch("api_provider.ollama.show", return_value={"capabilities": ["completion"]}):
        assert OllamaProvider(model="no-tools-model").capabilities.tools is False


def test_ollama_capabilities_tools_defaults_false_when_show_is_unavailable(monkeypatch):
    monkeypatch.setattr(api_provider, "_OLLAMA_CAPABILITY_CACHE", {})
    with patch("api_provider.ollama.show", side_effect=RuntimeError("daemon unreachable")):
        assert OllamaProvider(model="unreachable-model").capabilities.tools is False


def test_ollama_stream_translates_tools_into_the_native_function_shape(monkeypatch):
    import ollama

    captured = {}

    def fake_chat(**kwargs):
        captured.update(kwargs)
        return FakeOllamaStream([{"message": {"content": "hi"}, "done": True, "prompt_eval_count": 1, "eval_count": 1}])

    monkeypatch.setattr(ollama, "chat", fake_chat)
    provider = OllamaProvider(model="m")
    list(provider.stream(ChatRequest(task=config.TASK_CHAT, messages=[], tools=(ECHO_TOOL,)), CancelToken()))

    assert captured["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Echoes the given message back.",
                "parameters": ECHO_TOOL.input_schema,
            },
        }
    ]


def test_ollama_round_trips_an_echo_tool_call(monkeypatch):
    import ollama

    calls = []

    def fake_chat(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            # First turn: the model asks to call `echo`. Ollama delivers
            # the whole call in one chunk (already-parsed arguments dict),
            # then the stream ends without a separate done:true chunk for
            # this attempt - the tool_calls chunk IS terminal.
            return FakeOllamaStream([{
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "echo", "arguments": {"message": "hello"}}}],
                },
                "done": False,
            }])
        # Second turn: the model has the tool result and answers.
        return FakeOllamaStream([{
            "message": {"content": "The echo said: hello"},
            "done": True,
            "prompt_eval_count": 5,
            "eval_count": 2,
        }])

    monkeypatch.setattr(ollama, "chat", fake_chat)
    provider = OllamaProvider(model="m")

    # Turn 1: model requests the tool call.
    events = _collect(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "echo hello"}], tools=(ECHO_TOOL,)),
        CancelToken(),
    ))
    tool_call_events = [e for e in events if e.type == "tool_call"]
    assert len(tool_call_events) == 1
    call = tool_call_events[0].tool_call
    assert isinstance(call, ToolCall)
    assert call.name == "echo"
    assert call.arguments == {"message": "hello"}
    assert call.id  # synthesized, but must be non-empty and usable as a correlation key
    assert events[-1].text == ""  # a pure tool-call turn has no answer text yet

    # Turn 2: the app appends the assistant's tool-call turn + the tool's
    # result, then calls stream() again - proving the round trip, not just
    # that a call was detected.
    messages = [
        {"role": "user", "content": "echo hello"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": call.id, "name": call.name, "arguments": call.arguments}]},
        {"role": "tool", "tool_call_id": call.id, "name": "echo", "content": "hello"},
    ]
    events2 = _collect(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=messages, tools=(ECHO_TOOL,)), CancelToken(),
    ))
    assert events2[-1].text == "The echo said: hello"
    assert events2[-1].usage == {"prompt_tokens": 5, "completion_tokens": 2}

    # The second call's messages carry Ollama's native tool_calls/tool
    # shapes, not the app's generic dicts verbatim.
    second_call_messages = calls[1]["messages"]
    assert second_call_messages[1] == {
        "role": "assistant", "content": "",
        "tool_calls": [{"function": {"name": "echo", "arguments": {"message": "hello"}}}],
    }
    assert second_call_messages[2] == {"role": "tool", "content": "hello"}


def test_ollama_tool_call_turn_reports_usage_when_the_terminal_chunk_carries_it(monkeypatch):
    """review-fix: a tool-calling chunk IS the terminal chunk (see
    _extract_tool_calls' own docstring) and Ollama marks it done with real
    token counts, same as a plain-answer turn - but the code used to break
    out before ever reading them, so every builder tool-call turn silently
    reported usage=None and the token budget went unenforced on real
    spend."""
    import ollama

    def fake_chat(**kwargs):
        return FakeOllamaStream([{
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": "echo", "arguments": {"message": "hi"}}}],
            },
            "done": True,
            "prompt_eval_count": 30,
            "eval_count": 8,
        }])

    monkeypatch.setattr(ollama, "chat", fake_chat)
    provider = OllamaProvider(model="m")

    events = _collect(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "echo hi"}], tools=(ECHO_TOOL,)),
        CancelToken(),
    ))

    assert events[-1].usage == {"prompt_tokens": 30, "completion_tokens": 8}


def test_ollama_tool_call_turn_never_enters_the_reasoning_retry_loop(monkeypatch):
    """A pure tool-call turn (no visible answer text) must NOT be treated
    as the "reasoning but no answer" retryable case - that retry exists for
    a genuinely different failure, and misfiring it here would silently
    eat a real tool call."""
    import ollama

    def fake_chat(**kwargs):
        return FakeOllamaStream([{
            "message": {"content": "", "tool_calls": [{"function": {"name": "echo", "arguments": {}}}]},
            "done": False,
        }])

    monkeypatch.setattr(ollama, "chat", fake_chat)
    provider = OllamaProvider(model="gpt-oss:20b")  # a reasoning-capable family
    events = _collect(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=[], tools=(ECHO_TOOL,)), CancelToken(),
    ))
    assert [e.type for e in events] == ["tool_call", "done"]


# -- OpenAI ----------------------------------------------------------------


class FakeSDKStream:
    """Iterable-with-close() stand-in for the OpenAI/Anthropic SDKs'
    Stream types, mirroring test_providers.py's own FakeSDKStream
    convention - shared across the OpenAI and Anthropic sections below
    since both just need close()-on-exhaustion."""

    def __init__(self, chunks):
        self._iter = iter(chunks)
        self.close_calls = 0

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._iter)

    def close(self):
        self.close_calls += 1


def _openai_chunk(content=None, tool_calls=None):
    """tool_calls: list of {"index", "id"?, "name"?, "arguments"?} - id/name
    omitted on continuation deltas, matching the real SDK only sending them
    on a call's first delta."""
    delta_fields = {"content": content, "tool_calls": None}
    if tool_calls is not None:
        delta_fields["tool_calls"] = [
            types.SimpleNamespace(
                index=tc["index"],
                id=tc.get("id"),
                function=types.SimpleNamespace(name=tc.get("name"), arguments=tc.get("arguments")),
            )
            for tc in tool_calls
        ]
    return types.SimpleNamespace(
        usage=None,
        choices=[types.SimpleNamespace(delta=types.SimpleNamespace(**delta_fields))],
    )


def _fake_openai_client(stream_sequence):
    """stream_sequence: one chunk-list per expected create() call, in order -
    lets a round-trip test script turn 1's tool call and turn 2's answer as
    two distinct scripted responses, same shape as Ollama's own `calls`-
    indexed fake_chat."""
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return FakeSDKStream(stream_sequence[len(calls) - 1])

    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )
    return client, calls


def test_openai_capabilities_tools_is_true_unconditionally():
    assert OpenAIProvider(client=None, model="gpt-5").capabilities.tools is True


def test_openai_stream_translates_tools_into_the_native_function_shape():
    client, calls = _fake_openai_client([[_openai_chunk(content="hi")]])
    provider = OpenAIProvider(client=client, model="gpt-5")
    list(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=[], tools=(ECHO_TOOL,)), CancelToken(),
    ))

    assert calls[0]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Echoes the given message back.",
                "parameters": ECHO_TOOL.input_schema,
            },
        }
    ]


def test_openai_round_trips_an_echo_tool_call():
    client, calls = _fake_openai_client([
        [
            # Arguments split across two deltas - proves the char-by-char
            # JSON buffer, not just a single-chunk pass-through. id/name
            # only appear on the call's first delta, per the real wire shape.
            _openai_chunk(tool_calls=[{"index": 0, "id": "call_abc", "name": "echo", "arguments": '{"message": "hel'}]),
            _openai_chunk(tool_calls=[{"index": 0, "arguments": 'lo"}'}]),
        ],
        [_openai_chunk(content="The echo said: hello")],
    ])
    provider = OpenAIProvider(client=client, model="gpt-5")

    # Turn 1: model requests the tool call.
    events = _collect(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "echo hello"}], tools=(ECHO_TOOL,)),
        CancelToken(),
    ))
    tool_call_events = [e for e in events if e.type == "tool_call"]
    assert len(tool_call_events) == 1
    call = tool_call_events[0].tool_call
    assert isinstance(call, ToolCall)
    assert call.id == "call_abc"
    assert call.name == "echo"
    assert call.arguments == {"message": "hello"}
    assert events[-1].text == ""  # a pure tool-call turn has no answer text yet

    # Turn 2: the app appends the assistant's tool-call turn + the tool's
    # result, then calls stream() again - proving the round trip.
    messages = [
        {"role": "user", "content": "echo hello"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": call.id, "name": call.name, "arguments": call.arguments}]},
        {"role": "tool", "tool_call_id": call.id, "name": "echo", "content": "hello"},
    ]
    events2 = _collect(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=messages, tools=(ECHO_TOOL,)), CancelToken(),
    ))
    assert events2[-1].text == "The echo said: hello"

    # The second call's messages carry OpenAI's native tool_calls/tool
    # shapes (arguments re-serialized to a JSON string), not the app's
    # generic dicts verbatim.
    second_call_messages = calls[1]["messages"]
    assert second_call_messages[1] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "call_abc",
            "type": "function",
            "function": {"name": "echo", "arguments": json.dumps({"message": "hello"})},
        }],
    }
    assert second_call_messages[2] == {"role": "tool", "tool_call_id": "call_abc", "content": "hello"}


# -- Anthropic ---------------------------------------------------------------


def _fake_anthropic_client(event_sequence):
    """event_sequence: one list of raw SSE-shape dict events per expected
    create() call - the same dict wire shape test_providers.py's own
    _anthropic_raw_events uses (SDK and REST transports share it, both read
    through _extract_response_field)."""
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return FakeSDKStream(event_sequence[len(calls) - 1])

    client = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
    return client, calls


def test_anthropic_capabilities_tools_is_true_unconditionally():
    assert AnthropicProvider(client=None, api_key="k", model="claude-opus-5").capabilities.tools is True


def test_anthropic_stream_translates_tools_into_the_native_shape():
    client, calls = _fake_anthropic_client([[
        {"type": "message_start", "message": {"role": "assistant"}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}},
        {"type": "message_stop"},
    ]])
    provider = AnthropicProvider(client=client, api_key="k", model="claude-opus-5")
    list(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=[], tools=(ECHO_TOOL,)), CancelToken(),
    ))

    assert calls[0]["tools"] == [
        {"name": "echo", "description": "Echoes the given message back.", "input_schema": ECHO_TOOL.input_schema}
    ]


def test_anthropic_round_trips_an_echo_tool_call():
    client, calls = _fake_anthropic_client([
        [
            # Arguments split across two input_json_delta events - proves
            # the accumulate-then-parse buffer, not a single-event pass-
            # through. id/name arrive on content_block_start only.
            {"type": "message_start", "message": {"role": "assistant"}},
            {
                "type": "content_block_start", "index": 0,
                "content_block": {"type": "tool_use", "id": "toolu_abc", "name": "echo"},
            },
            {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"message": "hel'}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": 'lo"}'}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
            {"type": "message_stop"},
        ],
        [
            {"type": "message_start", "message": {"role": "assistant"}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "The echo said: hello"}},
            {"type": "message_stop"},
        ],
    ])
    provider = AnthropicProvider(client=client, api_key="k", model="claude-opus-5")

    # Turn 1: model requests the tool call.
    events = _collect(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "echo hello"}], tools=(ECHO_TOOL,)),
        CancelToken(),
    ))
    tool_call_events = [e for e in events if e.type == "tool_call"]
    assert len(tool_call_events) == 1
    call = tool_call_events[0].tool_call
    assert isinstance(call, ToolCall)
    assert call.id == "toolu_abc"
    assert call.name == "echo"
    assert call.arguments == {"message": "hello"}
    assert events[-1].text == ""  # a pure tool-call turn has no answer text yet

    # Turn 2: the app appends the assistant's tool-call turn + the tool's
    # result, then calls stream() again - proving the round trip.
    messages = [
        {"role": "user", "content": "echo hello"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": call.id, "name": call.name, "arguments": call.arguments}]},
        {"role": "tool", "tool_call_id": call.id, "name": "echo", "content": "hello"},
    ]
    events2 = _collect(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=messages, tools=(ECHO_TOOL,)), CancelToken(),
    ))
    assert events2[-1].text == "The echo said: hello"

    # The second call's messages carry Anthropic's native tool_use/
    # tool_result shapes, not the app's generic dicts verbatim - and the
    # tool result travels as a "user"-role message (Anthropic has no
    # separate "tool" role).
    second_call_messages = calls[1]["messages"]
    assert second_call_messages[1] == {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "toolu_abc", "name": "echo", "input": {"message": "hello"}}],
    }
    assert second_call_messages[2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "toolu_abc", "content": "hello"}],
    }


def test_anthropic_tool_call_turn_reports_usage(monkeypatch):
    """review-fix: prompt_tokens/completion_tokens are collected from
    message_start/message_delta regardless of how the turn ends, but the
    tool-call short-circuit dropped them before yielding "done" - every
    builder tool-call turn silently reported usage=None and the token
    budget went unenforced on real spend."""
    client, calls = _fake_anthropic_client([[
        {"type": "message_start", "message": {"role": "assistant", "usage": {"input_tokens": 40}}},
        {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "tool_use", "id": "toolu_xyz", "name": "echo"},
        },
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"message": "hi"}'}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 12}},
        {"type": "message_stop"},
    ]])
    provider = AnthropicProvider(client=client, api_key="k", model="claude-opus-5")

    events = _collect(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "echo hi"}], tools=(ECHO_TOOL,)),
        CancelToken(),
    ))

    assert events[-1].usage == {"prompt_tokens": 40, "completion_tokens": 12}


# -- Gemini --------------------------------------------------------------------
#
# Unlike the other three sections, Gemini's wire shapes here are
# documentation-only (no SDK in this repo to verify against - GeminiProvider's
# own capabilities.tools comment flags the same caveat), so these tests pin
# behavior against the PUBLIC REST contract, not an installed library's types.


def _gemini_sse_payload(*parts):
    return {"candidates": [{"content": {"parts": list(parts)}}]}


def test_gemini_capabilities_tools_is_true_unconditionally():
    assert GeminiProvider(api_key="k", model="gemini-2.5-pro").capabilities.tools is True


def test_gemini_stream_translates_tools_into_the_native_function_declarations_shape(monkeypatch):
    sse_calls = {}

    def fake_stream_sse(url, body, timeout=120, cancel_event=None, api_key=None):
        sse_calls.update(body=body)
        yield _gemini_sse_payload({"text": "hi"})

    monkeypatch.setattr("backend.providers.gemini_provider._gemini_stream_sse", fake_stream_sse)
    provider = GeminiProvider(api_key="k", model="gemini-2.5-pro")
    list(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}], tools=(ECHO_TOOL,)),
        CancelToken(),
    ))

    assert sse_calls["body"]["tools"] == [{
        "functionDeclarations": [
            {"name": "echo", "description": "Echoes the given message back.", "parameters": ECHO_TOOL.input_schema},
        ],
    }]


def test_gemini_tool_call_turn_reports_usage(monkeypatch):
    """review-fix: usageMetadata is collected from every SSE payload
    (including the trailing frame of a function-call response) but the
    tool-call short-circuit dropped it before yielding "done" - every
    builder tool-call turn silently reported usage=None and the token
    budget went unenforced on real spend."""
    def fake_stream_sse(url, body, timeout=120, cancel_event=None, api_key=None):
        yield {
            "candidates": [{"content": {"parts": [
                {"functionCall": {"name": "echo", "args": {"message": "hi"}}},
            ]}}],
            "usageMetadata": {"promptTokenCount": 22, "candidatesTokenCount": 6},
        }

    monkeypatch.setattr("backend.providers.gemini_provider._gemini_stream_sse", fake_stream_sse)
    provider = GeminiProvider(api_key="k", model="gemini-2.5-pro")

    events = _collect(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "echo hi"}], tools=(ECHO_TOOL,)),
        CancelToken(),
    ))

    assert events[-1].usage == {"prompt_tokens": 22, "completion_tokens": 6}


def test_gemini_round_trips_an_echo_tool_call(monkeypatch):
    calls = []

    def fake_stream_sse(url, body, timeout=120, cancel_event=None, api_key=None):
        calls.append(body)
        if len(calls) == 1:
            # Gemini delivers a function call whole in one part - no
            # incremental args accumulation, unlike OpenAI/Anthropic.
            yield {"candidates": [{"content": {"parts": [
                {"functionCall": {"name": "echo", "args": {"message": "hello"}}},
            ]}}]}
        else:
            yield _gemini_sse_payload({"text": "The echo said: hello"})

    monkeypatch.setattr("backend.providers.gemini_provider._gemini_stream_sse", fake_stream_sse)
    provider = GeminiProvider(api_key="k", model="gemini-2.5-pro")

    # Turn 1: model requests the tool call.
    events = _collect(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "echo hello"}], tools=(ECHO_TOOL,)),
        CancelToken(),
    ))
    tool_call_events = [e for e in events if e.type == "tool_call"]
    assert len(tool_call_events) == 1
    call = tool_call_events[0].tool_call
    assert isinstance(call, ToolCall)
    assert call.id  # synthesized (Gemini gives no native id), but non-empty
    assert call.name == "echo"
    assert call.arguments == {"message": "hello"}
    assert events[-1].text == ""  # a pure tool-call turn has no answer text yet

    # Turn 2: the app appends the assistant's tool-call turn + the tool's
    # result, then calls stream() again - proving the round trip.
    messages = [
        {"role": "user", "content": "echo hello"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": call.id, "name": call.name, "arguments": call.arguments}]},
        {"role": "tool", "tool_call_id": call.id, "name": "echo", "content": "hello"},
    ]
    events2 = _collect(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=messages, tools=(ECHO_TOOL,)), CancelToken(),
    ))
    assert events2[-1].text == "The echo said: hello"

    # The second call's contents carry Gemini's native functionCall/
    # functionResponse shapes, not the app's generic dicts verbatim - and
    # the tool result travels on Gemini's own dedicated "function" role.
    second_call_contents = calls[1]["contents"]
    assert second_call_contents[1] == {
        "role": "model",
        "parts": [{"functionCall": {"name": "echo", "args": {"message": "hello"}}}],
    }
    assert second_call_contents[2] == {
        "role": "function",
        "parts": [{"functionResponse": {"name": "echo", "response": {"result": "hello"}}}],
    }
