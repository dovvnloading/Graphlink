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


class _ImageResponse:
    def __init__(self, status=200, headers=None, chunks=()):
        self.status_code = status
        self.headers = headers or {}
        self.chunks = chunks
        self.closed = False

    def iter_content(self, chunk_size):
        assert chunk_size == 1
        yield from self.chunks

    def close(self):
        self.closed = True


class _ImageSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.mounts = []
        self.trust_env = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def mount(self, prefix, adapter):
        self.mounts.append((prefix, adapter))

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return self.responses.pop(0)


def test_image_redirect_to_localhost_is_refused_before_second_request(monkeypatch):
    import requests

    from graphlink_plugins.web_research.fetch_policy import FetchPolicy, ValidatedTarget

    original = FetchPolicy.validate

    def validated_public(self, url):
        if url == "https://image.example/start":
            return ValidatedTarget(url, "93.184.216.34")
        return original(self, url)

    redirected = _ImageResponse(status=302, headers={
        "Location": "https://127.0.0.1/admin",
    })
    session = _ImageSession([redirected])
    monkeypatch.setattr(FetchPolicy, "validate", validated_public)
    monkeypatch.setattr(requests, "Session", lambda: session)

    with pytest.raises(RuntimeError, match="blocked by fetch policy"):
        api_provider._extract_openai_image_bytes(_response_with_url("https://image.example/start"))
    assert len(session.requests) == 1
    assert session.requests[0][1]["allow_redirects"] is False
    assert session.requests[0][1]["stream"] is True
    assert session.trust_env is False
    assert session.mounts[0][1]._pinned_ip == "93.184.216.34"
    assert redirected.closed is True


def test_image_download_uses_pinned_ip_and_revalidates_public_redirect(monkeypatch):
    import requests

    from graphlink_plugins.web_research.fetch_policy import FetchPolicy, ValidatedTarget

    seen = []

    def validate(self, url):
        seen.append(url)
        addresses = {
            "https://first.example/start": "93.184.216.34",
            "https://second.example/picture": "1.1.1.1",
        }
        return ValidatedTarget(url, addresses[url])

    first = _ImageResponse(status=301, headers={"Location": "https://second.example/picture"})
    second = _ImageResponse(status=200, headers={"Content-Length": "4"}, chunks=[b"P", b"N", b"G", b"!"])
    session = _ImageSession([first, second])
    monkeypatch.setattr(FetchPolicy, "validate", validate)
    monkeypatch.setattr(requests, "Session", lambda: session)

    assert api_provider._extract_openai_image_bytes(_response_with_url("https://first.example/start")) == b"PNG!"
    assert seen == ["https://first.example/start", "https://second.example/picture"]
    assert [adapter._pinned_ip for _, adapter in session.mounts] == ["93.184.216.34", "1.1.1.1"]
    assert all(kwargs["allow_redirects"] is False for _, kwargs in session.requests)
    assert first.closed and second.closed


def test_provider_image_stream_enforces_byte_cap_even_when_content_length_lies(monkeypatch):
    import requests

    from graphlink_plugins.web_research.fetch_policy import FetchPolicy, ValidatedTarget
    import provider_runtime.media as media

    response = _ImageResponse(headers={"Content-Length": "1"}, chunks=[b"ab", b"cd"])
    session = _ImageSession([response])
    monkeypatch.setattr(FetchPolicy, "validate", lambda self, url: ValidatedTarget(url, "93.184.216.34"))
    monkeypatch.setattr(requests, "Session", lambda: session)
    monkeypatch.setattr(media, "_MAX_PROVIDER_IMAGE_BYTES", 3)

    with pytest.raises(RuntimeError, match="size limit"):
        api_provider._extract_openai_image_bytes(_response_with_url("https://image.example/image.png"))
    assert response.closed


def test_provider_image_download_refuses_overstated_content_length(monkeypatch):
    import requests

    from graphlink_plugins.web_research.fetch_policy import FetchPolicy, ValidatedTarget
    import provider_runtime.media as media

    response = _ImageResponse(headers={"Content-Length": "10000"})
    session = _ImageSession([response])
    monkeypatch.setattr(FetchPolicy, "validate", lambda self, url: ValidatedTarget(url, "93.184.216.34"))
    monkeypatch.setattr(requests, "Session", lambda: session)
    monkeypatch.setattr(media, "_MAX_PROVIDER_IMAGE_BYTES", 3)

    with pytest.raises(RuntimeError, match="size limit"):
        api_provider._extract_openai_image_bytes(_response_with_url("https://image.example/image.png"))
    assert response.closed


def test_a_b64_json_response_never_touches_the_network_or_the_policy():
    # The common OpenAI path returns inline base64 and must still work with
    # no URL validation involved at all.
    payload = base64.b64encode(b"PNGBYTES").decode("ascii")
    resp = SimpleNamespace(data=[SimpleNamespace(b64_json=payload)])
    assert api_provider._extract_openai_image_bytes(resp) == b"PNGBYTES"
