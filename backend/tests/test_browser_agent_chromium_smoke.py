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
