"""Shared operation schema, validation and reproducible scientific results."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction
from typing import Any

from .units import parse_unit, dimension_name, convert_decimal_measurement, interval_temperature_unit

VERSION = "1.1.0"
MAX_POINTS = 1024
MAX_COLUMNS = 32
MAX_CELLS = 4096
MAX_TEXT = 65536
MAX_RESULT_BYTES = 512 * 1024
# Legacy operation schemas and result receipts are deliberately unchanged.
# Typed imported tables have independent, larger storage limits.
TABLE_MAX_ROWS = 100_000
TABLE_MAX_COLUMNS = 256
TABLE_MAX_CELLS = 1_000_000
TABLE_MAX_SOURCE_BYTES = 64 * 1024 * 1024
INPUT_LIMITS = {
    "points": MAX_POINTS, "columns": MAX_COLUMNS, "cells": MAX_CELLS, "text": MAX_TEXT,
    "tableRows": TABLE_MAX_ROWS, "tableColumns": TABLE_MAX_COLUMNS,
    "tableCells": TABLE_MAX_CELLS, "tableSourceBytes": TABLE_MAX_SOURCE_BYTES,
}
_DIGEST = re.compile(r"[0-9a-f]{64}")
_NODE_KEY = re.compile(r"[0-9a-f]{32}")
_OPERATION_ID = re.compile(r"[a-z][a-z0-9_]*")


_NUMERIC = frozenset(("number", "integer", "vector", "matrix"))
_PARIS_UNIT = "m/cycle/(MPa√m)^m"
_HARDNESS_UNIT = "HV, HBW, HBS, HRC or HRB (as selected)"
_HARDNESS_SCALES = frozenset(("HV", "HBW", "HBS", "HRC", "HRB"))
_NUMBER_UNIT = re.compile(r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(?:\s*(\S.*?))?\s*$")
_MATRIX_COLUMNS = re.compile(r"^col 1:\s*([^;]+);\s*col 2:\s*(.+)$")
_DYNAMIC = {
    "selected time unit": "time_unit",
    "selected abundance unit": "abundance_unit",
    "selected rate unit": "rate_unit",
    "selected response unit": "response_unit",
    "see rate_constant_unit": "rate_constant_unit",
    "observation_unit": "observation_unit",
}


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str
    unit: str = ""
    description: str = ""
    default_unit: str = ""
    dimension: str = ""
    column_units: tuple[str, ...] = ()
    column_dimensions: tuple[str, ...] = ()
    choices: tuple[str, ...] = ()

    def __post_init__(self):
        if self.kind == "choice":
            if (not self.choices or any(
                not isinstance(value, str) or not value or value.strip() != value
                for value in self.choices
            ) or len(set(self.choices)) != len(self.choices)):
                raise ValueError("Choice fields require distinct, nonempty string values.")
        elif self.choices:
            raise ValueError("Only choice fields can declare allowed choices.")
        if self.kind == "matrix" and (columns := _MATRIX_COLUMNS.fullmatch(self.unit)):
            units = tuple(value.strip() for value in columns.groups())
            parsed_units = [parse_unit(value) for value in units]
            dimensions = tuple(dimension_name(value.dimensions, value.angle_power, value.kind) for value in parsed_units)
            object.__setattr__(self, "column_units", units)
            object.__setattr__(self, "column_dimensions", dimensions)
            object.__setattr__(self, "default_unit", "1")
            object.__setattr__(self, "dimension", "mixed: " + ", ".join(dimensions))
            return
        if self.kind == "number" and self.unit == _PARIS_UNIT:
            # C has a different physical dimension for each fitted Paris exponent.
            object.__setattr__(self, "default_unit", _PARIS_UNIT)
            object.__setattr__(self, "dimension", "exponent-dependent: " + _PARIS_UNIT)
            return
        if self.kind == "number" and self.unit == _HARDNESS_UNIT:
            # Empirical HRC/HV/etc scales cannot be converted by SI factors.
            selected = self.default_unit or "HRC"
            if selected not in _HARDNESS_SCALES:
                raise ValueError("Unknown empirical hardness scale.")
            object.__setattr__(self, "default_unit", selected)
            object.__setattr__(self, "dimension", "empirical hardness scale (selected)")
            return
        if self.kind in _NUMERIC:
            unit = self.default_unit or self.unit or "1"
            try:
                parsed = parse_unit(unit)
            except ValueError:
                if self.default_unit:
                    raise  # Explicit declarations must be valid SI expressions.
                unit, parsed = "1", parse_unit("1")
            object.__setattr__(self, "default_unit", unit)
            object.__setattr__(self, "dimension", dimension_name(parsed.dimensions, parsed.angle_power, parsed.kind))


def _effective_unit(field: Field, raw: dict) -> str:
    name = field.unit
    unit: object
    if name == _HARDNESS_UNIT:
        selected = raw.get("from_scale")
        return selected if isinstance(selected, str) and selected in _HARDNESS_SCALES else field.default_unit
    if name in ("1 / selected time unit", "1 / (predator × time unit)", "1 / (prey × time unit)"):
        time = raw.get("time_unit")
        unit = f"1/({time})" if isinstance(time, str) else ""
    elif name == "selected concentration unit":
        unit = raw.get("substrate_unit" if field.key == "substrate" else "concentration_unit")
    else:
        key = "from_unit" if field.key == "value" and "from_unit" in raw else _DYNAMIC.get(name)
        unit = raw.get(key) if key else None
    if not isinstance(unit, str) or not unit.strip():
        return field.default_unit
    try:
        parse_unit(unit)
    except ValueError:
        return field.default_unit  # Bare numbers with old free-form labels remain valid.
    return unit.strip()


def _unit_field(field: Field, raw: dict) -> str | None:
    """The sibling input whose text sets this field's unit, mirroring
    _effective_unit, so the editor can label the field with the unit the
    user actually chose. Derived units ("1 / selected time unit") stay on
    their declared default label."""
    if field.unit == _HARDNESS_UNIT:
        return "from_scale"
    if field.unit == "selected concentration unit":
        return "substrate_unit" if field.key == "substrate" else "concentration_unit"
    if field.key == "value" and "from_unit" in raw:
        return "from_unit"
    return _DYNAMIC.get(field.unit)


@dataclass(frozen=True)
class Citation:
    label: str
    url: str

    def __post_init__(self):
        if (not isinstance(self.label, str) or not self.label.strip()
                or not isinstance(self.url, str)
                or not re.fullmatch(r"https://[A-Za-z0-9.-]+(?:/[^\s]*)?", self.url)):
            raise ValueError("Citations require a label and an HTTPS URL.")


@dataclass(frozen=True)
class Operation:
    id: str
    title: str
    description: str
    fields: tuple[Field, ...]
    example: dict
    calculate: Callable[[dict], dict]
    # Optional pack-level wall-clock override. None uses deployment configuration.
    timeout_s: float | None = None
    methods: str = ""
    citations: tuple[Citation, ...] = ()

    def __post_init__(self):
        for field in self.fields:
            if field.kind == "choice" and self.example.get(field.key) not in field.choices:
                raise ValueError(f"Example for {field.key} must be an allowed choice.")
        # Expose a declared default unit and dimension for every numeric field.
        object.__setattr__(self, "fields", tuple(
            replace(f, default_unit=u) if (u := _effective_unit(f, self.example)) != f.default_unit else f
            for f in self.fields
        ))

    def describe(self) -> dict:
        fields = []
        for item in self.fields:
            described = asdict(item)
            if not item.choices:
                described.pop("choices")
            if (unit_field := _unit_field(item, self.example)) is not None:
                described["unit_field"] = unit_field
            fields.append(described)
        description = {
            "id": self.id, "title": self.title, "description": self.description,
            "fields": fields, "example": self.example,
        }
        if self.methods:
            description["methods"] = self.methods
        if self.citations:
            description["citations"] = [asdict(citation) for citation in self.citations]
        return description


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def finite(value, label: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{label} must be a finite number.")
    try:
        number = float(value)
    except OverflowError:
        raise ValueError(f"{label} exceeds the numeric range.") from None
    if type(value) is int and number != value:
        raise ValueError(f"{label} cannot be represented exactly as a measurement.")
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number.")
    return number


def _measurement(field: Field, value, target: str) -> tuple[float, str]:
    parsed_target = parse_unit(target)
    expected = dimension_name(parsed_target.dimensions, parsed_target.angle_power, parsed_target.kind)
    if type(value) in (int, float):
        number = finite(value, field.label)
        if (parsed_target.kind == "absolute"
                and Fraction(str(number)) * parsed_target.scale + parsed_target.offset < 0):
            raise ValueError(f"{field.label}: Absolute temperature must be at least 0 K.")
        return number, target
    if not isinstance(value, str) or len(value) > 256:
        raise ValueError(f"{field.label} requires a finite number; expected {expected} ({target}).")
    match = _NUMBER_UNIT.fullmatch(value)
    if not match:
        raise ValueError(f"{field.label} needs a decimal and optional unit; expected {expected} ({target}).")
    decimal, suffix = match.groups()
    supplied = suffix or target
    # A kelvin symbol on an interval field denotes a difference, including
    # SI-prefixed kelvins. degC/degF points require an explicit delta symbol.
    converted_from = interval_temperature_unit(supplied) if target.startswith("delta_") else supplied
    try:
        number = convert_decimal_measurement(decimal, converted_from, target)
    except ValueError as exc:
        raise ValueError(f"{field.label}: {exc} Expected {expected} (default {target}).") from None
    if not suffix and Decimal(decimal) == Decimal(decimal).to_integral_value():
        if Fraction(number) != Fraction(Decimal(decimal)):
            raise ValueError(f"{field.label} contains an integer count that cannot be represented exactly.")
    return number, supplied


def _field_with_units(field: Field, value, unit: str):
    if field.kind == "number" and field.unit in (_PARIS_UNIT, _HARDNESS_UNIT):
        # Neither exponent-dependent Paris C nor empirical hardness readings
        # are convertible through an SI dimensionless stand-in.
        selected = unit if field.unit == _HARDNESS_UNIT else _PARIS_UNIT
        if type(value) in (int, float):
            return finite(value, field.label), selected
        if isinstance(value, str) and (match := _NUMBER_UNIT.fullmatch(value)):
            decimal, suffix = match.groups()
            if suffix is None or (field.unit == _HARDNESS_UNIT and suffix == selected):
                return _measurement(field, decimal, "1")[0], suffix or selected
        raise ValueError(f"{field.label}: expected a numeric value in {selected}; "
                         "different physical or empirical units cannot be converted.")
    if field.kind == "number":
        return _measurement(field, value, unit)
    if field.kind == "integer":
        if type(value) is int and abs(value) <= 2**53 - 1:
            return value, unit
        if isinstance(value, str):
            number, used = _measurement(field, value, unit)
            match = _NUMBER_UNIT.fullmatch(value)
            if match is None:
                raise ValueError(f"{field.label} needs a decimal and optional unit.")
            original = Decimal(match.group(1))
            source, target = parse_unit(used), parse_unit(unit)
            exact = ((Fraction(original) * source.scale + source.offset - target.offset)
                     / target.scale)
            if exact.denominator == 1 and abs(exact.numerator) <= 2**53 - 1 and number == exact.numerator:
                return int(exact.numerator), used
        raise ValueError(f"{field.label} requires an exact integer in the supported range; expected {field.dimension}.")
    if field.kind == "choice":
        if not isinstance(value, str) or value not in field.choices:
            raise ValueError(f"{field.label} must be one of: {', '.join(field.choices)}.")
        return value, None
    if field.kind == "text":
        if not isinstance(value, str) or not 0 < len(value) <= MAX_TEXT:
            raise ValueError(f"{field.label} requires 1–{MAX_TEXT} characters.")
        return value, None
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_POINTS:
        raise ValueError(f"{field.label} requires 1–{MAX_POINTS} entries.")
    if field.kind == "vector":
        converted = [_measurement(field, v, unit) for v in value]
        return [n for n, _ in converted], [u for _, u in converted]
    if field.kind == "matrix":
        if not isinstance(value[0], list):
            raise ValueError(f"{field.label} requires a rectangular numeric matrix.")
        width = len(value[0])
        if not 1 <= width <= MAX_COLUMNS or len(value) * width > MAX_CELLS:
            raise ValueError(f"{field.label} exceeds the supported matrix dimensions.")
        if any(not isinstance(row, list) or len(row) != width for row in value):
            raise ValueError(f"{field.label} requires equal-length rows.")
        if field.column_units and len(field.column_units) != width:
            raise ValueError(f"{field.label} requires {len(field.column_units)} columns with declared units.")
        target_units = field.column_units or (unit,) * width
        converted_rows = [[_measurement(field, v, target_units[i]) for i, v in enumerate(row)] for row in value]
        return ([[n for n, _ in row] for row in converted_rows],
                [[u for _, u in row] for row in converted_rows])
    raise ValueError(f"Unsupported field kind {field.kind}.")


def _field_value(field: Field, value):
    return _field_with_units(field, value, field.default_unit)[0]


def validated_inputs_with_units(operation: Operation, raw: dict) -> tuple[dict, dict]:
    if not isinstance(raw, dict) or set(raw) != {f.key for f in operation.fields}:
        raise ValueError("Supply exactly the inputs listed for this operation.")
    normalized, used = {}, {}
    for field in operation.fields:
        unit = _effective_unit(field, raw) if field.kind in _NUMERIC else ""
        normalized[field.key], symbols = _field_with_units(field, raw[field.key], unit)
        if field.kind in _NUMERIC:
            used[field.key] = symbols
    return normalized, used


def validated_inputs(operation: Operation, raw: dict) -> dict:
    return validated_inputs_with_units(operation, raw)[0]


def check_artifact(data: dict) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("summary"), str) or not isinstance(data.get("values"), dict):
        raise ValueError("Result requires a summary and scalar values.")
    for value in data["values"].values():
        if not isinstance(value, str):
            finite(value, "Result")
    if "table" in data:
        table = data["table"]
        if not isinstance(table, dict) or not isinstance(table.get("columns"), list) or not isinstance(table.get("rows"), list):
            raise ValueError("Table columns and rows must be arrays.")
        columns, rows = table["columns"], table["rows"]
        if not columns or len(columns) > MAX_COLUMNS or len(rows) > MAX_POINTS or len(rows) * len(columns) > MAX_CELLS:
            raise ValueError("Result table exceeds the supported dimensions.")
        if not all(isinstance(c, str) for c in columns):
            raise ValueError("Table headers must be text.")
        for row in rows:
            if not isinstance(row, list) or len(row) != len(columns):
                raise ValueError("Result table contains a row with the wrong number of cells.")
            for cell in row:
                if not isinstance(cell, str):
                    finite(cell, "Table cell")
    if "uncertainty" in data:
        from .propagation import check_uncertainty
        check_uncertainty(data["uncertainty"], data["values"])
    if "plot" in data:
        plot = data["plot"]
        if not isinstance(plot, dict) or not all(isinstance(plot.get(k), list) for k in ("x", "y")):
            raise ValueError("Plot coordinates must be arrays.")
        if not 1 <= len(plot["x"]) == len(plot["y"]) <= MAX_POINTS:
            raise ValueError("A plot requires paired coordinates within the supported range.")
        if not all(isinstance(plot.get(k), str) for k in ("title", "xLabel", "yLabel")):
            raise ValueError("A plot requires a title and labeled axes.")
        for axis in ("x", "y"):
            for point in plot[axis]:
                finite(point, "Plot coordinate")


def run_operation(pack: str, operation: Operation, raw: dict, sources: dict | None = None,
                  *, uncertainty_inputs: dict | None = None, uncertainty_method: str = "auto") -> dict:
    values, units = validated_inputs_with_units(operation, raw)
    return calculate_result(pack, operation, values, sources, input_units=units,
                            uncertainty_inputs=uncertainty_inputs, uncertainty_method=uncertainty_method)


def calculate_result(pack: str, operation: Operation, inputs: dict, sources: dict | None,
                     *, input_units: dict | None = None, uncertainty_inputs: dict | None = None,
                     uncertainty_method: str = "auto") -> dict:
    """Calculate with normalized inputs after any requested source bindings are verified."""
    try:
        output = operation.calculate(inputs)
        check_artifact(output)
        if uncertainty_inputs:
            # Keep the numerical output and historic hash format for exact-input runs.
            # Uncertain connected runs attach a receipt-protected GUM/MC sidecar.
            from .propagation import propagate
            output = {**output, "uncertainty": propagate(
                operation, inputs, output, uncertainty_inputs, uncertainty_method)}
            check_artifact(output)
        input_hash = fingerprint(inputs)
        result_hash = fingerprint([pack, VERSION, operation.id, input_hash, output])
    except ArithmeticError:
        raise ValueError("These inputs exceed the numerical range of this operation.") from None
    result = {
        **output, "operationId": operation.id, "inputs": inputs,
        "provenance": {
            "packId": pack, "packVersion": VERSION, "operationId": operation.id,
            "inputHash": input_hash, "resultHash": result_hash,
            "createdAt": datetime.now(UTC).isoformat(), "sources": sources or {},
            **({"inputUnits": input_units} if input_units is not None else {}),
        },
    }
    receipt = result["provenance"]
    receipt["receiptHash"] = fingerprint(receipt)
    if len(canonical_bytes(result)) > MAX_RESULT_BYTES:
        raise ValueError("The result exceeds 512 KiB. Use fewer samples.")
    return result


def _source_valid(ref: dict) -> bool:
    if not isinstance(ref, dict) or not isinstance(ref.get("nodeId"), str) or not ref["nodeId"]:
        return False
    if not _DIGEST.fullmatch(ref["resultHash"]) or not _OPERATION_ID.fullmatch(ref["packId"]) or not _OPERATION_ID.fullmatch(ref["operationId"]):
        return False
    if "receiptHash" in ref and not _DIGEST.fullmatch(ref["receiptHash"]):
        return False
    if ref.get("sourceKey") is not None and not _NODE_KEY.fullmatch(ref["sourceKey"]):
        return False
    match ref.get("selector"):
        case ["plot", "x" | "y"] | ["table"] | ["values", str()]:
            return True
        case ["column", int(index)]:
            return type(index) is int and 0 <= index < MAX_COLUMNS
        case ["table_column", int(index), ("reject" | "drop"), int(start), int(end)]:
            return (type(index) is int and 0 <= index < TABLE_MAX_COLUMNS
                    and type(start) is int and type(end) is int
                    and 0 <= start < end <= TABLE_MAX_ROWS and end - start <= MAX_POINTS)
    return False


def readable_result(data: dict, pack: str) -> bool:
    """Check stored structure, source metadata and checksums without executing the method."""
    try:
        if type(data["version"]) is not int or data["version"] != 1:
            return False
        if "nodeKey" in data and not _NODE_KEY.fullmatch(data["nodeKey"]):
            return False
        result = data["latest"]
        if result is None:
            return True
        if (not isinstance(result, dict) or not isinstance(result["inputs"], dict)
                or len(canonical_bytes(result)) > MAX_RESULT_BYTES):
            return False
        receipt = result["provenance"]
        created_at = datetime.fromisoformat(receipt["createdAt"])
        if (created_at.utcoffset() != UTC.utcoffset(created_at)
                or receipt["packId"] != pack or receipt["operationId"] != result["operationId"]
                or not _OPERATION_ID.fullmatch(result["operationId"])):
            return False
        sources = receipt["sources"]
        if (not isinstance(sources, dict) or sources.keys() - result["inputs"].keys()
                or not all(_source_valid(ref) for ref in sources.values())):
            return False
        output = {k: result[k] for k in ("summary", "values", "table", "plot", "uncertainty") if k in result}
        check_artifact(output)
        inputs_hash = fingerprint(result["inputs"])
        version = receipt["packVersion"]
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
            return False
        if (receipt["inputHash"] != inputs_hash or
                receipt["resultHash"] != fingerprint([pack, version, result["operationId"], inputs_hash, output])):
            return False
        # Original 1.0.0 receipts only protected measurements and output.
        if "receiptHash" not in receipt:
            return version == "1.0.0"
        return receipt["receiptHash"] == fingerprint({k: v for k, v in receipt.items() if k != "receiptHash"})
    except (KeyError, TypeError, IndexError, ValueError, OverflowError, AttributeError, RecursionError):
        return False
