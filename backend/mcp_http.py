"""MCP Streamable HTTP for the 2025 protocol family.

Supports JSON responses and request-scoped SSE with bounded messages, a
single negotiated session, and explicit bearer/custom-header credentials.
No HTTP redirect, automatic retry, environment proxy, remote elicitation,
or background SSE subscription can dispatch another tool call. A missing
reply is an uncertain external effect, left for the graph journal to fence.
"""

from __future__ import annotations

import ipaddress
import json
import math
import re
import threading
import time
from collections.abc import Iterator
from urllib.parse import parse_qsl, unquote_plus, urlencode, urlsplit, urlunsplit

import httpx

from backend.mcp_client import McpError, McpToolResult, McpToolSpec, list_mcp_tools, parse_mcp_result


HTTP_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
MAX_HTTP_MESSAGE_BYTES = 8_000_000
MAX_HTTP_STREAM_BYTES = 16_000_000
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_RESERVED_HEADERS = frozenset({
    "host", "content-length", "content-type", "accept", "accept-encoding",
    "connection", "transfer-encoding", "upgrade", "te", "trailer",
    "proxy-authorization", "proxy-connection", "origin", "referer",
    "mcp-session-id", "mcp-protocol-version",
})


def _credential_query_key(name: str) -> bool:
    # Decode names separately too: common gateways accept encoded aliases
    # such as api%5Fkey, and a key must not become public through that spelling.
    normalized = re.sub(r"[^a-z0-9]", "", unquote_plus(name).lower())
    return normalized in {
        "key", "auth", "authorization", "accesskey", "sig", "code", "pass", "passwd",
        "session", "sessionid", "sid", "subscriptionkey",
    } or normalized.endswith((
        "token", "apikey", "secret", "password", "passphrase", "credential", "credentials",
        "privatekey", "signature",
    ))


def http_endpoint_for_wire(url: str) -> str:
    """Hide credentials in legacy settings without deleting the connection.

    The visible redaction leaves the invalid credential location clear, so
    an edit requires the user to move it to write-only authentication headers.
    Non-secret query parameters retain their original bytes when unchanged.
    """
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return "[invalid endpoint; re-enter the URL]"
        _ = parsed.port
        query = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=128)
        hidden = any(_credential_query_key(key) for key, _value in query)
        netloc = parsed.netloc
        if parsed.username is not None or parsed.password is not None:
            netloc = "redacted@" + netloc.rsplit("@", 1)[-1]
        safe_query = urlencode([(key, "[redacted]" if _credential_query_key(key) else value)
                                for key, value in query]) if hidden else parsed.query
        # Fragments are never sent to MCP. Older imported URLs may put a
        # credential there, so no fragment is republished, even a benign one.
        fragment = "[redacted]" if parsed.fragment else ""
        return urlunsplit((parsed.scheme, netloc, parsed.path, safe_query, fragment))
    except (ValueError, TypeError):
        return "[invalid endpoint; re-enter the URL]"


