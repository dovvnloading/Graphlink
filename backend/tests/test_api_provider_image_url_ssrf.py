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
import socket
from types import SimpleNamespace

import requests

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
    def __init__(self, *, status=200, headers=None, body=b"PNG DATA"):
        self.status_code = status
        self.headers = headers or {}
        self.body = body
        self.closed = False

    def iter_content(self, chunk_size=1):
        assert chunk_size == 1, "total timeout must be checked after each received byte"
        for value in self.body:
            yield bytes([value])

    def close(self):
        self.closed = True


class _FakeImageSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.mounted = []
        self.trust_env = True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def mount(self, scheme, adapter):
        self.mounted.append((scheme, adapter))

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def _public_image_policy(**kwargs):
    from graphlink_plugins.web_research.fetch_policy import FetchPolicy

    def resolver(host, port, type=None):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    return FetchPolicy(resolver=resolver, **kwargs)


def test_provider_image_download_pins_ip_and_disables_auto_redirects_and_proxies(monkeypatch):
    from provider_runtime.media import _download_provider_image_url

    session = _FakeImageSession([_FakeImageResponse()])
    monkeypatch.setattr(requests, "Session", lambda: session)
    assert _download_provider_image_url(
        "https://images.example/test.png", policy=_public_image_policy(),
    ) == b"PNG DATA"
    assert session.trust_env is False
    assert session.calls[0][0] == "https://images.example/test.png"
    assert session.calls[0][1]["allow_redirects"] is False
    assert session.calls[0][1]["stream"] is True
    assert session.mounted[0][0] == "https://"
    assert session.mounted[0][1]._pinned_ip == "93.184.216.34"


def test_provider_image_redirect_to_private_address_is_refused_before_second_request(monkeypatch):
    from provider_runtime.media import _download_provider_image_url

    redirected = _FakeImageResponse(status=302, headers={
        "Location": "https://127.0.0.1/private",
    })
    session = _FakeImageSession([redirected])
    monkeypatch.setattr(requests, "Session", lambda: session)
    with pytest.raises(RuntimeError, match="blocked by fetch policy"):
        _download_provider_image_url(
            "https://images.example/redirect", policy=_public_image_policy(),
        )
    assert len(session.calls) == 1
    assert redirected.closed


def test_provider_image_download_bounds_body_even_without_content_length(monkeypatch):
    from provider_runtime.media import _download_provider_image_url

    session = _FakeImageSession([_FakeImageResponse(body=b"12345")])
    monkeypatch.setattr(requests, "Session", lambda: session)
    with pytest.raises(RuntimeError, match="image size limit"):
        _download_provider_image_url(
            "https://images.example/huge.png", policy=_public_image_policy(max_bytes=4),
        )


def test_provider_image_download_denies_banned_redirect_schemes(monkeypatch, tmp_path):
    from provider_runtime.media import _download_provider_image_url

    secret = tmp_path / "private-key.txt"
    secret.write_text("sensitive", encoding="utf-8")
    session = _FakeImageSession([
        _FakeImageResponse(status=302, headers={"Location": secret.as_uri()}),
    ])
    monkeypatch.setattr(requests, "Session", lambda: session)
    with pytest.raises(RuntimeError, match="blocked by fetch policy"):
        _download_provider_image_url(
            "https://images.example/redirect", policy=_public_image_policy(),
        )
    assert len(session.calls) == 1


def test_provider_image_download_enforces_redirect_limit(monkeypatch):
    from provider_runtime.media import _download_provider_image_url

    session = _FakeImageSession([
        _FakeImageResponse(status=302, headers={"Location": "/again"}),
    ])
    monkeypatch.setattr(requests, "Session", lambda: session)
    with pytest.raises(RuntimeError, match="invalid redirect"):
        _download_provider_image_url(
            "https://images.example/redirect",
            policy=_public_image_policy(max_redirects=0),
        )
    assert len(session.calls) == 1
