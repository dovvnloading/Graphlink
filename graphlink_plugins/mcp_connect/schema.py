"""Bounded JSON Schema validation for MCP arguments, without remote resolution.

The supported vocabulary covers ordinary tool schemas, including local refs,
unions, nested objects/arrays and scalar constraints. Unsupported assertion
keywords fail closed instead of silently allowing an invalid tool call.
"""
from __future__ import annotations

import json
import math
from fractions import Fraction
from typing import Any

import regex

from backend.domain.model import SceneError

MAX_ARGUMENT_BYTES = 256 * 1024
MAX_SCHEMA_BYTES = 64 * 1024
_ANNOTATIONS = {
    "$schema", "$id", "$comment", "$defs", "definitions", "title", "description",
    "default", "examples", "deprecated", "readOnly", "writeOnly", "format",
    "contentEncoding", "contentMediaType", "contentSchema",
}
_ASSERTIONS = {
    "$ref", "type", "enum", "const", "properties", "required", "additionalProperties",
    "patternProperties", "propertyNames", "minProperties", "maxProperties",
    "items", "prefixItems", "minItems", "maxItems", "uniqueItems", "contains",
    "minContains", "maxContains", "minLength", "maxLength", "pattern",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "allOf", "anyOf", "oneOf", "not", "if", "then", "else", "dependentRequired",
    "dependentSchemas",
}
_TYPES = {"null", "boolean", "object", "array", "number", "integer", "string"}


def canonical(value: Any, *, limit: int = MAX_ARGUMENT_BYTES) -> bytes:
    """Reject non-JSON, nonfinite and excessively deep/large payloads before work."""
    count = 0

    def walk(item: Any, depth: int) -> None:
        nonlocal count
        count += 1
        if depth > 40 or count > 50_000:
            raise SceneError("MCP data is too deeply nested or contains too many values.")
        if type(item) is dict:
            if any(not isinstance(key, str) for key in item):
                raise SceneError("MCP object keys must be text.")
            for child in item.values():
                walk(child, depth + 1)
        elif type(item) is list:
            for child in item:
                walk(child, depth + 1)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise SceneError("MCP values must be JSON data.")
        elif type(item) is float and not math.isfinite(item):
            raise SceneError("MCP numbers must be finite.")

    walk(value, 0)
    try:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    except (ValueError, RecursionError, OverflowError) as exc:
        raise SceneError("MCP data cannot be represented as bounded JSON.") from exc
    if len(encoded) > limit:
        raise SceneError(f"MCP data exceeds the {limit // 1024} KiB size limit.")
    return encoded


def pointer(value: Any, path: str) -> Any:
    """RFC 6901 selection, with no attribute access or expression evaluation."""
    if not isinstance(path, str) or len(path) > 2048 or (path and not path.startswith("/")):
        raise SceneError("Use a JSON Pointer beginning with /, or leave it empty for the whole output.")
    for segment in path.split("/")[1:] if path else ():
        if regex.search(r"~(?![01])", segment):
            raise SceneError("JSON Pointer escapes must be ~0 or ~1.")
        key = segment.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and regex.fullmatch(r"0|[1-9][0-9]*", key) and len(key) < 10 and int(key) < len(value):
            value = value[int(key)]
        else:
            raise SceneError("The selected JSON Pointer is unavailable in this output.")
    return value


def _resolve(root: dict, reference: Any) -> Any:
    if not isinstance(reference, str) or not reference.startswith("#"):
        raise SceneError("MCP schemas may use only local JSON Pointer references.")
    result = pointer(root, reference[1:])
    if type(result) not in (dict, bool):
        raise SceneError("The MCP schema reference does not point to a schema.")
    return result


