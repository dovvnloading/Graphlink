"""Web host for the token-counter island - Phase 5 increment 4.

The phase-open recon flagged this as the one island with no dedicated host
class at all: graphlink_window.py used to construct a bare, un-subclassed
WebIslandHost directly, with its position computed inline inside
ChatWindow._update_overlay_positions rather than via a self-owned
reposition() method the way every other island (including NotificationWebHost,
built in an earlier phase) already has. This isn't a new architectural idea -
it just finishes applying the one pattern every other island already uses,
needed so this island has a real reposition_fn to register with
OverlayCoordinator alongside pin/search/composer-pickers/notification.

Positioning math is unchanged from the inline version it replaces: bottom-left
corner of the graph viewport, 10px padding, clamped so it never goes above the
top edge on a very short viewport.
"""

from __future__ import annotations

from graphlink_token_counter_bridge import TokenCounterBridge
from graphlink_web_island_host import WebIslandHost

TOKEN_COUNTER_UNAVAILABLE_MESSAGE = (
    "Token counter unavailable because QtWebEngine failed to initialize."
)

TOKEN_COUNTER_WIDTH = 150
TOKEN_COUNTER_HEIGHT = 90


class TokenCounterWebHost(WebIslandHost):
    def __init__(self, parent=None):
        bridge = TokenCounterBridge()
        super().__init__(
            bridge=bridge,
            asset_dir_name="token-counter",
            bridge_object_name="tokenCounterBridge",
            unavailable_message=TOKEN_COUNTER_UNAVAILABLE_MESSAGE,
            parent=parent,
        )
        self.setFixedSize(TOKEN_COUNTER_WIDTH, TOKEN_COUNTER_HEIGHT)

    def reposition(self, viewport) -> None:
        padding = 10
        target_y = viewport.height() - self.height() - padding
        self.move(padding, max(padding, target_y))
