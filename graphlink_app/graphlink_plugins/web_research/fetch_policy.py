"""Network boundary for Web Research source fetching."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit


class URLPolicyError(ValueError):
    """Raised when a source URL is outside the research network policy."""


def canonicalize_url(url: str) -> str:
    parsed = urlsplit(str(url or "").strip())
    if not parsed.scheme or not parsed.hostname:
        return ""
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError:
        return ""
    netloc = hostname
    if parsed.username or parsed.password:
        return ""
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        netloc = f"{netloc}:{port}"
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def _is_public_address(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return bool(
        parsed.is_global
        and not parsed.is_loopback
        and not parsed.is_private
        and not parsed.is_link_local
        and not parsed.is_reserved
        and not parsed.is_multicast
        and not parsed.is_unspecified
    )


@dataclass(frozen=True)
class FetchPolicy:
    allowed_schemes: tuple[str, ...] = ("https",)
    max_redirects: int = 3
    connect_timeout_seconds: float = 8.0
    read_timeout_seconds: float = 15.0
    total_timeout_seconds: float = 30.0
    max_bytes: int = 2 * 1024 * 1024
    resolver: object = socket.getaddrinfo

    def _resolve_addresses(self, parsed: SplitResult) -> list[str]:
        if not parsed.hostname:
            raise URLPolicyError("Source URL has no hostname.")
        if parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
            raise URLPolicyError("Local source hosts are blocked by policy.")
        try:
            literal = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            literal = None
        if literal is not None:
            return [str(literal)]
        try:
            records = self.resolver(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise URLPolicyError(f"Could not resolve source host: {parsed.hostname}") from exc
        addresses = []
        for record in records:
            sockaddr = record[4]
            if sockaddr:
                addresses.append(str(sockaddr[0]))
        if not addresses:
            raise URLPolicyError(f"Source host did not resolve: {parsed.hostname}")
        return addresses

    def validate(self, url: str) -> str:
        canonical = canonicalize_url(url)
        if not canonical:
            raise URLPolicyError("Source URL is malformed or contains credentials.")
        parsed = urlsplit(canonical)
        if parsed.scheme not in self.allowed_schemes:
            raise URLPolicyError(f"Source scheme '{parsed.scheme}' is blocked by policy.")
        if parsed.username or parsed.password:
            raise URLPolicyError("Authenticated source URLs are blocked by policy.")
        if not parsed.hostname:
            raise URLPolicyError("Source URL has no hostname.")
        try:
            parsed.port
        except ValueError as exc:
            raise URLPolicyError("Source URL has an invalid port.") from exc
        for address in self._resolve_addresses(parsed):
            if not _is_public_address(address):
                raise URLPolicyError("Source resolves to a non-public network address.")
        return canonical
