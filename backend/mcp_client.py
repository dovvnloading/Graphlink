"""Bounded MCP clients and Builder registration.

Stdio retains the established 2024 handshake. Streamable HTTP negotiates
the 2025 protocol family in backend.mcp_http. Both expose the same tool
metadata and structured results, while retaining ToolSpec/ToolResult
compatibility for Builder. Servers remain untrusted and approval-gated.
"""

from __future__ import annotations

import asyncio
import collections
import json
import math
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from backend.providers.base import ToolCall, ToolSpec
from backend.tools import RunContext, ToolRegistry, ToolResult
from graphlink_process_env import safe_subprocess_env

# The MCP spec's own date-versioned protocol string - the most recent
# stable revision at the time this client was written. Sent verbatim in
# the initialize handshake; a server on a different revision negotiates
# its own version back in the response, which this client does not
# currently need to branch on (the tools/list + tools/call shapes this
# client actually uses have been stable across every MCP revision to date).
MCP_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_NAME = "graphlink"

# SECURITY-FIX: an MCP tool result is returned to the Builder uncapped -
# unlike every built-in graph tool (backend/tools_graph.py's own excerpting,
# e.g. read_subgraph's nodes_truncated flag), a server's response text was
# handed straight through, whatever its size. The server is untrusted (this
# module's own docstring already treats it as such for env/approval), so an
# oversized text block round-trips intact into the Builder's message list
# and is re-sent to the provider on every subsequent turn - a cheap way for
# a hostile or misbehaving server to blow the model's context/cost budget
# with a single call. 200_000 chars comfortably covers a real tool answer
# (a file read, a search result page) while bounding the pathological case.
MAX_TOOL_RESULT_CHARS = 200_000

# SECURITY-FIX: `for line in process.stdout` (below, in _read_loop) - plain
# text-mode line iteration - accumulates an ENTIRE line in memory before
# ever yielding it, with no upper bound. A hostile or simply broken server
# that writes to stdout without ever emitting a newline grows that buffer
# without limit; Python eventually raises MemoryError on the reader daemon
# thread, which threading.excepthook reports through the crash logger,
# taking the desktop app down. 8MB comfortably covers any real single
# JSON-RPC message (tools/list, a tool result before its own
# MAX_TOOL_RESULT_CHARS cap applies) while bounding the pathological case
# to a fixed, small amount of memory per read.
_MAX_STDOUT_LINE_CHARS = 8_000_000
# How many additional bounded reads _read_loop will perform to resync past
# a single oversized/never-terminated line before giving up and treating
# the connection as unrecoverably desynced. Each read is itself capped at
# _MAX_STDOUT_LINE_CHARS, so this bounds total memory for the discard, not
# just each individual chunk.
_MAX_OVERSIZED_LINE_RESYNC_READS = 4

# SECURITY-FIX: _drain_stderr's ring buffer bounds the NUMBER of retained
# lines (200) but not the LENGTH of any one of them - `for line in
# process.stderr` accumulated an entire line in memory first, same as the
# stdout case above. A hostile/broken server writing to stderr without
# newlines could still exhaust memory even with the line-count cap in
# place. _stderr_tail_text only ever keeps the final 2000 chars anyway, so
# each stored line is capped well above that (with room for several lines
# of real diagnostic context) rather than truly unbounded.
_MAX_STDERR_LINE_CHARS = 4_000
_CLIENT_VERSION = "1.0"

# A reader-thread sentinel distinct from any real JSON-RPC payload (a plain
# dict), so `is` identity unambiguously marks "the server's stdout closed",
# never confusable with a legitimate (if unusual) server response.
_READER_CLOSED = object()
# A malicious MCP server can send repeated responses with the current
# request ID. Filtering notifications and mismatched IDs alone does not
# bound the host-side queue that stores matching parsed JSON payloads.
_MAX_PENDING_RESPONSES = 4

