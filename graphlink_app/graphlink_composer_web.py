"""Local React composer host for the QWebEngine renderer.

This is the composer-specific island: a thin, legacy-compatible layer on top
of WebIslandHost. Everything generic (asset loading, WebEngine hardening,
QWebChannel wiring, height negotiation, shutdown-registry participation)
lives in graphlink_web_island_host.py. What remains here is exactly the
legacy Qt composer's widget-impersonation surface ChatWindow still depends
on - hidden dummy buttons, unemitted compatibility signals, and text-editor-
style methods - kept because ChatWindow was never rewired off it. All of it
is explicitly Phase 2 scope to delete (legacy Qt composer removal), not this
file's problem to fix.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QPushButton

from graphlink_composer import ComposerController
from graphlink_composer_bridge import (
    COMPOSER_MAX_HEIGHT,
    COMPOSER_MIN_HEIGHT,
    ComposerBridge,
)
from graphlink_web_island_host import WebIslandHost

COMPOSER_UNAVAILABLE_MESSAGE = (
    "The React composer is unavailable in this installation. "
    "Set GRAPHLINK_COMPOSER_RENDERER=legacy to use the classic composer."
)


class ComposerWebHost(WebIslandHost):
    """QWidget-compatible host for the React/QWebEngine composer.

    The compatibility methods let ChatWindow migrate without giving the web
    surface ownership of request logic or attachment paths.
    """

    # Legacy-compat signals: ChatWindow.__init__ connects to all seven of
    # these (graphlink_window.py), but nothing in this class ever emits them -
    # they exist only so those .connect() calls don't raise AttributeError.
    # Deleting them is Phase 2 scope (legacy Qt composer removal), not this
    # extraction's.
    sendRequested = Signal()
    textChanged = Signal(str)
    attachRequested = Signal()
    filesDropped = Signal(list)
    textDropped = Signal(str)
    attachmentRemoved = Signal(str)
    largePasteDetected = Signal(str)
    composerHeightChanged = Signal(int)

    def __init__(self, window, controller: ComposerController | None = None, parent=None):
        resolved_controller = controller or getattr(window, "composer_controller", None)
        bridge = ComposerBridge(window, resolved_controller, None)

        super().__init__(
            bridge=bridge,
            asset_dir_name="composer",
            bridge_object_name="composerBridge",
            min_height=COMPOSER_MIN_HEIGHT,
            max_height=COMPOSER_MAX_HEIGHT,
            unavailable_message=COMPOSER_UNAVAILABLE_MESSAGE,
            parent=parent,
        )
        self.window = window
        # Preserves the pre-existing (dormant, unreachable via any real call
        # site) behavior where this can diverge from self.bridge.controller if
        # both `controller` and `window.composer_controller` are None - the
        # bridge falls back to constructing its own ComposerController in that
        # case, but this attribute does not follow it. Not fixed here since no
        # call site exercises it; flagging rather than silently changing it.
        self.controller = resolved_controller
        self._placeholder = "Ask about this graph…"

        # These hidden controls preserve the existing ChatWindow styling and
        # request-state hooks while all visible interaction belongs to React.
        self.attach_file_btn = QPushButton(self)
        self.send_button = QPushButton(self)
        self.attach_file_btn.setVisible(False)
        self.send_button.setVisible(False)

        self.bridge.heightRequested.connect(self.apply_requested_height)
        self.heightChanged.connect(self.composerHeightChanged.emit)

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

    def set_context_items(self, items):
        self.controller.set_attachments(items or [])
        self.bridge.publish()

    def set_context_anchor(self, node):
        self.bridge.publish()

    def set_provider_status(self, text, tooltip=""):
        # Route is derived from SettingsManager in the bridge; this method is
        # retained for the legacy ChatWindow call site during migration.
        self._provider_status = str(text or "")
        self.bridge.publish()

    def set_request_state(self, active=False, cancel_pending=False, message=""):
        self._request_message = str(message or "")

    def set_editor_enabled(self, enabled):
        # React derives editor enabled state from the controller request state.
        self._editor_enabled = bool(enabled)
