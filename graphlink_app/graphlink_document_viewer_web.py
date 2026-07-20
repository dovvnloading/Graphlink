"""Web host for the document-viewer island - Phase 4 increment 3.

Architecturally unlike AboutWebHost/HelpWebHost/SettingsWebHost (all
floating, frameless Qt.WindowType.Tool top-level windows): the legacy
DocumentViewerPanel is a permanent embedded QWidget, constructed once in
ChatWindow.__init__ and added directly to content_layout (a QHBoxLayout
sibling of chat_view) via addWidget() - it participates in the main window's
own layout, taking up real horizontal space at a fixed 500px width, rather
than floating above it. DocumentViewerWebHost keeps that exact shape: a
plain child QFrame with no Window flag, toggled via setVisible() only,
never .close()/closeEvent - matching NotificationWebHost's embedding style,
not About/Help/Settings'.

Because this host is never a native top-level window, it never receives a
real closeEvent at all (Qt only delivers that to windows), so - confirmed by
direct code recon, not assumed - it needs no hide-not-teardown override the
way every closable/reopenable floating host in this migration has needed.
True teardown still happens once, at app exit, via the shared shutdown
registry exactly like every other host.
"""

from __future__ import annotations

from graphlink_document_viewer_bridge import DocumentViewerBridge
from graphlink_web_island_host import WebIslandHost

DOCUMENT_VIEWER_UNAVAILABLE_MESSAGE = (
    "Document View is unavailable because QtWebEngine failed to initialize."
)

DOCUMENT_VIEWER_WIDTH = 500


class DocumentViewerWebHost(WebIslandHost):
    def __init__(self, parent=None):
        bridge = DocumentViewerBridge()
        super().__init__(
            bridge=bridge,
            asset_dir_name="document-viewer",
            bridge_object_name="documentViewerBridge",
            corner_radius=0,  # flush side panel (border-right only), not a floating card
            unavailable_message=DOCUMENT_VIEWER_UNAVAILABLE_MESSAGE,
            parent=parent,
        )
        self.setFixedWidth(DOCUMENT_VIEWER_WIDTH)
        self.setVisible(False)  # old widget: never shown until show_document_view()

    def set_document_content(self, text: str) -> None:
        """Same public name/signature as the legacy DocumentViewerPanel -
        graphlink_window.py's show_document_view() calls this unchanged;
        only self.doc_viewer_panel's type changes, not the call site."""
        self.bridge.set_content(text)