def validate_http_endpoint(url: str) -> str:
    """Credentials travel in encrypted headers; HTTP is loopback-only."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
        query = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=128)
    except ValueError as exc:
        raise McpError("MCP endpoint is invalid. Enter an HTTPS URL or a loopback HTTP URL.") from exc
    if parsed.username is not None or parsed.password is not None or any(_credential_query_key(key) for key, _value in query):
        raise McpError("MCP URL credentials cannot be saved. Remove the URL credentials and enter them in authentication headers.")
    if (
        parsed.scheme not in {"http", "https"} or not parsed.hostname
        or parsed.fragment
        or any(ord(char) <= 32 for char in url) or (port is not None and not 1 <= port <= 65535)
    ):
        raise McpError("MCP endpoint is invalid. Use an HTTPS URL without embedded credentials or a fragment.")
    if parsed.scheme == "http":
        try:
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = parsed.hostname.lower() == "localhost"
        if not loopback:
            raise McpError("Remote MCP endpoints require HTTPS. HTTP is allowed only on loopback.")
    return url


def validate_http_headers(headers: dict[str, str]) -> dict[str, str]:
    """Prevent credential headers from overriding transport security/framing."""
    if not isinstance(headers, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in headers.items()):
        raise McpError("MCP authentication headers must be a JSON object with string names and values.")
    result: dict[str, str] = {}
    seen: set[str] = set()
    for key, value in headers.items():
        lower = key.lower()
        if (
            not _HEADER_NAME.fullmatch(key) or lower in _RESERVED_HEADERS
            or lower.startswith("mcp-") or lower in seen
            or len(value) > 16_384 or any(ord(char) < 32 or ord(char) > 126 for char in value)
        ):
            raise McpError("An MCP authentication header is invalid or reserved. Review it in Settings.")
        seen.add(lower)
        result[key] = value
    if len(result) > 32:
        raise McpError("MCP supports at most 32 authentication headers.")
    return result


def _decode_json(raw: bytes) -> dict:
    try:
        data = json.loads(raw, parse_constant=_reject_json_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise McpError("MCP server returned invalid JSON.") from exc
    if not isinstance(data, dict):
        raise McpError("MCP server returned a non-object response.")
    return data


def _reject_json_constant(_value: str):
    raise ValueError("Non-finite JSON number")


def _bounded_chunks(response: httpx.Response, deadline: float) -> Iterator[bytes]:
    total = 0
    for chunk in response.iter_raw():
        total += len(chunk)
        if total > MAX_HTTP_STREAM_BYTES:
            raise McpError("MCP response exceeded the stream limit.")
        if time.monotonic() > deadline:
            raise McpError("MCP server timed out. Review the request before running it again.")
        yield chunk


def _messages(response: httpx.Response, deadline: float) -> Iterator[dict]:
    """Parse fragmented UTF-8 SSE without unbounded line/event accumulation."""
    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        raise McpError("MCP endpoint ignored the uncompressed response requirement.")
    chunks = _bounded_chunks(response, deadline)
    if media_type == "application/json":
        body = bytearray()
        for chunk in chunks:
            body.extend(chunk)
            if len(body) > MAX_HTTP_MESSAGE_BYTES:
                raise McpError("MCP response exceeded the message limit.")
        yield _decode_json(bytes(body))
        return
    if media_type != "text/event-stream":
        raise McpError("MCP endpoint must return JSON or Server-Sent Events. Check its URL in Settings.")
    pending = bytearray()
    data: list[bytes] = []
    size = 0
    for chunk in chunks:
        pending.extend(chunk)
        while b"\n" in pending:
            line, _, remainder = pending.partition(b"\n")
            pending = bytearray(remainder)
            line = line.rstrip(b"\r")
            if not line:
                if data:
                    raw = b"\n".join(data)
                    if raw.strip():
                        yield _decode_json(raw)
                data, size = [], 0
            elif line.startswith(b"data:"):
                field = line[5:]
                if field.startswith(b" "):
                    field = field[1:]
                size += len(field)
                if size > MAX_HTTP_MESSAGE_BYTES:
                    raise McpError("MCP event exceeded the message limit.")
                data.append(bytes(field))
        if len(pending) > MAX_HTTP_MESSAGE_BYTES:
            raise McpError("MCP event line exceeded the message limit.")
    # An SSE event is dispatched only when terminated by a blank line.
    # A disconnected stream never turns an incomplete event into a success.


class McpHttpClient:
    def __init__(self, *, url: str, headers: dict[str, str] | None = None, timeout: float = 30.0):
        self.url = validate_http_endpoint(url)
        self._headers = validate_http_headers(headers or {})
        if not math.isfinite(timeout) or not 1 <= timeout <= 300:
            raise McpError("MCP timeout must be between 1 and 300 seconds.")
        self.timeout = timeout
        self._http: httpx.Client | None = None
        self._session_id: str | None = None
        self._next_id = 0
        self._lock = threading.Lock()
        self._connected = False
        self.protocol_version = HTTP_PROTOCOL_VERSIONS[0]
        self.server_info: dict | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected and self._http is not None

    def connect(self) -> None:
        if self.is_connected:
            return
        self._http = httpx.Client(timeout=self.timeout, follow_redirects=False, trust_env=False)
        try:
            result = self._call("initialize", {
                "protocolVersion": HTTP_PROTOCOL_VERSIONS[0], "capabilities": {},
                "clientInfo": {"name": "graphlink", "version": "1.0"},
            })
            negotiated = result.get("protocolVersion")
            if negotiated not in HTTP_PROTOCOL_VERSIONS:
                raise McpError("MCP server selected an unsupported protocol. Use a server supporting a 2025 revision.")
            self.protocol_version = negotiated
            info = result.get("serverInfo")
            self.server_info = info if isinstance(info, dict) else None
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, None)
            self._connected = True
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        http = self._http
        self._http = None
        self._connected = False
        # Session termination is best effort and never substitutes for a
        # tool cancellation or supplies evidence that a tool did not run.
        if http is not None:
            try:
                if self._session_id:
                    with http.stream("DELETE", self.url, headers=self._request_headers(), timeout=min(5, self.timeout)):
                        pass
            except httpx.HTTPError:
                pass
            finally:
                http.close()
        self._session_id = None

    def list_tools(self) -> tuple[McpToolSpec, ...]:
        self._require_connected()
        return list_mcp_tools(self._call)

    def call_tool(self, name: str, arguments: dict) -> McpToolResult:
        self._require_connected()
        return parse_mcp_result(self._call("tools/call", {"name": name, "arguments": dict(arguments or {})}))

    def _require_connected(self) -> None:
        if not self.is_connected:
            raise McpError("MCP client is not connected. Reconnect before using its tools.")

    def _request_headers(self) -> dict[str, str]:
        headers = {
            **self._headers, "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json", "Accept-Encoding": "identity",
            "MCP-Protocol-Version": self.protocol_version,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _call(self, method: str, params: dict) -> dict:
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            return self._post({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}, request_id)

    def _post(self, payload: dict, request_id: int | None) -> dict:
        http = self._http
        if http is None:
            raise McpError("MCP client is not connected.")
        deadline = time.monotonic() + self.timeout
        try:
            with http.stream("POST", self.url, json=payload, headers=self._request_headers()) as response:
                status = response.status_code
                if status in {401, 403}:
                    raise McpError("MCP authentication was rejected. Update the authentication headers in Settings.")
                if 300 <= status < 400:
                    raise McpError("MCP endpoint redirected. Enter its final HTTPS URL in Settings; credentials were not forwarded.")
                if status == 404 and self._session_id:
                    raise McpError("MCP session expired. Reconnect, and review any unfinished request before running again.")
                if status >= 400:
                    # Never expose response bodies: an authentication proxy
                    # or server may echo credential/header values there.
                    raise McpError(f"MCP endpoint returned HTTP {status}. Review its configuration before retrying.")
                if payload.get("method") == "initialize":
                    session_id = response.headers.get("mcp-session-id")
                    if session_id is not None:
                        if not session_id or len(session_id) > 1024 or any(not 33 <= ord(c) <= 126 for c in session_id):
                            raise McpError("MCP server returned an invalid session identifier.")
                        self._session_id = session_id
                if request_id is None:
                    if status not in {202, 204}:
                        raise McpError("MCP server did not acknowledge initialization.")
                    return {}
                for message in _messages(response, deadline):
                    if "method" in message:
                        if "id" in message:
                            raise McpError("This MCP server requested an unsupported client capability.")
                        continue
                    if type(message.get("id")) is not int or message["id"] != request_id:
                        continue
                    if message.get("jsonrpc") != "2.0":
                        raise McpError("MCP server returned an invalid JSON-RPC response.")
                    if "error" in message:
                        raise McpError("MCP server rejected the request. Review the tool and its arguments.")
                    result = message.get("result")
                    if not isinstance(result, dict):
                        raise McpError("MCP server returned a non-object result.")
                    return result
                raise McpError("MCP connection ended before a matching reply. Review the request before running it again.")
        except httpx.TimeoutException as exc:
            raise McpError("MCP server timed out. Review the request before running it again.") from exc
        except httpx.HTTPError as exc:
            raise McpError("MCP connection failed. Review the request before running it again.") from exc
