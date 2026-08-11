"""Fills real coverage gaps in graphlink_plugins/web_research/providers.py
left after test_web_research_providers_fetcher.py (ADR-004 stage 4.5's
IP-pinning/robots/redirect-chain integration tests) and the dedicated
fetch_policy.py/crawl_etiquette.py suites.

fetch_policy.py and crawl_etiquette.py already have thorough direct unit
coverage (SSRF/private-range/localhost validation, DNS-rebinding pinning,
robots.txt parsing/caching/fail-open, Crawl-delay precedence/clamping) - see
test_web_research_fetch_policy.py and test_web_research_crawl_etiquette.py.
This file does not re-test those modules' own logic; the fake-resolver
FetchPolicy instances constructed below exist only to reach the providers.py
branches under test.

What was genuinely untested before this file (confirmed by reading every
existing web_research test file):

- RequestsDocumentFetcher.fetch()'s content-type/content-length/streaming
  byte-cap enforcement, redirect-limit/invalid-redirect handling, HTTP
  error-status mapping, the requests-package-missing guard, and the
  catch-all "fetch_failed" exception mapping - test_..._providers_fetcher.py
  covers the pinning/robots/redirect-chain/timeout wiring but never
  exercises these branches.
- RequestsDocumentFetcher._fetch_robots_bytes() - previously only exercised
  indirectly through fetch()'s own robots.txt lookup; never unit-tested on
  its own (policy-rejection, request-exception, truncation, close()-on-error
  paths).
- BeautifulSoupContentExtractor - zero prior coverage of any kind. This is
  the module's actual "response parsing" layer (HTML/JSON/plain-text
  extraction, truncation, empty-source/dependency-missing/extract-failed
  error mapping) and it had no direct tests at all.
- The small helpers dependency_status() and source_id_for_url().
- DuckDuckGoSearchProvider.search() - dependency-missing guard,
  normalization/dedup, and provider-exception mapping.
- ApiResearchModel - JSON/legacy-text response parsing and exception
  fallbacks for refine_query/assess_source/summarize.
"""

from __future__ import annotations

import hashlib
import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from graphlink_plugins.web_research import providers as providers_module
from graphlink_plugins.web_research.crawl_etiquette import CrawlEtiquette
from graphlink_plugins.web_research.domain import (
    CancellationToken,
    FetchedPayload,
    RequestCancelled,
    ResearchFailure,
    ResearchLimits,
    SearchResult,
)
from graphlink_plugins.web_research.fetch_policy import FetchPolicy
from graphlink_plugins.web_research.providers import (
    ApiResearchModel,
    BeautifulSoupContentExtractor,
    DuckDuckGoSearchProvider,
    RequestsDocumentFetcher,
    dependency_status,
    source_id_for_url,
)


def _search_result(url="https://example.com/page"):
    return SearchResult(source_id="s1-abc", title="Example", url=url, canonical_url=url)


def _real_policy_with_fake_resolver(*addresses, **kwargs):
    def resolver(host, port, type=None):
        import socket
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, port)) for addr in addresses]

    return FetchPolicy(resolver=resolver, **kwargs)


class _FakeResponse:
    def __init__(self, status_code=200, content_type="text/html", body=b"<html>hi</html>", headers=None, location=None):
        self.status_code = status_code
        self._body = body
        self.headers = dict(headers or {})
        self.headers.setdefault("Content-Type", content_type)
        if location:
            self.headers["Location"] = location
        self.closed = False

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308) and "Location" in self.headers

    @property
    def is_permanent_redirect(self):
        return self.status_code in (301, 308) and "Location" in self.headers

    def iter_content(self, chunk_size=16384):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]

    def close(self):
        self.closed = True


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.trust_env = True
        self.headers = {}
        self.mounted = []
        self.get_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def mount(self, prefix, adapter):
        self.mounted.append((prefix, adapter))

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        if not self._responses:
            raise AssertionError("no more scripted responses")
        return self._responses.pop(0)


@pytest.fixture
def limits():
    return ResearchLimits()


@pytest.fixture
def token():
    return CancellationToken()


def _patched_requests(fake_session):
    """Context manager patching providers_module.requests so fetch() drives
    the fake session instead of real sockets, matching the convention
    established in test_web_research_providers_fetcher.py."""
    return patch.object(providers_module, "requests")


# ---------------------------------------------------------------------------
# RequestsDocumentFetcher: content-type / content-length / streaming caps
# ---------------------------------------------------------------------------


