"""Desktop-side state bridge for the toolbar island (Phase 6 increment 1).

Every intent Slot is a pure pass-through to the SAME `ChatWindow` method the
old native button already called - zero behavior changes, a faithful port
matching every prior island's own "port the widget, defer the behavior
upgrade" precedent (e.g. PinOverlay's own increment 1/2 split). The mode
combo ships unchanged too: `selectMode()` still calls the exact same
`on_mode_changed` body (only its calling convention changes, from a
QComboBox index to a plain mode-text string, since there is no more
QComboBox to call `.itemText(index)` on) - the request/confirm/reject
protocol upgrade is deliberately deferred to increment 2.

`AnchorRect` is the one genuinely new mechanism this increment introduces: a
plain, duck-typed stand-in for a native `QToolButton` anchor reference, built
from a DOM-reported rect (`reportAnchorRect`, called from a
`ResizeObserver`/`getBoundingClientRect()` effect on each of the 4 real
anchor points - Pins/Plugins/Settings/Help) instead of a real Qt widget. It
implements exactly the subset of `QWidget`'s API `show_for_anchor()`/
`reposition()` already call on their `anchor_widget` argument (`mapToGlobal`/
`mapTo`/`width`/`height`/`size`) - none of those methods change at all; only
what gets passed in as the anchor changes.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QPoint, QSize, Signal, Slot

import graphlink_config as config
from graphlink_island_bridge import IslandBridge

MODE_OPTIONS = [config.MODE_OLLAMA_LOCAL, config.MODE_LLAMACPP_LOCAL, config.MODE_API_ENDPOINT]


class AnchorRect:
    def __init__(self, global_pos: QPoint, size: QSize):
        self._global_pos = global_pos
        self._size = size

    def mapToGlobal(self, point: QPoint) -> QPoint:
        return QPoint(self._global_pos.x() + point.x(), self._global_pos.y() + point.y())

    def mapTo(self, target, point: QPoint) -> QPoint:
        return target.mapFromGlobal(self.mapToGlobal(point))

    def width(self) -> int:
        return self._size.width()

    def height(self) -> int:
        return self._size.height()

    def size(self) -> QSize:
        return self._size


class ToolbarBridge(IslandBridge, QObject):
    stateChanged = Signal(str)

    def __init__(self, window, parent=None):
        QObject.__init__(self, parent)
        IslandBridge.__init__(self)
        self.window = window
        self._anchors: dict[str, AnchorRect] = {}

    def _transport_send(self, payload_json: str) -> None:
        self.stateChanged.emit(payload_json)

    def get_anchor(self, name: str):
        """Returns the last-reported AnchorRect for `name`, or the window
        itself as a safe duck-typed fallback (matching show_help()'s own
        pre-existing `self.help_btn if hasattr(...) else self` shape) if the
        toolbar hasn't reported it yet - e.g. the very first frame, before
        any ResizeObserver callback has fired."""
        return self._anchors.get(name, self.window)

    def _build_state_payload(self) -> dict[str, Any]:
        return {
            "pinsChecked": self.window.pin_overlay.isVisible(),
            "modeOptions": MODE_OPTIONS,
            "currentMode": self.window.settings_manager.get_current_mode(),
        }

    @Slot()
    def ready(self):
        self.publish()

    @Slot(str, int, int, int, int)
    def reportAnchorRect(self, name: str, x: int, y: int, width: int, height: int):
        toolbar_host = self.parent()
        if toolbar_host is None:
            return
        global_pos = toolbar_host.mapToGlobal(QPoint(int(x), int(y)))
        self._anchors[str(name)] = AnchorRect(global_pos, QSize(int(width), int(height)))

    @Slot()
    def openLibrary(self):
        self.window.show_library()

    @Slot()
    def saveChat(self):
        self.window.save_chat()

    @Slot()
    def togglePins(self):
        self.window.toggle_pin_overlay()
        self.publish()

    @Slot()
    def organizeNodes(self):
        self.window.chat_view.scene().organize_nodes()

    @Slot()
    def zoomIn(self):
        self.window.chat_view.zoom_by(1.1)

    @Slot()
    def zoomOut(self):
        self.window.chat_view.zoom_by(0.9)

    @Slot()
    def resetZoom(self):
        self.window.chat_view.reset_zoom()

    @Slot()
    def fitAll(self):
        self.window.chat_view.fit_all()

    @Slot(bool)
    def toggleControls(self, visible: bool):
        self.window.chat_view.toggle_overlays_visibility(bool(visible))

    @Slot()
    def togglePlugins(self):
        self.window._toggle_plugin_picker()

    @Slot(str)
    def selectMode(self, mode_text: str):
        self.window.on_mode_changed(mode_text)

    @Slot()
    def openSettings(self):
        self.window.show_settings()

    @Slot()
    def openAbout(self):
        self.window.show_about_dialog()

    @Slot()
    def openHelp(self):
        self.window.show_help()
