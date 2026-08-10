"""ADR-007 stage 7.3: respond_json - one schema-constrained JSON output path,
used by every caller that needs a model's answer to conform to a schema
(charts, plugins, the agentic planner) instead of each one hand-rolling its
own JSON-mode kwargs and repair chain the way graphlink_chart_agent.py's
now-retired ChartDataAgent used to (five hardcoded chart schemas, a bespoke
clean_response/repair_chart_data pair, and an if/elif on API_PROVIDER_TYPE
picking response_format/response_mime_type/format by hand - exactly the
per-caller sprawl this ADR's own "Alternatives considered" rejects).
ADR-013 stage 13.3 retired that whole pipeline in favor of this module -
backend/agents.py's _call_chart_agent is now just one respond_json call.

Two paths per provider, chosen from THIS request's own provider snapshot
(api_provider._snapshot_provider_state()/ProviderRuntime.snapshot() - the
same one api_provider.chat() itself reads, so this module never re-derives
"which provider is active" a second, potentially-inconsistent way):

- **Native** (OpenAI, Gemini, Ollama, llama.cpp): the schema rides the
  provider's own hard-constrained JSON mode - OpenAI's `response_format:
  {"type":"json_schema","json_schema":{...,"strict":True}}`, Gemini's
  `response_mime_type`+`response_schema` (an extra_kwargs pass-through into
  generationConfig, same snake_case convention ChartDataAgent's own Gemini
  branch already relies on), Ollama's `format` accepting a raw JSON Schema
  dict directly (not just the string "json"), llama.cpp's `response_format:
  {"type":"json_object","schema":...}` (a GBNF grammar derived from the
  schema, verified against the installed llama_cpp package's own
  ChatCompletionRequestResponseFormat shape). The provider is contractually
  constrained server-side, so this path should essentially never need
  repair - but the response is still parsed+validated as a safety net, not
  trusted blindly.
- **Native, tool-forced** (Anthropic - ADR-013 stage 13.3): Anthropic has no
  native JSON-schema RESPONSE mode, but it does have hard-enforced tool
  argument schemas - so the native path defines exactly one single-purpose
  tool (name=`schema_name`, input_schema=`schema`) and forces it via
  `tool_choice`. AnthropicProvider.complete() reads the forced tool call's
  arguments back as the result instead of visible text (see that method's
  own comment) - server-side constrained, same posture as every other
  native branch, not the schema-guided prompting the fallback below is.

Both paths converge on the SAME validate-and-repair tail: parse, validate
against `schema` (a hand-rolled Draft-2020-12 SUBSET validator - type/
properties/required/items/enum, the exact "OpenAPI-compatible subset"
ToolSpec's own docstring already documents as the portable target across
every provider, ADR-007 stage 7.1 - not a full implementation, and not a new
third-party dependency for a subset this codebase already hand-validates
elsewhere via graphlink_chart_data.canonicalize_chart_data's own precedent).
On failure, exactly ONE repair turn is sent - a fresh, standalone system+user
pair naming the validation errors and asking for a corrected object,
directly generalizing ChartDataAgent.repair_chart_data's own single-attempt
shape to an arbitrary caller-supplied schema instead of five hardcoded ones.
Failing that, respond_json raises StructuredOutputError - unlike
ChartDataAgent.get_response's legacy json.dumps({"error": ...}) string
convention, a caller here gets a normal Python exception, matching how a
plugin/planner caller actually wants to handle this (try/except), not a
second JSON payload shape to parse.

respond_json() is deliberately synchronous/blocking (api_provider.chat(),
not chat_stream()) - every real caller (ChartDataAgent-shaped ones, a future
plugin/planner call) wants the whole object back at once, not incremental
deltas; ADR-006 stage 6.4's own precedent already keeps a blocking chat()
path alive specifically for exactly this kind of single-shot request.
"""

from __future__ import annotations

import json
import re

import api_provider
import graphlink_task_config as config
from backend.providers.base import ToolSpec


class StructuredOutputError(RuntimeError):
    """Raised when respond_json cannot produce schema-conforming JSON even
    after the one repair attempt - carries the validation errors that
    doomed the repaired response in its own message."""


# -- response cleanup (mirrors ChartDataAgent.clean_response's own two-stage
# -- extraction - markdown fence first, then a raw brace-to-brace fallback -
# -- generalized here beyond chart JSON, so kept as a free function rather
# -- than importing graphlink_chart_agent, which this module has no other
# -- reason to depend on) ------------------------------------------------


