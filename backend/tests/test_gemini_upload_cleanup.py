"""Regression coverage for partial Gemini Files API uploads (issue #1004)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

import api_provider
import graphlink_task_config as config
from backend.providers import CancelToken, ChatRequest, GeminiProvider
from backend.providers import gemini_provider


def _audio_request(tmp_path: Path, count: int, *, missing_last=False):
    parts = []
    for index in range(count):
        path = tmp_path / f"audio-{index}.wav"
        if not (missing_last and index == count - 1):
            path.write_bytes(b"audio fixture")
        parts.append({
            "type": "audio_file",
            "path": str(path),
            "name": path.name,
            "mime_type": "audio/wav",
        })
    return ChatRequest(task=config.TASK_TITLE, messages=[{"role": "user", "content": parts}])


def _invoke(mode, request, cancel):
    provider = GeminiProvider(api_key="fixture-key", model="gemini-2.5-pro")
    if mode == "complete":
        return provider.complete(request, cancel)
    return list(provider.stream(request, cancel))


def _mock_deletes(monkeypatch, deleted, *, fail_first=False):
    def delete(name, *, api_key=None):
        deleted.append((name, api_key))
        if fail_first and name == "files/0":
            raise RuntimeError("delete failed")

    # Preparation uses the late-bound api_provider seam; the provider's
    # normal post-generation finally uses its imported binding.
    monkeypatch.setattr(api_provider, "_gemini_delete_file", delete)
    monkeypatch.setattr(gemini_provider, "_gemini_delete_file", delete)


def _forbid_generation(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("generation must not start after failed preparation")

    monkeypatch.setattr(gemini_provider, "_gemini_post_json", forbidden)
    monkeypatch.setattr(gemini_provider, "_gemini_stream_sse", forbidden)


@pytest.mark.parametrize("mode", ["complete", "stream"])
@pytest.mark.parametrize("failure", ["upload", "validation", "cancel"])
def test_failed_multi_audio_preparation_deletes_first_upload(tmp_path, monkeypatch, mode, failure):
    request = _audio_request(tmp_path, 2, missing_last=failure == "validation")
    cancel = CancelToken(threading.Event())
    uploaded = []
    deleted = []

    def upload(path, mime_type, *_args, cancel_event=None, **_kwargs):
        uploaded.append(Path(path).name)
        if len(uploaded) == 2:
            raise RuntimeError("second upload failed")
        if failure == "cancel":
            cancel_event.set()
        return {"name": "files/0", "uri": "https://files.example/0", "mime_type": mime_type}

    monkeypatch.setattr(api_provider, "_gemini_upload_file", upload)
    _mock_deletes(monkeypatch, deleted)
    _forbid_generation(monkeypatch)

    if failure == "cancel":
        with pytest.raises(api_provider.RequestCancelledError):
            _invoke(mode, request, cancel)
    else:
        match = "second upload failed" if failure == "upload" else "Attached audio file is no longer available"
        with pytest.raises(RuntimeError, match=match):
            _invoke(mode, request, cancel)

    assert uploaded == (["audio-0.wav", "audio-1.wav"] if failure == "upload" else ["audio-0.wav"])
    assert deleted == [("files/0", "fixture-key")]


@pytest.mark.parametrize("mode", ["complete", "stream"])
def test_failed_rollback_preserves_original_error_and_attempts_all_deletes(tmp_path, monkeypatch, mode):
    request = _audio_request(tmp_path, 3)
    deleted = []
    error = RuntimeError("third upload failed")

    def upload(path, mime_type, *_args, **_kwargs):
        index = Path(path).stem.removeprefix("audio-")
        if index == "2":
            raise error
        return {"name": f"files/{index}", "uri": f"https://files.example/{index}", "mime_type": mime_type}

    monkeypatch.setattr(api_provider, "_gemini_upload_file", upload)
    _mock_deletes(monkeypatch, deleted, fail_first=True)
    _forbid_generation(monkeypatch)

    with pytest.raises(RuntimeError) as exc:
        _invoke(mode, request, CancelToken())

    assert exc.value is error
    assert deleted == [("files/0", "fixture-key"), ("files/1", "fixture-key")]


@pytest.mark.parametrize("mode", ["complete", "stream"])
def test_successful_preparation_keeps_uploads_until_generation_finishes(tmp_path, monkeypatch, mode):
    request = _audio_request(tmp_path, 2)
    deleted = []

    def upload(path, mime_type, *_args, **_kwargs):
        index = Path(path).stem.removeprefix("audio-")
        return {"name": f"files/{index}", "uri": f"https://files.example/{index}", "mime_type": mime_type}

    def generate(_endpoint, body, **_kwargs):
        assert deleted == []
        assert body["contents"][0]["parts"] == [
            {"file_data": {"mime_type": "audio/wav", "file_uri": f"https://files.example/{index}"}}
            for index in range(2)
        ]
        return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "ok"}]}}]}

    def stream(*args, **kwargs):
        yield generate(*args, **kwargs)

    monkeypatch.setattr(api_provider, "_gemini_upload_file", upload)
    _mock_deletes(monkeypatch, deleted)
    monkeypatch.setattr(gemini_provider, "_gemini_post_json", generate)
    monkeypatch.setattr(gemini_provider, "_gemini_stream_sse", stream)

    result = _invoke(mode, request, CancelToken())
    if mode == "complete":
        assert result == "ok"
    else:
        assert result[-1].type == "done" and result[-1].text == "ok"
    assert deleted == [("files/0", "fixture-key"), ("files/1", "fixture-key")]
