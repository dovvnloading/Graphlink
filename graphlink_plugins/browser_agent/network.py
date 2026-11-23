"""Pinned-connection, read-only network boundary for Browser Agent.

Every Chromium request is intercepted and fulfilled from this Python transport.
Chromium itself gets a failing DNS resolver: it never contacts a remote host.
Consent is obtained before the first DNS lookup for a host. Every HTTP request
is independently validated and connected to the already-validated public IP,
closing the DNS-rebinding gap in a check-then-Chromium-connect design.
"""
from __future__ import annotations

import asyncio
import http.client
import socket
import ssl
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from graphlink_plugins.web_research.fetch_policy import FetchPolicy, URLPolicyError


class BrowserPolicyError(ValueError):
    """A refused or unsafe browser operation."""


MAX_RESOURCE_BYTES = 2 * 1024 * 1024
MAX_REQUESTS = 80
MAX_HOSTS = 8
MAX_URL_LENGTH = 2048
_ALLOWED_HEADERS = frozenset({
    "accept", "accept-language", "user-agent", "cookie", "referer", "origin",
    "sec-fetch-mode", "sec-fetch-site", "sec-fetch-dest",
})
_RETURN_HEADERS = frozenset({
    "content-type", "content-encoding", "cache-control", "content-security-policy",
    "access-control-allow-origin", "access-control-allow-credentials",
    "access-control-allow-headers", "access-control-allow-methods",
    "cross-origin-resource-policy", "cross-origin-embedder-policy",
    "cross-origin-opener-policy", "location", "etag", "last-modified", "vary",
    "x-content-type-options", "set-cookie",
})


@dataclass(frozen=True)
class BrowserResponse:
    status: int
    headers: dict[str, str]
    body: bytes