class TestContentTypeAndSizeEnforcement:
    def test_a_disallowed_content_type_is_rejected_before_any_body_is_kept(self, limits, token):
        policy = _real_policy_with_fake_resolver("93.184.216.34")
        etiquette = CrawlEtiquette(default_crawl_delay_seconds=0)
        fake_session = _FakeSession(
            [
                _FakeResponse(status_code=404, body=b""),  # robots.txt
                _FakeResponse(status_code=200, content_type="application/pdf", body=b"%PDF-1.4 ..."),
            ]
        )
        fetcher = RequestsDocumentFetcher(policy=policy, etiquette=etiquette)

        with _patched_requests(fake_session) as fake_requests_module:
            fake_requests_module.Session.return_value = fake_session
            fake_requests_module.RequestException = Exception
            with pytest.raises(ResearchFailure) as exc_info:
                fetcher.fetch(_search_result(), limits=limits, token=token)

        assert exc_info.value.code == "unsupported_content_type"
        assert exc_info.value.retryable is False

    def test_a_content_length_header_over_the_limit_rejects_without_streaming_the_body(self, limits, token):
        policy = _real_policy_with_fake_resolver("93.184.216.34")
        etiquette = CrawlEtiquette(default_crawl_delay_seconds=0)
        oversized = _FakeResponse(
            status_code=200,
            content_type="text/html",
            body=b"<html>should never be read</html>",
            headers={"Content-Length": str(limits.max_bytes_per_source + 1)},
        )

        def _iter_content_should_not_be_called(chunk_size=16384):
            raise AssertionError("body must not be streamed once Content-Length exceeds the cap")

        oversized.iter_content = _iter_content_should_not_be_called
        fake_session = _FakeSession([_FakeResponse(status_code=404, body=b""), oversized])
        fetcher = RequestsDocumentFetcher(policy=policy, etiquette=etiquette)

        with _patched_requests(fake_session) as fake_requests_module:
            fake_requests_module.Session.return_value = fake_session
            fake_requests_module.RequestException = Exception
            with pytest.raises(ResearchFailure) as exc_info:
                fetcher.fetch(_search_result(), limits=limits, token=token)

        assert exc_info.value.code == "source_too_large"
        assert exc_info.value.retryable is False

    def test_a_streamed_body_exceeding_the_cap_is_truncated_rather_than_rejected(self, limits, token):
        # No Content-Length header this time - the cap must still be
        # enforced by the chunked-read loop itself.
        policy = _real_policy_with_fake_resolver("93.184.216.34")
        etiquette = CrawlEtiquette(default_crawl_delay_seconds=0)
        tight_limits = ResearchLimits(max_bytes_per_source=10)
        oversized_body = b"0123456789ABCDEF"  # 16 bytes > the 10-byte cap
        fake_session = _FakeSession(
            [
                _FakeResponse(status_code=404, body=b""),
                _FakeResponse(status_code=200, content_type="text/plain", body=oversized_body),
            ]
        )
        fetcher = RequestsDocumentFetcher(policy=policy, etiquette=etiquette)

        with _patched_requests(fake_session) as fake_requests_module:
            fake_requests_module.Session.return_value = fake_session
            fake_requests_module.RequestException = Exception
            result = fetcher.fetch(_search_result(), limits=tight_limits, token=token)

        assert result.truncated is True
        assert result.body == oversized_body[:10]

    def test_the_smaller_of_policy_and_per_request_limits_wins(self, limits, token):
        policy = _real_policy_with_fake_resolver("93.184.216.34", max_bytes=5)
        etiquette = CrawlEtiquette(default_crawl_delay_seconds=0)
        generous_limits = ResearchLimits(max_bytes_per_source=1_000_000)
        body = b"0123456789"
        fake_session = _FakeSession(
            [
                _FakeResponse(status_code=404, body=b""),
                _FakeResponse(status_code=200, content_type="text/plain", body=body),
            ]
        )
        fetcher = RequestsDocumentFetcher(policy=policy, etiquette=etiquette)

        with _patched_requests(fake_session) as fake_requests_module:
            fake_requests_module.Session.return_value = fake_session
            fake_requests_module.RequestException = Exception
            result = fetcher.fetch(_search_result(), limits=generous_limits, token=token)

        # policy.max_bytes=5 is tighter than the request's own 1,000,000 cap.
        assert result.body == body[:5]
        assert result.truncated is True

    def test_a_body_under_the_cap_is_returned_whole_and_not_marked_truncated(self, limits, token):
        policy = _real_policy_with_fake_resolver("93.184.216.34")
        etiquette = CrawlEtiquette(default_crawl_delay_seconds=0)
        body = b"short body"
        fake_session = _FakeSession(
            [
                _FakeResponse(status_code=404, body=b""),
                _FakeResponse(status_code=200, content_type="text/plain", body=body),
            ]
        )
        fetcher = RequestsDocumentFetcher(policy=policy, etiquette=etiquette)

        with _patched_requests(fake_session) as fake_requests_module:
            fake_requests_module.Session.return_value = fake_session
            fake_requests_module.RequestException = Exception
            result = fetcher.fetch(_search_result(), limits=limits, token=token)

        assert result.body == body
        assert result.truncated is False


