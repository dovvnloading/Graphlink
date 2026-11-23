"""Chromium smoke coverage for the *optional* Python Browser Agent runtime.

Default Python CI skips this without optional Playwright; the targeted GitHub
Actions browser-agent-smoke workflow installs Playwright and Chromium, and runs
this test with real Chromium while still prohibiting outbound network traffic.
"""
from __future__ import annotations

import asyncio

import pytest

from graphlink_plugins.browser_agent import network
from graphlink_plugins.browser_agent.runner import run_browser
from graphlink_plugins.web_research.fetch_policy import FetchPolicy

_HTML = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Offline pricing fixture</title></head>
<body><h1>Public plan pricing</h1>
<button id="reveal" type="button" aria-label="Reveal the plan"
onclick="document.getElementById('price').textContent='$20/month'">Reveal</button>
<span id="price">Hidden</span>
<table><tr><th>Plan</th><th>Price</th></tr>
<tr><td>Standard</td><td>$20</td></tr></table>
<a href="https://example.com/details">Read details</a>
</body></html>"""


def test_real_playwright_chromium_renders_clicks_and_captures_without_network(monkeypatch):
    playwright = pytest.importorskip("playwright.async_api")
    requests: list[tuple[str, str]] = []
    approvals: list[tuple[str, str]] = []

    async def offline_fetch(self, url, *, method, navigation, headers):
        requests.append((method, url))
        # No genuine HTTP/DNS happens in this test. The pinned transport's
        # independent security tests cover SSRF, GET-only and IP pinning.
        if method == "GET" and url == "https://example.com/":
            return network.BrowserResponse(
                status=200, headers={"content-type": "text/html; charset=utf-8"}, body=_HTML,
            )
        raise network.BrowserPolicyError("Offline fixture blocks every other request.")

    monkeypatch.setattr(network.BrowserNetworkGate, "fetch", offline_fetch)

    async def host_approval(host: str, url: str) -> bool:
        approvals.append(("host", host))
        return True

    async def action_approval(kind: str, selector: str, url: str, detail: dict[str, str]) -> bool:
        approvals.append((kind, detail["label"]))
        return kind == "click" and selector == "#reveal" and detail["label"] == "Reveal the plan"

    result = asyncio.run(run_browser(
        "https://example.com/",
        [{"kind": "click", "selector": "#reveal"}],
        host_approval, action_approval,
        screenshot=True,
        playwright_factory=playwright.async_playwright,
    ))

    assert "Public plan pricing" in result.text
    assert "$20/month" in result.text
    assert result.tables[0][1] == ["Standard", "$20"]
    assert result.links[0]["url"] == "https://example.com/details"
    assert result.screenshot is not None and result.screenshot.startswith(b"\x89PNG\r\n\x1a\n")
    assert result.title == "Offline pricing fixture"
    assert any(item["selector"] == "#reveal" for item in result.controls)
    assert result.url == "https://example.com/"
    assert requests and all(method == "GET" and url == "https://example.com/" for method, url in requests)
    assert approvals == [("click", "Reveal the plan")]


def test_real_chromium_gate_blocks_post_and_unlisted_hosts_before_dns(monkeypatch):
    """Exercise the *real* interceptor + policy + route callback in Chromium.

    The sole permitted GET is supplied by a fixture-backed fake pinned
    transport. DNS resolution is fake too. No external socket is possible.
    """
    playwright = pytest.importorskip("playwright.async_api")
    events: list[tuple[str, str]] = []
    page_html = b"""<!doctype html><html lang="en">
      <head><title>Blocked resources</title></head><body>
      <h1>Browser safety boundary</h1>
      <script>
        document.addEventListener('DOMContentLoaded', () => {
          fetch('/submit', {method: 'POST', body: 'not sent'}).catch(() => {});
          fetch('https://unlisted.example/pixel').catch(() => {});
        });
      </script>
      </body></html>"""

    def resolver(host, port, *, type):
        events.append(("dns", host))
        if host != "example.com":
            raise AssertionError("An unapproved host reached DNS.")
        return [(None, None, None, None, ("93.184.216.34", port))]

    real_policy = FetchPolicy(resolver=resolver)
    monkeypatch.setattr(network, "FetchPolicy", lambda: real_policy)

    def fixture_fetch(url, pinned_ip, headers):
        events.append(("fetch", url))
        assert pinned_ip == "93.184.216.34"
        assert url == "https://example.com/"
        return network.BrowserResponse(200, {"content-type": "text/html"}, page_html)

    monkeypatch.setattr(network, "_fetch_verified", fixture_fetch)

    async def approve(host, url):
        events.append(("approve", host))
        assert url == "https://example.com/"
        return True

    async def action(kind, selector, url, detail):
        pytest.fail("No interactive action was requested in this test.")

    capture = asyncio.run(run_browser(
        "https://example.com/",
        [{"kind": "wait", "milliseconds": 500}],
        approve, action,
        playwright_factory=playwright.async_playwright,
    ))
    assert "Browser safety boundary" in capture.text
    assert events == [
        ("approve", "example.com"),
        ("dns", "example.com"),
        ("fetch", "https://example.com/"),
    ]
    assert any("non-read" in reason for reason in capture.blocked)
    assert any("unlisted" in reason for reason in capture.blocked)
    assert capture.request_count == 1
