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
                ]},
            })
        elif method == "tools/call":
            params = request.get("params", {})
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
        assert names == ["read_file", "unlisted_tool"]
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