def check_schema(schema: Any) -> dict:
    """Validate the supported vocabulary once before saving or executing a tool."""
    if not isinstance(schema, dict):
        raise SceneError("The tool must advertise an object JSON Schema.")
    canonical(schema, limit=MAX_SCHEMA_BYTES)

    def visit(item: Any, depth: int = 0) -> None:
        if type(item) is bool:
            return
        if not isinstance(item, dict) or depth > 40:
            raise SceneError("The tool schema is malformed or too deeply nested.")
        unknown = set(item) - _ANNOTATIONS - _ASSERTIONS
        if unknown:
            raise SceneError("The tool schema uses an unsupported keyword: " + sorted(unknown)[0] + ".")
        if "$ref" in item:
            _resolve(schema, item["$ref"])
        if "type" in item:
            kinds = item["type"] if isinstance(item["type"], list) else [item["type"]]
            if not kinds or any(not isinstance(kind, str) or kind not in _TYPES for kind in kinds):
                raise SceneError("The tool schema contains an invalid type.")
        for key in ("enum",):
            if key in item and (not isinstance(item[key], list) or not item[key]):
                raise SceneError("The tool schema contains an invalid enum.")
        for key in ("required",):
            if key in item and (not isinstance(item[key], list) or any(not isinstance(s, str) for s in item[key])):
                raise SceneError("The tool schema contains invalid required properties.")
        for key in ("minProperties", "maxProperties", "minItems", "maxItems", "minContains", "maxContains", "minLength", "maxLength"):
            if key in item and (type(item[key]) is not int or item[key] < 0):
                raise SceneError("The tool schema contains an invalid length constraint.")
        for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"):
            if key in item and (type(item[key]) not in (float, int) or (key == "multipleOf" and item[key] <= 0)):
                raise SceneError("The tool schema contains an invalid numeric constraint.")
        if "uniqueItems" in item and type(item["uniqueItems"]) is not bool:
            raise SceneError("The tool schema contains an invalid uniqueItems constraint.")
        for key in ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas"):
            if key in item:
                if not isinstance(item[key], dict):
                    raise SceneError("The tool schema contains an invalid property map.")
                for name, child in item[key].items():
                    if key == "patternProperties":
                        _pattern(name, "")
                    visit(child, depth + 1)
        for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
            if key in item:
                if not isinstance(item[key], list) or (not item[key] and key != "prefixItems"):
                    raise SceneError("The tool schema contains an invalid schema list.")
                for child in item[key]:
                    visit(child, depth + 1)
        for key in ("items", "additionalProperties", "propertyNames", "contains", "not", "if", "then", "else"):
            if key in item:
                visit(item[key], depth + 1)
        if "dependentRequired" in item:
            dependencies = item["dependentRequired"]
            if not isinstance(dependencies, dict) or any(
                not isinstance(values, list) or any(not isinstance(v, str) for v in values)
                for values in dependencies.values()
            ):
                raise SceneError("The tool schema contains invalid dependent properties.")
        if "pattern" in item:
            _pattern(item["pattern"], "")

    visit(schema)
    return schema


def _pattern(pattern: Any, value: str) -> bool:
    if not isinstance(pattern, str) or len(pattern) > 2048:
        raise SceneError("The tool schema pattern is invalid or too long.")
    try:
        return regex.search(pattern, value, timeout=0.025) is not None
    except (regex.error, TimeoutError) as exc:
        raise SceneError("The tool schema pattern is invalid or exceeded its validation time limit.") from exc


