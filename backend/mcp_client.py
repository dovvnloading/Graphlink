"""ADR-007 stage 7.5: a minimal, hand-rolled MCP (Model Context Protocol)
CLIENT - stdio transport, JSON-RPC 2.0, newline-delimited framing (the
transport MCP servers overwhelmingly ship as - a filesystem/git/search MCP
server is a local subprocess, not a network service). Hand-rolled rather
than depending on the official `mcp` package for the same reason
graphlink_wire_schema.py hand-rolls its own dataclass->JSON-Schema
generator instead of pydantic (see that module's own docstring): the
protocol surface this app actually needs - initialize, tools/list,
tools/call - is small and closed, and a new runtime dependency is not a
decision to make lightly in a codebase whose own ADR-005 stage 5.5 exists
specifically because of a hostile-sdist supply-chain finding. HTTP
transport (the ADR's own "stdio/HTTP per the MCP spec" line) is deferred:
every commonly-used MCP server today (filesystem, git, search) ships as a
stdio subprocess, and adding a second transport with no server to
exercise it against would be speculative surface, not a tested capability.

NOT an MCP *server* (exposing GraphLink's own tools outward) - explicitly
out of this ADR's scope (see its own "Decision" section, item 4).

This module owns exactly two things:
1. McpStdioClient - the JSON-RPC session with one running MCP server
   subprocess (connect/list_tools/call_tool/close).
2. register_mcp_server_tools - the glue that lists a connected client's
   tools and registers each into a backend.tools.ToolRegistry, namespaced
   `mcp:<server>:<tool>` (per the ADR's own decision), scope-mapped, and
   approval-gated - MCP servers are untrusted by default (arbitrary user-
   configured code, not a first-party tool), so approval defaults to
   "always" unless the caller explicitly relaxes it.

Where a configured server's settings actually LIVE (SettingsManager, see
graphlink_settings_store.py's own get_mcp_servers/set_mcp_servers) and the
Settings UI panel to edit them are separate concerns: the ADR's own
"Consequences" section defers the UI surface to ADR-012 ("Settings gain an
MCP configuration surface"). This stage's exit criterion - "a configured
filesystem MCP server's tool is callable, namespaced, approval-gated" - is
satisfied by the client + registry glue + the persisted config SHAPE, not
by a finished settings panel.
"""

from __future__ import annotations

import asyncio
import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from backend.providers.base import ToolSpec
from backend.tools import RunContext, ToolResult
from graphlink_process_env import safe_subprocess_env

# The MCP spec's own date-versioned protocol string - the most recent
# stable revision at the time this client was written. Sent verbatim in
# the initialize handshake; a server on a different revision negotiates
# its own version back in the response, which this client does not
# currently need to branch on (the tools/list + tools/call shapes this
# client actually uses have been stable across every MCP revision to date).
MCP_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_NAME = "graphlink"
_CLIENT_VERSION = "1.0"

# A reader-thread sentinel distinct from any real JSON-RPC payload (a plain
# dict), so `is` identity unambiguously marks "the server's stdout closed",
# never confusable with a legitimate (if unusual) server response.
_READER_CLOSED = object()


class McpError(RuntimeError):
    """Raised for any MCP-level failure: the server process failed to
    start, closed its output unexpectedly, returned a JSON-RPC error
    response, or didn't respond within the configured timeout."""


