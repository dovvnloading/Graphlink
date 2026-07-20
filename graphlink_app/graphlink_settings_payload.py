"""The settings island's outbound wire contract, as typed Python dataclasses.

THIS IS A WIRE FORMAT, NOT A DOMAIN MODEL - see graphlink_composer_payload.py
for the fuller rationale. Grown incrementally, one page at a time, per the
recorded Phase 3 increment sequence in
doc/FRONTEND_WEB_MIGRATION_MASTER_PLAN.md: increment 2 shipped
activeSection alone (shell/navigation); increment 3 adds the
General/Appearance page's real fields below. Each remaining page's own
fields land in its own later increment rather than being stubbed
speculatively here.

Field names are camelCase to match the JSON keys
SettingsBridge._build_state_payload() emits and
web_ui/src/lib/bridge-core/generated/settings-state.ts mirrors.

Cross-checked against a live SettingsBridge snapshot by
tests/test_settings_payload_schema.py.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SettingsStatePayload:
    """The complete published snapshot, including the envelope fields
    IslandBridge.publish() adds to every island's payload."""

    schemaVersion: int
    revision: int
    activeSection: str

    # General/Appearance page (increment 3) - mirrors
    # AppearanceSettingsWidget's fields exactly, minus the two that need a
    # real window callback (Check for Updates / Open Repository), deferred
    # to increment 8 alongside the rest of the duck-typed-callback wiring.
    theme: str
    showTokenCounter: bool
    enableSystemPrompt: bool
    notificationPreferences: dict[str, bool]
    updateNotificationsEnabled: bool
    updateStatusMessage: str
    updateStatusLevel: str
    updateLastCheckedAt: str
    updateAvailable: bool

    # See ComposerStatePayload's identical field for the full negotiation
    # rationale; optional for the same reason (models a sender predating this
    # field, not today's - IslandBridge.publish() always emits it).
    minCompatibleSchemaVersion: int | None = None
