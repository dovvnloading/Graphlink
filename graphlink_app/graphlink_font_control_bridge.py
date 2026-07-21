"""Desktop-side state bridge for the font-control island (Phase 6 increment
4) - absorbs `FontControl` (native QWidget, deleted this increment along
with `GridControl` in `graphlink_widgets/controls.py`).

Takes `chat_view` (the real `ChatView`) as its portal. All 3 intents forward
straight to `chat_view.scene()`'s own pre-existing `setFontFamily`/
`setFontSize`/`setFontColor` - the exact same methods the legacy widget's
own Qt signals already called (`ChatScene` was already the sole owner of
current font state; nothing about this migration moves that ownership).
Fire-and-forget, no `publish()` afterward - there is no live font state in
this payload at all to keep in sync (see the payload module's own
docstring).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QColor

from graphlink_island_bridge import IslandBridge

FONT_CONTROL_MIN_HEIGHT = 140
FONT_CONTROL_MAX_HEIGHT = 220

FONT_FAMILIES = [
    "Segoe UI", "Arial", "Verdana", "Tahoma", "Consolas",
    "Calibri", "Cambria", "Lucida Grande", "Trebuchet MS",
    "Courier New", "Times New Roman", "Georgia", "System UI",
    "DejaVu Sans", "Segoe UI Variable", "Arial Rounded MT Bold",
]
FONT_COLOR_PRESETS = ["#F0F0F0", "#C7C7C7", "#949494", "#818181"]
FONT_SIZE_MIN = 8
FONT_SIZE_MAX = 16


class FontControlBridge(IslandBridge, QObject):
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
            "fontFamilies": list(FONT_FAMILIES),
            "colorPresets": list(FONT_COLOR_PRESETS),
            "sizeMin": FONT_SIZE_MIN,
            "sizeMax": FONT_SIZE_MAX,
        }

    @Slot()
    def ready(self):
        self.publish()

    @Slot(str)
    def setFontFamily(self, family: str):
        self._chat_view.scene().setFontFamily(str(family))

    @Slot(int)
    def setFontSize(self, size: int):
        self._chat_view.scene().setFontSize(int(size))

    @Slot(str)
    def setFontColor(self, color_hex: str):
        self._chat_view.scene().setFontColor(QColor(str(color_hex)))

    @Slot(int)
    def resize(self, height: int):
        bounded = max(FONT_CONTROL_MIN_HEIGHT, min(FONT_CONTROL_MAX_HEIGHT, int(height)))
        if bounded == self._last_height:
            return
        self._last_height = bounded
        self.heightRequested.emit(bounded)
