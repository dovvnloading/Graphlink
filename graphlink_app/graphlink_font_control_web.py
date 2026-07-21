"""Web host for the font-control island (Phase 6 increment 4) - absorbs
`FontControl` (`graphlink_widgets/controls.py`, deleted this increment).

A plain embedded child QFrame (no Window flag, no
`dismiss_on_outside_focus`) - see graphlink_grid_control_web.py's own
docstring for the full rationale, identical here.
"""

from __future__ import annotations

from graphlink_font_control_bridge import (
    FONT_CONTROL_MAX_HEIGHT,
    FONT_CONTROL_MIN_HEIGHT,
    FontControlBridge,
)
from graphlink_web_island_host import WebIslandHost

FONT_CONTROL_UNAVAILABLE_MESSAGE = (
    "The font control panel is unavailable because QtWebEngine failed to initialize."
)

FONT_CONTROL_WIDTH = 220


class FontControlHost(WebIslandHost):
    def __init__(self, chat_view, parent=None):
        bridge = FontControlBridge(chat_view)
        super().__init__(
            bridge=bridge,
            asset_dir_name="font-control",
            bridge_object_name="fontControlBridge",
            min_height=FONT_CONTROL_MIN_HEIGHT,
            max_height=FONT_CONTROL_MAX_HEIGHT,
            unavailable_message=FONT_CONTROL_UNAVAILABLE_MESSAGE,
            parent=parent,
        )
        self.setFixedWidth(FONT_CONTROL_WIDTH)
        self.bridge.heightRequested.connect(self.apply_requested_height)
        self.setVisible(False)
