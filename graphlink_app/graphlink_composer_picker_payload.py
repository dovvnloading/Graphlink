"""The composer-picker island's outbound wire contract (Phase 5 increment 3).

Absorbs graphlink_composer_popups.py's ComposerPickerPopup (deleted this
increment) - one shared surface for BOTH the model picker and the
reasoning-level picker, exactly like the native popup it replaces (a `kind`
switch, not two surfaces). Filtering is pure client-side (matching every
prior list-bearing island's own established precedent) - Python always sends
the full option list; React filters locally by label/id as the user types,
then resets that local filter whenever a fresh open() begins (see the
island's App.tsx for the openToken-keyed reset, the same "reset local state
when a fresh X begins" pattern PinOverlay's own draft editor already uses).

THIS IS A WIRE FORMAT, NOT A DOMAIN MODEL - see graphlink_composer_payload.py
for the fuller rationale. `meta`/`current`/`unavailable` are precomputed
server-side exactly as ComposerPickerPopup._refresh_options() computed them
(status text, active-id cross-reference, ready/available gating) - genuine
business logic, not something worth re-deriving in two languages. Whether the
"Open Settings to discover models" hint should show is NOT sent here - it
depends on the client-side-only search query (empty options AND no query),
so React derives it itself from `options`/its own local query state.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ComposerPickerOption:
    id: str
    label: str
    meta: str
    current: bool
    unavailable: bool


@dataclass
class ComposerPickerStatePayload:
    """The complete published snapshot, including the envelope fields
    IslandBridge.publish() adds to every island's payload."""

    schemaVersion: int
    revision: int
    kind: str  # "model" | "reasoning"
    title: str
    options: list[ComposerPickerOption]
    # Bumped once per open() call (not on every publish) - lets React detect
    # "this is a genuinely fresh open," distinct from a republish triggered by
    # e.g. a theme change, and reset its own local search-query state
    # accordingly.
    openToken: int = 0
    # See ComposerStatePayload's identical field for the full negotiation
    # rationale; optional for the same reason (models a sender predating this
    # field, not today's - IslandBridge.publish() always emits it).
    minCompatibleSchemaVersion: int | None = None
