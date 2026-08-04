"""Integration tests for RequestsDocumentFetcher's NEW wiring in ADR-004
stage 4.5: IP-pinning (via _PinnedHTTPAdapter) and crawl-etiquette
(robots.txt + polite delay) integrated into the existing fetch loop.

The pre-existing SSRF/redirect/content-type/byte-cap logic this loop
already had is untouched by stage 4.5 and is exercised incidentally by
these tests, but not re-covered exhaustively here - see
test_web_research_fetch_policy.py for the dedicated SSRF boundary tests.

Uses a fake requests.Session/Response (no real sockets) so these tests
stay fast and network-independent, matching this suite's existing
MagicMock-based convention (e.g. test_backend_api_key_env_fallback.py's
own patch("openai.OpenAI", ...)).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from graphlink_plugins.web_research.crawl_etiquette import CrawlEtiquette, RobotsDisallowedError
from graphlink_plugins.web_research.domain import CancellationToken, ResearchFailure, ResearchLimits, SearchResult
from graphlink_plugins.web_research.fetch_policy import FetchPolicy, URLPolicyError
from graphlink_plugins.web_research import providers as providers_module
from graphlink_plugins.web_research.providers import RequestsDocumentFetcher, _PinnedHTTPAdapter


def _search_result(url="https://example.com/page"):
    return SearchResult(source_id="s1-abc", title="Example", url=url, canonical_url=url)


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
        yield self._body

    def close(self):
        self.closed = True


class _FakeSession:
    """Stands in for requests.Session - context-manager compatible, records
    every mount() call (to assert on the pinned IP) and every get() call,
    returns a scripted queue of _FakeResponse objects."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.trust_env = True
        self.headers = {}
        self.mounted = []  # (prefix, adapter) pairs, in call order
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


def _real_policy_with_fake_resolver(*addresses):
    def resolver(host, port, type=None):
        import socket
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, port)) for addr in addresses]

    return FetchPolicy(resolver=resolver)


@pytest.fixture
def limits():
    return ResearchLimits()


@pytest.fixture
def token():
    return CancellationToken()


class TestPinnedAdapterIsMountedWithTheValidatedIp:
    def test_mounts_a_pinned_adapter_for_the_resolved_ip_before_fetching(self, limits, token):
        policy = _real_policy_with_fake_resolver("93.184.216.34")
        fake_session = _FakeSession([_FakeResponse(status_code=200, content_type="text/html")])
        fetcher = RequestsDocumentFetcher(policy=policy, etiquette=CrawlEtiquette(default_crawl_delay_seconds=0))

        # robots.txt fetch also goes through session.get - script it as a 404
        # (no rules) ahead of the real content response.
        fake_session._responses.insert(0, _FakeResponse(status_code=404, body=b""))

        with patch.object(providers_module, "requests") as fake_requests_module:
            fake_requests_module.Session.return_value = fake_session
            fake_requests_module.RequestException = Exception
            result = fetcher.fetch(_search_result(), limits=limits, token=token)

        assert result.status_code == 200
        pinned_ips = {
            adapter._pinned_ip for prefix, adapter in fake_session.mounted if hasattr(adapter, "_pinned_ip")
        }
        assert "93.184.216.34" in pinned_ips


