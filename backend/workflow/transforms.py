"""Pure, versioned text, JSON and table transformations.

All configuration is declarative. No evaluation of Python, attribute access,
file reads, providers or other external operations are involved.
"""

from __future__ import annotations

import hashlib
import json
import math
import operator
import re
from collections.abc import Mapping
from dataclasses import dataclass
from string import Formatter
from typing import ClassVar

from .adapter import StepContext
from .contracts import (
    AdapterDescriptor, JsonData, JsonScalar, JsonValue, PortKind, PortSet, PortSpec,
    PortValue, SideEffectClass, TableValue, TextValue, WorkflowStepSpec, freeze_config,
)
from .ports import _check_table


_CONFIG_KEYS = {
    "template": {"template"},
    "json_path": {"path"},
    "text_split": {"separator"},
    "text_join": {"separator"},
    "table_filter": {"column", "operator", "value"},
    "table_sort": {"column", "direction"},
    "table_select": {"columns"},
    "table_rename": {"renames"},
}
_COMPARISONS = {"lt": operator.lt, "lte": operator.le, "gt": operator.gt, "gte": operator.ge}
_PATH_SEGMENT = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(0|[1-9][0-9]*)\]")


def _template_fields(template: str) -> tuple[str, ...]:
    """Only simple named fields; Python formatting traversal is not permitted."""
    names: dict[str, None] = {}
    for _, name, format_spec, conversion in Formatter().parse(template):
        if name is not None:
            if not name.isidentifier() or format_spec or conversion is not None:
                raise ValueError("template fields must be simple names such as {name}")
            names[name] = None
    return tuple(names)


def _path_tokens(path: str) -> tuple[str | int, ...]:
    """A single-value JSONPath subset: root, dotted names, indices, quoted keys."""
    if not path.startswith("$"):
        raise ValueError("JSON path must start with $")
    tokens: list[str | int] = []
    pos = 1
    while pos < len(path):
        match = _PATH_SEGMENT.match(path, pos)
        if match:
            name, index = match.groups()
            tokens.append(name if name is not None else int(index))
            pos = match.end()
        elif path.startswith('["', pos):
            key, end = json.JSONDecoder().raw_decode(path, pos + 1)
            if end >= len(path) or path[end] != "]":
                raise ValueError("JSON path quoted key must end with ]")
            tokens.append(key)
            pos = end + 1
        else:
            raise ValueError(f"unsupported JSON path segment at character {pos}")
    return tuple(tokens)


def _extract(value: JsonData, path: str) -> JsonData:
    for token in _path_tokens(path):
        if isinstance(token, str) and isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(token, int) and isinstance(value, list) and token < len(value):
            value = value[token]
        else:
            raise ValueError(f"JSON path {path!r} does not exist at {token!r}")
    return value


def _is_scalar(value: object) -> bool:
    return (
        value is None
        or type(value) in (str, bool, int)
        or (type(value) is float and math.isfinite(value))
    )


def _validate_json(value: JsonData) -> None:
    if _is_scalar(value):
        return
    if isinstance(value, list):
        for item in value:
            _validate_json(item)
    elif isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _validate_json(item)
    else:
        raise ValueError("JSON input must contain only JSON values with finite numbers and string keys")


def _string(config: dict, key: str) -> str:
    value = config[key]
    if not isinstance(value, str):
        raise ValueError(f"config {key!r} must be a string")
    return value


def _config(operation: str, config_json: str) -> dict:
    config = json.loads(config_json)
    if not isinstance(config, dict) or set(config) != _CONFIG_KEYS[operation]:
        raise ValueError(f"{operation} config must contain exactly {sorted(_CONFIG_KEYS[operation])}")
    if operation == "template":
        _template_fields(_string(config, "template"))
    elif operation == "json_path":
        _path_tokens(_string(config, "path"))
    elif operation in ("text_split", "text_join"):
        if not _string(config, "separator") and operation == "text_split":
            raise ValueError("split separator must not be empty")
    elif operation == "table_filter":
        _string(config, "column")
        op = _string(config, "operator")
        if op not in ("eq", "ne", "contains", *_COMPARISONS):
            raise ValueError(f"unsupported filter operator {op!r}")
        if not _is_scalar(config["value"]):
            raise ValueError("filter value must be a finite JSON scalar")
        if op == "contains" and not isinstance(config["value"], str):
            raise ValueError("contains requires a string filter value")
    elif operation == "table_sort":
        _string(config, "column")
        if _string(config, "direction") not in ("asc", "desc"):
            raise ValueError("sort direction must be asc or desc")
    elif operation == "table_select":
        columns = config["columns"]
        if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
            raise ValueError("selected columns must be an array of strings")
        if len(set(columns)) != len(columns):
            raise ValueError("selected columns must be unique")
    elif operation == "table_rename":
        renames = config["renames"]
        if not isinstance(renames, dict) or not all(isinstance(c, str) for c in renames.values()):
            raise ValueError("renames must be an object mapping column names to strings")
    return config


