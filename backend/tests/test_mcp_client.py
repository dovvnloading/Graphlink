"""ADR-007 stage 7.5: MCP client - stdio transport, JSON-RPC 2.0.

Exit criterion this file proves: "A configured filesystem MCP server's tool
is callable, namespaced, approval-gated." test_a_configured_filesystem_mcp_
servers_tool_is_callable_namespaced_and_approval_gated below spawns a REAL
subprocess (a minimal fake filesystem-shaped MCP server script, not a mock
of subprocess.Popen) speaking real JSON-RPC over real pipes, and drives it
through register_mcp_server_tools -> ToolRegistry.invoke end to end.
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
import time

import pytest

from backend.mcp_client import McpError, McpServerConfig, McpStdioClient, register_mcp_server_tools
from backend.providers import ToolCall
from backend.tools import RunContext, ToolRegistry

# A minimal fake MCP server, shaped like a real filesystem MCP server (one
# "read_file" tool) - just enough JSON-RPC 2.0 over stdio to prove this
# client speaks the real wire protocol against a real subprocess, not a
# mock of subprocess.Popen itself.
_FAKE_FS_SERVER_SCRIPT = textwrap.dedent(r'''
    import json
    import sys

    def send(message):
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        method = request.get("method")
        request_id = request.get("id")

        if method == "initialize":
            send({
                "jsonrpc": "2.0", "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake-filesystem", "version": "0.0.1"},
                },
            })
        elif method == "notifications/initialized":
            pass  # a notification - no response
        elif method == "tools/list":
            send({
                "jsonrpc": "2.0", "id": request_id,
                "result": {"tools": [
                    {
                        "name": "read_file",
                        "description": "Reads a file's contents.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                    {"name": "unlisted_tool", "description": "Not enabled.", "inputSchema": {"type": "object"}},
                    {"name": "read_env", "description": "Reports which env vars this process sees.",
                     "inputSchema": {"type": "object", "properties": {"names": {"type": "array"}}}},
                ]},
            })
        elif method == "tools/call":
            params = request.get("params", {})
            if params.get("name") == "read_env":
                import os
                names = (params.get("arguments") or {}).get("names", [])
                seen = {n: os.environ.get(n) for n in names}
                send({"jsonrpc": "2.0", "id": request_id, "result": {
                    "content": [{"type": "text", "text": json.dumps(seen)}], "isError": False,
                }})
                continue
            path = (params.get("arguments") or {}).get("path", "")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
                send({"jsonrpc": "2.0", "id": request_id, "result": {
                    "content": [{"type": "text", "text": text}], "isError": False,
                }})
            except OSError as exc:
                send({"jsonrpc": "2.0", "id": request_id, "result": {
                    "content": [{"type": "text", "text": f"error: {exc}"}], "isError": True,
                }})
        else:
            send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "unknown method"}})
''')


@pytest.fixture
def fake_fs_server(tmp_path):
    script_path = tmp_path / "fake_mcp_server.py"
    script_path.write_text(_FAKE_FS_SERVER_SCRIPT, encoding="utf-8")
    return str(script_path)


def _run(coro):
    return asyncio.run(coro)


# -- McpStdioClient: real subprocess, real JSON-RPC --------------------------


def test_connect_performs_the_initialize_handshake(fake_fs_server):
    client = McpStdioClient(command=sys.executable, args=(fake_fs_server,))
    try:
        client.connect()
        assert client.is_connected
        assert client.server_info == {"name": "fake-filesystem", "version": "0.0.1"}
    finally:
        client.close()


def test_list_tools_returns_normalized_toolspecs(fake_fs_server):
    client = McpStdioClient(command=sys.executable, args=(fake_fs_server,))
    try:
        client.connect()
        specs = client.list_tools()
        names = [spec.name for spec in specs]
        assert names == ["read_file", "unlisted_tool", "read_env"]
        read_file_spec = specs[0]
        assert read_file_spec.description == "Reads a file's contents."
        assert read_file_spec.input_schema == {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }
    finally:
        client.close()


def test_call_tool_returns_the_text_content_as_a_tool_result(fake_fs_server, tmp_path):
    target = tmp_path / "hello.txt"
    target.write_text("hello from disk", encoding="utf-8")
    client = McpStdioClient(command=sys.executable, args=(fake_fs_server,))
    try:
        client.connect()
        result = client.call_tool("read_file", {"path": str(target)})
        assert result.content == "hello from disk"
        assert result.is_error is False
    finally:
        client.close()


def test_call_tool_surfaces_a_tool_level_error_without_raising(fake_fs_server, tmp_path):
    client = McpStdioClient(command=sys.executable, args=(fake_fs_server,))
    try:
        client.connect()
        result = client.call_tool("read_file", {"path": str(tmp_path / "does_not_exist.txt")})
        assert result.is_error is True
        assert "error" in result.content.lower()
    finally:
        client.close()


def test_connect_raises_mcp_error_for_a_command_that_does_not_exist():
    client = McpStdioClient(command="this-binary-does-not-exist-anywhere")
    with pytest.raises(McpError):
        client.connect()


def test_call_before_connect_raises_mcp_error(fake_fs_server):
    client = McpStdioClient(command=sys.executable, args=(fake_fs_server,))
    with pytest.raises(McpError):
        client.list_tools()


def test_close_is_idempotent(fake_fs_server):
    client = McpStdioClient(command=sys.executable, args=(fake_fs_server,))
    client.connect()
    client.close()
    client.close()  # must not raise


# -- register_mcp_server_tools: namespacing + enabled_tools filter ----------


def test_register_mcp_server_tools_namespaces_and_filters_by_enabled_tools(fake_fs_server):
    client = McpStdioClient(command=sys.executable, args=(fake_fs_server,))
    registry = ToolRegistry()
    try:
        client.connect()
        config = McpServerConfig(
            name="fs", command=sys.executable, args=(fake_fs_server,),
            scopes=frozenset({"fs.read"}), approval="always",
            enabled_tools=frozenset({"read_file"}),
        )
        registered = register_mcp_server_tools(registry, client, config)
        assert registered == ("mcp:fs:read_file",)
        assert [spec.name for spec in registry.specs()] == ["mcp:fs:read_file"]
    finally:
        client.close()


# -- exit criterion: end-to-end through ToolRegistry.invoke -----------------


def test_a_configured_filesystem_mcp_servers_tool_is_callable_namespaced_and_approval_gated(fake_fs_server, tmp_path):
    """THE 7.5 EXIT CRITERION."""
    target = tmp_path / "readme.txt"
    target.write_text("mcp end to end works", encoding="utf-8")

    client = McpStdioClient(command=sys.executable, args=(fake_fs_server,))
    registry = ToolRegistry()
    try:
        client.connect()
        config = McpServerConfig(
            name="fs", command=sys.executable, args=(fake_fs_server,),
            scopes=frozenset({"fs.read"}), approval="always",
        )
        registered = register_mcp_server_tools(registry, client, config)
        assert "mcp:fs:read_file" in registered

        prompted = []

        async def request_approval(call):
            prompted.append(call)
            return True

        ctx = RunContext(granted_scopes=frozenset({"fs.read"}), request_approval=request_approval)
        call = ToolCall(id="1", name="mcp:fs:read_file", arguments={"path": str(target)})
        result = _run(registry.invoke(call, ctx))

        assert result.content == "mcp end to end works"
        assert result.is_error is False
        assert len(prompted) == 1  # approval-gated: the human-approval callback WAS consulted
    finally:
        client.close()


def test_an_out_of_scope_mcp_call_is_denied_pre_handler(fake_fs_server, tmp_path):
    client = McpStdioClient(command=sys.executable, args=(fake_fs_server,))
    registry = ToolRegistry()
    try:
        client.connect()
        config = McpServerConfig(
            name="fs", command=sys.executable, args=(fake_fs_server,), scopes=frozenset({"fs.read"})
        )
        register_mcp_server_tools(registry, client, config)

        prompted = []

        async def request_approval(call):
            prompted.append(call)
            return True

        ctx = RunContext(granted_scopes=frozenset(), request_approval=request_approval)  # no fs.read granted
        call = ToolCall(id="1", name="mcp:fs:read_file", arguments={"path": str(tmp_path)})
        result = _run(registry.invoke(call, ctx))

        assert result.is_error is True
        assert "scope" in result.content.lower()
        assert prompted == []  # denied before any approval prompt, let alone the handler
    finally:
        client.close()


# -- environment isolation (the spawn must never inherit the backend's env) --


def _env_seen_by_server(monkeypatch, tmp_path, *, config_env=None):
    """Spawn the fake server with a canary secret in THIS process's
    environment and ask the server which variables it can actually see."""
    import json as _json
    monkeypatch.setenv("OPENAI_API_KEY", "sk-CANARY-must-not-cross")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-CANARY-must-not-cross")
    script = tmp_path / "fake_server.py"
    script.write_text(_FAKE_FS_SERVER_SCRIPT, encoding="utf-8")
    client = McpStdioClient(command=sys.executable, args=(str(script),), env=config_env or {})
    client.connect()
    try:
        result = client.call_tool("read_env", {
            "names": ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN", "PATH"],
        })
    finally:
        client.close()
    return _json.loads(result.content)


def test_server_process_does_not_inherit_provider_keys_from_the_backend(monkeypatch, tmp_path):
    """The leak this closes: Popen(env=None) handed every provider API key
    the user had set as an environment variable to whatever third-party
    MCP server they configured. A server sees the allowlist base plus its
    OWN config env - nothing else."""
    seen = _env_seen_by_server(monkeypatch, tmp_path)
    assert seen["OPENAI_API_KEY"] is None
    assert seen["ANTHROPIC_API_KEY"] is None
    assert seen["PATH"]  # launchers like npx/uvx still resolve


def test_server_receives_exactly_the_variables_its_own_config_names(monkeypatch, tmp_path):
    seen = _env_seen_by_server(monkeypatch, tmp_path, config_env={"GITHUB_TOKEN": "ghp_for_this_server_only"})
    assert seen["GITHUB_TOKEN"] == "ghp_for_this_server_only"
    assert seen["OPENAI_API_KEY"] is None  # still nothing beyond base + own config


def test_config_env_round_trips_and_tolerates_malformed_values():
    cfg = McpServerConfig.from_dict({
        "name": "gh", "command": "npx",
        "env": {"GITHUB_TOKEN": "ghp_x", " ": "dropped-blank-name", "NULL": None, 7: 8},
    })
    assert cfg.env == {"GITHUB_TOKEN": "ghp_x", "7": "8"}
    assert McpServerConfig.from_dict(cfg.to_dict()).env == cfg.env
    # non-dict / missing degrade to "no extra variables", never a failed load
    assert McpServerConfig.from_dict({"name": "a", "command": "b", "env": "oops"}).env == {}
    assert McpServerConfig.from_dict({"name": "a", "command": "b"}).env == {}


# -- stderr must not deadlock the client (the pipe-buffer wedge) --------------


_STDERR_FLOOD_SERVER_SCRIPT = textwrap.dedent(r'''
    import json
    import sys

    def send(message):
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        method = request.get("method")
        request_id = request.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": request_id, "result": {
                "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "noisy", "version": "0.0.1"},
            }})
        elif method == "notifications/initialized":
            pass
        elif method == "tools/call":
            # Flood stderr well past any OS pipe buffer (~4 KiB on Windows,
            # ~64 KiB on Linux) BEFORE sending the stdout response. Without a
            # client-side stderr drainer this write blocks on a full pipe and
            # the response below is never sent - the client's _call() then
            # hangs until its timeout.
            sys.stderr.write("x" * (512 * 1024))
            sys.stderr.flush()
            send({"jsonrpc": "2.0", "id": request_id, "result": {
                "content": [{"type": "text", "text": "ok"}], "isError": False,
            }})
        else:
            send({"jsonrpc": "2.0", "id": request_id,
                  "error": {"code": -32601, "message": "unknown method"}})
''')


def test_a_server_flooding_stderr_does_not_deadlock_the_client(tmp_path):
    """Regression: stderr was read only once, on unexpected close, so a
    server writing more than the OS pipe buffer to stderr before answering
    blocked on its own stderr write and never sent its stdout response - so
    every call() hung until the timeout. A dedicated drainer thread keeps
    the pipe empty so the response still arrives. The generous timeout here
    is a backstop: with the fix this returns in well under a second; without
    it, the call never returns and the timeout is what fails the test."""
    script = tmp_path / "noisy_stderr_server.py"
    script.write_text(_STDERR_FLOOD_SERVER_SCRIPT, encoding="utf-8")
    client = McpStdioClient(command=sys.executable, args=(str(script),), timeout=20.0)
    client.connect()
    try:
        result = client.call_tool("anything", {})
        assert result.content == "ok"
        assert result.is_error is False
    finally:
        client.close()


# -- stdin write must not hang forever (the write-side deadlock) ------------


_DEAF_SERVER_SCRIPT = textwrap.dedent(r'''
    import json
    import sys
    import time

    def send(message):
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        method = request.get("method")
        request_id = request.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": request_id, "result": {
                "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "deaf", "version": "0.0.1"},
            }})
            # Stop reading stdin entirely from here on - simulates a server
            # that is busy/wedged and never drains its stdin again. The
            # sleep sits inside the loop body, so control never returns to
            # `for line in sys.stdin` to pull the next request. Bounded
            # (not e.g. a full minute) so client.close() - which itself
            # waits for the leaked writer thread's blocked write() to
            # finally unblock once this process resumes reading - doesn't
            # make the test itself slow; it just needs to comfortably
            # outlast the client's own 1s write timeout below.
            time.sleep(3)
        else:
            send({"jsonrpc": "2.0", "id": request_id,
                  "error": {"code": -32601, "message": "unreachable - stdin is not read again"}})
''')


def test_a_stdin_write_that_the_server_never_drains_raises_instead_of_hanging_forever(tmp_path):
    """Regression: _write() had no timeout on process.stdin.write()/flush().
    A subprocess that stops draining its stdin blocks that write forever
    while _call_lock is held, deadlocking every future call to this client
    - with nothing anywhere that ever unblocks it short of killing the
    process. Here the fake server answers the handshake and then never
    reads stdin again; the client's next call carries an argument large
    enough to fill the OS pipe buffer, so the write blocks. Bounded by the
    fix's write-side timeout, the call must raise a clear McpError well
    within a few seconds instead of hanging."""
    script = tmp_path / "deaf_server.py"
    script.write_text(_DEAF_SERVER_SCRIPT, encoding="utf-8")
    client = McpStdioClient(command=sys.executable, args=(str(script),), timeout=1.0)
    client.connect()
    try:
        # Comfortably larger than any OS pipe buffer (a few KB on Windows,
        # tens of KB on Linux) - see the stderr-flood test above for the
        # same reasoning on the read side.
        big_argument = "x" * (4 * 1024 * 1024)
        started = time.monotonic()
        with pytest.raises(McpError, match="timed out"):
            client.call_tool("anything", {"payload": big_argument})
        elapsed = time.monotonic() - started
        # The fake server first resumes draining stdin at the 3s mark (see
        # its own comment) - well past self.timeout=1.0s. A tight bound
        # here (not e.g. 10s) is deliberate: it's what actually proves the
        # write was cut off by ITS OWN timeout rather than merely finishing
        # whenever the server eventually got around to reading it.
        assert elapsed < 2.5, f"write timeout did not bound the call - took {elapsed:.1f}s"
    finally:
        client.close()


# -- a malformed tools/list response must not crash the whole registry ------


_MALFORMED_TOOLS_SERVER_SCRIPT = textwrap.dedent(r'''
    import json
    import sys

    def send(message):
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        method = request.get("method")
        request_id = request.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": request_id, "result": {
                "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "buggy", "version": "0.0.1"},
            }})
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            send({
                "jsonrpc": "2.0", "id": request_id,
                "result": {"tools": [
                    {"name": "good_tool", "description": "the only well-formed entry",
                     "inputSchema": {"type": "object"}},
                    {"description": "missing the required name field entirely"},
                    "not_even_a_dict",
                    {"name": None, "description": "a null name"},
                    {"name": "", "description": "a blank name"},
                ]},
            })
        else:
            send({"jsonrpc": "2.0", "id": request_id,
                  "error": {"code": -32601, "message": "unknown method"}})
''')


def test_list_tools_skips_malformed_entries_instead_of_crashing(tmp_path):
    """Regression: list_tools() indexed tool["name"] with no shape
    validation, so a single malformed entry (missing "name", a non-dict
    entry, a null/blank name) raised KeyError/TypeError uncaught by the
    only real caller - crashing the WHOLE Builder tool registry (every
    other configured server's tools too) for the session. Malformed
    entries must be skipped, not raised on - same "drop the bad entry, keep
    the rest" posture as graphlink_settings_store.py's get_mcp_servers."""
    script = tmp_path / "malformed_tools_server.py"
    script.write_text(_MALFORMED_TOOLS_SERVER_SCRIPT, encoding="utf-8")
    client = McpStdioClient(command=sys.executable, args=(str(script),))
    try:
        client.connect()
        specs = client.list_tools()  # must not raise
        assert [spec.name for spec in specs] == ["good_tool"]
    finally:
        client.close()


_NULL_TOOLS_SERVER_SCRIPT = textwrap.dedent(r'''
    import json
    import sys

    def send(message):
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        method = request.get("method")
        request_id = request.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": request_id, "result": {
                "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "buggy", "version": "0.0.1"},
            }})
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": None}})
        else:
            send({"jsonrpc": "2.0", "id": request_id,
                  "error": {"code": -32601, "message": "unknown method"}})
''')


def test_list_tools_tolerates_a_non_list_tools_field(tmp_path):
    """A `"tools": null` response (or any non-list shape) must degrade to
    "no tools", not raise - same tolerance as a malformed individual entry."""
    script = tmp_path / "null_tools_server.py"
    script.write_text(_NULL_TOOLS_SERVER_SCRIPT, encoding="utf-8")
    client = McpStdioClient(command=sys.executable, args=(str(script),))
    try:
        client.connect()
        assert client.list_tools() == ()
    finally:
        client.close()
