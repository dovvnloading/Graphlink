"""The pin-overlay island's outbound wire contract, as typed Python
dataclasses.

THIS IS A WIRE FORMAT, NOT A DOMAIN MODEL - see graphlink_composer_payload.py
for the fuller rationale. Filtering is pure client-side (matching
ChatLibraryDialog's own established pattern for its search box) - Python
always sends the FULL row list; React filters on title/note locally, exactly
as NavigationPinsFilterModel already did. Pin creation/editing still opens
the legacy NavigationPinEditor modal in this increment (Phase 5 increment 2
replaces that with an async draft-intent flow) - this payload has no
draft-state fields yet.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PinRow:
    id: str
    title: str
    note: str


@dataclass
class PinOverlayStatePayload:
    """The complete published snapshot, including the envelope fields
    IslandBridge.publish() adds to every island's payload."""

    schemaVersion: int
    revision: int
    rows: list[PinRow]
    # The pin currently selected on the canvas (via NavigationPinsController.
    # focus or a scene click), mirrored here so the list can highlight the
    # matching row - satisfies the phase's "pin parity incl. selection sync"
    # exit criterion. None when nothing is selected.
    selectedPinId: str | None = None
    # See ComposerStatePayload's identical field for the full negotiation
    # rationale; optional for the same reason (models a sender predating this
    # field, not today's - IslandBridge.publish() always emits it).
    minCompatibleSchemaVersion: int | None = None