class _PinnedConnection(http.client.HTTPSConnection):
    """TLS still checks the original hostname; only the TCP peer is pinned."""

    def __init__(self, host: str, port: int, pinned_ip: str):
        super().__init__(host, port, timeout=8.0, context=ssl.create_default_context())
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        raw = socket.create_connection((self._pinned_ip, self.port), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def _fetch_verified(url: str, pinned_ip: str, headers: dict[str, str]) -> BrowserResponse:
    """No redirects here: a 30x is surfaced to Chromium and re-gated on its next request."""
    target = urlsplit(url)
    host = target.hostname
    if not host:
        raise BrowserPolicyError("Browser request has no hostname.")
    request_headers = {key: val for key, val in headers.items() if key.lower() in _ALLOWED_HEADERS}
    request_headers["Accept-Encoding"] = "identity"
    request_headers["Host"] = target.netloc
    path = target.path or "/"
    if target.query:
        path += "?" + target.query
    connection = _PinnedConnection(host, target.port or 443, pinned_ip)
    try:
        connection.request("GET", path, headers=request_headers)
        response = connection.getresponse()
        if response.getheader("Content-Length"):
            try:
                declared_length = int(response.getheader("Content-Length") or "0")
            except ValueError as exc:
                raise BrowserPolicyError("Invalid browser response length.") from exc
            if declared_length > MAX_RESOURCE_BYTES:
                raise BrowserPolicyError("Browser resource is larger than 2 MiB.")
        body = response.read(MAX_RESOURCE_BYTES + 1)
        if len(body) > MAX_RESOURCE_BYTES:
            raise BrowserPolicyError("Browser resource exceeded 2 MiB.")
        if (response.getheader("Content-Disposition") or "").lower().startswith("attachment"):
            raise BrowserPolicyError("Browser downloads are disabled.")
        allowed = {
            key.lower(): val for key, val in response.getheaders()
            if key.lower() in _RETURN_HEADERS
        }
        return BrowserResponse(response.status, allowed, body)
    except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
        raise BrowserPolicyError("Public HTTPS resource could not be fetched.") from exc
    finally:
        connection.close()


def canonical_public_url(raw: str, policy: FetchPolicy | None = None) -> str:
    """DNS-free validation, safe to use before approval."""
    if not isinstance(raw, str) or len(raw) > MAX_URL_LENGTH:
        raise BrowserPolicyError("Browser URL must be at most 2048 characters.")
    gate = policy or FetchPolicy()
    try:
        canonical = gate.validate_url(raw)
    except URLPolicyError as exc:
        raise BrowserPolicyError(str(exc)) from exc
    host = urlsplit(canonical).hostname or ""
    if host.endswith((".local", ".internal", ".localhost", ".test", ".invalid")):
        raise BrowserPolicyError("Local or special-use domain is not permitted.")
    if urlsplit(canonical).port not in (None, 443):
        raise BrowserPolicyError("Browser Agent only connects to HTTPS port 443.")
    return canonical


class BrowserNetworkGate:
    """Task-local request budget, host consent, and pinned HTTPS transport."""

    def __init__(
        self,
        initial_url: str,
        approve_host: Callable[[str, str], Awaitable[bool]],
        *,
        allowed_hosts: tuple[str, ...] = (),
        policy: FetchPolicy | None = None,
    ):
        self._policy = policy or FetchPolicy()
        self.initial_url = canonical_public_url(initial_url, self._policy)
        self._initial_host = urlsplit(self.initial_url).hostname or ""
        if len(allowed_hosts) > MAX_HOSTS - 1:
            raise BrowserPolicyError("At most seven additional browser hosts may be allowed.")
        extras: set[str] = set()
        for entry in allowed_hosts:
            if not isinstance(entry, str) or not entry or "/" in entry or ":" in entry:
                raise BrowserPolicyError("Additional browser hosts must be bare DNS hostnames.")
            canonical = canonical_public_url("https://" + entry + "/", self._policy)
            extras.add(urlsplit(canonical).hostname or "")
        self._allowed_hosts = extras | {self._initial_host}
        self._approve_host = approve_host
        self._approved_hosts: set[str] = set()
        self._seen_hosts: set[str] = set()
        self.request_count = 0
        self.blocked: list[str] = []
        self._lock = asyncio.Lock()

    def _block(self, reason: str) -> None:
        if len(self.blocked) < 16:
            self.blocked.append(reason[:200])

    async def fetch(self, url: str, *, method: str, navigation: bool, headers: dict[str, str]) -> BrowserResponse:
        """Route handler calls this; non-GET methods never touch the network."""
        if method.upper() != "GET":
            self._block("Blocked non-read HTTP method: " + method[:20])
            raise BrowserPolicyError("Browser Agent blocks all non-GET HTTP requests.")
        try:
            canonical = canonical_public_url(url, self._policy)
        except BrowserPolicyError:
            self._block("Blocked unsafe browser URL.")
            raise
        host = urlsplit(canonical).hostname or ""
        # A top-level navigation may cross domains, but only after new host
        # consent. Third-party subresources require explicit allowed_hosts.
        if not navigation and host not in self._allowed_hosts:
            self._block("Blocked unlisted subresource host: " + host)
            raise BrowserPolicyError("Unlisted third-party browser resource.")
        async with self._lock:
            if self.request_count >= MAX_REQUESTS:
                raise BrowserPolicyError("Browser request budget reached (80).")
            if host not in self._seen_hosts and len(self._seen_hosts) >= MAX_HOSTS:
                raise BrowserPolicyError("Browser host budget reached (8).")
            self.request_count += 1
            self._seen_hosts.add(host)
            if host not in self._approved_hosts:
                if not await self._approve_host(host, canonical):
                    self._block("Approval denied for browser host: " + host)
                    raise BrowserPolicyError("Browser host approval was denied.")
                self._approved_hosts.add(host)
        # DNS only occurs after human approval. Never use the original host
        # as the TCP destination after this check: FetchPolicy pins an IP.
        try:
            target = await asyncio.to_thread(self._policy.validate, canonical)
        except URLPolicyError as exc:
            self._block("Blocked private/non-public network destination.")
            raise BrowserPolicyError(str(exc)) from exc
        return await asyncio.to_thread(_fetch_verified, target.canonical_url, target.pinned_ip, headers)
