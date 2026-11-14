"""Exercise cloud routes through the actual SDK and public Graphlink entry points."""
from __future__ import annotations

import io
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import openai
import pytest

import api_provider
import graphlink_task_config as config
from backend.providers.base import ToolSpec
from graphlink_cloud_providers import CLOUD_PROVIDERS
from graphlink_model_catalog import ModelRef


# The backend suite replaces chat_stream with a non-streaming stub by default.
chat_stream = api_provider.chat_stream
CHAT = config.TASK_CHAT
MESSAGES = [{"role": "user", "content": "Hello"}]


@pytest.fixture
def wire(monkeypatch):
    """Record requests at HTTP transport level. Credentials and model replies are fixtures."""
    responses: list[httpx.Response] = []
    requests: list[httpx.Request] = []

    def handle(request):
        requests.append(request)
        return responses.pop(0)

    sdk = openai.OpenAI

    def client(**kwargs):
        return sdk(http_client=httpx.Client(transport=httpx.MockTransport(handle)), **kwargs)

    monkeypatch.setattr(openai, "OpenAI", client)
    return requests, responses


def completion(text):
    return httpx.Response(200, json={
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
    })


def stream(deltas, reason="stop"):
    chunks = [
        {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]} for delta in deltas
    ] + [
        {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]},
        {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}},
    ]
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


@pytest.mark.parametrize("name", [p.name for p in CLOUD_PROVIDERS.values() if p.protocol == "openai"])
def test_named_provider_routes_to_its_own_origin_with_its_own_key(name, wire, monkeypatch):
    requests, responses = wire
    profile = CLOUD_PROVIDERS[name]
    endpoints = {
        "Azure OpenAI": "https://my-deployment.openai.azure.com/openai/v1",
        "Google Vertex AI": "https://aiplatform.googleapis.com/v1/projects/testing/locations/global/endpoints/openapi",
    }
    url = endpoints.get(name, profile.base_url)
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "X-Private-Org: another-tenant")
    monkeypatch.setenv("OPENAI_ORG_ID", "openai-tenant")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "openai-project")
    runtime = api_provider.ProviderRuntime()
    runtime.initialize_api(name, "provider-key", url, task_models={CHAT: "my/deployment"})
    responses.append(completion("ok"))
    answer = api_provider.chat(config.TASK_TITLE, MESSAGES, runtime=runtime)
    assert answer["message"]["content"] == "ok"
    req, = requests
    assert str(req.url) == f"{url}/chat/completions"
    assert req.headers["Authorization"] == "Bearer provider-key"
    if req.url.host != "api.openai.com":
        assert "X-Private-Org" not in req.headers
        assert "OpenAI-Organization" not in req.headers
        assert "OpenAI-Project" not in req.headers
    assert json.loads(req.content)["model"] == "my/deployment"
    assert runtime.snapshot().api_models[config.TASK_IMAGE_GEN] is None


def test_stream_and_pin_keep_the_named_provider_and_usage(wire):
    requests, responses = wire
    runtime = api_provider.ProviderRuntime()
    runtime.initialize_api("Mistral AI", "test", task_models={CHAT: "model-a"})
    # Mistral can send the last thinking chunk and the first visible text
    # together, then switch back to ordinary string deltas.
    responses.append(stream([{"content": [
        {"type": "thinking", "thinking": [{"type": "text", "text": "hidden"}]},
        {"type": "text", "text": "Say "},
    ]}, {"content": "hello"}]))
    chunks = []
    ref = ModelRef(provider="Mistral AI", model_id="model-b")
    reply = chat_stream(CHAT, MESSAGES, lambda text, reset: chunks.append(text), runtime=runtime, model_ref=ref)
    assert chunks == ["Say ", "hello"]
    assert reply == {"message": {"role": "assistant", "content": "Say hello"},
                     "usage": {"prompt_tokens": 7, "completion_tokens": 2}}
    assert json.loads(requests[0].content)["model"] == "model-b"
    with pytest.raises(RuntimeError, match="pinned to DeepSeek"):
        api_provider.chat(CHAT, MESSAGES, runtime=runtime, model_ref=ModelRef("DeepSeek", "other"))
    assert len(requests) == 1