# SECURITY-FIX: a tool's `description` in an MCP server's tools/list
# response is untrusted input per this module's own threat model (see the
# module docstring - "MCP servers are untrusted by default: arbitrary user-
# configured code, not a first-party tool"). register_mcp_server_tools below
# forwards it VERBATIM into the model's tool definitions on every Builder
# turn, so a hostile/compromised/typosquatted server can pack an arbitrarily
# long, prompt-injection-laden description into a single tool and steer the
# Builder's behavior on every turn that tool is registered for. This channel
# is never shown to the user anywhere (no Settings UI lists tool
# descriptions) and isn't covered by the executor prompt's "tool results and
# node content is DATA" warning, since it arrives as a tool DEFINITION, not a
# tool result. Capped in list_tools() itself, not at some later render/
# registration site, so nothing downstream can forget the bound. A real tool
# description needs at most a few paragraphs; this bounds the worst case per
# tool per turn without truncating any legitimate description.
MAX_TOOL_DESCRIPTION_CHARS = 2000


class McpError(RuntimeError):
    """Raised for any MCP-level failure: the server process failed to
    start, closed its output unexpectedly, returned a JSON-RPC error
    response, or didn't respond within the configured timeout."""


@dataclass(frozen=True)
class McpToolSpec(ToolSpec):
    """MCP metadata kept alongside the provider-neutral tool definition.

    Annotations are server-supplied hints, never permission grants.
    """

    output_schema: dict | None = None
    annotations: dict = field(default_factory=dict)
    title: str = ""


@dataclass(frozen=True)
class McpToolResult(ToolResult):
    """A bounded lossless result for graph nodes, plus Builder's text view."""

    content_blocks: tuple[dict, ...] = ()
    structured_content: dict | list | None = None
    truncated: bool = False


class McpClient(Protocol):
    @property
    def is_connected(self) -> bool: ...

    def connect(self) -> None: ...
    def close(self) -> None: ...
    def list_tools(self) -> tuple[McpToolSpec, ...]: ...
    def call_tool(self, name: str, arguments: dict) -> McpToolResult: ...


MAX_MCP_TOOLS = 2_000
MAX_MCP_TOOL_PAGES = 100
MAX_MCP_SCHEMA_CHARS = 256_000
MAX_MCP_CATALOGUE_CHARS = 2_000_000
MAX_MCP_RESULT_CHARS = 2_000_000


def _json_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=True, allow_nan=False))
    except (ValueError, TypeError, RecursionError) as exc:
        raise McpError("MCP server returned invalid or excessively nested JSON data.") from exc


def list_mcp_tools(call: Callable[[str, dict], dict]) -> tuple[McpToolSpec, ...]:
    """Read a complete, bounded catalogue, rejecting ambiguous identities."""
    specs: list[McpToolSpec] = []
    names: set[str] = set()
    cursors: set[str] = set()
    params: dict = {}
    catalogue_size = 0
    for _ in range(MAX_MCP_TOOL_PAGES):
        response = call("tools/list", params)
        raw_tools = response.get("tools")
        if not isinstance(raw_tools, list):
            raw_tools = []
        for tool in raw_tools:
            if not isinstance(tool, dict):
                continue
            name = tool.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            if name in names:
                raise McpError("MCP server advertised a duplicate tool name. Refresh after fixing the server.")
            if len(name) > 256 or any(ord(char) < 32 for char in name):
                continue
            names.add(name)
            description = str(tool.get("description") or "")
            if len(description) > MAX_TOOL_DESCRIPTION_CHARS:
                omitted = len(description) - MAX_TOOL_DESCRIPTION_CHARS
                description = description[:MAX_TOOL_DESCRIPTION_CHARS] + f"...[truncated, {omitted} more characters omitted]"
            input_schema = tool.get("inputSchema")
            if not isinstance(input_schema, dict):
                input_schema = {"type": "object"}
            output_schema = tool.get("outputSchema")
            if not isinstance(output_schema, dict):
                output_schema = None
            schema_size = _json_size([input_schema, output_schema])
            if schema_size > MAX_MCP_SCHEMA_CHARS:
                raise McpError("MCP tool schema is too large. Reduce the server's schema and refresh.")
            catalogue_size += schema_size + len(description) + len(name)
            if catalogue_size > MAX_MCP_CATALOGUE_CHARS:
                raise McpError("MCP tool catalogue is too large. Reduce it and refresh.")
            raw_annotations = tool.get("annotations")
            annotations = {
                key: value for key, value in (raw_annotations.items() if isinstance(raw_annotations, dict) else ())
                if key in {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}
                and isinstance(value, bool)
            }
            title = str(tool.get("title") or "")[:256]
            specs.append(McpToolSpec(
                name=name, description=description, input_schema=input_schema,
                output_schema=output_schema, annotations=annotations, title=title,
            ))
            if len(specs) > MAX_MCP_TOOLS:
                raise McpError("MCP server advertised too many tools. Reduce the catalogue and refresh.")
        cursor = response.get("nextCursor")
        if not cursor:
            return tuple(specs)
        if not isinstance(cursor, str) or len(cursor) > 4_096 or cursor in cursors:
            raise McpError("MCP server returned an invalid or repeated catalogue cursor.")
        cursors.add(cursor)
        params = {"cursor": cursor}
    raise McpError("MCP tool catalogue exceeded its page limit.")


