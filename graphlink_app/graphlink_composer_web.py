"""Local React composer host for the QWebEngine renderer.

This is the composer-specific island: a thin layer on top of WebIslandHost.
Everything generic (asset loading, WebEngine hardening, QWebChannel wiring,
height negotiation, shutdown-registry participation) lives in
graphlink_web_island_host.py. What remains here are the text-editor-style
compatibility methods ChatWindow calls generically (via self.composer /
self.message_input) - text(), setText(), set_context_items(), etc. These are
NOT legacy-widget impersonation; they are this island's own real public API,
used identically whether or not a legacy composer ever existed, so they stay.

The legacy Qt composer's widget-impersonation surface this class used to also
carry - 7 unemitted compatibility Signals and 2 hidden dummy QPushButtons,
kept only so ChatWindow.__init__'s .connect()/.attach_file_btn/.send_button
references (written for the old QWidget composer) didn't raise - was deleted
in the legacy composer removal (Phase 2 item 4/4), alongside every call site
that touched it. composerHeightChanged is the one exception: it was never
part of that impersonation seam (nothing in the old ComposerWidget ever had
an equivalent), it's a real, live signal ChatWindow._sync_footer_height
depends on, so it's kept as-is.
"""

from __future__ import annotations

from PySide6.QtCore import Signal

from graphlink_composer import ComposerController
from graphlink_composer_bridge import (
    COMPOSER_MAX_HEIGHT,
    COMPOSER_MIN_HEIGHT,
    ComposerBridge,
)
from graphlink_web_island_host import WebIslandHost

COMPOSER_UNAVAILABLE_MESSAGE = (
    "The composer is unavailable because QtWebEngine failed to initialize."
)


class ComposerWebHost(WebIslandHost):
    """Host for the React/QWebEngine composer, exposing the text-editor-style
    compatibility API ChatWindow calls generically (self.composer.text(),
    .setText(), .set_context_items(), etc.)."""

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
