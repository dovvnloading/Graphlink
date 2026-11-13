"""Authenticated, bounded direct-media upload for Fal Media Studio.

The canvas never sends image/video/audio bytes through the scene's WebSocket
JSON or persistent session file. A user-initiated POST streams a local file
into a private temporary path; the backend then uploads it to Fal CDN via
the optional official fal-client SDK and securely discards the temp copy.

This endpoint MUST inherit app.py's /api capability-token, Host and Origin
gates; never provide an auth-exempt or unauthenticated route.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.fal_integration import FalClient, FalError
from graphlink_settings_store import SettingsManager

MAX_UPLOAD_BYTES = 512 * 1024 * 1024
# No scripts, HTML or executable content; mime_type itself isn't trusted,
# but restricting extensions reduces accidental upload of local secrets.
_ALLOWED_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif",
    ".mp4", ".webm", ".mov", ".m4v", ".avi",
    ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac",
    ".glb", ".gltf", ".obj", ".ply", ".stl", ".fbx", ".usdz",
    ".pdf", ".zip", ".csv", ".srt", ".vtt",
})
_UPLOAD_SLOTS = asyncio.Semaphore(2)


def _suffix(filename: str) -> str | None:
    if not isinstance(filename, str) or len(filename) > 400:
        return None
    raw = unquote(filename)
    # Only the file extension is used for the server-side temporary name.
    # Accept Unicode filenames but not path traversal or control characters.
    if len(raw) > 160 or raw in (".", "..") or "/" in raw or chr(92) in raw or any(
        ord(character) < 32 or ord(character) == 127 for character in raw
    ):
        return None
    suffix = Path(raw).suffix.lower()
    return suffix if suffix in _ALLOWED_SUFFIXES else None


def register_fal_media_upload(app: FastAPI, manager: SettingsManager) -> None:
    @app.post("/api/fal-media/upload")
    async def upload_media(request: Request):
        file_header = request.headers.get("X-Fal-Media-Filename", "")
        extension = _suffix(file_header)
        if not extension:
            return JSONResponse(
                {"error": "Choose a supported media, 3D, document, caption or dataset file."},
                status_code=415,
            )
        length = request.headers.get("content-length")
        if length:
            try:
                value = int(length)
            except ValueError:
                return JSONResponse({"error": "Invalid upload length."}, status_code=400)
            if value <= 0 or value > MAX_UPLOAD_BYTES:
                return JSONResponse({"error": "Media upload size must be 1 B–512 MiB."}, status_code=413)

        if not manager.get_fal_api_key():
            return JSONResponse(
                {"error": "Configure a Fal.ai API key in Settings before uploading."},
                status_code=400,
            )

        # Keep temp files outside the graph data directory; TemporaryDirectory
        # creates a random mode-0700 directory, then removes it on all exits.
        async with _UPLOAD_SLOTS:
            with tempfile.TemporaryDirectory(prefix="graphlink-fal-media-") as temp:
                path = Path(temp) / f"upload{extension}"
                amount = 0
                with path.open("xb") as destination:
                    async for chunk in request.stream():
                        if not chunk:
                            continue
                        amount += len(chunk)
                        if amount > MAX_UPLOAD_BYTES:
                            return JSONResponse(
                                {"error": "Media upload exceeds 512 MiB."},
                                status_code=413,
                            )
                        await asyncio.to_thread(destination.write, chunk)
                if amount == 0:
                    return JSONResponse({"error": "File was empty."}, status_code=400)
                try:
                    client = FalClient(manager.get_fal_api_key())
                    url = await asyncio.to_thread(client.upload_file, path)
                except FalError as exc:
                    # FalClient sanitizes remote SDK exceptions. Never echo the
                    # key or any raw provider response body.
                    return JSONResponse({"error": str(exc)[:250]}, status_code=502)
                except (ValueError, TypeError):
                    return JSONResponse({"error": "Fal upload could not be completed."}, status_code=502)
                return JSONResponse(
                    {"url": url, "bytes": amount},
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
                )
