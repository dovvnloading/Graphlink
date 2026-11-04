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


def test_inline_base64_payload_above_limit_is_rejected_before_decode(monkeypatch):
    import provider_runtime.media as media

    monkeypatch.setattr(media, "MAX_PROVIDER_IMAGE_BYTES", 10)
    oversized = base64.b64encode(b"012345678901").decode("ascii")

    def unexpected_decode(_value):
        raise AssertionError("oversized base64 was decoded before validation")

    monkeypatch.setattr(media.base64, "b64decode", unexpected_decode)
    with pytest.raises(RuntimeError, match="download limit"):
        api_provider._extract_openai_image_bytes(
            SimpleNamespace(data=[SimpleNamespace(b64_json=oversized)])
        )


def test_inline_base64_payload_is_rejected_after_decode_if_payload_exceeds_limit(monkeypatch):
    import provider_runtime.media as media

    monkeypatch.setattr(media, "MAX_PROVIDER_IMAGE_BYTES", 10)
    # Base64 length can be within the loose encoded ceiling while decoded
    # bytes are still over the actual limit.
    oversized = base64.b64encode(b"01234567890").decode("ascii")
    with pytest.raises(RuntimeError, match="download limit"):
        api_provider._extract_openai_image_bytes(
            SimpleNamespace(data=[SimpleNamespace(b64_json=oversized)])
        )


def test_a_b64_json_response_never_touches_the_network_or_the_policy():
    # The common OpenAI path returns inline base64 and must still work with
    # no URL validation involved at all.
    payload = base64.b64encode(b"PNGBYTES").decode("ascii")
    resp = SimpleNamespace(data=[SimpleNamespace(b64_json=payload)])
    assert api_provider._extract_openai_image_bytes(resp) == b"PNGBYTES"


class _FakeImageResponse:
    def __init__(self, status_code=200, *, headers=None, body=b"PNG data"):
        self.status_code = status_code
        self.headers = headers or {}
        self.body = body
        self.closed = False
        self.read_started = False

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=65536):
        self.read_started = True
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset:offset + chunk_size]

    def close(self):
        self.closed = True


def _fake_image_network(monkeypatch, response_by_url, public_ips):
    """Network-free fixture: validates URLs separately from the pinned socket."""
    import provider_runtime.media as media
    from graphlink_plugins.web_research.fetch_policy import FetchPolicy, URLPolicyError, ValidatedTarget
    from graphlink_plugins.web_research import providers as provider_module

    class RecordingAdapter:
        def __init__(self, pinned_ip):
            self.ip = pinned_ip

    class RecordingSession:
        def __init__(self):
            self.trust_env = True
            self.mounts = []
            self.requests = []
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.closed = True

        def mount(self, scheme, adapter):
            self.mounts.append((scheme, adapter.ip))

        def get(self, url, **kwargs):
            self.requests.append((url, kwargs))
            return response_by_url[url]

    session = RecordingSession()

    def validate(_policy, url):
        if url not in public_ips:
            raise URLPolicyError("Source resolves to a non-public network address.")
        return ValidatedTarget(canonical_url=url, pinned_ip=public_ips[url])

    monkeypatch.setattr(FetchPolicy, "validate", validate)
    monkeypatch.setattr(provider_module, "_PinnedHTTPAdapter", RecordingAdapter)
    monkeypatch.setattr(media.requests, "Session", lambda: session)
    return session


def test_generated_image_redirect_to_private_host_is_denied_before_second_get(monkeypatch):
    first_url = "https://images.example.test/generated.png"
    first = _FakeImageResponse(302, headers={"Location": "https://127.0.0.1/private"})
    session = _fake_image_network(monkeypatch, {first_url: first}, {first_url: "93.184.216.34"})

    with pytest.raises(RuntimeError, match="blocked by fetch policy"):
        api_provider._extract_openai_image_bytes(_response_with_url(first_url))

    assert [entry[0] for entry in session.requests] == [first_url]
    assert session.requests[0][1]["allow_redirects"] is False
    assert session.trust_env is False
    assert first.closed and session.closed


def test_generated_image_download_pins_each_redirect_to_validated_public_ip(monkeypatch):
    first_url = "https://images.example.test/generated.png"
    last_url = "https://cdn.example.test/image.png"
    first = _FakeImageResponse(302, headers={"Location": last_url})
    last = _FakeImageResponse(body=b"valid image bytes")
    session = _fake_image_network(
        monkeypatch,
        {first_url: first, last_url: last},
        {first_url: "93.184.216.34", last_url: "8.8.8.8"},
    )

    payload = api_provider._extract_openai_image_bytes(_response_with_url(first_url))

    assert payload == b"valid image bytes"
    assert session.mounts == [("https://", "93.184.216.34"), ("https://", "8.8.8.8")]
    assert [request[0] for request in session.requests] == [first_url, last_url]
    assert all(request[1]["stream"] and not request[1]["allow_redirects"] for request in session.requests)
    assert first.closed and last.closed and session.closed


def test_generated_image_download_rejects_stream_over_limit(monkeypatch):
    import provider_runtime.media as media

    url = "https://images.example.test/generated.png"
    response = _FakeImageResponse(body=b"01234567890")
    monkeypatch.setattr(media, "MAX_PROVIDER_IMAGE_BYTES", 10)
    session = _fake_image_network(monkeypatch, {url: response}, {url: "93.184.216.34"})

    with pytest.raises(RuntimeError, match="download limit"):
        api_provider._extract_openai_image_bytes(_response_with_url(url))

    assert response.closed and session.closed


def test_generated_image_download_rejects_claimed_oversize_before_read(monkeypatch):
    import provider_runtime.media as media

    url = "https://images.example.test/generated.png"
    response = _FakeImageResponse(headers={"Content-Length": "100"}, body=b"small")
    monkeypatch.setattr(media, "MAX_PROVIDER_IMAGE_BYTES", 10)
    _fake_image_network(monkeypatch, {url: response}, {url: "93.184.216.34"})

    with pytest.raises(RuntimeError, match="download limit"):
        api_provider._extract_openai_image_bytes(_response_with_url(url))

    assert not response.read_started
    assert response.closed


def test_generated_image_redirects_are_bounded(monkeypatch):
    url = "https://images.example.test/generated.png"
    redirect = _FakeImageResponse(302, headers={"Location": url})
    session = _fake_image_network(monkeypatch, {url: redirect}, {url: "93.184.216.34"})

    with pytest.raises(RuntimeError, match="redirect limit"):
        api_provider._extract_openai_image_bytes(_response_with_url(url))

    assert len(session.requests) == 4
    assert redirect.closed
