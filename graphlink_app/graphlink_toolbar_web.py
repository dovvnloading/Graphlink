"""Web host for the toolbar island (Phase 6 increment 1) - absorbs the
native QToolBar built by graphlink_window.py's setup_toolbar() (deleted this
increment).

Unlike every other island so far (all small, corner/anchor-positioned
boxes), the toolbar is full-window-width permanent chrome - matching the
legacy QToolBar's own behavior inside its QVBoxLayout, this host passes
equal min_height/max_height to WebIslandHost, which gives it the
Expanding-horizontal/Fixed-vertical size policy every prior negotiated-height
host already gets, letting normal Qt layout stretch it to the container's
full width automatically. No content-driven height negotiation is needed (a
single toolbar row never resizes), so min_height == max_height == a single
fixed value rather than a real negotiated range.

Never registered with OverlayCoordinator - it isn't a floating/anchored
overlay surface needing z-order arbitration against other surfaces; it's a
permanent part of the main layout, like content_widget/chat_view themselves.
"""

from __future__ import annotations

from graphlink_toolbar_bridge import ToolbarBridge
from graphlink_web_island_host import WebIslandHost

TOOLBAR_UNAVAILABLE_MESSAGE = (
    "The toolbar is unavailable because QtWebEngine failed to initialize."
)

TOOLBAR_HEIGHT = 44


class ToolbarHost(WebIslandHost):
    def __init__(self, window, parent=None):
        bridge = ToolbarBridge(window)
        super().__init__(
            bridge=bridge,
            asset_dir_name="toolbar",
            bridge_object_name="toolbarBridge",
            corner_radius=0,
            min_height=TOOLBAR_HEIGHT,
            max_height=TOOLBAR_HEIGHT,
            unavailable_message=TOOLBAR_UNAVAILABLE_MESSAGE,
            parent=parent,
        )
