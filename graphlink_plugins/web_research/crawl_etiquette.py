"""Crawl etiquette for the Web Research fetch path (ADR-004 stage 4.5, the
robots.txt/rate-limiting half of audit finding M6 - fetch_policy.py/
providers.py's PinnedHTTPAdapter already close the other half, the
validate-then-connect DNS-rebinding TOCTOU).

Honors robots.txt Disallow rules and a per-host Crawl-delay, falling back
to a fixed polite delay when a host's robots.txt sets none. Deliberately
does NOT use urllib.robotparser.RobotFileParser.read() - that method
fetches robots.txt via urllib.request.urlopen internally, which has none
of fetch_policy.py's SSRF protections (no scheme allowlist, no IP-literal/
private-range check, no size cap) and would reintroduce exactly the kind
of unvalidated outbound request this ADR stage exists to close. Instead,
the caller fetches robots.txt through the SAME pinned-IP mechanism the
main content fetch uses (see providers.py) and hands the raw bytes here;
only the PARSING (RobotFileParser.parse()) is reused from the stdlib.
"""

from __future__ import annotations

import time
import urllib.robotparser
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlsplit


class RobotsDisallowedError(ValueError):
    """Raised when robots.txt disallows fetching a URL for our user agent."""


# Returns (status_code, body_bytes) on a completed HTTP response (any status,
# including 4xx/5xx - the caller already applied SSRF/IP-pinning validation
# to reach this point), or None if the request could not complete at all
# (network error, timeout, DNS failure). None means "robots.txt state
# unknown" - handled as fail-open below, same as a real crawler tolerating a
# transiently-unreachable robots.txt rather than refusing to ever crawl a
# site again.
RobotsFetcher = Callable[[str], "tuple[int, bytes] | None"]


@dataclass
class CrawlEtiquette:
    user_agent: str = "Graphlink-WebResearch/1.0"
    default_crawl_delay_seconds: float = 2.0
    # Adversarial-review finding: a malicious/misconfigured robots.txt could
    # otherwise demand an ARBITRARILY large Crawl-delay (a many-digit number
    # is valid input to RobotFileParser.crawl_delay(), which returns a real
    # Python int of unbounded size) - this caps how polite gate() will ever
    # ask the caller to be, regardless of what a site's robots.txt claims.
    max_crawl_delay_seconds: float = 30.0

    # Keyed by "scheme://host[:port]" (the robots.txt origin, per RFC 9309 -
    # rules never cross origins). None means "checked, robots.txt was
    # unreachable or the parse failed" (fail-open: no explicit rules, but the
    # fixed default delay still applies below).
    _robots: dict = field(default_factory=dict, repr=False, compare=False)
    _last_fetch_at: dict = field(default_factory=dict, repr=False, compare=False)

    @staticmethod
    def _origin(url: str) -> str:
        parsed = urlsplit(url)
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return f"{parsed.scheme}://{netloc}"

    def _robots_url(self, url: str) -> str:
        return f"{self._origin(url)}/robots.txt"

    def _load_robots(self, url: str, fetch: RobotsFetcher) -> "urllib.robotparser.RobotFileParser | None":
        origin = self._origin(url)
        if origin in self._robots:
            return self._robots[origin]

        parser = urllib.robotparser.RobotFileParser()
        try:
            outcome = fetch(self._robots_url(url))
        except Exception:
            outcome = None

        if outcome is None:
            # Unreachable (network error/timeout/blocked by our own SSRF
            # policy) - fail open, matching real crawlers tolerating a
            # transiently-down robots.txt rather than refusing to crawl the
            # site at all. The fixed default delay below still applies.
            #
            # Deliberately NOT cached (unlike every other outcome below):
            # an adversarial-review finding pointed out that caching a
            # single timeout/network-failure as "no rules, permanently, for
            # the rest of this run" gives a malicious site a deterministic,
            # one-shot way to disable its own robots.txt enforcement for
            # the whole research run - just let /robots.txt stall past
            # ROBOTS_TXT_TIMEOUT_SECONDS once. Leaving this uncached means
            # the NEXT gate() call for this origin (a later redirect hop or
            # a later source on the same host) re-attempts the fetch
            # instead of trusting a single failed attempt forever.
            return None

        status_code, body = outcome
        if status_code == 200:
            try:
                parser.parse(body.decode("utf-8", errors="replace").splitlines())
            except Exception:
                self._robots[origin] = None
                return None
            self._robots[origin] = parser
            return parser
        if status_code in (401, 403):
            # Mirrors RobotFileParser.read()'s own convention: a robots.txt
            # we're not authorized to see is treated as "no bots wanted here
            # at all", not as "no rules exist".
            parser.disallow_all = True
            self._robots[origin] = parser
            return parser
        # Any other non-200 (404 included) - no robots.txt in effect,
        # standard convention: everything is allowed, no explicit delay.
        self._robots[origin] = None
        return None

    def gate(self, url: str, fetch_robots: RobotsFetcher) -> float:
        """Call once per actual fetch attempt (i.e. once per redirect hop -
        the same granularity FetchPolicy.validate() is already called at).
        Raises RobotsDisallowedError if this URL is off-limits for our user
        agent; otherwise returns the number of seconds still owed before the
        caller's real request would be polite (0.0 if none).

        Deliberately does NOT sleep itself (adversarial-review fix): an
        earlier version called time.sleep() directly here, which is
        uninterruptible - it ran outside this fetch's own CancellationToken
        checks and total_timeout_seconds budget, so a malicious/
        misconfigured Crawl-delay (or even just this class's own default)
        could hang the calling worker thread for the full delay with no way
        for the user's Stop button or the fetch's own timeout to cut it
        short. The caller (RequestsDocumentFetcher.fetch()) is responsible
        for an interruptible, budget-aware wait using the returned value -
        see that method's own comment on why. The returned value is capped
        at max_crawl_delay_seconds regardless of what robots.txt asks for,
        as defense in depth even if a caller ever waited on it blindly."""
        parser = self._load_robots(url, fetch_robots)
        if parser is not None and not parser.can_fetch(self.user_agent, url):
            raise RobotsDisallowedError(f"robots.txt disallows fetching {url}.")

        origin = self._origin(url)
        # The LARGER of our own baseline and whatever this host's own
        # robots.txt asks for - never less polite than either one. A host
        # asking for MORE delay than our default gets it; a host that says
        # nothing, or asks for LESS than our default, still gets our
        # baseline rather than being crawled faster than we'd crawl an
        # unlisted host. Always capped at max_crawl_delay_seconds - a
        # malicious robots.txt cannot demand more than that (adversarial-
        # review finding: RobotFileParser.crawl_delay() happily returns an
        # arbitrary-precision int for a many-digit Crawl-delay value, and
        # float() on a sufficiently huge int raises OverflowError rather
        # than saturating - caught and ignored below, same as any other
        # malformed-robots.txt case: fail open on this one directive only).
        delay = self.default_crawl_delay_seconds
        if parser is not None:
            robots_delay = parser.crawl_delay(self.user_agent)
            if robots_delay is not None:
                try:
                    delay = max(delay, float(robots_delay))
                except (OverflowError, ValueError, TypeError):
                    pass
        delay = min(delay, self.max_crawl_delay_seconds)

        last = self._last_fetch_at.get(origin)
        if last is None:
            return 0.0
        owed = delay - (time.monotonic() - last)
        return max(0.0, owed)

    def record_fetch(self, url: str) -> None:
        self._last_fetch_at[self._origin(url)] = time.monotonic()
