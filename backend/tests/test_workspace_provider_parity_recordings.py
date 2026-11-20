"""Deterministic provider-wire recordings for the real workspace agent turn path.

The fixture contains transport-shaped responses, not a scripted Provider:
each case runs the actual adapter, chat_turn_with_tools' tool-name/fallback
boundary and ToolRegistry's scope/approval decisions. No live inference.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import api_provider
import graphlink_task_config as config
from backend.providers.anthropic_provider import AnthropicProvider
from backend.providers.gemini_provider import GeminiProvider
from backend.providers.llama_cpp_provider import LlamaCppProvider
from backend.providers.ollama_provider import OllamaProvider
from backend.providers.openai_provider import OpenAIProvider
from backend.providers.base import CancelToken, ChatRequest, ToolSpec
from backend.tools import FS_WRITE, RunContext, ToolRegistry, ToolResult
from graphlink_model_catalog import ModelRef


CASES = ("openai", "anthropic", "gemini", "compatible", "ollama", "llama")
ARGS = {"path": "notes.txt", "content": "hello"}
WRITE = ToolSpec(
    name="fs.write",
    description="Write text to a workspace-confined file",
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    },
)
MESSAGES = [
    {"role": "system", "content": "Work in the granted workspace and use the offered tools."},
    {"role": "user", "content": "Write hello to notes.txt."},
]


@pytest.fixture(scope="module")
def recordings():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "workspace_provider_parity.json").read_text(encoding="utf-8")
    )


class RecordedStream:
    def __init__(self, frames):
        self.frames = frames
        self.closed = False

    def __iter__(self):
        return iter(self.frames)

    def close(self):
        self.closed = True


def _sdk_object(value):
    """Turn recorded JSON into the OpenAI SDK's attribute-accessed shapes."""
    if isinstance(value, list):
        return [_sdk_object(item) for item in value]
    if isinstance(value, dict):
        return SimpleNamespace(**{name: _sdk_object(item) for name, item in value.items()})
    return value


def _provider_and_requests(monkeypatch, kind, recorded_turns):
    """Replace only the transport/model-loading seam, not the provider adapter."""
    calls = []
    turns = iter(recorded_turns)

    if kind in {"openai", "compatible"}:
        def create(**kwargs):
            calls.append(kwargs)
            return RecordedStream([_sdk_object(frame) for frame in next(turns)])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        provider_name = "OpenAI" if kind == "openai" else config.API_PROVIDER_OPENAI
        provider = OpenAIProvider(client=client, model="unit-model", provider_name=provider_name)
    elif kind == "anthropic":
        def create(**kwargs):
            calls.append(kwargs)
            return RecordedStream(next(turns))
        provider = AnthropicProvider(
            client=SimpleNamespace(messages=SimpleNamespace(create=create)),
            api_key="not-a-real-key", model="claude-test",
        )
        provider_name = config.API_PROVIDER_ANTHROPIC
    elif kind == "gemini":
        import backend.providers.gemini_provider as gemini_module

        def sse(endpoint, body, **kwargs):
            calls.append({"endpoint": endpoint, "body": body, **kwargs})
            return RecordedStream(next(turns))
        monkeypatch.setattr(gemini_module, "_gemini_stream_sse", sse)
        provider = GeminiProvider(api_key="not-a-real-key", model="gemini-2.5-flash")
        provider_name = config.API_PROVIDER_GEMINI
    elif kind == "ollama":
        import ollama
        import backend.providers.ollama_provider as ollama_module

        monkeypatch.setattr(ollama_module, "ollama_supports_tools", lambda model: True)
        monkeypatch.setattr(ollama_module, "ollama_supports_embedding", lambda model: False)

        def chat(**kwargs):
            calls.append(kwargs)
            return RecordedStream(next(turns))
        monkeypatch.setattr(ollama, "chat", chat)
        provider = OllamaProvider(model="unit-model", reasoning_level="off", context_window=4096)
        provider_name = config.LOCAL_PROVIDER_OLLAMA
    elif kind == "llama":
        import backend.providers.llama_cpp_provider as llama_module

        class Client:
            def create_chat_completion(self, **kwargs):
                calls.append(kwargs)
                return RecordedStream(next(turns))

        client = Client()
        monkeypatch.setattr(llama_module, "_get_llama_cpp_client", lambda task, settings: client)
        provider = LlamaCppProvider(
            settings={"chat_model_path": "unit.gguf", "title_model_path": "unit.gguf", "reasoning_level": "off"}
        )
        provider_name = config.LOCAL_PROVIDER_LLAMACPP
    else:
        raise ValueError(kind)

    monkeypatch.setattr(api_provider, "_provider_for_model_ref", lambda ref, state: provider)
    return provider, provider_name, calls


