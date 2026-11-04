"""SECURITY-FIX: _extract_openai_image_bytes validates a provider-returned
image URL through the audited web_research FetchPolicy before downloading it.

The OpenAI-compatible base_url is user-configurable, so an image-generation
response is untrusted network input (threat boundary (d)). urllib.request.
urlopen installs the file://, data: and ftp: handlers by default, so an
unchecked `url` field in that response could read an arbitrary LOCAL file or
reach an internal host (SSRF), with the bytes then persisted and served
back. These tests pin that the scheme/private-address checks now run first.
"""

import base64
from types import SimpleNamespace

import pytest

import api_provider


def _response_with_url(url):
    # Mirrors the OpenAI images response shape _extract_openai_image_bytes
    # reads: response.data[0].url (b64_json absent so the URL branch runs).
    return SimpleNamespace(data=[SimpleNamespace(b64_json=None, url=url)])


def test_a_file_url_is_refused_before_any_local_read(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")

    with pytest.raises(RuntimeError, match="blocked by fetch policy"):
        api_provider._extract_openai_image_bytes(_response_with_url(secret.as_uri()))


def test_a_data_url_is_refused():
    data_url = "data:image/png;base64," + base64.b64encode(b"hello").decode("ascii")
    with pytest.raises(RuntimeError, match="blocked by fetch policy"):
        api_provider._extract_openai_image_bytes(_response_with_url(data_url))


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x.png",          # loopback, and http scheme
        "http://169.254.169.254/latest/",  # cloud metadata
        "https://127.0.0.1/x.png",         # loopback over https
        "ftp://example.com/x.png",         # non-http(s) scheme
    ],
)
def test_private_or_non_https_urls_are_refused(url):
    with pytest.raises(RuntimeError, match="blocked by fetch policy"):
        api_provider._extract_openai_image_bytes(_response_with_url(url))


def test_a_b64_json_response_never_touches_the_network_or_the_policy():
    # The common OpenAI path returns inline base64 and must still work with
    # no URL validation involved at all.
    payload = base64.b64encode(b"PNGBYTES").decode("ascii")
    resp = SimpleNamespace(data=[SimpleNamespace(b64_json=payload)])
    assert api_provider._extract_openai_image_bytes(resp) == b"PNGBYTES"



class _FakeImageResponse:
    def __init__(self, *, status=200, location=None, chunks=None, content_length=None):
        self.status_code = status
        self.headers = {}
        if location is not None:
            self.headers["Location"] = location
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        self._chunks = chunks if chunks is not None else [b"\x89PNG\r\n\x1a\n" + b"image"]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=1024):
        yield from self._chunks


class _FakeImageSession:
    def __init__(self, responses):
        self.trust_env = True
        self.responses = list(responses)
        self.mounts = []
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def mount(self, prefix, adapter):
        self.mounts.append((prefix, adapter))

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def _install_pinned_image_session(monkeypatch, responses):
    import requests

    session = _FakeImageSession(responses)
    monkeypatch.setattr(requests, "Session", lambda: session)
    return session


def test_provider_image_download_uses_pinned_socket_and_no_proxy(monkeypatch):
    from graphlink_plugins.web_research.fetch_policy import FetchPolicy, ValidatedTarget

    monkeypatch.setattr(
        FetchPolicy, "validate",
        lambda self, url: ValidatedTarget(url, "93.184.216.34"),
    )
    session = _install_pinned_image_session(monkeypatch, [_FakeImageResponse()])
    # A regression to the old urllib.urlopen route must fail this test.
    monkeypatch.setattr(api_provider.urllib.request, "urlopen", lambda *a, **k: pytest.fail("un-pinned urllib connection"))
    actual = api_provider._extract_openai_image_bytes(
        _response_with_url("https://public.example/picture.png")
    )
    assert actual.startswith(b"\x89PNG")
    assert session.trust_env is False
    assert len(session.calls) == 1
    url, kwargs = session.calls[0]
    assert url == "https://public.example/picture.png"
    assert kwargs["allow_redirects"] is False
    assert kwargs["stream"] is True
    assert session.mounts[0][0] == "https://"
    assert session.mounts[0][1]._pinned_ip == "93.184.216.34"


@pytest.mark.parametrize("location", [
    "https://127.0.0.1/private",
    "https://169.254.169.254/latest/meta-data",
    "http://public.example/insecure.png",
])
def test_provider_image_redirects_are_revalidated_before_connect(monkeypatch, location):
    from graphlink_plugins.web_research.fetch_policy import FetchPolicy

    # Public host resolution is local and deterministic; IP literals still
    # use FetchPolicy's real private-address rejection.
    monkeypatch.setattr(
        FetchPolicy, "_resolve_addresses",
        lambda self, parsed: ["93.184.216.34"],
    )
    session = _install_pinned_image_session(
        monkeypatch, [_FakeImageResponse(status=302, location=location)]
    )
    with pytest.raises(RuntimeError, match="blocked by fetch policy"):
        api_provider._extract_openai_image_bytes(
            _response_with_url("https://public.example/picture.png")
        )
    assert len(session.calls) == 1


def test_provider_image_download_rejects_actual_stream_size_over_cap(monkeypatch):
    from graphlink_plugins.web_research.fetch_policy import FetchPolicy, ValidatedTarget
    from provider_runtime.media import _IMAGE_DOWNLOAD_MAX_BYTES

    monkeypatch.setattr(
        FetchPolicy, "validate",
        lambda self, url: ValidatedTarget(url, "93.184.216.34"),
    )
    session = _install_pinned_image_session(
        monkeypatch,
        [_FakeImageResponse(chunks=[b"x" * (_IMAGE_DOWNLOAD_MAX_BYTES + 1)])],
    )
    with pytest.raises(RuntimeError, match="size limit"):
        api_provider._extract_openai_image_bytes(
            _response_with_url("https://public.example/picture.png")
        )
    assert len(session.calls) == 1


def test_provider_image_download_rejects_reported_large_content_length(monkeypatch):
    from graphlink_plugins.web_research.fetch_policy import FetchPolicy, ValidatedTarget
    from provider_runtime.media import _IMAGE_DOWNLOAD_MAX_BYTES

    monkeypatch.setattr(
        FetchPolicy, "validate",
        lambda self, url: ValidatedTarget(url, "93.184.216.34"),
    )
    _install_pinned_image_session(
        monkeypatch,
        [_FakeImageResponse(content_length=_IMAGE_DOWNLOAD_MAX_BYTES + 1)],
    )
    with pytest.raises(RuntimeError, match="size limit"):
        api_provider._extract_openai_image_bytes(
            _response_with_url("https://public.example/picture.png")
        )
