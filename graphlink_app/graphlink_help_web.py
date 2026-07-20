"""Web host for the help-dialog island - Phase 4 increment 2.

Mirrors AboutWebHost's shape almost exactly (frameless Qt.WindowType.Tool
top-level QFrame, cached once in ChatWindow.__init__, hide-not-teardown
closeEvent from day one), but positions itself via show_for_anchor()
(anchor-relative, screen-clamped) rather than centering over the parent -
matching the legacy HelpDialog's own Qt.WindowType.Popup positioning
exactly, copied verbatim from show_for_anchor() (graphlink_ui_dialogs/
graphlink_system_dialogs.py) for the same reason SettingsWebHost's did:
switching renderers must not also move the panel.

closeEvent hides rather than tearing down, from this class's FIRST
implementation - not discovered by a drive afterward the way
SettingsWebHost's identical bug was. See graphlink_about_web.py's own
module docstring for the fuller rationale, identical here.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame

from graphlink_help_bridge import HelpBridge
from graphlink_web_island_host import WebIslandHost

HELP_UNAVAILABLE_MESSAGE = (
    "Help is unavailable because QtWebEngine failed to initialize."
)

HELP_WIDTH = 900
HELP_HEIGHT = 620


class HelpWebHost(WebIslandHost):
    def __init__(self, parent=None):
        bridge = HelpBridge()
        super().__init__(
            bridge=bridge,
            asset_dir_name="help",
            bridge_object_name="helpBridge",
            unavailable_message=HELP_UNAVAILABLE_MESSAGE,
            parent=parent,
        )
        self.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
        )
        self.resize(HELP_WIDTH, HELP_HEIGHT)

    def show_for_anchor(self, anchor_widget) -> None:
        self.resize(HELP_WIDTH, HELP_HEIGHT)

        target_global = anchor_widget.mapToGlobal(
            QPoint(anchor_widget.width() - self.width(), anchor_widget.height() + 6)
        )
        screen = QGuiApplication.screenAt(target_global) or QGuiApplication.primaryScreen()
        available_geometry = screen.availableGeometry() if screen else None

        x = target_global.x()
        y = target_global.y()

        if available_geometry is not None:
            max_x = available_geometry.right() - self.width() - 12
            max_y = available_geometry.bottom() - self.height() - 12
            x = max(available_geometry.left() + 12, min(x, max_x))
            y = max(available_geometry.top() + 12, min(y, max_y))

        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        # See module docstring: hide, don't tear down - real teardown
        # still happens via the shutdown registry at app exit.
        QFrame.closeEvent(self, event)
