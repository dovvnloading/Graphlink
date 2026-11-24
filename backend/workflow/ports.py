"""The complete workflow port coercion policy (no I/O or implicit chaining)."""

from __future__ import annotations

import csv
import io
import json
import math
from typing import get_args

from .contracts import FileValue, ImageValue, JsonScalar, JsonValue, PortKind, PortSpec, PortValue, TableValue, TextValue

PORT_KINDS: tuple[PortKind, ...] = get_args(PortKind.__value__)
# Identity is accepted separately. This is the entire cross-kind allowlist.
ALLOWED_COERCIONS: tuple[tuple[PortKind, PortKind], ...] = (
    ("json", "text"),
    ("table", "text"),
    ("table", "json"),
)

_VALUE_TYPES = {
    "text": TextValue,
    "json": JsonValue,
    "table": TableValue,
    "file": FileValue,
    "image": ImageValue,
}


class PortCoercionError(ValueError):
    """A named destination port cannot accept the supplied value."""


def _check_table(table: TableValue) -> None:
    if any(not isinstance(column, str) for column in table.columns):
        raise ValueError("table column names must be strings")
    if len(set(table.columns)) != len(table.columns):
        raise ValueError("table column names must be unique")
    for row in table.rows:
        if len(row) != len(table.columns):
            raise ValueError("table row width does not match its columns")
        for cell in row:
            if cell is not None and not isinstance(cell, (str, bool, int, float)):
                raise ValueError("table cells must be JSON scalars")
            if isinstance(cell, float) and not math.isfinite(cell):
                raise ValueError("table cells must be finite")


def _csv_cell(cell: JsonScalar) -> str:
    if cell is None:
        return ""
    if isinstance(cell, bool):
        return "true" if cell else "false"
    return str(cell)


def _table_csv(table: TableValue) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(table.columns)
    writer.writerows([_csv_cell(cell) for cell in row] for row in table.rows)
    return output.getvalue()


def coerce_port(value: PortValue, port: PortSpec) -> PortValue:
    """Pass identical kinds through; otherwise apply exactly one allowed rule.

    JSON text is sorted, two-space-indented, Unicode-preserving JSON. CSV
    includes the header and CRLF row endings (null cells are empty); JSON
    table output is an array of objects keyed by column names. Never parse
    text as JSON, read an asset, guess a conversion or chain multiple rules.
    This checks the connection, not all semantic constraints on a same-kind
    payload; adapters remain responsible for validating their inputs.
    """
    prefix = f"Port {port.name!r}"
    source = getattr(value, "kind", None)
    if not isinstance(source, str) or source not in _VALUE_TYPES:
        raise PortCoercionError(f"{prefix}: unknown source port kind")
    if not isinstance(value, _VALUE_TYPES[source]):
        raise PortCoercionError(f"{prefix}: value does not match declared source kind {source!r}")
    if port.kind not in PORT_KINDS:
        raise PortCoercionError(f"{prefix}: unknown destination port kind {port.kind!r}")
    if source == port.kind:
        return value
    if (source, port.kind) not in ALLOWED_COERCIONS:
        raise PortCoercionError(f"{prefix}: cannot coerce {source} to {port.kind}; no declared coercion")
    try:
        if isinstance(value, JsonValue):
            return TextValue(json.dumps(value.value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False))
        if isinstance(value, TableValue):
            _check_table(value)
            if port.kind == "text":
                return TextValue(_table_csv(value))
            return JsonValue([dict(zip(value.columns, row, strict=True)) for row in value.rows])
    except (TypeError, ValueError, OverflowError, csv.Error) as exc:
        raise PortCoercionError(f"{prefix}: cannot coerce {source} to {port.kind}: {exc}") from exc
    raise PortCoercionError(f"{prefix}: no implementation for declared coercion {source} to {port.kind}")
