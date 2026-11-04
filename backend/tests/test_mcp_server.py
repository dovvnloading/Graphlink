"""MCP stdio contract and a real end-to-end loopback backend round-trip."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from backend.headless import _bind_listener, _write_token_file
from backend.mcp_server import (
    MODERN_VERSION,
    MAX_INPUT_LINE,
    ControlError,
    ControlHttpClient,
    StdioMcpServer,
    serve,
)


class StubControl:
    def read(self, path, params):
        return {"path": path, "params": params}


def request(request_id, method, params=None):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


def modern_params(**other):
    return {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": {"name": "test-client", "version": "1.0"},
        }, **other,
    }


def test_legacy_stdio_initialization_and_read_only_tool_call():
    source = io.StringIO("\n".join([
        json.dumps(request(1, "initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {},
        })),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps(request(2, "tools/list")),
        json.dumps(request(3, "tools/call", {
            "name": "graphlink_list_workspaces", "arguments": {},
        })),
    ]) + "\n")
    sink = io.StringIO()
    serve(StubControl(), source, sink)
    output = [json.loads(line) for line in sink.getvalue().splitlines()]

    assert len(output) == 3, "notifications must not receive JSON-RPC replies"
    assert output[0]["result"]["protocolVersion"] == "2024-11-05"
    assert output[0]["result"]["capabilities"]["tools"] == {}
    tools = output[1]["result"]["tools"]
    assert {t["name"] for t in tools} == {
        "graphlink_list_workspaces", "graphlink_list_graphs", "graphlink_get_graph",
        "graphlink_list_nodes", "graphlink_read_subgraph",
    }
    assert all(t["annotations"]["readOnlyHint"] is True for t in tools)
    assert output[2]["result"]["structuredContent"] == {
        "path": "/api/control/workspaces", "params": {},
    }


def test_modern_discovery_and_stateless_calls_without_initialize():
    server = StdioMcpServer(StubControl())
    discovery = server.handle(request(1, "server/discover", modern_params()))
    assert discovery["result"]["supportedVersions"] == [MODERN_VERSION]
    assert discovery["result"]["resultType"] == "complete"
    assert discovery["result"]["capabilities"] == {"tools": {}}

    tools = server.handle(request(2, "tools/list", modern_params()))
    assert tools["result"]["resultType"] == "complete"
    assert len(tools["result"]["tools"]) == 5

    call = server.handle(request(3, "tools/call", modern_params(
        name="graphlink_get_graph", arguments={"graph_id": 5, "limit": 2},
    )))
    assert call["result"]["resultType"] == "complete"
    assert call["result"]["structuredContent"] == {
        "path": "/api/control/graphs/5", "params": {"limit": 2},
    }

    unsupported = server.handle(request(4, "tools/list", {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": "1900-01-01",
            "io.modelcontextprotocol/clientCapabilities": {},
        },
    }))
    assert unsupported["error"]["code"] == -32022


@pytest.mark.parametrize("invalid", (
    {"graph_id": "1"}, {"graph_id": True}, {"graph_id": 0},
    {"graph_id": 1, "offset": -1}, {"graph_id": 1, "limit": 101},
    {"graph_id": 1, "unexpected": "bad"},
))
def test_mcp_rejects_invalid_tool_arguments_before_http(invalid):
    server = StdioMcpServer(StubControl())
    result = server.handle(request(8, "tools/call", modern_params(
        name="graphlink_get_graph", arguments=invalid,
    )))
    assert result["error"]["code"] == -32602


def test_read_subgraph_schema_rejects_depth_above_actual_backend_cap():
    server = StdioMcpServer(StubControl())
    tools = server.handle(request(1, "tools/list", modern_params()))["result"]["tools"]
    spec = next(item for item in tools if item["name"] == "graphlink_read_subgraph")
    assert spec["inputSchema"]["properties"]["depth"]["maximum"] == 3
    invalid = server.handle(request(2, "tools/call", modern_params(
        name="graphlink_read_subgraph",
        arguments={"root_id": "n1", "depth": 4},
    )))
    assert invalid["error"]["code"] == -32602


def test_unknown_and_unauthorized_mcp_calls_are_not_raw_intent_dispatch():
    server = StdioMcpServer(StubControl())
    for name in ("graphlink_delete_node", "shell.exec", "graphlink_run_harness"):
        result = server.handle(request(9, "tools/call", modern_params(name=name, arguments={})))
        assert result["error"]["code"] == -32602


def test_mcp_tool_failure_returns_is_error_without_killing_server():
    class FailingControl:
        def read(self, path, params):
            raise ControlError("headless backend unavailable")

    server = StdioMcpServer(FailingControl())
    server.handle(request(1, "initialize", {"protocolVersion": "2025-11-25"}))
    result = server.handle(request(2, "tools/call", {
        "name": "graphlink_list_graphs", "arguments": {},
    }))
    assert result["result"]["isError"] is True
    assert "unavailable" in result["result"]["content"][0]["text"]
    assert len(server.handle(request(3, "tools/list"))["result"]["tools"]) == 5


@pytest.mark.parametrize("url", (
    "http://localhost:8765", "https://127.0.0.1:8765", "http://8.8.8.8:8765",
    "http://127.0.0.1:8765/private", "http://127.0.0.1:8765/?token=foo",
    "http://other.example:8765",
))
def test_mcp_rejects_non_loopback_or_credential_bearing_control_urls(url):
    with pytest.raises(ValueError, match="Control URL"):
        ControlHttpClient(url, "secret")


def test_mcp_stdio_recovers_after_invalid_or_oversized_input():
    sink = io.StringIO()
    source = io.StringIO("not-json\n" + " " * 256001 + "\n"
                         + json.dumps(request(3, "initialize", {"protocolVersion": "2025-11-25"})) + "\n")
    serve(StubControl(), source, sink)
    results = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert [r["error"]["code"] for r in results[:2]] == [-32700, -32700]
    assert results[2]["result"]["protocolVersion"] == "2025-11-25"


def test_stdio_rejects_a_complete_line_one_byte_over_the_cap():
    # Previously the cap was checked only if the oversized chunk lacked
    # a newline. A complete MAX_INPUT_LINE+1 line slipped through.
    message = json.dumps(request(1, "initialize", {"protocolVersion": "2024-11-05"}))
    oversized = message + " " * (MAX_INPUT_LINE - len(message)) + "\n"
    assert len(oversized) == MAX_INPUT_LINE + 1
    sink = io.StringIO()
    serve(StubControl(), io.StringIO(oversized + message + "\n"), sink)
    responses = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert responses[0]["error"]["code"] == -32700
    assert responses[1]["result"]["protocolVersion"] == "2024-11-05"


def test_stdio_rejects_multibyte_utf8_line_over_byte_cap():
    message = json.dumps(request(1, "initialize", {
        "protocolVersion": "2025-11-25",
    }))
    # This fits in MAX_INPUT_LINE Unicode characters but not wire bytes.
    oversized = message + ("é" * (MAX_INPUT_LINE // 2)) + "\\n"
    assert len(oversized) <= MAX_INPUT_LINE
    assert len(oversized.encode("utf-8")) > MAX_INPUT_LINE
    sink = io.StringIO()
    serve(StubControl(), io.StringIO(oversized + message + "\\n"), sink)
    replies = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert replies[0]["error"]["code"] == -32700
    assert replies[1]["result"]["protocolVersion"] == "2025-11-25"


def test_stdio_recovers_after_invalid_utf8_in_binary_input():
    message = json.dumps(request(1, "initialize", {
        "protocolVersion": "2025-11-25",
    })).encode("utf-8")
    sink = io.StringIO()
    serve(StubControl(), io.BytesIO(b"\\xff\\n" + message + b"\\n"), sink)
    replies = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert replies[0]["error"]["code"] == -32700
    assert replies[1]["result"]["protocolVersion"] == "2025-11-25"


@pytest.mark.parametrize(("status", "included"), [(400, True), (404, True), (500, False)])
def test_http_adapter_surfaces_safe_client_errors_but_hides_server_details(status, included):
    import urllib.error

    client = ControlHttpClient("http://127.0.0.1:8765", "read-only-token")

    class StubOpener:
        def open(self, request, *, timeout):
            raise urllib.error.HTTPError(
                request.full_url, status, "error", {},
                io.BytesIO(b'{"detail":"Unknown node: expected-id"}'),
            )

    client._opener = StubOpener()
    with pytest.raises(ControlError) as exc:
        client.read("/api/control/scene/subgraph", {"root_id": "expected-id"})
    assert "HTTP " + str(status) in str(exc.value)
    assert ("Unknown node: expected-id" in str(exc.value)) is included


def test_mcp_writes_are_hidden_unless_adapter_explicitly_receives_a_write_token():
    default = StdioMcpServer(StubControl())
    default.handle(request(1, "initialize", {"protocolVersion": "2025-11-25"}))
    assert len(default.handle(request(2, "tools/list"))["result"]["tools"]) == 5
    assert default.handle(request(3, "tools/call", {
        "name": "graphlink_create_node", "arguments": {},
    }))["error"]["code"] == -32602

    class WriteControl(StubControl):
        can_write = True

        def write(self, path, payload):
            return {"path": path, "payload": payload}

    enabled = StdioMcpServer(WriteControl())
    enabled.handle(request(1, "initialize", {"protocolVersion": "2025-11-25"}))
    listed = enabled.handle(request(2, "tools/list"))["result"]["tools"]
    assert len(listed) == 9
    names = {entry["name"] for entry in listed}
    assert names - {"graphlink_list_workspaces", "graphlink_list_graphs",
                    "graphlink_get_graph", "graphlink_list_nodes",
                    "graphlink_read_subgraph"} == {
        "graphlink_create_node", "graphlink_connect_nodes",
        "graphlink_set_node_content", "graphlink_save_graph",
    }
    assert all(not entry["annotations"]["readOnlyHint"] for entry in listed[5:])

    revision = "a" * 64
    call = enabled.handle(request(3, "tools/call", {
        "name": "graphlink_create_node",
        "arguments": {
            "operation_id": "mcp-create-0001", "expected_revision": revision,
            "kind": "chat", "content": "evidence", "is_user": True,
        },
    }))
    assert call["result"]["isError"] is False
    assert call["result"]["structuredContent"] == {
        "path": "/api/control/scene/mutate",
        "payload": {
            "operation_id": "mcp-create-0001", "expected_revision": revision,
            "operation": "create_node",
            "arguments": {"kind": "chat", "content": "evidence", "is_user": True},
        },
    }
    saved = enabled.handle(request(4, "tools/call", {
        "name": "graphlink_save_graph",
        "arguments": {"operation_id": "mcp-save-0001", "expected_revision": revision},
    }))
    assert saved["result"]["structuredContent"]["path"] == "/api/control/scene/save"
    assert saved["result"]["structuredContent"]["payload"] == {
        "operation_id": "mcp-save-0001", "expected_revision": revision,
    }
    invalid = enabled.handle(request(5, "tools/call", {
        "name": "graphlink_save_graph",
        "arguments": {"operation_id": "bad", "expected_revision": "not-a-hash"},
    }))
    assert invalid["error"]["code"] == -32602


def test_mcp_write_http_uses_only_the_write_credential():
    client = ControlHttpClient(
        "http://127.0.0.1:8765", "read-credential", write_token="write-credential",
    )
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, count):
            return b'{"saved":true}'

    class Opener:
        def open(self, req, *, timeout):
            calls.append((req.get_method(), req.get_header("Authorization"), req.full_url))
            assert req.get_header("Content-type") == "application/json"
            return Response()

    client._opener = Opener()
    result = client.write("/api/control/scene/save", {
        "operation_id": "test-write-0001", "expected_revision": "a" * 64,
    })
    assert result["saved"] is True
    assert calls == [("POST", "Bearer write-credential", "http://127.0.0.1:8765/api/control/scene/save")]
    assert "read-credential" not in str(calls)
    with pytest.raises(ControlError, match="allowlisted"):
        client.write("/api/health", {})


def test_headless_write_mode_is_opt_in_and_cleans_both_token_files(tmp_path, monkeypatch):
    import backend.headless as headless
    import backend.app as app_module
    import uvicorn

    token_file = tmp_path / "read.token"
    write_file = tmp_path / "write.token"
    minted = iter(("private-backend-admin", "read-only-credential", "write-only-credential"))
    seen = {}

    class Listener:
        def getsockname(self):
            return ("127.0.0.1", 8765)

        def close(self):
            seen["closed"] = True

    class Server:
        def __init__(self, config):
            pass

        def run(self, *, sockets):
            seen["read_file"] = token_file.read_text(encoding="utf-8").strip()
            seen["write_file"] = write_file.read_text(encoding="utf-8").strip()

    def fake_create_app(**kwargs):
        seen["app_kwargs"] = kwargs
        return object()

    monkeypatch.setattr(headless, "mint_token", lambda: next(minted))
    monkeypatch.setattr(headless, "_bind_listener", lambda port: Listener())
    monkeypatch.setattr(app_module, "create_app", fake_create_app)
    monkeypatch.setattr(uvicorn, "Server", Server)
    monkeypatch.setattr(uvicorn, "Config", lambda app, **kwargs: app)
    assert headless.main([
        "--port", "8765", "--chat-db", str(tmp_path / "chats.db"),
        "--token-file", str(token_file), "--write-token-file", str(write_file),
        "--allow-mcp-writes",
    ]) == 0
    assert seen["app_kwargs"]["auth_token"] == "private-backend-admin"
    assert seen["app_kwargs"]["control_read_token"] == "read-only-credential"
    assert seen["app_kwargs"]["control_write_token"] == "write-only-credential"
    assert seen["read_file"] == "read-only-credential"
    assert seen["write_file"] == "write-only-credential"
    assert seen["closed"] is True
    assert not token_file.exists() and not write_file.exists()


def test_headless_rejects_identical_read_write_token_file_paths(tmp_path):
    import backend.headless as headless

    path = tmp_path / "same-token"
    with pytest.raises(SystemExit):
        headless.main([
            "--port", "0", "--chat-db", str(tmp_path / "chats.db"),
            "--token-file", str(path), "--write-token-file", str(path),
            "--allow-mcp-writes",
        ])
    assert not path.exists()


@pytest.mark.posix_permissions
def test_headless_token_file_is_private_on_posix(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX 0600 bits do not express Windows ACLs")
    token_file = tmp_path / "subdir" / "headless.token"
    _write_token_file(token_file, "test-temporary-secret")
    assert token_file.read_text(encoding="utf-8").strip() == "test-temporary-secret"
    assert token_file.stat().st_mode & 0o777 == 0o600


def test_headless_token_file_refuses_symlinks(tmp_path):
    real = tmp_path / "target.txt"
    real.write_text("original")
    link = tmp_path / "headless.token"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported in this environment")
    with pytest.raises(OSError, match="symlink"):
        _write_token_file(link, "must-not-overwrite")
    assert real.read_text() == "original"


def test_headless_launcher_publishes_only_read_capability(tmp_path, monkeypatch):
    """The actual launcher must not publish its WebSocket/admin token."""
    import backend.headless as headless
    import backend.app as app_module
    import uvicorn

    token_file = tmp_path / "published.token"
    tokens = iter(("backend-admin-secret", "mcp-read-only-secret"))
    observed = {}

    class Listener:
        def getsockname(self):
            return ("127.0.0.1", 8765)

        def close(self):
            observed["socket_closed"] = True

    listener = Listener()

    def capture_app(**kwargs):
        observed["config"] = kwargs
        return object()

    class Server:
        def __init__(self, config):
            observed["uvicorn_config"] = config

        def run(self, *, sockets):
            assert sockets == [listener]
            observed["file_during_run"] = token_file.read_text(encoding="utf-8").strip()

    monkeypatch.setattr(headless, "mint_token", lambda: next(tokens))
    monkeypatch.setattr(headless, "_bind_listener", lambda port: listener)
    monkeypatch.setattr(app_module, "create_app", capture_app)
    monkeypatch.setattr(uvicorn, "Config", lambda app, **kwargs: app)
    monkeypatch.setattr(uvicorn, "Server", Server)

    assert headless.main([
        "--port", "8765", "--token-file", str(token_file),
        "--chat-db", str(tmp_path / "chats.db"),
    ]) == 0
    assert observed["config"]["auth_token"] == "backend-admin-secret"
    assert observed["config"]["control_read_token"] == "mcp-read-only-secret"
    assert observed["file_during_run"] == "mcp-read-only-secret"
    assert not token_file.exists(), "launcher should clean its own file on normal exit"
    assert observed["socket_closed"] is True


def test_headless_refuses_to_boot_when_private_token_staging_fails(tmp_path, monkeypatch, capsys):
    import backend.headless as headless
    import backend.app as app_module

    token_file = tmp_path / "headless.token"
    token_file.write_text("previous-token", encoding="utf-8")
    closed = []

    class Listener:
        def close(self):
            closed.append(True)

    monkeypatch.setattr(headless, "_bind_listener", lambda port: Listener())

    def reject_staging(_path):
        raise OSError("cannot secure token file DACL")

    def forbidden_app(**kwargs):
        pytest.fail("application must not start with an insecure MCP token file")

    monkeypatch.setattr(headless, "_private_staging", reject_staging)
    monkeypatch.setattr(app_module, "create_app", forbidden_app)

    result = headless.main([
        "--port", "0", "--chat-db", str(tmp_path / "chats.db"),
        "--token-file", str(token_file),
    ])
    assert result == 1
    assert closed == [True], "the listener must close on ACL failure"
    assert token_file.read_text(encoding="utf-8") == "previous-token"
    assert "cannot secure MCP token file" in capsys.readouterr().err


def test_headless_refuses_busy_data_dir_without_rotating_existing_mcp_token(tmp_path, monkeypatch, capsys):
    import backend.headless as headless
    from backend.instance_lock import exclusive_data_directory

    database = tmp_path / "data" / "chats.db"
    token_file = tmp_path / "read-only.token"
    token_file.write_text("the-first-instance-token", encoding="utf-8")

    # The busy path must terminate BEFORE the new process binds a socket or
    # replaces the first process's still-live read-only token file.
    monkeypatch.setattr(
        headless, "_bind_listener",
        lambda _port: pytest.fail("busy data directory reached listener binding"),
    )
    with exclusive_data_directory(database):
        status = headless.main([
            "--port", "0", "--chat-db", str(database),
            "--token-file", str(token_file),
        ])
        assert status == 1
        assert token_file.read_text(encoding="utf-8") == "the-first-instance-token"
        assert "already using" in capsys.readouterr().err


def test_real_fastapi_backend_is_reachable_from_separate_stdio_mcp_process(tmp_path):
    """Subprocess MCP -> authenticated local HTTP -> real Graphlink database.

    Run one FastAPI instance on an already-bound ephemeral loopback socket;
    do not start a second backend in the MCP process. No GUI or network
    service outside this test's 127.0.0.1 listener is involved.
    """
    import uvicorn

    from backend.app import create_app
    from backend.chat_library import get_all_workspaces, save_chat_atomically_row
    from graphlink_process_env import safe_subprocess_env

    admin_token = "secret-admin-token-never-published"
    control_token = "test-headless-stdio-read-only-token"
    token_file = tmp_path / "headless.token"
    _write_token_file(token_file, control_token)
    assert admin_token not in token_file.read_text(encoding="utf-8")
    db = tmp_path / "chats.db"
    workspace = get_all_workspaces(db)[0]
    saved_id, _ = save_chat_atomically_row(
        db, None, "MCP saved graph",
        {"nodes": [{"id": "n1", "node_type": "chat", "raw_content": "hello from real db"}]},
        [], [], workspace_id=workspace["id"],
    )

    app = create_app(
        spa_dir=tmp_path / "missing-spa",
        auth_token=admin_token, control_read_token=control_token,
        chat_db_path=db, settings_state_file=tmp_path / "session.dat",
    )
    listener = _bind_listener(0)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="error", lifespan="off",
    ))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            assert thread.is_alive()
            time.sleep(0.05)
        assert server.started, "headless FastAPI server did not start"

        messages = [
            request(1, "initialize", {
                "protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {},
            }),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            request(2, "tools/list"),
            request(3, "tools/call", {"name": "graphlink_list_workspaces", "arguments": {}}),
            request(4, "tools/call", {"name": "graphlink_get_graph", "arguments": {"graph_id": saved_id}}),
        ]
        proc = subprocess.run(
            [
                sys.executable, "-m", "backend.mcp_server", "--url",
                f"http://127.0.0.1:{port}", "--token-file", str(token_file),
            ],
            input="\n".join(json.dumps(m) for m in messages) + "\n",
            cwd=Path(__file__).resolve().parents[2],
            env=safe_subprocess_env(),
            text=True, capture_output=True, timeout=20, encoding="utf-8",
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        replies = [json.loads(line) for line in proc.stdout.splitlines()]
        assert len(replies) == 4
        assert replies[2]["result"]["structuredContent"]["workspaces"][0]["name"] == "Default"
        graph = replies[3]["result"]["structuredContent"]
        assert graph["graph"]["id"] == saved_id
        assert graph["nodes"][0]["excerpt"] == "hello from real db"
        assert graph["source"] == "saved"

        # Test against Graphlink's REAL legacy MCP stdio client as well,
        # not only the hand-authored JSON-RPC fixture above. This catches
        # handshake and tool-schema drift between our two implementations.
        from backend.mcp_client import McpStdioClient

        client = McpStdioClient(
            command=sys.executable,
            args=(
                "-m", "backend.mcp_server", "--url", f"http://127.0.0.1:{port}",
                "--token-file", str(token_file),
            ),
            cwd=str(Path(__file__).resolve().parents[2]),
            timeout=10,
        )
        try:
            client.connect()
            specs = client.list_tools()
            assert any(spec.name == "graphlink_list_workspaces" for spec in specs)
            result = client.call_tool("graphlink_get_graph", {"graph_id": saved_id})
            assert result.is_error is False
            assert "MCP saved graph" in result.content
        finally:
            client.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
