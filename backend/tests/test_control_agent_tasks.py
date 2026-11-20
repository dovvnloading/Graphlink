"""Write-token-scoped MCP control of the existing workspace-agent lifecycle."""

from __future__ import annotations

import asyncio
import threading

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app import create_app
from backend.control_writes import scene_revision
from backend.session_context import get_session_context

ADMIN = "test-agent-admin"
READ = "test-agent-read"
WRITE = "test-agent-write"


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def app(tmp_path):
    return create_app(
        spa_dir=tmp_path / "missing-spa",
        auth_token=ADMIN, control_read_token=READ, control_write_token=WRITE,
        chat_db_path=tmp_path / "chats.db",
        settings_state_file=tmp_path / "settings.dat",
    )


@pytest.fixture
def client(app):
    return TestClient(app, base_url="http://127.0.0.1", headers={"host": "127.0.0.1"})


def _agent(app):
    document = get_session_context(app.state.bus.session()).canvas_document
    return document.add_harness_node(0, 0, "initial task")


def _start(client, app, node_id, *, operation_id="agent-task-0001", text="Inspect the project"):
    document = get_session_context(app.state.bus.session()).canvas_document
    return client.post("/api/control/agent/task/start", headers=_auth(WRITE), json={
        "node_id": node_id, "text": text, "operation_id": operation_id,
        "expected_revision": scene_revision(document),
    })


def test_opt_in_required_even_to_read_status(tmp_path, app, client):
    node = _agent(app)
    path = "/api/control/agent/task"
    assert client.get(path, params={"node_id": node.id}, headers=_auth(READ)).status_code == 401
    assert client.get(path, params={"node_id": node.id}, headers=_auth(WRITE)).status_code == 200
    assert client.get(path, params={"node_id": node.id, "token": WRITE}).status_code == 401
    assert client.post("/api/control/agent/task/start", headers=_auth(READ), json={}).status_code == 401
    assert client.post("/api/control/agent/task/cancel", headers=_auth(READ), json={}).status_code == 401
    assert client.get("/api/control/scene/nodes", headers=_auth(WRITE)).status_code == 401
    assert client.post("/api/control/agent/task/approve", headers=_auth(WRITE), json={}).status_code == 401
    assert client.post("/api/control/agent/task/answer", headers=_auth(WRITE), json={}).status_code == 401
    assert client.post("/api/control/agent/task/start", headers={
        **_auth(WRITE), "Origin": "https://evil.example",
    }, json={}).status_code == 403
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws?token={WRITE}"):
            pass

    read_only_app = create_app(
        spa_dir=tmp_path / "other-spa",
        auth_token=ADMIN + "2", control_read_token=READ + "2",
        chat_db_path=tmp_path / "other.db", settings_state_file=tmp_path / "other-settings.dat",
    )
    no_write = TestClient(read_only_app, base_url="http://127.0.0.1",
                          headers={"host": "127.0.0.1"})
    assert no_write.get(path, headers=_auth(ADMIN + "2")).status_code == 404
    assert no_write.post("/api/control/agent/task/start",
                         headers=_auth(ADMIN + "2"), json={}).status_code == 404