def _clean_json_response(text: str) -> str:
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()
    block_match = re.search(r"```(?:json)?\s*([\[{][\s\S]*?[\]}])\s*```", text, re.IGNORECASE)
    if block_match:
        return block_match.group(1).strip()
    fallback_match = re.search(r"([\[{][\s\S]*[\]}])", text)
    if fallback_match:
        return fallback_match.group(1).strip()
    return text.strip()


# -- minimal JSON Schema subset validator ---------------------------------


def _validate_against_schema(data, schema: dict, path: str = "$") -> list[str]:
    """Returns human-readable error strings; an empty list means valid.
    Deliberately narrow (type/properties/required/items/enum only - no
    $ref/$defs/oneOf/pattern/...) - see this module's own docstring for why
    that subset, not a general-purpose implementation, is the right scope."""
    errors: list[str] = []
    expected_type = schema.get("type")

    if expected_type == "object":
        if not isinstance(data, dict):
            return [f"{path}: expected object, got {type(data).__name__}"]
        for required_key in schema.get("required", []) or []:
            if required_key not in data:
                errors.append(f"{path}: missing required property {required_key!r}")
        properties = schema.get("properties", {}) or {}
        for key, value in data.items():
            if key in properties:
                errors.extend(_validate_against_schema(value, properties[key], f"{path}.{key}"))
    elif expected_type == "array":
        if not isinstance(data, list):
            return [f"{path}: expected array, got {type(data).__name__}"]
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(data):
                errors.extend(_validate_against_schema(item, item_schema, f"{path}[{index}]"))
    elif expected_type == "string":
        if not isinstance(data, str):
            errors.append(f"{path}: expected string, got {type(data).__name__}")
    elif expected_type == "number":
        if not isinstance(data, (int, float)) or isinstance(data, bool):
            errors.append(f"{path}: expected number, got {type(data).__name__}")
    elif expected_type == "integer":
        if not isinstance(data, int) or isinstance(data, bool):
            errors.append(f"{path}: expected integer, got {type(data).__name__}")
    elif expected_type == "boolean":
        if not isinstance(data, bool):
            errors.append(f"{path}: expected boolean, got {type(data).__name__}")

    enum_values = schema.get("enum")
    if enum_values is not None and data not in enum_values:
        errors.append(f"{path}: {data!r} is not one of {enum_values!r}")

    return errors


# -- per-provider native-mode kwargs --------------------------------------


def _native_kwargs_for_active_provider(state, schema: dict, schema_name: str) -> dict | None:
    """None means the active provider/mode has no native structured-output
    affordance at all - the caller falls back to a schema-guided system
    message instead. As of ADR-013 stage 13.3 that is no longer any real
    provider (Anthropic now has a native path too, via tool-forcing below) -
    None stays reachable only for a provider/mode this function doesn't
    recognize, so the fallback remains a real safety net, not dead code.
    Branches on the exact same snapshot fields api_provider.chat() itself
    branches on (state.use_api_mode/local_provider_type/api_provider_type),
    so "which provider is active" is never derived two different,
    potentially-divergent ways."""
    if not state.use_api_mode:
        if state.local_provider_type == config.LOCAL_PROVIDER_OLLAMA:
            # Ollama's `format` accepts a raw JSON Schema dict directly
            # (not just the "json" string shortcut) - verified against the
            # installed ollama SDK's own chat() signature.
            return {"format": schema}
        if state.local_provider_type == config.LOCAL_PROVIDER_LLAMACPP:
            # Verified against llama_cpp.llama_types.
            # ChatCompletionRequestResponseFormat: {"type": "json_object",
            # "schema": <optional JSON Schema>} - llama-cpp-python compiles
            # this into a GBNF grammar server-side.
            return {"response_format": {"type": "json_object", "schema": schema}}
        return None

    if state.api_provider_type == config.API_PROVIDER_OPENAI:
        # Verified against the installed openai SDK's
        # ResponseFormatJSONSchema/JSONSchema TypedDicts - the modern
        # Structured Outputs shape, strict=True for hard schema adherence.
        return {
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema, "strict": True},
            }
        }
    if state.api_provider_type == config.API_PROVIDER_GEMINI:
        # snake_case, matching ChartDataAgent.get_response's own existing
        # (working) response_mime_type convention for this exact provider -
        # both land as extra_kwargs merged wholesale into generationConfig
        # by GeminiProvider._request_body, and Google's protobuf-JSON codec
        # accepts either casing on ingest.
        return {"response_mime_type": "application/json", "response_schema": schema}
    if state.api_provider_type == config.API_PROVIDER_ANTHROPIC:
        # ADR-013 stage 13.3: no native JSON-schema RESPONSE mode, but a
        # forced tool call's arguments ARE hard-schema-constrained - define
        # one single-purpose tool named `schema_name` whose input_schema is
        # exactly the caller's schema, and force it. AnthropicProvider.
        # complete() reads the tool call back as the structured result
        # instead of visible text (see its own request.tool_choice handling).
        return {
            "tools": (
                ToolSpec(
                    name=schema_name,
                    description=f"Return the {schema_name} result, matching the required schema exactly.",
                    input_schema=schema,
                ),
            ),
            "tool_choice": schema_name,
        }
    return None