class TestRobotsIntegration:
    def test_robots_disallow_turns_into_a_research_failure(self, limits, token):
        policy = _real_policy_with_fake_resolver("93.184.216.34")
        fake_etiquette = MagicMock(spec=CrawlEtiquette)
        fake_etiquette.gate.side_effect = RobotsDisallowedError("nope")
        fake_session = _FakeSession([])
        fetcher = RequestsDocumentFetcher(policy=policy, etiquette=fake_etiquette)

        with patch.object(providers_module, "requests") as fake_requests_module:
            fake_requests_module.Session.return_value = fake_session
            fake_requests_module.RequestException = Exception
            with pytest.raises(ResearchFailure) as exc_info:
                fetcher.fetch(_search_result(), limits=limits, token=token)

        assert exc_info.value.code == "robots_disallowed"
        # The gate rejected the fetch before any real GET was attempted.
        assert fake_session.get_calls == []

    def test_record_fetch_is_called_after_a_successful_get(self, limits, token):
        policy = _real_policy_with_fake_resolver("93.184.216.34")
        fake_etiquette = MagicMock(spec=CrawlEtiquette)
        fake_etiquette.gate.return_value = 0.0
        fake_session = _FakeSession([_FakeResponse(status_code=200, content_type="text/plain", body=b"hello")])
        fetcher = RequestsDocumentFetcher(policy=policy, etiquette=fake_etiquette)

        with patch.object(providers_module, "requests") as fake_requests_module:
            fake_requests_module.Session.return_value = fake_session
            fake_requests_module.RequestException = Exception
            fetcher.fetch(_search_result(), limits=limits, token=token)

        fake_etiquette.record_fetch.assert_called_once()
        fake_etiquette.gate.assert_called_once()

    def test_record_fetch_is_not_called_when_the_get_itself_raises(self, limits, token):
        policy = _real_policy_with_fake_resolver("93.184.216.34")
        fake_etiquette = MagicMock(spec=CrawlEtiquette)
        fake_etiquette.gate.return_value = 0.0

        class _RequestException(Exception):
            pass

        class _RaisingSession(_FakeSession):
            def get(self, url, **kwargs):
                raise _RequestException("network unreachable")

        fake_session = _RaisingSession([])
        fetcher = RequestsDocumentFetcher(policy=policy, etiquette=fake_etiquette)

        with patch.object(providers_module, "requests") as fake_requests_module:
            fake_requests_module.Session.return_value = fake_session
            fake_requests_module.RequestException = _RequestException
            with pytest.raises(ResearchFailure) as exc_info:
                fetcher.fetch(_search_result(), limits=limits, token=token)

        assert exc_info.value.code == "fetch_network_error"
        fake_etiquette.record_fetch.assert_not_called()

    def test_ssrf_validation_runs_before_robots_txt_gating(self, limits, token):
        # Order of precedence: an SSRF-blocked URL must never even reach the
        # robots.txt/etiquette layer - validate() first, gate() second.
        policy = FetchPolicy(resolver=lambda host, port, type=None: [(2, 1, 6, "", ("127.0.0.1", port))])
        fake_etiquette = MagicMock(spec=CrawlEtiquette)
        fetcher = RequestsDocumentFetcher(policy=policy, etiquette=fake_etiquette)

        with pytest.raises(ResearchFailure) as exc_info:
            fetcher.fetch(_search_result(), limits=limits, token=token)

        assert exc_info.value.code == "url_blocked_by_policy"
        fake_etiquette.gate.assert_not_called()

    def test_a_proxied_request_is_mapped_to_url_blocked_by_policy_not_a_generic_failure(self, limits, token):
        # Regression: _PinnedHTTPAdapter raises URLPolicyError (not a
        # requests.RequestException) when asked to route through a proxy -
        # it must be caught explicitly, not fall through to the generic
        # "fetch_failed" handler.
        policy = _real_policy_with_fake_resolver("93.184.216.34")
        fake_etiquette = MagicMock(spec=CrawlEtiquette)
        fake_etiquette.gate.return_value = 0.0

        class _ProxyForcingSession(_FakeSession):
            def get(self, url, **kwargs):
                adapter = self.mounted[-1][1]
                request = requests.PreparedRequest()
                request.prepare(method="GET", url=url, headers={})
                adapter.get_connection_with_tls_context(request, verify=True, proxies={"https": "http://proxy.example:8080"})
                raise AssertionError("should have raised URLPolicyError before this point")

        fake_session = _ProxyForcingSession([])
        fetcher = RequestsDocumentFetcher(policy=policy, etiquette=fake_etiquette)

        with patch.object(providers_module, "requests") as fake_requests_module:
            fake_requests_module.Session.return_value = fake_session
            fake_requests_module.RequestException = Exception
            with pytest.raises(ResearchFailure) as exc_info:
                fetcher.fetch(_search_result(), limits=limits, token=token)

        assert exc_info.value.code == "url_blocked_by_policy"


class TestMultiHopCrossHostRedirects:
    def test_each_hop_is_independently_validated_pinned_and_rate_limited(self, limits, token):
        # A redirect chain that moves to a DIFFERENT host must re-validate/
        # re-pin for the new host (not reuse the first hop's IP/adapter),
        # and crawl-etiquette must track the two hosts as separate origins.
        def resolver(host, port, type=None):
            ip = "93.184.216.34" if host == "a.example" else "1.1.1.1"
            return [(2, 1, 6, "", (ip, port))]

        policy = FetchPolicy(resolver=resolver)
        etiquette = CrawlEtiquette(default_crawl_delay_seconds=0)
        fake_session = _FakeSession(
            [
                _FakeResponse(status_code=404, body=b""),  # robots.txt for a.example
                _FakeResponse(status_code=302, location="https://b.example/final"),  # hop 1 content
                _FakeResponse(status_code=404, body=b""),  # robots.txt for b.example
                _FakeResponse(status_code=200, content_type="text/html", body=b"<html>done</html>"),  # hop 2 content
            ]
        )
        fetcher = RequestsDocumentFetcher(policy=policy, etiquette=etiquette)

        with patch.object(providers_module, "requests") as fake_requests_module:
            fake_requests_module.Session.return_value = fake_session
            fake_requests_module.RequestException = Exception
            result = fetcher.fetch(_search_result(url="https://a.example/start"), limits=limits, token=token)

        assert result.final_url == "https://b.example/final"
        pinned_ips_in_order = [adapter._pinned_ip for _, adapter in fake_session.mounted if hasattr(adapter, "_pinned_ip")]
        # robots.txt + content pin for hop 1 (a.example), then robots.txt + content pin for hop 2 (b.example).
        assert pinned_ips_in_order == ["93.184.216.34", "93.184.216.34", "1.1.1.1", "1.1.1.1"]
        # Both origins were tracked independently (both got a robots.txt lookup, neither delayed the other).
        assert set(etiquette._robots.keys()) >= {"https://a.example", "https://b.example"}


