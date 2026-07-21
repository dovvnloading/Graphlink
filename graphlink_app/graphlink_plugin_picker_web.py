"""Web host for the plugin-picker island (Phase 6 increment 3) - absorbs
PluginFlyoutPanel (graphlink_plugins/graphlink_plugin_picker.py, deleted
this increment).

A plain embedded child QFrame (no Window flag), matching every Phase 5/6
host so far - the legacy panel's own Qt.WindowType.Popup floating-window
shape doesn't survive the migration. Outside-click-close (the legacy
popup's own native Qt.Popup dismiss behavior) is reimplemented via
WebIslandHost's dismiss_on_outside_focus option - the exact mechanism Phase
5 increment 3 already built and proved for the composer's own pickers, reused
here unchanged rather than re-invented.

Positioning matches PluginFlyoutPanel.show_for_anchor()'s own math exactly:
anchor the popup's top-left just below the anchor's bottom-left corner (4px
gap), then clamp into the screen's available geometry with a 12px margin -
a simpler, self-contained shape than composer_picker_position() (which
anchors relative to the composer specifically), so it gets its own small
positioning method rather than a shared helper only this one host would use.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint
from PySide6.QtGui import QGuiApplication

from graphlink_plugin_picker_bridge import (
    PLUGIN_PICKER_MAX_HEIGHT,
    PLUGIN_PICKER_MIN_HEIGHT,
    PluginPickerBridge,
)
from graphlink_web_island_host import WebIslandHost

PLUGIN_PICKER_UNAVAILABLE_MESSAGE = (
    "The plugin picker is unavailable because QtWebEngine failed to initialize."
)

PLUGIN_PICKER_WIDTH = 520


class PluginPickerHost(WebIslandHost):
    def __init__(self, plugin_portal, parent=None):
        bridge = PluginPickerBridge(plugin_portal)
        super().__init__(
            bridge=bridge,
            asset_dir_name="plugin-picker",
            bridge_object_name="pluginPickerBridge",
            min_height=PLUGIN_PICKER_MIN_HEIGHT,
            max_height=PLUGIN_PICKER_MAX_HEIGHT,
            unavailable_message=PLUGIN_PICKER_UNAVAILABLE_MESSAGE,
            dismiss_on_outside_focus=True,
            parent=parent,
        )
        self.setFixedWidth(PLUGIN_PICKER_WIDTH)
        self.bridge.heightRequested.connect(self.apply_requested_height)
        self.setVisible(False)

    def reposition(self, anchor) -> None:
        if anchor is None or self.parentWidget() is None:
            return
        target_global = anchor.mapToGlobal(QPoint(0, anchor.height() + 4))
        screen = QGuiApplication.screenAt(target_global) or QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None

        x, y = target_global.x(), target_global.y()
        if available is not None:
            margin = 12
            max_x = available.right() - self.width() - margin
            max_y = available.bottom() - self.height() - margin
            x = max(available.left() + margin, min(x, max_x))
            y = max(available.top() + margin, min(y, max_y))

        self.move(self.parentWidget().mapFromGlobal(QPoint(x, y)))
