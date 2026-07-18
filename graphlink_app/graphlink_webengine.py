"""Shared QtWebEngine hardening for every local preview/island surface.

Both the HTML Renderer node's preview and the composer's React island render
content inside a QWebEngineView, and both need the same guarantee: nothing they
show may reach the network or the local disk. This module owns that guarantee in
one place - the offline, local-only sandbox profile and interceptor - so it is
proven once and every WebEngine surface inherits it instead of re-implementing
its own escape hatch.
"""

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


def preview_url_is_allowed(url):
    """Whether a sub-resource/navigation request from a hardened view may proceed.

    Every surface that uses this module renders content that isn't fully trusted: the
    HTML Renderer node auto-renders AI-generated and session-loaded markup, and the
    composer island runs a bundled but still script-capable JS app. Either could
    otherwise exfiltrate content to a remote host or hit the user's localhost/intranet
    (SSRF/CSRF from their network position). We treat every hardened view as an offline
    sandbox: only local, self-contained schemes are permitted; any request that would
    leave the machine is blocked.
    """
    try:
        scheme = (url.scheme() or "").lower()
    except AttributeError:
        return False
    return scheme in _LOCAL_PREVIEW_SCHEMES


if WEBENGINE_AVAILABLE:

    class _LocalOnlyRequestInterceptor(QWebEngineUrlRequestInterceptor):
        """Blocks every request whose scheme isn't local (see preview_url_is_allowed).
        Applied to the shared profile so it covers <img>/<script>/fetch/XHR/form-POST/
        navigation uniformly, regardless of how the payload was crafted.
        """

        def interceptRequest(self, info):
            if not preview_url_is_allowed(info.requestUrl()):
                info.block(True)

    # One dedicated, process-wide profile carries the interceptor for every hardened view
    # (HTML preview and composer alike). Created lazily (never at import time - a
    # QWebEngine QObject built before QApplication exists can crash) and cached, so we do
    # NOT mutate the shared default profile and do NOT create a new profile per view.
    # Lifetime is the crux of QtWebEngine stability:
    # - the profile is parented to the QApplication, so C++ owns it and it outlives every
    #   hardened page (a profile destroyed before its pages crashes);
    # - the interceptor is parented to the profile, so C++ owns it too - the profile can
    #   never call back into a Python object that GC already reclaimed.
    _PREVIEW_PROFILE = None

    def _get_preview_profile():
        global _PREVIEW_PROFILE
        if _PREVIEW_PROFILE is None:
            app = QApplication.instance()
            # Off-the-record profile: no on-disk cache/cookies for hardened content.
            profile = QWebEngineProfile(app)
            interceptor = _LocalOnlyRequestInterceptor(profile)
            profile.setUrlRequestInterceptor(interceptor)
            _PREVIEW_PROFILE = profile
        return _PREVIEW_PROFILE

    def _harden_preview_web_view(web_view):
        """Lock a QWebEngineView down to an offline, self-contained sandbox.

        The view is given its own page backed by the shared local-only profile, so every
        request (``<img>``/``<script>``/fetch/XHR/form-POST/navigation) is filtered
        through the interceptor regardless of how the payload was crafted.
        """
        profile = _get_preview_profile()
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
