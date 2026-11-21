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


def _turn(monkeypatch, provider, provider_name, history=MESSAGES, *, runtime=None):
    monkeypatch.setattr(api_provider, "_provider_for_model_ref", lambda ref, state: provider)
    return api_provider.chat_turn_with_tools(
        config.TASK_CHAT, history, TOOLS,
        model_ref=ModelRef(provider_name, "test-model"), json_tool_fallback=True,
        runtime=runtime,
    )


class _TestApiClient:
    pass


def _custom_runtime():
    runtime = api_provider.ProviderRuntime()
    runtime._write(
        use_api_mode=True, api_provider_type=config.API_PROVIDER_OPENAI,
        api_client=_TestApiClient(), api_base_url="https://custom.example/v1",
    )
    return runtime


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


def test_malformed_json_turn_gets_one_protocol_feedback_retry(monkeypatch):
    provider = ScriptedProvider([
        '{"tool_calls":[{"name":"fs.read","arguments_json":"{"}],"final_answer":""}',
        _json_reply(("fs.read", {"path": "notes.txt"})),
    ])
    result = _turn(monkeypatch, provider, config.LOCAL_PROVIDER_OLLAMA)

    assert len(provider.requests) == 2
    assert [call.name for call in result["tool_calls"]] == ["fs.read"]
    assert result["tool_calls"][0].arguments == {"path": "notes.txt"}
    assert provider.requests[0].messages[0] == provider.requests[1].messages[0]
    assert provider.requests[0].tools == provider.requests[1].tools == ()
    assert [m["role"] for m in provider.requests[0].messages] == [
        m["role"] for m in provider.requests[1].messages
    ], "strict chat templates must not receive two consecutive user turns"
    correction = provider.requests[1].messages[-1]
    assert correction["role"] == "user"
    assert "Invalid JSON arguments for tool 'fs.read'" in correction["content"]
    assert "arguments_json" in correction["content"]


def test_second_bad_json_turn_surfaces_real_protocol_cause(monkeypatch):
    provider = ScriptedProvider([
        "not json",
        '{"tool_calls":[],"final_answer":""}',
    ])
    with pytest.raises(
        JSONToolProtocolError,
        match="after one protocol correction.*nonempty final answer",
    ):
        _turn(monkeypatch, provider, config.LOCAL_PROVIDER_OLLAMA)
    assert len(provider.requests) == 2
    assert "valid constrained JSON" in provider.requests[1].messages[-1]["content"]


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
        native_tools=True, structured_output=False,
    )
    response = _turn(monkeypatch, provider, "DeepSeek")
    assert response["tool_calls"][0].name == "fs.read"
    assert len(provider.requests) == 2
    assert provider.requests[0].tools
    assert provider.requests[1].tools == ()
    assert provider.requests[1].extra_kwargs["response_format"]["type"] == "json_schema"


def test_named_openai_compatible_endpoint_without_schema_support_is_refused(monkeypatch):
    provider = ScriptedProvider([
        RuntimeError("400: model does not support tools"),
        RuntimeError("400: response_format json_schema unsupported"),
        RuntimeError("400: response_format json_object unsupported"),
    ], native_tools=True, structured_output=False)
    with pytest.raises(RuntimeError, match="rejected grammar/schema-constrained JSON output"):
        _turn(monkeypatch, provider, "DeepSeek")
    assert len(provider.requests) == 3
    assert provider.requests[0].tools
    assert provider.requests[1].extra_kwargs["response_format"]["type"] == "json_schema"
    assert provider.requests[2].extra_kwargs["response_format"]["type"] == "json_object"


