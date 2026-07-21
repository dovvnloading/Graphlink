"""Desktop-side state bridge for the minimap island (Phase 6 increment 5) -
absorbs MinimapWidget (native QPainter QWidget, deleted this increment).

Debounces publish() against ChatScene.scene_changed - unlike every other
bridge in this migration, which either publishes once (plugin-picker) or
reacts to a naturally low-frequency source (PinOverlayBridge's own store
events), scene_changed can fire many times in a tight burst (bulk node
operations, several scene mutations within a single user action) -
re-serializing every node's preview text on each individual emission would
be real, avoidable wire traffic a native QPainter repaint scheduling never
had to worry about (Qt's own update() call already coalesces repaints for
free). A single-shot QTimer restarted on each scene_changed collapses a
burst into one publish after a short quiet period, subscribed directly in
__init__ - the same "this bridge autonomously wires its own real data
source" shape PinOverlayBridge already established.

selectNode(id) rebuilds a fresh id->node lookup from scene.nodes on every
call (cheap at the node counts this app deals with) rather than maintaining
a second, separately-invalidated cache - scene.nodes is already the single
source of truth update_nodes() used to trust directly. `id` is a plain
`str(id(node))`, not a persisted node property - see the payload module's
own docstring for why.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from graphlink_island_bridge import IslandBridge

MINIMAP_DEBOUNCE_MS = 150
PREVIEW_MAX_LENGTH = 50


def _preview_for(node) -> str:
    """Matches MinimapWidget._show_tooltip()'s own preview text exactly."""
    text_preview = node.text.strip().split("\n")[0]
    if len(text_preview) > PREVIEW_MAX_LENGTH:
        text_preview = text_preview[: PREVIEW_MAX_LENGTH - 3] + "..."
    if not text_preview:
        text_preview = "[Attachment/Content Node]"
    return text_preview


class MinimapBridge(IslandBridge, QObject):
    stateChanged = Signal(str)

    def __init__(self, chat_view, parent=None):
        QObject.__init__(self, parent)
        IslandBridge.__init__(self)
        self._chat_view = chat_view
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(MINIMAP_DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self.publish)
        chat_view.scene().scene_changed.connect(self._on_scene_changed)

    def _transport_send(self, payload_json: str) -> None:
        self.stateChanged.emit(payload_json)

    def _build_state_payload(self) -> dict[str, Any]:
        return {
            "nodes": [
                {"id": str(id(node)), "isUser": bool(node.is_user), "preview": _preview_for(node)}
                for node in self._chat_view.scene().nodes
            ],
        }

    def _on_scene_changed(self):
        self._debounce_timer.start()

    @Slot()
    def ready(self):
        self.publish()

    @Slot(str)
    def selectNode(self, node_id: str):
        node_id = str(node_id)
        for node in self._chat_view.scene().nodes:
            if str(id(node)) == node_id:
                self._chat_view._on_minimap_node_selected(node)
                return