def test_reasoning_tool_round_trip_is_scoped_to_the_originating_provider_and_model(wire):
    requests, responses = wire
    runtime = api_provider.ProviderRuntime()
    runtime.initialize_api("DeepSeek", "test", task_models={CHAT: "reasoning"})
    tool = ToolSpec("graph.lookup", "Find the entry", {"type": "object", "properties": {}})
    responses.append(stream([
        {"reasoning_content": "look "}, {"reasoning_content": "it up"},
        {"tool_calls": [{"index": 0, "id": "call1", "type": "function",
                         "function": {"name": "graph_lookup", "arguments": "{}"}}]},
    ], reason="tool_calls"))
    reply = api_provider.chat_turn_with_tools(CHAT, MESSAGES, (tool,), runtime=runtime)
    call, = reply["tool_calls"]
    assert call.name == "graph.lookup"
    history = [*MESSAGES, {**reply["message"], "tool_calls": [call.history_entry()]},
               {"role": "tool", "tool_call_id": call.id, "content": "found"}]
    for provider, model in (("DeepSeek", "reasoning"), ("Moonshot AI", "reasoning"), ("DeepSeek", "another")):
        runtime.initialize_api(provider, "test", task_models={CHAT: model})
        responses.append(stream([{"content": "done"}]))
        api_provider.chat_turn_with_tools(CHAT, history, (tool,), runtime=runtime)
        sent = json.loads(requests[-1].content)["messages"][1]
        assert "provider_reasoning" not in sent
        assert sent["tool_calls"][0]["function"]["name"] == "graph_lookup"
        if (provider, model) == ("DeepSeek", "reasoning"):
            assert sent["reasoning_content"] == "look it up" and sent["content"] == ""
        else:
            assert "reasoning_content" not in sent


def test_failed_activation_does_not_change_live_state_or_leak_openai_env_key(wire, monkeypatch):
    requests, _ = wire
    monkeypatch.setenv("OPENAI_API_KEY", "openai-env")
    runtime = api_provider.ProviderRuntime()
    runtime.initialize_api("Groq", "groq-key", task_models={CHAT: "old", config.TASK_IMAGE_GEN: "old-image"})
    before = runtime.snapshot()
    with pytest.raises(RuntimeError, match="API key not configured"):
        runtime.initialize_api(config.API_PROVIDER_OPENAI, "", "https://other.example/v1", task_models={CHAT: "new"})
    assert runtime.snapshot() == before
    staged = api_provider.ProviderRuntime()
    staged.initialize_api("OpenAI", "", task_models={CHAT: "new", config.TASK_TITLE: "title"})
    runtime.activate_api(staged.snapshot())
    state = runtime.snapshot()
    assert state.api_key == "openai-env" and state.api_models[config.TASK_TITLE] == "title"
    assert state.api_models[config.TASK_IMAGE_GEN] is None
    runtime.clear_api_configuration()
    assert runtime.snapshot().api_client is None and not runtime.is_configured()
    assert not requests


@pytest.mark.parametrize(("provider", "body"), [
    ("Groq", {"data": [{"id": "chat-b"}, {"id": "chat-a"}]}),
    ("Together AI", [{"id": "chat-b", "type": "chat"}, {"id": "chat-a", "type": "chat"},
                     {"id": "embed", "type": "embedding"}]),
])
def test_model_catalog_resolves_openai_pages_and_together_arrays(wire, provider, body):
    requests, responses = wire
    responses.append(httpx.Response(200, json=body))
    assert api_provider.list_models_for_config(provider, "test") == ["chat-a", "chat-b"]
    assert requests[0].url.path.endswith("/models")


def test_gemini_model_catalog_validates_methods_and_paginates_without_putting_keys_in_urls(monkeypatch):
    requested = []
    pages = [
        {"models": [{"name": "models/embedding", "supportedGenerationMethods": ["embedContent"]},
                    {"name": "models/zeta", "supportedGenerationMethods": ["generateContent"]}],
         "nextPageToken": "next &/ page"},
        {"models": [{"name": "models/alpha", "supportedGenerationMethods": ["generateContent"]}]},
    ]

    class Opener:
        def open(self, request, timeout):
            assert request.headers["X-goog-api-key"] == "fixture"
            assert "fixture" not in request.full_url
            requested.append(parse_qs(urlsplit(request.full_url).query))
            return io.BytesIO(json.dumps(pages.pop(0)).encode())

    monkeypatch.setattr(api_provider.urllib.request, "build_opener", lambda *args: Opener())
    assert api_provider.list_models_for_config(config.API_PROVIDER_GEMINI, "fixture") == ["alpha", "zeta"]
    assert requested[1]["pageToken"] == ["next &/ page"]