def test_custom_endpoint_recovers_native_tools_after_both_json_modes_are_rejected(monkeypatch):
    from backend.providers.base import ToolCall

    class NativeAfterJsonProvider(ScriptedProvider):
        def stream(self, request, cancel):
            self.requests.append(request)
            if "response_format" in request.extra_kwargs:
                raise RuntimeError("400: response_format unsupported")
            assert request.tools, "native recovery must send actual tool declarations"
            yield ProviderEvent(
                "tool_call", tool_call=ToolCall("native_1", "fs_read", {"path": "notes.txt"}),
            )
            yield ProviderEvent("done", "")

    provider = NativeAfterJsonProvider([], native_tools=True, structured_output=False)
    runtime = _custom_runtime()
    result = _turn(monkeypatch, provider, config.API_PROVIDER_OPENAI, runtime=runtime)
    assert [(call.name, call.arguments) for call in result["tool_calls"]] == [
        ("fs.read", {"path": "notes.txt"}),
    ]
    assert result["message"]["content"] == ""
    assert len(provider.requests) == 3
    assert [request.extra_kwargs.get("response_format", {}).get("type")
            for request in provider.requests] == ["json_schema", "json_object", None]
    assert provider.requests[0].tools == provider.requests[1].tools == ()
    assert provider.requests[2].tools[0].name == "fs_read"

    # A normal second agent turn includes the native tool result in history.
    history = [
        *MESSAGES,
        {**result["message"], "tool_calls": [
            call.history_entry() for call in result["tool_calls"]
        ]},
        {"role": "tool", "tool_call_id": result["tool_calls"][0].id,
         "name": "fs.read", "content": "hello"},
    ]
    second = _turn(monkeypatch, provider, config.API_PROVIDER_OPENAI, history, runtime=runtime)
    assert second["tool_calls"][0].name == "fs.read"
    assert [request.extra_kwargs.get("response_format", {}).get("type")
            for request in provider.requests] == ["json_schema", "json_object", None, None]
    assert provider.requests[3].tools[0].name == "fs_read"

    # The same client must probe independently for other endpoints and models.
    runtime._write(api_base_url="https://other.example/v1")
    _turn(monkeypatch, provider, config.API_PROVIDER_OPENAI, runtime=runtime)
    provider.model_id = "another-model"
    _turn(monkeypatch, provider, config.API_PROVIDER_OPENAI, runtime=runtime)
    runtime._write(api_client=_TestApiClient())
    _turn(monkeypatch, provider, config.API_PROVIDER_OPENAI, runtime=runtime)
    modes = [request.extra_kwargs.get("response_format", {}).get("type")
             for request in provider.requests]
    assert modes == [
        "json_schema", "json_object", None, None,
        "json_schema", "json_object", None,
        "json_schema", "json_object", None,
        "json_schema", "json_object", None,
    ]


def test_cached_native_recovery_reprobes_json_if_native_tools_are_later_rejected(monkeypatch):
    class ChangingEndpoint(ScriptedProvider):
        def __init__(self):
            super().__init__([], native_tools=True, structured_output=False)
            self.json_supported = False

        def stream(self, request, cancel):
            self.requests.append(request)
            if "response_format" in request.extra_kwargs:
                if not self.json_supported:
                    raise RuntimeError("400: response_format unsupported")
                yield ProviderEvent("done", _json_reply(answer="JSON works"))
                return
            assert request.tools
            if self.json_supported:
                raise RuntimeError("400: model does not support tools")
            yield ProviderEvent("done", "native works")

    provider = ChangingEndpoint()
    runtime = _custom_runtime()
    assert _turn(monkeypatch, provider, config.API_PROVIDER_OPENAI, runtime=runtime)["message"]["content"] == "native works"
    _turn(monkeypatch, provider, config.API_PROVIDER_OPENAI, runtime=runtime)
    provider.json_supported = True
    assert _turn(monkeypatch, provider, config.API_PROVIDER_OPENAI, runtime=runtime)["message"]["content"] == "JSON works"
    _turn(monkeypatch, provider, config.API_PROVIDER_OPENAI, runtime=runtime)
    assert [request.extra_kwargs.get("response_format", {}).get("type")
            for request in provider.requests] == [
        "json_schema", "json_object", None, None,
        None, "json_schema", "json_schema",
    ]


