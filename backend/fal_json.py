"""Bounded, navigable JSON for model schemas and inference results."""
from __future__ import annotations

import json
import re
from itertools import islice
from typing import Any

from backend.fal_integration import FalError

_PAGE_CHARS = 12_000


def select_pointer(document: Any, pointer: str) -> Any:
    """Resolve an RFC 6901 pointer locally, including empty keys and array indices."""
    if not isinstance(pointer, str) or len(pointer) > 4096:
        raise FalError("pointer must be a JSON Pointer of at most 4096 characters.")
    if not pointer:
        return document
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise FalError("Invalid JSON Pointer. Use /field/0, with ~0 for ~ and ~1 for /.")
    value = document
    for raw in pointer[1:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]{0,9}", key) and int(key) < len(value):
            value = value[int(key)]
        else:
            raise FalError("JSON Pointer does not exist in this response.")
    return value


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False))


def page_value(value: Any, *, offset: int = 0) -> dict:
    """Return exact values where possible. Large children are listed separately."""
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise FalError("offset must be a nonnegative integer.")
    if offset == 0 and _size(value) <= _PAGE_CHARS:
        return {"data": value}
    if not isinstance(value, (dict, list, str)) or offset > len(value):
        raise FalError("offset is outside this value.")
    if isinstance(value, str):
        # Each code point occupies at most six characters in encoded JSON.
        end = min(len(value), offset + _PAGE_CHARS // 6)
        page: dict = {"data": value[offset:end]}
    else:
        # Keep indices intact even when an array has an oversized element.
        # entries uses the literal key/index, so a real null stays unambiguous.
        items = value.items() if isinstance(value, dict) else enumerate(value)
        entries = []
        used = 0
        end = offset
        for key, child in islice(items, offset, offset + 100):
            entry: dict = {"key": key}
            if _size(child) <= _PAGE_CHARS // 2:
                entry["value"] = child
            else:
                entry["inspect"] = "Append this key to the pointer to read the value."
            cost = _size(entry)
            if cost > _PAGE_CHARS:
                # Provider-defined object keys can themselves exceed the tool budget.
                entry = {"omitted": "Key exceeds the display limit."}
                cost = _size(entry)
            if used + cost > _PAGE_CHARS:
                break
            entries.append(entry)
            used += cost + 1
            end += 1
        page = {"entries": entries, "type": "object" if isinstance(value, dict) else "array"}
    return {**page, "offset": offset, "total": len(value),
            "next_offset": end if end < len(value) else None}
