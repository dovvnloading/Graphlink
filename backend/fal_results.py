"""Bounded views of heterogeneous Fal model results. No remote asset is fetched."""
from __future__ import annotations

import json
from itertools import islice
from typing import Any
from urllib.parse import urlsplit


def safe_https_url(value: object) -> str:
    if not isinstance(value, str) or not value.startswith("https://") or len(value) > 4096:
        return ""
    if "\\" in value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        return ""
    try:
        parsed = urlsplit(value)
        if not parsed.hostname or parsed.username is not None or parsed.password is not None:
            return ""
        if parsed.port not in (None, 443):
            return ""
    except ValueError:
        return ""
    return value


_EXTENSIONS = {
    "image": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".bmp", ".tif", ".tiff"},
    "video": {".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv"},
    "audio": {".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".opus"},
    "3d": {".glb", ".gltf", ".fbx", ".obj", ".ply", ".stl", ".usdz", ".splat"},
}
_FIELD_KINDS = {
    "image": {"image", "images", "thumbnail", "texture", "textures", "mask", "masks"},
    "video": {"video", "videos"},
    "audio": {"audio", "music", "speech"},
    "3d": {"mesh", "meshes", "glb", "gltf", "3d", "pointcloud"},
}


def _asset_kind(url: str, mime: object, filename: object, field: str) -> str:
    if isinstance(mime, str):
        major = mime.partition("/")[0].lower()
        if major in {"image", "video", "audio"}:
            return major
        if major == "model":
            return "3d"
    for name in (filename, urlsplit(url).path):
        if isinstance(name, str):
            ext = "." + name.rsplit(".", 1)[-1].lower()
            for kind, suffixes in _EXTENSIONS.items():
                if ext in suffixes:
                    return kind
    words = set(field.lower().split("_"))
    return next((kind for kind, hints in _FIELD_KINDS.items() if hints & words), "file")


def result_assets(data: Any, *, limit: int = 20) -> list[dict[str, str]]:
    """Index HTTPS output leaves in provider order, with RFC 6901 paths for reuse."""
    assets: list[dict[str, str]] = []
    seen: set[str] = set()
    visited = 0

    def visit(value, path: str, field: str, depth: int, mime=None, filename=None):
        nonlocal visited
        visited += 1
        if isinstance(value, str):
            url = safe_https_url(value)
            if url and url not in seen:
                assets.append({
                    "path": path, "url": url, "kind": _asset_kind(url, mime, filename, field),
                })
                seen.add(url)
            return
        if not isinstance(value, (list, dict)) or depth >= 32:
            return
        items = enumerate(value) if isinstance(value, list) else value.items()
        for key, item in items:
            if visited >= 4000 or len(assets) >= limit:
                break
            token = str(key).replace("~", "~0").replace("/", "~1")
            child_path = f"{path}/{token}"
            if len(child_path) > 1024:
                visited += 1
                continue
            if isinstance(value, dict) and key == "url":
                visit(item, child_path, field, depth + 1, value.get("content_type"), value.get("file_name"))
            else:
                visit(item, child_path, key if isinstance(key, str) else field, depth + 1)

    if limit > 0:
        visit(data, "", "", 0)
    return assets


def result_preview(data: Any, limit: int = 4096) -> str:
    """A plain-text/JSON excerpt for saved Studio history, excluding inline file data."""
    remaining = 128

    def excerpt(value: Any, depth: int = 0) -> Any:
        nonlocal remaining
        remaining -= 1
        if isinstance(value, str):
            if value.lstrip()[:5].lower() == "data:":
                return "[inline data omitted]"
            if value.startswith(("http://", "https://")) and not safe_https_url(value):
                return "[URL omitted]"
            return value[:limit] + ("…" if len(value) > limit else "")
        if isinstance(value, (dict, list)):
            if remaining <= 0 or depth >= 8:
                return "…"
            if isinstance(value, list):
                members = []
                for item in islice(value, 32):
                    if remaining <= 0:
                        break
                    members.append(excerpt(item, depth + 1))
                if len(members) < len(value):
                    members.append("…")
                return members
            fields: dict[str, Any] = {}
            for key, item in value.items():
                if remaining <= 0 or len(fields) >= 32:
                    fields["…"] = "additional fields omitted"
                    break
                fields[key[:160]] = ("[inline data omitted]" if "base64" in key.lower()
                                     else excerpt(item, depth + 1))
            return fields
        return value

    sample = excerpt(data)
    rendered = sample if isinstance(sample, str) else json.dumps(sample, indent=2, ensure_ascii=False)
    return rendered if len(rendered) <= limit else rendered[:limit - 1] + "…"
