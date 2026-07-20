"""Desktop-side state bridge for the about-dialog island.

Phase 4 increment 1 - the simplest surface in this migration: a pure,
static credits/links display with zero live app state (every payload
field is a build-time constant) and exactly one intent, openExternal(url),
replacing the legacy AboutDialog's 3 unparameterized webbrowser.open(url)
call sites (repo/personal-website/personal-github) with one generic
parameterized Slot.

Modal -> non-modal, matching this migration's own established, universal
convention (every prior island, including the formerly-modal
CommandPaletteDialog, is non-modal) - safe here specifically because the
legacy AboutDialog(self).exec()'s return value was discarded by its only
caller (graphlink_window.py's show_about_dialog), so nothing depended on
blocking/modal semantics. Cached once in ChatWindow.__init__ and reused,
unlike the legacy dialog's construct-fresh-with-WA_DeleteOnClose-every-call
lifecycle - see graphlink_about_web.py's own module docstring for the
closeEvent hide-not-teardown fix this requires, applied here from the
first implementation rather than found by a drive afterward, unlike
SettingsWebHost's own history with this exact bug class.
"""

from __future__ import annotations

import webbrowser
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from graphlink_island_bridge import IslandBridge
from graphlink_update import APP_VERSION

APP_NAME = "Graphlink"
REPOSITORY_URL = "https://github.com/dovvnloading/Graphlink"
DEVELOPER_NAME = "Matthew Robert Wesney"
DEVELOPER_WEBSITE_URL = "https://mattwesney.com"
DEVELOPER_GITHUB_URL = "https://github.com/dovvnloading"
COPYRIGHT_TEXT = "© 2026"


class AboutBridge(IslandBridge, QObject):
    stateChanged = Signal(str)

    def __init__(self, parent=None):
        QObject.__init__(self, parent)
        IslandBridge.__init__(self)

    def _transport_send(self, payload_json: str) -> None:
        self.stateChanged.emit(payload_json)

    def _build_state_payload(self) -> dict[str, Any]:
        return {
            "appName": APP_NAME,
            "appVersion": APP_VERSION,
            "repositoryUrl": REPOSITORY_URL,
            "developerName": DEVELOPER_NAME,
            "developerWebsiteUrl": DEVELOPER_WEBSITE_URL,
            "developerGithubUrl": DEVELOPER_GITHUB_URL,
            "copyrightText": COPYRIGHT_TEXT,
        }

    @Slot()
    def ready(self):
        self.publish()

    @Slot()
    def close(self):
        """Lets the in-DOM Close button (and Escape key) trigger the same
        close() the toolbar's About-button toggle already calls
        (ChatWindow.show_about_dialog) - WebIslandHost.setParent(self) in
        __init__ means self.parent() is the AboutWebHost itself, whose own
        closeEvent hides rather than tearing down (see graphlink_about_web.py).
        A no-op if this bridge is ever used detached from a real host
        (every test constructs it that way)."""
        parent = self.parent()
        if parent is not None and hasattr(parent, "close"):
            parent.close()

    @Slot(str)
    def openExternal(self, url: str):
        """Write-only, fire-and-forget - matches the legacy dialog's own
        unguarded webbrowser.open(url) call exactly (no try/except, no
        checking the boolean return) and the existing openRepository()
        precedent in graphlink_settings_bridge.py. Only Python-authored
        payload values (repositoryUrl/developerWebsiteUrl/developerGithubUrl)
        ever populate the 3 buttons that call this in practice, so no
        allow-list is added here, matching that same precedent."""
        webbrowser.open(url)
