"""Attachment and media helpers shared across providers: image decoding,
MIME sniffing, audio-part iteration, and message-content stringification.

Function bodies are relocated VERBATIM from api_provider.py; only the
patch-seam rewrites below are new. Any name that lives in api_provider's
module namespace (module globals, sibling helpers, constants, and the
`ollama`/`urllib`/`requests` module bindings) is accessed late-bound as
`_mod.<name>` through an in-body deferred `import api_provider as _mod`,
NEVER via a module-top import here: a top-level `from api_provider import X`
would be a circular import (api_provider imports this module at ITS top)
AND would freeze the name at import time, making the test suite's
`monkeypatch.setattr(api_provider, "X", ...)` patches invisible to these
functions. The deferred-import-then-attribute pattern resolves the name on
api_provider at call time, so those patch seams keep working with zero test
changes. api_provider.py re-exports every name below, so every existing
`api_provider.<name>` caller and patch site is unchanged.
"""

from __future__ import annotations

import base64
import os
import time
from urllib.parse import urljoin


def _read_attachment_bytes(file_path: str, attachment_kind: str) -> bytes:
    resolved_path = os.path.abspath(file_path or "")
    attachment_name = os.path.basename(resolved_path or file_path or "")
    if not resolved_path or not os.path.isfile(resolved_path):
        raise RuntimeError(
            f"Attached {attachment_kind} file is no longer available: "
            f"{attachment_name or '[missing file]'}"
        )

    try:
        with open(resolved_path, "rb") as source_file:
            return source_file.read()
    except OSError as exc:
        raise RuntimeError(
            f"Failed to read attached {attachment_kind} file '{attachment_name}': {exc}"
        ) from exc


def _decode_base64_image(image_data: str) -> bytes:
    try:
        return base64.b64decode(image_data)
    except Exception as exc:
        raise RuntimeError(f"Failed to decode generated image payload: {exc}") from exc


# Maximum decoded bytes from a provider-supplied image URL. This is a
# NETWORK INPUT limit, not just an output/context cap.
_IMAGE_DOWNLOAD_MAX_BYTES = 16 * 1024 * 1024


def _download_pinned_provider_image(image_url: str) -> bytes:
    """Fetch untrusted provider output with SSRF checks on *every* socket.

    An initial FetchPolicy.validate(url) followed by urllib.urlopen(url)
    is unsafe: urllib re-resolves DNS, honors ambient proxies, and follows
    redirects without validating their destinations. Reuse the research
    fetcher's pinned-IP/TLS-SNI adapter, disable ambient proxies, and
    validate every redirect target before opening a connection.
    """
    import requests

    from graphlink_plugins.web_research.fetch_policy import FetchPolicy, URLPolicyError
    from graphlink_plugins.web_research.providers import _PinnedHTTPAdapter

    policy = FetchPolicy()
    deadline = time.monotonic() + policy.total_timeout_seconds
    current_url = image_url
    with requests.Session() as session:
        session.trust_env = False
        for hop in range(policy.max_redirects + 1):
            try:
                validated = policy.validate(current_url)
            except URLPolicyError as exc:
                raise RuntimeError(f"Image endpoint returned a URL blocked by fetch policy: {exc}") from exc

            if time.monotonic() >= deadline:
                raise RuntimeError("Image download exceeded the total time limit.")
            session.mount("https://", _PinnedHTTPAdapter(validated.pinned_ip))
            try:
                with session.get(
                    validated.canonical_url,
                    stream=True,
                    allow_redirects=False,
                    timeout=(policy.connect_timeout_seconds, policy.read_timeout_seconds),
                    headers={"Accept": "image/png,image/jpeg,image/webp"},
                ) as response:
                    if 300 <= response.status_code < 400:
                        location = response.headers.get("Location")
                        if not location:
                            raise RuntimeError("Image endpoint redirected without a location.")
                        if hop >= policy.max_redirects:
                            raise RuntimeError("Image endpoint exceeded the redirect limit.")
                        current_url = urljoin(validated.canonical_url, location)
                        continue
                    response.raise_for_status()

                    # Size is checked on decoded streamed bytes, not just
                    # Content-Length (which may be absent or dishonest).
                    header_size = response.headers.get("Content-Length", "")
                    try:
                        advertised_size = int(header_size)
                    except (TypeError, ValueError):
                        advertised_size = 0
                    if advertised_size > _IMAGE_DOWNLOAD_MAX_BYTES:
                        raise RuntimeError("Image download exceeded the size limit.")

                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_content(chunk_size=1024):
                        if time.monotonic() >= deadline:
                            raise RuntimeError("Image download exceeded the total time limit.")
                        total += len(chunk)
                        if total > _IMAGE_DOWNLOAD_MAX_BYTES:
                            raise RuntimeError("Image download exceeded the size limit.")
                        chunks.append(chunk)
                    image = b"".join(chunks)
                    if _guess_image_mime_type(image) == "application/octet-stream":
                        raise RuntimeError("Image endpoint returned unsupported image data.")
                    return image
            except requests.RequestException as exc:
                # Do not include URL / query tokens or raw response bodies in
                # a user-facing error message from an untrusted endpoint.
                raise RuntimeError("Image endpoint download failed.") from exc
    raise RuntimeError("Image endpoint exceeded the redirect limit.")


def _extract_openai_image_bytes(response) -> bytes:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    data_items = getattr(response, "data", None)
    if not data_items:
        raise RuntimeError("Image endpoint returned no image payload.")

    first_item = data_items[0]
    b64_json = getattr(first_item, "b64_json", None)
    if not b64_json and isinstance(first_item, dict):
        b64_json = first_item.get("b64_json")
    if b64_json:
        return _mod._decode_base64_image(b64_json)

    image_url = getattr(first_item, "url", None)
    if not image_url and isinstance(first_item, dict):
        image_url = first_item.get("url")
    if image_url:
        return _download_pinned_provider_image(str(image_url))

    raise RuntimeError("Image endpoint response did not include b64_json or a URL.")


def _guess_image_mime_type(image_data: bytes) -> str:
    if image_data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_data.startswith(b"RIFF") and image_data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _iter_audio_parts(messages: list):
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "audio_file":
                yield part


def _message_contains_audio(messages: list) -> bool:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    return any(True for _ in _mod._iter_audio_parts(messages))


def _stringify_message_content(content) -> str:
    if isinstance(content, list):
        text_parts = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    text_parts.append(str(part.get("text", "")))
                elif "text" in part:
                    text_parts.append(str(part.get("text", "")))
            else:
                text_parts.append(str(part))
        return "\n".join(part for part in text_parts if part).strip()
    return str(content or "").strip()