# ---------------------------------------------------------------------------
# RequestsDocumentFetcher: redirect-limit / invalid-redirect / HTTP errors
# ---------------------------------------------------------------------------


class TestRedirectAndStatusHandling:
    def test_exceeding_the_redirect_limit_raises_redirect_limit(self, limits, token):
        policy = _real_policy_with_fake_resolver("93.184.216.34", max_redirects=1)
        etiquette = CrawlEtiquette(default_crawl_delay_seconds=0)
        # robots.txt (once - same origin throughout) + two redirect hops,
        # the second of which exceeds max_redirects=1.
        fake_session = _FakeSession(
            [
                _FakeResponse(status_code=404, body=b""),
                _FakeResponse(status_code=302, location="https://example.com/hop2"),
                _FakeResponse(status_code=302, location="https://example.com/hop3"),
            ]
        )
        fetcher = RequestsDocumentFetcher(policy=policy, etiquette=etiquette)

        with _patched_requests(fake_session) as fake_requests_module:
            fake_requests_module.Session.return_value = fake_session
            fake_requests_module.RequestException = Exception
            with pytest.raises(ResearchFailure) as exc_info:
                fetcher.fetch(_search_result(), limits=limits, token=token)

        assert exc_info.value.code == "redirect_limit"

    def test_a_redirect_status_with_no_location_header_is_rejected(self, limits, token):
        # A distinct one-off subclass (never mutates the shared _FakeResponse
        # class used by every other test in this file) that reports itself
        # as a redirect without actually carrying a Location header.
        class _RedirectWithNoLocation(_FakeResponse):
            @property
            def is_redirect(self):
                return True

            @property
            def is_permanent_redirect(self):
                return False

        policy = _real_policy_with_fake_resolver("93.184.216.34")
        etiquette = CrawlEtiquette(default_crawl_delay_seconds=0)
        broken_redirect = _RedirectWithNoLocation(status_code=302, body=b"")
        broken_redirect.headers.pop("Location", None)
        fake_session = _FakeSession([_FakeResponse(status_code=404, body=b""), broken_redirect])
        fetcher = RequestsDocumentFetcher(policy=policy, etiquette=etiquette)

        with _patched_requests(fake_session) as fake_requests_module:
            fake_requests_module.Session.return_value = fake_session
            fake_requests_module.RequestException = Exception
            with pytest.raises(ResearchFailure) as exc_info:
                fetcher.fetch(_search_result(), limits=limits, token=token)

        assert exc_info.value.code == "invalid_redirect"

    def test_an_http_error_status_is_mapped_to_fetch_http_error(self, limits, token):
        policy = _real_policy_with_fake_resolver("93.184.216.34")
        etiquette = CrawlEtiquette(default_crawl_delay_seconds=0)
        fake_session = _FakeSession(
            [
                _FakeResponse(status_code=404, body=b""),
                _FakeResponse(status_code=500, content_type="text/html", body=b"server error"),
            ]
        )
        fetcher = RequestsDocumentFetcher(policy=policy, etiquette=etiquette)

        with _patched_requests(fake_session) as fake_requests_module:
            fake_requests_module.Session.return_value = fake_session
            fake_requests_module.RequestException = Exception
            with pytest.raises(ResearchFailure) as exc_info:
                fetcher.fetch(_search_result(), limits=limits, token=token)

        assert exc_info.value.code == "fetch_http_error"
        assert "500" in str(exc_info.value)


# ---------------------------------------------------------------------------
# RequestsDocumentFetcher: dependency-missing guard + catch-all mapping
# ---------------------------------------------------------------------------


