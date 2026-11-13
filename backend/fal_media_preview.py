"""Authenticated, SSRF-resistant media preview for Fal Media Studio images.

Graphlink's document CSP intentionally sets img-src 'self' data:, and that
must NOT be relaxed to permit arbitrary model-controlled remote URLs. The
preview route resolves a generated image from the current session's OWN
saved node gallery. It will only fetch HTTPS files from Fal's media CDN,
bounded by size and MIME and without forwarding any user credentials.
"""
from __future__ import annotations

import asyncio
import ipaddress
from urllib.parse import urlsplit

import requests
from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

from backend.assets import _get_canvas_document
from backend.domain.node_access import optional_node
from backend.domain.node_states import FalMediaState
from backend.events import EventBus
from backend.fal_media_domain import read_media

MAX_PREVIEW_BYTES = 20 * 1024 * 1024
MEDIA_CONTENT_TYPES = {
    "image/png", "image/jpeg", "image/webp", "image/gif", "image/avif",
}


def safe_media_host(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 4096:
        return False
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or (port is not None and port != 443):
        return False
    if parts.username or parts.password or not host or not parts.netloc:
        return False
    # Untrusted model output cannot coerce a fetch to localhost/private IP,
    # a company intranet, or an unrelated third-party CDN.
    if host != "fal.media" and not host.endswith(".fal.media"):
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    return True


def _image_bytes(url: str) -> tuple[bytes, str]:
    try:
        response = requests.get(url, timeout=(5, 25), stream=True, allow_redirects=False)
    except requests.RequestException:
        raise ValueError("The media preview could not be retrieved.") from None
    with response:
        if response.status_code != 200:
            raise ValueError("The media preview is unavailable.")
        media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if media_type not in MEDIA_CONTENT_TYPES:
            raise ValueError("Fal returned an unsupported preview content type.")
        length = response.headers.get("Content-Length")
        if length is not None:
            try:
                size = int(length)
            except (ValueError, TypeError):
                raise ValueError("Fal returned an invalid media length.") from None
            if size < 0 or size > MAX_PREVIEW_BYTES:
                raise ValueError("Media preview exceeds the 20 MiB size limit.")
        collected = bytearray()
        try:
            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                if len(collected) + len(chunk) > MAX_PREVIEW_BYTES:
                    raise ValueError("Media preview exceeds the 20 MiB size limit.")
                collected.extend(chunk)
        except requests.RequestException:
            raise ValueError("Media preview download was interrupted.") from None
        return bytes(collected), media_type


def register_fal_media_preview(app: FastAPI, event_bus: EventBus) -> None:
    @app.get("/api/fal-media/preview/{node_id}/{history_index}/{output_index}")
    async def preview_fal_media(
        node_id: str, history_index: int, output_index: int, session: str = "default",
    ) -> Response:
        document = _get_canvas_document(event_bus, session)
        if document is None:
            return JSONResponse({"error": "media not found"}, status_code=404)
        node = optional_node(document.nodes, node_id, "fal_media", FalMediaState)
        if node is None:
            return JSONResponse({"error": "media not found"}, status_code=404)
        media = read_media(node)
        history = media.get("history", [])
        if not isinstance(history, list) or history_index not in range(min(len(history), 12)):
            return JSONResponse({"error": "media not found"}, status_code=404)
        entry = history[history_index]
        if not isinstance(entry, dict) or not isinstance(entry.get("outputs"), list):
            return JSONResponse({"error": "media not found"}, status_code=404)
        outputs = entry["outputs"]
        if output_index not in range(min(len(outputs), 12)):
            return JSONResponse({"error": "media not found"}, status_code=404)
        output = outputs[output_index]
        if not isinstance(output, dict) or output.get("kind") != "image":
            return JSONResponse({"error": "media not found"}, status_code=404)
        url = output.get("url")
        if not isinstance(url, str) or not safe_media_host(url):
            return JSONResponse({"error": "media cannot be previewed safely"}, status_code=404)
        try:
            content, mime = await asyncio.to_thread(_image_bytes, url)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return Response(
            content=content,
            media_type=mime,
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": 'inline; filename="fal-preview"',
                "Cache-Control": "private, max-age=300",
                "Referrer-Policy": "no-referrer",
            },
        )
