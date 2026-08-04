"""Tests for graphlink_plugins/web_research/fetch_policy.py - the SSRF
network boundary for Web Research source fetching (ADR-004 stage 4.5,
audit finding M6).

No prior test coverage existed for this module at all (confirmed by recon
before this stage - the only reference in graphlink_app/tests/ was already
deleted by the Qt-removal cutover), so this covers both the pre-existing
scheme/private-range/localhost checks and the new ValidatedTarget/IP-pinning
return value stage 4.5 adds.
"""

from __future__ import annotations

import socket

import pytest

from graphlink_plugins.web_research.fetch_policy import (
    FetchPolicy,
    URLPolicyError,
    ValidatedTarget,
    _is_public_address,
    canonicalize_url,
)


def _fake_resolver(*addresses: str):
    """Stands in for socket.getaddrinfo - same call signature FetchPolicy
    uses (host, port, type=...), same (family, type, proto, canonname,
    sockaddr) record shape, so _resolve_addresses' own record[4][0]
    extraction works unchanged against it."""

    def resolver(host, port, type=None):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port)) for address in addresses]

    return resolver


def _raising_resolver(exc: Exception):
    def resolver(host, port, type=None):
        raise exc

    return resolver


class TestCanonicalizeUrl:
    def test_lowercases_scheme_and_host(self):
        assert canonicalize_url("HTTPS://Example.COM/Path") == "https://example.com/Path"

    def test_strips_the_default_port_for_the_scheme(self):
        assert canonicalize_url("https://example.com:443/") == "https://example.com/"
        assert canonicalize_url("http://example.com:80/") == "http://example.com/"

    def test_keeps_a_non_default_port(self):
        assert canonicalize_url("https://example.com:8443/") == "https://example.com:8443/"

    def test_defaults_an_empty_path_to_a_single_slash(self):
        assert canonicalize_url("https://example.com") == "https://example.com/"

    def test_drops_the_fragment(self):
        assert canonicalize_url("https://example.com/page#section") == "https://example.com/page"

    def test_rejects_a_url_with_userinfo(self):
        assert canonicalize_url("https://user:pass@example.com/") == ""

    def test_rejects_a_url_with_no_scheme_or_hostname(self):
        assert canonicalize_url("not-a-url") == ""
        assert canonicalize_url("") == ""

    def test_rejects_an_invalid_port(self):
        assert canonicalize_url("https://example.com:not-a-port/") == ""


class TestIsPublicAddress:
    @pytest.mark.parametrize(
        "address",
        ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"],
    )
    def test_true_for_real_public_addresses(self, address):
        assert _is_public_address(address) is True

    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",  # loopback
            "10.0.0.1",  # private
            "172.16.0.1",  # private
            "192.168.1.1",  # private
            "169.254.1.1",  # link-local
            "0.0.0.0",  # unspecified
            "224.0.0.1",  # multicast
            "::1",  # loopback (IPv6)
            "fc00::1",  # unique local (private, IPv6)
            "fe80::1",  # link-local (IPv6)
        ],
    )
    def test_false_for_non_public_addresses(self, address):
        assert _is_public_address(address) is False

    def test_false_for_a_non_ip_string(self):
        assert _is_public_address("not-an-ip") is False


