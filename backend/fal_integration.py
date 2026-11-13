"""Server-side fal.ai Model API client for arbitrary hosted model endpoints.

No model catalog is hardcoded: all fal model IDs (including nested endpoint paths)
are accepted after strict path validation. Authentication is never sent to the SPA.
Queue lifecycle URLs are BUILT from validated IDs, never followed from responses.
The HTTP integration needs only requests (already a Graphlink dependency).
Optional fal-client unlocks direct CDN uploads and realtime WebSocket sessions.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlencode
from uuid import UUID

import requests

QUEUE_ORIGIN = "https://queue.fal.run"
SYNC_ORIGIN = "https://fal.run"
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_MAX_BODY_BYTES = 24 * 1024 * 1024
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class FalError(RuntimeError):
    """A safe, user-presentable failure (never contains credentials)."""


def model_path(model_id: str) -> str:
    """Validate an arbitrary fal owner/app[/endpoint...] without URL injection."""
    if not isinstance(model_id, str) or not 3 <= len(model_id) <= 300:
        raise FalError("Model ID must be owner/app or owner/app/endpoint.")
    segments = model_id.split("/")
    if len(segments) < 2 or any(
        not _SEGMENT.fullmatch(part) or part in {".", ".."} or len(part) > 100
        for part in segments
    ):
        raise FalError("Invalid fal model ID; use owner/app[/endpoint], without URLs or query strings.")
    return "/".join(segments)


def request_path(model_id: str, request_id: str) -> str:
    path = model_path(model_id)
    try:
        parsed = UUID(str(request_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise FalError("Invalid fal request ID (expected UUID).") from exc
    # Fal queue status/result/cancel are scoped to owner/app, not a nested
    # endpoint route. Do not trust the response_url returned by a model.
    return f"{'/'.join(path.split('/')[:2])}/requests/{parsed}"


def _inputs(arguments: object) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise FalError("Model arguments must be a JSON object.")
    try:
        data = json.dumps(arguments, allow_nan=False, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise FalError("Model arguments must contain only JSON-compatible values.") from exc
    if len(data.encode("utf-8")) > _MAX_BODY_BYTES:
        raise FalError("Model arguments exceed the 24 MiB request limit; upload media instead.")
    return arguments


def _request_headers(options: dict | None) -> dict[str, str]:
    if options is None:
        return {}
    if not isinstance(options, dict):
        raise FalError("fal request options must be an object.")
    # Explicit allowlist: callers cannot override the Authorization, Host,
    # proxy or content-length headers with model-provided arbitrary values.
    permitted = {
        "start_timeout", "priority", "runner_hint", "no_retry",
        "store_io", "object_lifecycle", "disable_fallback", "retry_config",
    }
    unknown = set(options) - (permitted | {"webhook_url", "max_queue_length"})
    if unknown:
        raise FalError(f"Unknown fal request option(s): {', '.join(sorted(map(str, unknown)))}")
    headers: dict[str, str] = {}
    if options.get("start_timeout") is not None:
        value = options["start_timeout"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.1 < value <= 3600:
            raise FalError("start_timeout must be between 0.1 and 3600 seconds.")
        headers["X-Fal-Request-Start-Timeout"] = str(value)
    if options.get("priority") is not None:
        if options["priority"] not in ("normal", "low"):
            raise FalError("priority must be normal or low.")
        headers["X-Fal-Queue-Priority"] = options["priority"]
    if options.get("runner_hint") is not None:
        hint = options["runner_hint"]
        if not isinstance(hint, str) or len(hint) > 100 or any(ord(c) < 32 for c in hint):
            raise FalError("runner_hint must be printable and at most 100 characters.")
        headers["X-Fal-Runner-Hint"] = hint
    for field, header in [
        ("no_retry", "X-Fal-No-Retry"),
        ("disable_fallback", "x-app-fal-disable-fallback"),
    ]:
        if field in options:
            if not isinstance(options[field], bool):
                raise FalError(f"{field} must be a boolean.")
            if options[field]:
                headers[header] = "true"
    if "store_io" in options:
        if not isinstance(options["store_io"], bool):
            raise FalError("store_io must be a boolean.")
        headers["X-Fal-Store-IO"] = "1" if options["store_io"] else "0"
    for field, header in [
        ("object_lifecycle", "X-Fal-Object-Lifecycle-Preference"),
        ("retry_config", "X-Fal-Retry-Config"),
    ]:
        if field in options:
            value = options[field]
            if not isinstance(value, dict):
                raise FalError(f"{field} must be a JSON object.")
            headers[header] = json.dumps(value, allow_nan=False, separators=(",", ":"))
    return headers


def _queue_params(options: dict | None) -> dict[str, str | int]:
    options = options or {}
    params: dict[str, str | int] = {}
    webhook = options.get("webhook_url")
    if webhook is not None:
        from urllib.parse import urlsplit
        parsed = urlsplit(webhook) if isinstance(webhook, str) else None
        # HTTPS avoids transmitting outputs over cleartext. Local Graphlink
        # cannot receive a public webhook; the user must supply their own.
        if not parsed or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise FalError("webhook_url must be a public HTTPS URL.")
        params["fal_webhook"] = webhook
    length = options.get("max_queue_length")
    if length is not None:
        if isinstance(length, bool) or not isinstance(length, int) or not 1 <= length <= 100000:
            raise FalError("max_queue_length must be a positive integer (up to 100000).")
        params["fal_max_queue_length"] = length
    return params


def media_urls(data: Any, limit: int = 20) -> list[str]:
    """Collect likely generated media links from variable model output schemas."""
    urls: list[str] = []
    stack = [data]
    examined = 0
    while stack and examined < 2000 and len(urls) < limit:
        item = stack.pop()
        examined += 1
        if isinstance(item, dict):
            url = item.get("url")
            if isinstance(url, str) and url.startswith("https://") and len(url) < 4000 and url not in urls:
                urls.append(url)
            stack.extend(list(item.values())[:80])
        elif isinstance(item, list):
            stack.extend(item[:80])
    return urls


class FalClient:
    """Safe HTTP transport for any fal-hosted model, including custom apps."""

    def __init__(self, api_key: str, *, session: requests.Session | None = None, timeout: float = 90.0):
        if not isinstance(api_key, str) or not api_key.strip():
            raise FalError("Configure a fal.ai API key in Settings > Fal.ai, or set FAL_KEY.")
        if any(c in api_key for c in "\r\n"):
            raise FalError("Invalid fal API key.")
        self._key = api_key.strip()
        self.session = session or requests.Session()
        self.timeout = max(1.0, min(float(timeout), 300.0))

    def _call(self, method: str, origin: str, path: str, *,
              arguments: dict | None = None, params: dict | None = None,
              headers: dict | None = None) -> Any:
        # Only our hardcoded hosts are used. Redirects are disabled to keep
        # the key from reaching a malicious location.
        if origin not in {QUEUE_ORIGIN, SYNC_ORIGIN}:
            raise FalError("Unapproved fal API host.")
        url = origin + "/" + path
        if params:
            url += "?" + urlencode(params)
        auth_headers = {
            "Authorization": f"Key {self._key}",
            "Content-Type": "application/json",
        }
        auth_headers.update(headers or {})
        try:
            response = self.session.request(
                method, url, json=arguments, headers=auth_headers,
                timeout=self.timeout, allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise FalError(f"Could not reach fal.ai ({type(exc).__name__}).") from None
        with response:
            if not 200 <= response.status_code < 300:
                # The API might echo the submitted prompt/key in validation
                # errors, so never send raw error bodies to agent/UI/logs.
                raise FalError(f"fal.ai returned HTTP {response.status_code} for {method} {origin.split('//')[1]}.")
            if len(response.content) > _MAX_RESPONSE_BYTES:
                raise FalError("fal.ai returned a result larger than 4 MiB; retrieve media from its output URL.")
            try:
                return response.json()
            except (ValueError, requests.exceptions.JSONDecodeError):
                raise FalError("fal.ai returned an invalid JSON response.") from None

    def submit(self, model_id: str, arguments: dict, *, options: dict | None = None) -> dict:
        path = model_path(model_id)
        result = self._call(
            "POST", QUEUE_ORIGIN, path, arguments=_inputs(arguments),
            params=_queue_params(options), headers=_request_headers(options),
        )
        if not isinstance(result, dict) or not result.get("request_id"):
            raise FalError("fal.ai queue response did not include a request ID.")
        request_path(model_id, result["request_id"])
        return result

    def run(self, model_id: str, arguments: dict, *, options: dict | None = None) -> Any:
        options = options or {}
        if options.get("webhook_url") or options.get("max_queue_length"):
            raise FalError("webhook_url and max_queue_length are queue-only options.")
        return self._call(
            "POST", SYNC_ORIGIN, model_path(model_id),
            arguments=_inputs(arguments), headers=_request_headers(options),
        )

    def status(self, model_id: str, request_id: str, *, logs: bool = False) -> dict:
        result = self._call("GET", QUEUE_ORIGIN, request_path(model_id, request_id) + "/status",
                            params={"logs": 1} if logs else None)
        if not isinstance(result, dict) or result.get("status") not in {"IN_QUEUE", "IN_PROGRESS", "COMPLETED"}:
            raise FalError("fal.ai returned an invalid queue status.")
        return result

    def result(self, model_id: str, request_id: str) -> Any:
        return self._call("GET", QUEUE_ORIGIN, request_path(model_id, request_id))

    def cancel(self, model_id: str, request_id: str) -> dict:
        result = self._call("PUT", QUEUE_ORIGIN, request_path(model_id, request_id) + "/cancel")
        return result if isinstance(result, dict) else {"status": "UNKNOWN"}

    def wait(self, model_id: str, request_id: str, *, timeout: float = 240.0,
             interval: float = 1.5) -> Any:
        if isinstance(timeout, bool) or not 1 <= timeout <= 3600:
            raise FalError("wait timeout must be from 1 to 3600 seconds.")
        if not 0.25 <= interval <= 30:
            raise FalError("poll interval must be from 0.25 to 30 seconds.")
        deadline = time.monotonic() + timeout
        while True:
            status = self.status(model_id, request_id)
            if status["status"] == "COMPLETED":
                if status.get("error"):
                    raise FalError("fal.ai reported that this request failed; inspect status logs for details.")
                return self.result(model_id, request_id)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FalError("Timed out waiting for fal.ai; the queued request is still available.")
            time.sleep(min(interval, remaining))

    def stream_status(self, model_id: str, request_id: str, *,
                      logs: bool = False, timeout: float = 300) -> Iterator[dict]:
        """Stream Fal's native SSE progress without arbitrary URL requests."""
        path = request_path(model_id, request_id) + "/status/stream"
        url = QUEUE_ORIGIN + "/" + path + ("?logs=1" if logs else "")
        try:
            response = self.session.request(
                "GET", url, headers={"Authorization": f"Key {self._key}",
                                     "Accept": "text/event-stream"},
                timeout=(10, timeout), allow_redirects=False, stream=True,
            )
        except requests.RequestException as exc:
            raise FalError(f"fal.ai status stream could not connect ({type(exc).__name__}).") from None
        with response:
            if not 200 <= response.status_code < 300:
                raise FalError(f"fal.ai status stream returned HTTP {response.status_code}.")
            for line in response.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                try:
                    obj = json.loads(line[5:].strip())
                except (ValueError, TypeError):
                    continue
                if isinstance(obj, dict):
                    yield obj
                    if obj.get("status") == "COMPLETED":
                        break

    def upload_file(self, path: str | Path, *, max_bytes: int = 512 * 1024 * 1024) -> str:
        """Upload local media to the fal CDN; requires optional fal-client."""
        filename = Path(path).expanduser()
        if not filename.is_file() or filename.is_symlink():
            raise FalError("Upload path must be a regular, non-symlink file.")
        if filename.stat().st_size > max_bytes:
            raise FalError("File is too large for this upload policy.")
        try:
            import fal_client
        except ImportError as exc:
            raise FalError("File upload requires the optional package: pip install fal-client") from exc
        try:
            url = fal_client.SyncClient(key=self._key).upload_file(str(filename))
        except Exception as exc:
            # SDK exceptions might include signed URLs/credentials.
            raise FalError(f"fal.ai file upload failed ({type(exc).__name__}).") from None
        if not isinstance(url, str) or not url.startswith("https://"):
            raise FalError("fal.ai file upload returned an invalid URL.")
        return url

    def realtime(self, model_id: str, *, path: str = "/realtime"):
        """Optional realtime/WebSocket sessions via the official fal-client SDK.

        The caller owns the connection lifetime; the key stays server-side.
        """
        model_path(model_id)
        if not isinstance(path, str) or not path.startswith("/") or "//" in path or "?" in path:
            raise FalError("Invalid realtime path.")
        try:
            import fal_client
        except ImportError as exc:
            raise FalError("Realtime requires pip install fal-client") from exc
        return fal_client.SyncClient(key=self._key).realtime(model_id, path=path)
