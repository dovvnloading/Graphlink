"""Typed, bounded scientific tables kept outside the graph as content-addressed assets.

The temporary store bridges the interval before autosave. On save the plugin's
serialize_saved hook copies the verified blob into the session's *actual* asset
store (not a guessed default db path). On load the matching restore context
rehydrates this cache. The graph and scene wire carry only bounded metadata.
"""
from __future__ import annotations

import atexit
import base64
import os
import threading
import binascii
import csv
import hashlib
import io
import json
import math
import re
import shutil
import tempfile
import time
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from backend.asset_store import AssetStore
from backend.domain.node_states import NodeState
from graphlink_plugins.scientific.core import (
    MAX_POINTS, TABLE_MAX_ROWS, TABLE_MAX_COLUMNS, TABLE_MAX_CELLS,
    TABLE_MAX_SOURCE_BYTES, canonical_bytes, fingerprint,
)
from graphlink_plugins.scientific.units import split_column_header
# Canonical JSON expands UTF-8, delimiters and quotes; leave room for it.
TABLE_MAX_ASSET_BYTES = 128 * 1024 * 1024
TABLE_MAX_CELL_CHARS = 65_536
MISSING_DEFAULT = ("", "NA", "N/A", "null")
KINDS = frozenset(("numeric", "text", "categorical", "datetime", "boolean"))
_REF = re.compile(r"[0-9a-f]{64}")
_STAGING_DIR: tempfile.TemporaryDirectory[str] | None = None
_STAGING: AssetStore | None = None
_WORKER_STORE: ContextVar[AssetStore | None] = ContextVar("scientific_table_worker_store", default=None)
_CSV_DIR: tempfile.TemporaryDirectory[str] | None = None
_CSV_CACHE: OrderedDict[str, Path] = OrderedDict()
_CSV_LOCK = threading.Lock()
_TEMP_DIR_LOCK = threading.Lock()
_STALE_DIRECTORIES_SWEPT = False
CSV_CHUNK_BYTES = 192 * 1024
CSV_MAX_BYTES = 256 * 1024 * 1024


def _sweep_stale_directories() -> None:
    """Best-effort removal of scientific scratch directories left by dead processes."""
    try:
        cutoff = time.time() - 24 * 60 * 60
        # Be safe even if this helper is invoked again after our first use.
        own_directories = {
            os.path.normcase(os.path.abspath(directory.name))
            for directory in (_STAGING_DIR, _CSV_DIR)
            if directory is not None
        }
        with os.scandir(tempfile.gettempdir()) as entries:
            for entry in entries:
                try:
                    if (not entry.name.startswith(("graphlink-scientific-", "graphlink-scientific-csv-"))
                            or not entry.is_dir(follow_symlinks=False)
                            or os.path.normcase(os.path.abspath(entry.path)) in own_directories
                            or entry.stat(follow_symlinks=False).st_mtime >= cutoff):
                        continue
                    shutil.rmtree(entry.path, ignore_errors=True)
                except Exception:
                    continue
    except Exception:
        pass


def _new_temporary_directory(prefix: str) -> tempfile.TemporaryDirectory[str]:
    """Called only with _TEMP_DIR_LOCK held, on the first real store/export use."""
    global _STALE_DIRECTORIES_SWEPT
    if not _STALE_DIRECTORIES_SWEPT:
        _sweep_stale_directories()
        _STALE_DIRECTORIES_SWEPT = True
    directory = tempfile.TemporaryDirectory(prefix=prefix, ignore_cleanup_errors=True)
    atexit.register(directory.cleanup)
    return directory


def _staging_store() -> AssetStore:
    global _STAGING_DIR, _STAGING
    with _TEMP_DIR_LOCK:
        if _STAGING is None:
            _STAGING_DIR = _new_temporary_directory("graphlink-scientific-")
            _STAGING = AssetStore(Path(_STAGING_DIR.name))
        return _STAGING


def _csv_directory() -> Path:
    global _CSV_DIR
    with _TEMP_DIR_LOCK:
        if _CSV_DIR is None:
            _CSV_DIR = _new_temporary_directory("graphlink-scientific-csv-")
        return Path(_CSV_DIR.name)


def table_store_root() -> str:
    """The parent-owned staging directory, passed only to a trusted worker."""
    return str(_staging_store().root)


@contextmanager
def use_table_store(root: str):
    """Use the parent's verified staging store without allocating a worker cache."""
    token = _WORKER_STORE.set(AssetStore(Path(root)))
    try:
        yield
    finally:
        _WORKER_STORE.reset(token)


