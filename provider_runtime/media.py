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
        # SECURITY-FIX: image_url comes verbatim from the image endpoint's
        # response - the OpenAI-compatible base_url is user-configurable and,
        # under the threat model, a hostile or MITM'd provider response
        # (boundary (d)) is untrusted. urllib.request.urlopen installs the
        # file://, data: and ftp: handlers by default, so an unchecked URL
        # here let a response body read an arbitrary LOCAL file (proven:
        # file:///.../secret returned the file's bytes) or hit an internal
        # host - classic SSRF - with the bytes then persisted and served
        # back. The web_research subsystem already solved exactly this for
        # LLM-reached URLs; reuse its audited FetchPolicy (https-only,
        # rejects private/loopback/link-local/metadata addresses) rather
        # than rolling a second, weaker check here.
        from graphlink_plugins.web_research.fetch_policy import FetchPolicy, URLPolicyError

        try:
            FetchPolicy().validate(image_url)
        except URLPolicyError as exc:
            raise RuntimeError(
                f"Image endpoint returned a URL blocked by fetch policy: {exc}"
            ) from exc
        try:
            with _mod.urllib.request.urlopen(image_url, timeout=120) as resp:
                return resp.read()
        except Exception as exc:
            raise RuntimeError(
                f"Image endpoint returned a URL, but the image download failed: {exc}"
            ) from exc

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
