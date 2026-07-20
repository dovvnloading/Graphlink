"""Web host for the notification island.

Unlike TokenCounterWidget (Phase 2's first island), this one DOES need a
thin WebIslandHost subclass - not for legacy-widget-impersonation the way
ComposerWebHost does, but because show_message(message, duration_ms,
msg_type) has to stay callable verbatim across 64 call sites in 16 files
(the checklist's own "facade preserved verbatim" requirement). Touching all
64 sites to call through a separate bridge attribute, the way the 3-call-site
TokenCounterWidget migration did, would be real, unnecessary churn here.
update_position() is kept as a host method for the same reason - it has
exactly one call site (graphlink_window.py's _update_overlay_positions),
already being touched by this migration regardless, so there's no facade
pressure there, but keeping it here still matches the old widget's
observable shape most closely.
"""

from __future__ import annotations

from graphlink_notification_bridge import (
    NOTIFICATION_MAX_HEIGHT,
    NOTIFICATION_MIN_HEIGHT,
    NotificationBridge,
)
from graphlink_web_island_host import WebIslandHost

NOTIFICATION_UNAVAILABLE_MESSAGE = (
    "Notifications are unavailable because QtWebEngine failed to initialize."
)


class NotificationWebHost(WebIslandHost):
    def __init__(self, window, parent=None):
        bridge = NotificationBridge(window)
        super().__init__(
            bridge=bridge,
            asset_dir_name="notification",
            bridge_object_name="notificationBridge",
            min_height=NOTIFICATION_MIN_HEIGHT,
            max_height=NOTIFICATION_MAX_HEIGHT,
            unavailable_message=NOTIFICATION_UNAVAILABLE_MESSAGE,
            parent=parent,
        )
        self.setFixedWidth(460)  # old widget: setFixedWidth(460); height is negotiated
        self.margin_bottom = 20
        self.margin_right = 20
        self.bridge.heightRequested.connect(self.apply_requested_height)
        self.bridge.visibilityChanged.connect(self.setVisible)
        self.setVisible(False)  # old widget: setVisible(False) in its own __init__

    def show_message(self, message, duration_ms=5000, msg_type="info"):
        self.bridge.show_message(message, duration_ms, msg_type)

    def update_position(self):
        """Matches the old NotificationBanner.update_position() exactly:
        bottom-right corner of the parent, only while visible."""
        if self.isVisible() and self.parent():
            parent_rect = self.parent().rect()
            target_x = parent_rect.width() - self.width() - self.margin_right
            target_y = parent_rect.height() - self.height() - self.margin_bottom
            self.move(target_x, target_y)
