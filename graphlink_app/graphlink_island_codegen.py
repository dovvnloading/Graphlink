"""Generate the checked-in TypeScript wire-contract artifacts from the Python
payload dataclasses.

Emits two files per island, both committed and both guarded by a staleness
pytest (the pattern already established for gl-theme.css / gl-vars-dev.css):

  <name>.schema.json  - the JSON Schema, as the language-neutral contract
  <name>.ts           - TS types + a runtime validator generated from it

The runtime validator matters more than the types here. TypeScript types are
erased at build time and do exactly nothing when a malformed payload actually
arrives over the bridge at runtime - which is precisely the situation
bridge.ts's parseState() mishandles today by returning null and silently
freezing the UI. A generated validator that runs on real data is what makes
the "visible error state" fix possible at all, so it is generated from the same
single source rather than hand-written alongside it.

Generation is one-directional (Python -> TS) and the output is committed, so a
single Python-side staleness test is sufficient; there is no TS-side generator
to drift independently.
"""

from __future__ import annotations

import json
from typing import Any

from graphlink_island_schema import json_schema_for

__all__ = ["schema_json_for", "typescript_for"]

_HEADER = (
    "/* GENERATED - do not hand-edit. Source of truth: {source}.\n"
    " * Regenerate with graphlink_island_codegen.py; a pytest fails if this file\n"
    " * drifts from what regenerating it now would produce. */\n"
)


def schema_json_for(dataclass_type: type, *, title: str) -> str:
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": title,
        **json_schema_for(dataclass_type),
    }
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def _ts_type_name(dataclass_name: str) -> str:
    """ComposerDraftPayload -> ComposerDraft. The `Payload` suffix exists to
    keep the Python classes distinguishable from graphlink_composer.py's
    same-named domain models; it carries no meaning on the TS side, where the
    wire shape is the only shape there is."""
    return dataclass_name.removesuffix("Payload")


def _ts_type_for(schema: dict[str, Any], *, inline_name: str) -> str:
    kind = schema.get("type")
    if "enum" in schema:
        return " | ".join(json.dumps(value) for value in schema["enum"])
    if kind == "string":
        return "string"
    if kind == "boolean":
        return "boolean"
    if kind in ("integer", "number"):
        return "number"
    if kind == "array":
        return f"{_ts_type_for(schema['items'], inline_name=inline_name)}[]"
    if kind == "object":
        if "additionalProperties" in schema and schema["additionalProperties"] is not False:
            value_type = _ts_type_for(schema["additionalProperties"], inline_name=inline_name)
            return f"Record<string, {value_type}>"
        return inline_name
    raise ValueError(f"cannot render TS type for schema fragment: {schema!r}")


def _collect_object_types(
    dataclass_type: type,
    *,
    out: dict[str, dict[str, Any]],
) -> None:
    """Walk the dataclass graph, recording one named TS interface per nested
    dataclass so the generated file mirrors the Python structure rather than
    emitting one giant anonymous nested type."""
    import dataclasses
    import typing
    from typing import get_args, get_origin

    name = _ts_type_name(dataclass_type.__name__)
    if name in out:
        return
    out[name] = {}  # reserve first, so a self-referential shape can't recurse forever

    hints = typing.get_type_hints(dataclass_type)
    fields: dict[str, Any] = {}

    for field in dataclasses.fields(dataclass_type):
        annotation = hints[field.name]
        inner = annotation
        optional = False
        origin = get_origin(annotation)
        import types as _types

        if origin is _types.UnionType or origin is typing.Union:
            args = [a for a in get_args(annotation) if a is not type(None)]
            inner, optional = args[0], True

        fields[field.name] = {"annotation": inner, "optional": optional}

        for nested in _nested_dataclasses(inner):
            _collect_object_types(nested, out=out)

    out[name] = fields


def _nested_dataclasses(annotation: Any) -> list[type]:
    import dataclasses
    from typing import get_args, get_origin

    found: list[type] = []
    if dataclasses.is_dataclass(annotation) and isinstance(annotation, type):
        found.append(annotation)
        return found
    origin = get_origin(annotation)
    if origin in (list, dict):
        for arg in get_args(annotation):
            found.extend(_nested_dataclasses(arg))
    return found


def _annotation_to_ts(annotation: Any) -> str:
    import dataclasses
    import typing
    from typing import Literal, get_args, get_origin

    if dataclasses.is_dataclass(annotation) and isinstance(annotation, type):
        return _ts_type_name(annotation.__name__)
    if annotation is str:
        return "string"
    if annotation is bool:
        return "boolean"
    if annotation in (int, float):
        return "number"

    origin = get_origin(annotation)
    if origin is Literal:
        return " | ".join(json.dumps(v) for v in get_args(annotation))
    if origin is list:
        (item,) = get_args(annotation)
        return f"{_annotation_to_ts(item)}[]"
    if origin is dict:
        _, value = get_args(annotation)
        return f"Record<string, {_annotation_to_ts(value)}>"
    if origin is typing.Union or origin is __import__("types").UnionType:
        args = [a for a in get_args(annotation) if a is not type(None)]
        return " | ".join(_annotation_to_ts(a) for a in args)
    raise ValueError(f"cannot render TS type for annotation: {annotation!r}")


