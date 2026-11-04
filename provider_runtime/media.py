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
        # This is an untrusted URL supplied by the provider, not a locally
        # chosen download address. The downloader MUST use the validated IP
        # for the socket and re-validate each redirect before connecting.
        return _download_provider_image_url(image_url)

    raise RuntimeError("Image endpoint response did not include b64_json or a URL.")


def _download_provider_image_url(image_url: str, *, policy=None) -> bytes:
    """Download a provider-returned image without an SSRF or unbounded-read gap.

    Validating an URL and THEN using urllib.request.urlopen() was unsafe:
    urllib follows redirects automatically, resolves DNS again (so a
    validate-then-connect DNS rebinding can hit loopback), reads an unbounded
    body into memory, and inherits ambient proxy settings. Reuse the web
    research fetcher's HTTPS/TLS-hostname-preserving *pinned IP* adapter;
    validate every redirect target before its own socket is opened.
    """
    import requests

    from graphlink_plugins.web_research.fetch_policy import FetchPolicy, URLPolicyError
    from graphlink_plugins.web_research.providers import _PinnedHTTPAdapter

    policy = policy if policy is not None else FetchPolicy(
        max_bytes=20 * 1024 * 1024,
        total_timeout_seconds=45.0,
    )
    if _PinnedHTTPAdapter is None:
        raise RuntimeError("Image downloading requires the requests package.")
    started = time.monotonic()
    current_url = image_url

    with requests.Session() as session:
        # Provider-supplied URLs must not inherit proxy credentials, HTTP
        # proxy addresses, netrc auth, or other process-local settings.
        session.trust_env = False
        for hop in range(policy.max_redirects + 1):
            if time.monotonic() - started > policy.total_timeout_seconds:
                raise RuntimeError("Image download exceeded the total time limit.")
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
                raise RuntimeError("Image endpoint download failed.") from exc

            try:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if hop >= policy.max_redirects or not location:
                        raise RuntimeError("Image endpoint returned an invalid redirect.")
                    current_url = urljoin(target.canonical_url, location)
                    # No request to current_url occurs until the next
                    # iteration validates its scheme, host and IP.
                    continue
                if response.status_code < 200 or response.status_code >= 300:
                    raise RuntimeError(f"Image endpoint returned HTTP {response.status_code}.")
                content_length = response.headers.get("Content-Length", "")
                if content_length.isdigit() and int(content_length) > policy.max_bytes:
                    raise RuntimeError("Image endpoint response exceeds the image size limit.")

                body = bytearray()
                try:
                    # One-byte reads bound the interval between timeout
                    # checks under slow-drip servers (urllib3 buffers the
                    # actual socket reads, avoiding one syscall per byte).
                    for chunk in response.iter_content(chunk_size=1):
                        if time.monotonic() - started > policy.total_timeout_seconds:
                            raise RuntimeError("Image download exceeded the total time limit.")
                        if len(body) + len(chunk) > policy.max_bytes:
                            raise RuntimeError("Image endpoint response exceeds the image size limit.")
                        body.extend(chunk)
                except requests.RequestException as exc:
                    raise RuntimeError("Image endpoint download failed.") from exc
                if time.monotonic() - started > policy.total_timeout_seconds:
                    raise RuntimeError("Image download exceeded the total time limit.")
                return bytes(body)
            finally:
                response.close()

    raise RuntimeError("Image endpoint exceeded the redirect limit.")


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
