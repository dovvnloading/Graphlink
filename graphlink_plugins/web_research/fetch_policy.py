"""Network boundary for Web Research source fetching."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Any, Callable, Sequence
from urllib.parse import SplitResult, urlsplit, urlunsplit


class URLPolicyError(ValueError):
    """Raised when a source URL is outside the research network policy."""


def canonicalize_url(url: str) -> str:
    raw = str(url or "")
    # urlsplit silently strips CR/LF/TAB and leading C0 controls. Never let
    # an approval disclosure describe a different URL than the one fetched.
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        return ""
    try:
        parsed = urlsplit(raw.strip())
        if not parsed.scheme or not parsed.hostname or "\\" in parsed.netloc:
            return ""
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname.lower().rstrip(".")
        port = parsed.port
        # Even an empty userinfo component is forbidden (https://@host/).
        if parsed.username is not None or parsed.password is not None:
            return ""
    except ValueError:
        # Invalid IPv6 brackets, NFKC-ambiguous hosts and invalid ports all
        # become a policy rejection, not an uncaught URL parser exception.
        return ""
    # urlsplit().hostname strips brackets from IPv6 literals; urlunsplit()
    # does not put them back, so a valid literal becomes an invalid netloc.
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        netloc = f"{netloc}:{port}"
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


@dataclass(frozen=True)
class ValidatedTarget:
    """FetchPolicy.validate()'s result: the canonical URL (for the Host
    header, TLS SNI, and cert hostname verification) plus the ONE public IP
    address the caller must actually connect the socket to. Keeping both
    together, rather than the URL alone, is what closes the DNS-rebinding
    TOCTOU - see validate()'s own comment."""

    canonical_url: str
    pinned_ip: str


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
    # Injected so tests (and any future caller with its own resolution
    # policy) can substitute a resolver; the shape that matters is
    # socket.getaddrinfo's - called with a host and a port, returning
    # records whose fifth element is the sockaddr.
    resolver: Callable[..., Sequence[Any]] = socket.getaddrinfo

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

    def validate_url(self, url: str) -> str:
        """DNS-free syntax/literal-IP validation to run BEFORE user consent.

        An unapproved host can encode data in DNS labels. Calling validate()
        before an approval leaks that host to DNS even when the user denies
        the request, including after an HTTP redirect. Do not resolve here.
        """
        canonical = canonicalize_url(url)
        if not canonical:
            raise URLPolicyError("Source URL is malformed or contains credentials.")
        parsed = urlsplit(canonical)
        if parsed.scheme not in self.allowed_schemes:
            raise URLPolicyError(f"Source scheme '{parsed.scheme}' is blocked by policy.")
        if not parsed.hostname:
            raise URLPolicyError("Source URL has no hostname.")
        if parsed.hostname in {"localhost", "localhost.localdomain"}:
            raise URLPolicyError("Local source hosts are blocked by policy.")
        try:
            literal = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            pass
        else:
            if not _is_public_address(str(literal)):
                raise URLPolicyError("Source resolves to a non-public network address.")
        return canonical

    def validate(self, url: str) -> ValidatedTarget:
        canonical = self.validate_url(url)
        parsed = urlsplit(canonical)
        addresses = self._resolve_addresses(parsed)
        for address in addresses:
            if not _is_public_address(address):
                raise URLPolicyError("Source resolves to a non-public network address.")
        # ADR-004 stage 4.5 (audit finding M6): callers used to discard the
        # validated address(es) entirely and let the HTTP client re-resolve
        # the hostname to actually connect - a validate-then-connect TOCTOU
        # a DNS-rebinding attacker can exploit (return a public IP here,
        # then a private/loopback one for the real connection moments
        # later). Returning the FIRST validated address alongside the
        # canonical URL lets the caller pin its actual socket connection to
        # this exact, already-checked IP instead of resolving again.
        return ValidatedTarget(canonical_url=canonical, pinned_ip=addresses[0])
