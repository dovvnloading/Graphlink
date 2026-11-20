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