def parse_mcp_result(response: dict) -> McpToolResult:
    """Retain structured data even when a tool also supplies text blocks.

    Large payloads are visibly truncated and lose their structured binding
    value, rather than quietly persisting incomplete JSON as a full result.
    """
    if response.get("resultType") == "input_required":
        raise McpError("This MCP tool requires an interactive continuation that this client does not support.")
    content = response.get("content", [])
    if not isinstance(content, list):
        raise McpError("tools/call returned an invalid content list")
    blocks = tuple(block for block in content if isinstance(block, dict))
    structured = response.get("structuredContent")
    if not isinstance(structured, (dict, list)):
        structured = None
    text_parts = [str(block.get("text", "")) for block in blocks if block.get("type") == "text"]
    result_text = "\n".join(text_parts)
    result_size = _json_size([blocks, structured])
    if not text_parts and structured is not None:
        result_text = json.dumps(structured, ensure_ascii=True)
    truncated = len(result_text) > MAX_TOOL_RESULT_CHARS
    if truncated:
        result_text = (
            result_text[:MAX_TOOL_RESULT_CHARS]
            + f"\n...[truncated, {len(result_text) - MAX_TOOL_RESULT_CHARS} more characters omitted]"
        )
    if result_size > MAX_MCP_RESULT_CHARS:
        truncated = True
        structured = None
        result_text += "\n[Structured data and content blocks omitted: result exceeds the storage limit.]"
        blocks = ({"type": "text", "text": result_text},)
    return McpToolResult(
        content=result_text, is_error=bool(response.get("isError", False)),
        content_blocks=blocks, structured_content=structured, truncated=truncated,
    )


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
    command: str = ""
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
    env: dict[str, str] = field(default_factory=dict, repr=False)
    id: str = ""
    transport: str = "stdio"
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict, repr=False)

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
            "id": self.id,
            "transport": self.transport,
            "url": self.url,
            "headers": dict(self.headers),
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
            id=str(data.get("id") or "").strip(),
            transport=str(data.get("transport") or "stdio").strip(),
            url=str(data.get("url") or "").strip(),
            headers=_coerce_env(data.get("headers")),
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


