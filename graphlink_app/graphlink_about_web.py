"""Web host for the about-dialog island - Phase 4 increment 1.

A direct architectural sibling of SettingsWebHost: a frameless, non-modal
Qt.WindowType.Tool top-level QFrame, cached once in ChatWindow.__init__
(self.about_panel) and toggled via show()/close(), not constructed fresh
per open the way the legacy AboutDialog(QDialog) was (WA_DeleteOnClose,
.exec() every call).

closeEvent hides rather than tearing down, from this class's FIRST
implementation - not discovered by a drive afterward the way
SettingsWebHost's identical bug was. WebIslandHost's default closeEvent
treats close as app teardown (prepare_for_shutdown(): the bridge disposed,
the page stopped) - correct for a permanent child-widget island, wrong for
a closable, reopenable top-level window like this one. See
graphlink_settings_web.py's own closeEvent for the identical shape and the
regression it was found fixing there.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame

from graphlink_about_bridge import AboutBridge
from graphlink_web_island_host import WebIslandHost

ABOUT_UNAVAILABLE_MESSAGE = (
    "About is unavailable because QtWebEngine failed to initialize."
)

ABOUT_WIDTH = 420
ABOUT_HEIGHT = 420


class AboutWebHost(WebIslandHost):
    def __init__(self, parent=None):
        bridge = AboutBridge()
        super().__init__(
            bridge=bridge,
            asset_dir_name="about",
            bridge_object_name="aboutBridge",
            unavailable_message=ABOUT_UNAVAILABLE_MESSAGE,
            parent=parent,
        )
        self.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
        )
        self.resize(ABOUT_WIDTH, ABOUT_HEIGHT)

    def show_centered_over_parent(self) -> None:
        """The legacy AboutDialog never explicitly positioned itself (a
        plain QDialog.exec(), left to the window manager's default
        placement) - centering over ChatWindow is a small, deliberate
        improvement for a credits dialog opened from a toolbar button, not
        a preserved legacy behavior."""
        parent = self.parent()
        if parent is not None:
            center = parent.geometry().center()
        else:
            screen = QGuiApplication.primaryScreen()
            center = screen.availableGeometry().center() if screen is not None else None
        if center is not None:
            self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        # See module docstring: hide, don't tear down - real teardown
        # still happens via the shutdown registry at app exit.
        QFrame.closeEvent(self, event)