def validate(value: Any, schema: dict, *, label: str = "Arguments", limit: int = MAX_ARGUMENT_BYTES) -> None:
    check_schema(schema)
    canonical(value, limit=limit)
    visits = 0

    def matches(item: Any, rule: Any, path: str, depth: int) -> bool:
        try:
            test(item, rule, path, depth)
            return True
        except _Mismatch:
            return False

    def test(item: Any, rule: Any, path: str, depth: int) -> None:
        nonlocal visits
        visits += 1
        if depth > 64 or visits > 20_000:
            raise SceneError("The tool schema exceeds the validation complexity limit.")
        if rule is True:
            return
        if rule is False:
            raise _Mismatch(f"{path} is not allowed by the tool schema.")
        if "$ref" in rule:
            test(item, _resolve(schema, rule["$ref"]), path, depth + 1)
        expected = rule.get("type")
        types = expected if isinstance(expected, list) else [expected] if expected else []
        actual = ("null" if item is None else "boolean" if type(item) is bool else
                  "object" if isinstance(item, dict) else "array" if isinstance(item, list) else
                  "string" if isinstance(item, str) else "integer" if type(item) is int or
                  type(item) is float and item.is_integer() else "number")
        if types and actual not in types and not (actual == "integer" and "number" in types):
            raise _Mismatch(f"{path} must be {' or '.join(types)}.")
        if "enum" in rule and not any(_equal(item, choice) for choice in rule["enum"]):
            raise _Mismatch(f"{path} must match one of the tool's allowed values.")
        if "const" in rule and not _equal(item, rule["const"]):
            raise _Mismatch(f"{path} must match the tool's required constant.")
        for child in rule.get("allOf", []):
            test(item, child, path, depth + 1)
        for key, required in (("anyOf", None), ("oneOf", 1)):
            if key in rule:
                count = sum(matches(item, child, path, depth + 1) for child in rule[key])
                if (required is None and not count) or (required is not None and count != required):
                    raise _Mismatch(f"{path} does not match the tool's {key} alternatives.")
        if "not" in rule and matches(item, rule["not"], path, depth + 1):
            raise _Mismatch(f"{path} matches a disallowed tool schema.")
        if "if" in rule:
            branch = "then" if matches(item, rule["if"], path, depth + 1) else "else"
            if branch in rule:
                test(item, rule[branch], path, depth + 1)
        if isinstance(item, dict):
            for key in rule.get("required", []):
                if key not in item:
                    raise _Mismatch(f"{path}.{key} is required.")
            _length(len(item), rule, "Properties", path)
            properties = rule.get("properties", {})
            patterns = rule.get("patternProperties", {})
            for key, child in item.items():
                covered = False
                if key in properties:
                    test(child, properties[key], f"{path}.{key}", depth + 1)
                    covered = True
                for pattern, sub in patterns.items():
                    if _pattern(pattern, key):
                        test(child, sub, f"{path}.{key}", depth + 1)
                        covered = True
                if not covered:
                    test(child, rule.get("additionalProperties", True), f"{path}.{key}", depth + 1)
                if "propertyNames" in rule:
                    test(key, rule["propertyNames"], path, depth + 1)
            for key, needed in rule.get("dependentRequired", {}).items():
                if key in item and any(name not in item for name in needed):
                    raise _Mismatch(f"{path}.{key} requires its dependent fields.")
            for key, sub in rule.get("dependentSchemas", {}).items():
                if key in item:
                    test(item, sub, path, depth + 1)
        elif isinstance(item, list):
            _length(len(item), rule, "Items", path)
            prefix = rule.get("prefixItems", [])
            for index, child in enumerate(item):
                test(child, prefix[index] if index < len(prefix) else rule.get("items", True), f"{path}[{index}]", depth + 1)
            if rule.get("uniqueItems") and len({_identity(child) for child in item}) != len(item):
                raise _Mismatch(f"{path} must contain unique items.")
            if "contains" in rule:
                count = sum(matches(child, rule["contains"], path, depth + 1) for child in item)
                if count < rule.get("minContains", 1) or count > rule.get("maxContains", len(item)):
                    raise _Mismatch(f"{path} does not contain the required matching items.")
        elif isinstance(item, str):
            _length(len(item), rule, "Length", path)
            if "pattern" in rule and not _pattern(rule["pattern"], item):
                raise _Mismatch(f"{path} does not match the tool's required pattern.")
        elif type(item) in (int, float):
            for key, failed in (("minimum", lambda a, b: a < b), ("maximum", lambda a, b: a > b),
                                ("exclusiveMinimum", lambda a, b: a <= b), ("exclusiveMaximum", lambda a, b: a >= b)):
                if key in rule and failed(item, rule[key]):
                    raise _Mismatch(f"{path} is outside the tool's allowed numeric range.")
            if "multipleOf" in rule:
                quotient = Fraction(str(item)) / Fraction(str(rule["multipleOf"]))
                if quotient.denominator != 1:
                    raise _Mismatch(f"{path} must be a multiple of the tool's allowed increment.")

    try:
        test(value, schema, label, 0)
    except _Mismatch as exc:
        raise SceneError(str(exc)) from exc


class _Mismatch(ValueError):
    pass


def _length(length: int, rule: dict, suffix: str, path: str) -> None:
    if length < rule.get("min" + suffix, 0) or length > rule.get("max" + suffix, length):
        raise _Mismatch(f"{path} is outside the tool's allowed size range.")


def _identity(value: Any) -> str:
    # JSON Schema considers 1 and 1.0 equal, but never true and 1.
    if type(value) in (int, float):
        return "number:" + str(Fraction(str(value)))
    if isinstance(value, list):
        return "array:" + json.dumps([_identity(child) for child in value])
    if isinstance(value, dict):
        return "object:" + json.dumps({key: _identity(child) for key, child in sorted(value.items())})
    return json.dumps(value)


def _equal(a: Any, b: Any) -> bool:
    return _identity(a) == _identity(b)
