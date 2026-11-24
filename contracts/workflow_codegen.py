"""Generate standalone workflow schemas and static TS types.

The workflow formats are not WebSocket topics. They need recursive JSON,
discriminated unions and immutable tuple collections, which the deliberately
narrow live-topic generator does not support. Keep that generator unchanged.
"""

from __future__ import annotations

import dataclasses
import json
import types
import typing
from pathlib import Path
from typing import Literal, TypeAliasType, get_args, get_origin, get_type_hints

from backend.workflow import contracts as model
from backend.workflow.ports import ALLOWED_COERCIONS

OUTPUT = Path(__file__).resolve().parents[1] / "web_ui" / "src" / "lib" / "workflow-contract" / "generated"
ROOTS = {
    "port-value": model.PortValue,
    "port-spec": model.PortSpec,
    "adapter-descriptor": model.AdapterDescriptor,
    "workflow-spec": model.WorkflowSpec,
    "workflow-run": model.WorkflowRunRecord,
    "workflow-step": model.WorkflowStepRecord,
    "workflow-event": model.WorkflowEventRecord,
}
_PRIMITIVES = {str: "string", int: "integer", float: "number", bool: "boolean", type(None): "null"}
_TS_PRIMITIVES = {**_PRIMITIVES, int: "number"}
_NAMED = {item.__name__: item for item in model.WIRE_TYPES}
_HEADER = "// GENERATED - do not hand-edit. Source: backend/workflow/contracts.py and ports.py.\n"


def _named(annotation: object) -> bool:
    return _NAMED.get(getattr(annotation, "__name__", "")) is annotation


def _fields(annotation: type) -> dict:
    hints = get_type_hints(annotation)
    return {field.name: hints[field.name] for field in dataclasses.fields(annotation)}


def _schema(annotation: object, definitions: dict) -> dict:
    if annotation in _PRIMITIVES:
        return {"type": _PRIMITIVES[annotation]}
    if _named(annotation):
        name = annotation.__name__
        if name not in definitions:
            definitions[name] = {}  # reserve before recursing into JsonData
            if isinstance(annotation, TypeAliasType):
                definitions[name] = _schema(annotation.__value__, definitions)
            else:
                fields = _fields(annotation)
                definitions[name] = {
                    "type": "object", "additionalProperties": False, "required": list(fields),
                    "properties": {key: _schema(value, definitions) for key, value in fields.items()},
                }
        return {"$ref": f"#/$defs/{name}"}
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is Literal:
        return {"enum": list(args)}
    if origin in (types.UnionType, typing.Union):
        return {"anyOf": [_schema(arg, definitions) for arg in args]}
    if origin is list or (origin is tuple and len(args) == 2 and args[1] is Ellipsis):
        return {"type": "array", "items": _schema(args[0], definitions)}
    if origin is dict and args[0] is str:
        return {"type": "object", "additionalProperties": _schema(args[1], definitions)}
    raise TypeError(f"Unsupported workflow wire annotation: {annotation!r}")


def schema_for(root: object) -> str:
    definitions: dict = {}
    ref = _schema(root, definitions)
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": root.__name__,
        **ref,
        "$defs": definitions,
    }
    return json.dumps(schema, sort_keys=True, indent=2) + "\n"


def _typescript(annotation: object) -> str:
    if annotation in _TS_PRIMITIVES:
        return _TS_PRIMITIVES[annotation]
    if _named(annotation):
        return annotation.__name__
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is Literal:
        return " | ".join(json.dumps(arg) for arg in args)
    if origin in (types.UnionType, typing.Union):
        return " | ".join(dict.fromkeys(_typescript(arg) for arg in args))
    if origin is list or (origin is tuple and len(args) == 2 and args[1] is Ellipsis):
        return f"ReadonlyArray<{_typescript(args[0])}>"
    if origin is dict and args[0] is str:
        return "{ readonly [key: string]: " + _typescript(args[1]) + " }"
    raise TypeError(f"Unsupported workflow wire annotation: {annotation!r}")


def typescript_for() -> str:
    lines = [_HEADER]
    for item in model.WIRE_TYPES:
        if isinstance(item, TypeAliasType):
            lines.append(f"export type {item.__name__} = {_typescript(item.__value__)};")
        else:
            lines.append(f"export interface {item.__name__} {{")
            for key, value in _fields(item).items():
                lines.append(f"  readonly {key}: {_typescript(value)};")
            lines.append("}")
        lines.append("")
    lines.append(f"export const ALLOWED_COERCIONS = {json.dumps(ALLOWED_COERCIONS)} as const;")
    return "\n".join(lines) + "\n"


def generated_workflow_artifacts() -> dict[Path, str]:
    """The separate non-topic inventory, checked by codegen.py --check."""
    artifacts = {OUTPUT / f"{name}.schema.json": schema_for(root) for name, root in ROOTS.items()}
    artifacts[OUTPUT / "workflow.ts"] = typescript_for()
    return artifacts
