"""Desktop-side state bridge for the document-viewer island.

Phase 4 increment 3 - unlike About/Help, this bridge IS content-carrying:
set_content(text) publishes whatever markdown _extract_document_view_content()
(graphlink_window.py) produced for the node the user just opened. That ladder
and its 4 helper methods stay completely unchanged by this migration - only
the rendering target moves, from QTextEdit.setHtml(markdown.markdown(...))
to react-markdown in the browser.

set_content() is a plain method, not a Slot: only Python ever calls it (the
web side has no reason to push content back), exactly mirroring
NotificationBridge.show_message()'s shape for the same reason.

close() targets self.parent().setVisible(False), not .close() - unlike
About/Help's bridge.close(), which calls .close() on a floating
Qt.WindowType.Tool host whose own closeEvent hides rather than tears down.
This island's host is a plain embedded child QFrame (added directly to
content_layout, never a Window), so it never receives a native closeEvent at
all - see graphlink_document_viewer_web.py's module docstring for the full
embedding-shape reasoning.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from graphlink_island_bridge import IslandBridge


class DocumentViewerBridge(IslandBridge, QObject):
    stateChanged = Signal(str)

    def __init__(self, parent=None):
        QObject.__init__(self, parent)
        IslandBridge.__init__(self)
        self._content = ""

    def _transport_send(self, payload_json: str) -> None:
        self.stateChanged.emit(payload_json)

    def _build_state_payload(self) -> dict[str, Any]:
        return {"content": self._content}

    @Slot()
    def ready(self):
        self.publish()

    def set_content(self, text: str) -> None:
        """Same public shape as the legacy DocumentViewerPanel.
        set_document_content(markdown_text) - graphlink_window.py's
        show_document_view() calls this through DocumentViewerWebHost's
        identically-named facade method, unchanged at its one call site."""
        self._content = str(text or "")
        self.publish()

    @Slot()
    def close(self):
        """Lets the in-DOM Close button trigger the same setVisible(False)
        the toolbar-adjacent "Open Document View" flow's hide_document_view()
        already calls. self.parent() is the DocumentViewerWebHost (set by
        WebIslandHost.__init__'s own bridge.setParent(self)); setVisible, not
        close, because this host is never a native top-level window - see
        this module's own docstring."""
        parent = self.parent()
        if parent is not None and hasattr(parent, "setVisible"):
            parent.setVisible(False)