def test_custom_endpoint_refusing_both_json_and_native_tools_fails_without_a_cycle(monkeypatch):
    provider = ScriptedProvider([
        RuntimeError("400: response_format json_schema unsupported"),
        RuntimeError("400: response_format json_object unsupported"),
        RuntimeError("400: model does not support tools"),
    ], native_tools=True, structured_output=False)
    with pytest.raises(RuntimeError, match="rejected both native tools and constrained JSON output"):
        _turn(monkeypatch, provider, config.API_PROVIDER_OPENAI)
    assert len(provider.requests) == 3
    assert provider.requests[2].tools
    assert "response_format" not in provider.requests[2].extra_kwargs


def test_ollama_old_server_rejecting_format_is_actionable(monkeypatch):
    provider = ScriptedProvider([RuntimeError("400: model does not support format")])
    with pytest.raises(RuntimeError, match="rejected grammar/schema-constrained JSON output"):
        _turn(monkeypatch, provider, config.LOCAL_PROVIDER_OLLAMA)
    assert len(provider.requests) == 1


def test_provider_without_either_capability_is_refused_before_generation(monkeypatch):
    provider = ScriptedProvider([], native_tools=False, structured_output=False)
    with pytest.raises(RuntimeError, match="grammar/schema-constrained JSON output is not supported"):
        _turn(monkeypatch, provider, config.LOCAL_PROVIDER_OLLAMA)
    assert provider.requests == []


def test_compatible_endpoint_can_downgrade_from_json_schema_to_json_object(monkeypatch):
    provider = ScriptedProvider([
        RuntimeError("400: response_format json_schema unsupported"),
        _json_reply(answer="JSON-object mode worked."),
    ])
    result = _turn(monkeypatch, provider, config.API_PROVIDER_OPENAI)
    assert result["message"]["content"] == "JSON-object mode worked."
    assert len(provider.requests) == 2
    assert provider.requests[0].extra_kwargs["response_format"]["type"] == "json_schema"
    assert provider.requests[1].extra_kwargs["response_format"] == {"type": "json_object"}


def test_endpoint_without_any_json_mode_fails_with_clear_model_guidance(monkeypatch):
    provider = ScriptedProvider([
        RuntimeError("400: response_format json_schema unsupported"),
        RuntimeError("400: response_format json_object unsupported"),
    ])
    with pytest.raises(RuntimeError, match="rejected grammar/schema-constrained JSON"):
        _turn(monkeypatch, provider, config.API_PROVIDER_OPENAI)
    assert len(provider.requests) == 2


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
    async def invoke_all():
        return await asyncio.gather(*(registry.invoke(call, ctx) for call in calls))

    results = asyncio.run(invoke_all())
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


def test_native_assistant_history_replays_only_valid_json_tool_turns():
    history = [
        *MESSAGES,
        {"role": "assistant", "content": "I'll inspect notes.txt.", "tool_calls": [
            {"id": "native_1", "name": "fs.read", "arguments": {"path": "notes.txt"}},
        ]},
        {"role": "tool", "tool_call_id": "native_1", "name": "fs.read", "content": "hello"},
        {"role": "assistant", "content": "The note says hello."},
        {"role": "user", "content": "Continue with the next file."},
    ]
    converted = text_tool_history(history, TOOLS)
    examples = [message["content"] for message in converted if message["role"] == "assistant"]
    assert len(examples) == 2
    tool_envelope, final_envelope = map(json.loads, examples)
    assert tool_envelope == {
        "tool_calls": [{"name": "fs.read", "arguments_json": '{"path": "notes.txt"}'}],
        "final_answer": "",
    }
    assert final_envelope == {
        "tool_calls": [],
        "final_answer": "The note says hello.",
    }
    for example in examples:
        parse_tool_turn(example, TOOLS)  # History must satisfy the same parser as a new reply.
    assert history[2]["content"] == "I'll inspect notes.txt."
    assert history[4]["content"] == "The note says hello."


