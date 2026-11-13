"""Schema lookup never executes a Fal model or follows a network reference."""
import json

import pytest

from backend.fal_integration import FalError
from backend.fal_model_contract import model_contract


MODEL = "fal-ai/collection/transcribe"
SUBMIT = "/" + MODEL


def ref(name):
    return {"$ref": f"#/components/schemas/{name}"}


def body(schema):
    return {"content": {"application/json": {"schema": schema}}}


def operation(response):
    return {"responses": {"200": body(response)}}


def details():
    submit = {**operation(ref("QueueStatus")), "requestBody": body(ref("AudioInput"))}
    paths = {
        "/fal-ai/collection/other": {"post": {"requestBody": body({"type": "number"})}},
        SUBMIT + "/requests/{request_id}/status": {"get": operation(ref("QueueStatus"))},
        SUBMIT: {"post": submit},
        SUBMIT + "/requests/{request_id}": {"get": operation(ref("Transcript"))},
    }
    schemas = {
        "AudioInput": {
            "type": "object", "required": ["audio_url"],
            "properties": {
                "audio_url": {"type": "string", "description": "Audio URL", "format": "uri"},
                "options": {"anyOf": [ref("Options"), {"type": "null"}]},
                "examples": {"type": "integer", "minimum": 0, "examples": [1]},
            },
        },
        "Options": {"allOf": [ref("Base"), {"type": "object", "additionalProperties": False}]},
        "Base": {"properties": {"mode": {"enum": ["short", "full"], "default": "full"}}},
        "Transcript": {"type": "object", "properties": {
            "text": {"type": "string"},
            "chunks": {"type": "array", "items": ref("Chunk")},
        }},
        "Chunk": {"oneOf": [{"type": "string"}, {"type": "array", "items": ref("Chunk")}]},
        "QueueStatus": {"type": "object", "properties": {"request_id": {"type": "string"}}},
    }
    return {"endpoint_id": MODEL, "openapi": {
        "openapi": "3.1.0", "servers": [{"url": "https://queue.fal.run"}],
        "paths": paths, "components": {"schemas": schemas},
    }}


@pytest.mark.parametrize("route", [SUBMIT, "/fal-ai/collection"])
def test_exact_endpoint_input_and_final_result_with_reachable_refs(route):
    metadata = details()
    paths = metadata["openapi"]["paths"]
    suffix = "/requests/{request_id}"
    paths[route + suffix] = paths.pop(SUBMIT + suffix)
    original = json.dumps(metadata)
    contract = model_contract(metadata)
    assert json.dumps(metadata) == original
    assert contract["model_id"] == MODEL
    assert contract["input"]["required"] == ["audio_url"]
    fields = contract["input"]["properties"]
    assert fields["audio_url"] == {"type": "string", "format": "uri", "description": "Audio URL"}
    assert fields["examples"] == {"type": "integer", "minimum": 0}
    assert fields["options"]["anyOf"] == [ref("Options"), {"type": "null"}]
    output = contract["output"]["properties"]
    assert output["text"]["type"] == "string"
    assert output["chunks"]["items"] == ref("Chunk")
    actual = contract["components"]["schemas"]
    assert set(actual) == {"Options", "Base", "Chunk"}
    assert actual["Chunk"]["oneOf"][1]["items"] == ref("Chunk")
    assert actual["Base"]["properties"]["mode"] == {"enum": ["short", "full"], "default": "full"}


def test_unavailable_result_never_returns_queue_acknowledgement():
    metadata = details()
    del metadata["openapi"]["paths"][SUBMIT + "/requests/{request_id}"]
    contract = model_contract(metadata)
    assert contract["output"] is None
    assert "output" in contract["warnings"][0]


def test_synchronous_model_with_relative_path_response_and_referenced_request_body():
    submit = {"requestBody": {"$ref": "#/components/requestBodies/Args"},
              "responses": {"200": {"$ref": "#/components/responses/Result"}}}
    metadata = {"endpoint_id": MODEL, "openapi": {
        "servers": [{"url": "https://fal.run/fal-ai/collection"}],
        "paths": {"/transcribe": {"post": submit}}, "components": {
            "requestBodies": {"Args": body({"type": "object"})},
            "responses": {"Result": body({"type": "string"})},
        },
    }}
    contract = model_contract(metadata)
    assert contract == {"model_id": MODEL, "input": {"type": "object"}, "output": {"type": "string"}}


def test_unknown_references_explicit_and_external_refs_are_not_followed():
    metadata = details()
    metadata["openapi"]["components"]["schemas"]["AudioInput"]["properties"].update({
        "extra": {"$ref": "https://elsewhere.test/schema"},
        "no_such_key": ref("Options/missing"),
        "escaped": ref("A~1B~0C"),
    })
    metadata["openapi"]["components"]["schemas"]["A/B~C"] = {"type": "boolean"}
    contract = model_contract(metadata)
    assert contract["components"]["schemas"]["A/B~C"] == {"type": "boolean"}
    assert set(contract["unresolved_refs"]) == {"https://elsewhere.test/schema",
                                                "#/components/schemas/Options/missing"}


@pytest.mark.parametrize("openapi", [
    None, {"error": {"message": "schema unavailable"}}, {"paths": {}},
    {"paths": {"/unrelated": {"post": {"requestBody": body({"type": "number"})}}}},
])
def test_missing_or_unrelated_schemas_report_explicit_error(openapi):
    with pytest.raises(FalError, match="schema"):
        model_contract({"endpoint_id": MODEL, "openapi": openapi})
