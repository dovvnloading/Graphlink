"""Desktop-side state bridge for the settings island.

Phase 3 increment 2 (per the recorded scope note on the Phase 3 checklist
item in doc/FRONTEND_WEB_MIGRATION_MASTER_PLAN.md): shell-only state and
navigation, proven against a real bridge before any page grows real fields.
setActiveSection is the one intent, the same section-name vocabulary
SettingsDialog.set_current_section_by_mode already uses
(graphlink_config.MODE_OLLAMA_LOCAL etc.), so a later increment's
deep-linking wiring has nothing to re-derive.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

import graphlink_config as config
from graphlink_island_bridge import IslandBridge

# The 5 settings sections, in rail order - identical vocabulary to
# SettingsDialog.SECTION_DEFS/set_current_section_by_mode so this bridge and
# the eventual native shell never need two names for the same section.
SECTION_NAMES = (
    "General",
    config.MODE_OLLAMA_LOCAL,
    config.MODE_LLAMACPP_LOCAL,
    config.MODE_API_ENDPOINT,
    "Integrations",
)


class SettingsBridge(IslandBridge, QObject):
    stateChanged = Signal(str)

    def __init__(self, parent=None):
        QObject.__init__(self, parent)
        IslandBridge.__init__(self)
        self._active_section = SECTION_NAMES[0]

    def _transport_send(self, payload_json: str) -> None:
        self.stateChanged.emit(payload_json)

    def _build_state_payload(self) -> dict[str, Any]:
        return {
            "activeSection": self._active_section,
        }

    @Slot()
    def ready(self):
        self.publish()

    @Slot(str)
    def setActiveSection(self, section: str):
        """Navigate the rail. Unrecognized section names are ignored, not
        raised - a boundary intent from JS must never crash the bridge on a
        bad string, matching CommandPaletteBridge.executeCommand's own
        tolerance of a stale/invalid id."""
        if section not in SECTION_NAMES or section == self._active_section:
            return
        self._active_section = section
        self.publish()

    def set_active_section(self, section: str):
        """Python-side equivalent of setActiveSection, for a future native
        shell to deep-link into (mirrors set_current_section_by_mode)."""
        self.setActiveSection(section)
