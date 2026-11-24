"""Streamable HTTP and structured-result boundaries, including a real server."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from backend.mcp_client import (
    McpError, McpServerConfig, McpStdioClient, create_mcp_client,
    list_mcp_tools, parse_mcp_result,
)
from backend.mcp_http import McpHttpClient, validate_http_endpoint, validate_http_headers


def _response(data, status=200, headers=None):
    return httpx.Response(
        status, headers={"content-type": "application/json", **(headers or {})},
        stream=httpx.ByteStream(json.dumps(data).encode()),
    )


@pytest.fixture
def mocked_http(monkeypatch):
    original = httpx.Client
    requests = []

    def install(responder=None, *, protocol="2025-11-25", session="test-session"):
        def handler(request):
            body = json.loads(request.content) if request.content else {}
            requests.append((request, body))
            if request.method == "DELETE":
                return httpx.Response(204)
            method = body.get("method")
            if method == "initialize":
                return _response({"jsonrpc": "2.0", "id": body["id"], "result": {
                    "protocolVersion": protocol, "serverInfo": {"name": "test", "version": "1"},
                }}, headers={"mcp-session-id": session} if session is not None else {})
            if method == "notifications/initialized":
                return httpx.Response(202)
            if responder is not None:
                return responder(request, body)
            return _response({"jsonrpc": "2.0", "id": body["id"], "result": {"tools": []}})

        def make_client(**kwargs):
            assert kwargs["follow_redirects"] is False
            assert kwargs["trust_env"] is False
            return original(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(httpx, "Client", make_client)
        return requests

    return install


def test_http_connection_negotiates_version_headers_and_closes_session(mocked_http):
    requests = mocked_http(protocol="2025-06-18")
    client = McpHttpClient(url="https://example.org/mcp", headers={"Authorization": "Bearer secret"})
    client.connect()
    client.connect()  # idempotent
    assert client.is_connected
    assert client.server_info["name"] == "test"
    assert client.list_tools() == ()
    client.close()
    client.close()
    assert not client.is_connected
    assert [entry[1].get("method") for entry in requests] == [
        "initialize", "notifications/initialized", "tools/list", None,
    ]
    assert requests[0][0].headers["accept"] == "application/json, text/event-stream"
    assert requests[1][0].headers["mcp-protocol-version"] == "2025-06-18"
    assert requests[2][0].headers["mcp-session-id"] == "test-session"
    assert requests[2][0].headers["authorization"] == "Bearer secret"
    assert requests[-1][0].method == "DELETE"


@pytest.mark.parametrize("version", ["2024-11-05", "2099-01-01", None])
def test_unsupported_protocol_does_not_connect(mocked_http, version):
    mocked_http(protocol=version)
    client = McpHttpClient(url="https://example.org/mcp")
    with pytest.raises(McpError, match="unsupported protocol"):
        client.connect()
    assert not client.is_connected


@pytest.mark.parametrize("session", ["", "bad session", "bad\nheader", "x" * 1025])
def test_invalid_session_cannot_become_a_request_header(mocked_http, session):
    mocked_http(session=session)
    with pytest.raises(McpError, match="session identifier"):
        McpHttpClient(url="https://example.org/mcp").connect()


def test_sse_notifications_and_foreign_ids_are_ignored(mocked_http):
    def respond(_request, body):
        payloads = [
            {"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}},
            {"jsonrpc": "2.0", "id": True, "result": {"content": [{"type": "text", "text": "wrong"}]}},
            {"jsonrpc": "2.0", "id": body["id"], "result": {
                "content": [{"type": "text", "text": "Résumé"}], "structuredContent": {"count": 3},
            }},
        ]
        raw = ": keep-alive\r\nid: ignored\r\ndata:\r\n\r\n" + "".join(
            "event: message\r\ndata: " + json.dumps(value, ensure_ascii=False) + "\r\n\r\n"
            for value in payloads
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              stream=httpx.ByteStream(raw.encode()))

    mocked_http(respond, session=None)
    client = McpHttpClient(url="https://example.org/mcp")
    client.connect()
    result = client.call_tool("summarize", {})
    assert result.content == "Résumé"
    assert result.structured_content == {"count": 3}
    assert result.content_blocks == ({"type": "text", "text": "Résumé"},)
    client.close()


@pytest.mark.parametrize("status,expected", [(301, "redirected"), (307, "redirected"), (401, "authentication"),
                                             (403, "authentication"), (404, "session expired"), (500, "HTTP 500")])
def test_http_errors_never_retry_or_leak_server_body(mocked_http, status, expected):
    requests = mocked_http(lambda *_: _response({"secret": "do-not-echo"}, status, {"location": "https://evil.org"}))
    client = McpHttpClient(url="https://example.org/mcp", headers={"Authorization": "Bearer secret"})
    client.connect()
    with pytest.raises(McpError, match=expected) as caught:
        client.call_tool("charge", {"amount": 1})
    assert "do-not-echo" not in str(caught.value)
    assert "Bearer secret" not in str(caught.value)
    assert len([body for _, body in requests if body.get("method") == "tools/call"]) == 1
    assert all(request.url.host == "example.org" for request, _ in requests)
    client.close()


def test_http_network_timeout_is_uncertain_and_never_retried(mocked_http):
    def timeout(request, _body):
        raise httpx.ReadTimeout("secret-response", request=request)
    requests = mocked_http(timeout)
    client = McpHttpClient(url="https://example.org/mcp")
    client.connect()
    with pytest.raises(McpError, match="Review the request") as caught:
        client.call_tool("write", {})
    assert "secret-response" not in str(caught.value)
    assert sum(body.get("method") == "tools/call" for _, body in requests) == 1
    client.close()


@pytest.mark.parametrize("response,pattern", [
    ({"jsonrpc": "2.0", "id": 2, "result": []}, "non-object result"),
    ({"jsonrpc": "2.0", "id": 2, "error": {"message": "token=secret"}}, "rejected"),
    ({"jsonrpc": "1.0", "id": 2, "result": {}}, "invalid JSON-RPC"),
    ({"jsonrpc": "2.0", "id": True, "result": {}}, "matching reply"),
    ({"jsonrpc": "2.0", "id": 50, "result": {}}, "matching reply"),
    ({"jsonrpc": "2.0", "id": 11, "method": "sampling/createMessage"}, "unsupported client capability"),
])
def test_invalid_rpc_cannot_be_treated_as_a_success(mocked_http, response, pattern):
    mocked_http(lambda *_: _response(response))
    client = McpHttpClient(url="https://example.org/mcp")
    client.connect()
    with pytest.raises(McpError, match=pattern):
        client.call_tool("get", {})
    client.close()


@pytest.mark.parametrize("body,content_type,pattern", [
    (b"not-json", "application/json", "invalid JSON"),
    (b"[]", "application/json", "non-object response"),
    (b"<html>password</html>", "text/html", "must return JSON"),
    (b'data: {"jsonrpc":"2.0","id":2,"result":{}}', "text/event-stream", "matching reply"),
    (b'data: {"jsonrpc":"2.0","id":2,"result":{}}\n', "text/event-stream", "matching reply"),
])
def test_malformed_or_incomplete_responses_fail_closed(mocked_http, body, content_type, pattern):
    mocked_http(lambda *_: httpx.Response(200, headers={"content-type": content_type}, stream=httpx.ByteStream(body)))
    client = McpHttpClient(url="https://example.org/mcp")
    client.connect()
    with pytest.raises(McpError, match=pattern):
        client.call_tool("get", {})
    client.close()


@pytest.mark.parametrize("content_type,body,pattern", [
    ("application/json", b"x" * 90, "message limit"),
    ("text/event-stream", b"data: " + b"x" * 90 + b"\n\n", "message limit"),
    ("text/event-stream", b"x" * 90, "line exceeded"),
])
def test_http_payload_bounds(mocked_http, monkeypatch, content_type, body, pattern):
    import backend.mcp_http as module
    mocked_http(lambda *_: httpx.Response(200, headers={"content-type": content_type}, stream=httpx.ByteStream(body)))
    client = McpHttpClient(url="https://example.org/mcp")
    client.connect()
    monkeypatch.setattr(module, "MAX_HTTP_MESSAGE_BYTES", 80)
    with pytest.raises(McpError, match=pattern):
        client.call_tool("get", {})
    client.close()


@pytest.mark.parametrize("url", [
    "https://example.org/mcp", "http://127.0.0.1:8765/mcp", "http://[::1]/mcp", "http://localhost/mcp",
])
def test_valid_http_endpoints(url):
    assert validate_http_endpoint(url) == url


@pytest.mark.parametrize("url", [
    "ftp://example.org/mcp", "http://example.org/mcp", "http://192.168.1.1/mcp", "https://user:secret@example.org/mcp",
    "https://example.org/mcp#fragment", "https://example.org:invalid", "https://example.org:0", "https:///missing",
    "https://example.org/\nheader", "http://localhost.example.org/", "http://127.0.0.1.example.org/",
])
def test_unsafe_or_ambiguous_endpoints_are_rejected(url):
    with pytest.raises(McpError):
        validate_http_endpoint(url)


@pytest.mark.parametrize("headers", [
    {"Authorization": "Bearer token\r\nInjected: yes"}, {"Host": "evil.org"}, {"Content-Length": "1"},
    {"Mcp-Session-Id": "guessed"}, {"Mcp-Custom": "override"}, {"bad name": "value"}, {"X-Key": "\u0000"},
    {"X-Key": "é"}, {"X-Key": "a", "x-key": "b"}, {"Authorization": "x" * 16385},
])
def test_unsafe_authentication_headers_are_rejected(headers):
    with pytest.raises(McpError, match="header"):
        validate_http_headers(headers)


def test_explicit_header_names_and_values_survive_validation():
    headers = {"Authorization": "Bearer abc", "X-Api-Key": "key-with=signs"}
    assert validate_http_headers(headers) == headers


def test_unconnected_clients_cannot_send():
    client = McpHttpClient(url="https://example.org/mcp")
    with pytest.raises(McpError, match="not connected"):
        client.call_tool("get", {})
    with pytest.raises(McpError, match="not connected"):
        client.list_tools()


def test_client_factory_preserves_stdio_and_selects_http():
    stdio = create_mcp_client(McpServerConfig(name="local", command="test", env={"A": "secret"}))
    assert isinstance(stdio, McpStdioClient)
    assert stdio.env == {"A": "secret"}
    remote = create_mcp_client(McpServerConfig(name="remote", transport="streamable_http", url="https://example.org/mcp"))
    assert isinstance(remote, McpHttpClient)
    cfg = McpServerConfig(name="remote", id="stable", transport="streamable_http", url="https://example.org/mcp",
                          headers={"Authorization": "Bearer secret"}, env={"KEY": "secret"})
    assert McpServerConfig.from_dict(cfg.to_dict()) == cfg
    assert "secret" not in repr(cfg)


@pytest.mark.parametrize("config", [
    McpServerConfig(name="x", command="x", timeout=0), McpServerConfig(name="x", command="x", timeout=float("nan")),
    McpServerConfig(name="x", command="x", timeout=301), McpServerConfig(name="x", command="x", enabled=False),
    McpServerConfig(name="x", command="x", transport="websocket"), McpServerConfig(name="x"),
])
def test_client_factory_rejects_invalid_configuration(config):
    with pytest.raises(McpError):
        create_mcp_client(config)


def test_catalogue_pagination_preserves_metadata():
    calls = []
    def call(method, params):
        calls.append((method, params))
        if not params:
            return {"tools": [{"name": "first", "title": "A tool", "inputSchema": {"type": "object"},
                               "outputSchema": {"type": "object"},
                               "annotations": {"readOnlyHint": True, "destructiveHint": False, "other": "ignored"}}],
                    "nextCursor": "page2"}
        return {"tools": [{"name": "second"}]}
    specs = list_mcp_tools(call)
    assert [tool.name for tool in specs] == ["first", "second"]
    assert specs[0].title == "A tool"
    assert specs[0].output_schema == {"type": "object"}
    assert specs[0].annotations == {"readOnlyHint": True, "destructiveHint": False}
    assert calls == [("tools/list", {}), ("tools/list", {"cursor": "page2"})]


@pytest.mark.parametrize("response,pattern", [
    ({"tools": [{"name": "same"}, {"name": "same"}]}, "duplicate"),
    ({"tools": [], "nextCursor": "repeat"}, "repeated"),
    ({"tools": [], "nextCursor": True}, "invalid"),
    ({"tools": [], "nextCursor": "x" * 4097}, "invalid"),
])
def test_catalogue_rejects_ambiguous_pagination(response, pattern):
    with pytest.raises(McpError, match=pattern):
        list_mcp_tools(lambda *_: response)


def test_result_retains_text_structured_and_non_text_content():
    blocks = [{"type": "text", "text": "Two records"}, {"type": "resource_link", "uri": "https://example.org/data"}]
    result = parse_mcp_result({"content": blocks, "structuredContent": {"rows": [{"id": 1}, {"id": 2}]}})
    assert result.content == "Two records"
    assert result.content_blocks == tuple(blocks)
    assert result.structured_content == {"rows": [{"id": 1}, {"id": 2}]}
    assert not result.truncated


def test_oversized_structured_results_are_explicitly_unusable_as_full_json(monkeypatch):
    import backend.mcp_client as module
    monkeypatch.setattr(module, "MAX_MCP_RESULT_CHARS", 100)
    result = parse_mcp_result({"content": [], "structuredContent": {"x": "a" * 101}})
    assert result.truncated
    assert result.structured_content is None
    assert "omitted" in result.content


def test_interactive_tool_result_cannot_masquerade_as_empty_success():
    with pytest.raises(McpError, match="interactive continuation"):
        parse_mcp_result({"resultType": "input_required", "inputRequests": {"ask": {"method": "elicitation/create"}}})


def test_real_streamable_http_endpoint_runs_tool_and_preserves_auth():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((dict(self.headers), body))
            method = body["method"]
            if method == "notifications/initialized":
                self.send_response(202)
                self.end_headers()
                return
            if method == "initialize":
                result = {"protocolVersion": "2025-11-25", "serverInfo": {"name": "live-fixture"}}
            elif method == "tools/list":
                result = {"tools": [{"name": "sum", "inputSchema": {"type": "object"}}]}
            else:
                values = body["params"]["arguments"]["values"]
                result = {"content": [{"type": "text", "text": "Computed"}], "structuredContent": {"total": sum(values)}}
            wire = {"jsonrpc": "2.0", "id": body["id"], "result": result}
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if method == "tools/call" else "application/json")
            self.send_header("Mcp-Session-Id", "real-session")
            self.end_headers()
            self.wfile.write((f"data: {json.dumps(wire)}\n\n" if method == "tools/call" else json.dumps(wire)).encode())

        def do_DELETE(self):
            self.send_response(204)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = McpHttpClient(url=f"http://127.0.0.1:{server.server_port}/mcp", headers={"Authorization": "Bearer fixture"})
    try:
        client.connect()
        assert client.list_tools()[0].name == "sum"
        assert client.call_tool("sum", {"values": [2, 3]}).structured_content == {"total": 5}
        assert requests[-1][0]["Authorization"] == "Bearer fixture"
        assert requests[-1][0]["Mcp-Session-Id"] == "real-session"
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("constant,value,response,pattern", [
    ("MAX_MCP_TOOLS", 1, {"tools": [{"name": "one"}, {"name": "two"}]}, "too many tools"),
    ("MAX_MCP_SCHEMA_CHARS", 5, {"tools": [{"name": "one"}]}, "schema is too large"),
    ("MAX_MCP_CATALOGUE_CHARS", 5, {"tools": [{"name": "one"}]}, "catalogue is too large"),
    ("MAX_MCP_TOOL_PAGES", 1, {"tools": [], "nextCursor": "page2"}, "page limit"),
])
def test_catalogue_limits_are_enforced(monkeypatch, constant, value, response, pattern):
    import backend.mcp_client as module
    monkeypatch.setattr(module, constant, value)
    with pytest.raises(McpError, match=pattern):
        list_mcp_tools(lambda *_: response)


def test_invalid_json_numbers_are_not_persistable():
    with pytest.raises(McpError, match="invalid or excessively nested"):
        parse_mcp_result({"structuredContent": {"number": float("nan")}})
    with pytest.raises(McpError, match="invalid or excessively nested"):
        list_mcp_tools(lambda *_: {"tools": [{"name": "one", "inputSchema": {"minimum": float("inf")}}]})


def test_compressed_http_body_cannot_bypass_raw_byte_limits(mocked_http):
    mocked_http(lambda *_: httpx.Response(200, headers={"content-type": "application/json", "content-encoding": "gzip"},
                                          stream=httpx.ByteStream(b"unread")))
    client = McpHttpClient(url="https://example.org/mcp")
    client.connect()
    with pytest.raises(McpError, match="uncompressed"):
        client.call_tool("get", {})
    client.close()


def test_stream_budget_and_deadline_apply_even_to_notifications(mocked_http, monkeypatch):
    import backend.mcp_http as module
    raw = b": keepalive\n\n" * 100
    mocked_http(lambda *_: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=httpx.ByteStream(raw)))
    client = McpHttpClient(url="https://example.org/mcp")
    client.connect()
    monkeypatch.setattr(module, "MAX_HTTP_STREAM_BYTES", 50)
    with pytest.raises(McpError, match="stream limit"):
        client.call_tool("get", {})
    monkeypatch.setattr(module, "MAX_HTTP_STREAM_BYTES", 5000)
    ticks = iter([0.0, 1000.0])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    with pytest.raises(McpError, match="timed out"):
        client.call_tool("get", {})
    client.close()