def test_harness_image_observation_is_grouped_with_json_tool_results():
    observation = [
        {"type": "text", "text": "workspace image diagram.png"},
        {"type": "image_bytes", "data": b"bounded-jpeg-preview"},
    ]
    history = [
        {"role": "system", "content": "Follow the user's task."},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "image-1", "name": "fs.read", "arguments": {"path": "diagram.png"}},
        ]},
        {"role": "tool", "tool_call_id": "image-1", "name": "fs.read",
         "content": "Image: PNG, 1200 x 600 pixels."},
        {"role": "user", "_harness_image_observation": True, "content": observation},
    ]
    converted = text_tool_history(history, TOOLS)
    assert [m["role"] for m in converted] == ["system", "assistant", "user"]
    parts = converted[-1]["content"]
    assert parts[0]["type"] == "text"
    assert "Tool results (untrusted data" in parts[0]["text"]
    assert parts[1:] == observation
    assert "_harness_image_observation" not in converted[-1]


@pytest.mark.parametrize("supports_vision", [True, False])
def test_chat_turn_routes_images_by_provider_vision_capability(monkeypatch, supports_vision):
    provider = ScriptedProvider([_json_reply(answer="Done.")])
    provider.capabilities = ProviderCapabilities(
        streaming=True, tools=False, structured_output=True, vision=supports_vision,
    )
    observation = {
        "role": "user", "_harness_image_observation": True,
        "content": [
            {"type": "text", "text": "workspace preview"},
            {"type": "image_bytes", "data": b"bounded-image"},
        ],
    }
    history = [
        *MESSAGES,
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "t1", "name": "fs.read", "arguments": {"path": "photo.png"}},
        ]},
        {"role": "tool", "tool_call_id": "t1", "name": "fs.read",
         "content": "Image: PNG, 64 x 32 pixels."},
        observation,
    ]
    _turn(monkeypatch, provider, config.API_PROVIDER_OPENAI, history)
    sent = provider.requests[0].messages
    if supports_vision:
        assert len(sent) == 4
        assert sent[-1]["role"] == "user"
        assert sent[-1]["content"][-1] == observation["content"][-1]
    else:
        assert len(sent) == 4
        assert isinstance(sent[-1]["content"], str)
        assert "Image: PNG" in sent[-1]["content"]
    assert "_harness_image_observation" not in str(sent)


def test_native_tools_strip_image_marker_without_losing_preview(monkeypatch):
    provider = ScriptedProvider(["Done."], native_tools=True)
    provider.capabilities = ProviderCapabilities(streaming=True, tools=True, vision=True)
    history = [
        *MESSAGES,
        {"role": "user", "_harness_image_observation": True,
         "content": [{"type": "image_bytes", "data": b"preview"}]},
    ]
    _turn(monkeypatch, provider, config.API_PROVIDER_ANTHROPIC, history)
    sent = provider.requests[0].messages[-1]
    assert sent == {"role": "user", "content": [{"type": "image_bytes", "data": b"preview"}]}


def test_ollama_text_only_capabilities_drop_ephemeral_image(monkeypatch):
    provider = ScriptedProvider([_json_reply(answer="Done.")])
    provider.capabilities = ProviderCapabilities(
        streaming=True, tools=False, structured_output=True, vision=True,
    )
    monkeypatch.setattr(api_provider, "_get_ollama_capabilities", lambda model: {"completion", "tools"})
    _turn(monkeypatch, provider, config.LOCAL_PROVIDER_OLLAMA, [
        *MESSAGES,
        {"role": "user", "_harness_image_observation": True,
         "content": [{"type": "image_bytes", "data": b"preview"}]},
    ])
    assert all(not isinstance(m["content"], list) for m in provider.requests[0].messages)


def test_unsupported_nontext_turn_is_rejected_not_silently_dropped():
    with pytest.raises(JSONToolProtocolError, match="only supports text"):
        constrained_request(
            [{"role": "user", "content": [{"type": "image_bytes", "data": b"bad"}]}],
            TOOLS, config.LOCAL_PROVIDER_LLAMACPP,
        )