class _FakeClock:
    def __init__(self, start=1_000.0):
        self.now = start
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class TestWaitPolitely:
    """Adversarial-review fix: gate()'s old internal time.sleep() was
    uninterruptible - it ran outside the fetch's own cancellation token and
    total_timeout_seconds budget, so a malicious/misconfigured Crawl-delay
    could hang a worker thread indefinitely. _wait_politely replaces that
    with a chopped-up, re-checked wait - these tests exercise it directly."""

    def test_sleeps_in_small_bounded_increments_not_one_long_sleep(self):
        clock = _FakeClock()
        policy = FetchPolicy(total_timeout_seconds=100.0)
        fetcher = RequestsDocumentFetcher(policy=policy)
        token = CancellationToken()

        with patch.object(providers_module.time, "monotonic", clock.monotonic), \
             patch.object(providers_module.time, "sleep", clock.sleep):
            fetcher._wait_politely(3.0, token=token, started=clock.monotonic(), result=_search_result())

        assert clock.slept == [0.5, 0.5, 0.5, 0.5, 0.5, 0.5]  # POLITE_WAIT_STEP_SECONDS chunks, never one 3.0s sleep
        assert sum(clock.slept) == pytest.approx(3.0)

    def test_raises_cancelled_promptly_instead_of_completing_the_full_wait(self):
        clock = _FakeClock()
        policy = FetchPolicy(total_timeout_seconds=100.0)
        fetcher = RequestsDocumentFetcher(policy=policy)
        token = CancellationToken()

        real_sleep = clock.sleep

        def sleep_then_cancel_after_first_step(seconds):
            real_sleep(seconds)
            token.cancel()  # simulates the user hitting Stop mid-wait

        with patch.object(providers_module.time, "monotonic", clock.monotonic), \
             patch.object(providers_module.time, "sleep", sleep_then_cancel_after_first_step):
            with pytest.raises(Exception):  # RequestCancelled
                fetcher._wait_politely(30.0, token=token, started=clock.monotonic(), result=_search_result())

        # Cancelled after roughly ONE step, not after waiting out the full 30s.
        assert len(clock.slept) == 1

    def test_raises_fetch_timeout_instead_of_waiting_past_the_total_budget(self):
        clock = _FakeClock()
        policy = FetchPolicy(total_timeout_seconds=1.0)
        fetcher = RequestsDocumentFetcher(policy=policy)
        token = CancellationToken()
        started = clock.monotonic()

        with patch.object(providers_module.time, "monotonic", clock.monotonic), \
             patch.object(providers_module.time, "sleep", clock.sleep):
            with pytest.raises(ResearchFailure) as exc_info:
                fetcher._wait_politely(30.0, token=token, started=started, result=_search_result())

        assert exc_info.value.code == "fetch_timeout"
        # Never slept past the 1.0s total budget, despite a 30s delay being owed.
        assert sum(clock.slept) <= 1.0 + 1e-9


class TestEmptyBodyTimeoutEnforcement:
    def test_an_empty_body_response_still_enforces_the_total_timeout(self, limits, token):
        # Regression: `for chunk in response.iter_content(...)` never enters
        # its loop body for a response streaming zero chunks, so the
        # per-chunk timeout check was never reached - a fetch that already
        # blew its budget (e.g. waiting out a polite delay) returned a
        # fully "successful" FetchedPayload with no error at all.
        clock = _FakeClock()
        policy = _real_policy_with_fake_resolver("93.184.216.34")
        policy = FetchPolicy(resolver=policy.resolver, total_timeout_seconds=1.0)
        fake_etiquette = MagicMock(spec=CrawlEtiquette)
        fake_etiquette.gate.return_value = 0.0
        empty_response = _FakeResponse(status_code=200, content_type="text/html", body=b"")
        empty_response.iter_content = lambda chunk_size=16384: iter(())  # yields NOTHING, not even one empty chunk
        fake_session = _FakeSession([empty_response])
        fetcher = RequestsDocumentFetcher(policy=policy, etiquette=fake_etiquette)

        def advance_and_get(url, **kwargs):
            clock.now += 5.0  # simulate 5s having elapsed during the request itself
            return fake_session._responses.pop(0)

        fake_session.get = advance_and_get

        with patch.object(providers_module, "requests") as fake_requests_module, \
             patch.object(providers_module.time, "monotonic", clock.monotonic):
            fake_requests_module.Session.return_value = fake_session
            fake_requests_module.RequestException = Exception
            with pytest.raises(ResearchFailure) as exc_info:
                fetcher.fetch(_search_result(), limits=limits, token=token)

        assert exc_info.value.code == "fetch_timeout"


