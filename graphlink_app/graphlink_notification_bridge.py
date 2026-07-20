"""Desktop-side state bridge for the notification island.

Event-push toast shape: Python drives visibility/content/type, plus one real
intent back from JS (copyDetails - clipboard access is OS-level, so this
stays Python-authoritative rather than a JS navigator.clipboard call, per
the plan's own stated design for this item).

The auto-hide timer stays entirely on the Python side (this bridge owns a
QTimer, exactly like the old NotificationBanner widget did) rather than
duplicating duration/countdown logic in JS - the web side is purely
reactive to whatever visible/message/msgType state Python publishes, never
runs its own dismiss timer. The "Copied" button-text feedback after
copyDetails() is deliberately NOT round-tripped through Python: it has zero
externally-observable consequence beyond the button's own label, so it's a
local, JS-side cosmetic detail like any other island's hover/pressed state.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication

from graphlink_island_bridge import IslandBridge

_KNOWN_TYPES = frozenset({"info", "success", "warning", "error"})

# Old widget: setFixedWidth(460), setMinimumHeight(108), then adjustSize() let
# it grow taller for longer wrapped messages with no explicit cap. The web
# version needs an explicit cap (WebIslandHost's height negotiation requires
# one) - 400px comfortably fits every real message this app currently sends
# (the longest, at graphlink_window.py:815, wraps to ~3 lines at 460px wide)
# with room to spare; the island's own CSS makes the message body scroll
# internally past that, so no message can ever become truly unreadable.
NOTIFICATION_MIN_HEIGHT = 108
NOTIFICATION_MAX_HEIGHT = 400


class NotificationBridge(IslandBridge, QObject):
    stateChanged = Signal(str)
    heightRequested = Signal(int)  # Qt-only side channel; see ComposerBridge's identical field
    # Also Qt-only. NotificationWebHost connects this straight to setVisible()
    # so isVisible()/raise_() at the ~64 call sites keep meaning exactly what
    # they meant against the old QWidget (only true while a message is
    # actively shown) - stateChanged's "visible" field alone can't drive that,
    # since nothing else ever calls .show()/.hide() on the host widget.
    visibilityChanged = Signal(bool)

    def __init__(self, window, parent=None):
        QObject.__init__(self, parent)
        IslandBridge.__init__(self)
        self.window = window
        self._visible = False
        self._message = ""
        self._msg_type = "info"
        self._last_height = 0

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self._hide)

    def _transport_send(self, payload_json: str) -> None:
        self.stateChanged.emit(payload_json)

    def _build_state_payload(self) -> dict[str, Any]:
        return {
            "visible": self._visible,
            "message": self._message,
            "msgType": self._msg_type,
        }

    def _on_dispose(self) -> None:
        self.hide_timer.stop()

    @Slot()
    def ready(self):
        self.publish()

    def show_message(self, message, duration_ms=5000, msg_type="info"):
        """Same public signature/semantics as the widget this replaces -
        every one of its 64 call sites keeps calling this verbatim through
        NotificationWebHost's pass-through, no changes needed at any of
        them."""
        if (
            self.window is not None
            and hasattr(self.window, "should_show_notification")
            and not self.window.should_show_notification(msg_type)
        ):
            return

        # Same fallback-to-"info" the old widget's TYPE_STYLES.get(msg_type, ...)
        # applied for an unrecognized type - required here too, since the wire
        # contract's msgType is a closed Literal the generated validator would
        # otherwise reject outright (several call sites pass a dynamic string,
        # e.g. a settings-derived "level"/"tone" value, not always one of the
        # 4 literal type names directly).
        normalized_type = msg_type if msg_type in _KNOWN_TYPES else "info"

        if msg_type == "error":
            duration_ms = 0
        elif msg_type == "warning":
            duration_ms = max(duration_ms, 10000)

        self.hide_timer.stop()

        was_visible = self._visible
        self._visible = True
        self._message = str(message or "")
        self._msg_type = normalized_type
        self.publish()
        if not was_visible:
            self.visibilityChanged.emit(True)

        if duration_ms > 0:
            self.hide_timer.start(duration_ms)

    def _hide(self):
        if not self._visible:
            return
        self._visible = False
        self.publish()
        self.visibilityChanged.emit(False)

    @Slot()
    def copyDetails(self):
        QApplication.clipboard().setText(self._message)

    @Slot()
    def dismiss(self):
        """User-initiated early close - the old widget's close (X) and
        Dismiss button both called hide_banner() directly, with no Python
        round trip. Needed here for the same reason: "error" type sets
        duration_ms=0 (never auto-hides, see the branch above), so without
        this, an error notification could never be closed at all."""
        self.hide_timer.stop()
        self._hide()

    @Slot(int)
    def resize(self, height: int):
        bounded = max(NOTIFICATION_MIN_HEIGHT, min(NOTIFICATION_MAX_HEIGHT, int(height)))
        if bounded == self._last_height:
            return
        self._last_height = bounded
        self.heightRequested.emit(bounded)
