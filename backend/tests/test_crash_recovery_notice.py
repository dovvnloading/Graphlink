"""Integration test (Qt-removal plan R6.7): create_app's previous_run_crashed
flag actually reaches a real WS session's notification topic. Runs the real
ASGI app through Starlette's TestClient, mirroring test_app_ws.py's own
make_client() pattern (fresh temp settings/chat-db paths, never the real
~/.graphlink) with one addition: previous_run_crashed=True."""

import logging
import tempfile
from pathlib import Path

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

import backend.app as app_module
from backend.app import create_app
from backend.crash_recovery import CRASH_NOTICE_MESSAGE

# R7.2: api_provider/graphlink_task_config sit at the repo root, a sibling
# of backend/ - already importable, no ordering constraint.
import api_provider
import graphlink_task_config as config


def _client(previous_run_crashed: bool) -> TestClient:
    state_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    state_path = Path(state_dir.name)
    client = TestClient(
        create_app(
            spa_dir=Path("__no_such_dir__"),
            settings_state_file=state_path / "session.dat",
            chat_db_path=state_path / "chats.db",
            previous_run_crashed=previous_run_crashed,
        ),
        # ADR-004 stage 4.2: see backend/tests/test_app_ws.py's make_client
        # for why BOTH kwargs are required.
        base_url="http://127.0.0.1",
        headers={"host": "127.0.0.1"},
    )
    client._state_tmpdir = state_dir  # type: ignore[attr-defined]
    return client


def test_a_crashed_previous_run_surfaces_the_notice_on_first_subscribe():
    # ADR-004 stage 4.3: session id is "default" - each test here gets its
    # own fresh, isolated _client()/EventBus, so there is no cross-test
    # collision to avoid by using a distinct id, and "default" is the only
    # id the real (restrict_sessions=True by default) policy ever issues.
    client = _client(previous_run_crashed=True)
    with client.websocket_connect("/ws?session=default") as ws:
        ws.send_json({"kind": "subscribe", "topics": ["notification"]})
        message = ws.receive_json()

        assert message["kind"] == "state"
        assert message["topic"] == "notification"
        assert message["payload"]["visible"] is True
        assert message["payload"]["msgType"] == "warning"
        assert message["payload"]["message"] == CRASH_NOTICE_MESSAGE


def test_a_clean_previous_run_shows_no_notice():
    client = _client(previous_run_crashed=False)
    with client.websocket_connect("/ws?session=default") as ws:
        ws.send_json({"kind": "subscribe", "topics": ["notification"]})
        message = ws.receive_json()

        assert message["payload"]["visible"] is False


def test_the_notice_can_still_be_dismissed_like_any_other_notification():
    client = _client(previous_run_crashed=True)
    with client.websocket_connect("/ws?session=default") as ws:
        ws.send_json({"kind": "subscribe", "topics": ["notification"]})
        ws.receive_json()  # the initial crash-notice snapshot

        ws.send_json({"kind": "intent", "topic": "notification", "intent": "dismiss", "args": []})
        message = ws.receive_json()

        assert message["topic"] == "notification"
        assert message["payload"]["visible"] is False


def test_a_session_setup_bug_is_logged_via_this_apps_own_logger_and_closed_cleanly(monkeypatch, caplog):
    # Adversarial-review regression test: a bug in any of _configure_session's
    # register_X calls used to vanish entirely - it never becomes a
    # Python-level unhandled exception (uvicorn's own ASGI machinery catches
    # it first and logs it via "uvicorn.error", a logger that does NOT
    # propagate to the root logger backend/crash_recovery.py's
    # RotatingFileHandler is attached to), so it landed in neither
    # graphlink.log nor sys.excepthook - directly contradicting this
    # increment's own point. This proves the fix: backend/app.py's ws_endpoint
    # now catches it itself and logs via its OWN logger (which DOES
    # propagate to root), then closes the connection with code 1011 instead
    # of leaving it to uvicorn's default handling.
    def _boom(bus):
        raise RuntimeError("register_about exploded")

    monkeypatch.setattr(app_module, "register_about", _boom)

    # ADR-004 stage 4.3: "default" - a non-default id here would now be
    # rejected by the separate UnknownSessionError branch before ever
    # reaching _configure_session, so _boom would never fire and this
    # test would stop exercising the setup-failure path it exists to
    # cover.
    client = _client(previous_run_crashed=False)
    with caplog.at_level(logging.ERROR, logger="backend.app"):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws?session=default"):
                pass
        assert exc_info.value.code == 1011

    assert any(
        "session setup failed" in record.message and "default" in record.message
        for record in caplog.records
    )
