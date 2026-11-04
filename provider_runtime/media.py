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
from urllib.parse import urljoin

import requests

# Generated images can be larger than the web-research text fetch limit,
# but must not be allowed to exhaust the application process.
MAX_PROVIDER_IMAGE_BYTES = 16 * 1024 * 1024


def _fetch_provider_image_url(image_url: str) -> bytes:
    """Fetch provider-returned image data using a validated, pinned socket.

    A URL preflight followed by urllib.request.urlopen is not an SSRF
    boundary: urllib resolves the host AGAIN and automatically follows
    redirects to unrelated destinations. Reuse web research's pinned
    adapter and validate each redirect BEFORE making another request.
    """
    from graphlink_plugins.web_research.fetch_policy import FetchPolicy, URLPolicyError
    from graphlink_plugins.web_research.providers import _PinnedHTTPAdapter

    policy = FetchPolicy(max_bytes=MAX_PROVIDER_IMAGE_BYTES)
    current_url = image_url
    with requests.Session() as session:
        # Never inherit an ambient proxy that could route the validated
        # destination elsewhere or receive user credentials.
        session.trust_env = False
        for hop in range(policy.max_redirects + 1):
            try:
                target = policy.validate(current_url)
            except URLPolicyError as exc:
                raise RuntimeError(
                    f"Image endpoint returned a URL blocked by fetch policy: {exc}"
                ) from exc

            session.mount("https://", _PinnedHTTPAdapter(target.pinned_ip))
            try:
                response = session.get(
                    target.canonical_url,
                    timeout=(policy.connect_timeout_seconds, policy.read_timeout_seconds),
                    allow_redirects=False,
                    stream=True,
                )
            except requests.RequestException as exc:
                raise RuntimeError("Image endpoint image download failed.") from exc

            try:
                if response.status_code in (301, 302, 303, 307, 308):
                    if hop >= policy.max_redirects:
                        raise RuntimeError("Image endpoint exceeded the redirect limit.")
                    location = response.headers.get("Location")
                    if not location:
                        raise RuntimeError("Image endpoint returned an empty redirect.")
                    current_url = urljoin(target.canonical_url, location)
                    continue

                try:
                    response.raise_for_status()
                    claimed_size = response.headers.get("Content-Length", "")
                    if str(claimed_size).isdigit() and int(claimed_size) > MAX_PROVIDER_IMAGE_BYTES:
                        raise RuntimeError("Image endpoint image exceeds the download limit.")
                    chunks = []
                    total = 0
                    for chunk in response.iter_content(chunk_size=65536):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > MAX_PROVIDER_IMAGE_BYTES:
                            raise RuntimeError("Image endpoint image exceeds the download limit.")
                        chunks.append(chunk)
                    return b"".join(chunks)
                except requests.RequestException as exc:
                    raise RuntimeError("Image endpoint image download failed.") from exc
            finally:
                response.close()

    raise RuntimeError("Image endpoint exceeded the redirect limit.")


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
    # Provider-supplied base64 is untrusted just like provider-supplied URLs.
    # Bound the ENCODED string before decoding: a giant JSON blob must not
    # allocate a second, equally giant decoded buffer in this process.
    if not isinstance(image_data, (str, bytes)):
        raise RuntimeError("Generated image payload is not base64 text.")
    max_base64_chars = 4 * ((MAX_PROVIDER_IMAGE_BYTES + 2) // 3) + 4
    if len(image_data) > max_base64_chars:
        raise RuntimeError("Generated image payload exceeds the download limit.")
    try:
        decoded = base64.b64decode(image_data)
    except Exception as exc:
        raise RuntimeError(f"Failed to decode generated image payload: {exc}") from exc
    if len(decoded) > MAX_PROVIDER_IMAGE_BYTES:
        raise RuntimeError("Generated image payload exceeds the download limit.")
    return decoded


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
        return _fetch_provider_image_url(str(image_url))

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
