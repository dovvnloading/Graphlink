"""Desktop-side state bridge for the composer-context island (Phase 5
increment 3) - absorbs ComposerContextPopup (native Qt.Tool popup, deleted
this increment).

Takes the SAME context dict ComposerBridge.reviewContext() already builds
(anchor/items/totalTokens/reviewAvailable - see graphlink_composer_bridge.py's
_build_state_payload()) and republishes it; `removeContextItem` forwards
straight to the real ComposerBridge.removeContextItem(), the exact Slot the
legacy popup's contextItemRemoved signal used to reach.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from graphlink_island_bridge import IslandBridge

COMPOSER_CONTEXT_MIN_HEIGHT = 140
COMPOSER_CONTEXT_MAX_HEIGHT = 420


class ComposerContextBridge(IslandBridge, QObject):
    stateChanged = Signal(str)
    heightRequested = Signal(int)  # Qt-only side channel; see PinOverlayBridge's identical field

    def __init__(self, composer_bridge, parent=None):
        QObject.__init__(self, parent)
        IslandBridge.__init__(self)
        self._composer_bridge = composer_bridge
        self._context: dict[str, Any] = {}
        self._last_height = 0

    def _transport_send(self, payload_json: str) -> None:
        self.stateChanged.emit(payload_json)

    def open(self, context: dict[str, Any]) -> None:
        """Called directly by graphlink_window.py's open_composer_context_popup
        - Python-initiated, mirroring ComposerPickerBridge.open()'s identical
        shape."""
        self._context = context if isinstance(context, dict) else {}
        self.publish()

    def _build_state_payload(self) -> dict[str, Any]:
        anchor = self._context.get("anchor")
        items = self._context.get("items") or []
        return {
            "anchor": (
                {
                    "id": str(anchor.get("id") or ""),
                    "label": str(anchor.get("label") or ""),
                    "type": str(anchor.get("type") or "Graph"),
                }
                if isinstance(anchor, dict)
                else None
            ),
            "items": [
                {
                    "id": str(item.get("id") or ""),
                    "name": str(item.get("name") or ""),
                    "kind": str(item.get("kind") or "Context"),
                    "tokenCount": int(item.get("tokenCount") or 0),
                }
                for item in items
                if isinstance(item, dict)
            ],
            "totalTokens": int(self._context.get("totalTokens") or 0),
        }

    @Slot()
    def ready(self):
        self.publish()

    @Slot(str)
    def removeContextItem(self, item_id: str):
        """Matches the legacy popup's own _remove_item(): unconditionally
        closes afterward, even for an (unreachable in practice) empty id -
        removing any one item closes the whole review panel, requiring the
        user to reopen it to remove another."""
        item_id = str(item_id or "").strip()
        if item_id:
            self._composer_bridge.removeContextItem(item_id)
        self.close()

    @Slot(int)
    def resize(self, height: int):
        bounded = max(COMPOSER_CONTEXT_MIN_HEIGHT, min(COMPOSER_CONTEXT_MAX_HEIGHT, int(height)))
        if bounded == self._last_height:
            return
        self._last_height = bounded
        self.heightRequested.emit(bounded)

    @Slot()
    def close(self):
        parent = self.parent()
        if parent is not None and hasattr(parent, "setVisible"):
            parent.setVisible(False)
