"""The hand-rolled Anthropic REST transport: headers, JSON POST/GET, SSE
streaming, message/kwargs preparation, and response-text extraction.

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
from typing import Any


def _anthropic_headers(api_key: str, extra_headers: dict | None = None) -> dict:
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    return headers


def _anthropic_get_json(endpoint: str, timeout: int = 30, cancel_event=None, api_key=None) -> dict:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    _mod._raise_if_cancelled(cancel_event)
    request = _mod.urllib.request.Request(
        endpoint,
        # api_key (6.5): an explicit key wins - list_models_for_config lists
        # a catalog for a config being EDITED, not the live provider's.
        headers=_mod._anthropic_headers(_mod._get_anthropic_api_key(api_key)),
        method="GET",
    )

    try:
        with _mod.urllib.request.urlopen(request, timeout=timeout) as response:
            _mod._raise_if_cancelled(cancel_event)
            return json.loads(response.read().decode("utf-8"))
    except _mod.urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Anthropic API request failed ({exc.code}): {details or exc.reason}") from exc
    except _mod.urllib.error.URLError as exc:
        raise ConnectionError(f"Failed to reach Anthropic API: {exc.reason}") from exc


def _attach_http_error_metadata(error: Exception, exc) -> Exception:
    """ADR-006 stage 6.8: the REST helpers used to raise a bare RuntimeError
    that destroyed the HTTPError's status/headers - the transport-retry
    predicate needs both. Attaches `status_code` (int) and `retry_after`
    (float seconds parsed from the Retry-After header, or None) onto the
    error about to be raised."""
    # setattr, not plain attribute assignment: status_code and retry_after
    # are metadata bolted onto an arbitrary Exception instance, so they are
    # declared on no class. Every reader already goes through
    # getattr(exc, ..., None) - see _is_transport_retryable and
    # _retry_after_from_exception in api_provider - and setattr is simply
    # the symmetric write side of that.
    setattr(error, "status_code", getattr(exc, "code", None))
    retry_after = None
    try:
        headers = getattr(exc, "headers", None)
        header_value = headers.get("Retry-After") if headers is not None else None
        if header_value is not None:
            retry_after = float(str(header_value).strip())
    except (TypeError, ValueError):
        retry_after = None
    setattr(error, "retry_after", retry_after)
    return error


def _anthropic_post_json(endpoint: str, body: dict, timeout: int = 180, cancel_event=None, api_key: str | None = None) -> dict:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    _mod._raise_if_cancelled(cancel_event)
    request = _mod.urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers=_mod._anthropic_headers(_mod._get_anthropic_api_key(api_key)),
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
            message = (
                parsed.get("error", {}).get("message")
                or parsed.get("message")
                or error_payload
            )
        except json.JSONDecodeError:
            message = error_payload
        # ADR-006 stage 6.8: status/Retry-After survive for the retry layer.
        raise _mod._attach_http_error_metadata(RuntimeError(message), exc) from exc
    except _mod.urllib.error.URLError as exc:
        raise ConnectionError(f"Failed to reach Anthropic API: {exc.reason}") from exc


def _anthropic_stream_sse(endpoint: str, body: dict, timeout: int = 180, cancel_event=None, api_key: str | None = None):
    """Streaming sibling of _anthropic_post_json (ADR-006 stage 6.5b): POSTs
    the same request with "stream": true and yields each SSE `data:` line's
    parsed JSON event dict. The raw REST wire shape is identical to the SDK's
    raw event stream from messages.create(stream=True), so AnthropicProvider
    consumes both transports through one translation loop. urllib's
    HTTPResponse is line-iterable; the finally's close() tears the live
    connection down whether the stream ends, errors, is cancelled, or the
    consumer close()es this generator mid-flight."""
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    _mod._raise_if_cancelled(cancel_event)
    request = _mod.urllib.request.Request(
        endpoint,
        data=json.dumps({**body, "stream": True}).encode("utf-8"),
        headers=_mod._anthropic_headers(_mod._get_anthropic_api_key(api_key)),
        method="POST",
    )

    try:
        response = _mod.urllib.request.urlopen(request, timeout=timeout)
    except _mod.urllib.error.HTTPError as exc:
        error_payload = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(error_payload)
            message = (
                parsed.get("error", {}).get("message")
                or parsed.get("message")
                or error_payload
            )
        except json.JSONDecodeError:
            message = error_payload
        # ADR-006 stage 6.8: status/Retry-After survive for the retry layer.
        raise _mod._attach_http_error_metadata(RuntimeError(message), exc) from exc
    except _mod.urllib.error.URLError as exc:
        raise ConnectionError(f"Failed to reach Anthropic API: {exc.reason}") from exc

    try:
        for raw_line in response:
            _mod._raise_if_cancelled(cancel_event)
            line = raw_line.decode("utf-8", errors="replace").strip()
            # SSE frames: `event: <name>` naming lines are redundant (every
            # data payload repeats its own "type") and blank lines are frame
            # separators - only `data:` lines carry events.
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                continue  # a torn/partial frame; the message_stop event is what ends the stream
    finally:
        response.close()


def _anthropic_content_block_from_part(part: dict) -> dict | None:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    part_type = part.get("type")

    if part_type == "text":
        text_value = str(part.get("text", ""))
        if text_value:
            return {"type": "text", "text": text_value}
        return None

    if part_type == "image_bytes":
        image_data = part.get("data")
        if not image_data:
            return None
        if isinstance(image_data, str):
            encoded_data = image_data
            raw_image_bytes = base64.b64decode(image_data)
        else:
            raw_image_bytes = bytes(image_data)
            encoded_data = base64.b64encode(raw_image_bytes).decode("utf-8")
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": _mod._guess_image_mime_type(raw_image_bytes),
                "data": encoded_data,
            },
        }

    if part_type == "audio_file":
        raise RuntimeError(
            "Anthropic Claude does not support audio input in Graphlink yet.\n\n"
            "Please remove the audio attachment or switch to Google Gemini or Ollama."
        )

    fallback_text = str(part.get("text", "")).strip()
    if fallback_text:
        return {"type": "text", "text": fallback_text}
    return None


def _prepare_anthropic_messages(messages: list, cancel_event=None) -> tuple[str | None, list]:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    system_parts = []
    anthropic_messages: list[dict[str, Any]] = []

    for msg in messages:
        _mod._raise_if_cancelled(cancel_event)
        role_name = str(msg.get("role") or "user").strip().lower()

        # ADR-007 stage 7.1: the two tool-turn roles ChatRequest's docstring
        # adds, translated to Anthropic's native shape - checked before the
        # system/text branches below since neither carries plain string/
        # part-list content the way ordinary turns do. Anthropic has no
        # separate "tool" role: a tool's result travels as a "user"-role
        # tool_result block, keyed by tool_use_id - the same value this
        # app's ToolCall.id / the generic message's tool_call_id carries.
        if role_name == "tool":
            blocks = [{
                "type": "tool_result",
                "tool_use_id": msg.get("tool_call_id", ""),
                "content": str(msg.get("content") or ""),
            }]
            if anthropic_messages and anthropic_messages[-1]["role"] == "user":
                anthropic_messages[-1]["content"].extend(blocks)
            else:
                anthropic_messages.append({"role": "user", "content": blocks})
            continue
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            blocks = []
            lead_in_text = str(msg.get("content") or "").strip()
            if lead_in_text:
                blocks.append({"type": "text", "text": lead_in_text})
            blocks.extend(
                {"type": "tool_use", "id": call["id"], "name": call["name"], "input": call["arguments"]}
                for call in tool_calls
            )
            anthropic_messages.append({"role": "assistant", "content": blocks})
            continue

        content = msg.get("content")

        if role_name == "system":
            system_text = _mod._stringify_message_content(content)
            if system_text:
                system_parts.append(system_text)
            continue

        role = "assistant" if role_name == "assistant" else "user"
        blocks = []

        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    text_value = str(part or "").strip()
                    if text_value:
                        blocks.append({"type": "text", "text": text_value})
                    continue
                anthropic_part = _mod._anthropic_content_block_from_part(part)
                if anthropic_part is not None:
                    blocks.append(anthropic_part)
        else:
            text_value = str(content or "").strip()
            if text_value:
                blocks.append({"type": "text", "text": text_value})

        if not blocks:
            continue

        if anthropic_messages and anthropic_messages[-1]["role"] == role:
            anthropic_messages[-1]["content"].extend(blocks)
            continue

        anthropic_messages.append({
            "role": role,
            "content": blocks,
        })

    system_prompt = "\n\n".join(part for part in system_parts if part).strip()
    return (system_prompt or None), anthropic_messages


def _prepare_anthropic_kwargs(task: str, kwargs: dict, model_id: str = "", reasoning_level: str = "off") -> dict:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    anthropic_kwargs = dict(kwargs or {})

    if "max_completion_tokens" in anthropic_kwargs and "max_tokens" not in anthropic_kwargs:
        anthropic_kwargs["max_tokens"] = anthropic_kwargs.pop("max_completion_tokens")

    if "stop" in anthropic_kwargs and "stop_sequences" not in anthropic_kwargs:
        stop_value = anthropic_kwargs.pop("stop")
        if isinstance(stop_value, str):
            anthropic_kwargs["stop_sequences"] = [stop_value]
        elif isinstance(stop_value, (list, tuple)):
            anthropic_kwargs["stop_sequences"] = [str(item) for item in stop_value if str(item)]

    if not anthropic_kwargs.get("max_tokens"):
        anthropic_kwargs["max_tokens"] = _mod.ANTHROPIC_DEFAULT_MAX_TOKENS.get(task, 4096)

    # anthropic_reasoning_kwargs may raise max_tokens (a requested thinking
    # budget must stay strictly under it) - update() applies that override
    # on top of the default/caller-supplied value set just above.
    anthropic_kwargs.update(
        _mod.anthropic_reasoning_kwargs(model_id, reasoning_level, anthropic_kwargs["max_tokens"])
    )

    return anthropic_kwargs


def _extract_anthropic_text(response) -> str:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    answer_parts = []
    reasoning_parts: list[str] = []
    reasoning_seen: set[str] = set()

    for block in _mod._extract_response_field(response, "content", []) or []:
        block_type = str(_mod._extract_response_field(block, "type", "")).strip().lower()
        if block_type == "text":
            answer_parts.append(str(_mod._extract_response_field(block, "text", "")))
            continue
        if block_type == "thinking":
            _mod._append_unique_text_segment(
                reasoning_parts,
                _mod._extract_response_field(block, "thinking") or _mod._extract_response_field(block, "text"),
                reasoning_seen,
            )

    return _mod._compose_reasoned_response(
        "".join(answer_parts).strip(),
        "\n\n".join(reasoning_parts).strip(),
        "Anthropic Claude",
    )


def _raise_if_cancelled(cancel_event=None):
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    if cancel_event is not None and getattr(cancel_event, "is_set", None) and cancel_event.is_set():
        raise _mod.RequestCancelledError("Request cancelled.")
