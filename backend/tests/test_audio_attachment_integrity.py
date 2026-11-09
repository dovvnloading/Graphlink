"""Issue #534: staged audio cannot grow or change unnoticed before upload."""

import hashlib

import pytest

from backend import attachments
from provider_runtime.media import _read_attachment_bytes


def test_staged_audio_is_bound_to_exact_content(tmp_path, monkeypatch):
    path = tmp_path / "clip.wav"
    original = b"RIFF-test-audio"
    path.write_bytes(original)
    monkeypatch.setattr(
        attachments, "inspect_audio_file",
        lambda _path: {"duration_seconds": 1.0, "mime_type": "audio/wav"},
    )
    staged = attachments.stage_file(str(path))
    assert staged.content_part["sha256"] == hashlib.sha256(original).hexdigest()
    assert _read_attachment_bytes(str(path), "audio", staged.content_part["sha256"]) == original

    path.write_bytes(b"RIFF-other-data")
    with pytest.raises(RuntimeError, match="changed since"):
        _read_attachment_bytes(str(path), "audio", staged.content_part["sha256"])


def test_provider_reader_rejects_growth_without_unbounded_allocation(tmp_path, monkeypatch):
    monkeypatch.setattr(attachments, "MAX_STAGED_FILE_BYTES", 8)
    path = tmp_path / "clip.wav"
    path.write_bytes(b"12345678")
    assert _read_attachment_bytes(str(path), "audio") == b"12345678"
    path.write_bytes(b"123456789")
    with pytest.raises(RuntimeError, match="size limit"):
        _read_attachment_bytes(str(path), "audio")


def test_gemini_rejects_oversized_audio_before_opening_network(tmp_path, monkeypatch):
    import api_provider
    from provider_runtime.gemini_transport import _gemini_upload_file

    monkeypatch.setattr(attachments, "MAX_STAGED_FILE_BYTES", 8)
    path = tmp_path / "clip.wav"
    path.write_bytes(b"123456789")
    monkeypatch.setattr(api_provider, "_get_gemini_api_key", lambda value: "dummy")
    monkeypatch.setattr(
        api_provider.urllib.request, "urlopen",
        lambda *args, **kwargs: pytest.fail("network called for rejected audio"),
    )
    with pytest.raises(RuntimeError, match="size limit"):
        _gemini_upload_file(str(path), "audio/wav", api_key="dummy")
