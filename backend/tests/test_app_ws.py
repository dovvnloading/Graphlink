"""FastAPI app tests (Qt-removal plan R0): health endpoint, WS handshake,
subscribe snapshots, the system/ping acceptance round-trip, and error paths.
Runs the real ASGI app through Starlette's TestClient - no network, no Qt."""

import tempfile
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend import BACKEND_VERSION
from backend.app import create_app

# Importing any backend.* submodule (above) runs backend/__init__.py first,
# which puts graphlink_app/ on sys.path - these bare top-level imports must
# come after it, same ordering rule backend/tests/test_agents.py documents.
import api_provider
import graphlink_task_config as config


def make_client(tmp_path: Path | None = None) -> TestClient:
    # Point spa_dir at a guaranteed-missing directory: R0 tests exercise the
    # API surface, not the static build (the acceptance drive covers that).
    spa = tmp_path if tmp_path is not None else Path("__no_such_dir__")
    # R2.5d/e: create_app() now builds a real SettingsManager and a real
    # chats.db path - always point both at a fresh temp dir so tests never
    # read or mutate the developer's actual ~/.graphlink/session.dat or
    # ~/.graphlink/chats.db. TemporaryDirectory (not mkdtemp) so its
    # finalizer removes the dir when the client is garbage collected instead
    # of accumulating litter in %TEMP% run after run; pinned to the client
    # so it lives exactly as long as the app that writes into it.
    state_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    state_path = Path(state_dir.name)
    client = TestClient(
        create_app(
            spa_dir=spa,
            settings_state_file=state_path / "session.dat",
            chat_db_path=state_path / "chats.db",
        )
    )
    client._state_tmpdir = state_dir  # type: ignore[attr-defined]
    return client


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


def test_disconnect_cancels_any_in_flight_chat_request(monkeypatch):
    # R4 concurrency-review finding: a client that sends a message and then
    # closes its tab must not leave the real outbound LLM call running
    # server-side forever with no way to ever cancel it. ws_endpoint's
    # disconnect handler should trip the session's AgentDispatcher cancel
    # event once its last connection drops - this exercises that through the
    # real ASGI app, not just AgentDispatcher in isolation (test_agents.py
    # already covers cancel()/cancel_all() unit-level).
    call_started = threading.Event()

    def fake_chat(task, messages, cancellation_event=None, **kwargs):
        call_started.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if cancellation_event is not None and cancellation_event.is_set():
                raise api_provider.RequestCancelledError("cancelled")
            time.sleep(0.01)
        raise AssertionError("cancel_event was never set after disconnect")

    # make_client() -> create_app() runs bootstrap_provider_state() against
    # a fresh (unconfigured) SettingsManager - monkeypatching BEFORE that
    # would just get overwritten, so the client comes first.
    client = make_client()
    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_OLLAMA)
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, "test-model")
    monkeypatch.setattr(api_provider, "chat", fake_chat)

    with client.websocket_connect("/ws?session=cancel-test") as ws:
        ws.send_json({"kind": "subscribe", "topics": ["scene"]})
        ws.receive_json()  # initial scene snapshot
        ws.send_json({"kind": "intent", "topic": "scene", "intent": "sendMessage", "args": ["hello"]})
        ws.receive_json()  # scene republish after the user node is created

        deadline = time.monotonic() + 5
        while not call_started.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert call_started.is_set(), "fake_chat never started - dispatch did not fire"

        session = client.app.state.bus.session("cancel-test")
        in_flight = list(session.agent_dispatcher._requests.values())
        assert len(in_flight) == 1
        cancel_event = in_flight[0]["cancel_event"]
        assert not cancel_event.is_set()
    # Exiting the `with` block closes the websocket, running ws_endpoint's
    # finally - this is the disconnect this test exists to exercise.

    deadline = time.monotonic() + 5
    while not cancel_event.is_set() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert cancel_event.is_set()
