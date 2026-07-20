"""Desktop-side state bridge for the help-dialog island.

Phase 4 increment 2 - the migration's second-simplest surface after About:
zero live app state (content is 100% static reference copy, moved entirely
to web_ui/src/islands/help/data/sections.ts) and no intents beyond the same
close() pattern About introduced. Section navigation (which of the 9
sections is showing) is pure client-side React state - Python never needs
to know which section is open, so there is no setActiveSection Slot here,
unlike the settings island's identical-looking rail.

Non-modal, matching HelpDialog's own already-non-modal Qt.WindowType.Popup
shape - not a modal-to-non-modal conversion the way About was. Cached once
in ChatWindow.__init__ and toggled, exactly like the legacy dialog already
did (self.help_panel, never destroyed). See graphlink_help_web.py's module
docstring for the closeEvent hide-not-teardown fix this requires, applied
here from the first implementation per the same precedent About and
Settings both established.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from graphlink_island_bridge import IslandBridge


class HelpBridge(IslandBridge, QObject):
    stateChanged = Signal(str)

    def __init__(self, parent=None):
        QObject.__init__(self, parent)
        IslandBridge.__init__(self)

    def _transport_send(self, payload_json: str) -> None:
        self.stateChanged.emit(payload_json)

    def _build_state_payload(self) -> dict[str, Any]:
        return {}

    @Slot()
    def ready(self):
        self.publish()

    @Slot()
    def close(self):
        """Lets the in-DOM Close button (and Escape key) trigger the same
        close() the toolbar's Help-button toggle already calls
        (ChatWindow.show_help) - see AboutBridge.close()'s identical
        docstring for the self.parent() mechanism this relies on."""
        parent = self.parent()
        if parent is not None and hasattr(parent, "close"):
            parent.close()
