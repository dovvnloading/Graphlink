"""Graphlink Fal Media Studio preview must not weaken img-src CSP or SSRF guards."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.app import CONTENT_SECURITY_POLICY, create_app
from backend.domain.node_states import FalMediaState
from backend.fal_media_domain import completed_media, fresh_media, store_media
from backend.fal_media_preview import _image_bytes, safe_media_host
from backend.session_context import get_session_context

TEST_TOKEN = "fal-media-preview-secret"


class FakeResponse:
    def __init__(self, payload: bytes, *, mime="image/png", status=200, length=None):
        self.payload = payload
        self.status_code = status
        self.headers = {"Content-Type": mime}
        if length is not None:
            self.headers["Content-Length"] = length

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self.payload), chunk_size):
            yield self.payload[i:i+chunk_size]


def make_client():
    directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    root = Path(directory.name)
    client = TestClient(
        create_app(auth_token=TEST_TOKEN,
                   settings_state_file=root / "session.dat",
                   chat_db_path=root / "chats.db"),
        base_url="http://127.0.0.1",
        headers={"host": "127.0.0.1", "Authorization": f"Bearer {TEST_TOKEN}"},
    )
    client._fal_tempdir = directory  # type: ignore[attr-defined]
    return client


def create_completed_image(client, url="https://v3.fal.media/files/result.png"):
    doc = get_session_context(client.app.state.bus.session("default")).canvas_document
    node = doc.add_plugin_node("fal_media", 10, 10, None,
                               title="Fal Media Studio", state=FalMediaState())
    state = fresh_media()
    state.update({"modelId": "fal-ai/example", "mode": "image", "prompt": "mountains",
                  "requestId": "764cabcf-b745-4b3e-ae38-1200304cf45b"})
    done = completed_media(state, {"images": [{"url": url}]})
    store_media(node, done)
    return node


@pytest.mark.parametrize("url", [
    "http://v3.fal.media/result.png",
    "https://evil.com/result.png",
    "https://fal.media.attacker.invalid/result.png",
    "https://fal.media@127.0.0.1/private",
    "https://127.0.0.1/media.png",
    "https://v3.fal.media:444/file.png",
    "https://v3.fal.media\\@evil.example/result.png",
    "file:///etc/passwd",
    "https://[::1]/private",
])
def test_untrusted_remote_urls_cannot_be_fetched(url):
    assert safe_media_host(url) is False


def test_only_fal_media_hosts_are_accepted():
    assert safe_media_host("https://v3.fal.media/files/sample.png") is True
    assert safe_media_host("https://fal.media/example.png") is True


def test_preview_is_auth_gated_and_keeps_original_csp(monkeypatch):
    client = make_client()
    node = create_completed_image(client)
    media = b"\x89PNG\r\n\x1a\nexample"
    with patch("backend.fal_media_preview.requests.get", return_value=FakeResponse(media)) as fake_get:
        response = client.get(f"/api/fal-media/preview/{node.id}/0/0")
    assert response.status_code == 200
    assert response.content == media
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert fake_get.call_args.kwargs["allow_redirects"] is False
    assert "img-src 'self' data:" == CONTENT_SECURITY_POLICY

    without_auth = client.get(f"/api/fal-media/preview/{node.id}/0/0",
                              headers={"host": "127.0.0.1", "Authorization": ""})
    assert without_auth.status_code in (401, 403)


def test_preview_rejects_non_fal_host_without_opening_socket():
    client = make_client()
    node = create_completed_image(client, "https://attacker.example/data.png")
    with patch("backend.fal_media_preview.requests.get") as fake_get:
        response = client.get(f"/api/fal-media/preview/{node.id}/0/0")
    assert response.status_code == 404
    fake_get.assert_not_called()


def test_missing_node_and_index_rejected_before_network():
    client = make_client()
    node = create_completed_image(client)
    with patch("backend.fal_media_preview.requests.get") as fake_get:
        assert client.get("/api/fal-media/preview/nonexistent/0/0").status_code == 404
        assert client.get(f"/api/fal-media/preview/{node.id}/10/0").status_code == 404
        assert client.get(f"/api/fal-media/preview/{node.id}/0/10").status_code == 404
    fake_get.assert_not_called()


def test_svg_html_and_redirect_response_must_never_be_rendered_inline():
    for mime, status in [("image/svg+xml", 200), ("text/html", 200), ("image/png", 302)]:
        with patch("backend.fal_media_preview.requests.get",
                   return_value=FakeResponse(b"<script>bad</script>", mime=mime, status=status)):
            with pytest.raises(ValueError):
                _image_bytes("https://v3.fal.media/files/unsafe.svg")


def test_huge_content_length_is_rejected_without_downloading():
    with patch("backend.fal_media_preview.requests.get",
               return_value=FakeResponse(b"", length=str(21 * 1024 * 1024))):
        with pytest.raises(ValueError, match="20 MiB"):
            _image_bytes("https://v3.fal.media/files/huge.png")


def test_bad_origin_and_host_cannot_read_fal_preview_even_with_token():
    client = make_client()
    node = create_completed_image(client)
    path = f"/api/fal-media/preview/{node.id}/0/0"
    response = client.get(path, headers={
        "host": "attacker.example", "Authorization": f"Bearer {TEST_TOKEN}",
    })
    assert response.status_code == 400
    foreign = client.get(path, headers={
        "host": "127.0.0.1",
        "Authorization": f"Bearer {TEST_TOKEN}",
        "Origin": "https://attacker.example",
    })
    assert foreign.status_code in (401, 403)
