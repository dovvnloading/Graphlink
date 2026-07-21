"""Web host for the minimap island (Phase 6 increment 5) - absorbs
MinimapWidget (native QPainter QWidget, deleted this increment).

Unlike every other host in this migration, height is NOT content-negotiated
via ResizeObserver - it is externally imposed by `ChatView.
_update_overlay_positions()` (`setFixedHeight(int(viewport_height * 0.7))`),
matching MinimapWidget's own identical externally-imposed-height behavior
exactly (never resizes itself; the caller resizes it). No `min_height`/
`max_height` is passed to the base constructor, and there is deliberately no
`resize`/`heightRequested` Slot on MinimapBridge - nothing about this
island's own content ever needs to ask Python for more room. Width stays
fixed at 40px, matching the legacy narrow vertical-strip design exactly.

A plain embedded child QFrame (no Window flag, no
`dismiss_on_outside_focus`) - the legacy widget had neither either.
"""

from __future__ import annotations

from graphlink_minimap_bridge import MinimapBridge
from graphlink_web_island_host import WebIslandHost

MINIMAP_UNAVAILABLE_MESSAGE = (
    "The minimap is unavailable because QtWebEngine failed to initialize."
)

MINIMAP_WIDTH = 40


class MinimapHost(WebIslandHost):
    def __init__(self, chat_view, parent=None):
        bridge = MinimapBridge(chat_view)
        super().__init__(
            bridge=bridge,
            asset_dir_name="minimap",
            bridge_object_name="minimapBridge",
            unavailable_message=MINIMAP_UNAVAILABLE_MESSAGE,
            parent=parent,
        )
        self.setFixedWidth(MINIMAP_WIDTH)
