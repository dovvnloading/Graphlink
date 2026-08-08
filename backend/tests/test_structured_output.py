"""ADR-007 stage 7.3: respond_json - the unified schema-constrained JSON path.

Exit criterion this file proves: "Same schema output across all 5 providers
incl. Anthropic (golden tests)" - test_respond_json_returns_the_same_parsed_
object_on_every_provider below drives all five provider branches of the REAL
api_provider.chat() dispatch (not a fake of chat() itself) with each
provider's own complete() overridden to return a canned string, and asserts
every one parses to the identical dict.
"""

from __future__ import annotations

import api_provider
import graphlink_task_config as config
import pytest

import backend.structured_output as so

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


# -- schema-subset validator --------------------------------------------


def test_validate_accepts_a_conforming_object():
    assert so._validate_against_schema({"answer": "hi"}, SCHEMA) == []


def test_validate_reports_a_missing_required_property():
    errors = so._validate_against_schema({}, SCHEMA)
    assert any("answer" in e and "missing" in e for e in errors)


def test_validate_reports_a_wrong_top_level_type():
    errors = so._validate_against_schema(["not", "an", "object"], SCHEMA)
    assert any("expected object" in e for e in errors)


def test_validate_reports_a_wrong_property_type():
    errors = so._validate_against_schema({"answer": 42}, SCHEMA)
    assert any("expected string" in e for e in errors)


def test_validate_recurses_into_nested_objects_and_arrays():
    schema = {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}}
        },
    }
    errors = so._validate_against_schema({"items": [{"n": 1}, {"n": "not an int"}]}, schema)
    assert any("items[1].n" in e for e in errors)


def test_validate_enforces_enum():
    schema = {"type": "string", "enum": ["red", "green", "blue"]}
    assert so._validate_against_schema("purple", schema) != []
    assert so._validate_against_schema("red", schema) == []


# -- response cleanup -----------------------------------------------------


def test_clean_json_response_strips_markdown_fences():
    assert so._clean_json_response('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_clean_json_response_strips_think_blocks_and_falls_back_to_brace_matching():
    text = "<think>reasoning...</think>Here you go: {\"a\": 1} thanks!"
    assert so._clean_json_response(text) == '{"a": 1}'


# -- per-provider native kwargs shape --------------------------------------


def _snapshot(monkeypatch, **overrides):
    for key, value in overrides.items():
        monkeypatch.setattr(api_provider, key, value)
    return api_provider._snapshot_provider_state()


def test_native_kwargs_openai_shape(monkeypatch):
    state = _snapshot(monkeypatch, USE_API_MODE=True, API_PROVIDER_TYPE=config.API_PROVIDER_OPENAI)
    assert so._native_kwargs_for_active_provider(state, SCHEMA, "response") == {
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "response", "schema": SCHEMA, "strict": True},
        }
    }


def test_native_kwargs_gemini_shape(monkeypatch):
    state = _snapshot(monkeypatch, USE_API_MODE=True, API_PROVIDER_TYPE=config.API_PROVIDER_GEMINI)
    assert so._native_kwargs_for_active_provider(state, SCHEMA, "response") == {
        "response_mime_type": "application/json",
        "response_schema": SCHEMA,
    }


def test_native_kwargs_ollama_shape(monkeypatch):
    state = _snapshot(monkeypatch, USE_API_MODE=False, LOCAL_PROVIDER_TYPE=config.LOCAL_PROVIDER_OLLAMA)
    assert so._native_kwargs_for_active_provider(state, SCHEMA, "response") == {"format": SCHEMA}


def test_native_kwargs_llama_cpp_shape(monkeypatch):
    state = _snapshot(monkeypatch, USE_API_MODE=False, LOCAL_PROVIDER_TYPE=config.LOCAL_PROVIDER_LLAMACPP)
    assert so._native_kwargs_for_active_provider(state, SCHEMA, "response") == {
        "response_format": {"type": "json_object", "schema": SCHEMA}
    }


