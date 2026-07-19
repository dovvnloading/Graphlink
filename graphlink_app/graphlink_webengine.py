"""Shared QtWebEngine hardening for every local preview/island surface.

Both the HTML Renderer node's preview and the composer's React island render
content inside a QWebEngineView, and both need the same guarantee: nothing they
show may reach the network or the local disk. This module owns that guarantee in
one place - the offline, local-only sandbox profile and interceptor - so it is
proven once and every WebEngine surface inherits it instead of re-implementing
its own escape hatch.

One narrow, developer-only exception exists: when BOTH GRAPHLINK_FRONTEND_DEV
and GRAPHLINK_FRONTEND_DEV_URL are set (see
graphlink_frontend_bootstrap.resolve_dev_server_origin), requests to that one
exact loopback origin (http and its ws HMR upgrade, nothing else) are also
allowed, so WebIslandHost can load the live Vite dev server in-window.

That exception lives on a SEPARATE profile (_get_dev_server_profile), used
only by a WebIslandHost that is actually in live mode. The shared offline
profile (_get_offline_profile) - which backs the composer's normal offline
mode AND the HTML-renderer node preview, the latter rendering untrusted
AI-generated markup - never consults the dev origin at all: its interceptor is
constructed with allow_dev_server=False and is unconditionally offline. This is
structural, not a per-request heuristic: an untrusted surface cannot reach the
dev server because its profile's interceptor never even asks whether a dev
origin exists. (A single shared profile can't express this - a
QWebEngineUrlRequestInterceptor can only block, never un-block, so a per-page
interceptor cannot re-add a permission the profile interceptor denied.)

The exception is additionally gated on sys.frozen three ways over:
resolve_dev_server_origin() refuses to produce an origin in a frozen build; a
live host therefore never opts into the dev profile when frozen; and
preview_url_is_allowed() below independently ignores any dev_origin it is
handed while frozen. No caller mistake can reopen the sandbox in a shipped
build.
"""

import sys
from urllib.parse import urlsplit

from graphlink_frontend_bootstrap import resolve_dev_server_origin

try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import (
        QWebEnginePage,
        QWebEngineProfile,
        QWebEngineSettings,
        QWebEngineUrlRequestInterceptor,
    )
    WEBENGINE_AVAILABLE = True
except ImportError:
    # Handle the case where WebEngine might not be installed but other parts of the app are
    WEBENGINE_AVAILABLE = False


# Schemes a self-contained local surface legitimately needs: the setHtml document itself
# (about:), inline data: URIs, JS-created blob: URLs, and Qt's compiled-in qrc resources.
# Everything else - http(s), file, ftp, ws(s) - would leave the machine or reach
# localhost/intranet/disk.
_LOCAL_PREVIEW_SCHEMES = frozenset({"about", "data", "blob", "qrc"})

# The only schemes the developer-opt-in live path may add on top: plain http to
# the dev server, plus the WebSocket upgrade Vite's HMR client opens against
# that same origin (without which nothing tells the page a file changed and
# "hot reload" silently degrades to "stale until manually reloaded").
_DEV_SERVER_SCHEMES = frozenset({"http", "ws"})


def preview_url_is_allowed(url, dev_origin=None):
    """Whether a sub-resource/navigation request from a hardened view may proceed.

    Every surface that uses this module renders content that isn't fully trusted: the
    HTML Renderer node auto-renders AI-generated and session-loaded markup, and the
    composer island runs a bundled but still script-capable JS app. Either could
    otherwise exfiltrate content to a remote host or hit the user's localhost/intranet
    (SSRF/CSRF from their network position). We treat every hardened view as an offline
    sandbox: only local, self-contained schemes are permitted; any request that would
    leave the machine is blocked.

    dev_origin, when not None, is the ONE exact extra origin ("http://host:port",
    from resolve_dev_server_origin()) whose http/ws requests are additionally
    allowed - an exact scheme+host+port match, never a host- or port-wildcard, so
    a compromised process on another loopback port (or Vite silently drifting to
    5174 when 5173 is taken - prevented separately by strictPort in
    vite.config.ts) gains nothing. The default None is byte-for-byte the
    pre-existing offline behavior. Ignored entirely in a frozen build regardless
    of what the caller passes - this module does not trust its callers to have
    gated that.
    """
    try:
        scheme = (url.scheme() or "").lower()
    except AttributeError:
        return False
    if scheme in _LOCAL_PREVIEW_SCHEMES:
        return True
    if not dev_origin or getattr(sys, "frozen", False):
        return False
    if scheme not in _DEV_SERVER_SCHEMES:
        return False
    try:
        host = (url.host() or "").lower()
        port = url.port(-1)
    except AttributeError:
        return False
    # dev_origin's shape is not trusted (the docstring says so): a malformed
    # value must fail CLOSED, never raise. urlsplit(...).port raises
    # ValueError on a bad port, and this runs on the interceptor's IO thread
    # where an unhandled exception is swallowed-and-printed by PySide and the
    # request would proceed unblocked - the exact fail-open shape this module
    # exists to prevent.
    try:
        dev = urlsplit(dev_origin)
        dev_host = (dev.hostname or "").lower()
        dev_port = dev.port
    except ValueError:
        return False
    return host == dev_host and port == dev_port


def _request_should_be_blocked(url, *, allow_dev_server: bool = False) -> bool:
    """The interceptor's actual per-request decision, factored out of the Qt
    class so the full env-var-to-verdict chain is testable without a live
    WebEngine context (QWebEngineUrlRequestInfo can't be constructed by hand).

    allow_dev_server distinguishes the two profiles: the offline profile's
    interceptor passes False and can NEVER reach the dev server (it doesn't
    even resolve the origin); only the dedicated dev-server profile's
    interceptor passes True. When True the dev origin is re-resolved on EVERY
    call, deliberately: the profile and its interceptor are a lazily-created,
    forever-cached process-wide singleton, so anything baked in at
    construction time would never re-evaluate for the process's lifetime.
    """
    dev_origin = resolve_dev_server_origin() if allow_dev_server else None
    return not preview_url_is_allowed(url, dev_origin)