class TestFetchPolicyValidate:
    def test_a_public_ip_literal_succeeds_and_pins_that_exact_ip(self):
        policy = FetchPolicy()
        result = policy.validate("https://8.8.8.8/path")
        assert isinstance(result, ValidatedTarget)
        assert result.canonical_url == "https://8.8.8.8/path"
        assert result.pinned_ip == "8.8.8.8"

    @pytest.mark.parametrize("literal", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.1.1", "0.0.0.0"])
    def test_a_non_public_ip_literal_is_rejected(self, literal):
        policy = FetchPolicy()
        with pytest.raises(URLPolicyError, match="non-public"):
            policy.validate(f"https://{literal}/path")

    @pytest.mark.parametrize("host", ["localhost", "LOCALHOST", "localhost.localdomain"])
    def test_localhost_hostnames_are_blocked_before_even_resolving(self, host):
        policy = FetchPolicy(resolver=_raising_resolver(AssertionError("must not resolve localhost")))
        with pytest.raises(URLPolicyError, match="[Ll]ocal"):
            policy.validate(f"https://{host}/path")

    def test_a_hostname_resolving_to_a_public_address_succeeds_and_pins_it(self):
        policy = FetchPolicy(resolver=_fake_resolver("93.184.216.34"))
        result = policy.validate("https://example.com/path")
        assert result.canonical_url == "https://example.com/path"
        assert result.pinned_ip == "93.184.216.34"

    def test_a_hostname_resolving_to_a_private_address_is_rejected(self):
        # The DNS-rebinding scenario stage 4.5 is really about: a hostname
        # that resolves somewhere unsafe must never reach the connect step.
        policy = FetchPolicy(resolver=_fake_resolver("127.0.0.1"))
        with pytest.raises(URLPolicyError, match="non-public"):
            policy.validate("https://attacker.example/path")

    def test_if_any_resolved_address_is_non_public_the_whole_host_is_rejected(self):
        # Conservative-by-design: a host that resolves to BOTH a public and
        # a private address is blocked entirely, not just steered to the
        # public one - preserved from the pre-stage-4.5 behavior.
        policy = FetchPolicy(resolver=_fake_resolver("93.184.216.34", "127.0.0.1"))
        with pytest.raises(URLPolicyError, match="non-public"):
            policy.validate("https://mixed.example/path")

    def test_pins_the_first_address_when_multiple_public_addresses_resolve(self):
        policy = FetchPolicy(resolver=_fake_resolver("93.184.216.34", "1.1.1.1"))
        result = policy.validate("https://multi.example/path")
        assert result.pinned_ip == "93.184.216.34"

    def test_a_disallowed_scheme_is_rejected(self):
        policy = FetchPolicy(allowed_schemes=("https",))
        with pytest.raises(URLPolicyError, match="scheme"):
            policy.validate("http://example.com/path")

    def test_a_url_with_userinfo_is_rejected(self):
        policy = FetchPolicy()
        with pytest.raises(URLPolicyError):
            policy.validate("https://user:pass@example.com/path")

    def test_a_malformed_url_is_rejected(self):
        policy = FetchPolicy()
        with pytest.raises(URLPolicyError, match="malformed"):
            policy.validate("not a url at all")

    def test_a_dns_resolution_failure_is_surfaced_clearly(self):
        policy = FetchPolicy(resolver=_raising_resolver(OSError("name resolution failed")))
        with pytest.raises(URLPolicyError, match="Could not resolve"):
            policy.validate("https://nonexistent.example/path")

    def test_an_empty_resolution_result_is_rejected(self):
        policy = FetchPolicy(resolver=lambda host, port, type=None: [])
        with pytest.raises(URLPolicyError, match="did not resolve"):
            policy.validate("https://empty.example/path")

    def test_repeated_validation_of_the_same_dns_rebinding_host_never_succeeds_via_the_public_answer_alone(self):
        # Simulates the actual TOCTOU scenario stage 4.5 closes: an
        # attacker's DNS server answers PUBLIC on one lookup and PRIVATE on
        # the next. Both individual validate() calls must independently
        # enforce the policy against whatever THEY were told - there is no
        # cross-call memory that could be tricked into treating a
        # previously-validated host as permanently safe.
        first = FetchPolicy(resolver=_fake_resolver("93.184.216.34"))
        assert first.validate("https://rebinding.example/path").pinned_ip == "93.184.216.34"

        second = FetchPolicy(resolver=_fake_resolver("127.0.0.1"))
        with pytest.raises(URLPolicyError, match="non-public"):
            second.validate("https://rebinding.example/path")