@dataclass(frozen=True)
class TableState(NodeState):
    """Immutable, small metadata snapshot; never contains the full table."""
    json_text: str

    @classmethod
    def from_data(cls, data: dict) -> "TableState":
        return cls(canonical_bytes(data).decode("ascii"))

    @property
    def data(self) -> dict:
        return json.loads(self.json_text)

    def __deepcopy__(self, memo):
        return self


def _numeric(token: str) -> float:
    try:
        value = float(token)
        exact = Decimal(token)
        if not math.isfinite(value) or (value == 0 and exact != 0):
            raise ValueError
        if exact == exact.to_integral_value() and Decimal.from_float(value) != exact:
            raise ValueError
        return value
    except (ValueError, OverflowError, InvalidOperation):
        raise ValueError(f"Invalid finite numeric measurement {token[:40]!r}.") from None


def _datetime(token: str) -> str:
    try:
        if "T" not in token and " " not in token:
            return date.fromisoformat(token).isoformat()
        return datetime.fromisoformat(token.replace("Z", "+00:00")).isoformat()
    except ValueError:
        raise ValueError(f"Expected an ISO 8601 date/time, got {token[:40]!r}.") from None


def _boolean(token: str) -> bool:
    match token.strip().casefold():
        case "true" | "yes":
            return True
        case "false" | "no":
            return False
    raise ValueError(f"Expected true/false or yes/no, got {token[:40]!r}.")