class TestPinnedHTTPAdapterConnectionLogic:
    """Direct tests of _PinnedHTTPAdapter's own connection-pool construction
    - distinct from a real-network proof (already established manually
    before this code was written: pinning to the correct resolved IP
    succeeds; pinning to a different real site's IP fails TLS hostname
    verification; pinning to loopback fails cleanly). These tests instead
    guard the IMPLEMENTATION against regressing that behavior, without
    needing real sockets: they inspect exactly what host/SNI/hostname-
    verification arguments the adapter hands to urllib3's pool manager."""

    @staticmethod
    def _prepared_request(url: str) -> requests.PreparedRequest:
        req = requests.PreparedRequest()
        req.prepare(method="GET", url=url, headers={})
        return req

    def test_the_pool_is_built_with_the_pinned_ip_as_the_connect_host(self):
        adapter = _PinnedHTTPAdapter("93.184.216.34")
        adapter.poolmanager = MagicMock()
        request = self._prepared_request("https://example.com/path")

        adapter.get_connection_with_tls_context(request, verify=True)

        _, kwargs = adapter.poolmanager.connection_from_host.call_args
        assert kwargs["host"] == "93.184.216.34"

    def test_the_original_hostname_is_used_for_sni_and_hostname_verification(self):
        adapter = _PinnedHTTPAdapter("93.184.216.34")
        adapter.poolmanager = MagicMock()
        request = self._prepared_request("https://example.com/path")

        adapter.get_connection_with_tls_context(request, verify=True)

        _, kwargs = adapter.poolmanager.connection_from_host.call_args
        pool_kwargs = kwargs["pool_kwargs"]
        assert pool_kwargs["assert_hostname"] == "example.com"
        assert pool_kwargs["server_hostname"] == "example.com"

    def test_a_proxied_request_is_rejected_rather_than_silently_unpinned(self):
        adapter = _PinnedHTTPAdapter("93.184.216.34")
        adapter.poolmanager = MagicMock()
        request = self._prepared_request("https://example.com/path")

        with pytest.raises(URLPolicyError, match="[Pp]rox"):
            adapter.get_connection_with_tls_context(request, verify=True, proxies={"https": "http://proxy.example:8080"})

    def test_send_injects_the_original_host_header_not_the_pinned_ip(self):
        adapter = _PinnedHTTPAdapter("93.184.216.34")
        request = self._prepared_request("https://example.com/path")

        with patch.object(_PinnedHTTPAdapter.__bases__[0], "send", return_value="sentinel") as base_send:
            result = adapter.send(request)

        assert request.headers["Host"] == "example.com"
        assert result == "sentinel"
        base_send.assert_called_once()

    def test_send_includes_a_non_default_port_in_the_host_header(self):
        adapter = _PinnedHTTPAdapter("93.184.216.34")
        request = self._prepared_request("https://example.com:8443/path")

        with patch.object(_PinnedHTTPAdapter.__bases__[0], "send", return_value="sentinel"):
            adapter.send(request)

        assert request.headers["Host"] == "example.com:8443"

    def test_hostname_verification_is_still_injected_even_with_verify_false(self):
        # A caller disabling cert verification entirely doesn't mean the
        # pinning-related pool kwargs should be skipped - they're harmless
        # (unused) when verify=False, and this confirms the injection isn't
        # accidentally gated behind a `verify` check that could silently
        # drop it for some other verify value in the future.
        adapter = _PinnedHTTPAdapter("93.184.216.34")
        adapter.poolmanager = MagicMock()
        request = self._prepared_request("https://example.com/path")

        adapter.get_connection_with_tls_context(request, verify=False)

        _, kwargs = adapter.poolmanager.connection_from_host.call_args
        assert kwargs["host"] == "93.184.216.34"
        assert kwargs["pool_kwargs"]["assert_hostname"] == "example.com"
        assert kwargs["pool_kwargs"]["server_hostname"] == "example.com"
