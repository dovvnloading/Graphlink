"""Real headless launch -> MCP stdio -> authenticated operator WebSocket consent.

The browser launch is intercepted only to capture the exact URL a local
browser would receive; the backend, loopback listener, MCP subprocess and
WebSocket intent path are real. A deterministic agent replaces the external
LLM/tool call so no provider or network permission is needed in CI.
"""

from __future__ import annotations

import asyncio
import json
import queue
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from websockets.sync.client import connect

from backend import headless
from graphlink_process_env import safe_subprocess_env


def _mcp_tool(port: int, read_file: Path, write_file: Path, name: str, args: dict):
    messages = [
        {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {}},
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": name, "arguments": args}},
    ]
    proc = subprocess.run(
        [
            sys.executable, "-m", "backend.mcp_server",
            "--url", f"http://127.0.0.1:{port}",
            "--token-file", str(read_file), "--write-token-file", str(write_file),
        ],
        input="\n".join(json.dumps(item) for item in messages) + "\n",
        text=True, capture_output=True, encoding="utf-8", timeout=20,
        cwd=Path(__file__).resolve().parents[2], env=safe_subprocess_env(),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    replies = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [item["id"] for item in replies] == [1, 2]
    result = replies[1]["result"]
    assert result["isError"] is False, result["content"]
    return result["structuredContent"]


def _operator_intent(ws, request_id: int, intent: str, args: list):
    ws.send(json.dumps({
        "kind": "intent", "topic": "harness", "intent": intent,
        "args": args, "id": request_id,
    }))
    while True:
        packet = json.loads(ws.recv(timeout=10))
        if packet.get("id") == request_id and packet["kind"] in ("result", "error"):
            assert packet["kind"] == "result", packet
            return packet["value"]


def test_real_headless_mcp_start_and_operator_approval_round_trip(tmp_path, monkeypatch):
    """A UI-created Agent stays on the live canvas and human consent stays on /ws."""
    import uvicorn
    from backend.harness import loop as harness_loop

    browser_urls: queue.Queue[str] = queue.Queue()
    servers: queue.Queue[uvicorn.Server] = queue.Queue()
    outcomes = []

    # Real browser navigation would load this fragment into token.ts; capture
    # it so this smoke test can exercise the same authenticated WS connection
    # without requiring a platform GUI or a configured model provider.
    def open_browser(url, **_kwargs):
        browser_urls.put(url)
        return True

    monkeypatch.setattr(webbrowser, "open", open_browser)
    real_server = uvicorn.Server

    def capture_server(config):
        server = real_server(config)
        servers.put(server)
        return server

    monkeypatch.setattr(uvicorn, "Server", capture_server)

    async def simulated_agent(
        *, document, harness_node_id, user_text, request_id, handle, bus, **_kwargs,
    ):
        node = document.nodes[harness_node_id]
        if user_text == "Create operator Agent":
            # The normal app's harness/start intent creates the node; its
            # first (test-only) run finishes before MCP begins another.
            node.state.harness_status = "done"
            node.pending_request_id = None
            await bus.publish("scene")
            return

        assert user_text == "Needs human approval"
        approval_id = "operator-consent-for-this-run"
        handle.approval_future = asyncio.get_running_loop().create_future()
        handle.approval_token = approval_id
        node.state.harness_awaiting_approval = True
        node.state.harness_approval_id = approval_id
        node.state.harness_approval_tool_name = "fs.write"
        node.state.harness_approval_summary = "Overwrite a report (simulated)"
        await bus.publish("scene")
        approved = await asyncio.wait_for(handle.approval_future, timeout=10)
        node.state.harness_awaiting_approval = False
        node.state.harness_approval_id = ""
        node.state.harness_status = "done"
        node.state.harness_reply = "Approved by the operator" if approved else "Denied"
        node.pending_request_id = None
        await bus.publish("scene")

    monkeypatch.setattr(harness_loop, "run_harness", simulated_agent)

    read_file = tmp_path / "read.token"
    write_file = tmp_path / "write.token"

    def start_backend():
        try:
            outcomes.append(headless.main([
                "--port", "0", "--chat-db", str(tmp_path / "chats.db"),
                "--settings-file", str(tmp_path / "session.dat"),
                "--token-file", str(read_file), "--write-token-file", str(write_file),
                "--allow-mcp-writes", "--open-ui",
            ]))
        except BaseException as exc:
            outcomes.append(exc)

    thread = threading.Thread(target=start_backend, daemon=True)
    thread.start()
    server = None
    try:
        server = servers.get(timeout=15)
        operator_url = urlsplit(browser_urls.get(timeout=15))
        port = operator_url.port
        admin_token = parse_qs(operator_url.fragment)["token"][0]
        assert operator_url.scheme == "http"
        assert operator_url.hostname == "127.0.0.1"
        assert operator_url.path == "/"
        assert port and admin_token
        assert read_file.read_text(encoding="utf-8").strip() != admin_token
        assert write_file.read_text(encoding="utf-8").strip() != admin_token
        assert read_file.read_text(encoding="utf-8") != write_file.read_text(encoding="utf-8")

        # Neither restricted MCP credential can connect to the app's WS,
        # approve a tool, or create a harness node.
        with pytest.raises(Exception):
            with connect(
                f"ws://127.0.0.1:{port}/ws?token={write_file.read_text().strip()}",
                open_timeout=3,
            ) as denied:
                denied.recv(timeout=3)

        with connect(f"ws://127.0.0.1:{port}/ws?token={admin_token}", open_timeout=5) as ws:
            ws.send(json.dumps({"kind": "subscribe", "topics": ["scene"], "id": 10}))
            initial = json.loads(ws.recv(timeout=5))
            assert initial["kind"] == "state" and initial["topic"] == "scene"

            node_id = _operator_intent(ws, 11, "start", ["Create operator Agent"])
            assert isinstance(node_id, str) and node_id

            # The first run has finished and released its registry slot.
            # The MCP adapter is a distinct process: it cannot seed a node.
            deadline = time.monotonic() + 10
            while True:
                listing = _mcp_tool(port, read_file, write_file, "graphlink_list_nodes", {})
                if any(node["id"] == node_id for node in listing["nodes"]):
                    break
                assert time.monotonic() < deadline
                time.sleep(0.05)
            time.sleep(0.1)

            started = _mcp_tool(port, read_file, write_file, "graphlink_start_agent_task", {
                "node_id": node_id, "text": "Needs human approval",
                "operation_id": "operator-consent-0001",
                "expected_revision": listing["revision"],
            })
            assert started["accepted"] is True
            run_id = started["run_id"]

            # Read the approval ID from the same scene data the real browser
            # renders. The MCP status intentionally excludes this secret.
            deadline = time.monotonic() + 10
            approval_id = None
            while approval_id is None:
                assert time.monotonic() < deadline
                packet = json.loads(ws.recv(timeout=5))
                if packet.get("topic") != "scene":
                    continue
                if packet["kind"] == "state":
                    nodes = packet["payload"]["nodes"]
                else:
                    nodes = [
                        op["node"] for op in packet.get("ops", [])
                        if op["op"] == "upsertNode"
                    ]
                for node in nodes:
                    if node["id"] == node_id and node.get("harnessAwaitingApproval"):
                        approval_id = node["harnessApprovalId"]
                        break
            assert approval_id
            pending = _mcp_tool(port, read_file, write_file, "graphlink_get_agent_task", {
                "node_id": node_id, "run_id": run_id,
            })
            assert pending["awaiting_app_approval"] is True
            assert "approval_id" not in pending
            assert approval_id not in json.dumps(pending)

            _operator_intent(ws, 12, "approveTool", [run_id, approval_id])
            deadline = time.monotonic() + 10
            while True:
                status = _mcp_tool(port, read_file, write_file, "graphlink_get_agent_task", {
                    "node_id": node_id, "run_id": run_id,
                })
                if status["status"] == "done":
                    break
                assert time.monotonic() < deadline
                time.sleep(0.05)
            assert status["reply"] == "Approved by the operator"
            assert status["awaiting_app_approval"] is False
    finally:
        if server is not None:
            server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive(), "headless launch did not shut down"
        assert outcomes == [0], outcomes
        assert not read_file.exists() and not write_file.exists()
