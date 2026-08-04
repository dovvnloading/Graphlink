"""Tests for graphlink_plugins/web_research/crawl_etiquette.py (ADR-004
stage 4.5) - robots.txt Disallow rules and polite per-host rate limiting for
the Web Research fetch path.

time.monotonic()/time.sleep() are monkeypatched throughout for fast,
deterministic tests - a fake clock advanced explicitly between calls,
rather than real (even if short) waits.
"""

from __future__ import annotations

import pytest

import graphlink_plugins.web_research.crawl_etiquette as etiquette_module
from graphlink_plugins.web_research.crawl_etiquette import CrawlEtiquette, RobotsDisallowedError


class _FakeClock:
    def __init__(self, start: float = 1_000.0):
        self.now = start
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = _FakeClock()
    monkeypatch.setattr(etiquette_module.time, "monotonic", fake.monotonic)
    monkeypatch.setattr(etiquette_module.time, "sleep", fake.sleep)
    return fake


def _fetcher(status_code: int, body: bytes):
    def fetch(url):
        return (status_code, body)
    return fetch


ROBOTS_WITH_DISALLOW_AND_DELAY = b"""User-agent: *
Disallow: /private/
Crawl-delay: 5
"""

ROBOTS_ALLOW_ALL_NO_DELAY = b"""User-agent: *
Disallow:
"""


class TestRobotsDisallow:
    def test_an_allowed_path_does_not_raise(self, clock):
        etq = CrawlEtiquette()
        etq.gate("https://example.com/public/page", _fetcher(200, ROBOTS_WITH_DISALLOW_AND_DELAY))

    def test_a_disallowed_path_raises(self, clock):
        etq = CrawlEtiquette()
        with pytest.raises(RobotsDisallowedError, match="disallows"):
            etq.gate("https://example.com/private/secret", _fetcher(200, ROBOTS_WITH_DISALLOW_AND_DELAY))

    def test_robots_txt_is_only_fetched_once_per_origin(self, clock):
        calls = []

        def counting_fetch(url):
            calls.append(url)
            return (200, ROBOTS_ALLOW_ALL_NO_DELAY)

        etq = CrawlEtiquette(default_crawl_delay_seconds=0)
        etq.gate("https://example.com/page-a", counting_fetch)
        etq.gate("https://example.com/page-b", counting_fetch)
        etq.gate("https://example.com/page-c", counting_fetch)

        assert calls == ["https://example.com/robots.txt"]

    def test_different_paths_on_different_origins_each_get_their_own_robots_txt(self, clock):
        calls = []

        def counting_fetch(url):
            calls.append(url)
            return (200, ROBOTS_ALLOW_ALL_NO_DELAY)

        etq = CrawlEtiquette(default_crawl_delay_seconds=0)
        etq.gate("https://a.example/page", counting_fetch)
        etq.gate("https://b.example/page", counting_fetch)

        assert calls == ["https://a.example/robots.txt", "https://b.example/robots.txt"]

    def test_a_404_on_robots_txt_means_everything_is_allowed(self, clock):
        etq = CrawlEtiquette()
        etq.gate("https://example.com/anything", _fetcher(404, b""))  # must not raise

    @pytest.mark.parametrize("status_code", [401, 403])
    def test_401_or_403_on_robots_txt_disallows_everything(self, clock, status_code):
        etq = CrawlEtiquette()
        with pytest.raises(RobotsDisallowedError):
            etq.gate("https://example.com/anything", _fetcher(status_code, b""))

    def test_a_5xx_on_robots_txt_is_treated_as_no_explicit_rules(self, clock):
        etq = CrawlEtiquette()
        etq.gate("https://example.com/anything", _fetcher(503, b""))  # must not raise

    def test_a_redirect_status_on_robots_txt_is_treated_as_no_explicit_rules(self, clock):
        # providers.py never follows redirects for robots.txt itself (see
        # its own _fetch_robots_bytes docstring) - a 3xx status simply
        # isn't handled specially here either, matching "no rules found".
        etq = CrawlEtiquette()
        etq.gate("https://example.com/anything", _fetcher(301, b""))  # must not raise

    def test_a_network_failure_fetching_robots_txt_fails_open(self, clock):
        def failing_fetch(url):
            raise ConnectionError("boom")

        etq = CrawlEtiquette()
        etq.gate("https://example.com/anything", failing_fetch)  # must not raise

    def test_a_fetcher_returning_none_fails_open(self, clock):
        etq = CrawlEtiquette()
        etq.gate("https://example.com/anything", lambda url: None)  # must not raise

    def test_unparseable_robots_txt_body_fails_open_rather_than_crashing(self, clock):
        etq = CrawlEtiquette()
        # Invalid UTF-8 - decode uses errors="replace", so this shouldn't
        # even hit the except path, but confirms no exception either way.
        etq.gate("https://example.com/anything", _fetcher(200, b"\xff\xfe\x00garbage"))

    def test_a_network_failure_is_not_permanently_cached_and_gets_retried(self, clock):
        # Regression for an adversarial-review finding: caching a single
        # timeout/network-failure as "no rules, forever, for this run" gave
        # a malicious site a deterministic way to disable its own robots.txt
        # enforcement with one deliberately-stalled request. A later gate()
        # call for the same origin must re-attempt the fetch, not trust a
        # single earlier failure.
        calls = []

        def failing_fetch(url):
            calls.append(url)
            raise ConnectionError("boom")

        etq = CrawlEtiquette(default_crawl_delay_seconds=0)
        etq.gate("https://example.com/page-a", failing_fetch)
        etq.gate("https://example.com/page-b", failing_fetch)
        etq.gate("https://example.com/page-c", failing_fetch)

        assert calls == [
            "https://example.com/robots.txt",
            "https://example.com/robots.txt",
            "https://example.com/robots.txt",
        ]

    def test_a_successful_fetch_after_earlier_failures_is_cached_normally(self, clock):
        attempts = {"count": 0}

        def flaky_then_ok(url):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise ConnectionError("boom")
            return (200, ROBOTS_WITH_DISALLOW_AND_DELAY)

        etq = CrawlEtiquette()
        etq.gate("https://example.com/public/a", flaky_then_ok)
        etq.gate("https://example.com/public/b", flaky_then_ok)
        with pytest.raises(RobotsDisallowedError):
            etq.gate("https://example.com/private/c", flaky_then_ok)  # 3rd attempt succeeds, rules now apply

        assert attempts["count"] == 3  # not re-fetched again after the successful 3rd attempt