def test_native_kwargs_anthropic_is_none_the_fallback_signal(monkeypatch):
    state = _snapshot(monkeypatch, USE_API_MODE=True, API_PROVIDER_TYPE=config.API_PROVIDER_ANTHROPIC)
    assert so._native_kwargs_for_active_provider(state, SCHEMA, "response") is None


# -- full dispatch: the golden, all-5-providers exit criterion ------------


def _activate_mode(monkeypatch, mode):
    if mode == "ollama":
        monkeypatch.setattr(api_provider, "USE_API_MODE", False)
        monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_OLLAMA)
        monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, "fake-model:1b")
    elif mode == "llama_cpp":
        monkeypatch.setattr(api_provider, "USE_API_MODE", False)
        monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_LLAMACPP)
        monkeypatch.setattr(
            api_provider, "LLAMA_CPP_SETTINGS", {"chat_model_path": "m.gguf", "reasoning_level": "off"}
        )
    else:
        api_provider_type = {
            "openai": config.API_PROVIDER_OPENAI,
            "anthropic": config.API_PROVIDER_ANTHROPIC,
            "gemini": config.API_PROVIDER_GEMINI,
        }[mode]
        monkeypatch.setattr(api_provider, "USE_API_MODE", True)
        monkeypatch.setattr(api_provider, "API_PROVIDER_TYPE", api_provider_type)
        monkeypatch.setattr(api_provider, "API_KEY", "k")
        # complete() is overridden by every caller below and never touches
        # self.client - a bare sentinel just needs to be truthy for chat()'s
        # own "API client not initialized" guard.
        monkeypatch.setattr(api_provider, "API_CLIENT", object())
        monkeypatch.setitem(api_provider.API_MODELS, config.TASK_CHAT, "fake-model")


_PROVIDER_MODES = [
    ("ollama", "backend.providers.ollama_provider", "OllamaProvider"),
    ("llama_cpp", "backend.providers.llama_cpp_provider", "LlamaCppProvider"),
    ("openai", "backend.providers.openai_provider", "OpenAIProvider"),
    ("anthropic", "backend.providers.anthropic_provider", "AnthropicProvider"),
    ("gemini", "backend.providers.gemini_provider", "GeminiProvider"),
]


@pytest.mark.parametrize("mode, module_name, class_name", _PROVIDER_MODES)
def test_respond_json_returns_the_same_parsed_object_on_every_provider(monkeypatch, mode, module_name, class_name):
    _activate_mode(monkeypatch, mode)
    module = __import__(module_name, fromlist=[class_name])
    real = getattr(module, class_name)

    class Fake(real):
        def complete(self, request, cancel):
            return '{"answer": "42"}'

    monkeypatch.setattr(module, class_name, Fake)

    result = so.respond_json(config.TASK_CHAT, [{"role": "user", "content": "hi"}], SCHEMA)
    assert result == {"answer": "42"}


def test_respond_json_does_not_prepend_a_system_message_for_a_native_provider(monkeypatch):
    _activate_mode(monkeypatch, "openai")
    from backend.providers import openai_provider as op

    seen_messages = []

    class Fake(op.OpenAIProvider):
        def complete(self, request, cancel):
            seen_messages.append(request.messages)
            return '{"answer": "42"}'

    monkeypatch.setattr(op, "OpenAIProvider", Fake)
    so.respond_json(config.TASK_CHAT, [{"role": "user", "content": "hi"}], SCHEMA)

    assert seen_messages[0] == [{"role": "user", "content": "hi"}]


def test_respond_json_passes_native_kwargs_through_to_the_providers_complete_call(monkeypatch):
    _activate_mode(monkeypatch, "openai")
    from backend.providers import openai_provider as op

    seen_kwargs = []

    class Fake(op.OpenAIProvider):
        def complete(self, request, cancel):
            seen_kwargs.append(dict(request.extra_kwargs))
            return '{"answer": "42"}'

    monkeypatch.setattr(op, "OpenAIProvider", Fake)
    so.respond_json(config.TASK_CHAT, [{"role": "user", "content": "hi"}], SCHEMA, schema_name="my_schema")

    assert seen_kwargs[0] == {
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "my_schema", "schema": SCHEMA, "strict": True},
        }
    }


