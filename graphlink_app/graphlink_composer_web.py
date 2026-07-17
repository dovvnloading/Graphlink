"""Local React composer host for the QWebEngine renderer."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QRect, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QRegion
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from graphlink_composer import ComposerController
from graphlink_paths import asset_path

try:
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineWidgets import QWebEngineView

    from graphlink_html_view import WEBENGINE_AVAILABLE, _harden_preview_web_view
except ImportError:
    QWebChannel = None
    QWebEngineView = None
    WEBENGINE_AVAILABLE = False
    _harden_preview_web_view = None

from graphlink_composer_bridge import (
    COMPOSER_MAX_HEIGHT,
    COMPOSER_MIN_HEIGHT,
    ComposerBridge,
)


def _inline_bundle(asset_root: Path) -> str:
    """Inline the Vite output so the page has no file:// or network dependency."""
    index_path = asset_root / "index.html"
    if not index_path.is_file():
        return (
            "<!doctype html><html><body><p>Composer assets are not installed.</p>"
            "</body></html>"
        )
    document = index_path.read_text(encoding="utf-8")

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
    document = document.replace("<head>", f"<head>{csp}", 1)
    channel_script = '<script src="qrc:///qtwebchannel/qwebchannel.js"></script>'
    document = document.replace("</head>", f"{channel_script}</head>", 1)
    return document


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


class ComposerWebHost(QFrame):
    """QWidget-compatible host for the React/QWebEngine composer.

    The compatibility methods let ChatWindow migrate without giving the web
    surface ownership of request logic or attachment paths.
    """

    sendRequested = Signal()
    textChanged = Signal(str)
    attachRequested = Signal()
    filesDropped = Signal(list)
    textDropped = Signal(str)
    attachmentRemoved = Signal(str)
    largePasteDetected = Signal(str)
    composerHeightChanged = Signal(int)

    def __init__(self, window, controller: ComposerController | None = None, parent=None):
        super().__init__(parent)
        self.window = window
        self.controller = controller or getattr(window, "composer_controller", None)
        self.setObjectName("composerWebHost")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(COMPOSER_MIN_HEIGHT)
        self.setStyleSheet("QFrame#composerWebHost { background: transparent; border: 0; }")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._corner_radius = 14
        self._placeholder = "Ask about this graph…"
        self._shutdown_started = False

        # These hidden controls preserve the existing ChatWindow styling and
        # request-state hooks while all visible interaction belongs to React.
        self.attach_file_btn = QPushButton(self)
        self.send_button = QPushButton(self)
        self.attach_file_btn.setVisible(False)
        self.send_button.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.web_view = None
        self.bridge = ComposerBridge(window, self.controller, self)
        self.bridge.heightRequested.connect(self._apply_requested_height)

        if WEBENGINE_AVAILABLE and QWebEngineView and QWebChannel:
            self.web_view = QWebEngineView(self)
            self.web_view.setStyleSheet("background: transparent; border: 0;")
            self.web_view.page().setBackgroundColor(QColor(0, 0, 0, 0))
            _harden_preview_web_view(self.web_view)
            channel = QWebChannel(self.web_view.page())
            channel.registerObject("composerBridge", self.bridge)
            self.web_view.page().setWebChannel(channel)
            self.web_view.loadFinished.connect(lambda _ok: self.bridge.ready())
            self.web_view.setHtml(_inline_bundle(asset_path("composer")), QUrl("about:blank"))
            layout.addWidget(self.web_view)
        else:
            fallback = QLabel(
                "The React composer is unavailable in this installation. "
                "Set GRAPHLINK_COMPOSER_RENDERER=legacy to use the classic composer.",
                self,
            )
            fallback.setWordWrap(True)
            fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(fallback)

        self._apply_native_mask()

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.prepare_for_shutdown)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_native_mask()

    def _apply_native_mask(self):
        self.setMask(_rounded_region(self.rect(), self._corner_radius))
        if self.web_view is not None:
            self.web_view.setMask(_rounded_region(self.web_view.rect(), self._corner_radius))

    def text(self) -> str:
        return str(self.controller.draft.text or "")

    def setText(self, text):
        self.bridge.updateDraft(str(text or ""))

    def clear(self):
        self.setText("")

    def insertPlainText(self, text):
        self.setText(self.text() + str(text or ""))

    def setPlaceholderText(self, text):
        self._placeholder = str(text or "")

    def setFocus(self, reason=Qt.FocusReason.OtherFocusReason):
        if self.web_view:
            self.web_view.setFocus(reason)
        else:
            super().setFocus(reason)

    def focusWidget(self):
        return self.web_view or super().focusWidget()

    def set_context_items(self, items):
        self.controller.set_attachments(items or [])
        self.bridge._publish()

    def set_context_anchor(self, node):
        self.bridge._publish()

    def set_provider_status(self, text, tooltip=""):
        # Route is derived from SettingsManager in the bridge; this method is
        # retained for the legacy ChatWindow call site during migration.
        self._provider_status = str(text or "")
        self.bridge._publish()

    def set_request_state(self, active=False, cancel_pending=False, message=""):
        self._request_message = str(message or "")

    def set_editor_enabled(self, enabled):
        # React derives editor enabled state from the controller request state.
        self._editor_enabled = bool(enabled)

    def on_theme_changed(self):
        self.bridge._publish()

    def prepare_for_shutdown(self):
        """Stop web content callbacks before Qt tears down the application."""
        if self._shutdown_started:
            return
        self._shutdown_started = True
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

    def _apply_requested_height(self, height: int):
        bounded = max(COMPOSER_MIN_HEIGHT, min(COMPOSER_MAX_HEIGHT, int(height)))
        if self.height() == bounded:
            return
        self.setFixedHeight(bounded)
        self.composerHeightChanged.emit(bounded)