@dataclass(frozen=True)
class McpServerConfig:
    """One configured MCP server - the persisted shape SettingsManager
    stores (graphlink_settings_store.py's own get_mcp_servers/
    set_mcp_servers, as plain JSON-safe dicts via to_dict/from_dict below -
    that module stays agnostic of this one's types, matching how every
    other settings getter there returns a plain dict/primitive, never a
    domain object from another module) and the shape a caller passes to
    register_mcp_server_tools below. `scopes`/`approval` are the SAME
    ToolRegistry vocabulary stage 7.2 already defines (backend/tools.py) -
    granted once per server, applied to every tool that server advertises,
    matching the ADR's own "granted scopes" per-server (not per-tool)
    framing. `enabled_tools`, when non-empty, is an allow-list of raw
    (un-namespaced) tool names - a server advertising a tool the user never
    opted into is silently skipped, not registered read-only-by-default."""

    name: str
    command: str
    args: tuple[str, ...] = ()
    scopes: frozenset[str] = frozenset()
    approval: str = "always"
    enabled_tools: frozenset[str] = frozenset()
    enabled: bool = True
    timeout: float = 30.0
    # Extra environment variables for THIS server's process, layered on top
    # of the safe allowlist base (graphlink_process_env.safe_subprocess_env)
    # - the ONLY way a server receives anything beyond that base. A GitHub
    # MCP server needs GITHUB_TOKEN, a Brave one BRAVE_API_KEY; the user
    # names exactly the variable that server needs here, and nothing else
    # crosses. Before this field existed the spawn inherited the backend's
    # whole environment, so every such server silently received every
    # provider key the user had configured as an env var - the exact leak
    # safe_subprocess_env was written to close at every other spawn site.
    env: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "command": self.command,
            "args": list(self.args),
            "scopes": sorted(self.scopes),
            "approval": self.approval,
            "enabled_tools": sorted(self.enabled_tools),
            "enabled": self.enabled,
            "timeout": self.timeout,
            "env": dict(self.env),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "McpServerConfig":
        return cls(
            name=str(data.get("name", "")).strip(),
            command=str(data.get("command", "")).strip(),
            args=tuple(str(a) for a in (data.get("args") or [])),
            scopes=frozenset(str(s) for s in (data.get("scopes") or [])),
            approval=str(data.get("approval") or "always"),
            enabled_tools=frozenset(str(t) for t in (data.get("enabled_tools") or [])),
            enabled=bool(data.get("enabled", True)),
            timeout=float(data.get("timeout", 30.0) or 30.0),
            env=_coerce_env(data.get("env")),
        )


