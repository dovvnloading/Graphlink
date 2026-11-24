"""Storage representation for typed workflow values.

The adapter boundary is the only place large structured values are hydrated.
The run journal and scheduler queues carry immutable content references.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from backend.asset_store import AssetStore, content_ref
from .contracts import AssetRef, FileValue, ImageValue, JsonValue, PortValue, TableValue, TextValue
from .ports import coerce_port
from .contracts import PortSpec

INLINE_LIMIT = 64 * 1024


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value: Any) -> str:
    return content_ref(canonical(value).encode("utf-8"))


class WorkflowArtifacts:
    def __init__(self, store: AssetStore):
        self.store = store

    def file(self, data: bytes, filename: str, media_type: str = "application/octet-stream",
             *, image: bool = False) -> FileValue | ImageValue:
        ref = self.store.put(data)
        asset = AssetRef(ref, ref, media_type, filename, len(data))
        return ImageValue(asset) if image else FileValue(asset)

    def bytes_for(self, asset: AssetRef) -> bytes:
        data = self.store.get(asset.sha256, max_bytes=asset.size_bytes)
        if data is None or len(data) != asset.size_bytes:
            raise ValueError("Workflow asset is missing or corrupt.")
        return data

    def pack(self, value: PortValue, *, persist: bool = True) -> dict[str, Any]:
        coerce_port(value, PortSpec("value", value.kind, True))
        payload = asdict(value)
        if isinstance(value, (FileValue, ImageValue)):
            self.bytes_for(value.asset)
            return {**payload, "asset_ref": value.asset.sha256, "mime_type": value.asset.media_type}
        # Validate/snapshot even inline values (JSON containers are mutable).
        raw = canonical(payload).encode("utf-8")
        if len(raw) > INLINE_LIMIT:
            ref = self.store.put(raw) if persist else content_ref(raw)
            return {"kind": value.kind, "asset_ref": ref, "mime_type": "application/json",
                    "size_bytes": len(raw)}
        return json.loads(raw)

    def unpack(self, payload: dict[str, Any]) -> PortValue:
        kind = payload["kind"]
        if kind in ("file", "image"):
            asset = AssetRef(**payload["asset"])
            self.bytes_for(asset)
            return FileValue(asset) if kind == "file" else ImageValue(asset)
        if "asset_ref" in payload:
            raw = self.store.get(payload["asset_ref"], max_bytes=payload["size_bytes"])
            if raw is None or len(raw) != payload["size_bytes"]:
                raise ValueError("Workflow value is missing or corrupt.")
            payload = json.loads(raw)
            if payload["kind"] != kind:
                raise ValueError("Workflow asset has the wrong port kind.")
        if kind == "text":
            return TextValue(payload["text"])
        if kind == "json":
            return JsonValue(payload["value"])
        if kind == "table":
            return TableValue(tuple(payload["columns"]), tuple(tuple(row) for row in payload["rows"]))
        raise ValueError("Unknown workflow value kind.")
