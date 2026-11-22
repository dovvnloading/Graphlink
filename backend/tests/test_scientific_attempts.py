"""Same-socket start/stop and retry semantics through the shipped WebSocket handler."""
import asyncio
import itertools
import json
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.session_context import get_session_context
from backend.tests.test_scientific_execution_budget import FAST, HELD, _no_science_workers, _wait_for_marker
from backend.tests.test_scientific_workbench import envelope, session as _science_session
from graphlink_plugins.scientific import workbench

session = _science_session


def test_same_socket_cancel_retry_and_status_after_reconnect(session, tmp_path, monkeypatch):
    # Use isolated manifests with the same SDK, app, dispatcher and spawn worker as production.
    _, _, registry, _ = session
    monkeypatch.setattr("backend.plugins.discover_plugins", lambda: registry)
    monkeypatch.setattr(workbench, "operations", lambda pack: (FAST, HELD) if pack == "core" else ())
    monkeypatch.setenv("GRAPHLINK_SCIENTIFIC_TIMEOUT_SECONDS", "45")
    app = create_app(auth_token="test-scientific-attempt", chat_db_path=tmp_path / "chats.db",
                     settings_state_file=tmp_path / "settings.dat")
    message_ids = itertools.count(1)

    def call(ws, name, args):
        msg_id = next(message_ids)
        ws.send_json({"kind": "intent", "id": msg_id, "topic": "app-plugins", "intent": name, "args": args})
        while True:
            frame = ws.receive_json()
            if frame.get("id") == msg_id:
                assert frame["kind"] == "result", frame
                return frame["value"]

    def invoke(ws, action, args):
        return call(ws, "invokePluginIntent", ["science_core", action, args])

    def terminal(ws, identity):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            response = invoke(ws, "status_attempt", identity)["attempt"]
            if response["status"] not in ("running", "cancelling"):
                return response
            time.sleep(0.01)
        raise AssertionError("Scientific worker did not report a terminal state")

    url = "ws://127.0.0.1/ws?token=test-scientific-attempt"
    with TestClient(app) as client:
        with client.websocket_connect(url) as socket:
            call(socket, "setPluginGrant", ["science_core", True])
            node_id = call(socket, "executePlugin", ["Scientific Core"])
            document = get_session_context(app.state.bus.session("default")).canvas_document
            assert invoke(socket, "run", {"node_id": node_id, "operation_id": "quick",
                                         "inputs": {"quantity": 5}}) == {"ok": True}
            original = envelope(document, node_id)["latest"]
            undo_count = len(document.command_log)
            attempt = {"node_id": node_id, "attempt_id": uuid4().hex}
            marker = tmp_path / "worker-started"
            args = {**attempt, "operation_id": "held", "inputs": {"marker": str(marker)},
                    "expected_run": envelope(document, node_id)["runToken"]}
            assert invoke(socket, "start_attempt", args)["attempt"]["status"] == "running"
            asyncio.run(_wait_for_marker(marker))
            assert invoke(socket, "start_attempt", args)["attempt"]["attemptId"] == attempt["attempt_id"]
            assert invoke(socket, "start_attempt", {**args, "inputs": {"marker": "other"}})["errorType"] == "conflict"
            assert invoke(socket, "start_attempt", {**args, "attempt_id": uuid4().hex})["errorType"] == "busy"
            assert invoke(socket, "cancel", {"node_id": node_id, "operation_id": "held"})["errorType"] == "not_running"
            assert invoke(socket, "cancel_attempt", attempt)["attempt"]["status"] == "cancelling"
            assert terminal(socket, attempt)["errorType"] == "cancelled"
            assert envelope(document, node_id)["latest"] == original
            assert len(document.command_log) == undo_count
            assert _no_science_workers()

            later = {"node_id": node_id, "attempt_id": uuid4().hex}
            later_marker = tmp_path / "second-held-worker"
            assert invoke(socket, "start_attempt", {**args, **later, "inputs": {"marker": str(later_marker)}})["ok"]
            asyncio.run(_wait_for_marker(later_marker))
            assert invoke(socket, "cancel_attempt", attempt)["attempt"]["status"] == "cancelled"
            assert invoke(socket, "status_attempt", later)["attempt"]["status"] == "running"
            assert invoke(socket, "cancel_attempt", later)["ok"]
            assert terminal(socket, later)["status"] == "cancelled"

            next_id = {"node_id": node_id, "attempt_id": uuid4().hex}
            next_args = {**next_id, "operation_id": "quick", "inputs": {"quantity": 11},
                         "expected_run": args["expected_run"]}
            # Disconnect after sending Start, before receiving its acknowledgement.
            socket.send_json({"kind": "intent", "id": next(message_ids), "topic": "app-plugins",
                              "intent": "invokePluginIntent", "args": ["science_core", "start_attempt", next_args]})
        with client.websocket_connect(url) as restored:
            result = terminal(restored, next_id)
            assert result["status"] == "succeeded"
            assert invoke(restored, "cancel_attempt", attempt)["attempt"]["status"] == "cancelled"
            assert invoke(restored, "start_attempt", next_args)["attempt"] == result
            state = envelope(document, node_id)
            assert state["attempt"] == result
            assert result["receiptHash"] == state["latest"]["provenance"]["receiptHash"]
            assert state["latest"]["values"]["quantity (m)"] == 11
            assert len(document.command_log) == undo_count + 1
            assert "attempt" not in json.dumps(document.nodes[node_id].state.data)


def test_attempt_history_is_bounded_and_scoped_to_live_node_objects(monkeypatch):
    from backend.domain.model import SceneNode
    from graphlink_plugins.scientific import attempts

    clock = [0.0]
    monkeypatch.setattr(attempts, "ATTEMPT_LIMIT", 2)
    monkeypatch.setattr(attempts, "time", type("Clock", (), {
        "monotonic": lambda: clock[0], "time": lambda: 1_800_000_000.0,
    }))
    ledger = attempts.AttemptLedger()
    first = SceneNode(id="n1", kind="science_core.workbench", x=0, y=0, title="First")
    second = SceneNode(id="n1", kind="science_core.workbench", x=0, y=0, title="Other workspace")
    identity = uuid4().hex
    old = ledger.reserve(first, identity, "op", "original")
    pending = ledger.reserve(second, identity, "op", "separate")
    old.finish({"ok": False, "errorType": "cancelled", "error": "Stopped"})
    assert ledger.get(first, identity) is old and ledger.get(second, identity) is pending
    with pytest.raises(ValueError, match="full"):
        ledger.reserve(first, uuid4().hex, "op", "new")
    clock[0] = attempts.RETENTION_SECONDS + 1
    assert ledger.get(first, identity) is None
    assert ledger.current(second) is pending
    assert ledger.reserve(first, uuid4().hex, "op", "new") is ledger.current(first)
