"""Opt-in MCP writes must not bypass auth, revision checks, or normal Save.

The default read-only app remains unaffected. These tests use the real
FastAPI app, SceneDocument handlers and SQLite chat library; the control
surface must never expose arbitrary intents or execute code.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app import create_app
from backend.chat_library import get_all_chats, load_chat_row
from backend.session_context import get_session_context
from backend.control_writes import scene_revision
from backend.domain.graph import SceneDocument

ADMIN = "test-full-admin-credential"
READ = "test-mcp-readonly-credential"
WRITE = "test-opt-in-writer-credential"


@pytest.fixture
def backend(tmp_path):
    app = create_app(
        spa_dir=tmp_path / "no-spa",
        auth_token=ADMIN,
        control_read_token=READ,
        control_write_token=WRITE,
        chat_db_path=tmp_path / "chats.db",
        settings_state_file=tmp_path / "session.dat",
    )
    client = TestClient(app, base_url="http://127.0.0.1", headers={"host": "127.0.0.1"})
    return client, app, tmp_path / "chats.db"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _revision(client: TestClient) -> str:
    resp = client.get("/api/control/scene/nodes", headers=_auth(READ))
    assert resp.status_code == 200
    rev = resp.json()["revision"]
    assert re.fullmatch(r"[0-9a-f]{64}", rev)
    return rev


def _edit(client: TestClient, revision: str, operation: str, args: dict, nonce: str):
    return client.post("/api/control/scene/mutate", headers=_auth(WRITE), json={
        "operation_id": nonce, "expected_revision": revision,
        "operation": operation, "arguments": args,
    })


def test_revision_binds_to_scene_lifetime_even_when_canvas_contents_match():
    # A restarted backend has an empty scene again; a pre-restart revision
    # must NEVER match and allow the same create-node retry to execute.
    first_process_scene = SceneDocument()
    restarted_scene = SceneDocument()
    assert first_process_scene.scene_payload() == restarted_scene.scene_payload()
    first_revision = scene_revision(first_process_scene)
    assert scene_revision(first_process_scene) == first_revision
    assert scene_revision(restarted_scene) != first_revision


def test_new_chat_clear_invalidates_revision_even_if_scene_is_equally_empty():
    document = SceneDocument()
    before = scene_revision(document)
    original_payload = document.scene_payload()
    document.clear_for_load()
    assert document.scene_payload() == original_payload
    assert scene_revision(document) != before


def test_writes_off_by_default_and_bad_token_configuration_fails_closed(tmp_path):
    app = create_app(
        auth_token=ADMIN, control_read_token=READ,
        spa_dir=tmp_path / "no-spa", chat_db_path=tmp_path / "chats.db",
        settings_state_file=tmp_path / "session.dat",
    )
    client = TestClient(app, base_url="http://127.0.0.1", headers={"host": "127.0.0.1"})
    dummy = {"operation_id": "attempt-0001", "expected_revision": "0" * 64,
             "operation": "create_node", "arguments": {"kind": "chat", "content": "x"}}
    assert client.post("/api/control/scene/mutate", headers=_auth(ADMIN), json=dummy).status_code == 404
    assert client.post("/api/control/scene/mutate", headers=_auth(READ), json=dummy).status_code == 401

    for token in ("", ADMIN, READ):
        with pytest.raises(ValueError, match="write token"):
            create_app(
                auth_token=ADMIN, control_read_token=READ, control_write_token=token,
                spa_dir=tmp_path / "no-spa", chat_db_path=tmp_path / "chats.db",
                settings_state_file=tmp_path / "session.dat",
            )


def test_write_token_is_scoped_to_only_two_post_routes(backend):
    client, _, _ = backend
    for path in (
        "/api/health", "/api/control/workspaces", "/api/control/scene/nodes",
        "/api/control/graphs", "/api/control/private-future-route",
    ):
        assert client.get(path, headers=_auth(WRITE)).status_code == 401, path
    assert client.post("/api/control/scene/unsafe", headers=_auth(WRITE)).status_code == 401
    assert client.delete("/api/control/scene/mutate", headers=_auth(WRITE)).status_code == 401
    assert client.get("/api/control/scene/nodes", params={"token": WRITE}).status_code == 401
    assert client.post("/api/control/scene/mutate", headers={
        **_auth(WRITE), "Origin": "https://evil.example",
    }).status_code == 403
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws?token={WRITE}"):
            pass


def test_create_connect_update_and_guarded_save_round_trip(backend):
    client, app, db = backend
    observed = _revision(client)
    first = _edit(client, observed, "create_node", {
        "kind": "chat", "content": "hypothesis A", "is_user": True,
    }, "create-hypothesis-A")
    assert first.status_code == 200, first.text
    node_id = first.json()["result"]["node_id"]
    assert first.json()["saved"] is False

    # A lost HTTP response may be retried with the SAME request ID.
    duplicate = _edit(client, first.json()["revision"], "create_node", {
        "kind": "chat", "content": "hypothesis A", "is_user": True,
    }, "create-hypothesis-A")
    # Different expected revision with the same ID is a conflict rather
    # than silently accepting a different request.
    assert duplicate.status_code == 409

    original_revision = _revision(client)
    second = _edit(client, original_revision, "create_node", {
        "kind": "chat", "content": "evidence B",
    }, "create-evidence-B")
    assert second.status_code == 200, second.text
    second_id = second.json()["result"]["node_id"]

    connection = _edit(client, second.json()["revision"], "connect_nodes", {
        "source_id": node_id, "target_id": second_id,
    }, "connect-A-B-0001")
    assert connection.status_code == 200, connection.text

    changed = _edit(client, connection.json()["revision"], "set_node_content", {
        "node_id": node_id, "content": "revised hypothesis A",
    }, "update-hypothesis-A-0001")
    assert changed.status_code == 200, changed.text

    save = client.post("/api/control/scene/save", headers=_auth(WRITE), json={
        "operation_id": "save-research-0001",
        "expected_revision": changed.json()["revision"],
    })
    assert save.status_code == 200, save.text
    assert save.json()["saved"] is True
    chat_id = save.json()["graph_id"]
    assert isinstance(chat_id, int)
    stored = load_chat_row(db, chat_id)
    assert "revised hypothesis A" in str(stored["data"])
    assert "evidence B" in str(stored["data"])
    assert len(get_all_chats(db)) == 1
    scene = get_session_context(app.state.bus.session()).canvas_document
    assert len(scene.nodes) == 2
    assert len(scene.edges) == 1


def test_idempotent_replay_returns_prior_result_and_never_duplicates(backend):
    client, app, _ = backend
    revision = _revision(client)
    args = {"kind": "note", "content": "original"}
    first = _edit(client, revision, "create_node", args, "create-note-100")
    assert first.status_code == 200, first.text
    second = _edit(client, revision, "create_node", args, "create-note-100")
    assert second.status_code == 200, second.text
    assert second.json()["replayed"] is True
    assert first.json()["result"] == second.json()["result"]
    assert len(get_session_context(app.state.bus.session()).canvas_document.nodes) == 1
    reused = _edit(client, revision, "create_node", {
        "kind": "note", "content": "DIFFERENT",
    }, "create-note-100")
    assert reused.status_code == 409
    assert "DIFFERENT" not in str(get_session_context(app.state.bus.session()).canvas_document.scene_payload())


def test_receipt_replay_is_refused_after_loading_another_canvas(backend):
    client, app, _ = backend
    observed = _revision(client)
    args = {"kind": "note", "content": "belongs to the old canvas"}
    first = _edit(client, observed, "create_node", args, "canvas-lifetime-create-001")
    assert first.status_code == 200, first.text
    document = get_session_context(app.state.bus.session()).canvas_document
    assert len(document.nodes) == 1

    document.clear_for_load()
    assert not document.nodes
    # The same request fingerprint and operation ID must NOT replay a
    # success referring to a node absent from this newly loaded canvas.
    replay = _edit(client, observed, "create_node", args, "canvas-lifetime-create-001")
    assert replay.status_code == 409
    assert not document.nodes


def test_receipt_for_save_does_not_replay_on_a_different_canvas(backend):
    client, app, _ = backend
    first = _edit(client, _revision(client), "create_node", {
        "kind": "chat", "content": "old canvas",
    }, "canvas-lifetime-create-002")
    assert first.status_code == 200
    body = {"operation_id": "canvas-lifetime-save-001", "expected_revision": first.json()["revision"]}
    saved = client.post("/api/control/scene/save", headers=_auth(WRITE), json=body)
    assert saved.status_code == 200, saved.text

    get_session_context(app.state.bus.session()).canvas_document.clear_for_load()
    replay = client.post("/api/control/scene/save", headers=_auth(WRITE), json=body)
    assert replay.status_code == 409


def test_stale_revision_and_busy_guard_reject_without_side_effects(backend):
    client, app, _ = backend
    rev = _revision(client)
    document = get_session_context(app.state.bus.session()).canvas_document
    document.add_chat_node(1, 2, "UI edit", is_user=True)
    assert _edit(client, rev, "create_node", {
        "kind": "chat", "content": "should not appear",
    }, "stale-mutate-001").status_code == 409
    assert len(document.nodes) == 1
    active_revision = _revision(client)
    guard = app.state.bus.session().chat_mutation_guard
    guard["active"] = True
    guard["owner"] = "user"
    try:
        assert _edit(client, active_revision, "create_node", {
            "kind": "chat", "content": "busy",
        }, "busy-mutate-001").status_code == 409
    finally:
        guard["active"] = False
        guard["owner"] = None
    assert len(document.nodes) == 1


@pytest.mark.parametrize("args", [
    {"kind": "image", "content": "x"},
    {"kind": "chat", "content": "x", "unexpected": True},
    {"kind": "chat", "content": "x" * 20001},
    {"kind": "chat", "is_user": "True"},
    {"kind": ["chat"], "content": "invalid kind type"},
    {"kind": {"name": "chat"}, "content": "invalid kind type"},
    {"kind": "document", "title": "missing parent"},
])
def test_invalid_mutations_are_rejected_before_domain_change(backend, args):
    client, app, _ = backend
    before = _revision(client)
    result = _edit(client, before, "create_node", args, "invalid-graph-op-001")
    assert result.status_code == 422
    assert _revision(client) == before
    assert not get_session_context(app.state.bus.session()).canvas_document.nodes


def test_guarded_save_failures_never_report_success(backend, monkeypatch):
    import backend.chat_library as library

    client, _, db = backend
    first = _edit(client, _revision(client), "create_node", {
        "kind": "chat", "content": "pending unsaved change",
    }, "save-fail-create-001")
    assert first.status_code == 200
    revision = first.json()["revision"]
    original = library.save_chat_atomically_row

    def fail(*args, **kwargs):
        raise OSError("injected disk failure")

    monkeypatch.setattr(library, "save_chat_atomically_row", fail)
    attempt = client.post("/api/control/scene/save", headers=_auth(WRITE), json={
        "operation_id": "save-failure-0001", "expected_revision": revision,
    })
    assert attempt.status_code == 409
    assert not get_all_chats(db)
    monkeypatch.setattr(library, "save_chat_atomically_row", original)
    again = client.post("/api/control/scene/save", headers=_auth(WRITE), json={
        "operation_id": "save-failure-0001", "expected_revision": revision,
    })
    assert again.status_code == 200, again.text
    assert get_all_chats(db)


def test_expected_revision_blocks_duplicate_save_retries(backend):
    client, _, db = backend
    first = _edit(client, _revision(client), "create_node", {
        "kind": "chat", "content": "retry-safe",
    }, "retry-create-001")
    assert first.status_code == 200
    body = {
        "operation_id": "retry-save-0001",
        "expected_revision": first.json()["revision"],
    }
    initial = client.post("/api/control/scene/save", headers=_auth(WRITE), json=body)
    assert initial.status_code == 200, initial.text
    replay = client.post("/api/control/scene/save", headers=_auth(WRITE), json=body)
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert len(get_all_chats(db)) == 1
    different_id = client.post("/api/control/scene/save", headers=_auth(WRITE), json={
        "operation_id": "retry-save-0002",
        "expected_revision": body["expected_revision"],
    })
    assert different_id.status_code == 409


def test_write_scope_rejects_editing_html_or_other_executable_node_kinds(backend):
    client, app, _ = backend
    document = get_session_context(app.state.bus.session()).canvas_document
    parent = document.add_chat_node(0, 0, "parent", is_user=True)
    html = document.add_html_node(20, 0, "<p>original safe markup</p>", parent.id)
    result = _edit(client, _revision(client), "set_node_content", {
        "node_id": html.id, "content": "<script>window.evil()</script>",
    }, "no-executable-html-001")
    assert result.status_code == 403
    assert "window.evil" not in document.nodes[html.id].content


def test_write_token_cannot_skip_permission_via_query_or_bad_method(backend):
    client, _, _ = backend
    rev = _revision(client)
    body = {"operation_id": "token-bypass-001", "expected_revision": rev,
            "operation": "create_node", "arguments": {"kind": "chat", "content": "x"}}
    assert client.post("/api/control/scene/mutate", params={"token": WRITE}, json=body).status_code == 401
    assert client.post("/api/control/scene/mutate", headers=_auth(READ), json=body).status_code == 401
    assert client.post("/api/control/scene/mutate", headers=_auth(WRITE), json={
        **body, "expected_revision": "not-a-revision",
    }).status_code == 422