class TestDependencyMissingAndCatchAllErrorMapping:
    def test_requests_unavailable_raises_a_non_retryable_dependency_failure(self, limits, token):
        fetcher = RequestsDocumentFetcher()
        with patch.object(providers_module, "REQUESTS_AVAILABLE", False):
            with pytest.raises(ResearchFailure) as exc_info:
                fetcher.fetch(_search_result(), limits=limits, token=token)

        assert exc_info.value.code == "fetch_dependency_missing"
        assert exc_info.value.retryable is False
        assert exc_info.value.source_id == "s1-abc"

    def test_an_unexpected_non_research_exception_is_mapped_to_a_generic_fetch_failed(self, limits, token):
        # A defect somewhere in the inner loop (not a ResearchFailure, not a
        # requests.RequestException, not URLPolicyError) must still surface
        # as a user-safe ResearchFailure rather than an unhandled traceback.
        policy = _real_policy_with_fake_resolver("93.184.216.34")
        etiquette = CrawlEtiquette(default_crawl_delay_seconds=0)
        broken_response = _FakeResponse(status_code=200, content_type="text/html")

        def _boom(chunk_size=16384):
            raise RuntimeError("unexpected parsing bug")

        broken_response.iter_content = _boom
        # One scripted response for the robots.txt lookup CrawlEtiquette.gate()
        # performs first, then the broken one for the actual content GET.
        fake_session = _FakeSession([_FakeResponse(status_code=404, body=b""), broken_response])
        fetcher = RequestsDocumentFetcher(policy=policy, etiquette=etiquette)

        with _patched_requests(fake_session) as fake_requests_module:
            fake_requests_module.Session.return_value = fake_session
            fake_requests_module.RequestException = Exception
            with pytest.raises(ResearchFailure) as exc_info:
                fetcher.fetch(_search_result(), limits=limits, token=token)

        assert exc_info.value.code == "fetch_failed"


# ---------------------------------------------------------------------------
# RequestsDocumentFetcher._fetch_robots_bytes - direct unit coverage
# ---------------------------------------------------------------------------


class TestFetchRobotsBytesDirectly:
    """_fetch_robots_bytes was previously only exercised indirectly, as a
    side effect of fetch()'s own robots.txt lookup (always scripted as a
    trivial 404 in the existing integration tests). These call it directly
    to pin its own error-handling contract."""

    def test_returns_none_when_the_robots_url_itself_fails_policy_validation(self):
        # A private/loopback resolution for the robots.txt host must be
        # rejected the same way the main content fetch would be.
        policy = FetchPolicy(resolver=lambda host, port, type=None: [(2, 1, 6, "", ("127.0.0.1", port))])
        fetcher = RequestsDocumentFetcher(policy=policy)

        result = fetcher._fetch_robots_bytes("https://blocked.example/robots.txt", MagicMock())

        assert result is None

    def test_returns_none_on_a_request_exception(self):
        policy = _real_policy_with_fake_resolver("93.184.216.34")
        fetcher = RequestsDocumentFetcher(policy=policy)

        class _RequestException(Exception):
            pass

        fake_session = MagicMock()
        fake_session.get.side_effect = _RequestException("network unreachable")

        with patch.object(providers_module, "requests") as fake_requests_module:
            fake_requests_module.RequestException = _RequestException
            result = fetcher._fetch_robots_bytes("https://example.com/robots.txt", fake_session)

        assert result is None

    def test_returns_the_status_code_and_full_body_on_a_completed_response(self):
        policy = _real_policy_with_fake_resolver("93.184.216.34")
        fetcher = RequestsDocumentFetcher(policy=policy)
        response = _FakeResponse(status_code=200, body=b"User-agent: *\nDisallow: /private/\n")
        fake_session = MagicMock()
        fake_session.get.return_value = response

        with patch.object(providers_module, "requests") as fake_requests_module:
            fake_requests_module.RequestException = Exception
            result = fetcher._fetch_robots_bytes("https://example.com/robots.txt", fake_session)

        assert result == (200, b"User-agent: *\nDisallow: /private/\n")
        assert response.closed is True

    def test_returns_a_non_200_status_code_unmodified_alongside_whatever_body_it_sent(self):
        policy = _real_policy_with_fake_resolver("93.184.216.34")
        fetcher = RequestsDocumentFetcher(policy=policy)
        response = _FakeResponse(status_code=404, body=b"not found")
        fake_session = MagicMock()
        fake_session.get.return_value = response

        with patch.object(providers_module, "requests") as fake_requests_module:
            fake_requests_module.RequestException = Exception
            result = fetcher._fetch_robots_bytes("https://example.com/robots.txt", fake_session)

        assert result == (404, b"not found")

    def test_the_body_is_truncated_to_the_robots_txt_byte_cap(self):
        policy = _real_policy_with_fake_resolver("93.184.216.34")
        fetcher = RequestsDocumentFetcher(policy=policy)
        oversized_body = b"x" * (RequestsDocumentFetcher.ROBOTS_TXT_MAX_BYTES + 500)
        response = _FakeResponse(status_code=200, body=oversized_body)
        fake_session = MagicMock()
        fake_session.get.return_value = response

        with patch.object(providers_module, "requests") as fake_requests_module:
            fake_requests_module.RequestException = Exception
            status_code, body = fetcher._fetch_robots_bytes("https://example.com/robots.txt", fake_session)

        assert status_code == 200
        assert len(body) == RequestsDocumentFetcher.ROBOTS_TXT_MAX_BYTES

    def test_the_response_is_closed_even_when_reading_the_body_raises(self):
        policy = _real_policy_with_fake_resolver("93.184.216.34")
        fetcher = RequestsDocumentFetcher(policy=policy)
        response = _FakeResponse(status_code=200, body=b"irrelevant")

        class _RequestException(Exception):
            pass

        def _boom(chunk_size=8192):
            raise _RequestException("connection reset mid-body")

        response.iter_content = _boom
        fake_session = MagicMock()
        fake_session.get.return_value = response

        with patch.object(providers_module, "requests") as fake_requests_module:
            fake_requests_module.RequestException = _RequestException
            result = fetcher._fetch_robots_bytes("https://example.com/robots.txt", fake_session)

        assert result is None
        assert response.closed is True