def typescript_for(dataclass_type: type, *, source: str) -> str:
    """Emit TS interfaces plus a runtime validator for the payload graph."""
    collected: dict[str, dict[str, Any]] = {}
    _collect_object_types(dataclass_type, out=collected)

    lines: list[str] = [_HEADER.format(source=source)]

    # Interfaces, root last so the file reads bottom-up like the payload nests.
    root_name = _ts_type_name(dataclass_type.__name__)
    ordered = [n for n in collected if n != root_name] + [root_name]

    for name in ordered:
        lines.append(f"export interface {name} {{")
        for field_name, info in collected[name].items():
            ts_type = _annotation_to_ts(info["annotation"])
            if info["optional"]:
                # `?: T | null` rather than just `?: T`, because BOTH shapes
                # genuinely occur on this wire: Python's None serializes to an
                # explicit JSON `null` when the key is built unconditionally
                # (context.anchor, request.id), while a key the sender omits
                # entirely is absent (route.modelValue, only present on the
                # llama.cpp branch). Emitting only `?` would typecheck against
                # the absent case and lie about the null one.
                lines.append(f"  {field_name}?: {ts_type} | null;")
            else:
                lines.append(f"  {field_name}: {ts_type};")
        lines.append("}")
        lines.append("")

    lines.append(_VALIDATOR_PREAMBLE)

    for name in ordered:
        lines.append(_validator_for(name, collected[name]))

    lines.append(
        f"export function validate{root_name}(value: unknown): ValidationResult<{root_name}> {{\n"
        f"  const errors: string[] = [];\n"
        f"  check{root_name}(value, \"$\", errors);\n"
        f"  return errors.length === 0\n"
        f"    ? {{ ok: true, value: value as {root_name} }}\n"
        f"    : {{ ok: false, errors }};\n"
        f"}}\n"
    )

    return "\n".join(lines)


_VALIDATOR_PREAMBLE = '''export type ValidationResult<T> =
  | { ok: true; value: T }
  | { ok: false; errors: string[] };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// Unknown keys are tolerated on purpose. The JSON Schema marks the contract
// additionalProperties:false because Python and the schema must not drift, but
// an incoming payload carrying a field this build has never heard of is the
// normal, expected shape of a NEWER compatible sender - rejecting it here would
// defeat the additive-forward-compatibility the version negotiation exists to
// provide. Missing or wrongly-typed KNOWN fields are still hard errors.
'''


def _ts_check_expr(annotation: Any, *, value_expr: str, path_expr: str) -> str:
    """Inline runtime check for one value, appending to `errors`.

    `path_expr` is a complete JS expression evaluating to a string. Error
    messages concatenate onto it rather than interpolating it into another
    template literal, which would nest backticks and emit valid-but-ugly
    `${`${path}.id`}` generated code.
    """
    import dataclasses
    from typing import Literal, get_args, get_origin

    if dataclasses.is_dataclass(annotation) and isinstance(annotation, type):
        name = _ts_type_name(annotation.__name__)
        return f"check{name}({value_expr}, {path_expr}, errors);"

    origin = get_origin(annotation)

    if origin is Literal:
        allowed = list(get_args(annotation))
        allowed_ts = ", ".join(json.dumps(v) for v in allowed)
        allowed_msg = json.dumps(", ".join(allowed))
        return (
            f"if (![{allowed_ts}].includes({value_expr} as string)) "
            f"errors.push({path_expr} + `: ${{JSON.stringify({value_expr})}} is not one of "
            f"[` + {allowed_msg} + `]`);"
        )

    if annotation is str:
        return (
            f'if (typeof {value_expr} !== "string") '
            f'errors.push({path_expr} + ": expected string");'
        )
    if annotation is bool:
        return (
            f'if (typeof {value_expr} !== "boolean") '
            f'errors.push({path_expr} + ": expected boolean");'
        )
    if annotation in (int, float):
        return (
            f'if (typeof {value_expr} !== "number") '
            f'errors.push({path_expr} + ": expected number");'
        )

    if origin is list:
        (item,) = get_args(annotation)
        inner = _ts_check_expr(
            item, value_expr="item", path_expr=f"{path_expr} + `[${{i}}]`"
        )
        return (
            f"if (!Array.isArray({value_expr})) "
            f'errors.push({path_expr} + ": expected array");\n'
            f"    else ({value_expr} as unknown[]).forEach((item, i) => {{ {inner} }});"
        )

    if origin is dict:
        _, value_type = get_args(annotation)
        inner = _ts_check_expr(
            value_type, value_expr="v", path_expr=f"{path_expr} + `.${{k}}`"
        )
        return (
            f"if (!isRecord({value_expr})) "
            f'errors.push({path_expr} + ": expected object");\n'
            f"    else Object.entries({value_expr} as Record<string, unknown>)"
            f".forEach(([k, v]) => {{ {inner} }});"
        )

    raise ValueError(f"cannot render TS runtime check for: {annotation!r}")


def _validator_for(name: str, fields: dict[str, Any]) -> str:
    body: list[str] = [
        f"function check{name}(value: unknown, path: string, errors: string[]): void {{",
        "  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }",
    ]
    for field_name, info in fields.items():
        annotation = info["annotation"]
        optional = info["optional"]
        access = f'value[{json.dumps(field_name)}]'
        field_path = "`${path}." + field_name + "`"
        check = _ts_check_expr(annotation, value_expr="fieldValue", path_expr=field_path)
        body.append(f"  {{")
        body.append(f"    const fieldValue = {access};")
        if optional:
            body.append(f"    if (fieldValue !== undefined && fieldValue !== null) {{ {check} }}")
        else:
            body.append(
                f"    if (fieldValue === undefined || fieldValue === null) "
                f"errors.push(`${{path}}.{field_name}: missing required field`);"
            )
            body.append(f"    else {{ {check} }}")
        body.append("  }")
    body.append("}")
    body.append("")
    return "\n".join(body)
