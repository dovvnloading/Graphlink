"""The pin-overlay island's outbound wire contract, as typed Python
dataclasses.

THIS IS A WIRE FORMAT, NOT A DOMAIN MODEL - see graphlink_composer_payload.py
for the fuller rationale. Filtering is pure client-side (matching
ChatLibraryDialog's own established pattern for its search box) - Python
always sends the FULL row list; React filters on title/note locally, exactly
as NavigationPinsFilterModel already did.

Phase 5 increment 2: `draft` carries the async draft-in-progress state
(NavigationPinsController.begin_draft_pin()/commit_draft()/discard_draft() -
see that module's own docstrings for the full rationale replacing the
legacy NavigationPinEditor.exec() modal). The DRAFT'S EDITED VALUES (what the
user is currently typing) are deliberately NOT part of this payload - they
are pure client-side React state, only sent back once via commitDraft(title,
note) when the user saves. `draft` only carries the STARTING values (the
pin's current title/note when the draft began) so the editor view can
prefill itself, plus `isNew` so the editor can show "Add" vs "Edit" copy if
it wants to. `error` is a transient, recoverable validation-failure message
(mirroring ChatLibraryStatePayload's own `notice` field) - defense in depth
only, since PinOverlayBridge's commitDraft already validates client-side
first; real users should rarely see it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PinRow:
    id: str
    title: str
    note: str


@dataclass
class PinDraft:
    pinId: str
    title: str
    note: str
    isNew: bool


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
    # None when no create/edit is in progress - see this module's own
    # docstring for the full async-draft rationale.
    draft: PinDraft | None = None
    # A recoverable commitDraft() validation failure, or None. Transient -
    # cleared on the next successful action, never persisted.
    error: str | None = None
    # See ComposerStatePayload's identical field for the full negotiation
    # rationale; optional for the same reason (models a sender predating this
    # field, not today's - IslandBridge.publish() always emits it).
    minCompatibleSchemaVersion: int | None = None