def create_mcp_client(config: McpServerConfig) -> McpClient:
    """Create a disconnected client; credentials never enter a node receipt."""
    if not math.isfinite(config.timeout) or not 1 <= config.timeout <= 300:
        raise McpError("MCP timeout must be between 1 and 300 seconds.")
    if not config.enabled:
        raise McpError("This MCP server is disabled. Enable it in Settings before connecting.")
    if config.transport == "streamable_http":
        from backend.mcp_http import McpHttpClient

        return McpHttpClient(url=config.url, headers=config.headers, timeout=config.timeout)
    if config.transport != "stdio":
        raise McpError("Unsupported MCP transport. Choose stdio or Streamable HTTP.")
    if not config.command:
        raise McpError("Set the MCP server command in Settings before connecting.")
    return McpStdioClient(command=config.command, args=config.args, env=config.env, timeout=config.timeout)


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
        self._stderr_thread: threading.Thread | None = None
        # A bounded tail of the server's stderr, drained continuously by its
        # own thread. Without a dedicated drainer, a server that writes more
        # than the OS pipe buffer (~4 KiB on Windows, measured) to stderr
        # before we read it BLOCKS on its own stderr write - and since we
        # never read stderr except after stdout already closed, its stdout
        # response never arrives and _call hangs until the timeout on EVERY
        # request. A ring buffer keeps the diagnostic tail close() and the
        # unexpected-close path want, without unbounded memory growth.
        self._stderr_tail: "collections.deque[str]" = collections.deque(maxlen=200)
        self._responses: "queue.Queue[Any]" = queue.Queue(maxsize=_MAX_PENDING_RESPONSES)
        self._next_id = 0
        # REVIEW-FIX: the id of the ONE request _call is currently waiting
        # on (None when idle) - see _read_loop's own comment for why the
        # reader thread checks this before queuing anything. Set/cleared
        # only from within _call, always under _call_lock, matching this
        # client's one-in-flight-request-at-a-time contract (see this
        # class's own docstring).
        self._pending_id: int | None = None
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
        # Drain stderr continuously (see _stderr_tail's own comment): a
        # server that fills the stderr pipe buffer would otherwise block on
        # its own stderr write and never send its stdout response.
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

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
        try:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            for thread in (self._reader_thread, self._stderr_thread):
                if thread is not None and thread is not threading.current_thread():
                    thread.join(timeout=1)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

    def list_tools(self) -> tuple[McpToolSpec, ...]:
        return list_mcp_tools(self._call)

    def call_tool(self, name: str, arguments: dict) -> McpToolResult:
        response = self._call("tools/call", {"name": name, "arguments": dict(arguments or {})})
        return parse_mcp_result(response)

    # -- JSON-RPC framing ------------------------------------------------

    @staticmethod
    def _drain_oversized_line(stdout) -> bool:
        """Consumes the remainder of a single line that already exceeded
        _MAX_STDOUT_LINE_CHARS, in further _MAX_STDOUT_LINE_CHARS-bounded
        reads, until the real trailing newline (or EOF) is found - so
        _read_loop's own next readline() starts at a genuine line boundary
        again instead of misinterpreting the tail of a too-long line as a
        fresh one. Returns True once resynced, False if the line is still
        not terminated after _MAX_OVERSIZED_LINE_RESYNC_READS more reads
        (a server that just never stops writing) - the caller treats that
        as unrecoverable and stops reading."""
        for _ in range(_MAX_OVERSIZED_LINE_RESYNC_READS):
            chunk = stdout.readline(_MAX_STDOUT_LINE_CHARS)
            if chunk == "" or chunk.endswith("\n"):
                return True
        return False

    def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            # Raised, not asserted: `python -O` strips an assert, and the
            # AttributeError that follows on a None pipe is a far worse
            # message than this - it surfaces deep inside a reader thread
            # with no hint that the process simply was not connected.
            raise RuntimeError("read loop started before the process was connected")
        try:
            while True:
                # SECURITY-FIX: readline(size) bounds a single read to
                # _MAX_STDOUT_LINE_CHARS instead of the unbounded `for line
                # in process.stdout` this replaced - see that constant's own
                # doc. Returns "" only at real EOF; a chunk that hits the
                # cap with no trailing "\n" means the real line is longer
                # than the cap, so the rest of it is discarded (bounded, via
                # _drain_oversized_line) rather than ever held in memory.
                raw_line = process.stdout.readline(_MAX_STDOUT_LINE_CHARS)
                if raw_line == "":
                    break  # EOF
                if len(raw_line) >= _MAX_STDOUT_LINE_CHARS and not raw_line.endswith("\n"):
                    if not self._drain_oversized_line(process.stdout):
                        break  # could not resync within the read budget - give up
                    continue
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue  # server-side logging on stdout, not JSON-RPC - skip
                # REVIEW-FIX: only queue a payload that answers the
                # CURRENTLY in-flight request. Anything else - a legal MCP
                # notification (no "id" at all; a server may send
                # notifications/progress, notifications/message, etc. at
                # any time, including while otherwise idle) or a stray/late
                # response nothing is waiting on - used to be put() here
                # unconditionally. Nothing ever drains self._responses
                # between calls (the client sits idle/connected for most of
                # a session), so a chatty/idle-notifying server grew this
                # queue without bound for the client's whole lifetime.
                # Dropping the non-matching payload here, rather than
                # queuing then skipping past it in _call, is what actually
                # bounds the queue.
                pending_id = self._pending_id
                if (pending_id is not None and isinstance(payload, dict)
                        and type(payload.get("id")) is int and payload["id"] == pending_id):
                    # bool is a subclass of int in Python, but JSON-RPC
                    # prohibits boolean ids. True must not satisfy id=1.
                    try:
                        self._responses.put_nowait(payload)
                    except queue.Full:
                        # Keep draining the pipe so the server cannot wedge
                        # on stdout backpressure; discard excess replies
                        # rather than growing the host's parsed-object queue.
                        pass
        finally:
            # Never block the reader on a full queue when the subprocess
            # exits. Preserve an EOF signal even after a response flood.
            try:
                self._responses.put_nowait(_READER_CLOSED)
            except queue.Full:
                try:
                    self._responses.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._responses.put_nowait(_READER_CLOSED)
                except queue.Full:
                    pass

    def _drain_stderr(self) -> None:
        """Continuously read the server's stderr into a bounded ring buffer.
        Its whole job is to keep the stderr pipe from filling and blocking
        the server's own writes - see _stderr_tail's own comment. Runs for
        the life of the process; ends when stderr reaches EOF."""
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            while True:
                line = process.stderr.readline(_MAX_STDERR_LINE_CHARS)
                if line == "":
                    break  # EOF
                self._stderr_tail.append(line)
        except (ValueError, OSError):
            # stderr closed out from under us (close() race) - nothing left
            # to drain.
            pass

    def _stderr_tail_text(self, limit: int = 2000) -> str:
        """The most recent stderr output, newest-bounded to `limit` chars -
        for the unexpected-close diagnostic. Reads the ring buffer the
        drainer thread fills, never the raw pipe (which the drainer owns)."""
        return "".join(self._stderr_tail)[-limit:]

    def _write(self, message: dict) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise McpError(f"MCP server {self.command!r} is not connected.")
        payload = json.dumps(message) + "\n"
        with self._write_lock:
            # REVIEW-FIX: stdin.write()/flush() are plain blocking file
            # calls - Python gives no async/select-based way to bound them.
            # A subprocess that stops draining its stdin (busy, wedged, or
            # just an ordinarily-sized argument that fills the OS pipe
            # buffer, a few KB on Windows) used to block this forever, and
            # since _call() holds _call_lock for its whole body, every
            # future call on this client deadlocked too - with no way to
            # interrupt it (asyncio.to_thread cancellation doesn't touch the
            # underlying blocked OS thread). The write is offloaded to a
            # daemon helper thread and bounded with a join() timeout instead
            # - mirrors _call's own read-side timeout against self.timeout.
            # If it doesn't finish in time we raise rather than wait
            # forever; the blocked syscall (and its thread) leaks, but a
            # leaked thread beats a permanently deadlocked client.
            outcome: list[BaseException] = []
            # Bound to a local because the guard above narrows process.stdin
            # only for this frame - a nested function could in principle run
            # after the attribute changed, so the narrowing does not reach
            # inside the closure. The stream itself is the same object.
            stdin = process.stdin

            def _blocking_write() -> None:
                try:
                    stdin.write(payload)
                    stdin.flush()
                except Exception as exc:  # re-raised on the caller's thread below
                    outcome.append(exc)

            writer = threading.Thread(target=_blocking_write, daemon=True)
            writer.start()
            writer.join(self.timeout)
            if writer.is_alive():
                raise McpError(
                    f"MCP server {self.command!r} timed out after {self.timeout}s writing a "
                    f"{message.get('method')!r} request - the process is not reading its stdin."
                )
            if outcome:
                exc = outcome[0]
                if isinstance(exc, (BrokenPipeError, ValueError)):
                    raise McpError(f"MCP server {self.command!r} is not accepting input: {exc}") from exc
                raise exc

    def _notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _call(self, method: str, params: dict) -> dict:
        with self._call_lock:
            self._next_id += 1
            request_id = self._next_id
            # REVIEW-FIX: recorded so _read_loop can tell "the response to
            # THIS call" apart from an unsolicited notification or a stray
            # response nothing is waiting on - see that method's own
            # comment. Cleared in `finally` no matter how the call ends
            # (result, error, or timeout) so a late response that arrives
            # after we've already given up is dropped by the reader too,
            # not left for whatever happens to read the queue next.
            self._pending_id = request_id
            try:
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
                        stderr_tail = self._stderr_tail_text()
                        raise McpError(
                            f"MCP server {self.command!r} closed its output unexpectedly while calling {method!r}."
                            + (f" stderr: {stderr_tail}" if stderr_tail.strip() else "")
                        )
                    if not isinstance(payload, dict) or payload.get("id") != request_id:
                        # Defense in depth: _read_loop now only ever queues
                        # a payload matching self._pending_id (== request_id
                        # for this call's whole duration), so this should be
                        # unreachable - kept as a safety net rather than an
                        # invariant this method silently trusts.
                        continue
                    if "error" in payload:
                        error = payload["error"]
                        message_text = error.get("message", error) if isinstance(error, dict) else error
                        # SECURITY-FIX: message_text comes straight from the
                        # (untrusted) server with no length bound - a hostile
                        # or misbehaving server's error.message could be
                        # arbitrarily large, and this exception's text flows
                        # into agents.py's own logger.warning(...) call and
                        # into user-facing notification text, unbounded.
                        # Same 2000-char bound this file already uses for
                        # the stderr-tail diagnostic just above.
                        message_text = str(message_text)
                        if len(message_text) > 2000:
                            message_text = message_text[:2000] + "…[truncated]"
                        raise McpError(f"{method} failed: {message_text}")
                    result = payload.get("result", {})
                    if result is None:
                        return {}
                    if not isinstance(result, dict):
                        # A malformed third-party MCP server can return a
                        # JSON scalar/array. Callers expect a result object;
                        # make it a recoverable McpError, not an AttributeError.
                        raise McpError(f"MCP server returned a non-object result for {method!r}")
                    return result
            finally:
                self._pending_id = None


def register_mcp_server_tools(
    registry: ToolRegistry, client: McpClient, config: McpServerConfig,
) -> tuple[str, ...]:
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


def _make_mcp_handler(client: McpClient, real_tool_name: str) -> Callable[[Any, "RunContext"], Awaitable[ToolResult]]:
    """One handler per real (un-namespaced) tool name - a closure rather
    than a shared handler keying off call.name, since call.name for an
    MCP-backed registration is the NAMESPACED name (mcp:<server>:<tool>),
    which McpStdioClient.call_tool must never see (the server itself only
    knows its own un-namespaced tool names)."""

    async def _handler(call: ToolCall, ctx: RunContext) -> ToolResult:
        return await asyncio.to_thread(client.call_tool, real_tool_name, call.arguments)

    return _handler