def test_live_agent_task_start_status_retry_and_cancel(app, client, monkeypatch):
    node = _agent(app)
    context = get_session_context(app.state.bus.session())
    dispatcher = context.agent_dispatcher
    started = []
    claimed = []

    async def fake_start(*, harness_node_id, user_text, **unused):
        started.append((harness_node_id, user_text, unused.get("allow_session_grants")))
        handle = dispatcher._runs.claim(
            "harness", node_id=harness_node_id, cancel_event=threading.Event(),
        )
        claimed.append(handle)
        node.state.harness_run_id = handle.request_id
        node.state.harness_status = "running"
        node.state.harness_goal = user_text
        node.state.harness_reply = "Latest reply"
        node.state.harness_plan = [
            {"text": "Read files", "status": "done"},
            {"text": "Write summary", "status": "active"},
        ]
        node.state.harness_awaiting_approval = True
        node.state.harness_approval_id = "private-approval-token"
        # A real run may be parked on the app's tool approval future.
        handle.approval_future = asyncio.get_running_loop().create_future()
        handle.approval_token = "private-approval-token"
        return handle.request_id

    monkeypatch.setattr(dispatcher, "start_harness_run", fake_start)
    original_revision = scene_revision(context.canvas_document)
    payload = {
        "operation_id": "agent-task-0001", "expected_revision": original_revision,
        "node_id": node.id, "text": "Inspect the project",
    }
    first = client.post("/api/control/agent/task/start", headers=_auth(WRITE), json=payload)
    assert first.status_code == 200, first.text
    run_id = first.json()["run_id"]
    assert first.json()["accepted"] and first.json()["replayed"] is False
    assert started == [(node.id, "Inspect the project", False)]

    # Status is a bounded, live snapshot. It deliberately omits tool
    # approval IDs, approval decisions, workspace paths and transcript data.
    status = client.get("/api/control/agent/task", headers=_auth(WRITE),
                        params={"node_id": node.id, "run_id": run_id})
    assert status.status_code == 200, status.text
    data = status.json()
    assert data["source"] == "live" and data["status"] == "running"
    assert data["run_id"] == run_id
    assert data["checklist"] == [
        {"text": "Read files", "status": "done"},
        {"text": "Write summary", "status": "active"},
    ]
    assert data["reply"] == "Latest reply"
    assert data["awaiting_app_approval"] is True
    assert not any("approval_id" in key or "workspace" in key for key in data)
    assert "private-approval-token" not in status.text

    retry = client.post("/api/control/agent/task/start", headers=_auth(WRITE), json=payload)
    assert retry.status_code == 200
    assert retry.json()["replayed"] is True and retry.json()["run_id"] == run_id
    assert len(started) == 1, "retries must never launch a second run"
    changed = client.post("/api/control/agent/task/start", headers=_auth(WRITE),
                          json={**payload, "text": "different"})
    assert changed.status_code == 409
    assert _start(client, app, node.id, operation_id="agent-task-0002").status_code == 409

    assert client.get("/api/control/agent/task", headers=_auth(WRITE),
                      params={"node_id": node.id, "run_id": "stale"}).status_code == 409
    other = context.canvas_document.add_harness_node(5, 5, "different agent")
    bad_cancel = client.post("/api/control/agent/task/cancel", headers=_auth(WRITE),
                             json={"node_id": other.id, "run_id": run_id})
    assert bad_cancel.status_code == 409
    assert not claimed[0].cancel_event.is_set()

    stopped = client.post("/api/control/agent/task/cancel", headers=_auth(WRITE),
                          json={"node_id": node.id, "run_id": run_id})
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["cancel_requested"] is True
    assert claimed[0].cancel_event.is_set()
    assert claimed[0].approval_future.done()
    assert claimed[0].approval_future.result() is False, "cancel is DENY, never approval"


def test_just_accepted_task_is_visible_before_background_worker_runs(app, client, monkeypatch):
    node = _agent(app)
    dispatcher = get_session_context(app.state.bus.session()).agent_dispatcher
    claimed = []

    async def claim_without_running(*, harness_node_id, user_text, **unused):
        handle = dispatcher._runs.claim(
            "harness", node_id=harness_node_id, cancel_event=threading.Event(),
        )
        claimed.append(handle)
        # The real dispatcher only schedules run_harness here. It has NOT
        # yet written harness_run_id or status onto the node.
        return handle.request_id

    monkeypatch.setattr(dispatcher, "start_harness_run", claim_without_running)
    started = _start(client, app, node.id, operation_id="claim-first-0001")
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    status = client.get("/api/control/agent/task", headers=_auth(WRITE),
                        params={"node_id": node.id, "run_id": run_id})
    assert status.status_code == 200, status.text
    assert status.json()["run_id"] == run_id
    assert status.json()["status"] == "running"
    assert node.pending_request_id == run_id
    cancelled = client.post("/api/control/agent/task/cancel", headers=_auth(WRITE),
                            json={"node_id": node.id, "run_id": run_id})
    assert cancelled.status_code == 200, cancelled.text
    assert claimed[0].cancel_event.is_set()


