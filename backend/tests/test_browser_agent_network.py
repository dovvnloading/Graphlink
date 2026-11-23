"""Offline security tests for the Browser Agent's pinned HTTPS boundary."""
from __future__ import annotations

import asyncio
import pytest

from graphlink_plugins.browser_agent import network
from graphlink_plugins.web_research.fetch_policy import FetchPolicy


def _policy(*addresses):
    def resolver(host, port, *, type):
        return [(None, None, None, None, (address, port)) for address in (addresses or ("93.184.216.34",))]
    return FetchPolicy(resolver=resolver)


@pytest.mark.parametrize("url", [
    "http://example.com/",
    "file:///etc/passwd",
    "https://localhost/",
    "https://127.0.0.1/",
    "https://[::1]/",
    "https://169.254.169.254/latest/meta-data/",
    "https://10.2.3.4/",
    "https://example.local/",
    "https://example.internal/",
    "https://example.test/",
    "https://user:password@example.com/",
    "https://@example.com/",
    "https://example.com:8443/",
    "https://example.com/\nheader",
    "https://example.com\\@evil.test/",
    "",
])
def test_unsafe_urls_are_rejected_without_dns(url):
    calls = []

    def resolver(*args, **kwargs):
        calls.append(args)
        raise AssertionError("Pre-consent validation must not resolve DNS.")

    with pytest.raises(network.BrowserPolicyError):
        network.canonical_public_url(url, FetchPolicy(resolver=resolver))
    assert calls == []


def test_initial_host_approval_precedes_dns_and_is_per_task(monkeypatch):
    events = []
    def resolver(host, port, *, type):
        events.append(("dns", host))
        return [(None, None, None, None, ("93.184.216.34", port))]

    def fetch(url, address, headers):
        events.append(("fetch", address))
        return network.BrowserResponse(200, {"content-type": "text/html"}, b"hello")
    monkeypatch.setattr(network, "_fetch_verified", fetch)

    async def approve(host, url):
        events.append(("approve", host))
        return True

    async def run():
        gate = network.BrowserNetworkGate("https://example.com/", approve, policy=FetchPolicy(resolver=resolver))
        one = await gate.fetch("https://example.com/", method="GET", navigation=True, headers={})
        two = await gate.fetch("https://example.com/next", method="GET", navigation=True, headers={})
        return one, two, gate

    first, second, gate = asyncio.run(run())
    assert first.body == second.body == b"hello"
    assert events == [
        ("approve", "example.com"), ("dns", "example.com"), ("fetch", "93.184.216.34"),
        ("dns", "example.com"), ("fetch", "93.184.216.34"),
    ]
    assert gate.request_count == 2


def test_denied_hosts_never_trigger_dns_or_network(monkeypatch):
    monkeypatch.setattr(network, "_fetch_verified", lambda *args: pytest.fail("Fetch after denial"))
    calls = []

    async def denied(host, url):
        calls.append(host)
        return False

    gate = network.BrowserNetworkGate("https://example.com/", denied, policy=_policy())
    with pytest.raises(network.BrowserPolicyError, match="approval"):
        asyncio.run(gate.fetch("https://example.com/", method="GET", navigation=True, headers={}))
    assert calls == ["example.com"]
    assert gate.blocked


def test_dns_rebinding_and_mixed_public_private_answers_fail_closed(monkeypatch):
    monkeypatch.setattr(network, "_fetch_verified", lambda *args: pytest.fail("Unsafe destination contacted"))

    async def approve(host, url):
        return True

    gate = network.BrowserNetworkGate(
        "https://example.com/", approve, policy=_policy("93.184.216.34", "127.0.0.1"),
    )
    with pytest.raises(network.BrowserPolicyError, match="non-public"):
        asyncio.run(gate.fetch("https://example.com/", method="GET", navigation=True, headers={}))
    assert gate.blocked


