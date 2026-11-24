"""Tool inputs use bounded JSON Schema checks and literal JSON Pointers only."""
from __future__ import annotations

import pytest

from backend.domain.model import SceneError
from graphlink_plugins.mcp_connect.schema import canonical, check_schema, pointer, validate


@pytest.mark.parametrize(("value", "schema"), [
    ({"name": "Ada", "age": 36}, {"type": "object", "required": ["name"], "additionalProperties": False,
                                  "properties": {"name": {"type": "string"}, "age": {"type": "integer", "minimum": 0}}}),
    ({"value": ["a", None]}, {"type": "object", "properties": {"value": {"type": "array", "items": {"type": ["string", "null"]}}}}),
    (2.0, {"type": "integer", "multipleOf": 0.1}),
    (None, {"enum": [True, 1, None]}),
    ([1, True], {"uniqueItems": True}),
    ({"x": 3}, {"$defs": {"positive": {"type": "number", "exclusiveMinimum": 0}},
                "properties": {"x": {"$ref": "#/$defs/positive"}}}),
    ({"kind": "x", "x": "yes"}, {"if": {"properties": {"kind": {"const": "x"}}}, "then": {"required": ["x"]},
                                  "else": {"required": ["y"]}}),
    ({"kind": "y", "y": "yes"}, {"if": {"properties": {"kind": {"const": "x"}}}, "then": {"required": ["x"]},
                                  "else": {"required": ["y"]}}),
    ({"name": "Jane", "email": "a@b"}, {"dependentRequired": {"name": ["email"]},
                                        "dependentSchemas": {"name": {"properties": {"email": {"type": "string"}}}}}),
    (["a", 2, 3], {"prefixItems": [{"type": "string"}], "items": {"type": "integer"},
                   "contains": {"type": "number"}, "minContains": 2, "maxContains": 2}),
    ({"x_a": 1}, {"patternProperties": {"^x_": {"type": "number"}}, "additionalProperties": False,
                  "propertyNames": {"minLength": 2}}),
    ("hello", {"type": "string", "minLength": 2, "maxLength": 8, "pattern": "^h.*o$", "format": "annotation-only"}),
    (5, {"allOf": [{"minimum": 1}, {"maximum": 8}], "anyOf": [{"const": 5}, {"type": "string"}], "not": {"const": 9}}),
    ("a", {"oneOf": [{"type": "string"}, {"type": "number"}]}),
])
def test_supported_schema_accepts_valid_json(value, schema):
    validate(value, schema)


@pytest.mark.parametrize(("value", "schema", "message"), [
    ({}, {"required": ["query"]}, "required"),
    ({"extra": 1}, {"additionalProperties": False}, "not allowed"),
    (True, {"type": "integer"}, "integer"),
    (1, {"enum": [True]}, "allowed values"),
    (True, {"const": 1}, "constant"),
    ([1, 1.0], {"uniqueItems": True}, "unique"),
    ([{"x": 1}, {"x": 1.0}], {"uniqueItems": True}, "unique"),
    (0.3, {"multipleOf": 0.2}, "multiple"),
    (10, {"exclusiveMaximum": 10}, "range"),
    (0, {"minimum": 1}, "range"),
    ("x", {"minLength": 2}, "size"),
    ({"x": 1}, {"maxProperties": 0}, "size"),
    ([], {"minItems": 1}, "size"),
    ("abcdef", {"pattern": "^[0-9]+$"}, "pattern"),
    ({"name": "A"}, {"dependentRequired": {"name": ["email"]}}, "dependent"),
    ({"a": 1}, {"propertyNames": {"minLength": 2}}, "size"),
    (["a"], {"contains": {"type": "number"}}, "matching"),
    (1, {"oneOf": [{"type": "integer"}, {"type": "number"}]}, "oneOf"),
    (None, {"anyOf": [{"type": "integer"}, {"type": "string"}]}, "anyOf"),
    (1, {"not": {"type": "number"}}, "disallowed"),
])
def test_invalid_arguments_are_rejected_with_field_level_messages(value, schema, message):
    with pytest.raises(SceneError, match=message):
        validate(value, schema)


@pytest.mark.parametrize("schema", [
    {"$ref": "https://example.invalid/schema"},
    {"$ref": "#/missing"},
    {"$defs": {"wrong": 12}, "$ref": "#/$defs/wrong"},
    {"unevaluatedProperties": False},
    {"$dynamicRef": "#self"},
    {"type": "something"},
    {"type": []},
    {"enum": []},
    {"required": "wrong"},
    {"minItems": -1},
    {"minimum": True},
    {"multipleOf": 0},
    {"properties": []},
    {"anyOf": []},
    {"items": []},
    {"dependentRequired": {"x": "y"}},
    {"pattern": "["},
    {"patternProperties": {"[": {"type": "string"}}},
    {"uniqueItems": 1},
])
def test_invalid_or_unsupported_schema_fails_closed_without_remote_resolution(schema):
    with pytest.raises(SceneError):
        check_schema(schema)


def test_recursive_references_are_bounded_and_legitimate_recursive_data_is_supported():
    schema = {"$defs": {"item": {"type": "object", "properties": {"child": {"$ref": "#/$defs/item"}}}},
              "$ref": "#/$defs/item"}
    validate({"child": {"child": {}}}, schema)
    with pytest.raises(SceneError, match="complexity"):
        validate({}, {"$ref": "#"})


def test_pointer_escape_rules_indices_and_empty_keys():
    value = {"a/b": {"~key": [10, 20]}, "": {"value": 3}}
    assert pointer(value, "") is value
    assert pointer(value, "/a~1b/~0key/1") == 20
    assert pointer(value, "//value") == 3
    for path in ("a", "/a~2b", "/a~1b/~0key/-1", "/a~1b/~0key/01", "/a~1b/~0key/-", "/a~1b/~0key/2"):
        with pytest.raises(SceneError):
            pointer(value, path)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "not JSON"}, {"x": object()}, (1, 2)])
def test_non_json_and_nonfinite_values_are_rejected(value):
    with pytest.raises(SceneError):
        canonical(value)


def test_limits_apply_before_validation_and_error_messages_do_not_echo_values():
    secret = "DO_NOT_ECHO_THIS_SECRET"
    with pytest.raises(SceneError) as failure:
        validate({"query": secret}, {"properties": {"query": {"const": "other"}}})
    assert secret not in str(failure.value)
    with pytest.raises(SceneError, match="size limit"):
        canonical("x" * 300_000)
    deep = []
    for _ in range(42):
        deep = [deep]
    with pytest.raises(SceneError, match="deeply nested"):
        canonical(deep)


def test_decimal_multiple_and_numeric_equality_do_not_round_large_values():
    validate(0.3, {"multipleOf": 0.1})
    validate(10**80 + 1, {"const": 10**80 + 1})
    with pytest.raises(SceneError, match="constant"):
        validate(10**80 + 1, {"const": 10**80 + 2})
