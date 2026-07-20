"""Web host for the search-overlay island - Phase 5 increment 1.

A plain embedded child QFrame (no Window flag), matching DocumentViewer's
established "embedded, not floating" shape - not a Qt.WindowType.Tool window
like About/Help/Settings. Confirmed, not assumed: the legacy SearchOverlay
this replaces is itself a plain QWidget, shown/hidden purely via setVisible()
calls (graphlink_window.py's show_search_overlay/_close_search), never
.close(), so this host never receives a native closeEvent either and needs
no hide-not-teardown override.

Sized to match the legacy widget's fixed 300px width and natural ~44px
height exactly - a compact toolbar-like bar, not a floating card, hence the
smaller corner_radius than every other island's default.
"""

from __future__ import annotations

from graphlink_search_overlay_bridge import SearchOverlayBridge
from graphlink_web_island_host import WebIslandHost

SEARCH_OVERLAY_UNAVAILABLE_MESSAGE = (
    "Search is unavailable because QtWebEngine failed to initialize."
)

SEARCH_OVERLAY_WIDTH = 300
SEARCH_OVERLAY_HEIGHT = 44


class SearchOverlayHost(WebIslandHost):
    def __init__(self, chat_view, parent=None):
        bridge = SearchOverlayBridge(chat_view)
        super().__init__(
            bridge=bridge,
            asset_dir_name="search-overlay",
            bridge_object_name="searchOverlayBridge",
            corner_radius=8,
            unavailable_message=SEARCH_OVERLAY_UNAVAILABLE_MESSAGE,
            parent=parent,
        )
        self.setFixedSize(SEARCH_OVERLAY_WIDTH, SEARCH_OVERLAY_HEIGHT)
        self.setVisible(False)

    def reposition(self, viewport) -> None:
        padding = 10
        self.move(viewport.width() - self.width() - padding, padding)