def _coerce_env(raw: object) -> dict[str, str]:
    """A persisted/wire `env` value as a clean str->str dict. Tolerant of
    the shapes a hand-edited session.dat or an older payload can carry
    (missing, null, non-dict, non-string values) - a malformed env entry
    degrades to "no extra variables", never to a failed config load that
    would take the whole server list with it."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        name = str(key).strip()
        if not name or value is None:
            continue
        out[name] = str(value)
    return out


class McpStdioClient:
    """One running MCP server subprocess, spoken to over stdio JSON-RPC 2.0.
    One instance per configured server - construct, connect() once, then
    list_tools()/call_tool() as needed, close() when done. Not reentrant
    across concurrent callers beyond the one in-flight-request-at-a-time
    serialization _call already provides internally."""

    def __init__(self, *, command: str, args: tuple[str, ...] = (), env: dict[str, str] | None = None,
                 cwd: str | None = None, timeout: float = 30.0):
        self.command = command
        self.args = tuple(args)
        self.env = env
        self.cwd = cwd
        self.timeout = timeout
        self._process: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._responses: "queue.Queue[Any]" = queue.Queue()
        self._next_id = 0
        self._write_lock = threading.Lock()
        self._call_lock = threading.Lock()

    @property
    def is_connected(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def connect(self) -> None:
        if self._process is not None:
            return
        try:
            # env= is ALWAYS passed and ALWAYS built from the allowlist base:
            # Popen(env=None) inherits the backend's full os.environ, which
            # carries every provider API key the user configured as an
            # environment variable. An MCP server is third-party code the
            # user chose to run - it gets PATH/TEMP/HOME-class plumbing plus
            # exactly the variables its own config names, nothing else. Same
            # posture as pycoder/code_sandbox/plugin_sdk's spawns.
            spawn_env = safe_subprocess_env()
            spawn_env.update(self.env or {})
            self._process = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=spawn_env,
                cwd=self.cwd,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise McpError(f"Failed to start MCP server {self.command!r}: {exc}") from exc

        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

        try:
            response = self._call("initialize", {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": _CLIENT_NAME, "version": _CLIENT_VERSION},
            })
        except McpError:
            self.close()
            raise
        self._notify("notifications/initialized", {})
        self.server_info = response.get("serverInfo") if isinstance(response, dict) else None

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
        except Exception:
            pass
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    def list_tools(self) -> tuple[ToolSpec, ...]:
        response = self._call("tools/list", {})
        tools = response.get("tools", []) if isinstance(response, dict) else []
        return tuple(
            ToolSpec(
                name=str(tool["name"]),
                description=str(tool.get("description") or ""),
                input_schema=tool.get("inputSchema") or {"type": "object"},
            )
            for tool in tools
        )

    def call_tool(self, name: str, arguments: dict) -> ToolResult:
        response = self._call("tools/call", {"name": name, "arguments": dict(arguments or {})})
        content = response.get("content", []) if isinstance(response, dict) else []
        # MCP's own content-block shape (a list of {"type": "text", "text":
        # ...} - the ADR's own ToolSpec docstring already documents the
        # OpenAPI-schema caveat for the request side; this is the mirror
        # concession on the response side) - only text blocks are supported
        # today, matching every other ToolResult in this codebase being a
        # plain string; a non-text block (image/resource) is skipped rather
        # than crashing the call, since a partial answer beats a hard failure.
        text_parts = [
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return ToolResult(content="\n".join(text_parts), is_error=bool(response.get("isError", False)))

    # -- JSON-RPC framing ------------------------------------------------

    def _read_loop(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        try:
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue  # server-side logging on stdout, not JSON-RPC - skip
                self._responses.put(payload)
        finally:
            self._responses.put(_READER_CLOSED)

    def _write(self, message: dict) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise McpError(f"MCP server {self.command!r} is not connected.")
        with self._write_lock:
            try:
                process.stdin.write(json.dumps(message) + "\n")
                process.stdin.flush()
            except (BrokenPipeError, ValueError) as exc:
                raise McpError(f"MCP server {self.command!r} is not accepting input: {exc}") from exc

    def _notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _call(self, method: str, params: dict) -> dict:
        with self._call_lock:
            self._next_id += 1
            request_id = self._next_id
            self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            deadline = time.monotonic() + self.timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise McpError(f"MCP server {self.command!r} timed out after {self.timeout}s calling {method!r}.")
                try:
                    payload = self._responses.get(timeout=remaining)
                except queue.Empty:
                    raise McpError(f"MCP server {self.command!r} timed out after {self.timeout}s calling {method!r}.")
                if payload is _READER_CLOSED:
                    stderr_tail = ""
                    if self._process is not None and self._process.stderr is not None:
                        stderr_tail = self._process.stderr.read(2000)
                    raise McpError(
                        f"MCP server {self.command!r} closed its output unexpectedly while calling {method!r}."
                        + (f" stderr: {stderr_tail}" if stderr_tail.strip() else "")
                    )
                if not isinstance(payload, dict) or payload.get("id") != request_id:
                    # A notification, or a response to a different in-flight
                    # id - can't happen from THIS client (one call at a time
                    # under _call_lock), but a server sending its own
                    # unsolicited notification is legal MCP and must not be
                    # mistaken for our response.
                    continue
                if "error" in payload:
                    error = payload["error"]
                    message_text = error.get("message", error) if isinstance(error, dict) else error
                    raise McpError(f"{method} failed: {message_text}")
                return payload.get("result", {}) or {}


def register_mcp_server_tools(registry, client: McpStdioClient, config: McpServerConfig) -> tuple[str, ...]:
    """Lists `client`'s tools and registers each into `registry` (a
    backend.tools.ToolRegistry) as `mcp:<config.name>:<tool name>`, scoped
    and approval-gated per `config` - see McpServerConfig's own docstring.
    Returns the namespaced names actually registered (after the
    enabled_tools allow-list filter), in server-advertised order."""
    registered: list[str] = []
    for spec in client.list_tools():
        if config.enabled_tools and spec.name not in config.enabled_tools:
            continue
        namespaced_name = f"mcp:{config.name}:{spec.name}"
        namespaced_spec = ToolSpec(
            name=namespaced_name, description=spec.description, input_schema=spec.input_schema
        )
        registry.register(
            namespaced_spec,
            _make_mcp_handler(client, spec.name),
            scopes=config.scopes,
            approval=config.approval,
        )
        registered.append(namespaced_name)
    return tuple(registered)


def _make_mcp_handler(client: McpStdioClient, real_tool_name: str) -> Callable[[Any, "RunContext"], Awaitable[ToolResult]]:
    """One handler per real (un-namespaced) tool name - a closure rather
    than a shared handler keying off call.name, since call.name for an
    MCP-backed registration is the NAMESPACED name (mcp:<server>:<tool>),
    which McpStdioClient.call_tool must never see (the server itself only
    knows its own un-namespaced tool names)."""

    async def _handler(call, ctx) -> ToolResult:
        return await asyncio.to_thread(client.call_tool, real_tool_name, call.arguments)

    return _handler

