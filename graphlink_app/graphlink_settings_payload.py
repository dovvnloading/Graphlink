"""The settings island's outbound wire contract, as typed Python dataclasses.

THIS IS A WIRE FORMAT, NOT A DOMAIN MODEL - see graphlink_composer_payload.py
for the fuller rationale. Shell-only for now (Phase 3 increment 2, per the
recorded scope note on the Phase 3 checklist item in
doc/FRONTEND_WEB_MIGRATION_MASTER_PLAN.md): activeSection is the one real
field, mirroring SettingsDialog.set_current_section_by_mode's deep-linking.
Each page's own fields land in its own later increment rather than being
stubbed speculatively here.

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
    # See ComposerStatePayload's identical field for the full negotiation
    # rationale; optional for the same reason (models a sender predating this
    # field, not today's - IslandBridge.publish() always emits it).
    minCompatibleSchemaVersion: int | None = None
