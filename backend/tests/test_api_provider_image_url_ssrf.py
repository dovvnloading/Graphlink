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
