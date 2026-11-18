"""Shared operation schema, validation and reproducible scientific results."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

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


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str
    unit: str = ""
    description: str = ""


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

    def describe(self) -> dict:
        return {
            "id": self.id, "title": self.title, "description": self.description,
            "fields": [asdict(item) for item in self.fields], "example": self.example,
        }


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


def _field_value(field: Field, value):
    label = field.label
    if field.kind == "number":
        return finite(value, label)
    if field.kind == "integer":
        if type(value) is not int or abs(value) > 2**53 - 1:
            raise ValueError(f"{label} requires an exact integer in the supported range.")
        return value
    if field.kind == "text":
        if not isinstance(value, str) or not 0 < len(value) <= MAX_TEXT:
            raise ValueError(f"{label} requires 1–{MAX_TEXT} characters.")
        return value
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_POINTS:
        raise ValueError(f"{label} requires 1–{MAX_POINTS} entries.")
    if field.kind == "vector":
        return [finite(v, label) for v in value]
    if field.kind == "matrix":
        if not isinstance(value[0], list):
            raise ValueError(f"{label} requires a rectangular numeric matrix.")
        width = len(value[0])
        if not 1 <= width <= MAX_COLUMNS or len(value) * width > MAX_CELLS:
            raise ValueError(f"{label} exceeds the supported matrix dimensions.")
        if any(not isinstance(row, list) or len(row) != width for row in value):
            raise ValueError(f"{label} requires equal-length rows.")
        return [[finite(v, label) for v in row] for row in value]
    raise ValueError(f"Unsupported field kind {field.kind}.")


def validated_inputs(operation: Operation, raw: dict) -> dict:
    if not isinstance(raw, dict) or set(raw) != {f.key for f in operation.fields}:
        raise ValueError("Supply exactly the inputs listed for this operation.")
    return {f.key: _field_value(f, raw[f.key]) for f in operation.fields}


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


def run_operation(pack: str, operation: Operation, raw: dict, sources: dict | None = None) -> dict:
    return calculate_result(pack, operation, validated_inputs(operation, raw), sources)


def calculate_result(pack: str, operation: Operation, inputs: dict, sources: dict | None) -> dict:
    """Calculate with normalized inputs after any requested source bindings are verified."""
    try:
        output = operation.calculate(inputs)
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
        output = {k: result[k] for k in ("summary", "values", "table", "plot") if k in result}
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
