"""Read-only Graphlink MCP stdio server backed by ONE running Graphlink API.

This is a protocol adapter, never another Graphlink backend. It uses only
the existing authenticated /api/control routes on 127.0.0.1, and exposes
no mutation, shell, filesystem, settings or approval-bypass tools.

Supports MCP's 2026-07-28 stateless server/discover era and older
initialize-based stdio clients (including Graphlink's own MCP client).
Every stdout line is a JSON-RPC message; errors/logging go to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, TextIO

from backend.control import DEFAULT_CONTROL_PORT, DEFAULT_TOKEN_FILE

MODERN_VERSION = "2026-07-28"
LEGACY_VERSIONS = frozenset({"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"})
LATEST_LEGACY_VERSION = "2025-11-25"
SERVER_INFO = {"name": "graphlink-control", "version": "0.1.0"}
MAX_INPUT_LINE = 256_000
MAX_RESPONSE_BYTES = 512_000

_TOOL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "graphlink_list_workspaces",
        "description": "List Graphlink workspaces in the running local backend. Read only.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "graphlink_list_graphs",
        "description": "List saved graphs with metadata, optionally filtered by workspace ID. Read only.",
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "integer", "minimum": 1},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "graphlink_get_graph",
        "description": "Read saved graph metadata and a PAGE of node excerpts; does not load or alter the live canvas.",
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "graph_id": {"type": "integer", "minimum": 1},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["graph_id"],
        },
    },
    {
        "name": "graphlink_list_nodes",
        "description": "Page through nodes on the currently open LIVE canvas. Read only; no graph switch.",
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "kind": {"type": "string", "maxLength": 100},
                "query": {"type": "string", "maxLength": 200},
                "offset": {"type": "integer", "minimum": 0},
            },
        },
    },
    {
        "name": "graphlink_read_subgraph",
        "description": "Read bounded neighbours and edges of a node on the LIVE canvas.",
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "root_id": {"type": "string", "minLength": 1, "maxLength": 200},
                "depth": {"type": "integer", "minimum": 1, "maximum": 8},
                "direction": {"type": "string", "enum": ["down", "up", "both"]},
            },
            "required": ["root_id"],
        },
    },
)
TOOLS = tuple({**item, "annotations": {"readOnlyHint": True, "destructiveHint": False}} for item in _TOOL_SPECS)
_BY_NAME = {item["name"]: item for item in TOOLS}


class ControlError(Exception):
    """Expected tool-level failure to return to MCP without crashing stdio."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never send a local capability token to a redirected origin.
        return None


