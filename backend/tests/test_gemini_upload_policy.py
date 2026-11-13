"""Guard Gemini resumable upload destinations without sending real network traffic.

The initial response controls X-Goog-Upload-URL. Neither the Google API key
in the first request nor private audio bytes in the second may follow an
unvalidated redirect. Fake transports exercise both optional HTTP backends.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.request import Request

import pytest

import api_provider
from provider_runtime.gemini_transport import (
    _NoGeminiUploadRedirects,
    _gemini_upload_file,
    _validated_gemini_upload_url,
)

VALID_UPLOAD = "https://generativelanguage.googleapis.com/upload/v1beta/files?upload_id=fixture"


@pytest.mark.parametrize("url", [
    "http://generativelanguage.googleapis.com/upload/v1beta/files",
    "https://generativelanguage.googleapis.com.evil.test/upload/v1beta/files",
    "https://127.0.0.1/upload/v1beta/files",
    "https://user:password@generativelanguage.googleapis.com/upload/v1beta/files",
    "https://generativelanguage.googleapis.com:8443/upload/v1beta/files",
    "https://generativelanguage.googleapis.com/elsewhere?upload_id=fixture",
    VALID_UPLOAD + "#fragment",
    " " + VALID_UPLOAD,
    VALID_UPLOAD + "\r\nX-Bad: value",
    "ftp://generativelanguage.googleapis.com/upload/v1beta/files",
    "https://generativelanguage.googleapis.com:bad/upload/v1beta/files",
])
def test_untrusted_gemini_upload_destination_is_refused(url):
    with pytest.raises(RuntimeError, match="invalid destination|untrusted destination"):
        _validated_gemini_upload_url(url)


def test_valid_google_resumable_url_keeps_its_query_unmodified():
    assert _validated_gemini_upload_url(VALID_UPLOAD) == VALID_UPLOAD


class _FakeResponse:
    def __init__(self, *, upload_url: str | None = None, status: int = 200):
        self.headers = {"X-Goog-Upload-URL": upload_url} if upload_url else {}
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps({
            "file": {"name": "files/fixture", "uri": "https://files.example/fixture",
                     "mimeType": "audio/wav"}
        }).encode("utf-8")


class _FakeOpener:
    def __init__(self, upload_url: str):
        self.upload_url = upload_url
        self.requests: list[Request] = []

    def open(self, request: Request, *, timeout: int):
        self.requests.append(request)
        return _FakeResponse(upload_url=self.upload_url if len(self.requests) == 1 else None)


def _fixture_transport(monkeypatch, *, upload_url: str):
    opener = _FakeOpener(upload_url)
    handlers = []

    def make_opener(*args):
        handlers.extend(args)
        return opener

    monkeypatch.setattr(api_provider.urllib.request, "build_opener", make_opener)
    monkeypatch.setattr(api_provider, "_get_gemini_api_key", lambda *_args: "fixture-secret")
    assert isinstance(_NoGeminiUploadRedirects(), api_provider.urllib.request.HTTPRedirectHandler)
    return opener, handlers


@pytest.mark.parametrize("use_requests", [True, False])
def test_gemini_rejects_untrusted_upload_header_before_sending_audio(
    tmp_path: Path, monkeypatch, use_requests: bool,
):
    audio = tmp_path / "safe.wav"
    audio.write_bytes(b"benign audio fixture")
    opener, _handlers = _fixture_transport(monkeypatch, upload_url="http://127.0.0.1:9000/receive")
    monkeypatch.setattr(api_provider, "REQUESTS_AVAILABLE", use_requests)
    if use_requests:
        def forbidden_post(*_args, **_kwargs):
            pytest.fail("audio must not be posted to an untrusted URL")
        monkeypatch.setattr(api_provider.requests, "post", forbidden_post)

    with pytest.raises(RuntimeError, match="untrusted destination"):
        _gemini_upload_file(str(audio), "audio/wav", api_key="fixture-secret")
    assert len(opener.requests) == 1


def test_gemini_requests_upload_refuses_redirect_without_following_it(tmp_path: Path, monkeypatch):
    audio = tmp_path / "safe.wav"
    audio.write_bytes(b"benign audio fixture")
    opener, handlers = _fixture_transport(monkeypatch, upload_url=VALID_UPLOAD)
    monkeypatch.setattr(api_provider, "REQUESTS_AVAILABLE", True)
    seen = []

    class Redirect:
        status_code = 307
        ok = True

        def json(self):
            pytest.fail("redirect body must not be parsed as success")

    def post(url, **kwargs):
        seen.append((url, kwargs["allow_redirects"], kwargs["data"].read()))
        return Redirect()

    monkeypatch.setattr(api_provider.requests, "post", post)
    with pytest.raises(RuntimeError, match="refused a redirect"):
        _gemini_upload_file(str(audio), "audio/wav", api_key="fixture-secret")

    assert seen == [(VALID_UPLOAD, False, audio.read_bytes())]
    assert len(opener.requests) == 1
    assert len(handlers) == 1 and isinstance(handlers[0], _NoGeminiUploadRedirects)


def test_gemini_urllib_fallback_uses_checked_url_and_no_redirect_opener(tmp_path: Path, monkeypatch):
    audio = tmp_path / "safe.wav"
    audio.write_bytes(b"benign audio fixture")
    opener, handlers = _fixture_transport(monkeypatch, upload_url=VALID_UPLOAD)
    monkeypatch.setattr(api_provider, "REQUESTS_AVAILABLE", False)

    result = _gemini_upload_file(str(audio), "audio/wav", api_key="fixture-secret")
    assert result == {
        "name": "files/fixture",
        "uri": "https://files.example/fixture",
        "mime_type": "audio/wav",
    }
    assert len(opener.requests) == 2
    assert opener.requests[1].full_url == VALID_UPLOAD
    assert opener.requests[1].data == audio.read_bytes()
    assert len(handlers) == 1 and isinstance(handlers[0], _NoGeminiUploadRedirects)


def test_urllib_redirect_handler_refuses_reissued_requests():
    handler = _NoGeminiUploadRedirects()
    assert handler.redirect_request(
        Request(VALID_UPLOAD, data=b"private bytes", method="POST"),
        None, 307, "Temporary Redirect",
        {"Location": "https://attacker.invalid/receive"},
        "https://attacker.invalid/receive",
    ) is None
