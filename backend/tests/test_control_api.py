"""Real read-only control routes: authorization, DB reads, and live canvas.

Every TestClient has its own settings and chat database under tmp_path. The
control API MUST NOT write to a graph or load it into the current session.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.chat_library import get_all_workspaces, save_chat_atomically_row
from backend.session_context import get_session_context

TOKEN = "test-control-capability"


@pytest.fixture
def control_app(tmp_path):
    return create_app(
        spa_dir=tmp_path / "missing-spa",
        auth_token=TOKEN,
        chat_db_path=tmp_path / "chats.db",
        settings_state_file=tmp_path / "session.dat",
    )


@pytest.fixture
def api(control_app):
    return TestClient(
        control_app, base_url="http://127.0.0.1",
        headers={"host": "127.0.0.1", "Authorization": f"Bearer {TOKEN}"},
    )


def test_control_routes_require_authentication_and_origin_allowlist(control_app):
    client = TestClient(control_app, base_url="http://127.0.0.1", headers={"host": "127.0.0.1"})
    for path in (
        "/api/control/workspaces", "/api/control/graphs", "/api/control/graphs/1",
        "/api/control/scene/nodes", "/api/control/scene/subgraph?root_id=abc",
    ):
        assert client.get(path).status_code == 401, path
        assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401, path
        result = client.get(path, headers={
            "Authorization": f"Bearer {TOKEN}", "Origin": "https://untrusted.example",
        })
        assert result.status_code == 403, path


def test_control_list_workspaces_and_saved_graphs_are_paged(api, tmp_path):
    db = tmp_path / "chats.db"
    default = get_all_workspaces(db)[0]
    from backend.chat_library import create_workspace

    other = create_workspace(db, "Research")
    for workspace_id, title in (
        (default["id"], "In default"),
        (other["id"], "First"),
        (other["id"], "Second"),
    ):
        save_chat_atomically_row(db, None, title, {"nodes": []}, [], [], workspace_id=workspace_id)

    workspaces = api.get("/api/control/workspaces")
    assert workspaces.status_code == 200
    assert {w["name"] for w in workspaces.json()["workspaces"]} == {"Default", "Research"}

    response = api.get("/api/control/graphs", params={
        "workspace_id": other["id"], "limit": 1, "offset": 1,
    })
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 2 and page["returned"] == 1 and page["more"] is False
    assert {row["title"] for row in page["graphs"]}.issubset({"First", "Second"})
    assert api.get("/api/control/graphs", params={"limit": 101}).status_code == 422


def test_get_saved_graph_is_bounded_and_does_not_load_the_live_canvas(api, control_app, tmp_path):
    db = tmp_path / "chats.db"
    graph_id, _ = save_chat_atomically_row(
        db, None, "Saved research",
        {"nodes": [
            {"id": "n1", "node_type": "chat", "raw_content": "evidence alpha", "is_user": True},
            {"id": "n2", "node_type": "code", "code": "print(1)"},
        ], "edges": [{"source": "n1", "target": "n2"}]},
        [], [],
    )
    document = get_session_context(control_app.state.bus.session()).canvas_document
    assert len(document.nodes) == 0

    first = api.get(f"/api/control/graphs/{graph_id}", params={"limit": 1})
    assert first.status_code == 200
    data = first.json()
    assert data["source"] == "saved"
    assert data["graph"]["id"] == graph_id
    assert data["graph"]["title"] == "Saved research"
    assert data["totalNodes"] == 2
    assert data["nodes"][0]["excerpt"] == "evidence alpha"
    assert data["returned"] == 1 and data["more"] is True
    assert api.get(f"/api/control/graphs/{graph_id}", params={"offset": 1}).json()["nodes"][0]["id"] == "n2"
    assert api.get("/api/control/graphs/999999").status_code == 404
    assert len(document.nodes) == 0, "reading a saved graph must never replace the current canvas"


def test_live_graph_tools_read_the_same_session_as_the_ui(api, control_app):
    document = get_session_context(control_app.state.bus.session()).canvas_document
    parent = document.add_chat_node(0, 0, "parent research", is_user=True)
    child = document.add_chat_node(100, 0, "linked evidence", is_user=False)
    document.connect(parent.id, child.id)

    listing = api.get("/api/control/scene/nodes", params={"query": "evidence"}).json()
    assert listing["source"] == "live"
    assert listing["total"] == 1
    assert listing["nodes"][0]["id"] == child.id

    branch = api.get("/api/control/scene/subgraph", params={
        "root_id": parent.id, "direction": "down", "depth": 2,
    })
    assert branch.status_code == 200
    assert {n["id"] for n in branch.json()["nodes"]} == {parent.id, child.id}
    assert {"source": parent.id, "target": child.id} in branch.json()["edges"]
    assert api.get("/api/control/scene/subgraph", params={"root_id": "unknown"}).status_code == 400
    assert api.get("/api/control/scene/subgraph", params={
        "root_id": parent.id, "direction": "sideways",
    }).status_code == 422
    assert len(document.nodes) == 2


def test_live_canvas_is_not_the_saved_graph_inventory(api, control_app):
    document = get_session_context(control_app.state.bus.session()).canvas_document
    document.add_chat_node(0, 0, "unsaved draft", is_user=True)
    assert api.get("/api/control/scene/nodes").json()["total"] == 1
    assert api.get("/api/control/graphs").json()["total"] == 0
