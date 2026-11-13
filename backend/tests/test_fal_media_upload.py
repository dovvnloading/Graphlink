"""Fal Media Studio's direct upload route: auth, no WS blob, no path traversal."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.fal_media_upload import MAX_UPLOAD_BYTES, _suffix
from graphlink_settings_store import SettingsManager

TOKEN = "fal-studio-upload-test-token"


def make_client(*, with_key=True):
    temp = TemporaryDirectory(ignore_cleanup_errors=True)
    folder = Path(temp.name)
    state = folder / "session.dat"
    if with_key:
        SettingsManager(state).set_fal_api_key("fake-fal-key")
    app = create_app(
        auth_token=TOKEN,
        settings_state_file=state,
        chat_db_path=folder / "chats.db",
    )
    client = TestClient(app, base_url="http://127.0.0.1",
                        headers={"host": "127.0.0.1",
                                 "Authorization": f"Bearer {TOKEN}"})
    client._fal_temp = temp  # type: ignore[attr-defined]
    return client


class FakeUploader:
    calls = []

    def __init__(self, key):
        assert key == "fake-fal-key"

    def upload_file(self, path):
        p = Path(path)
        assert p.is_file()
        assert p.name.endswith(".png")
        assert p.read_bytes() == b"\x89PNG\r\n\x1a\nexample bytes"
        self.calls.append(str(p))
        return "https://v3.fal.media/files/studio-image.png"


def test_upload_is_authenticated_and_discards_local_file():
    client = make_client()
    FakeUploader.calls.clear()
    with patch("backend.fal_media_upload.FalClient", FakeUploader):
        response = client.post(
            "/api/fal-media/upload",
            content=b"\x89PNG\r\n\x1a\nexample bytes",
            headers={"X-Fal-Media-Filename": "image.png",
                     "Content-Type": "image/png"},
        )
    assert response.status_code == 200
    assert response.json()["url"] == "https://v3.fal.media/files/studio-image.png"
    assert response.json()["bytes"] == len(b"\x89PNG\r\n\x1a\nexample bytes")
    assert response.headers["cache-control"] == "no-store"
    assert FakeUploader.calls
    assert not Path(FakeUploader.calls[0]).exists(), "Upload temp file must be removed after request"


def test_upload_requires_existing_fal_key():
    client = make_client(with_key=False)
    response = client.post(
        "/api/fal-media/upload", content=b"hi",
        headers={"X-Fal-Media-Filename": "recording.wav"},
    )
    assert response.status_code == 400
    assert "API key" in response.json()["error"]


def test_missing_capability_token_is_rejected_before_upload():
    client = make_client()
    with patch("backend.fal_media_upload.FalClient") as provider:
        response = client.post(
            "/api/fal-media/upload",
            content=b"secret",
            headers={"host": "127.0.0.1", "Authorization": "",
                     "X-Fal-Media-Filename": "recording.wav"},
        )
    assert response.status_code in (401, 403)
    provider.assert_not_called()


@pytest.mark.parametrize("name", [
    "../secrets.png", "C:\\Users\\admin\\secrets.png", "script.exe",
    "document.html", ".git", "", "bad%0Afile.jpg", "video%2Fclip.mp4",
])
def test_path_injection_and_executable_extensions_are_rejected(name):
    assert _suffix(name) is None


def test_unicode_filename_only_passes_safe_extension():
    assert _suffix(quote("風景の動画.mp4")) == ".mp4"
    assert _suffix("reference-flower.jpeg") == ".jpeg"


def test_unsupported_extension_rejected_pre_provider():
    client = make_client()
    with patch("backend.fal_media_upload.FalClient") as provider:
        response = client.post(
            "/api/fal-media/upload", content=b"binary",
            headers={"X-Fal-Media-Filename": "shell-script.sh"},
        )
    assert response.status_code == 415
    provider.assert_not_called()


def test_huge_content_length_is_rejected_without_file_creation():
    client = make_client()
    with patch("backend.fal_media_upload.FalClient") as provider:
        response = client.post(
            "/api/fal-media/upload", content=b"short",
            headers={"X-Fal-Media-Filename": "clip.mp4",
                     "Content-Length": str(MAX_UPLOAD_BYTES + 1)},
        )
    assert response.status_code == 413
    provider.assert_not_called()


def test_upload_does_not_accept_foreign_origin():
    client = make_client()
    with patch("backend.fal_media_upload.FalClient") as provider:
        response = client.post(
            "/api/fal-media/upload", content=b"bin",
            headers={"Origin": "https://attacker.example",
                     "X-Fal-Media-Filename": "photo.png"},
        )
    assert response.status_code in (401, 403)
    provider.assert_not_called()