def _schema_guided_system_message(schema: dict, schema_name: str) -> dict:
    return {
        "role": "system",
        "content": (
            f"You must respond with exactly one raw JSON object named {schema_name!r} that "
            "matches the following JSON Schema precisely. Do not include markdown fences, "
            "explanations, or any wrapper keys - output only the JSON object itself.\n\n"
            f"{json.dumps(schema, indent=2)}"
        ),
    }


def _repair_messages(schema: dict, schema_name: str, raw_response: str, errors: list[str]) -> list:
    # A fresh, standalone turn - NOT the original conversation - directly
    # generalizing ChartDataAgent.repair_chart_data's own shape (a bespoke
    # system+user pair naming the malformed payload, never threaded through
    # the original request's messages).
    return [
        _schema_guided_system_message(schema, schema_name),
        {
            "role": "user",
            "content": (
                "The following response does not match the required schema.\n\n"
                f"--- MALFORMED RESPONSE ---\n{raw_response}\n\n"
                "--- VALIDATION ERRORS ---\n" + "\n".join(errors) + "\n\n"
                "Rewrite it as exactly one valid JSON object matching the schema."
            ),
        },
    ]


def respond_json(
    task: str,
    messages: list,
    schema: dict,
    *,
    schema_name: str = "response",
    runtime=None,
    **kwargs,
) -> dict:
    """One schema-constrained JSON call - see this module's own docstring
    for the native-vs-fallback decision and the validate-repair tail.
    `kwargs` passes through to api_provider.chat() exactly like any other
    chat() caller's passthrough kwargs (e.g. cancellation_event); do not
    pass response_format/format/response_mime_type/response_schema
    yourself - this function owns building those."""
    cancel_event = kwargs.pop("cancellation_event", None)
    state = runtime.snapshot() if runtime is not None else api_provider._snapshot_provider_state()

    call_kwargs = dict(kwargs)
    if runtime is not None:
        call_kwargs["runtime"] = runtime
    if cancel_event is not None:
        call_kwargs["cancellation_event"] = cancel_event

    native_kwargs = _native_kwargs_for_active_provider(state, schema, schema_name)
    first_messages = list(messages) if native_kwargs is not None else (
        [_schema_guided_system_message(schema, schema_name)] + list(messages)
    )

    raw_response = api_provider.chat(
        task=task, messages=first_messages, **call_kwargs, **(native_kwargs or {})
    )["message"]["content"]

    try:
        data = json.loads(_clean_json_response(raw_response))
    except json.JSONDecodeError:
        data = None
    errors = _validate_against_schema(data, schema) if data is not None else ["response was not valid JSON"]
    if not errors:
        return data

    repaired_raw = api_provider.chat(
        task=task,
        messages=_repair_messages(schema, schema_name, raw_response, errors),
        **call_kwargs,
        **(native_kwargs or {}),
    )["message"]["content"]

    try:
        repaired_data = json.loads(_clean_json_response(repaired_raw))
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(f"Model response was not valid JSON after repair: {exc}") from exc

    repair_errors = _validate_against_schema(repaired_data, schema)
    if repair_errors:
        raise StructuredOutputError(
            "Model response did not match the required schema after repair: " + "; ".join(repair_errors)
        )
    return repaired_data