if WEBENGINE_AVAILABLE:

    class _LocalOnlyRequestInterceptor(QWebEngineUrlRequestInterceptor):
        """Blocks every request whose scheme isn't local (see preview_url_is_allowed).

        allow_dev_server is fixed per interceptor instance, not per request: the
        offline profile's interceptor is constructed with False and can never
        reach the dev server; the dedicated dev-server profile's is constructed
        with True. Applied at profile level so it covers <img>/<script>/fetch/
        XHR/form-POST/navigation uniformly, regardless of how the payload was
        crafted.
        """

        def __init__(self, parent=None, *, allow_dev_server: bool = False):
            super().__init__(parent)
            self._allow_dev_server = allow_dev_server

        def interceptRequest(self, info):
            if _request_should_be_blocked(
                info.requestUrl(), allow_dev_server=self._allow_dev_server
            ):
                info.block(True)

    # Two dedicated, process-wide profiles, each carrying its own interceptor.
    # Created lazily (never at import time - a QWebEngine QObject built before
    # QApplication exists can crash) and cached, so we do NOT mutate the shared
    # default profile and do NOT create a new profile per view.
    #
    # _OFFLINE_PROFILE is the unconditional offline sandbox: the composer's
    # normal mode and the untrusted HTML-renderer preview both use it, and its
    # interceptor (allow_dev_server=False) can never reach the network.
    # _DEV_SERVER_PROFILE exists only so a live-mode WebIslandHost can reach the
    # one dev origin; nothing untrusted is ever put on it. Splitting the two is
    # what keeps the dev-server relaxation off the untrusted preview surface
    # structurally (see module docstring).
    #
    # Lifetime is the crux of QtWebEngine stability:
    # - each profile is parented to the QApplication, so C++ owns it and it
    #   outlives every hardened page (a profile destroyed before its pages crashes);
    # - each interceptor MUST be kept alive by a strong Python reference (the
    #   module globals below), NOT just Qt parenting. An earlier version relied
    #   on parenting alone ("C++ owns it") and that was empirically wrong in the
    #   way that matters most: C++ ownership keeps the C++ object installed on
    #   the profile, but once the Python wrapper is garbage-collected its
    #   interceptRequest override is gone, and Qt silently dispatches to the C++
    #   base no-op instead - every request PROCEEDS, fail-open, with zero errors
    #   anywhere. Reproduced directly on PySide6 6.11.1: an interceptor held only
    #   by parenting logged 0 interceptions; the identical interceptor held by a
    #   module global intercepted every request. test_html_preview_sandbox.py's
    #   TestInterceptorIsAliveAndBlocking guards the references' existence.
    _OFFLINE_PROFILE = None
    _OFFLINE_INTERCEPTOR = None
    _DEV_SERVER_PROFILE = None
    _DEV_SERVER_INTERCEPTOR = None

    def _make_hardened_profile(*, allow_dev_server: bool):
        app = QApplication.instance()
        # Off-the-record profile: no on-disk cache/cookies for hardened content.
        profile = QWebEngineProfile(app)
        interceptor = _LocalOnlyRequestInterceptor(profile, allow_dev_server=allow_dev_server)
        profile.setUrlRequestInterceptor(interceptor)
        return profile, interceptor

    def _get_offline_profile():
        global _OFFLINE_PROFILE, _OFFLINE_INTERCEPTOR
        if _OFFLINE_PROFILE is None:
            _OFFLINE_PROFILE, _OFFLINE_INTERCEPTOR = _make_hardened_profile(allow_dev_server=False)
        return _OFFLINE_PROFILE

    def _get_dev_server_profile():
        global _DEV_SERVER_PROFILE, _DEV_SERVER_INTERCEPTOR
        if _DEV_SERVER_PROFILE is None:
            _DEV_SERVER_PROFILE, _DEV_SERVER_INTERCEPTOR = _make_hardened_profile(allow_dev_server=True)
        return _DEV_SERVER_PROFILE

    def _harden_preview_web_view(web_view, *, allow_dev_server: bool = False):
        """Lock a QWebEngineView down to a self-contained sandbox.

        The view is given its own page backed by a shared local-only profile, so
        every request (``<img>``/``<script>``/fetch/XHR/form-POST/navigation) is
        filtered through the interceptor regardless of how the payload was
        crafted. allow_dev_server picks the dev-server profile - passed True ONLY
        by a WebIslandHost that resolved a live dev origin (which a frozen build
        never does); every other caller, including the untrusted HTML-renderer
        preview, takes the default and stays unconditionally offline.
        """
        profile = _get_dev_server_profile() if allow_dev_server else _get_offline_profile()
        page = QWebEnginePage(profile, web_view)  # child of the view: destroyed with it
        web_view.setPage(page)
        settings = web_view.settings()
        # Defense in depth alongside the interceptor: no disk reads, no remote pulls from
        # local content, no clipboard access, no popups.
        for attr, value in (
            ("LocalContentCanAccessFileUrls", False),
            ("LocalContentCanAccessRemoteUrls", False),
            ("JavascriptCanAccessClipboard", False),
            ("JavascriptCanPaste", False),
            ("JavascriptCanOpenWindows", False),
        ):
            enum_val = getattr(QWebEngineSettings.WebAttribute, attr, None)
            if enum_val is not None:
                settings.setAttribute(enum_val, value)
