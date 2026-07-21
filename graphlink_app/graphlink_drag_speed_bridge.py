"""Desktop-side state bridge for the drag-speed island (Phase 6 increment
5) - absorbs `ChatView.control_widget` (native QWidget, deleted this
increment).

`setDragFactor(float)` matches the plan checklist's own literal intent
signature - it sets `chat_view._drag_factor` directly, the SAME plain float
attribute `ChatView._update_drag()` already computed and cached
(`self.drag_slider.value() / 100.0`) before this increment; nothing about
how drag speed is actually consumed (`graphlink_view.py`'s panning math)
changes. No clamping - the legacy `_update_drag()` never validated either,
relying entirely on the native QSlider's own min/max to constrain what
value could ever reach it; the DOM range input's own min/max attributes
play the same role here.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from graphlink_island_bridge import IslandBridge

DRAG_SPEED_MIN_HEIGHT = 80
DRAG_SPEED_MAX_HEIGHT = 140

PERCENT_PRESETS = [25, 50, 75, 100]
PERCENT_MIN = 10
PERCENT_MAX = 100


class DragSpeedBridge(IslandBridge, QObject):
    stateChanged = Signal(str)
    heightRequested = Signal(int)  # Qt-only side channel; see PinOverlayBridge's identical field

    def __init__(self, chat_view, parent=None):
        QObject.__init__(self, parent)
        IslandBridge.__init__(self)
        self._chat_view = chat_view
        self._last_height = 0

    def _transport_send(self, payload_json: str) -> None:
        self.stateChanged.emit(payload_json)

    def _build_state_payload(self) -> dict[str, Any]:
        return {
            "percentPresets": list(PERCENT_PRESETS),
            "percentMin": PERCENT_MIN,
            "percentMax": PERCENT_MAX,
        }

    @Slot()
    def ready(self):
        self.publish()

    @Slot(float)
    def setDragFactor(self, factor: float):
        self._chat_view._drag_factor = float(factor)

    @Slot(int)
    def resize(self, height: int):
        bounded = max(DRAG_SPEED_MIN_HEIGHT, min(DRAG_SPEED_MAX_HEIGHT, int(height)))
        if bounded == self._last_height:
            return
        self._last_height = bounded
        self.heightRequested.emit(bounded)