# ---------------------------------------------------------------------------
# BeautifulSoupContentExtractor - previously zero direct coverage
# ---------------------------------------------------------------------------


def _payload(content_type="text/html", body=b"<html><body><p>hi</p></body></html>", truncated=False, final_url="https://example.com/page"):
    return FetchedPayload(
        source_id="s1-abc",
        requested_url=final_url,
        final_url=final_url,
        content_type=content_type,
        body=body,
        truncated=truncated,
        status_code=200,
        duration_ms=5,
    )


class TestBeautifulSoupContentExtractorHtml:
    def test_extracts_title_and_readable_paragraph_text(self, limits, token):
        html = b"<html><head><title>My Page</title></head><body><p>Hello world.</p></body></html>"
        extractor = BeautifulSoupContentExtractor()

        doc = extractor.extract(_payload(body=html), limits=limits, token=token)

        assert doc.title == "My Page"
        assert "Hello world." in doc.text
        assert doc.sections == ("Hello world.",)
        assert doc.source_id == "s1-abc"

    def test_falls_back_to_the_hostname_when_there_is_no_title_tag(self, limits, token):
        html = b"<html><body><p>No title here.</p></body></html>"
        extractor = BeautifulSoupContentExtractor()

        doc = extractor.extract(_payload(body=html, final_url="https://news.example/a"), limits=limits, token=token)

        assert doc.title == "news.example"

    def test_script_style_nav_and_footer_content_is_stripped(self, limits, token):
        html = (
            b"<html><body>"
            b"<nav>Site nav</nav>"
            b"<script>alert('x')</script>"
            b"<style>.a{color:red}</style>"
            b"<p>Real content.</p>"
            b"<footer>Copyright 2026</footer>"
            b"</body></html>"
        )
        extractor = BeautifulSoupContentExtractor()

        doc = extractor.extract(_payload(body=html), limits=limits, token=token)

        assert "Real content." in doc.text
        assert "Site nav" not in doc.text
        assert "alert" not in doc.text
        assert "color:red" not in doc.text
        assert "Copyright" not in doc.text

    def test_extraction_prefers_the_main_or_article_element_when_present(self, limits, token):
        html = (
            b"<html><body>"
            b"<nav><p>Nav link text</p></nav>"
            b"<main><p>Main content only.</p></main>"
            b"</body></html>"
        )
        extractor = BeautifulSoupContentExtractor()

        doc = extractor.extract(_payload(body=html), limits=limits, token=token)

        assert "Main content only." in doc.text
        assert "Nav link text" not in doc.text

    def test_text_is_truncated_to_max_chars_per_source_and_flagged(self, limits, token):
        long_paragraph = "word " * 10_000
        html = f"<html><body><p>{long_paragraph}</p></body></html>".encode("utf-8")
        tight_limits = ResearchLimits(max_chars_per_source=100)
        extractor = BeautifulSoupContentExtractor()

        doc = extractor.extract(_payload(body=html), limits=tight_limits, token=token)

        assert len(doc.text) <= 100
        assert doc.truncated is True

    def test_a_payload_already_marked_truncated_stays_truncated_even_if_the_text_fits(self, limits, token):
        html = b"<html><body><p>short.</p></body></html>"
        extractor = BeautifulSoupContentExtractor()

        doc = extractor.extract(_payload(body=html, truncated=True), limits=limits, token=token)

        assert doc.truncated is True

    def test_content_hash_is_a_stable_sha256_of_the_final_text(self, limits, token):
        html = b"<html><body><p>Deterministic content.</p></body></html>"
        extractor = BeautifulSoupContentExtractor()

        doc = extractor.extract(_payload(body=html), limits=limits, token=token)

        assert doc.content_hash == hashlib.sha256(doc.text.encode("utf-8", errors="replace")).hexdigest()

    def test_purely_whitespace_or_markup_only_html_raises_empty_source(self, limits, token):
        html = b"<html><body><script>1</script><style>.a{}</style></body></html>"
        extractor = BeautifulSoupContentExtractor()

        with pytest.raises(ResearchFailure) as exc_info:
            extractor.extract(_payload(body=html), limits=limits, token=token)

        assert exc_info.value.code == "empty_source"
        assert exc_info.value.retryable is False

    def test_cancellation_is_honored_before_extraction_begins(self, limits, token):
        token.cancel()
        extractor = BeautifulSoupContentExtractor()

        with pytest.raises(RequestCancelled):
            extractor.extract(_payload(), limits=limits, token=token)

    def test_beautifulsoup_unavailable_raises_a_non_retryable_dependency_failure(self, limits, token):
        extractor = BeautifulSoupContentExtractor()
        with patch.object(providers_module, "BEAUTIFULSOUP_AVAILABLE", False):
            with pytest.raises(ResearchFailure) as exc_info:
                extractor.extract(_payload(), limits=limits, token=token)

        assert exc_info.value.code == "extract_dependency_missing"
        assert exc_info.value.retryable is False

    def test_an_unexpected_exception_during_extraction_is_mapped_to_extract_failed(self, limits, token):
        extractor = BeautifulSoupContentExtractor()
        with patch.object(providers_module, "BeautifulSoup", side_effect=RuntimeError("parser exploded")):
            with pytest.raises(ResearchFailure) as exc_info:
                extractor.extract(_payload(), limits=limits, token=token)

        assert exc_info.value.code == "extract_failed"


