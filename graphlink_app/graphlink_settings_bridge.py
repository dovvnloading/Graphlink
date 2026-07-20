"""Desktop-side state bridge for the settings island.

Grown one page at a time per the recorded Phase 3 increment sequence in
doc/FRONTEND_WEB_MIGRATION_MASTER_PLAN.md. Increment 2 shipped
activeSection navigation alone; increment 3 (this) adds the
General/Appearance page - the only page with no secrets and no background
workers, so it's the first to get real, persisted fields.

Each field-level intent (setTheme/setShowTokenCounter/etc.) applies and
publishes immediately, one field at a time - a deliberate departure from
AppearanceSettingsWidget's single batched "Apply" button (see the Phase 3
session log for the reasoning): a persistent settings panel with instant
feedback fits a live snapshot-driven bridge better than a modal-style
commit step, and every existing bridge intent in this codebase (composer,
notification, command-palette) is already shaped as one small, immediately
effective call per concern, never a multi-field batch.

Two of the original page's behaviors are deliberately NOT here yet: the
Check-for-Updates button (needs to start UpdateCheckWorker, a QThread
ChatWindow currently owns) and Open Repository (QDesktopServices), plus
main_window.on_settings_changed()'s side effects (token-counter visibility,
overlay repositioning, agent reinitialization). All three need a real
window reference this bridge doesn't have until increment 8 wires it into
the real SettingsDialog/ChatWindow - see that increment's own scope note
for the deferred checklist item ("duck-typed callbacks replaced by
explicit bridge wiring").
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QApplication

import graphlink_config as config
from graphlink_island_bridge import IslandBridge
from graphlink_licensing import SettingsManager
from graphlink_styles import THEMES

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

    def __init__(self, settings_manager: SettingsManager, parent=None):
        QObject.__init__(self, parent)
        IslandBridge.__init__(self)
        self.settings_manager = settings_manager
        self._active_section = SECTION_NAMES[0]

    def _transport_send(self, payload_json: str) -> None:
        self.stateChanged.emit(payload_json)

    def _build_state_payload(self) -> dict[str, Any]:
        sm = self.settings_manager
        return {
            "activeSection": self._active_section,
            "theme": sm.get_theme(),
            "showTokenCounter": sm.get_show_token_counter(),
            "enableSystemPrompt": sm.get_enable_system_prompt(),
            "notificationPreferences": sm.get_notification_preferences(),
            "updateNotificationsEnabled": sm.get_update_notifications_enabled(),
            "updateStatusMessage": sm.get_update_status_message(),
            "updateStatusLevel": sm.get_update_status_level(),
            "updateLastCheckedAt": sm.get_update_last_checked_at(),
            "updateAvailable": sm.get_update_available(),
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

    @Slot(str)
    def setTheme(self, theme_name: str):
        """Persist the theme, restyle the running app, and publish this
        island's own updated snapshot. apply_theme()'s theme_changed_all()
        call (Phase 3 increment 2) separately republishes every OTHER
        registered island host - but only once this bridge itself is
        wrapped in a registered WebIslandHost (increment 8); calling
        publish() directly here keeps this bridge's own state correct
        regardless of that wiring, and is harmless once it's also reached a
        second time via theme_changed_all(). Unrecognized theme names are
        ignored rather than raised, same boundary-tolerance convention as
        setActiveSection."""
        if theme_name not in THEMES:
            return
        self.settings_manager.set_theme(theme_name)
        config.apply_theme(QApplication.instance(), theme_name)
        self.publish()

    @Slot(bool)
    def setShowTokenCounter(self, enabled: bool):
        self.settings_manager.set_show_token_counter(enabled)
        self.publish()

    @Slot(bool)
    def setEnableSystemPrompt(self, enabled: bool):
        self.settings_manager.set_enable_system_prompt(enabled)
        self.publish()

    @Slot(str, bool)
    def setNotificationPreference(self, notification_type: str, enabled: bool):
        if notification_type not in SettingsManager.NOTIFICATION_TYPES:
            return
        self.settings_manager.set_notification_preferences({notification_type: enabled})
        self.publish()

    @Slot(bool)
    def setUpdateNotificationsEnabled(self, enabled: bool):
        self.settings_manager.set_update_notifications_enabled(enabled)
        self.publish()
