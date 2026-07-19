"""Generic QWidget host for a React/QWebEngine island, plus the shared
shutdown registry every island host participates in.

WebIslandHost owns exactly what every current and future island needs:
asset loading, WebEngine hardening, QWebChannel wiring to an IslandBridge,
the loadFinished -> bridge.publish() handshake, rounded-corner native
masking, negotiated height sizing, and shutdown-registry participation.

It deliberately does NOT own: any per-surface legacy-widget compatibility
shim (the composer's hidden dummy buttons and unemitted Qt Signals exist
only because ChatWindow still expects a QWidget-like text-input API from
its early Qt-composer days; that is ComposerWebHost's problem, explicitly
kept there until Phase 2 deletes the legacy Qt composer and rewires
ChatWindow off it), or drag-drop capture (still a separate, not-yet-built
Phase 1 item - see doc/FRONTEND_WEB_MIGRATION_MASTER_PLAN.md if present,
otherwise treat as future work).

Keyboard/focus arbitration IS owned here: every host publishes whether the
island's own DOM currently has a text-editable element focused
(reportTextFocus()/hasTextFocus()/textFocusChanged), and any_host_has_text_
focus() answers "does ANY registered island want keyboard input right now"
without naming a specific island. AcceleratorForwardingFilter, below, is the
consumer that gates global QShortcuts on that answer. This does NOT forward
individual keystrokes into an island's DOM - Chromium already owns native
key input the instant its content has focus; this protocol only answers the
one binary question needed to keep the REST of the app (canvas pan keys,
global shortcuts) from fighting over keys an island is actively using.
"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, QRect, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QColor, QRegion
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
)

import graphlink_config as config
from graphlink_frontend_bootstrap import resolve_dev_server_origin
from graphlink_paths import asset_path
from graphlink_styles import css_root_block

try:
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEngineScript
    from PySide6.QtWebEngineWidgets import QWebEngineView

    from graphlink_webengine import WEBENGINE_AVAILABLE, _harden_preview_web_view
except ImportError:
    QWebChannel = None
    QWebEngineScript = None
    QWebEngineView = None
    WEBENGINE_AVAILABLE = False
    _harden_preview_web_view = None


class MultiChunkBuildError(RuntimeError):
    """Raised when a Vite build produced more than one CSS or JS chunk.

    `_inline_bundle()`'s whole approach - regex-replace the single `<link>`/
    `<script src>` tag `index.html` references with the real file's content -
    assumes exactly one chunk of each kind exists. That assumption can break
    two ways, neither of which is safe to inline past silently:

    - Vite emits a SECOND `<link>`/`<script src>` tag (e.g. a shared-vendor
      chunk split out automatically once a second heavy dependency is added).
      Inlining each tag separately still "succeeds" mechanically, but any
      `import`/`export` relationship between the original files - resolved via
      real relative URLs when the browser loads separate <script> files -
      breaks once both are flattened into bodyless inline <script type=
      "module"> text with no src to resolve against.
    - Vite code-splits via a DYNAMIC `import()` inside the entry chunk, with
      no second <link>/<script src> tag in index.html at all - the browser
      fetches that chunk on demand at runtime. A regex over index.html's tags
      can never see this case; the extra .js file just sits on disk,
      unaccounted for, until a user's action triggers the dynamic import and
      it fails to fetch (no server - the page is `about:blank` with fully
      inlined content).

    Both failure modes are silent until someone hits them at runtime, in a
    shipped build, with no signal pointing back at "the build shape changed."
    Checking the real file count on disk - not just what index.html's tags
    reference - catches both.
    """


def _assert_single_chunk_build(asset_root: Path) -> None:
    assets_dir = asset_root / "assets"
    if not assets_dir.is_dir():
        return

    css_files = sorted(p.name for p in assets_dir.glob("*.css"))
    js_files = sorted(p.name for p in assets_dir.glob("*.js"))

    if len(css_files) > 1 or len(js_files) > 1:
        raise MultiChunkBuildError(
            f"{assets_dir} contains {len(css_files)} CSS file(s) {css_files} and "
            f"{len(js_files)} JS file(s) {js_files} - _inline_bundle() only knows "
            "how to inline exactly one of each. This means Vite's build for this "
            "island has code-split into multiple chunks (a new dynamic import(), "
            "a manualChunks() config, or a new dependency large enough for Vite's "
            "automatic chunking to kick in). Find what changed in the island's "
            "source or vite.config.ts and either remove the split (this island's "
            "single-chunk assumption is a real, load-bearing constraint, not "
            "incidental) or extend _inline_bundle() deliberately to handle "
            "multiple chunks correctly, rather than letting this fail silently "
            "at runtime the first time a dynamically-imported chunk can't be "
            "fetched from the fully-inlined, server-less page."
        )


def _inline_bundle(asset_root: Path) -> str:
    """Inline the Vite output so the page has no file:// or network dependency.

    Also embeds a `:root { --gl-*: ...; }` block for the current app theme
    (config.CURRENT_THEME) directly into <head>, ahead of the island's own
    built stylesheet - so any var(--gl-*) reference in island CSS resolves to
    a real value from first paint, before QWebChannel connects or the bridge
    ever calls publish(). Without this, an island CSS rule referencing
    var(--gl-*) would render unstyled (the property is simply undefined)
    until some later JS-side mechanism sets it - a gap that still exists
    separately (nothing today writes --gl-* onto document.documentElement at
    runtime for live theme switching; this only covers the value present at
    construction time). Same trust-CURRENT_THEME convention already used
    throughout the app (e.g. graphlink_composer_bridge.py's _theme()) - not
    defensive against an invalid theme name, since apply_theme() is the one
    place that guarantees CURRENT_THEME is valid.
    """
    index_path = asset_root / "index.html"
    if not index_path.is_file():
        return (
            "<!doctype html><html><body><p>Island assets are not installed.</p>"
            "</body></html>"
        )
    document = index_path.read_text(encoding="utf-8")

    _assert_single_chunk_build(asset_root)

    # Vite emits one stylesheet and one module. Replacing those tags separately
    # keeps the generated HTML easy to inspect and prevents accidental path use.
    css_pattern = re.compile(
        r'<link[^>]+href=["\'](?P<path>[^"\']+\.css)["\'][^>]*>',
        re.IGNORECASE,
    )

    def replace_css(match: re.Match[str]) -> str:
        candidate = (index_path.parent / match.group("path")).resolve()
        try:
            candidate.relative_to(asset_root.resolve())
        except ValueError:
            return match.group(0)
        if not candidate.is_file():
            return match.group(0)
        return f"<style>{candidate.read_text(encoding='utf-8')}</style>"

    document = css_pattern.sub(replace_css, document)

    script_pattern = re.compile(
        r'<script[^>]+src=["\'](?P<path>[^"\']+\.js)["\'][^>]*>\s*</script>',
        re.IGNORECASE,
    )

    def replace_script(match: re.Match[str]) -> str:
        candidate = (index_path.parent / match.group("path")).resolve()
        try:
            candidate.relative_to(asset_root.resolve())
        except ValueError:
            return match.group(0)
        if not candidate.is_file():
            return match.group(0)
        return f"<script type=\"module\">{candidate.read_text(encoding='utf-8')}</script>"

    document = script_pattern.sub(replace_script, document)
    csp = (
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        'script-src \'unsafe-inline\' qrc:; style-src \'unsafe-inline\'; img-src data:; '
        'connect-src \'none\';">'
    )
    theme_root_style = f"<style>{css_root_block(config.CURRENT_THEME)}</style>"
    document = document.replace("<head>", f"<head>{csp}{theme_root_style}", 1)
    channel_script = '<script src="qrc:///qtwebchannel/qwebchannel.js"></script>'
    document = document.replace("</head>", f"{channel_script}</head>", 1)
    return document


def _qwebchannel_injection_script():
    """Live-URL equivalent of _inline_bundle()'s injected
    <script src="qrc:///qtwebchannel/qwebchannel.js"> tag.

    Vite's own served index.html has no such tag and must not gain one -
    web_ui/ stays Qt-agnostic. The injection point must be DocumentCreation
    (before ANY page script runs, including deferred module scripts): the
    island's bridge.ts checks isQWebChannelAvailable() exactly once,
    synchronously, when main.tsx's module script executes, and permanently
    falls back to its mock bridge if window.QWebChannel is absent at that
    moment. Injecting later (e.g. runJavaScript after loadFinished) would
    "work" without error while the composer silently ran disconnected from
    Python forever.

    Returned script must be added to the PAGE's script collection
    (page().scripts()), never the shared profile's - _PREVIEW_PROFILE is
    shared with the HTML-renderer node preview, which renders untrusted
    markup and must never be handed a QWebChannel bootstrap it didn't ask
    for. Each WebIslandHost constructs its own QWebEnginePage, so
    page-scoped scripts can't leak across surfaces.
    """
    qfile = QFile(":/qtwebchannel/qwebchannel.js")
    if not qfile.open(QIODevice.OpenModeFlag.ReadOnly):
        raise RuntimeError(
            "qrc:///qtwebchannel/qwebchannel.js could not be read from Qt's "
            "resource system - QtWebChannel appears not to be loaded. The "
            "live dev-server page cannot be wired to the Python bridge "
            "without it, and proceeding would silently run the island on "
            "its mock bridge instead."
        )
    try:
        source = bytes(qfile.readAll()).decode("utf-8")
    finally:
        qfile.close()
    script = QWebEngineScript()
    script.setName("qwebchannel-bootstrap")
    script.setSourceCode(source)
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    script.setRunsOnSubFrames(False)
    return script


def _rounded_region(rect: QRect, radius: int) -> QRegion:
    """Return a true rounded-rectangle region for native child clipping."""
    width = max(0, rect.width())
    height = max(0, rect.height())
    if width == 0 or height == 0:
        return QRegion()

    radius = max(0, min(int(radius), width // 2, height // 2))
    if radius == 0:
        return QRegion(QRect(0, 0, width, height), QRegion.RegionType.Rectangle)

    diameter = radius * 2
    region = QRegion(
        QRect(radius, 0, max(1, width - diameter), height),
        QRegion.RegionType.Rectangle,
    )
    region = region.united(
        QRegion(
            QRect(0, radius, width, max(1, height - diameter)),
            QRegion.RegionType.Rectangle,
        )
    )
    for x, y in (
        (0, 0),
        (width - diameter, 0),
        (0, height - diameter),
        (width - diameter, height - diameter),
    ):
        region = region.united(
            QRegion(QRect(x, y, diameter, diameter), QRegion.RegionType.Ellipse)
        )
    return region


# --- Shutdown registry -------------------------------------------------------
#
# Every WebIslandHost registers itself here instead of each host independently
# hooking QApplication.aboutToQuit. As more islands exist (Phase 2+), each new
# host participates automatically - nothing else has to know their names, and
# ChatWindow's shutdown path can tear all of them down through one call instead
# of duck-typing an attribute lookup per surface.
#
# This registry is inherently Qt-specific (QApplication.aboutToQuit has no
# equivalent outside Qt) - unlike IslandBridge, which is deliberately Qt-free,
# this module's shutdown mechanism will need a PyWebView-appropriate
# replacement in Phase 9, not a reusable carry-over.

_hosts: list["WebIslandHost"] = []
_app_hooked = False


def register(host: "WebIslandHost") -> None:
    if host not in _hosts:
        _hosts.append(host)
    _ensure_app_hook()


def unregister(host: "WebIslandHost") -> None:
    try:
        _hosts.remove(host)
    except ValueError:
        pass


def any_host_has_text_focus() -> bool:
    """Whether ANY currently-registered island wants keyboard input right
    now - the query AcceleratorForwardingFilter and ChatView's keyPressEvent
    both consult, on every shortcut-eligible keystroke application-wide, for
    the lifetime of the process. Deliberately answers only the aggregate
    boolean, never "which island" - nothing today needs per-island routing.

    Queries _hosts live rather than maintaining a second, separately-updated
    set of "which hosts are focused": unregister() (called from
    prepare_for_shutdown()) already removes a torn-down host from
    consideration for free, so there is no separate cleanup path to keep in
    sync or let go stale.

    Guards each host call the same way shutdown_all() does: a registered
    host's C++ side can in principle be gone (deleted outside the normal
    prepare_for_shutdown()/unregister() path) while the Python reference
    lingers in _hosts. Given this runs on essentially every keystroke, a
    single stale reference must not turn into "every shortcut in the app
    throws forever" - treat an inaccessible host as not focused instead.
    """
    for host in _hosts:
        try:
            if host.hasTextFocus():
                return True
        except (AttributeError, RuntimeError, SystemError, TypeError):
            continue
    return False


def shutdown_all() -> None:
    """Call prepare_for_shutdown() on every still-registered host.

    Each host's own prepare_for_shutdown() already guards against being
    called more than once, so this is safe to invoke from both
    QApplication.aboutToQuit and an explicit ChatWindow.closeEvent call.
    """
    for host in list(_hosts):
        prepare = getattr(host, "prepare_for_shutdown", None)
        if callable(prepare):
            try:
                prepare()
            except (AttributeError, RuntimeError, SystemError, TypeError):
                pass


def _ensure_app_hook() -> None:
    global _app_hooked
    if _app_hooked:
        return
    app = QApplication.instance()
    if app is None:
        return
    app.aboutToQuit.connect(shutdown_all)
    _app_hooked = True


class WebIslandHost(QFrame):
    """Generic host widget for one React/QWebEngine island.

    Parametrized by:
    - bridge: an already-constructed IslandBridge+QObject instance. Reparented
      under this host (via setParent) so Qt owns its lifetime.
    - asset_dir_name: the assets/<name> directory this island's Vite build
      produced (resolved via graphlink_paths.asset_path).
    - bridge_object_name: the name the bridge is registered under on the
      QWebChannel (what the JS side's `channel.objects.<name>` resolves to).
    - corner_radius: native rounded-corner clipping, matches the composer's
      current visual treatment by default.
    - min_height / max_height: bounds for negotiated sizing. Pass both for a
      island whose height the web content can request changes to (via
      apply_requested_height); pass neither for a host that manages its own
      size entirely through normal Qt layout.
    - unavailable_message: shown instead of the web view when WebEngine isn't
      available in this installation.
    """

    heightChanged = Signal(int)
    textFocusChanged = Signal(bool)

    def __init__(
        self,
        *,
        bridge,
        asset_dir_name: str,
        bridge_object_name: str,
        corner_radius: int = 14,
        min_height: int | None = None,
        max_height: int | None = None,
        unavailable_message: str = (
            "This content is unavailable because QtWebEngine failed to initialize."
        ),
        parent=None,
    ):
        super().__init__(parent)
        self.bridge = bridge
        self.bridge.setParent(self)

        self._corner_radius = corner_radius
        self._min_height = min_height
        self._max_height = max_height
        self._shutdown_started = False
        self._has_text_focus = False
        # Resolved once at construction (unlike the request interceptor's
        # per-request re-resolution): a host is not a forever-cached
        # singleton, and its load mode must not change out from under an
        # already-loaded page.
        self._dev_origin = resolve_dev_server_origin()

        self.setObjectName(f"{bridge_object_name}Host")
        if min_height is not None:
            # Negotiated/fixed sizing: horizontally fills its layout slot, but
            # height is host-controlled (via setFixedHeight here and later via
            # apply_requested_height), not left to normal Qt layout sizing.
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.setFixedHeight(min_height)
        # else: no bounds provided, so this host keeps Qt's default size
        # policy and participates in normal layout sizing - see the class
        # docstring's "pass neither for a host that manages its own size
        # entirely through normal Qt layout" contract.
        self.setStyleSheet(
            f"QFrame#{self.objectName()} {{ background: transparent; border: 0; }}"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.web_view = None
        if WEBENGINE_AVAILABLE and QWebEngineView and QWebChannel:
            self.web_view = QWebEngineView(self)
            self.web_view.setStyleSheet("background: transparent; border: 0;")
            self.web_view.page().setBackgroundColor(QColor(0, 0, 0, 0))
            # Live-dev hosts get the dedicated dev-server profile; every other
            # surface (offline composer, HTML-renderer preview) stays on the
            # unconditionally-offline profile. self._dev_origin is None unless
            # both opt-in env vars are set and this isn't a frozen build, so a
            # shipped build always takes the offline profile here.
            _harden_preview_web_view(
                self.web_view, allow_dev_server=self._dev_origin is not None
            )
            channel = QWebChannel(self.web_view.page())
            channel.registerObject(bridge_object_name, self.bridge)
            # "islandHost" is a reserved, well-known QWebChannel object name
            # every host registers alongside its own named bridge - the JS
            # side's textFocus reporter (bridge-core, generic) calls
            # objects.islandHost.reportTextFocus(bool) without needing to know
            # which specific bridge object this island also exposes.
            channel.registerObject("islandHost", self)
            self.web_view.page().setWebChannel(channel)
            self.web_view.loadFinished.connect(self._on_load_finished)
            if self._dev_origin is not None:
                # Live dev-server mode (developer opt-in, double-gated - see
                # resolve_dev_server_origin). The request interceptor
                # independently allowlists exactly this origin; everything
                # else stays blocked as in the offline path.
                self.web_view.page().scripts().insert(_qwebchannel_injection_script())
                self.web_view.setUrl(QUrl(self._dev_origin))
            else:
                self.web_view.setHtml(
                    _inline_bundle(asset_path(asset_dir_name)), QUrl("about:blank")
                )
            layout.addWidget(self.web_view)
        else:
            fallback = QLabel(unavailable_message, self)
            fallback.setWordWrap(True)
            fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(fallback)

        self._apply_native_mask()
        register(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_native_mask()

    def _apply_native_mask(self):
        self.setMask(_rounded_region(self.rect(), self._corner_radius))
        if self.web_view is not None:
            self.web_view.setMask(_rounded_region(self.web_view.rect(), self._corner_radius))

    def setFocus(self, reason=Qt.FocusReason.OtherFocusReason):
        if self.web_view:
            self.web_view.setFocus(reason)
        else:
            super().setFocus(reason)

    def focusWidget(self):
        return self.web_view or super().focusWidget()

    @Slot(bool)
    def reportTextFocus(self, has_focus: bool) -> None:
        """Called from JS (via the "islandHost" QWebChannel object) whenever
        the island's DOM focus moves into or out of a text-editable element.
        Only emits on a real transition, so a caller doing e.g. tab-between-
        two-textareas (which reports True, True) doesn't spam listeners."""
        has_focus = bool(has_focus)
        if has_focus == self._has_text_focus:
            return
        self._has_text_focus = has_focus
        self.textFocusChanged.emit(has_focus)

    def hasTextFocus(self) -> bool:
        return self._has_text_focus

    def on_theme_changed(self):
        self.bridge.publish()

    def _on_load_finished(self, ok: bool) -> None:
        """Publish on load, or - in live dev-server mode only - replace
        Chromium's generic error page with an actionable one when the dev
        server isn't answering.

        Deliberately does NOT exit the process the way a
        FrontendBootstrapError does: that failure is pre-window and
        unrecoverable, while this one is mid-session and fixed by starting
        the server - exiting would make the live path strictly worse than
        the separate-browser-tab workflow it replaces. No pre-flight TCP
        probe either; loadFinished is Qt's own single source of truth for
        whether the load worked. The offline path's behavior is unchanged:
        publish regardless of ok, exactly as before.
        """
        # prepare_for_shutdown() calls web_view.stop(), which aborts an
        # in-flight load and fires loadFinished(False). Without this guard a
        # teardown mid-load would kick off a brand-new setHtml() during
        # shutdown - starting work exactly when the host is being torn down.
        if self._shutdown_started:
            return
        if not ok and self._dev_origin is not None:
            self.web_view.setHtml(
                "<!doctype html><html><body style=\"font-family:sans-serif;"
                "padding:2rem\">"
                f"<h2>Could not reach {self._dev_origin}</h2>"
                "<p>GRAPHLINK_FRONTEND_DEV_URL is set but nothing answered "
                "there. Run <code>npm run dev</code> in web_ui/, then reopen "
                "this window - or unset GRAPHLINK_FRONTEND_DEV_URL to load "
                "the offline bundle instead.</p></body></html>",
                QUrl("about:blank"),
            )
            return
        self.bridge.publish()

    def apply_requested_height(self, height: int) -> None:
        """Negotiate a new height for a "negotiated"-sizing island.

        Bounds the request to [min_height, max_height] (both must have been
        provided at construction) and does nothing if the bounded value
        matches the current height, matching how frequent web-side resize
        requests are expected to behave.
        """
        if self._min_height is None or self._max_height is None:
            raise NotImplementedError(
                "apply_requested_height() requires min_height and max_height "
                "to have been provided at construction"
            )
        bounded = max(self._min_height, min(self._max_height, int(height)))
        if self.height() == bounded:
            return
        self.setFixedHeight(bounded)
        self.heightChanged.emit(bounded)

    def prepare_for_shutdown(self):
        """Stop web content callbacks before Qt tears down the application."""
        if self._shutdown_started:
            return
        self._shutdown_started = True
        unregister(self)
        try:
            self.bridge.dispose()
        except (AttributeError, RuntimeError, SystemError, TypeError):
            pass
        web_view = self.web_view
        if web_view is None:
            return
        try:
            web_view.stop()
            web_view.setUpdatesEnabled(False)
            web_view.hide()
        except (AttributeError, RuntimeError, SystemError, TypeError):
            return

    def closeEvent(self, event):
        self.prepare_for_shutdown()
        super().closeEvent(event)


class AcceleratorForwardingFilter(QObject):
    """Gates global QShortcuts off while any island wants keyboard input.

    All of the app's global QShortcuts (graphlink_window.py) use Qt's default
    WindowShortcut context - active whenever the window has focus, regardless
    of which child widget currently holds it. That's correct for native
    widgets (a focused QLineEdit gets first crack at a key via Qt's own
    ShortcutOverride protocol before the shortcut fires), but a QWebEngineView
    hosting an island's own text input does not participate in that protocol
    the same way - nothing today stops e.g. Ctrl+K from firing and yanking
    focus away while a user is mid-sentence in a composer textarea.

    Installed once, application-wide (not per-QWebEngineView), because the
    actual focus-holding descendant inside a QWebEngineView's internal
    Chromium content isn't guaranteed to be the QWebEngineView object itself.
    Intercepts QEvent.Type.ShortcutOverride - which Qt sends to the focused
    widget's ancestor chain BEFORE dispatching to any matching QShortcut - and
    accepts it (claiming the key so the shortcut never fires) when the key
    combination is in GATED_SHORTCUTS and any_host_has_text_focus() is true.

    Not uniform: Ctrl+S (save_chat) is deliberately exempt. Save is
    non-destructive, never collides with anything an island's own text input
    would want, and is the one combo a user reflexively expects to keep
    working mid-sentence. Every other gated combo (new chat, library, command
    palette, search, frame/container creation, canvas arrow-nav) is
    workspace-level, and the island is the more plausible intended owner of
    that keystroke while it holds focus - so the default for any future
    shortcut added to GATED_SHORTCUTS is "gated," matching this project's
    general fail-safe bias; only Ctrl+S has been evaluated and exempted.
    """

    GATED_SHORTCUTS = {
        (Qt.Key.Key_T, Qt.KeyboardModifier.ControlModifier),
        (Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier),
        (Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier),
        (Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier),
        (Qt.Key.Key_G, Qt.KeyboardModifier.ControlModifier),
        (Qt.Key.Key_G, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier),
        (Qt.Key.Key_Up, Qt.KeyboardModifier.ControlModifier),
        (Qt.Key.Key_Down, Qt.KeyboardModifier.ControlModifier),
        (Qt.Key.Key_Left, Qt.KeyboardModifier.ControlModifier),
        (Qt.Key.Key_Right, Qt.KeyboardModifier.ControlModifier),
    }

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.ShortcutOverride:
            return False
        if not any_host_has_text_focus():
            return False
        if (event.key(), event.modifiers()) not in self.GATED_SHORTCUTS:
            return False
        event.accept()
        return True