class ControlHttpClient:
    def __init__(self, base_url: str, token: str):
        parsed = urllib.parse.urlsplit(base_url)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                or parsed.username or parsed.password or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment or parsed.port is None):
            raise ValueError("Control URL must be http://127.0.0.1:<port> with no path or query")
        if not token:
            raise ValueError("headless capability token is empty")
        self.base_url = f"http://127.0.0.1:{parsed.port}"
        self._token = token
        # Ignore HTTP(S)_PROXY for localhost: even a misconfigured
        # environment must not send this capability token to a proxy.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def read(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self._token}"}, method="GET",
        )
        try:
            with self._opener.open(request, timeout=10) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            status = exc.getcode()
            if status == 401:
                raise ControlError("Graphlink rejected the control token (401). Restart the MCP adapter.") from exc
            # For expected bad-input/missing-resource responses, keep a
            # short actionable detail instead of reducing every error to
            # "HTTP 400". Never reflect 5xx bodies or credentials.
            detail = ""
            if status in (400, 404, 422):
                try:
                    body = json.loads(exc.read(2048))
                    message = body.get("detail") if isinstance(body, dict) else None
                    if isinstance(message, str):
                        detail = message[:300]
                except (ValueError, OSError, UnicodeDecodeError):
                    pass
            suffix = f": {detail}" if detail else ""
            raise ControlError(f"Graphlink control endpoint returned HTTP {status}{suffix}.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ControlError("Graphlink headless is unavailable on 127.0.0.1; start its backend first.") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ControlError("Graphlink result exceeds the read-only MCP size limit; request a smaller page.")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ControlError("Graphlink returned invalid JSON.") from exc
        if not isinstance(result, dict):
            raise ControlError("Graphlink returned an unexpected response shape.")
        return result


def _validate_args(name: str, arguments: Any) -> dict[str, Any]:
    spec = _BY_NAME.get(name)
    if spec is None:
        raise ValueError(f"unknown MCP tool: {name}")
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be a JSON object")
    schema = spec["inputSchema"]
    properties = schema["properties"]
    unknown = set(arguments) - set(properties)
    if unknown:
        raise ValueError("unknown arguments: " + ", ".join(sorted(unknown)))
    for key in schema.get("required", ()):
        if key not in arguments:
            raise ValueError(f"missing required argument: {key}")
    for key, value in arguments.items():
        spec_field = properties[key]
        if spec_field["type"] == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{key} must be an integer")
            if value < spec_field.get("minimum", value) or value > spec_field.get("maximum", value):
                raise ValueError(f"{key} is out of range")
        else:
            if not isinstance(value, str):
                raise ValueError(f"{key} must be a string")
            if len(value) < spec_field.get("minLength", 0) or len(value) > spec_field.get("maxLength", len(value)):
                raise ValueError(f"{key} has an invalid length")
            if "enum" in spec_field and value not in spec_field["enum"]:
                raise ValueError(f"{key} must be one of {', '.join(spec_field['enum'])}")
    return arguments


def _tool_target(name: str, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if name == "graphlink_list_workspaces":
        return "/api/control/workspaces", {}
    if name == "graphlink_list_graphs":
        return "/api/control/graphs", arguments
    if name == "graphlink_get_graph":
        return f"/api/control/graphs/{arguments['graph_id']}", {
            k: value for k, value in arguments.items() if k != "graph_id"
        }
    if name == "graphlink_list_nodes":
        return "/api/control/scene/nodes", arguments
    if name == "graphlink_read_subgraph":
        return "/api/control/scene/subgraph", arguments
    raise ValueError("unknown tool")


def _rpc_error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


class StdioMcpServer:
    def __init__(self, client: ControlHttpClient):
        self._client = client
        self._legacy_initialized = False

    def handle(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _rpc_error(None, -32600, "invalid JSON-RPC request")
        if "id" not in message:
            # JSON-RPC notifications never receive replies.
            return None
        request_id = message["id"]
        if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
            return _rpc_error(None, -32600, "invalid request id")
        method = message.get("method")
        params = message.get("params", {})
        if not isinstance(method, str) or not isinstance(params, dict):
            return _rpc_error(request_id, -32600, "invalid method or params")

        if method == "initialize":
            requested = params.get("protocolVersion")
            selected = (
                requested if isinstance(requested, str) and requested in LEGACY_VERSIONS
                else LATEST_LEGACY_VERSION
            )
            self._legacy_initialized = True
            return {"jsonrpc": "2.0", "id": request_id, "result": {
                "protocolVersion": selected, "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            }}

        meta = params.get("_meta")
        if isinstance(meta, dict) and "io.modelcontextprotocol/protocolVersion" in meta:
            modern = True
            version = meta["io.modelcontextprotocol/protocolVersion"]
            if version != MODERN_VERSION:
                return _rpc_error(
                    request_id, -32022, "Unsupported protocol version",
                    {"supported": [MODERN_VERSION], "requested": version},
                )
            if not isinstance(meta.get("io.modelcontextprotocol/clientCapabilities"), dict):
                return _rpc_error(request_id, -32602, "missing modern client capabilities")
        else:
            modern = False
            if not self._legacy_initialized:
                return _rpc_error(request_id, -32600, "initialize or server/discover first")

        if method == "server/discover":
            if not modern:
                return _rpc_error(request_id, -32601, "server/discover requires modern MCP metadata")
            result: dict[str, Any] = {
                "resultType": "complete", "supportedVersions": [MODERN_VERSION],
                "capabilities": {"tools": {}},
                "instructions": "Read-only Graphlink workspace, saved graph and live canvas inspection.",
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": list(TOOLS)}
        elif method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or name not in _BY_NAME:
                return _rpc_error(request_id, -32602, "unknown MCP tool")
            try:
                arguments = _validate_args(name, params.get("arguments", {}))
            except ValueError as exc:
                return _rpc_error(request_id, -32602, str(exc))
            try:
                path, query = _tool_target(name, arguments)
                payload = self._client.read(path, query)
                result = {
                    "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=True)}],
                    "structuredContent": payload, "isError": False,
                }
            except ControlError as exc:
                result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        else:
            return _rpc_error(request_id, -32601, "method not found")

        if modern:
            result["resultType"] = "complete"
            result["_meta"] = {"io.modelcontextprotocol/serverInfo": SERVER_INFO}
        return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(client: ControlHttpClient, source: TextIO, sink: TextIO) -> None:
    server = StdioMcpServer(client)
    while True:
        raw = source.readline(MAX_INPUT_LINE + 1)
        if not raw:
            return
        reply: dict[str, Any] | None
        if len(raw) > MAX_INPUT_LINE:
            # Even a newline-terminated line just ONE byte beyond the
            # input cap must be refused. Resynchronize when a longer line
            # exceeded readline's bounded chunk.
            while raw and not raw.endswith("\n"):
                raw = source.readline(MAX_INPUT_LINE + 1)
            reply = _rpc_error(None, -32700, "MCP message too large")
        else:
            try:
                message = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                reply = _rpc_error(None, -32700, "invalid JSON")
            else:
                reply = server.handle(message)
        if reply is not None:
            sink.write(json.dumps(reply, ensure_ascii=True, separators=(",", ":")) + "\n")
            sink.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Graphlink read-only MCP stdio adapter")
    parser.add_argument("--url", default=f"http://127.0.0.1:{DEFAULT_CONTROL_PORT}")
    parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_FILE)
    options = parser.parse_args(argv)
    try:
        token = options.token_file.expanduser().read_text(encoding="utf-8").strip()
        client = ControlHttpClient(options.url, token)
    except (OSError, ValueError) as exc:
        parser.error(f"cannot configure Graphlink MCP: {exc}")
    serve(client, sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
