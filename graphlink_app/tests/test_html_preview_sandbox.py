"""Tests for the shared QtWebEngine network-egress sandbox (graphlink_webengine).

Regression coverage for the HTML-Renderer arbitrary-egress issue: the node auto-renders
AI-generated and session-loaded markup in a QWebEngineView with JavaScript enabled and
no restrictions, so an injected <script>/onerror payload could exfiltrate page content
to a remote host or hit the user's localhost/intranet (SSRF/CSRF). A local-only request
interceptor now blocks every request whose scheme would leave the machine. The composer
island shares this same sandbox.

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

import graphlink_frontend_bootstrap as gfb
import graphlink_webengine
from graphlink_webengine import _request_should_be_blocked, preview_url_is_allowed


class TestLocalSchemesAreAllowed:
    @pytest.mark.parametrize("url", [
        "about:blank",
        "data:text/html;base64,PHA+aGk8L3A+",
        "data:image/png;base64,iVBORw0KGgo=",
        "blob:about:blank/1234-5678",
        "qrc:///qtwebchannel/qwebchannel.js",
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


_DEV_ORIGIN = "http://127.0.0.1:5173"


class TestDevServerOriginRelaxation:
    """The one developer-opt-in exception to the offline sandbox: an exact
    dev-server origin. Every existing test above calls preview_url_is_allowed
    with NO second argument - their continued passing is itself the regression
    proof that the default (dev_origin=None) is byte-for-byte the pre-existing
    offline behavior."""

    @pytest.mark.parametrize("url", [
        "http://127.0.0.1:5173/",
        "http://127.0.0.1:5173/src/islands/composer/main.tsx",
        "ws://127.0.0.1:5173/",                   # Vite's HMR websocket
        "ws://127.0.0.1:5173/@vite/client",
    ])
    def test_exact_origin_http_and_ws_are_allowed(self, url):
        assert preview_url_is_allowed(QUrl(url), _DEV_ORIGIN) is True

    @pytest.mark.parametrize("url", [
        "http://127.0.0.1:5174/",                 # port drift (Vite auto-increment)
        "http://127.0.0.1:8080/admin/shutdown",   # another loopback service
        "http://localhost:5173/",                  # host string mismatch vs 127.0.0.1
        "http://192.168.1.50:5173/",               # non-loopback, matching port
        "http://attacker.example:5173/",           # remote, matching port
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata - the key regression proof
        "https://127.0.0.1:5173/",                 # right authority, wrong scheme
        "wss://127.0.0.1:5173/",                   # right authority, wrong scheme
        "file:///C:/Windows/win.ini",
    ])
    def test_everything_but_the_exact_origin_stays_blocked(self, url):
        assert preview_url_is_allowed(QUrl(url), _DEV_ORIGIN) is False

    def test_no_dev_origin_still_blocks_the_dev_server_url_itself(self):
        assert preview_url_is_allowed(QUrl("http://127.0.0.1:5173/")) is False

    def test_frozen_build_ignores_a_dev_origin_regardless_of_caller(self, monkeypatch):
        # graphlink_webengine's own side of the double sys.frozen gate: even a
        # validly-formed dev_origin passed straight in is discarded.
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        assert preview_url_is_allowed(QUrl("http://127.0.0.1:5173/"), _DEV_ORIGIN) is False


class TestOfflineProfileIsUnconditionallyOffline:
    """The security invariant the two-profile split exists to guarantee: the
    OFFLINE profile's decision (allow_dev_server=False) can never reach the dev
    server, even with both opt-in env vars set. This is the surface the
    untrusted HTML-renderer preview shares, so it must stay offline no matter
    what a developer set for the composer's benefit."""

    @pytest.fixture(autouse=True)
    def _clean_dev_env(self, monkeypatch):
        monkeypatch.delenv(gfb.DEV_MODE_ENV_VAR, raising=False)
        monkeypatch.delenv(gfb.DEV_SERVER_URL_ENV_VAR, raising=False)
        gfb._warned_dev_url_issues.clear()

    def test_offline_decision_blocks_the_dev_origin_even_with_both_vars_set(self, monkeypatch):
        monkeypatch.setenv(gfb.DEV_MODE_ENV_VAR, "1")
        monkeypatch.setenv(gfb.DEV_SERVER_URL_ENV_VAR, _DEV_ORIGIN)
        # allow_dev_server=False is the offline profile's fixed setting.
        assert _request_should_be_blocked(QUrl("http://127.0.0.1:5173/")) is True
        assert _request_should_be_blocked(QUrl("ws://127.0.0.1:5173/")) is True

    def test_offline_decision_never_resolves_the_origin(self, monkeypatch):
        # Even if resolve_dev_server_origin somehow returned an origin, the
        # offline path must not consult it - prove it's never called.
        called = {"n": 0}
        monkeypatch.setattr(
            graphlink_webengine, "resolve_dev_server_origin",
            lambda: (called.__setitem__("n", called["n"] + 1) or _DEV_ORIGIN),
        )
        assert _request_should_be_blocked(QUrl("http://127.0.0.1:5173/")) is True
        assert called["n"] == 0

    def test_local_schemes_still_allowed_offline(self):
        assert _request_should_be_blocked(QUrl("qrc:///qtwebchannel/qwebchannel.js")) is False


