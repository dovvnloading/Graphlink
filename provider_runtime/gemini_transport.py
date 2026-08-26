"""The hand-rolled Gemini REST transport: headers, JSON POST, SSE streaming,
File API upload/delete, content preparation, and response extraction.

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
import json
import os


def _gemini_headers(api_key: str, extra_headers: dict | None = None) -> dict:
    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    return headers


def _gemini_post_json(endpoint: str, body: dict, timeout: int = 120, cancel_event=None, api_key: str | None = None) -> dict:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    _mod._raise_if_cancelled(cancel_event)
    api_key = _mod._get_gemini_api_key(api_key)
    request = _mod.urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers=_mod._gemini_headers(api_key),
        method="POST",
    )

    try:
        with _mod.urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        _mod._raise_if_cancelled(cancel_event)
        return payload
    except _mod.urllib.error.HTTPError as exc:
        error_payload = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(error_payload)
            message = parsed.get("error", {}).get("message") or error_payload
        except json.JSONDecodeError:
            message = error_payload
        # ADR-006 stage 6.8: status/Retry-After survive for the retry layer.
        raise _mod._attach_http_error_metadata(RuntimeError(message), exc) from exc


def _gemini_stream_sse(endpoint: str, body: dict, timeout: int = 120, cancel_event=None, api_key: str | None = None):
    """Streaming sibling of _gemini_post_json (ADR-006 stage 6.5b): POSTs to
    :streamGenerateContent?alt=sse and yields each SSE `data:` line's parsed
    JSON - every line is a full GenerateContentResponse payload with its own
    candidates[0].content.parts[], the same shape _gemini_post_json returns
    whole. urllib's HTTPResponse is line-iterable; the finally's close()
    tears the live connection down on end, error, cancel, or consumer
    close() alike."""
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    _mod._raise_if_cancelled(cancel_event)
    api_key = _mod._get_gemini_api_key(api_key)
    request = _mod.urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers=_mod._gemini_headers(api_key),
        method="POST",
    )

    try:
        response = _mod.urllib.request.urlopen(request, timeout=timeout)
    except _mod.urllib.error.HTTPError as exc:
        error_payload = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(error_payload)
            message = parsed.get("error", {}).get("message") or error_payload
        except json.JSONDecodeError:
            message = error_payload
        # ADR-006 stage 6.8: status/Retry-After survive for the retry layer.
        raise _mod._attach_http_error_metadata(RuntimeError(message), exc) from exc

    try:
        for raw_line in response:
            _mod._raise_if_cancelled(cancel_event)
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue  # blank frame separators; Gemini's alt=sse sends no event: lines
            payload = line[len("data:"):].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                continue  # a torn/partial frame - the stream simply ends when the server closes it
    finally:
        response.close()


def _gemini_upload_file(
    file_path: str,
    mime_type: str,
    display_name: str | None = None,
    cancel_event=None,
    api_key: str | None = None,
) -> dict:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    _mod._raise_if_cancelled(cancel_event)
    api_key = _mod._get_gemini_api_key(api_key)
    resolved_path = os.path.abspath(file_path)
    file_size = os.path.getsize(resolved_path)
    upload_start = _mod.urllib.request.Request(
        f"{_mod.GEMINI_BASE_URL}/upload/v1beta/files",
        data=json.dumps({"file": {"display_name": display_name or os.path.basename(resolved_path)}}).encode("utf-8"),
        headers=_mod._gemini_headers(api_key, {
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(file_size),
            "X-Goog-Upload-Header-Content-Type": mime_type,
        }),
        method="POST",
    )

    try:
        with _mod.urllib.request.urlopen(upload_start, timeout=300) as response:
            upload_url = response.headers.get("X-Goog-Upload-URL")
        _mod._raise_if_cancelled(cancel_event)
    except _mod.urllib.error.HTTPError as exc:
        error_payload = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini file upload initialization failed: {error_payload}") from exc

    if not upload_url:
        raise RuntimeError("Gemini file upload did not return an upload URL.")

    if _mod.REQUESTS_AVAILABLE:
        with open(resolved_path, "rb") as source_file:
            response = _mod.requests.post(
                upload_url,
                headers={
                    "Content-Length": str(file_size),
                    "X-Goog-Upload-Offset": "0",
                    "X-Goog-Upload-Command": "upload, finalize",
                },
                data=source_file,
                timeout=1800,
            )
        _mod._raise_if_cancelled(cancel_event)
        if not response.ok:
            raise RuntimeError(f"Gemini file upload failed: {response.text}")
        payload = response.json()
    else:
        with open(resolved_path, "rb") as source_file:
            upload_request = _mod.urllib.request.Request(
                upload_url,
                data=source_file.read(),
                headers={
                    "Content-Length": str(file_size),
                    "X-Goog-Upload-Offset": "0",
                    "X-Goog-Upload-Command": "upload, finalize",
                },
                method="POST",
            )
            try:
                with _mod.urllib.request.urlopen(upload_request, timeout=1800) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                _mod._raise_if_cancelled(cancel_event)
            except _mod.urllib.error.HTTPError as exc:
                error_payload = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Gemini file upload failed: {error_payload}") from exc

    file_info = payload.get("file", {})
    file_uri = file_info.get("uri")
    file_name = file_info.get("name")
    resolved_mime = file_info.get("mimeType") or file_info.get("mime_type") or mime_type

    if not file_uri or not file_name:
        raise RuntimeError("Gemini file upload succeeded, but the file metadata was incomplete.")

    return {
        "name": file_name,
        "uri": file_uri,
        "mime_type": resolved_mime,
    }


def _gemini_delete_file(file_name: str, api_key: str | None = None):
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    if not file_name:
        return

    api_key = _mod._get_gemini_api_key(api_key)
    resource_name = file_name if str(file_name).startswith("files/") else f"files/{file_name}"
    delete_request = _mod.urllib.request.Request(
        f"{_mod.GEMINI_BASE_URL}/v1beta/{resource_name}",
        headers={"x-goog-api-key": api_key},
        method="DELETE",
    )
    try:
        with _mod.urllib.request.urlopen(delete_request, timeout=60):
            return
    except Exception:
        return


def _gemini_part_from_content(part: dict, uploaded_files: list, cancel_event=None, api_key: str | None = None) -> dict | None:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    _mod._raise_if_cancelled(cancel_event)
    part_type = part.get("type")
    if part_type == "text":
        return {"text": part.get("text", "")}

    if part_type == "image_bytes":
        image_data = part.get("data")
        if image_data:
            return {
                "inline_data": {
                    "mime_type": _mod._guess_image_mime_type(image_data),
                    "data": base64.b64encode(image_data).decode("utf-8"),
                }
            }
        return None

    if part_type == "audio_file":
        audio_path = part.get("path")
        if not audio_path or not os.path.isfile(audio_path):
            raise RuntimeError(
                f"Attached audio file is no longer available: {part.get('name') or audio_path or '[missing file]'}"
            )

        mime_type = part.get("mime_type") or _mod.guess_audio_mime_type(audio_path)
        upload_info = _mod._gemini_upload_file(
            audio_path,
            mime_type,
            part.get("name"),
            cancel_event=cancel_event,
            api_key=api_key,
        )
        uploaded_files.append(upload_info.get("name"))
        return {
            "file_data": {
                "mime_type": upload_info["mime_type"],
                "file_uri": upload_info["uri"],
            }
        }

    return None


def _prepare_gemini_contents(messages: list, cancel_event=None, api_key: str | None = None) -> tuple[str | None, list, list]:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    system_prompt = None
    contents = []
    uploaded_files = []

    for msg in messages:
        _mod._raise_if_cancelled(cancel_event)
        role_name = msg.get("role")

        # ADR-007 stage 7.1: the two tool-turn roles ChatRequest's docstring
        # adds, translated to Gemini's native shape - checked before the
        # system/text branches below. Gemini has its own dedicated
        # "function" role for a tool's result (unlike Anthropic's user-role
        # tool_result block) and, like Ollama, provides no native call id -
        # GeminiProvider.stream() synthesizes one the same way Ollama's does.
        if role_name == "tool":
            parts = [{
                "functionResponse": {
                    "name": msg.get("name", ""),
                    "response": {"result": str(msg.get("content") or "")},
                },
            }]
            if contents and contents[-1]["role"] == "function":
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": "function", "parts": parts})
            continue
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            parts = []
            lead_in_text = str(msg.get("content") or "").strip()
            if lead_in_text:
                parts.append({"text": lead_in_text})
            parts.extend(
                {"functionCall": {"name": call["name"], "args": call["arguments"]}}
                for call in tool_calls
            )
            contents.append({"role": "model", "parts": parts})
            continue

        if role_name == "system":
            system_prompt = msg.get("content")
            continue

        role = "model" if role_name == "assistant" else "user"
        content = msg.get("content")
        parts = []

        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    parts.append({"text": str(part)})
                    continue
                gemini_part = _mod._gemini_part_from_content(part, uploaded_files, cancel_event=cancel_event, api_key=api_key)
                if gemini_part is not None:
                    parts.append(gemini_part)
        else:
            parts.append({"text": str(content)})

        if not parts:
            continue

        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].extend(parts)
            continue

        contents.append({
            "role": role,
            "parts": parts,
        })

    return system_prompt, contents, uploaded_files


def _extract_gemini_text(payload: dict) -> str:
    prompt_feedback = payload.get("promptFeedback", {})
    block_reason = prompt_feedback.get("blockReason") or prompt_feedback.get("block_reason")
    if block_reason:
        raise RuntimeError(f"The response was blocked by Google's Safety Filters ({block_reason}).")

    text_parts = []
    for candidate in payload.get("candidates", []):
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            text = part.get("text")
            if text:
                text_parts.append(text)

    response_text = "".join(text_parts).strip()
    if not response_text:
        raise RuntimeError("Gemini returned an empty response.")
    return response_text


def _calculate_gemini_timeout(messages: list) -> int:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    max_audio_duration = 0
    for part in _mod._iter_audio_parts(messages):
        max_audio_duration = max(max_audio_duration, int(part.get("duration_seconds") or 0))

    if max_audio_duration <= 0:
        return 180
    return min(1800, max(300, 180 + max_audio_duration // 2))


def _extract_gemini_image_bytes(payload: dict) -> bytes:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    for candidate in payload.get("candidates", []):
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            inline_data = part.get("inline_data") or part.get("inlineData")
            if inline_data and inline_data.get("data"):
                return _mod._decode_base64_image(inline_data["data"])

    prompt_feedback = payload.get("promptFeedback", {})
    block_reason = prompt_feedback.get("blockReason") or prompt_feedback.get("block_reason")
    if block_reason:
        raise RuntimeError(f"Gemini blocked the image request: {block_reason}")

    model_text = []
    for candidate in payload.get("candidates", []):
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            if part.get("text"):
                model_text.append(part["text"])
    if model_text:
        raise RuntimeError(
            f"Gemini returned text instead of image data: {' '.join(model_text).strip()}"
        )

    raise RuntimeError("Gemini did not return image data.")