@pytest.mark.parametrize(("name", "url", "cursor", "next_cursor"), [
    ("Cohere", "https://api.cohere.ai/v1/models", "page_token", "next_page_token"),
    ("Fireworks AI", "https://api.fireworks.ai/v1/accounts/fireworks/models", "pageToken", "nextPageToken"),
])
def test_native_catalog_protocol_and_cursor(wire, name, url, cursor, next_cursor):
    requests, responses = wire
    entry = {"name": "chat", "supportsServerless": True, "conversationConfig": {"style": "auto"}}
    responses.extend([httpx.Response(200, json={"models": [entry], next_cursor: "page/2"}),
                      httpx.Response(200, json={"models": [{**entry, "name": "chat-2"}]})])
    assert api_provider.list_models_for_config(name, "test") == ["chat", "chat-2"]
    assert str(requests[0].url).split("?")[0] == url
    assert requests[0].headers["Authorization"] == "Bearer test"
    assert requests[1].url.params[cursor] == "page/2"


def test_anthropic_pages_use_after_id(monkeypatch):
    urls = []
    pages = [{"data": [{"id": "a"}], "has_more": True, "last_id": "a/0"}, {"data": [{"id": "b"}]}]

    def fetch(url, *, api_key):
        urls.append(url)
        assert api_key == "test"
        return pages.pop(0)

    monkeypatch.setattr(api_provider, "_anthropic_get_json", fetch)
    assert api_provider._list_models(config.API_PROVIDER_ANTHROPIC, {}, "test") == ["a", "b"]
    assert parse_qs(urlsplit(urls[1]).query)["after_id"] == ["a/0"]


def test_catalog_auth_errors_never_echo_a_masked_or_complete_credential(wire):
    _, responses = wire
    responses.append(httpx.Response(401, json={"error": {"message": "Incorrect API key sk-***TAIL"}}))
    with pytest.raises(RuntimeError, match="rejected access") as error:
        api_provider.list_models_for_config("OpenAI", "sk-private-TAIL")
    assert "TAIL" not in str(error.value)


def test_mistral_translates_foreign_history_ids_including_collisions(wire):
    requests, responses = wire
    runtime = api_provider.ProviderRuntime()
    runtime.initialize_api("Mistral AI", "test", task_models={CHAT: "mistral-model"})
    history = [*MESSAGES, {"role": "assistant", "content": "", "tool_calls": [
        {"id": "foreign-id", "name": "tool", "arguments": {}},
        {"id": "G00000000", "name": "tool", "arguments": {}},
    ]}, {"role": "tool", "tool_call_id": "foreign-id", "content": "first"},
        {"role": "tool", "tool_call_id": "G00000000", "content": "second"}]
    responses.append(stream([{"content": "done"}]))
    api_provider.chat_turn_with_tools(CHAT, history, runtime=runtime)
    sent = json.loads(requests[0].content)["messages"]
    ids = [call["id"] for call in sent[1]["tool_calls"]]
    assert ids == ["G00000001", "G00000000"]
    assert [message["tool_call_id"] for message in sent[2:]] == ids
    assert history[2]["tool_call_id"] == "foreign-id"


def test_xai_image_request_uses_aspect_ratio_and_base64(wire):
    requests, responses = wire
    runtime = api_provider.ProviderRuntime()
    runtime.initialize_api("xAI", "test", task_models={CHAT: "grok", config.TASK_IMAGE_GEN: "my-image-model"})
    responses.append(httpx.Response(200, json={"data": [{"b64_json": "cGl4ZWxz"}]}))
    assert api_provider.generate_image("draw a cat", size="1536x1024", runtime=runtime) == b"pixels"
    assert str(requests[0].url) == "https://api.x.ai/v1/images/generations"
    assert json.loads(requests[0].content) == {"model": "my-image-model", "prompt": "draw a cat",
                                              "aspect_ratio": "3:2", "response_format": "b64_json"}