def _turn(provider_name, messages):
    return api_provider.chat_turn_with_tools(
        config.TASK_CHAT,
        messages,
        (WRITE,),
        model_ref=ModelRef(provider_name, "unit-model"),
        json_tool_fallback=True,
    )


@pytest.mark.parametrize("kind", CASES)
def test_recorded_two_turn_tool_result_approval_and_model_settings(kind, recordings, monkeypatch):
    _provider, provider_name, requests = _provider_and_requests(
        monkeypatch, kind, [recordings[kind]["first"], recordings[kind]["second"]]
    )
    first = _turn(provider_name, MESSAGES)
    assert first["message"]["content"] == ""
    assert first["usage"] == (
        None if kind == "llama" else {"prompt_tokens": 15, "completion_tokens": 5}
    )
    (call,) = first["tool_calls"]
    assert call.name == WRITE.name  # cloud aliases never escape the adapter
    assert call.arguments == ARGS
    assert call.id

    attempts = []
    executions = []

    async def handler(actual, ctx):
        executions.append(actual.arguments)
        return ToolResult("written")

    async def deny(actual):
        attempts.append(("deny", actual.name))
        return False

    async def approve(actual):
        attempts.append(("approve", actual.name))
        return True

    registry = ToolRegistry()
    registry.register(WRITE, handler, scopes={FS_WRITE}, approval="once")
    base = dict(granted_scopes=frozenset({FS_WRITE}), offered_tool_names=frozenset({WRITE.name}))
    rejected = asyncio.run(registry.invoke(call, RunContext(request_approval=deny, **base)))
    assert rejected.is_error
    assert executions == [], "denied calls must never execute"
    result = asyncio.run(registry.invoke(call, RunContext(request_approval=approve, **base)))
    assert not result.is_error and result.content == "written"
    assert executions == [ARGS] and attempts == [("deny", WRITE.name), ("approve", WRITE.name)]

    history = [
        *MESSAGES,
        {**first["message"], "tool_calls": [call.history_entry()]},
        {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": result.content},
    ]
    second = _turn(provider_name, history)
    assert second["message"]["content"] == "Saved."
    assert second["tool_calls"] == []
    assert second["usage"] == (
        None if kind == "llama" else {"prompt_tokens": 21, "completion_tokens": 3}
    )
    assert len(requests) == 2

    if kind in {"openai", "compatible"}:
        assert requests[0]["model"] == "unit-model"
        assert requests[0]["stream"] is True
        assert requests[0]["stream_options"] == {"include_usage": True}
        if kind == "openai":
            assert requests[0]["tools"][0]["function"]["name"] == "fs_write"
            assert requests[1]["messages"][-1]["role"] == "tool"
            assert requests[1]["messages"][-2]["tool_calls"][0]["function"]["name"] == "fs_write"
        else:
            assert "tools" not in requests[0]
            assert requests[0]["response_format"]["type"] == "json_schema"
            assert requests[0]["response_format"]["json_schema"]["strict"] is True
            assert requests[1]["messages"][-1]["role"] == "user"
    elif kind == "anthropic":
        assert requests[0]["model"] == "claude-test"
        assert requests[0]["tools"][0]["name"] == "fs_write"
        assert requests[1]["messages"][-1]["content"][0]["type"] == "tool_result"
        assert requests[1]["messages"][-1]["content"][0]["tool_use_id"] == call.id
        assert requests[0]["system"][0]["cache_control"]["type"] == "ephemeral"
    elif kind == "gemini":
        assert "gemini-2.5-flash:streamGenerateContent" in requests[0]["endpoint"]
        assert requests[0]["body"]["tools"][0]["functionDeclarations"][0]["name"] == "fs_write"
        parts = requests[1]["body"]["contents"][-1]["parts"]
        assert parts[-1]["functionResponse"]["name"] == "fs_write"
        assert parts[-1]["functionResponse"]["id"] == "fc_parity_1"
        assert requests[1]["body"]["contents"][-2]["parts"][-1]["thoughtSignature"] == "opaque-signature"
    elif kind == "ollama":
        assert requests[0]["model"] == "unit-model" and requests[0]["stream"] is True
        assert requests[0]["options"]["num_ctx"] == 4096
        assert requests[0]["tools"][0]["function"]["name"] == WRITE.name
        assert requests[1]["messages"][-1]["tool_name"] == WRITE.name
        assert requests[1]["messages"][-1]["content"] == "written"
    elif kind == "llama":
        assert requests[0]["stream"] is True
        assert requests[0]["response_format"]["type"] == "json_object"
        assert requests[0]["response_format"]["schema"]["additionalProperties"] is False
        assert "tools" not in requests[0]
        assert requests[1]["messages"][-1]["role"] == "user"
        assert "Tool results (untrusted data" in requests[1]["messages"][-1]["content"]


@pytest.mark.parametrize("typed_events", (False, True))
def test_recorded_anthropic_signed_thinking_and_redaction_keep_block_order(
    typed_events, recordings, monkeypatch,
):
    first_frames = json.loads(json.dumps(recordings["anthropic"]["first"]))
    # Place the existing tool-use block after three preceding content blocks.
    for frame in first_frames:
        if "index" in frame:
            frame["index"] += 3
    first_frames[1:1] = [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "Need to inspect "}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "notes.txt first."}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "signature_delta", "signature": "opaque-signature"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "redacted_thinking", "data": "opaque-redacted"}},
        {"type": "content_block_stop", "index": 1},
        {"type": "content_block_start", "index": 2,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 2,
         "delta": {"type": "text_delta", "text": "Writing the file."}},
        {"type": "content_block_stop", "index": 2},
    ]
    if typed_events:
        # Exercise both SDK attribute objects and REST/SSE dict payloads.
        first_frames = [_sdk_object(frame) for frame in first_frames]
    _, provider_name, requests = _provider_and_requests(
        monkeypatch, "anthropic", [first_frames, recordings["anthropic"]["second"]]
    )
    first = _turn(provider_name, MESSAGES)
    (call,) = first["tool_calls"]
    expected_blocks = [
        {"type": "thinking", "thinking": "Need to inspect notes.txt first.",
         "signature": "opaque-signature"},
        {"type": "redacted_thinking", "data": "opaque-redacted"},
        {"type": "text", "text": "Writing the file."},
        {"type": "tool_use", "id": call.id, "name": "fs_write", "input": ARGS},
    ]
    assert first["message"]["provider_replay"]["content_blocks"] == expected_blocks
    history = [
        *MESSAGES,
        {**first["message"], "tool_calls": [call.history_entry()]},
        {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": "written"},
    ]
    second = _turn(provider_name, history)
    assert second["message"]["content"] == "Saved."
    assert "provider_replay" not in second["message"]
    # The original signed content travels intact, not a reconstruction
    # with lost signatures or thinking moved after the tool_use block.
    assert requests[1]["messages"][-2]["content"] == expected_blocks
    assert requests[1]["messages"][-1]["content"][0]["tool_use_id"] == call.id


def test_signed_claude_history_remaps_tool_uses_when_alias_collides(recordings, monkeypatch):
    """A newly offered legal fs_write must never impersonate old fs.write."""
    frames = json.loads(json.dumps(recordings["anthropic"]["first"]))
    for frame in frames:
        if "index" in frame:
            frame["index"] += 1
    frames[1:1] = [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "Use the workspace write tool."}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "signature_delta", "signature": "signed-original"}},
        {"type": "content_block_stop", "index": 0},
    ]
    _, provider_name, requests = _provider_and_requests(
        monkeypatch, "anthropic", [frames, recordings["anthropic"]["second"]],
    )
    first = _turn(provider_name, MESSAGES)
    (call,) = first["tool_calls"]
    original_blocks = first["message"]["provider_replay"]["content_blocks"]
    assert [b["name"] for b in original_blocks if b["type"] == "tool_use"] == ["fs_write"]

    history = [
        *MESSAGES,
        {**first["message"], "tool_calls": [call.history_entry()]},
        {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": "written"},
    ]
    conflicting_tool = ToolSpec(name="fs_write", description="A DIFFERENT tool", input_schema={"type": "object"})
    api_provider.chat_turn_with_tools(
        config.TASK_CHAT, history, (WRITE, conflicting_tool),
        model_ref=ModelRef(provider_name, "unit-model"), json_tool_fallback=True,
    )
    assert {tool["name"] for tool in requests[1]["tools"]} == {"fs_write", "fs_write_1"}
    replayed = requests[1]["messages"][-2]["content"]
    assert replayed[0] == {
        "type": "thinking", "thinking": "Use the workspace write tool.",
        "signature": "signed-original",
    }
    assert [(b["id"], b["name"], b["input"]) for b in replayed if b["type"] == "tool_use"] == [
        (call.id, "fs_write_1", ARGS),
    ]
    # The persisted historical wire name stays intact: remapping is purely
    # a request-boundary copy, not a mutation of signed stored history.
    assert [b["name"] for b in original_blocks if b["type"] == "tool_use"] == ["fs_write"]


def test_inconsistent_signed_tool_use_falls_back_to_canonical_calls():
    from backend.providers.tool_names import cloud_tool_names

    history = [{
        "role": "assistant",
        "tool_calls": [{"id": "toolu_1", "name": "fs.write", "arguments": ARGS}],
        "_anthropic_content_blocks": [
            {"type": "thinking", "thinking": "Thought.", "signature": "signed"},
            {"type": "tool_use", "id": "toolu_1", "name": "fs_write", "input": {"path": "other.txt"}},
        ],
    }]
    _, translated, _ = cloud_tool_names((WRITE,), history)
    assert translated[0]["tool_calls"][0]["name"] == "fs_write"
    assert "_anthropic_content_blocks" not in translated[0]


def test_recorded_ollama_streamed_thinking_replays_with_native_tools(recordings, monkeypatch):
    frames = json.loads(json.dumps(recordings["ollama"]["first"]))
    frames[0:0] = [
        {"message": {"thinking": "Need to inspect "}, "done": False},
        {"message": {"thinking": "notes.txt first."}, "done": False},
    ]
    _, provider_name, requests = _provider_and_requests(
        monkeypatch, "ollama", [frames, recordings["ollama"]["second"]]
    )
    first = _turn(provider_name, MESSAGES)
    (call,) = first["tool_calls"]
    assert first["message"]["provider_replay"] == {
        "provider": provider_name, "model": "unit-model",
        "thinking": "Need to inspect notes.txt first.",
    }
    history = [
        *MESSAGES,
        {**first["message"], "tool_calls": [call.history_entry()]},
        {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": "written"},
    ]
    second = _turn(provider_name, history)
    assert second["message"]["content"] == "Saved."
    assert "provider_replay" not in second["message"]
    previous_assistant = requests[1]["messages"][-2]
    assert previous_assistant["content"] == ""
    assert previous_assistant["thinking"] == "Need to inspect notes.txt first."
    assert previous_assistant["tool_calls"][0]["function"] == {
        "name": WRITE.name, "arguments": ARGS,
    }
    assert requests[1]["messages"][-1]["tool_name"] == WRITE.name


@pytest.mark.parametrize("kind", ("anthropic", "ollama"))
@pytest.mark.parametrize("mismatch", ("provider", "model"))
def test_private_tool_replay_is_scoped_to_origin(kind, mismatch, recordings, monkeypatch):
    _, provider_name, requests = _provider_and_requests(
        monkeypatch, kind, [recordings[kind]["second"]]
    )
    metadata = {
        "provider": provider_name,
        "model": "claude-test" if kind == "anthropic" else "unit-model",
        "content_blocks": [
            {"type": "thinking", "thinking": "private-thought", "signature": "opaque-signature"},
            {"type": "tool_use", "id": "saved-call", "name": "fs_write", "input": ARGS},
        ],
        "thinking": "private-thought",
    }
    metadata[mismatch] = "different-origin"
    history = [
        *MESSAGES,
        {"role": "assistant", "content": "", "provider_replay": metadata,
         "tool_calls": [{"id": "saved-call", "name": WRITE.name, "arguments": ARGS}]},
        {"role": "tool", "tool_call_id": "saved-call",
         "name": WRITE.name, "content": "written"},
    ]
    _turn(provider_name, history)
    previous_assistant = requests[0]["messages"][-2]
    if kind == "anthropic":
        assert previous_assistant["content"] == [
            {"type": "tool_use", "id": "saved-call", "name": "fs_write", "input": ARGS}
        ]
    else:
        assert "thinking" not in previous_assistant
    assert "provider_replay" not in str(requests[0])
    assert "private-thought" not in str(requests[0])


def test_private_tool_replay_not_forwarded_to_another_provider(recordings, monkeypatch):
    _, provider_name, requests = _provider_and_requests(
        monkeypatch, "openai", [recordings["openai"]["second"]]
    )
    history = [
        *MESSAGES,
        {"role": "assistant", "content": "",
         "provider_replay": {
             "provider": config.API_PROVIDER_ANTHROPIC, "model": "claude-test",
             "content_blocks": [
                 {"type": "thinking", "thinking": "private-thought", "signature": "opaque-signature"},
             ],
         },
         "tool_calls": [{"id": "saved-call", "name": WRITE.name, "arguments": ARGS}]},
        {"role": "tool", "tool_call_id": "saved-call", "name": WRITE.name, "content": "written"},
    ]
    _turn(provider_name, history)
    assert "provider_replay" not in str(requests[0])
    assert "opaque-signature" not in str(requests[0])
    assert "private-thought" not in str(requests[0])


@pytest.mark.parametrize("kind", CASES)
def test_recorded_truncated_stream_never_commits_tool_or_final_answer(kind, recordings, monkeypatch):
    # Remove each transport's mandatory end marker from an otherwise valid
    # native/JSON tool response. The dispatcher must never execute torn calls.
    frames = json.loads(json.dumps(recordings[kind]["first"]))
    if kind in {"openai", "compatible", "llama"}:
        for frame in frames:
            for choice in frame["choices"]:
                choice["finish_reason"] = None
    elif kind == "anthropic":
        frames = [frame for frame in frames if frame["type"] != "message_stop"]
    elif kind == "gemini":
        for frame in frames:
            for candidate in frame["candidates"]:
                candidate.pop("finishReason", None)
    elif kind == "ollama":
        for frame in frames:
            frame["done"] = False

    _, provider_name, _ = _provider_and_requests(monkeypatch, kind, [frames])
    with pytest.raises(RuntimeError, match="ended unexpectedly|without a terminal"):
        _turn(provider_name, MESSAGES)


def test_recorded_ollama_two_tool_call_chunks_keep_both_calls_and_terminal_usage(monkeypatch):
    import ollama
    import backend.providers.ollama_provider as module

    monkeypatch.setattr(module, "ollama_supports_tools", lambda model: True)
    monkeypatch.setattr(module, "ollama_supports_embedding", lambda model: False)
    frames = [
        {"message": {"tool_calls": [{"function": {"name": "fs.read", "arguments": {"path": "a"}}}]}, "done": False},
        {"message": {"tool_calls": [{"function": {"name": "fs.read", "arguments": {"path": "b"}}}]}, "done": False},
        {"message": {}, "done": True, "prompt_eval_count": 8, "eval_count": 3},
    ]
    monkeypatch.setattr(ollama, "chat", lambda **kwargs: RecordedStream(frames))
    provider = OllamaProvider(model="unit-model", reasoning_level="off")
    events = list(provider.stream(
        ChatRequest(task=config.TASK_CHAT, messages=[{"role": "user", "content": "read a and b"}],
                    tools=(ToolSpec("fs.read", "Read", {"type": "object"}),)),
        CancelToken(),
    ))
    tool_calls = [event.tool_call for event in events if event.type == "tool_call"]
    assert [(call.id, call.name, call.arguments) for call in tool_calls] == [
        ("call_0", "fs.read", {"path": "a"}),
        ("call_1", "fs.read", {"path": "b"}),
    ]
    assert events[-1].type == "done"
    assert events[-1].usage == {"prompt_tokens": 8, "completion_tokens": 3}


@pytest.mark.parametrize("kind", ("openai", "anthropic", "gemini", "ollama"))
def test_native_recording_rejects_non_object_arguments(kind, recordings, monkeypatch):
    frames = json.loads(json.dumps(recordings[kind]["first"]))
    if kind == "openai":
        frames[0]["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"] = '["bad"]'
        frames[1]["choices"][0]["delta"]["tool_calls"] = []
    elif kind == "anthropic":
        frames[2]["delta"]["partial_json"] = '["bad"]'
        frames[3]["delta"]["partial_json"] = ""
    elif kind == "gemini":
        frames[0]["candidates"][0]["content"]["parts"][0]["functionCall"]["args"] = ["bad"]
    else:
        frames[0]["message"]["tool_calls"][0]["function"]["arguments"] = ["bad"]
    provider, _, _ = _provider_and_requests(monkeypatch, kind, [frames])
    with pytest.raises(RuntimeError, match="not a JSON object"):
        list(provider.stream(
            ChatRequest(task=config.TASK_CHAT, messages=MESSAGES, tools=(WRITE,)),
            CancelToken(),
        ))


@pytest.mark.parametrize("kind", CASES)
@pytest.mark.parametrize("tool_turn", (False, True))
def test_recorded_output_limit_never_commits_agent_final_or_tool_turn(
    kind, tool_turn, recordings, monkeypatch,
):
    """All six wire adapters must reject a terminal max-token marker before done/tool calls."""
    frames = json.loads(json.dumps(recordings[kind]["first" if tool_turn else "second"]))
    if kind in {"openai", "compatible", "llama"}:
        for frame in frames:
            for choice in frame.get("choices", []):
                if choice.get("finish_reason") is not None:
                    choice["finish_reason"] = "length"
    elif kind == "anthropic":
        for frame in frames:
            if frame.get("type") == "message_delta":
                frame["delta"]["stop_reason"] = "max_tokens"
    elif kind == "gemini":
        for frame in frames:
            for candidate in frame.get("candidates", []):
                if candidate.get("finishReason") is not None:
                    candidate["finishReason"] = "MAX_TOKENS"
    else:
        for frame in frames:
            if frame.get("done"):
                frame["done_reason"] = "length"

    _, provider_name, _ = _provider_and_requests(monkeypatch, kind, [frames])
    with pytest.raises(RuntimeError, match="generation incomplete"):
        _turn(provider_name, MESSAGES)