def test_respond_json_embeds_the_schema_as_a_system_message_for_the_anthropic_fallback(monkeypatch):
    _activate_mode(monkeypatch, "anthropic")
    from backend.providers import anthropic_provider as ap

    seen_messages = []

    class Fake(ap.AnthropicProvider):
        def complete(self, request, cancel):
            seen_messages.append(request.messages)
            return '{"answer": "42"}'

    monkeypatch.setattr(ap, "AnthropicProvider", Fake)
    so.respond_json(config.TASK_CHAT, [{"role": "user", "content": "hi"}], SCHEMA, schema_name="my_schema")

    first_call_messages = seen_messages[0]
    assert first_call_messages[0]["role"] == "system"
    assert "my_schema" in first_call_messages[0]["content"]
    assert first_call_messages[1] == {"role": "user", "content": "hi"}


# -- validate-and-repair tail -----------------------------------------------


def test_respond_json_repairs_a_malformed_first_response(monkeypatch):
    _activate_mode(monkeypatch, "openai")
    from backend.providers import openai_provider as op

    calls = []

    class Fake(op.OpenAIProvider):
        def complete(self, request, cancel):
            calls.append(request.messages)
            if len(calls) == 1:
                return "this is not json at all"
            return '{"answer": "fixed"}'

    monkeypatch.setattr(op, "OpenAIProvider", Fake)
    result = so.respond_json(config.TASK_CHAT, [{"role": "user", "content": "hi"}], SCHEMA)

    assert result == {"answer": "fixed"}
    assert len(calls) == 2
    # the repair turn is a fresh standalone system+user pair, not the
    # original conversation threaded through.
    assert calls[1][0]["role"] == "system"
    assert "this is not json at all" in calls[1][1]["content"]


def test_respond_json_repairs_a_schema_violating_first_response(monkeypatch):
    _activate_mode(monkeypatch, "openai")
    from backend.providers import openai_provider as op

    calls = []

    class Fake(op.OpenAIProvider):
        def complete(self, request, cancel):
            calls.append(request.messages)
            if len(calls) == 1:
                return '{"wrong_key": "nope"}'  # valid JSON, fails schema
            return '{"answer": "fixed"}'

    monkeypatch.setattr(op, "OpenAIProvider", Fake)
    result = so.respond_json(config.TASK_CHAT, [{"role": "user", "content": "hi"}], SCHEMA)

    assert result == {"answer": "fixed"}
    assert len(calls) == 2


def test_respond_json_raises_when_the_repair_attempt_is_still_not_valid_json(monkeypatch):
    _activate_mode(monkeypatch, "openai")
    from backend.providers import openai_provider as op

    class Fake(op.OpenAIProvider):
        def complete(self, request, cancel):
            return "still not json"

    monkeypatch.setattr(op, "OpenAIProvider", Fake)
    with pytest.raises(so.StructuredOutputError, match="not valid JSON"):
        so.respond_json(config.TASK_CHAT, [{"role": "user", "content": "hi"}], SCHEMA)


def test_respond_json_raises_when_the_repair_attempt_still_violates_the_schema(monkeypatch):
    _activate_mode(monkeypatch, "openai")
    from backend.providers import openai_provider as op

    class Fake(op.OpenAIProvider):
        def complete(self, request, cancel):
            return '{"wrong_key": "still nope"}'

    monkeypatch.setattr(op, "OpenAIProvider", Fake)
    with pytest.raises(so.StructuredOutputError, match="schema"):
        so.respond_json(config.TASK_CHAT, [{"role": "user", "content": "hi"}], SCHEMA)