class TestPoliteDelay:
    """gate() itself never sleeps (adversarial-review fix - see its own
    docstring on why an uninterruptible internal sleep was a real bug: it
    bypassed the fetch's cancellation token and total-timeout budget). It
    returns the number of seconds still owed instead, which
    RequestsDocumentFetcher._wait_politely actually sleeps out - covered by
    its own tests in test_web_research_providers_fetcher.py."""

    def test_no_delay_on_the_very_first_fetch_to_a_host(self, clock):
        etq = CrawlEtiquette(default_crawl_delay_seconds=5.0)
        owed = etq.gate("https://example.com/page", _fetcher(200, ROBOTS_ALLOW_ALL_NO_DELAY))
        assert owed == 0.0

    def test_the_default_delay_is_owed_on_a_second_fetch_to_the_same_host(self, clock):
        etq = CrawlEtiquette(default_crawl_delay_seconds=5.0)
        etq.gate("https://example.com/page-a", _fetcher(200, ROBOTS_ALLOW_ALL_NO_DELAY))
        etq.record_fetch("https://example.com/page-a")

        clock.advance(1.0)  # only 1s of the 5s owed has passed
        owed = etq.gate("https://example.com/page-b", _fetcher(200, ROBOTS_ALLOW_ALL_NO_DELAY))

        assert owed == pytest.approx(4.0)

    def test_no_delay_if_enough_time_has_already_passed(self, clock):
        etq = CrawlEtiquette(default_crawl_delay_seconds=5.0)
        etq.gate("https://example.com/page-a", _fetcher(200, ROBOTS_ALLOW_ALL_NO_DELAY))
        etq.record_fetch("https://example.com/page-a")

        clock.advance(10.0)  # well past the 5s owed
        owed = etq.gate("https://example.com/page-b", _fetcher(200, ROBOTS_ALLOW_ALL_NO_DELAY))

        assert owed == 0.0

    def test_a_larger_robots_txt_crawl_delay_wins_over_the_default(self, clock):
        etq = CrawlEtiquette(default_crawl_delay_seconds=1.0)
        etq.gate("https://example.com/page-a", _fetcher(200, ROBOTS_WITH_DISALLOW_AND_DELAY))  # Crawl-delay: 5
        etq.record_fetch("https://example.com/page-a")

        clock.advance(0.0)
        owed = etq.gate("https://example.com/page-b", _fetcher(200, ROBOTS_WITH_DISALLOW_AND_DELAY))

        assert owed == pytest.approx(5.0)

    def test_the_default_wins_when_it_is_larger_than_robots_txt_own_crawl_delay(self, clock):
        # Never LESS polite than our own baseline, even if the site itself
        # asked for a shorter delay.
        etq = CrawlEtiquette(default_crawl_delay_seconds=10.0)
        etq.gate("https://example.com/page-a", _fetcher(200, ROBOTS_WITH_DISALLOW_AND_DELAY))  # Crawl-delay: 5
        etq.record_fetch("https://example.com/page-a")

        clock.advance(0.0)
        owed = etq.gate("https://example.com/page-b", _fetcher(200, ROBOTS_WITH_DISALLOW_AND_DELAY))

        assert owed == pytest.approx(10.0)

    def test_a_robots_txt_crawl_delay_larger_than_the_max_ceiling_is_clamped(self, clock):
        etq = CrawlEtiquette(default_crawl_delay_seconds=1.0, max_crawl_delay_seconds=3.0)
        etq.gate("https://example.com/page-a", _fetcher(200, ROBOTS_WITH_DISALLOW_AND_DELAY))  # Crawl-delay: 5
        etq.record_fetch("https://example.com/page-a")

        clock.advance(0.0)
        owed = etq.gate("https://example.com/page-b", _fetcher(200, ROBOTS_WITH_DISALLOW_AND_DELAY))

        assert owed == pytest.approx(3.0)  # clamped, not the robots.txt-declared 5.0

    def test_a_huge_crawl_delay_digit_string_does_not_raise_and_is_clamped(self, clock):
        # Regression: RobotFileParser.crawl_delay() returns a real Python
        # int for a numeric Crawl-delay directive, of UNBOUNDED size for a
        # many-digit value - float() on a sufficiently huge int raises
        # OverflowError rather than saturating. gate() must not propagate
        # that; a malformed-but-huge value should fail open on that one
        # directive (same as any other malformed robots.txt content) and
        # still be bounded by max_crawl_delay_seconds via the plain default.
        huge_delay_robots = b"User-agent: *\nDisallow:\nCrawl-delay: " + b"9" * 350 + b"\n"
        etq = CrawlEtiquette(default_crawl_delay_seconds=1.0, max_crawl_delay_seconds=3.0)
        etq.gate("https://example.com/page-a", _fetcher(200, huge_delay_robots))
        etq.record_fetch("https://example.com/page-a")

        clock.advance(0.0)
        owed = etq.gate("https://example.com/page-b", _fetcher(200, huge_delay_robots))

        assert owed == pytest.approx(1.0)  # falls back to the default, not an exception

    def test_different_hosts_never_delay_each_other(self, clock):
        etq = CrawlEtiquette(default_crawl_delay_seconds=5.0)
        etq.gate("https://a.example/page", _fetcher(200, ROBOTS_ALLOW_ALL_NO_DELAY))
        etq.record_fetch("https://a.example/page")

        clock.advance(0.0)
        owed = etq.gate("https://b.example/page", _fetcher(200, ROBOTS_ALLOW_ALL_NO_DELAY))

        assert owed == 0.0

    def test_record_fetch_without_a_prior_gate_call_still_seeds_the_next_delay(self, clock):
        etq = CrawlEtiquette(default_crawl_delay_seconds=5.0)
        etq.record_fetch("https://example.com/page-a")

        clock.advance(2.0)
        owed = etq.gate("https://example.com/page-b", _fetcher(200, ROBOTS_ALLOW_ALL_NO_DELAY))

        assert owed == pytest.approx(3.0)

    def test_same_host_different_ports_are_tracked_as_separate_origins(self, clock):
        etq = CrawlEtiquette(default_crawl_delay_seconds=5.0)
        etq.gate("https://example.com:8443/page", _fetcher(200, ROBOTS_ALLOW_ALL_NO_DELAY))
        etq.record_fetch("https://example.com:8443/page")

        clock.advance(0.0)
        owed = etq.gate("https://example.com/page", _fetcher(200, ROBOTS_ALLOW_ALL_NO_DELAY))

        assert owed == 0.0  # default port 443 is a DIFFERENT origin from :8443, no delay owed
