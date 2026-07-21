"""Web host for the drag-speed island (Phase 6 increment 5) - absorbs
`ChatView.control_widget` (native QWidget, deleted this increment).

A plain embedded child QFrame (no Window flag, no
`dismiss_on_outside_focus`) - the legacy widget was a plain `QWidget(self)`
with no popup/window flags and no outside-click dismissal, only ever
shown/hidden together with the grid/font panels via `ChatView.
toggle_overlays_visibility()`. Owned and positioned by `ChatView` itself,
matching where `control_widget` already lived - see
graphlink_grid_control_web.py's own docstring for the full rationale
(ChatView's own floating-panel stacking system, separate from
`OverlayCoordinator`).
"""

from __future__ import annotations

from graphlink_drag_speed_bridge import (
    DRAG_SPEED_MAX_HEIGHT,
    DRAG_SPEED_MIN_HEIGHT,
    DragSpeedBridge,
)
from graphlink_web_island_host import WebIslandHost

DRAG_SPEED_UNAVAILABLE_MESSAGE = (
    "The drag speed panel is unavailable because QtWebEngine failed to initialize."
)

DRAG_SPEED_WIDTH = 220


class DragSpeedHost(WebIslandHost):
    def __init__(self, chat_view, parent=None):
        bridge = DragSpeedBridge(chat_view)
        super().__init__(
            bridge=bridge,
            asset_dir_name="drag-speed",
            bridge_object_name="dragSpeedBridge",
            min_height=DRAG_SPEED_MIN_HEIGHT,
            max_height=DRAG_SPEED_MAX_HEIGHT,
            unavailable_message=DRAG_SPEED_UNAVAILABLE_MESSAGE,
            parent=parent,
        )
        self.setFixedWidth(DRAG_SPEED_WIDTH)
        self.bridge.heightRequested.connect(self.apply_requested_height)
        self.setVisible(False)