class TestBeautifulSoupContentExtractorJsonAndPlainText:
    def test_valid_json_is_pretty_printed_and_titled_by_hostname(self, limits, token):
        payload = _payload(content_type="application/json", body=b'{"b": 2, "a": 1}', final_url="https://api.example/data")
        extractor = BeautifulSoupContentExtractor()

        doc = extractor.extract(payload, limits=limits, token=token)

        assert doc.title == "api.example"
        parsed_back = json.loads(doc.text)
        assert parsed_back == {"b": 2, "a": 1}

    def test_malformed_json_falls_back_to_the_raw_decoded_text(self, limits, token):
        payload = _payload(content_type="application/json", body=b"{not valid json", final_url="https://api.example/data")
        extractor = BeautifulSoupContentExtractor()

        doc = extractor.extract(payload, limits=limits, token=token)

        assert "not valid json" in doc.text

    def test_plain_text_content_is_split_into_line_sections(self, limits, token):
        payload = _payload(content_type="text/plain", body=b"line one\nline two\n\nline three", final_url="https://text.example/f.txt")
        extractor = BeautifulSoupContentExtractor()

        doc = extractor.extract(payload, limits=limits, token=token)

        assert doc.title == "text.example"
        assert doc.sections == ("line one", "line two", "line three")


# ---------------------------------------------------------------------------
# dependency_status / source_id_for_url helpers
# ---------------------------------------------------------------------------


class TestSmallHelpers:
    def test_dependency_status_reports_all_three_optional_packages(self):
        status = dependency_status()
        assert set(status.keys()) == {"ddgs", "requests", "beautifulsoup4"}
        assert all(isinstance(v, bool) for v in status.values())

    def test_source_id_is_deterministic_for_the_same_url_and_rank(self):
        first = source_id_for_url("https://example.com/a", rank=0)
        second = source_id_for_url("https://example.com/a", rank=0)
        assert first == second

    def test_source_id_differs_for_different_urls(self):
        a = source_id_for_url("https://example.com/a", rank=0)
        b = source_id_for_url("https://example.com/b", rank=0)
        assert a != b

    def test_source_id_prefix_is_1_indexed_from_rank(self):
        assert source_id_for_url("https://example.com/a", rank=0).startswith("s1-")
        assert source_id_for_url("https://example.com/a", rank=4).startswith("s5-")

    def test_source_id_canonicalizes_equivalent_urls_to_the_same_digest(self):
        # https + default port 443 written explicitly vs. omitted - same
        # canonical URL, so the same digest (mirrors DuckDuckGoSearchProvider's
        # own dedup-by-canonical-url use of this function).
        a = source_id_for_url("https://Example.com:443/path", rank=0)
        b = source_id_for_url("https://example.com/path", rank=0)
        assert a == b


# ---------------------------------------------------------------------------
# DuckDuckGoSearchProvider
# ---------------------------------------------------------------------------


