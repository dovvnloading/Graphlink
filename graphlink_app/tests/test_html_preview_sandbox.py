"""Tests for the HTML Renderer network-egress sandbox (graphlink_html_view).

Regression coverage for the HTML-Renderer arbitrary-egress issue
(doc/ARCHITECTURE_REVIEW_FINDINGS.md): the HTML Renderer node auto-renders AI-generated
and session-loaded markup in a QWebEngineView with JavaScript enabled and no restrictions,
so an injected <script>/onerror payload could exfiltrate page content to a remote host or
hit the user's localhost/intranet (SSRF/CSRF). A local-only request interceptor now blocks
every request whose scheme would leave the machine.

The block/allow decision is factored into the pure function preview_url_is_allowed(QUrl),
tested here directly so coverage doesn't require a live QtWebEngine/GPU context (which
isn't available on the headless CI runner, and where QWebEngineUrlRequestInfo can't be
constructed by hand anyway).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from PySide6.QtCore import QUrl

from graphlink_html_view import preview_url_is_allowed


class TestLocalSchemesAreAllowed:
    @pytest.mark.parametrize("url", [
        "about:blank",
        "data:text/html;base64,PHA+aGk8L3A+",
        "data:image/png;base64,iVBORw0KGgo=",
        "blob:about:blank/1234-5678",
    ])
    def test_self_contained_local_schemes_pass(self, url):
        assert preview_url_is_allowed(QUrl(url)) is True


class TestNetworkAndDiskSchemesAreBlocked:
    @pytest.mark.parametrize("url", [
        "http://attacker.example/x?d=stolen",       # exfiltration
        "https://attacker.example/x?d=stolen",       # exfiltration over TLS
        "http://127.0.0.1:8080/admin/shutdown",      # localhost SSRF/CSRF
        "http://localhost/",                          # localhost by name
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata endpoint
        "http://192.168.1.1/",                        # intranet
        "file:///C:/Windows/win.ini",                # local disk read
        "ftp://example.com/secret",
        "ws://attacker.example/sock",
        "wss://attacker.example/sock",
    ])
    def test_egress_and_disk_schemes_are_blocked(self, url):
        assert preview_url_is_allowed(QUrl(url)) is False


class TestDefensiveEdges:
    def test_scheme_matching_is_case_insensitive(self):
        assert preview_url_is_allowed(QUrl("HTTP://attacker.example")) is False
        assert preview_url_is_allowed(QUrl("DATA:text/html,hi")) is True

    def test_a_non_url_object_is_blocked_not_crashed(self):
        # Fail closed: anything that doesn't look like a QUrl is treated as disallowed.
        assert preview_url_is_allowed(object()) is False
