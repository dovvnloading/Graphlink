"""FastAPI app tests (Qt-removal plan R0): health endpoint, WS handshake,
subscribe snapshots, the system/ping acceptance round-trip, and error paths.
Runs the real ASGI app through Starlette's TestClient - no network, no Qt."""

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from backend import BACKEND_VERSION
from backend.app import create_app


def make_client(tmp_path: Path | None = None) -> TestClient:
    # Point spa_dir at a guaranteed-missing directory: R0 tests exercise the
    # API surface, not the static build (the acceptance drive covers that).
    spa = tmp_path if tmp_path is not None else Path("__no_such_dir__")
    # R2.5d/e: create_app() now builds a real SettingsManager and a real
    # chats.db path - always point both at a fresh temp dir so tests never
    # read or mutate the developer's actual ~/.graphlink/session.dat or
    # ~/.graphlink/chats.db.
    temp_dir = Path(tempfile.mkdtemp())
    return TestClient(
        create_app(
            spa_dir=spa,
            settings_state_file=temp_dir / "session.dat",
            chat_db_path=temp_dir / "chats.db",
        )
    )


def test_health_reports_ok_and_version():
    client = make_client()
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == BACKEND_VERSION


def test_subscribe_delivers_system_snapshot_with_envelope():
    client = make_client()
    with client.websocket_connect("/ws?session=test-a") as ws:
        ws.send_json({"kind": "subscribe", "topics": ["system"]})
        message = ws.receive_json()
        assert message["kind"] == "state"
        assert message["topic"] == "system"
        payload = message["payload"]
        assert payload["app"] == "graphlink"
        assert payload["sessionId"] == "test-a"
        assert payload["schemaVersion"] == 1
        assert payload["revision"] >= 1


def test_subscribe_without_topics_sends_every_registered_topic():
    client = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"kind": "subscribe"})
        # R2 surface: canvas + View-popover + composer/counter/notification +
        # R2.5 about/plugins/settings/chat-library topics, sorted.
        topics = [ws.receive_json()["topic"] for _ in range(12)]
        assert topics == [
            "app-about",
            "app-chat-library",
            "app-composer",
            "app-plugins",
            "app-settings",
            "drag-speed",
            "font-control",
            "grid-control",
            "notification",
            "scene",
            "system",
            "token-counter",
        ]


def test_ping_round_trip_returns_echo_and_server_time():
    client = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {"kind": "intent", "topic": "system", "intent": "ping", "args": ["hello"], "id": 1}
        )
        message = ws.receive_json()
        assert message["kind"] == "result"
        assert message["id"] == 1
        assert message["value"]["echo"] == ["hello"]
        assert message["value"]["serverTime"] > 0


def test_unknown_intent_and_topic_return_error_not_disconnect():
    client = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"kind": "intent", "topic": "system", "intent": "nope", "args": [], "id": 2})
        message = ws.receive_json()
        assert message["kind"] == "error"
        assert message["id"] == 2

        ws.send_json({"kind": "intent", "topic": "nope", "intent": "x", "args": [], "id": 3})
        message = ws.receive_json()
        assert message["kind"] == "error"

        # Socket must still be usable after errors.
        ws.send_json({"kind": "intent", "topic": "system", "intent": "ping", "args": [], "id": 4})
        assert ws.receive_json()["kind"] == "result"


def test_unknown_message_kind_returns_error():
    client = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"kind": "bogus", "id": 9})
        message = ws.receive_json()
        assert message["kind"] == "error"
        assert "unknown message kind" in message["error"]


def test_sessions_do_not_share_connections():
    client = make_client()
    with client.websocket_connect("/ws?session=a") as ws_a:
        with client.websocket_connect("/ws?session=b") as ws_b:
            ws_a.send_json({"kind": "subscribe", "topics": ["system"]})
            ws_b.send_json({"kind": "subscribe", "topics": ["system"]})
            assert ws_a.receive_json()["payload"]["sessionId"] == "a"
            assert ws_b.receive_json()["payload"]["sessionId"] == "b"