def test_agent_node_and_payload_validation_prevents_approval_or_workspace_grants(app, client):
    node = _agent(app)
    document = get_session_context(app.state.bus.session()).canvas_document
    non_agent = document.add_chat_node(20, 0, "not an agent", is_user=True)
    assert client.get("/api/control/agent/task", headers=_auth(WRITE),
                      params={"node_id": non_agent.id}).status_code == 404
    assert client.get("/api/control/agent/task", headers=_auth(WRITE),
                      params={"node_id": "missing"}).status_code == 404
    assert _start(client, app, non_agent.id).status_code == 404
    assert _start(client, app, node.id, text="   ").status_code == 422
    base = {
        "node_id": node.id, "text": "Run task",
        "operation_id": "no-approval-0001", "expected_revision": scene_revision(document),
    }
    for injection in (
        {"approve_tool": True}, {"workspace_path": "/etc"},
        {"model_ref": "unreviewed"}, {"approval_id": "forged"},
        {"allow_session_grants": True},
    ):
        assert client.post("/api/control/agent/task/start", headers=_auth(WRITE),
                           json={**base, **injection}).status_code == 422
    assert client.post("/api/control/agent/task/cancel", headers=_auth(WRITE), json={
        "node_id": node.id, "run_id": "none", "approve": True,
    }).status_code == 422
    assert node.state.harness_status == "idle"
    assert not get_session_context(app.state.bus.session()).agent_dispatcher._runs.is_busy("harness")


def test_task_reply_is_bounded_and_canvas_reload_invalidates_receipt(app, client, monkeypatch):
    node = _agent(app)
    ctx = get_session_context(app.state.bus.session())
    node.state.harness_reply = "x" * 20_000
    payload = client.get("/api/control/agent/task", headers=_auth(WRITE),
                         params={"node_id": node.id}).json()
    assert len(payload["reply"]) == 16_000 and payload["reply_truncated"] is True

    async def fake_start(*, harness_node_id, **kwargs):
        node.state.harness_run_id = "test-run-1"
        node.state.harness_status = "done"
        return "test-run-1"

    monkeypatch.setattr(ctx.agent_dispatcher, "start_harness_run", fake_start)
    expected = scene_revision(ctx.canvas_document)
    body = {
        "node_id": node.id, "text": "task", "operation_id": "reload-0001",
        "expected_revision": expected,
    }
    assert client.post("/api/control/agent/task/start",
                       headers=_auth(WRITE), json=body).status_code == 200
    ctx.canvas_document.clear_for_load()
    # The old operation ID must NOT succeed against a different canvas.
    # A new harness node may even reuse the same node ID after reload.
    ctx.canvas_document.add_harness_node(0, 0, "replacement")
    retry = client.post("/api/control/agent/task/start",
                        headers=_auth(WRITE), json=body)
    assert retry.status_code in (404, 409)


def test_mcp_control_run_cannot_inherit_ui_approved_session_grants(app, monkeypatch):
    """End-to-end: the real dispatcher passes the restrictive flag to the run."""
    from backend.harness import loop as harness_loop

    node = _agent(app)
    context = get_session_context(app.state.bus.session())
    context.agent_dispatcher.harness_session_grants(node.state.harness_workspace_id).add("fs.write")
    observed = []
    ran = threading.Event()

    async def capture_run(**kwargs):
        observed.append(kwargs["allow_session_grants"])
        ran.set()

    monkeypatch.setattr(harness_loop, "run_harness", capture_run)
    # Keep TestClient's async portal alive until the scheduled worker executes;
    # don't rely on a later unrelated request to flush background tasks.
    with TestClient(app, base_url="http://127.0.0.1",
                    headers={"host": "127.0.0.1"}) as active_client:
        accepted = _start(active_client, app, node.id, operation_id="no-inherited-grants-0001")
        assert accepted.status_code == 200, accepted.text
        assert ran.wait(timeout=2.0), "scheduled workspace run never entered"
    assert observed == [False]
    # Do not clear an app-approved grant: only this MCP-origin run is isolated.
    assert "fs.write" in context.agent_dispatcher.harness_session_grants(
        node.state.harness_workspace_id
    )
