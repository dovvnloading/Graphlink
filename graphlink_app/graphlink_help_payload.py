"""The help-dialog island's outbound wire contract, as a typed Python
dataclass.

THIS IS A WIRE FORMAT, NOT A DOMAIN MODEL - see graphlink_composer_payload.py
for the fuller rationale. Envelope-only, deliberately: this surface has zero
live/dynamic Python-side state (100% static reference content, moved
entirely to web_ui/src/islands/help/data/sections.ts - see that file's own
header). Which section is currently open is pure client-side React state,
never round-tripped to Python at all. Cross-checked against a live
HelpBridge snapshot by tests/test_help_payload_schema.py.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HelpStatePayload:
    """The complete published snapshot - just the envelope fields
    IslandBridge.publish() adds to every island's payload, since this
    island carries no content of its own."""

    schemaVersion: int
    revision: int
    # See ComposerStatePayload's identical field for the full negotiation
    # rationale; optional for the same reason (models a sender predating this
    # field, not today's - IslandBridge.publish() always emits it).
    minCompatibleSchemaVersion: int | None = None
