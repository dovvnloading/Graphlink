"""The composer-context island's outbound wire contract (Phase 5 increment
3). Absorbs ComposerContextPopup (graphlink_composer_popups.py, deleted this
increment) - context review surface for the React composer's attached
context (a graph-node anchor plus attachment items).

THIS IS A WIRE FORMAT, NOT A DOMAIN MODEL - see graphlink_composer_payload.py
for the fuller rationale. `anchor`/`items`/`totalTokens` are forwarded
verbatim from ComposerBridge._build_state_payload()["context"] - the same
dict reviewContext() already builds and used to hand straight to the native
ComposerContextPopup's constructor; this bridge only republishes it into a
persistent host instead of a one-shot popup. Every item here is removable
(ComposerBridge._context_items() always assigns a real id), so unlike the
legacy popup's per-row `removable` bool, no such field is carried - only the
synthetic anchor row (never removable) is handled separately client-side.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ComposerContextAnchor:
    id: str
    label: str
    type: str


@dataclass
class ComposerContextItem:
    id: str
    name: str
    kind: str
    tokenCount: int


@dataclass
class ComposerContextStatePayload:
    """The complete published snapshot, including the envelope fields
    IslandBridge.publish() adds to every island's payload."""

    schemaVersion: int
    revision: int
    items: list[ComposerContextItem]
    totalTokens: int
    anchor: ComposerContextAnchor | None = None
    # See ComposerStatePayload's identical field for the full negotiation
    # rationale; optional for the same reason (models a sender predating this
    # field, not today's - IslandBridge.publish() always emits it).
    minCompatibleSchemaVersion: int | None = None
