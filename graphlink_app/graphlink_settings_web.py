"""Web host for the settings island - Phase 3 increment 8, the first real
wiring of any settings page into the actual running app.

Unlike every other already-wired island, SettingsWebHost fully replaces
SettingsDialog's own Qt-side rail/header chrome, not just its content
pages - the React app already renders its own rail navigation
(SECTION_NAMES) inside the single QWebEngineView, so there is nothing left
for a Qt-side QStackedWidget/rail to do. This host is a direct drop-in
replacement, matching SettingsDialog's own public shape exactly
(show_for_anchor/set_current_section_by_mode/isVisible/close) so
ChatWindow.show_settings() can hold either one behind the same variable,
selected once per session by resolve_renderer_flag("settings", ...).

Positioning math in show_for_anchor() is copied verbatim from
SettingsDialog.show_for_anchor() (graphlink_ui_dialogs/
graphlink_settings_dialogs.py) - same anchor-relative target point, same
screen-clamping, same fixed 820x560 size - so switching the flag doesn't
also change where the panel appears.

The close-guard (_iter_running_workers/closeEvent) is a from-scratch port
against this bridge's own worker references, not SettingsDialog's tab-
widget attributes (self.ollama_tab.scan_worker no longer exists - all four
workers are bridge-owned now). This also fixes a real pre-existing bug the
legacy dialog's own close guard had: it never included ApiModelLoadWorker
in its 3-of-4 tracking list (recorded in this phase's own scope note,
found during the increment-5 worker-registry extraction) - the port here
checks all 4.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMessageBox

from graphlink_settings_bridge import SECTION_NAMES, SettingsBridge
from graphlink_web_island_host import WebIslandHost

SETTINGS_UNAVAILABLE_MESSAGE = (
    "Settings are unavailable because QtWebEngine failed to initialize."
)

SETTINGS_WIDTH = 820
SETTINGS_HEIGHT = 560


class SettingsWebHost(WebIslandHost):
    def __init__(self, settings_manager, main_window=None, parent=None):
        bridge = SettingsBridge(settings_manager, main_window=main_window, parent=None)
        super().__init__(
            bridge=bridge,
            asset_dir_name="settings",
            bridge_object_name="settingsBridge",
            unavailable_message=SETTINGS_UNAVAILABLE_MESSAGE,
            parent=parent,
        )
        # A persistent tool window, not a transient popup - same rationale
        # as SettingsDialog's own comment: stays open during scans, message
        # boxes, and incidental outside clicks.
        self.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
        )
        self.resize(SETTINGS_WIDTH, SETTINGS_HEIGHT)

    def set_current_section_by_mode(self, mode_text: str) -> None:
        """Mirrors SettingsDialog.set_current_section_by_mode() - deep-links
        the settings panel to whichever section corresponds to the app's
        current provider mode, falling back to General for anything else.
        Reuses set_active_section(), already added in increment 2 for
        exactly this future call site."""
        section = mode_text if mode_text in SECTION_NAMES else "General"
        self.bridge.set_active_section(section)

    def show_for_anchor(self, anchor_widget) -> None:
        self.resize(SETTINGS_WIDTH, SETTINGS_HEIGHT)

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

    def _iter_running_workers(self):
        bridge = self.bridge
        for label, worker in (
            ("API model catalog load", bridge._api_worker),
            ("Ollama model scan", bridge._ollama_scan_worker),
            ("Ollama model pull", bridge._ollama_pull_worker),
            ("Llama.cpp model scan", bridge._llama_scan_worker),
        ):
            if worker is not None and worker.isRunning():
                yield label, worker

    def _request_worker_shutdown(self, worker):
        for method_name in ("cancel", "stop"):
            method = getattr(worker, method_name, None)
            if callable(method):
                try:
                    method()
                except Exception:
                    pass
                return

        request_interruption = getattr(worker, "requestInterruption", None)
        if callable(request_interruption):
            try:
                request_interruption()
            except Exception:
                pass

    def closeEvent(self, event):
        still_running = []
        for label, worker in self._iter_running_workers():
            self._request_worker_shutdown(worker)
            if not worker.wait(3000):
                still_running.append(label)

        if still_running:
            worker_list = "\n".join(f"- {label}" for label in still_running)
            QMessageBox.information(
                self,
                "Background Work Still Running",
                "Please wait for these settings tasks to finish before closing:\n\n"
                f"{worker_list}",
            )
            event.ignore()
            return

        super().closeEvent(event)