def test_cross_origin_subresources_are_denied_unless_allowlisted(monkeypatch):
    visits = []
    monkeypatch.setattr(network, "_fetch_verified", lambda url, ip, headers: (
        visits.append(url) or network.BrowserResponse(200, {}, b"body")
    ))
    approvals = []

    async def approve(host, url):
        approvals.append(host)
        return True

    async def run():
        gate = network.BrowserNetworkGate(
            "https://main.example/", approve, allowed_hosts=("cdn.example",), policy=_policy()
        )
        with pytest.raises(network.BrowserPolicyError, match="Unlisted"):
            await gate.fetch("https://tracking.example/t", method="GET", navigation=False, headers={})
        await gate.fetch("https://cdn.example/app.js", method="GET", navigation=False, headers={})
        await gate.fetch("https://cdn.example/b.css", method="GET", navigation=False, headers={})
        await gate.fetch("https://redirect.example/", method="GET", navigation=True, headers={})
        return gate

    gate = asyncio.run(run())
    assert approvals == ["cdn.example", "redirect.example"]
    assert len(visits) == 3
    assert gate.request_count == 3
    assert "tracking.example" in gate.blocked[0]


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
def test_non_get_http_requests_never_reach_consent_or_fetch(monkeypatch, method):
    monkeypatch.setattr(network, "_fetch_verified", lambda *args: pytest.fail("Network write attempted"))

    async def deny_any_request(host, url):
        pytest.fail("Non-GET must be stopped before approval")

    gate = network.BrowserNetworkGate("https://example.com/", deny_any_request)
    with pytest.raises(network.BrowserPolicyError, match="non-GET"):
        asyncio.run(gate.fetch("https://example.com/form", method=method, navigation=True, headers={}))
    assert gate.request_count == 0


def test_request_budget_caps_network_load(monkeypatch):
    monkeypatch.setattr(network, "_fetch_verified", lambda *args: network.BrowserResponse(200, {}, b"ok"))

    async def approve(host, url):
        return True

    async def run():
        gate = network.BrowserNetworkGate("https://example.com/", approve, policy=_policy())
        for _ in range(network.MAX_REQUESTS):
            await gate.fetch("https://example.com/", method="GET", navigation=True, headers={})
        with pytest.raises(network.BrowserPolicyError, match="budget"):
            await gate.fetch("https://example.com/", method="GET", navigation=True, headers={})
        return gate

    gate = asyncio.run(run())
    assert gate.request_count == network.MAX_REQUESTS


def test_pinned_transport_preserves_tls_hostname_and_does_not_follow_redirect(monkeypatch):
    sent = []

    class FakeResponse:
        status = 302
        def getheader(self, name):
            return {"Content-Length": "4", "Content-Disposition": "inline"}.get(name)
        def read(self, cap):
            assert cap == network.MAX_RESOURCE_BYTES + 1
            return b"next"
        def getheaders(self):
            return [("Location", "https://elsewhere.example/new"), ("Content-Type", "text/html")]

    class FakeConnection:
        def __init__(self, host, port, pinned_ip):
            sent.append(("connect", host, port, pinned_ip))
        def request(self, method, path, *, headers):
            sent.append(("request", method, path, headers))
        def getresponse(self):
            return FakeResponse()
        def close(self):
            sent.append(("closed",))

    monkeypatch.setattr(network, "_PinnedConnection", FakeConnection)
    result = network._fetch_verified(
        "https://example.com/path?q=1", "93.184.216.34",
        {"Authorization": "sensitive", "Cookie": "ephemeral=1"},
    )
    assert result.status == 302
    assert result.headers["location"] == "https://elsewhere.example/new"
    assert sent[0] == ("connect", "example.com", 443, "93.184.216.34")
    assert sent[1][1:3] == ("GET", "/path?q=1")
    assert "Authorization" not in sent[1][3]
    assert sent[1][3]["Host"] == "example.com"
    assert sent[-1] == ("closed",)


def test_oversize_and_download_responses_are_rejected(monkeypatch):
    class FakeConnection:
        def __init__(self, *args):
            pass
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            return self
        def getheader(self, name):
            if name == "Content-Length":
                return str(network.MAX_RESOURCE_BYTES + 1)
            return ""
        def close(self):
            pass

    monkeypatch.setattr(network, "_PinnedConnection", FakeConnection)
    with pytest.raises(network.BrowserPolicyError, match="larger"):
        network._fetch_verified("https://example.com/", "93.184.216.34", {})