class TestDuckDuckGoSearchProvider:
    def test_raises_a_non_retryable_failure_when_ddgs_is_unavailable(self, limits, token):
        provider = DuckDuckGoSearchProvider()
        with patch.object(providers_module, "DUCKDUCKGO_SEARCH_AVAILABLE", False):
            with pytest.raises(ResearchFailure) as exc_info:
                provider.search("query", limits=limits, token=token)

        assert exc_info.value.code == "search_dependency_missing"
        assert exc_info.value.retryable is False

    def test_normalizes_results_and_dedupes_by_canonical_url(self, limits, token):
        raw_results = [
            {"href": "https://example.com/a", "title": "A", "body": "snippet a"},
            {"href": "https://example.com/a#dup", "title": "A dup", "body": "snippet dup"},  # same canonical URL
            {"href": "https://example.com/b", "title": "B", "body": "snippet b"},
        ]
        fake_ddgs = MagicMock()
        fake_ddgs.__enter__.return_value = fake_ddgs
        fake_ddgs.__exit__.return_value = False
        fake_ddgs.text.return_value = raw_results
        provider = DuckDuckGoSearchProvider()

        with patch.object(providers_module, "DDGS", return_value=fake_ddgs):
            results = provider.search("query", limits=limits, token=token)

        assert [r.canonical_url for r in results] == ["https://example.com/a", "https://example.com/b"]
        assert results[0].rank == 1
        # Rank reflects the ORIGINAL raw-result index (enumerate() runs
        # before the dedup check), not a sequential post-dedup count - the
        # skipped duplicate at index 1 leaves a gap, so "b" (index 2) is
        # rank 3, not rank 2.
        assert results[1].rank == 3

    def test_non_dict_raw_results_are_skipped(self, limits, token):
        fake_ddgs = MagicMock()
        fake_ddgs.__enter__.return_value = fake_ddgs
        fake_ddgs.__exit__.return_value = False
        fake_ddgs.text.return_value = ["not-a-dict", None, {"href": "https://example.com/a", "title": "A"}]
        provider = DuckDuckGoSearchProvider()

        with patch.object(providers_module, "DDGS", return_value=fake_ddgs):
            results = provider.search("query", limits=limits, token=token)

        assert len(results) == 1
        assert results[0].canonical_url == "https://example.com/a"

    def test_a_result_missing_a_title_falls_back_to_the_hostname(self, limits, token):
        fake_ddgs = MagicMock()
        fake_ddgs.__enter__.return_value = fake_ddgs
        fake_ddgs.__exit__.return_value = False
        fake_ddgs.text.return_value = [{"href": "https://news.example/a", "body": "snippet"}]
        provider = DuckDuckGoSearchProvider()

        with patch.object(providers_module, "DDGS", return_value=fake_ddgs):
            results = provider.search("query", limits=limits, token=token)

        assert results[0].title == "news.example"

    def test_a_provider_side_exception_is_mapped_to_a_research_failure(self, limits, token):
        fake_ddgs_cls = MagicMock(side_effect=RuntimeError("provider is down"))
        provider = DuckDuckGoSearchProvider()

        with patch.object(providers_module, "DDGS", fake_ddgs_cls):
            with pytest.raises(ResearchFailure) as exc_info:
                provider.search("query", limits=limits, token=token)

        assert exc_info.value.code == "search_provider_unavailable"

    def test_cancellation_is_honored_before_the_search_call(self, limits, token):
        token.cancel()
        provider = DuckDuckGoSearchProvider()

        with pytest.raises(RequestCancelled):
            provider.search("query", limits=limits, token=token)


# ---------------------------------------------------------------------------
# ApiResearchModel - JSON/legacy-text parsing + exception fallbacks
# ---------------------------------------------------------------------------


class TestApiResearchModelRefineQuery:
    def test_returns_the_bare_query_without_calling_the_model_when_there_is_no_history(self, limits, token):
        model = ApiResearchModel()
        with patch.object(providers_module.api_provider, "chat") as fake_chat:
            result = model.refine_query("  what is graphlink  ", [], limits=limits, token=token)

        fake_chat.assert_not_called()
        assert result == "what is graphlink"

    def test_uses_the_models_rewritten_query_when_history_is_present(self, limits, token):
        model = ApiResearchModel()
        with patch.object(
            providers_module.api_provider, "chat", return_value={"message": {"content": '"refined query"'}}
        ):
            result = model.refine_query("original", [{"role": "user", "content": "context"}], limits=limits, token=token)

        assert result == "refined query"

    def test_falls_back_to_the_original_query_when_the_model_call_raises(self, limits, token):
        model = ApiResearchModel()
        with patch.object(providers_module.api_provider, "chat", side_effect=RuntimeError("provider down")):
            result = model.refine_query("original query", [{"role": "user", "content": "context"}], limits=limits, token=token)

        assert result == "original query"

    def test_falls_back_to_the_original_query_when_the_model_returns_blank(self, limits, token):
        model = ApiResearchModel()
        with patch.object(providers_module.api_provider, "chat", return_value={"message": {"content": "   "}}):
            result = model.refine_query("original query", [{"role": "user", "content": "context"}], limits=limits, token=token)

        assert result == "original query"