def _ports(inputs: tuple[tuple[str, PortKind], ...], output: tuple[str, PortKind]) -> PortSet:
    return PortSet(tuple(PortSpec(name, kind, True) for name, kind in inputs), (PortSpec(*output, True),))


def _cell_rank(value: JsonScalar) -> int:
    # A fixed total order across heterogeneous JSON scalar types.
    if value is None:
        return 0
    if isinstance(value, bool):
        return 1
    if isinstance(value, (int, float)):
        return 2
    return 3


def _matches(cell: JsonScalar, op: str, target: JsonScalar) -> bool:
    same_kind = _cell_rank(cell) == _cell_rank(target)
    if op == "eq":
        return same_kind and cell == target
    if op == "ne":
        return not (same_kind and cell == target)
    if op == "contains":
        return isinstance(cell, str) and target in cell
    # Ordered comparisons are meaningful only within numbers or strings.
    return same_kind and _cell_rank(cell) in (2, 3) and _COMPARISONS[op](cell, target)


def _table(payload: dict) -> TableValue:
    return TableValue(tuple(payload["columns"]), tuple(tuple(row) for row in payload["rows"]))


def _column_index(table: TableValue, name: str) -> int:
    try:
        return table.columns.index(name)
    except ValueError:
        raise ValueError(f"unknown table column {name!r}") from None


def _renamed_columns(table: TableValue, renames: dict) -> tuple[str, ...]:
    for name in renames:
        _column_index(table, name)
    columns = tuple(renames.get(name, name) for name in table.columns)
    if len(set(columns)) != len(columns):
        raise ValueError("renaming would produce duplicate table columns")
    return columns


def _validate_inputs(operation: str, config: dict, inputs: dict) -> None:
    if operation == "json_path":
        _extract(inputs["json"], config["path"])
    elif operation == "text_join":
        parts = inputs["parts"]
        if not isinstance(parts, list) or not all(isinstance(part, str) for part in parts):
            raise ValueError("join parts must be a JSON array of strings")
    elif operation.startswith("table_"):
        table = _table(inputs["table"])
        if operation in ("table_filter", "table_sort"):
            _column_index(table, config["column"])
        elif operation == "table_select":
            for name in config["columns"]:
                _column_index(table, name)
        elif operation == "table_rename":
            _renamed_columns(table, config["renames"])


def _transform_table(operation: str, config: dict, table: TableValue) -> TableValue:
    if operation == "table_select":
        columns = tuple(config["columns"])
        indices = tuple(_column_index(table, name) for name in columns)
        return TableValue(columns, tuple(tuple(row[i] for i in indices) for row in table.rows))
    if operation == "table_rename":
        return TableValue(_renamed_columns(table, config["renames"]), table.rows)

    index = _column_index(table, config["column"])
    if operation == "table_filter":
        rows = tuple(row for row in table.rows if _matches(row[index], config["operator"], config["value"]))
    else:
        # Stable, locale-independent ordering; nulls are last in either direction.
        present = [row for row in table.rows if row[index] is not None]
        missing = [row for row in table.rows if row[index] is None]
        present.sort(key=lambda row: (_cell_rank(row[index]), row[index]), reverse=config["direction"] == "desc")
        rows = tuple(present + missing)
    return TableValue(table.columns, rows)


def _shape(value: PortValue) -> dict[str, JsonData]:
    if isinstance(value, TextValue):
        return {"kind": "text", "characters": len(value.text)}
    if isinstance(value, TableValue):
        return {"kind": "table", "columns": list(value.columns), "row_count": len(value.rows)}
    if isinstance(value, JsonValue):
        data = value.value
        if isinstance(data, dict):
            return {"kind": "json", "type": "object", "keys": sorted(data)}
        if isinstance(data, list):
            return {"kind": "json", "type": "array", "length": len(data)}
        return {"kind": "json", "type": ("null", "boolean", "number", "string")[_cell_rank(data)]}
    raise ValueError("unsupported transform output kind")


@dataclass(frozen=True, slots=True)
class PreparedTransform:
    """By-value snapshot; no mutable input lists or dictionaries are retained."""

    adapter_id: str
    adapter_version: str
    config_json: str
    inputs_json: str


