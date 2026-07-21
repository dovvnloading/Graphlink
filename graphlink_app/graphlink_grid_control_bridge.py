"""Desktop-side state bridge for the grid-control island (Phase 6 increment
4) - absorbs `GridControl` (native QWidget, deleted this increment along
with `FontControl` in `graphlink_widgets/controls.py`).

Takes `chat_view` (the real `ChatView`) as its portal, the same shape
`PluginPickerBridge` takes `plugin_portal` - every intent either mutates the
real `chat_view.grid_settings` (the extracted `GridViewSettings` model) plus
triggers a real repaint, or forwards straight to `chat_view`'s own
pre-existing `_on_snap_toggled`/`_on_ortho_toggled`/`_on_guides_toggled`/
`_on_fade_connections_toggled` methods (unchanged - these already wrote
directly onto `ChatScene`, not the widget, so nothing about them needed to
move). The 4 toggle Slots deliberately do NOT call `publish()` afterward -
their state isn't part of this payload at all (see the payload module's own
docstring), matching the toolbar's `controlsChecked` "no server round-trip
for state nothing else needs to know" precedent exactly.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from graphlink_config import get_current_palette
from graphlink_grid_view_settings import GRID_SIZE_PRESETS, GRID_STYLE_PRESETS
from graphlink_island_bridge import IslandBridge

GRID_CONTROL_MIN_HEIGHT = 320
GRID_CONTROL_MAX_HEIGHT = 460


def _color_presets() -> list[str]:
    palette = get_current_palette()
    return [
        "#404040",
        "#555555",
        palette.SELECTION.name(),
        palette.USER_NODE.name(),
        palette.AI_NODE.name(),
    ]


class GridControlBridge(IslandBridge, QObject):
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
        settings = self._chat_view.grid_settings
        return {
            "gridSize": settings.grid_size,
            "gridOpacityPercent": round(settings.grid_opacity * 100),
            "gridStyle": settings.grid_style,
            "gridColor": settings.grid_color,
            "sizePresets": list(GRID_SIZE_PRESETS),
            "stylePresets": list(GRID_STYLE_PRESETS),
            "colorPresets": _color_presets(),
        }

    @Slot()
    def ready(self):
        self.publish()

    @Slot(int)
    def setGridSize(self, size: int):
        self._chat_view.grid_settings.grid_size = int(size)
        self._chat_view.update()
        self.publish()

    @Slot(int)
    def setGridOpacityPercent(self, percent: int):
        bounded = max(0, min(100, int(percent)))
        self._chat_view.grid_settings.grid_opacity = bounded / 100.0
        self._chat_view.update()
        self.publish()

    @Slot(str)
    def setGridStyle(self, style: str):
        self._chat_view.grid_settings.grid_style = str(style)
        self._chat_view.update()
        self.publish()

    @Slot(str)
    def setGridColor(self, color_hex: str):
        self._chat_view.grid_settings.grid_color = str(color_hex)
        self._chat_view.update()
        self.publish()

    @Slot(bool)
    def setSnapToGrid(self, enabled: bool):
        self._chat_view._on_snap_toggled(bool(enabled))

    @Slot(bool)
    def setOrthogonalConnections(self, enabled: bool):
        self._chat_view._on_ortho_toggled(bool(enabled))

    @Slot(bool)
    def setSmartGuides(self, enabled: bool):
        self._chat_view._on_guides_toggled(bool(enabled))

    @Slot(bool)
    def setFadeConnections(self, enabled: bool):
        self._chat_view._on_fade_connections_toggled(bool(enabled))

    @Slot(int)
    def resize(self, height: int):
        bounded = max(GRID_CONTROL_MIN_HEIGHT, min(GRID_CONTROL_MAX_HEIGHT, int(height)))
        if bounded == self._last_height:
            return
        self._last_height = bounded
        self.heightRequested.emit(bounded)
