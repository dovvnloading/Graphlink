"""Asset-serving route tests (Qt-removal plan R3.21): GET /api/assets/{id}
against the real ASGI app through FastAPI's TestClient - no network, no Qt.

Image nodes are created directly through the session's SceneDocument (not
through the WS layer) so these tests stay focused on the HTTP route itself;
the WS addImageNode intent (base64 decode -> real node) is already covered
in backend/tests/test_canvas.py."""

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app


def make_client(tmp_path: Path | None = None) -> TestClient:
    # Same isolation convention as test_app_ws.py's make_client: a fresh temp
    # dir per client so tests never touch the developer's real
    # ~/.graphlink/session.dat or ~/.graphlink/chats.db.
    spa = tmp_path if tmp_path is not None else Path("__no_such_dir__")
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


def test_get_asset_returns_exact_bytes_and_stored_mime_type():
    client = make_client()
    bus = client.app.state.bus
    document = bus.session("default").canvas_document
    parent = document.add_node(0, 0, "parent")
    node = document.add_image_node(
        0, 0, b"\x89PNG\r\n\x1a\nsome raw image bytes", "a test image", parent.id, mime_type="image/png"
    )

    response = client.get(f"/api/assets/{node.image_asset_id}")

    assert response.status_code == 200
    assert response.content == b"\x89PNG\r\n\x1a\nsome raw image bytes"
    assert response.headers["content-type"] == "image/png"


def test_get_asset_respects_the_mime_type_it_was_stored_with():
    client = make_client()
    bus = client.app.state.bus
    document = bus.session("default").canvas_document
    parent = document.add_node(0, 0, "parent")
    node = document.add_image_node(0, 0, b"jpeg-ish bytes", "prompt", parent.id, mime_type="image/jpeg")

    response = client.get(f"/api/assets/{node.image_asset_id}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"


def test_get_asset_for_unknown_id_returns_404_json():
    client = make_client()

    response = client.get("/api/assets/nope-not-real")

    assert response.status_code == 404
    assert response.json() == {"error": "unknown asset"}


def test_get_asset_scopes_by_session_query_param():
    client = make_client()
    bus = client.app.state.bus
    document_a = bus.session("session-a").canvas_document
    parent = document_a.add_node(0, 0, "parent")
    node = document_a.add_image_node(0, 0, b"session-a bytes", "prompt", parent.id)

    # The default session has no such asset.
    default_response = client.get(f"/api/assets/{node.image_asset_id}")
    assert default_response.status_code == 404

    # The session it was actually created under does.
    scoped_response = client.get(f"/api/assets/{node.image_asset_id}?session=session-a")
    assert scoped_response.status_code == 200
    assert scoped_response.content == b"session-a bytes"


def test_asset_ids_do_not_collide_across_sessions_with_identical_creation_order():
    # Regression: asset ids must be globally unique, not just unique within
    # one SceneDocument. Two sessions performing the identical sequence of
    # operations (one parent node, then one image node) used to mint the
    # exact same "imgN" id from each document's own fresh counter - not a
    # rare fluke, the deterministic median case. That let a request missing
    # or mis-supplying its session query param be silently served a
    # different session's real image instead of a 404.
    client = make_client()
    bus = client.app.state.bus

    document_a = bus.session("session-a").canvas_document
    parent_a = document_a.add_node(0, 0, "parent")
    node_a = document_a.add_image_node(0, 0, b"session-a bytes", "prompt", parent_a.id)

    document_b = bus.session("session-b").canvas_document
    parent_b = document_b.add_node(0, 0, "parent")
    node_b = document_b.add_image_node(0, 0, b"session-b bytes", "prompt", parent_b.id)

    assert node_a.image_asset_id != node_b.image_asset_id

    response_a = client.get(f"/api/assets/{node_a.image_asset_id}?session=session-b")
    assert response_a.status_code == 404

    response_b = client.get(f"/api/assets/{node_b.image_asset_id}?session=session-a")
    assert response_b.status_code == 404
