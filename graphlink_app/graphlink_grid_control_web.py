"""Web host for the grid-control island (Phase 6 increment 4) - absorbs
`GridControl` (`graphlink_widgets/controls.py`, deleted this increment).

A plain embedded child QFrame (no Window flag, no
`dismiss_on_outside_focus`) - the legacy `GridControl` was a plain
`QWidget(self)` with no popup/window flags and no outside-click dismissal
either, only ever shown/hidden together with `FontControl`/`control_widget`
via `ChatView.toggle_overlays_visibility()`. Owned and positioned by
`ChatView` itself (not `ChatWindow`/`OverlayCoordinator`) - matching where
`GridControl` already lived; this is ChatView's own pre-existing floating-
panel stacking system (`_update_overlay_positions()`), a separate mechanism
from `OverlayCoordinator`, and out of scope to merge this increment.
"""

from __future__ import annotations

from graphlink_grid_control_bridge import (
    GRID_CONTROL_MAX_HEIGHT,
    GRID_CONTROL_MIN_HEIGHT,
    GridControlBridge,
)
from graphlink_web_island_host import WebIslandHost

GRID_CONTROL_UNAVAILABLE_MESSAGE = (
    "The grid control panel is unavailable because QtWebEngine failed to initialize."
)

GRID_CONTROL_WIDTH = 220


class GridControlHost(WebIslandHost):
    def __init__(self, chat_view, parent=None):
        bridge = GridControlBridge(chat_view)
        super().__init__(
            bridge=bridge,
            asset_dir_name="grid-control",
            bridge_object_name="gridControlBridge",
            min_height=GRID_CONTROL_MIN_HEIGHT,
            max_height=GRID_CONTROL_MAX_HEIGHT,
            unavailable_message=GRID_CONTROL_UNAVAILABLE_MESSAGE,
            parent=parent,
        )
        self.setFixedWidth(GRID_CONTROL_WIDTH)
        self.bridge.heightRequested.connect(self.apply_requested_height)
        self.setVisible(False)
