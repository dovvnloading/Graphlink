"""Workspace JSON tool protocol: constrained transport, round trips and refusal.

These tests exercise chat_turn_with_tools' real capability gate. The fake
provider only replaces external model inference, not the turn dispatch,
tool-history conversion or JSON parser.
"""

from __future__ import annotations

import asyncio
import json

import api_provider
import graphlink_task_config as config
import pytest

from backend.providers.base import ProviderCapabilities, ProviderEvent, ToolSpec
from backend.providers.json_tool_protocol import (
    JSONToolProtocolError,
    constrained_request,
    parse_tool_turn,
    text_tool_history,
    tool_turn_schema,
)
from backend.tools import FS_WRITE, RunContext, ToolRegistry, ToolResult
from graphlink_model_catalog import ModelRef


READ = ToolSpec(
    "fs.read", "Read a file in the workspace",
    {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
)
WRITE = ToolSpec(
    "fs.write", "Write a file in the workspace",
    {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
     "required": ["path", "content"]},
)
TOOLS = (READ, WRITE)
MESSAGES = [
    {"role": "system", "content": "Follow the workspace task."},
    {"role": "user", "content": "Read notes.txt and summarize it."},
]


def _json_reply(*calls, answer=""):
    return json.dumps({
        "tool_calls": [
            {"name": name, "arguments_json": json.dumps(args)} for name, args in calls
        ],
        "final_answer": answer,
    })


class ScriptedProvider:
    def __init__(self, responses, *, native_tools=False, structured_output=True):
        self.responses = list(responses)
        self.requests = []
        self.model_id = "test-model"
        self.capabilities = ProviderCapabilities(
            streaming=True, tools=native_tools, structured_output=structured_output,
        )

    def stream(self, request, cancel):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        yield ProviderEvent("done", response, usage={"prompt_tokens": 15, "completion_tokens": 10})


def _turn(monkeypatch, provider, provider_name, history=MESSAGES):
    monkeypatch.setattr(api_provider, "_provider_for_model_ref", lambda ref, state: provider)
    return api_provider.chat_turn_with_tools(
        config.TASK_CHAT, history, TOOLS,
        model_ref=ModelRef(provider_name, "test-model"), json_tool_fallback=True,
    )


def test_schema_is_strict_and_names_are_constrained():
    schema = tool_turn_schema(TOOLS)
    assert set(schema["required"]) == {"tool_calls", "final_answer"}
    assert schema["additionalProperties"] is False
    item = schema["properties"]["tool_calls"]["items"]
    assert item["properties"]["name"]["enum"] == ["fs.read", "fs.write"]
    assert item["additionalProperties"] is False
    assert item["properties"]["arguments_json"]["type"] == "string"


def test_llama_cpp_round_trip_replays_history_as_text_without_native_roles(monkeypatch):
    provider = ScriptedProvider([
        _json_reply(("fs.read", {"path": "notes.txt"})),
        _json_reply(answer="The note says hello."),
    ])
    first = _turn(monkeypatch, provider, config.LOCAL_PROVIDER_LLAMACPP)
    assert first["message"]["content"] == ""
    assert first["usage"] == {"prompt_tokens": 15, "completion_tokens": 10}
    (call,) = first["tool_calls"]
    assert call.name == "fs.read" and call.arguments == {"path": "notes.txt"}
    assert call.id.startswith("json_")
    assert provider.requests[0].tools == ()
    assert provider.requests[0].extra_kwargs["response_format"]["type"] == "json_object"
    assert provider.requests[0].extra_kwargs["response_format"]["schema"] == tool_turn_schema(TOOLS)
    assert "WORKSPACE JSON TOOL PROTOCOL" in provider.requests[0].messages[0]["content"]

    history = [
        *MESSAGES,
        {**first["message"], "tool_calls": [call.history_entry()]},
        {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": "hello"},
    ]
    second = _turn(monkeypatch, provider, config.LOCAL_PROVIDER_LLAMACPP, history)
    assert second["tool_calls"] == []
    assert second["message"]["content"] == "The note says hello."
    sent = provider.requests[1].messages
    assert [entry["role"] for entry in sent] == ["system", "user", "assistant", "user"]
    assert all("tool_calls" not in entry for entry in sent)
    assert json.loads(sent[2]["content"])["tool_calls"][0]["name"] == "fs.read"
    assert json.loads(sent[3]["content"].split("\n", 1)[1])[0]["content"] == "hello"
    assert history[2]["tool_calls"][0]["name"] == "fs.read", "original transcript must be unchanged"


def test_ollama_without_tools_uses_schema_format_not_native_tool_params(monkeypatch):
    provider = ScriptedProvider([_json_reply(("fs.read", {"path": "a"}))])
    response = _turn(monkeypatch, provider, config.LOCAL_PROVIDER_OLLAMA)
    assert response["tool_calls"][0].name == "fs.read"
    assert provider.requests[0].tools == ()
    assert provider.requests[0].extra_kwargs["format"] == tool_turn_schema(TOOLS)


@pytest.mark.parametrize("native_tools", [False, True])
def test_custom_openai_compatible_endpoint_uses_strict_json_not_unreliable_native_tools(
    monkeypatch, native_tools,
):
    provider = ScriptedProvider([_json_reply(answer="Done.")], native_tools=native_tools)
    turn = _turn(monkeypatch, provider, config.API_PROVIDER_OPENAI)
    assert turn["message"]["content"] == "Done."
    request, = provider.requests
    assert request.tools == ()
    response_format = request.extra_kwargs["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"] == tool_turn_schema(TOOLS)


def test_native_tool_capability_keeps_the_existing_path(monkeypatch):
    from backend.providers.base import ToolCall

    class NativeProvider(ScriptedProvider):
        def stream(self, request, cancel):
            self.requests.append(request)
            yield ProviderEvent(
                "tool_call", tool_call=ToolCall("native_1", "fs_read", {"path": "a"}),
            )
            yield ProviderEvent("done", "")

    provider = NativeProvider([], native_tools=True)
    turn = _turn(monkeypatch, provider, "DeepSeek")
    assert provider.requests[0].extra_kwargs.get("response_format") is None
    assert provider.requests[0].tools[0].name == "fs_read"
    assert turn["tool_calls"][0].name == "fs.read"


def test_explicit_native_tools_rejection_falls_back_to_constrained_json(monkeypatch):
    provider = ScriptedProvider(
        [RuntimeError("400: model does not support tools"), _json_reply(("fs.read", {"path": "a"}))],
        native_tools=True, structured_output=True,
    )
    response = _turn(monkeypatch, provider, "DeepSeek")
    assert response["tool_calls"][0].name == "fs.read"
    assert len(provider.requests) == 2
    assert provider.requests[0].tools
    assert provider.requests[1].tools == ()
    assert provider.requests[1].extra_kwargs["response_format"]["type"] == "json_schema"


def test_provider_without_either_capability_is_refused_before_generation(monkeypatch):
    provider = ScriptedProvider([], native_tools=False, structured_output=False)
    with pytest.raises(RuntimeError, match="grammar/schema-constrained JSON output is not supported"):
        _turn(monkeypatch, provider, config.LOCAL_PROVIDER_OLLAMA)
    assert provider.requests == []


def test_endpoint_rejecting_json_schema_fails_with_clear_model_guidance(monkeypatch):
    provider = ScriptedProvider([RuntimeError("400: response_format json_schema unsupported")])
    with pytest.raises(RuntimeError, match="rejected grammar/schema-constrained JSON"):
        _turn(monkeypatch, provider, config.API_PROVIDER_OPENAI)


@pytest.mark.parametrize(
    "payload",
    [
        "Not JSON: call fs.write now",
        _json_reply(("fs.delete", {"path": "/"})),
        _json_reply(("fs.write", ["not", "an", "object"])),
        '{"tool_calls":[{"name":"fs.read","arguments_json":"not-json"}],"final_answer":""}',
        _json_reply(("fs.read", {"path": "a"}), answer="I finished"),
        _json_reply(),
        '{"tool_calls":[], "final_answer":"done", "extra":true}',
    ],
)
def test_malformed_or_unauthorized_tool_outputs_fail_closed(payload):
    with pytest.raises(JSONToolProtocolError):
        parse_tool_turn(payload, TOOLS)


def test_multiple_calls_get_distinct_ids_and_approvals_are_not_bypassed():
    answer, calls = parse_tool_turn(
        _json_reply(("fs.write", {"path": "a", "content": "A"}),
                    ("fs.write", {"path": "b", "content": "B"})),
        TOOLS,
    )
    assert answer == ""
    assert len({call.id for call in calls}) == 2

    registry = ToolRegistry()
    ran = []

    async def write(call, ctx):
        ran.append(call.arguments)
        return ToolResult("written")

    async def deny(call):
        return False

    registry.register(WRITE, write, scopes={FS_WRITE}, approval="always")
    ctx = RunContext(granted_scopes=frozenset({FS_WRITE}), request_approval=deny)
    results = asyncio.run(asyncio.gather(*(registry.invoke(call, ctx) for call in calls)))
    assert all(result.is_error and "denied" in result.content for result in results)
    assert ran == []


def test_history_preserves_order_and_groups_parallel_tool_results():
    history = [
        *MESSAGES,
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "name": "fs.read", "arguments": {"path": "a"}},
            {"id": "b", "name": "fs.read", "arguments": {"path": "b"}},
        ], "provider_reasoning": {"text": "secret"}},
        {"role": "tool", "tool_call_id": "a", "name": "fs.read", "content": "one"},
        {"role": "tool", "tool_call_id": "b", "name": "fs.read", "content": "two"},
    ]
    converted = text_tool_history(history, TOOLS)
    assert [m["role"] for m in converted] == ["system", "user", "assistant", "user"]
    results = json.loads(converted[-1]["content"].split("\n", 1)[1])
    assert [r["content"] for r in results] == ["one", "two"]
    assert "secret" not in json.dumps(converted)


def test_unsupported_nontext_turn_is_rejected_not_silently_dropped():
    with pytest.raises(JSONToolProtocolError, match="only supports text"):
        constrained_request(
            [{"role": "user", "content": [{"type": "image_bytes", "data": b"bad"}]}],
            TOOLS, config.LOCAL_PROVIDER_LLAMACPP,
        )
