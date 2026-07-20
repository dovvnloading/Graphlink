"""Desktop-side state bridge for the search-overlay island.

Phase 5 increment 1 - live query-as-you-type search over the graph scene,
matching the legacy SearchOverlay's exact behavior (GraphScene.find_items,
current-match cycling via _find_next_match/_find_previous_match in the old
ChatWindow, selection + centerOn via _focus_on_current_match). The query text
is Python-driven (search(text) is called on every keystroke, matching the
legacy widget's own live textChanged wiring) but never round-tripped back -
only the derived currentIndex/totalMatches state publishes, since the query
itself already sits in the DOM input the user is typing into.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from graphlink_island_bridge import IslandBridge


class SearchOverlayBridge(IslandBridge, QObject):
    stateChanged = Signal(str)

    def __init__(self, chat_view, parent=None):
        QObject.__init__(self, parent)
        IslandBridge.__init__(self)
        self._chat_view = chat_view
        self._matches = []
        self._current_index = -1

    def _transport_send(self, payload_json: str) -> None:
        self.stateChanged.emit(payload_json)

    def _build_state_payload(self) -> dict[str, Any]:
        return {"currentIndex": self._current_index, "totalMatches": len(self._matches)}

    @Slot()
    def ready(self):
        self.publish()

    @Slot(str)
    def search(self, text: str):
        scene = self._chat_view.scene()
        text = str(text or "")
        self._matches = scene.find_items(text) if text else []
        self._current_index = -1
        scene.update_search_highlight(self._matches)
        self.publish()

    @Slot()
    def next(self):
        if not self._matches:
            return
        self._current_index = (self._current_index + 1) % len(self._matches)
        self._focus_current()

    @Slot()
    def previous(self):
        if not self._matches:
            return
        self._current_index = (self._current_index - 1) % len(self._matches)
        self._focus_current()

    def _focus_current(self):
        if not (0 <= self._current_index < len(self._matches)):
            return
        target = self._matches[self._current_index]
        scene = self._chat_view.scene()
        scene.clearSelection()
        target.setSelected(True)
        self._chat_view.centerOn(target)
        self.publish()

    @Slot()
    def close(self):
        """Hides the host directly (setVisible(False), not .close()) -
        matches the legacy widget's own search_overlay.hide() and avoids the
        closeEvent-teardown risk class embedded hosts don't need to opt into
        (see graphlink_search_overlay_web.py's module docstring). self.
        parent() is the SearchOverlayHost (set by WebIslandHost.__init__'s
        own bridge.setParent(self))."""
        scene = self._chat_view.scene()
        scene.update_search_highlight([])
        self._matches = []
        self._current_index = -1
        parent = self.parent()
        if parent is not None and hasattr(parent, "setVisible"):
            parent.setVisible(False)
        self.publish()