class TestApiResearchModelAssessSource:
    def _document(self, text="Some evidence text about the query."):
        from graphlink_plugins.web_research.domain import FetchedDocument

        return FetchedDocument(
            source_id="s1-abc",
            title="Title",
            final_url="https://example.com/a",
            content_type="text/html",
            text=text,
        )

    def test_parses_a_valid_json_response_into_a_source_assessment(self, limits, token):
        model = ApiResearchModel()
        raw = json.dumps({"policy": "allow", "relevance": "high", "quality": "high", "reason": "on_topic"})
        with patch.object(providers_module.api_provider, "chat", return_value={"message": {"content": raw}}):
            assessment = model.assess_source("query", self._document(), limits=limits, token=token)

        assert assessment.accepted is True
        assert assessment.policy_status == "allow"
        assert assessment.reason == "on_topic"

    def test_a_block_policy_or_low_quality_is_not_accepted(self, limits, token):
        model = ApiResearchModel()
        raw = json.dumps({"policy": "allow", "relevance": "high", "quality": "low", "reason": "thin_content"})
        with patch.object(providers_module.api_provider, "chat", return_value={"message": {"content": raw}}):
            assessment = model.assess_source("query", self._document(), limits=limits, token=token)

        assert assessment.accepted is False

    def test_legacy_unsafe_text_response_is_treated_as_blocked(self, limits, token):
        model = ApiResearchModel()
        with patch.object(providers_module.api_provider, "chat", return_value={"message": {"content": "UNSAFE"}}):
            assessment = model.assess_source("query", self._document(), limits=limits, token=token)

        assert assessment.accepted is False
        assert assessment.policy_status == "block"
        assert assessment.reason == "model_blocked"

    def test_legacy_safe_text_response_is_treated_as_allowed(self, limits, token):
        model = ApiResearchModel()
        with patch.object(providers_module.api_provider, "chat", return_value={"message": {"content": "SAFE"}}):
            assessment = model.assess_source("query", self._document(), limits=limits, token=token)

        assert assessment.accepted is True
        assert assessment.policy_status == "allow"

    def test_unparseable_non_legacy_text_is_treated_as_unknown_and_not_accepted(self, limits, token):
        model = ApiResearchModel()
        with patch.object(providers_module.api_provider, "chat", return_value={"message": {"content": "gibberish response"}}):
            assessment = model.assess_source("query", self._document(), limits=limits, token=token)

        assert assessment.accepted is False
        assert assessment.reason == "invalid_model_output"

    def test_a_model_call_exception_yields_a_not_accepted_unknown_assessment(self, limits, token):
        model = ApiResearchModel()
        with patch.object(providers_module.api_provider, "chat", side_effect=RuntimeError("boom")):
            assessment = model.assess_source("query", self._document(), limits=limits, token=token)

        assert assessment.accepted is False
        assert assessment.reason == "validation_unavailable"


class TestApiResearchModelSummarize:
    def test_returns_the_models_answer_text(self, limits, token):
        model = ApiResearchModel()
        with patch.object(providers_module.api_provider, "chat", return_value={"message": {"content": "Final answer [s1]."}}):
            answer = model.summarize("query", [], ["evidence chunk"], limits=limits, token=token)

        assert answer == "Final answer [s1]."

    def test_an_empty_answer_raises_a_research_failure(self, limits, token):
        model = ApiResearchModel()
        with patch.object(providers_module.api_provider, "chat", return_value={"message": {"content": "   "}}):
            with pytest.raises(ResearchFailure) as exc_info:
                model.summarize("query", [], ["evidence chunk"], limits=limits, token=token)

        assert exc_info.value.code == "empty_summary"

    def test_a_model_call_exception_is_wrapped_as_a_research_failure(self, limits, token):
        model = ApiResearchModel()
        with patch.object(providers_module.api_provider, "chat", side_effect=RuntimeError("provider down")):
            with pytest.raises(ResearchFailure) as exc_info:
                model.summarize("query", [], ["evidence chunk"], limits=limits, token=token)

        assert exc_info.value.code == "summarization_failed"
