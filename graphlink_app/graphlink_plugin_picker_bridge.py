"""Desktop-side state bridge for the plugin-picker island (Phase 6 increment
3) - absorbs PluginFlyoutPanel (native Qt.Tool popup, deleted this
increment).

Wraps the SAME PluginPortal.get_plugin_categories()/execute_plugin() every
path already used - this bridge only reformats get_plugin_categories()'s
dict shape into the wire contract (stripping the non-serializable
`callback`), exactly as PluginFlyoutPanel._build_category_buttons()/
set_current_category() used to read it directly.

Categories are static app-lifetime data (registered once at startup, no
subscription/mutation mechanism exists for them) - unlike PinOverlayBridge's
own store-subscription republish, this bridge only ever needs to publish
once, on ready(). There is deliberately no open()-style method the window
calls before showing the host (unlike ComposerPickerHost/
ComposerContextHost, which snapshot per-open state) - nothing about plugin
categories changes between opens.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from graphlink_island_bridge import IslandBridge

PLUGIN_PICKER_MIN_HEIGHT = 220
PLUGIN_PICKER_MAX_HEIGHT = 420


class PluginPickerBridge(IslandBridge, QObject):
    stateChanged = Signal(str)
    heightRequested = Signal(int)  # Qt-only side channel; see PinOverlayBridge's identical field

    def __init__(self, plugin_portal, parent=None):
        QObject.__init__(self, parent)
        IslandBridge.__init__(self)
        self._plugin_portal = plugin_portal
        self._last_height = 0

    def _transport_send(self, payload_json: str) -> None:
        self.stateChanged.emit(payload_json)

    def _build_state_payload(self) -> dict[str, Any]:
        categories = self._plugin_portal.get_plugin_categories() or []
        return {
            "categories": [
                {
                    "name": str(category.get("name") or ""),
                    "description": str(category.get("description") or ""),
                    "plugins": [
                        {
                            "name": str(plugin.get("name") or ""),
                            "description": str(plugin.get("description") or ""),
                        }
                        for plugin in (category.get("plugins") or [])
                        if isinstance(plugin, dict) and plugin.get("name")
                    ],
                }
                for category in categories
                if isinstance(category, dict) and category.get("name")
            ],
        }

    @Slot()
    def ready(self):
        self.publish()

    @Slot(str)
    def executePlugin(self, plugin_name: str):
        plugin_name = str(plugin_name or "").strip()
        if plugin_name:
            self._plugin_portal.execute_plugin(plugin_name)
        self.close()

    @Slot(int)
    def resize(self, height: int):
        bounded = max(PLUGIN_PICKER_MIN_HEIGHT, min(PLUGIN_PICKER_MAX_HEIGHT, int(height)))
        if bounded == self._last_height:
            return
        self._last_height = bounded
        self.heightRequested.emit(bounded)

    @Slot()
    def close(self):
        parent = self.parent()
        if parent is not None and hasattr(parent, "setVisible"):
            parent.setVisible(False)
