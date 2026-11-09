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


def _read_attachment_bytes(
    file_path: str, attachment_kind: str, expected_sha256: str | None = None,
) -> bytes:
    """Read only the staged attachment limit, rejecting files changed after staging.

    A stat when the user attaches audio does not bound a later provider read:
    another process can grow or replace the file while the user composes.
    Keep the actual send capped and compare the stage-time content fingerprint.
    """
    import hashlib

    from backend.attachments import MAX_STAGED_FILE_BYTES

    resolved_path = os.path.abspath(file_path or "")
    attachment_name = os.path.basename(resolved_path or file_path or "")
    if not resolved_path or not os.path.isfile(resolved_path):
        raise RuntimeError(
            f"Attached {attachment_kind} file is no longer available: "
            f"{attachment_name or '[missing file]'}"
        )
    try:
        with open(resolved_path, "rb") as source_file:
            data = source_file.read(MAX_STAGED_FILE_BYTES + 1)
    except OSError as exc:
        raise RuntimeError(
            f"Failed to read attached {attachment_kind} file '{attachment_name}': {exc}"
        ) from exc
    if len(data) > MAX_STAGED_FILE_BYTES:
        raise RuntimeError("Attached audio exceeds the file size limit; reattach a smaller file.")
    if expected_sha256 is not None and hashlib.sha256(data).hexdigest() != expected_sha256:
        raise RuntimeError("Attached audio changed since it was selected; reattach the file.")
    return data


def _decode_base64_image(image_data: str) -> bytes:
    # Encoded inline images are untrusted input just like downloaded URLs.
    # Check encoded size before allocating the decoded copy.
    if not isinstance(image_data, (str, bytes)):
        raise RuntimeError("Generated image payload is not base64 text.")
    max_encoded_chars = 4 * ((_MAX_PROVIDER_IMAGE_BYTES + 2) // 3) + 4
    if len(image_data) > max_encoded_chars:
        raise RuntimeError("Generated image payload exceeds the download limit.")
    try:
        decoded = base64.b64decode(image_data)
    except Exception as exc:
        raise RuntimeError(f"Failed to decode generated image payload: {exc}") from exc
    if len(decoded) > _MAX_PROVIDER_IMAGE_BYTES:
        raise RuntimeError("Generated image payload exceeds the download limit.")
    return decoded


# A provider response is untrusted: an arbitrarily large image or one
# byte at a time can exhaust local RAM or stall the worker. The same cap is
# enforced on decoded streamed bytes, not just Content-Length.
_MAX_PROVIDER_IMAGE_BYTES = 20 * 1024 * 1024


def _download_provider_image_url(image_url: str) -> bytes:
    """Fetch an untrusted image URL with the same pinned-IP SSRF boundary
    used by Web Research, including EVERY redirect hop.

    A pre-flight validate() followed by urllib.request.urlopen() is NOT an
    SSRF defense: urllib re-resolves DNS at connect time and follows a 302
    to a private address without consulting that validation again.
    """
    import requests

    from graphlink_plugins.web_research.fetch_policy import FetchPolicy, URLPolicyError
    from graphlink_plugins.web_research.providers import _PinnedHTTPAdapter

    policy = FetchPolicy(max_bytes=_MAX_PROVIDER_IMAGE_BYTES)
    current = image_url
    started = time.monotonic()
    with requests.Session() as session:
        # Do not inherit HTTP(S)_PROXY, credentials, or .netrc configuration.
        session.trust_env = False
        for hop in range(policy.max_redirects + 1):
            if time.monotonic() - started > policy.total_timeout_seconds:
                raise RuntimeError("Image download exceeded the total time limit.")
            try:
                target = policy.validate(current)
            except URLPolicyError as exc:
                raise RuntimeError(f"Image endpoint returned a URL blocked by fetch policy: {exc}") from exc
            # Route the actual HTTPS socket to the IP that was validated,
            # while retaining the hostname for Host, SNI and TLS verification.
            session.mount("https://", _PinnedHTTPAdapter(target.pinned_ip))
            try:
                response = session.get(
                    target.canonical_url,
                    timeout=(policy.connect_timeout_seconds, policy.read_timeout_seconds),
                    allow_redirects=False,
                    stream=True,
                )
            except requests.RequestException as exc:
                raise RuntimeError("Image endpoint URL could not be fetched.") from exc
            try:
                if 300 <= response.status_code < 400:
                    if hop >= policy.max_redirects:
                        raise RuntimeError("Image download exceeded the redirect limit.")
                    location = response.headers.get("Location")
                    if not location:
                        raise RuntimeError("Image download redirected without a destination.")
                    current = urljoin(target.canonical_url, location)
                    continue
                if response.status_code >= 400:
                    raise RuntimeError(f"Image endpoint URL returned HTTP {response.status_code}.")
                length = response.headers.get("Content-Length", "")
                if length.isdecimal() and int(length) > _MAX_PROVIDER_IMAGE_BYTES:
                    raise RuntimeError("Image download exceeds the size limit.")
                body = bytearray()
                # Iterating in larger chunks lets a slow-drip server hold
                # each read waiting for the full chunk indefinitely while
                # resetting the per-socket read timeout. One-byte reads can
                # check the total deadline between each received byte.
                try:
                    for chunk in response.iter_content(chunk_size=1):
                        if time.monotonic() - started > policy.total_timeout_seconds:
                            raise RuntimeError("Image download exceeded the total time limit.")
                        if len(body) + len(chunk) > _MAX_PROVIDER_IMAGE_BYTES:
                            raise RuntimeError("Image download exceeds the size limit.")
                        body.extend(chunk)
                except requests.RequestException as exc:
                    raise RuntimeError("Image download stream failed.") from exc
                if time.monotonic() - started > policy.total_timeout_seconds:
                    raise RuntimeError("Image download exceeded the total time limit.")
                return bytes(body)
            finally:
                response.close()
    raise RuntimeError("Image download exceeded the redirect limit.")


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
        return _download_provider_image_url(image_url)

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
