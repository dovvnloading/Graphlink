"""Web host for the settings island - the app's ONLY settings surface since
Phase 3 increment 10 deleted the legacy Qt SettingsDialog stack (recoverable
at the `legacy-settings-final` git tag).

Unlike every other island, SettingsWebHost replaced the legacy dialog's
entire Qt-side rail/header chrome, not just its content pages - the React
app renders its own rail navigation (SECTION_NAMES) inside the single
QWebEngineView, so there is nothing for a Qt-side QStackedWidget/rail to
do. Its public shape (show_for_anchor/set_current_section_by_mode/
isVisible/close) deliberately matches the deleted SettingsDialog's, which
is what let ChatWindow.show_settings() hold either behind one variable
during the increments-8/9 flag-gated coexistence window.

Positioning math in show_for_anchor() was copied verbatim from the legacy
SettingsDialog.show_for_anchor() - same anchor-relative target point, same
screen-clamping, same fixed 820x560 size - so the renderer swap never moved
the panel.

The close-guard (_iter_running_workers/closeEvent) is a port against this
bridge's own worker references, not the legacy tab-widget attributes. It
also fixed a real pre-existing bug the legacy dialog's close guard had: it
never included ApiModelLoadWorker in its 3-of-4 tracking list (recorded in
this phase's scope note, found during the increment-5 worker-registry
extraction) - this guard checks all 4.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QMessageBox

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
        # UI-refactor P1 (audit B4/B5): no longer a top-level Tool window
        # positioned/clamped against the SCREEN - that let it hang past the
        # main window's edge onto the desktop, z-order under other dialogs,
        # and ship without any close affordance. It is now a plain child
        # widget embedded in a DialogFrame (title + close button) that
        # OverlayManager centers, clamps INSIDE the window, and scrims.
        # The legacy "stays open during scans" rationale is preserved by
        # the manager's dialog policy: dialogs ignore incidental outside
        # clicks (only scrim click / close button / Escape dismiss).
        self.resize(SETTINGS_WIDTH, SETTINGS_HEIGHT)

    def set_current_section_by_mode(self, mode_text: str) -> None:
        """Mirrors the legacy SettingsDialog's set_current_section_by_mode()
        - deep-links the settings panel to whichever section corresponds to
        the app's current provider mode, falling back to General for
        anything else. Reuses set_active_section(), added in increment 2 for
        exactly this call site."""
        section = mode_text if mode_text in SECTION_NAMES else "General"
        self.bridge.set_active_section(section)

    # P1: show_for_anchor (screen-coordinate positioning) deleted - the host
    # is embedded in a DialogFrame that OverlayManager centers and clamps
    # inside the main window. Positioning is no longer this class's job.

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

        # Deliberately SKIP WebIslandHost.closeEvent, which treats close as
        # app teardown (prepare_for_shutdown: dispose the bridge, stop the
        # web view, unregister). Correct for every other island - they are
        # permanent child widgets whose closeEvent only fires when the app
        # is going down - but this host is the one closable, REOPENABLE
        # top-level window: the user toggles it shut with the Settings
        # button and expects the next click to bring it back alive. Close
        # here means hide (exactly what the legacy dialog's closeEvent did
        # after its own worker guard); real teardown still happens via the
        # shutdown registry (aboutToQuit -> shutdown_all ->
        # prepare_for_shutdown) at app exit. Found by increment 10's drive:
        # routing through the base closeEvent left a disposed bridge and a
        # dead page behind every reopen - a real bug reachable since the
        # increment-9 default flip, masked earlier because no prior drive
        # asserted an EMISSION after a close/reopen cycle.
        QFrame.closeEvent(self, event)
