"""Lossless scene JSON reuse for the ADR-019 publish budget.

Full snapshots contain hundreds of unchanged strings and edges. CPython's
json.dumps on a 500-node structure is expensive; a C-level marshal.dumps
of the same JSON-compatible primitives is substantially cheaper while
preserving type, ordering, negative zero and nested mutations.

Marshal is used *only* to compare fresh in-memory value witnesses. No
marshal bytes are loaded or put on the wire. When the witness is unchanged,
reuse the previous, unmodified JSON text. Otherwise stdlib json.dumps
builds exactly the original wire bytes, with no codec or contract change.
The compact WebSocket path memoizes nodes and edges separately because a
snapshot's revision changes while those arrays usually do not.

The cache is per SceneDocument, and is cleared on load. Unsupported types
fall back to the original JSON encoder (including its errors).
"""

from __future__ import annotations

import json
import marshal
from typing import Any


def _witness(value: Any) -> bytes | None:
    try:
        # A fresh *value* snapshot notices in-place nested changes, unlike
        # identity-based keys. It also distinguishes False/0 and +/-0.0.
        return marshal.dumps(value, 4)
    except (TypeError, ValueError):
        return None


class SceneJSONCache:
    def __init__(self) -> None:
        self._payload_cache: tuple[bytes, str] | None = None
        self._array_cache: dict[
            tuple[bool, tuple[str, str], str], tuple[bytes, str]
        ] = {}

    def clear(self) -> None:
        self._payload_cache = None
        self._array_cache.clear()

    @staticmethod
    def _encode_full(payload: dict[str, Any]) -> str:
        return json.dumps(payload)

    @staticmethod
    def _encode_array_value(
        value: list[dict[str, Any]], *, ensure_ascii: bool, separators: tuple[str, str],
    ) -> str:
        return json.dumps(value, ensure_ascii=ensure_ascii, separators=separators)

    def _array(
        self, name: str, value: list[dict[str, Any]], *,
        ensure_ascii: bool, separators: tuple[str, str],
    ) -> str:
        key = (ensure_ascii, separators, name)
        stamp = _witness(value)
        cached = self._array_cache.get(key) if stamp is not None else None
        if cached is not None and cached[0] == stamp:
            return cached[1]
        encoded = self._encode_array_value(value, ensure_ascii=ensure_ascii, separators=separators)
        if stamp is not None:
            self._array_cache[key] = (stamp, encoded)
        else:
            self._array_cache.pop(key, None)
        return encoded

    def encode_payload(self, payload: dict[str, Any]) -> str:
        """Byte-for-byte json.dumps(payload) with default JSON settings."""
        stamp = _witness(payload)
        cached = self._payload_cache if stamp is not None else None
        if cached is not None and cached[0] == stamp:
            return cached[1]
        encoded = self._encode_full(payload)
        if stamp is not None:
            self._payload_cache = (stamp, encoded)
        else:
            self._payload_cache = None
        return encoded

    def encode_message(self, message: dict[str, Any]) -> str:
        """Byte-for-byte Starlette WebSocket.send_json's text encoding."""
        separators = (",", ":")
        if message.get("kind") != "state" or not isinstance(message.get("payload"), dict):
            return json.dumps(message, separators=separators, ensure_ascii=False)

        # Only large arrays are reused: the revision changes per envelope,
        # and every other field is encoded fresh.
        items: list[str] = []
        for key, value in message.items():
            if key == "payload":
                inner = []
                for field, field_value in value.items():
                    encoded = (
                        self._array(field, field_value, ensure_ascii=False, separators=separators)
                        if field in ("nodes", "edges")
                        else json.dumps(field_value, separators=separators, ensure_ascii=False)
                    )
                    inner.append(json.dumps(field, ensure_ascii=False) + ":" + encoded)
                part = "{" + ",".join(inner) + "}"
            else:
                part = json.dumps(value, separators=separators, ensure_ascii=False)
            items.append(json.dumps(key, ensure_ascii=False) + ":" + part)
        return "{" + ",".join(items) + "}"