class TestDevServerProfileDecisionWiring:
    """The dedicated dev-server profile's decision (allow_dev_server=True) -
    the ONLY path that may reach the dev origin, and only when both env vars
    are set. Proves the env-var-to-verdict chain re-resolves per request."""

    @pytest.fixture(autouse=True)
    def _clean_dev_env(self, monkeypatch):
        monkeypatch.delenv(gfb.DEV_MODE_ENV_VAR, raising=False)
        monkeypatch.delenv(gfb.DEV_SERVER_URL_ENV_VAR, raising=False)
        gfb._warned_dev_url_issues.clear()

    def test_dev_server_request_blocked_without_the_env_vars(self):
        assert _request_should_be_blocked(QUrl("http://127.0.0.1:5173/"), allow_dev_server=True) is True

    def test_dev_server_request_allowed_once_both_vars_are_set(self, monkeypatch):
        monkeypatch.setenv(gfb.DEV_MODE_ENV_VAR, "1")
        monkeypatch.setenv(gfb.DEV_SERVER_URL_ENV_VAR, _DEV_ORIGIN)
        assert _request_should_be_blocked(QUrl("http://127.0.0.1:5173/"), allow_dev_server=True) is False
        assert _request_should_be_blocked(QUrl("ws://127.0.0.1:5173/"), allow_dev_server=True) is False

    def test_unrelated_egress_stays_blocked_even_with_both_vars_set(self, monkeypatch):
        monkeypatch.setenv(gfb.DEV_MODE_ENV_VAR, "1")
        monkeypatch.setenv(gfb.DEV_SERVER_URL_ENV_VAR, _DEV_ORIGIN)
        assert _request_should_be_blocked(QUrl("http://attacker.example/x"), allow_dev_server=True) is True
        assert _request_should_be_blocked(QUrl("http://127.0.0.1:8080/"), allow_dev_server=True) is True

    def test_local_schemes_stay_allowed(self, monkeypatch):
        assert _request_should_be_blocked(QUrl("qrc:///qtwebchannel/qwebchannel.js"), allow_dev_server=True) is False


class TestInterceptorIsAliveAndBlocking:
    """Regression guard for a real, empirically-confirmed fail-open bug: the
    interceptor was originally kept alive ONLY by Qt parenting, and PySide's
    wrapper for it could be garbage-collected - the C++ object stayed
    installed on the profile, but its interceptRequest silently dispatched to
    the C++ no-op base instead of the Python override, so every request
    PROCEEDED. Nothing errored anywhere; the offline sandbox held for the
    composer only because _inline_bundle()'s CSP meta tag did the real work,
    and the HTML-renderer node preview (no CSP) had no working egress block
    at all. Fixed by the module-level strong references; these tests pin BOTH
    profiles' interceptors and prove the Python override is the one reachable."""

    def test_both_profiles_pin_a_strong_interceptor_reference(self):
        import gc

        offline = graphlink_webengine._get_offline_profile()
        dev = graphlink_webengine._get_dev_server_profile()
        gc.collect()

        assert offline is graphlink_webengine._OFFLINE_PROFILE
        assert dev is graphlink_webengine._DEV_SERVER_PROFILE
        assert offline is not dev  # genuinely separate profiles
        for interceptor in (
            graphlink_webengine._OFFLINE_INTERCEPTOR,
            graphlink_webengine._DEV_SERVER_INTERCEPTOR,
        ):
            assert isinstance(interceptor, graphlink_webengine._LocalOnlyRequestInterceptor)
        assert graphlink_webengine._OFFLINE_INTERCEPTOR._allow_dev_server is False
        assert graphlink_webengine._DEV_SERVER_INTERCEPTOR._allow_dev_server is True

    def test_the_pinned_offline_interceptor_runs_the_real_python_override(self):
        # Drive interceptRequest directly with a duck-typed info object (a
        # real QWebEngineUrlRequestInfo can't be constructed by hand). If the
        # Python override were lost, this call would still "work" via the
        # base class and never touch block() - asserting the block call is
        # what proves the override is the live code path.
        graphlink_webengine._get_offline_profile()
        blocked = []

        class _FakeInfo:
            def requestUrl(self):
                return QUrl("http://attacker.example/exfil")

            def block(self, value):
                blocked.append(value)

        graphlink_webengine._OFFLINE_INTERCEPTOR.interceptRequest(_FakeInfo())
        assert blocked == [True]


class TestHtmlViewNodeWebengineAvailabilityIsAtomic:
    """graphlink_html_view.WEBENGINE_AVAILABLE gates `QWebEngineView(...)` construction
    in HtmlViewNode. QWebEngineView lives in a separate compiled module
    (PySide6.QtWebEngineWidgets) from the classes graphlink_webengine's own
    availability check imports (QtWebEngineCore, QtWidgets), so the flag must combine
    both: True only when the node's own QWebEngineView/QWebEngineScript imports AND
    the shared hardening module's imports both succeeded. A gap here would let
    WEBENGINE_AVAILABLE be True while QWebEngineView is unbound, crashing HtmlViewNode
    with NameError instead of falling back to its "module not found" placeholder UI.

    This deliberately does not simulate the partial-import-failure case by reloading
    graphlink_html_view: 11 other modules cache `HtmlViewNode` at their own import
    time, and reloading would fork class identity between this test and them for the
    rest of the shared-process test session.
    """

    def test_true_and_shared_when_both_import_cleanly(self):
        import graphlink_html_view
        import graphlink_web_island_host
        import graphlink_webengine

        assert graphlink_html_view.WEBENGINE_AVAILABLE is True
        assert graphlink_html_view.WEBENGINE_AVAILABLE is graphlink_webengine.WEBENGINE_AVAILABLE
        assert graphlink_html_view._harden_preview_web_view is (
            graphlink_webengine._harden_preview_web_view
        )
        # graphlink_composer_web.ComposerWebHost no longer imports the hardening
        # function directly - it inherits WebIslandHost, which does. This checks
        # the actual current consumer, not a module that only re-exports it.
        assert graphlink_web_island_host._harden_preview_web_view is (
            graphlink_webengine._harden_preview_web_view
        )