@dataclass(frozen=True, slots=True)
class TransformAdapter:
    operation: str
    adapter_version: ClassVar[str] = "1"
    side_effect_class: ClassVar[SideEffectClass] = "pure"

    def __post_init__(self) -> None:
        if self.operation not in _CONFIG_KEYS:
            raise ValueError(f"unknown transform {self.operation!r}")

    @property
    def adapter_id(self) -> str:
        return f"transform.{self.operation}"

    def ports(self, config_json: str) -> PortSet:
        config = _config(self.operation, config_json)
        if self.operation == "template":
            return _ports(tuple((name, "text") for name in _template_fields(config["template"])), ("text", "text"))
        if self.operation == "json_path":
            return _ports((("json", "json"),), ("value", "json"))
        if self.operation == "text_split":
            return _ports((("text", "text"),), ("parts", "json"))
        if self.operation == "text_join":
            return _ports((("parts", "json"),), ("text", "text"))
        return _ports((("table", "table"),), ("table", "table"))

    def prepare(self, spec: WorkflowStepSpec, inputs: Mapping[str, PortValue]) -> PreparedTransform:
        ports = self.ports(spec.config_json)
        expected = AdapterDescriptor(self.adapter_id, self.adapter_version, ports, self.side_effect_class)
        if spec.adapter != expected:
            raise ValueError("step adapter descriptor does not match the pinned transform")
        if set(inputs) != {port.name for port in ports.inputs}:
            raise ValueError(f"expected input ports {sorted(port.name for port in ports.inputs)}")
        payload: dict = {}
        for port in ports.inputs:
            value = inputs[port.name]
            if port.kind == "text" and isinstance(value, TextValue) and isinstance(value.text, str):
                payload[port.name] = value.text
            elif port.kind == "json" and isinstance(value, JsonValue):
                _validate_json(value.value)
                payload[port.name] = value.value
            elif port.kind == "table" and isinstance(value, TableValue):
                _check_table(value)
                payload[port.name] = {"columns": list(value.columns), "rows": [list(row) for row in value.rows]}
            else:
                raise ValueError(f"input port {port.name!r} requires {port.kind}")
        config = _config(self.operation, spec.config_json)
        # Serializing snapshots detaches nested JSON from the caller.
        inputs_json = freeze_config(payload)
        _validate_inputs(self.operation, config, json.loads(inputs_json))
        return PreparedTransform(self.adapter_id, self.adapter_version, freeze_config(config), inputs_json)

    def _check_prepared(self, prepared: PreparedTransform) -> None:
        if (prepared.adapter_id, prepared.adapter_version) != (self.adapter_id, self.adapter_version):
            raise ValueError("prepared state belongs to a different transform adapter")

    def fingerprint(self, prepared: PreparedTransform) -> str:
        self._check_prepared(prepared)
        payload = json.dumps(
            [prepared.adapter_id, prepared.adapter_version, prepared.config_json, prepared.inputs_json],
            ensure_ascii=True, separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("ascii")).hexdigest()

    def _evaluate(self, prepared: PreparedTransform) -> dict[str, PortValue]:
        self._check_prepared(prepared)
        config = json.loads(prepared.config_json)
        inputs = json.loads(prepared.inputs_json)
        if self.operation == "template":
            # Reconstruct from validated literal segments, never call format/eval
            # on a user-supplied expression.
            text = "".join(literal + (inputs[name] if name is not None else "")
                           for literal, name, _, _ in Formatter().parse(config["template"]))
            return {"text": TextValue(text)}
        if self.operation == "json_path":
            return {"value": JsonValue(_extract(inputs["json"], config["path"]))}
        if self.operation == "text_split":
            return {"parts": JsonValue(inputs["text"].split(config["separator"]))}
        if self.operation == "text_join":
            return {"text": TextValue(config["separator"].join(inputs["parts"]))}
        return {"table": _transform_table(self.operation, config, _table(inputs["table"]))}

    def preview(self, prepared: PreparedTransform) -> Mapping[str, JsonData]:
        """Compute only local pure values; return shape metadata, not content."""
        return {name: _shape(value) for name, value in self._evaluate(prepared).items()}

    async def run(self, prepared: PreparedTransform, context: StepContext) -> Mapping[str, PortValue]:
        return self._evaluate(prepared)

    async def cancel(self, context: StepContext) -> None:
        """No external work or retained attempt state to cancel."""


TRANSFORM_ADAPTERS = tuple(TransformAdapter(operation) for operation in _CONFIG_KEYS)