def _kind(tokens: list[str]) -> str:
    if not tokens:
        return "text"
    if all(v.strip().casefold() in ("true", "false", "yes", "no") for v in tokens):
        return "boolean"
    try:
        for v in tokens:
            _numeric(v.strip())
        return "numeric"
    except ValueError:
        pass
    try:
        for v in tokens:
            _datetime(v.strip())
        return "datetime"
    except ValueError:
        pass
    distinct = len(set(tokens))
    return "categorical" if distinct < len(tokens) and distinct <= min(64, max(2, len(tokens) // 5)) else "text"


def _options(fmt: str, missing: list[str] | None, hints: dict[str, str] | None) -> dict:
    if fmt not in ("csv", "tsv"):
        raise ValueError("Choose CSV or TSV format.")
    if missing is None:
        missing = list(MISSING_DEFAULT)
    if not isinstance(missing, list) or len(missing) > 32 or any(
        not isinstance(s, str) or len(s) > 80 for s in missing
    ):
        raise ValueError("Missing-value tokens must be a list of up to 32 short strings.")
    if not isinstance(hints, (dict, type(None))):
        raise ValueError("Column types must be an object keyed by header.")
    hints = hints or {}
    if len(hints) > TABLE_MAX_COLUMNS or any(
        not isinstance(k, str) or not isinstance(v, str) or v not in KINDS for k, v in hints.items()
    ):
        raise ValueError("Column type overrides must use numeric, text, categorical, datetime or boolean.")
    if "" not in missing:
        if len(missing) == 32:
            raise ValueError("Missing-value tokens must be a list of up to 32 short strings, including blank.")
        missing = ["", *missing]
    return {
        "format": fmt, "delimiter": "," if fmt == "csv" else "\t",
        "encoding": "utf-8-sig", "header": True, "headerUnits": True,
        "missingValues": missing, "columnTypes": hints,
    }


def parse_table(source: bytes, *, fmt: str = "csv", missing: list[str] | None = None,
                hints: dict[str, str] | None = None, filename: str = "",
                preview_only: bool = False, expected_preview: str | None = None) -> dict:
    """Parse untrusted raw file bytes (or the UTF-8 bytes of pasted text).

    Never coerce a missing value to zero or a malformed typed value to null.
    The SHA-256 records *original bytes*, before BOM removal/CSV unquoting.
    """
    options = _options(fmt, missing, hints)
    if not isinstance(source, bytes) or not 0 < len(source) <= TABLE_MAX_SOURCE_BYTES:
        raise ValueError("Provide 1 byte to 64 MiB of UTF-8 CSV or TSV data.")
    try:
        decoded = source.decode("utf-8-sig")
    except UnicodeError:
        raise ValueError("Data must use valid UTF-8 encoding.") from None
    if "\x00" in decoded:
        raise ValueError("NUL bytes are not supported in a scientific table.")
    reader = csv.reader(io.StringIO(decoded, newline=""), delimiter=options["delimiter"], strict=True)
    try:
        header = next(reader, None)
        if header is None:
            raise ValueError("The table requires a header and at least one row.")
        labels = [h.strip() for h in header]
        if not 1 <= len(labels) <= TABLE_MAX_COLUMNS or any(
            not h or len(h) > 256 or "\n" in h or "\r" in h for h in labels
        ) or len(set(labels)) != len(labels):
            raise ValueError("Use 1–256 distinct, nonempty headers (at most 256 characters each).")
        unused_hints = set(options["columnTypes"])
        col_names = [split_column_header(h)[0] for h in labels]
        for key in unused_hints:
            if key not in labels and col_names.count(key) != 1:
                raise ValueError(f"Type override {key!r} does not identify one column.")
        raw_rows: list[list[str]] = []
        for row in reader:
            if not row:
                continue
            if len(row) != len(labels):
                raise ValueError(f"Line {reader.line_num} has {len(row)} cells; expected {len(labels)}.")
            if len(raw_rows) >= TABLE_MAX_ROWS or (len(raw_rows) + 1) * len(labels) > TABLE_MAX_CELLS:
                raise ValueError("Table exceeds 100,000 rows or 1,000,000 cells.")
            if any(len(v) > TABLE_MAX_CELL_CHARS for v in row):
                raise ValueError(f"Line {reader.line_num} contains a cell longer than 65,536 characters.")
            raw_rows.append(row)
    except csv.Error as exc:
        raise ValueError(f"Invalid delimited text at line {reader.line_num}: {exc}") from None
    if not raw_rows:
        raise ValueError("The table needs at least one data row.")

    missing_set = {s.casefold() for s in options["missingValues"]}
    def missing_cell(value: str) -> bool:
        return value.strip().casefold() in missing_set

    columns: list[dict] = []
    rows: list[list] = [[None] * len(labels) for _ in raw_rows]
    for index, label in enumerate(labels):
        name, unit = split_column_header(label)
        present = [row[index] for row in raw_rows if not missing_cell(row[index])]
        kind = options["columnTypes"].get(label, options["columnTypes"].get(name, _kind(present)))
        missing_count = 0
        for row_index, row in enumerate(raw_rows):
            raw = row[index]
            if missing_cell(raw):
                missing_count += 1
                continue
            try:
                value = {"numeric": lambda: _numeric(raw.strip()),
                         "datetime": lambda: _datetime(raw.strip()),
                         "boolean": lambda: _boolean(raw),
                         "text": lambda: raw,
                         "categorical": lambda: raw}[kind]()
            except ValueError as exc:
                raise ValueError(f"Row {row_index + 2}, {label}: {exc}") from None
            rows[row_index][index] = value
        columns.append({"name": name, "label": label, "unit": unit, "type": kind, "missingCount": missing_count})
    payload = {"version": 1, "columns": columns, "rows": rows}
    encoded = canonical_bytes(payload)
    if len(encoded) > TABLE_MAX_ASSET_BYTES:
        raise ValueError("The normalized table asset exceeds 128 MiB.")
    ref = hashlib.sha256(encoded).hexdigest()
    source_hash = hashlib.sha256(source).hexdigest()
    receipt = {"fileHash": source_hash, "parseOptions": options, "tableHash": ref}
    receipt["receiptHash"] = fingerprint(receipt)
    if expected_preview is not None and receipt["receiptHash"] != expected_preview:
        raise ValueError("Source or parsing options changed. Preview the table again.")
    if not preview_only:
        _staging_store().put(encoded)
    return {
        "version": 1, "asset_ref": ref, "mime_type": "application/json",
        "rowCount": len(rows), "columnCount": len(columns), "columns": columns,
        "previewColumns": min(len(columns), 8),
        "preview": [[(v[:80] + "…" if isinstance(v, str) and len(v) > 80 else v)
                     for v in row[:8]] for row in rows[:8]],
        "sourceName": (Path(filename).name[:128] if filename else "Pasted data"),
        "provenance": receipt,
    }



def stage_result_table(table: dict, *, pack: str, operation_id: str) -> dict:
    """Store a generated table with the same typed schema as an imported dataset.

    Output values are already typed Python data: never guess that a *string*
    containing digits is numeric. Mixed-type columns are represented as text.
    The source hash protects the original output before normalization.
    """
    headers, original_rows = table["columns"], table["rows"]
    if not original_rows:
        raise ValueError("A generated typed table needs at least one row.")
    if len(set(headers)) != len(headers) or any(
        not label.strip() or "\r" in label or "\n" in label for label in headers
    ):
        raise ValueError("Generated table headers must be distinct, nonempty names.")
    columns: list[dict] = []
    rows = [list(row) for row in original_rows]
    for index, label in enumerate(headers):
        name, unit = split_column_header(label)
        present = [row[index] for row in rows if row[index] is not None]
        if present and all(type(value) in (int, float) for value in present):
            kind = "numeric"
            if any(type(value) is int and abs(value) > 2**53 - 1 for value in present):
                raise ValueError("Generated numeric table contains an inexact integer count.")
        elif present and all(type(value) is bool for value in present):
            kind = "boolean"
        elif present and all(isinstance(value, str) for value in present):
            if all(_is_iso_datetime(value) for value in present):
                kind = "datetime"
            else:
                distinct = len(set(present))
                kind = ("categorical" if distinct < len(present)
                        and distinct <= min(64, max(2, len(present) // 5)) else "text")
        else:
            kind = "text"
            for row in rows:
                value = row[index]
                if value is not None and not isinstance(value, str):
                    row[index] = ("true" if value is True else "false" if value is False
                                  else str(value))
        missing_count = sum(row[index] is None for row in rows)
        columns.append({"name": name, "label": label, "unit": unit,
                        "type": kind, "missingCount": missing_count})
    payload = {"version": 1, "columns": columns, "rows": rows}
    encoded = canonical_bytes(payload)
    if len(encoded) > TABLE_MAX_ASSET_BYTES:
        raise ValueError("The generated table asset exceeds 128 MiB.")
    ref = (_WORKER_STORE.get() or _staging_store()).put(encoded)
    receipt = {
        "fileHash": fingerprint(table),
        "parseOptions": {"source": "operation", "packId": pack, "operationId": operation_id},
        "tableHash": ref,
    }
    receipt["receiptHash"] = fingerprint(receipt)
    return {
        "version": 1, "asset_ref": ref, "mime_type": "application/json",
        "rowCount": len(rows), "columnCount": len(columns), "columns": columns,
        "previewColumns": min(len(columns), 8),
        "preview": [[(value[:80] + "…" if isinstance(value, str) and len(value) > 80 else value)
                     for value in row[:8]] for row in rows[:8]],
        "sourceName": f"{pack}.{operation_id} result"[:128],
        "provenance": receipt,
    }


def _is_iso_datetime(value: str) -> bool:
    try:
        return _datetime(value) == value
    except ValueError:
        return False


def _tables_in_state(data: dict) -> list[dict]:
    tables = []
    if "table" in data:
        tables.append(data["table"])
    latest = data.get("latest")
    if isinstance(latest, dict) and "tableAsset" in latest:
        tables.append(latest["tableAsset"])
    return tables

def table_metadata_valid(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    try:
        if type(data["version"]) is not int or data["version"] != 1 or not _REF.fullmatch(data["asset_ref"]) or data["mime_type"] != "application/json":
            return False
        cols = data["columns"]
        rows = data["rowCount"]
        if (not isinstance(cols, list) or type(data["columnCount"]) is not int
                or not 1 <= len(cols) == data["columnCount"] <= TABLE_MAX_COLUMNS):
            return False
        if type(rows) is not int or not 1 <= rows <= TABLE_MAX_ROWS or rows * len(cols) > TABLE_MAX_CELLS:
            return False
        if any(not isinstance(c, dict) or c.get("type") not in KINDS
               or not isinstance(c.get("name"), str) or not 0 < len(c["name"]) <= 256
               or not isinstance(c.get("label"), str) or not 0 < len(c["label"]) <= 256
               or not isinstance(c.get("unit"), str) or len(c["unit"]) > 256
               or type(c.get("missingCount")) is not int
               or not 0 <= c["missingCount"] <= rows for c in cols):
            return False
        preview_width = data["previewColumns"]
        preview = data["preview"]
        if (type(preview_width) is not int or preview_width != min(len(cols), 8)
                or not isinstance(preview, list) or not 1 <= len(preview) <= min(rows, 8)
                or not isinstance(data.get("sourceName"), str) or len(data["sourceName"]) > 128):
            return False
        if any(not isinstance(row, list) or len(row) != preview_width
               or any(cell is not None and type(cell) not in (str, int, float, bool)
                      or isinstance(cell, str) and len(cell) > 81
                      or type(cell) in (int, float) and not math.isfinite(cell) for cell in row)
               for row in preview):
            return False
        receipt = data["provenance"]
        if (not _REF.fullmatch(receipt["fileHash"]) or receipt["tableHash"] != data["asset_ref"]
                or not isinstance(receipt["parseOptions"], dict)):
            return False
        return receipt["receiptHash"] == fingerprint({k: v for k, v in receipt.items() if k != "receiptHash"})
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return False


def stage_from_saved(data: dict) -> None:
    """Restore both imported and generated tables from the workspace asset store."""
    from backend.session_load import _ACTIVE_ASSET_STORE
    store = _ACTIVE_ASSET_STORE.get()
    if store is None:
        return
    for table in _tables_in_state(data):
        if table_metadata_valid(table):
            raw = store.get(table["asset_ref"], max_bytes=TABLE_MAX_ASSET_BYTES)
            if raw is not None:
                _staging_store().put(raw)


def persist_table(data: dict) -> dict:
    """Persist every verified table asset in the active session asset store."""
    tables = _tables_in_state(data)
    if not tables:
        return data
    from backend.session_save import _ACTIVE_SAVE_ASSET_STORE
    store = _ACTIVE_SAVE_ASSET_STORE.get()
    if store is None:
        raise OSError("Saving a scientific table requires the workspace asset store.")
    for table in tables:
        if not table_metadata_valid(table):
            raise OSError("Scientific table metadata has a damaged receipt.")
        ref = table["asset_ref"]
        raw = _staging_store().get(ref, max_bytes=TABLE_MAX_ASSET_BYTES)
        if raw is not None:
            if store.put(raw) != ref:
                raise OSError("Scientific table asset digest changed during save.")
        elif not store.verify(ref):
            raise OSError("The scientific table asset is unavailable; restore it before saving.")
    return data


def read_table(meta: dict) -> dict:
    if not table_metadata_valid(meta):
        raise ValueError("The scientific table's import receipt is damaged.")
    raw = (_WORKER_STORE.get() or _staging_store()).get(meta["asset_ref"], max_bytes=TABLE_MAX_ASSET_BYTES)
    if raw is None:
        raise ValueError("The scientific table asset is missing. Restore the workspace asset and reopen.")
    try:
        table = json.loads(raw)
        if (not isinstance(table, dict) or type(table.get("version")) is not int
                or table["version"] != 1 or table.get("columns") != meta["columns"]
                or not isinstance(table.get("rows"), list)
                or len(table["rows"]) != meta["rowCount"]):
            raise ValueError
        columns = meta["columns"]
        missing_counts = [0] * len(columns)
        for row in table["rows"]:
            if not isinstance(row, list) or len(row) != len(columns):
                raise ValueError
            for index, value in enumerate(row):
                if value is None:
                    missing_counts[index] += 1
                elif columns[index]["type"] == "numeric":
                    if type(value) not in (int, float) or not math.isfinite(value):
                        raise ValueError
                elif columns[index]["type"] == "boolean":
                    if type(value) is not bool:
                        raise ValueError
                elif not isinstance(value, str) or len(value) > TABLE_MAX_CELL_CHARS:
                    raise ValueError
        if any(count != col["missingCount"] for count, col in zip(missing_counts, columns)):
            raise ValueError
    except (TypeError, ValueError, KeyError, OverflowError):
        raise ValueError("The scientific table asset is damaged.") from None
    return table



def table_csv_chunk(meta: dict, offset: int = 0) -> dict:
    """Export *all* typed rows in bounded binary CSV pages, never a wire-sized table.

    The verified table is converted once to a temporary CSV file per content
    digest. Paging uses byte offsets, so even Unicode and very long quoted
    cells round-trip without splitting a CSV record at transport boundaries.
    """
    if type(offset) is not int or offset < 0:
        raise ValueError("CSV offset must be a nonnegative integer.")
    if not table_metadata_valid(meta):
        raise ValueError("The scientific table receipt is damaged.")
    ref = meta["asset_ref"]
    with _CSV_LOCK:
        file = _CSV_CACHE.get(ref)
        if file is None:
            table = read_table(meta)
            directory = _csv_directory()
            file = directory / (ref + ".csv")
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", newline="", dir=directory,
                    suffix=".tmp", delete=False,
                ) as handle:
                    temporary = Path(handle.name)
                    writer = csv.writer(handle, lineterminator="\r\n")
                    writer.writerow([_csv_cell(col["label"]) for col in meta["columns"]])
                    for row in table["rows"]:
                        writer.writerow([_csv_cell(value) for value in row])
                if temporary is None:
                    raise OSError("Unable to stage the scientific CSV export.")
                if temporary.stat().st_size > CSV_MAX_BYTES:
                    raise ValueError("The CSV export exceeds 256 MiB.")
                os.replace(temporary, file)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            _CSV_CACHE[ref] = file
            if len(_CSV_CACHE) > 2:
                _, old_file = _CSV_CACHE.popitem(last=False)
                old_file.unlink(missing_ok=True)
        else:
            _CSV_CACHE.move_to_end(ref)
        total = file.stat().st_size
        if offset > total:
            raise ValueError("The CSV offset is beyond the end of the table.")
        with file.open("rb") as source:
            source.seek(offset)
            part = source.read(CSV_CHUNK_BYTES)
    next_offset = offset + len(part)
    return {
        "chunk": base64.b64encode(part).decode("ascii"),
        "nextOffset": next_offset, "totalBytes": total, "done": next_offset == total,
    }


def _csv_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        # Match the browser's CSV formula-injection defense.
        return "'" + value if re.match(r"^(?:[\t\r\n]|\s*[=+@-])", value) else value
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def numeric_selection(meta: dict, column: int, missing_policy: str, start: int, end: int | None,
                      complete_columns: list[int] | None = None, *, table: dict | None = None) -> tuple[list, list[int]]:
    """Return values with zero-based row identities from the verified source asset."""
    if not table_metadata_valid(meta):
        raise ValueError("The scientific table's import receipt is damaged.")
    if type(column) is not int or not 0 <= column < meta["columnCount"]:
        raise ValueError("Choose an existing table column.")
    if meta["columns"][column]["type"] != "numeric":
        raise ValueError("Only numeric table columns can supply numerical operation inputs.")
    if missing_policy not in ("reject", "drop"):
        raise ValueError("Choose either reject or explicitly drop missing measurements.")
    required = [column] if complete_columns is None else complete_columns
    if (not isinstance(required, list) or not 1 <= len(required) <= meta["columnCount"]
            or any(type(i) is not int or not 0 <= i < meta["columnCount"] for i in required)
            or column not in required or len(set(required)) != len(required)):
        raise ValueError("Complete-case selection must include this column and distinct existing column indices.")
    if any(meta["columns"][i]["type"] != "numeric" for i in required):
        raise ValueError("Choose numeric columns for paired measurements.")
    stop = meta["rowCount"] if end is None else end
    if type(start) is not int or type(stop) is not int or not 0 <= start < stop <= meta["rowCount"]:
        raise ValueError("Select a valid nonempty row range.")
    if stop - start > MAX_POINTS:
        raise ValueError(f"Existing operations accept at most {MAX_POINTS} measurements; choose a smaller row range.")
    rows = (read_table(meta) if table is None else table)["rows"]
    indices = [i for i in range(start, stop) if all(rows[i][j] is not None for j in required)]
    if missing_policy == "reject" and len(indices) != stop - start:
        raise ValueError("This column selection contains missing values. Choose 'Drop missing' explicitly.")
    if not indices:
        raise ValueError("No measurements remain after dropping missing values.")
    return [rows[i][column] for i in indices], indices


def numeric_column(meta: dict, column: int, missing_policy: str = "reject",
                   start: int = 0, end: int | None = None) -> list[float]:
    values, _ = numeric_selection(meta, column, missing_policy, start, end)
    return values


def decode_input(*, text: str | None, encoded_file: str | None) -> bytes:
    if (text is None) == (encoded_file is None):
        raise ValueError("Supply either pasted text or a file, not both.")
    if text is not None:
        if not isinstance(text, str):
            raise ValueError("Pasted data must be text.")
        if len(text) > TABLE_MAX_SOURCE_BYTES:
            raise ValueError("Scientific table source must be between 1 byte and 64 MiB.")
        encoded = text.encode("utf-8")
    else:
        if not isinstance(encoded_file, str) or len(encoded_file) > TABLE_MAX_SOURCE_BYTES * 4 // 3 + 8:
            raise ValueError("The uploaded file is too large.")
        try:
            encoded = base64.b64decode(encoded_file, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("The uploaded file encoding is invalid.") from None
    if not 0 < len(encoded) <= TABLE_MAX_SOURCE_BYTES:
        raise ValueError("Scientific table source must be between 1 byte and 64 MiB.")
    return encoded
