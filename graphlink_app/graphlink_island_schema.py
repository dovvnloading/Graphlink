"""Generic dataclass -> JSON Schema generation and payload validation for
island bridge wire contracts.

WHY HAND-ROLLED RATHER THAN pydantic/dataclasses-json: the payload shapes this
has to describe are deliberately narrow - strings, ints, bools, string-literal
enums, lists, nested objects, and one string->string map. That is a small,
closed set that dataclass introspection covers in a couple hundred readable
lines, and it keeps the wire contract free of a runtime dependency that would
then sit in the shipped app for the sole benefit of a build-time artifact.
Same reasoning that produced the hand-rolled QSS generator and
css_custom_properties(); the counter-example (Tailwind) was adopted precisely
because hand-rolling a CSS utility compiler genuinely would have been absurd.
This is not that.

SUPPORTED TYPE CONSTRUCTS, exhaustively - anything else raises rather than
silently emitting a wrong schema:
  str, int, float, bool
  Literal["a", "b", ...]        -> enum of strings
  list[X]                       -> array of X
  dict[str, str]                -> object with additionalProperties: string
  X | None                      -> X, and the field becomes not-required
  a nested @dataclass           -> inlined object ($defs/$ref is deliberately
                                   NOT used; these payloads are shallow and
                                   inlining keeps the generated TS readable)

The refusal to guess is the point: a future payload field typed as something
outside this set fails loudly at generation time instead of producing a schema
that quietly disagrees with the real payload.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from typing import Any, Literal, get_args, get_origin

__all__ = [
    "json_schema_for",
    "validate_payload",
    "SchemaGenerationError",
]


class SchemaGenerationError(TypeError):
    """A payload dataclass uses a type this generator refuses to guess at."""


def _is_dataclass_type(annotation: Any) -> bool:
    return dataclasses.is_dataclass(annotation) and isinstance(annotation, type)


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """Return (inner_type, is_optional) for `X | None` / Optional[X]."""
    origin = get_origin(annotation)
    if origin is types.UnionType or origin is typing.Union:
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(args) != 1:
            raise SchemaGenerationError(
                f"Union type {annotation!r} is not supported - only `X | None` is. "
                "A genuine multi-type union needs an explicit design decision "
                "about how it crosses the wire, not a guess."
            )
        return args[0], True
    return annotation, False


_PRIMITIVES: dict[Any, str] = {
    str: "string",
    bool: "boolean",
    int: "integer",
    float: "number",
}


def _schema_for_annotation(annotation: Any, *, path: str) -> dict[str, Any]:
    inner, _ = _unwrap_optional(annotation)

    # bool must be checked before int: bool is a subclass of int in Python, and
    # dict lookup by identity happens to work here, but being explicit stops a
    # future refactor to isinstance-based dispatch from silently mistyping it.
    if inner in _PRIMITIVES:
        return {"type": _PRIMITIVES[inner]}

    origin = get_origin(inner)

    if origin is Literal:
        values = list(get_args(inner))
        if not all(isinstance(value, str) for value in values):
            raise SchemaGenerationError(
                f"{path}: only string Literal values are supported, got {values!r}"
            )
        return {"type": "string", "enum": values}

    if origin is list:
        (item_type,) = get_args(inner)
        return {"type": "array", "items": _schema_for_annotation(item_type, path=f"{path}[]")}

    if origin is dict:
        key_type, value_type = get_args(inner)
        if key_type is not str:
            raise SchemaGenerationError(f"{path}: dict keys must be str, got {key_type!r}")
        return {
            "type": "object",
            "additionalProperties": _schema_for_annotation(value_type, path=f"{path}{{}}"),
        }

    if _is_dataclass_type(inner):
        return json_schema_for(inner, _path=path)

    raise SchemaGenerationError(
        f"{path}: unsupported type {inner!r}. Extend graphlink_island_schema.py "
        "deliberately rather than working around this - an unsupported type here "
        "means the generated schema would not describe the real payload."
    )


def json_schema_for(dataclass_type: type, *, _path: str = "") -> dict[str, Any]:
    """Build a JSON Schema (draft 2020-12) object for one payload dataclass.

    `additionalProperties: false` is emitted deliberately: the wire contract is
    generated from exactly this dataclass, so an unexpected key means Python and
    the schema have drifted, and that must fail loudly rather than pass. Note
    this is the SCHEMA's strictness, not the versioning policy - additive
    forward-compatibility is handled at the version-negotiation layer, which
    decides whether an unknown-but-newer payload is acceptable, not here.
    """
    if not _is_dataclass_type(dataclass_type):
        raise SchemaGenerationError(f"{dataclass_type!r} is not a dataclass type")

    hints = typing.get_type_hints(dataclass_type)
    properties: dict[str, Any] = {}
    required: list[str] = []

    for field in dataclasses.fields(dataclass_type):
        annotation = hints[field.name]
        field_path = f"{_path}.{field.name}" if _path else f"{dataclass_type.__name__}.{field.name}"
        _, is_optional = _unwrap_optional(annotation)
        properties[field.name] = _schema_for_annotation(annotation, path=field_path)
        if not is_optional:
            required.append(field.name)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
    return schema


def validate_payload(payload: Any, dataclass_type: type, *, _path: str = "$") -> list[str]:
    """Validate a real payload dict against a payload dataclass definition.

    Returns a list of human-readable error strings; empty means valid. Walks the
    dataclass definitions directly rather than interpreting the emitted JSON
    Schema, deliberately: a bug in the schema GENERATOR would otherwise be
    invisible to a validator that only ever reads the generator's own output.
    Checking the payload against the dataclasses independently means the
    generated schema and this validator can disagree - and a test that asserts
    the real bridge payload satisfies both is then a genuine cross-check, not a
    tautology.
    """
    errors: list[str] = []

    if not isinstance(payload, dict):
        return [f"{_path}: expected object, got {type(payload).__name__}"]

    hints = typing.get_type_hints(dataclass_type)
    known: set[str] = set()

    for field in dataclasses.fields(dataclass_type):
        annotation = hints[field.name]
        known.add(field.name)
        inner, is_optional = _unwrap_optional(annotation)
        field_path = f"{_path}.{field.name}"

        if field.name not in payload:
            if not is_optional:
                errors.append(f"{field_path}: missing required field")
            continue

        value = payload[field.name]
        if value is None:
            if not is_optional:
                errors.append(f"{field_path}: null is not allowed for a required field")
            continue

        errors.extend(_validate_value(value, inner, path=field_path))

    for key in payload:
        if key not in known:
            errors.append(f"{_path}.{key}: unexpected field not present in {dataclass_type.__name__}")

    return errors


def _validate_value(value: Any, annotation: Any, *, path: str) -> list[str]:
    inner, _ = _unwrap_optional(annotation)

    if inner is bool:
        return [] if isinstance(value, bool) else [f"{path}: expected boolean, got {type(value).__name__}"]
    if inner is str:
        return [] if isinstance(value, str) else [f"{path}: expected string, got {type(value).__name__}"]
    if inner is int:
        # bool is a subclass of int; a boolean where an integer belongs is a
        # real mismatch, not an acceptable widening.
        if isinstance(value, bool) or not isinstance(value, int):
            return [f"{path}: expected integer, got {type(value).__name__}"]
        return []
    if inner is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return [f"{path}: expected number, got {type(value).__name__}"]
        return []

    origin = get_origin(inner)

    if origin is Literal:
        allowed = list(get_args(inner))
        return [] if value in allowed else [f"{path}: {value!r} is not one of {allowed!r}"]

    if origin is list:
        if not isinstance(value, list):
            return [f"{path}: expected array, got {type(value).__name__}"]
        (item_type,) = get_args(inner)
        errors: list[str] = []
        for index, item in enumerate(value):
            errors.extend(_validate_value(item, item_type, path=f"{path}[{index}]"))
        return errors

    if origin is dict:
        if not isinstance(value, dict):
            return [f"{path}: expected object, got {type(value).__name__}"]
        _, value_type = get_args(inner)
        errors = []
        for key, item in value.items():
            if not isinstance(key, str):
                errors.append(f"{path}: key {key!r} is not a string")
            errors.extend(_validate_value(item, value_type, path=f"{path}.{key}"))
        return errors

    if _is_dataclass_type(inner):
        return validate_payload(value, inner, _path=path)

    return [f"{path}: unsupported type {inner!r} in payload definition"]
