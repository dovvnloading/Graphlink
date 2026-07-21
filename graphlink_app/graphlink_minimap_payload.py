"""The minimap island's outbound wire contract (Phase 6 increment 5).

Absorbs `MinimapWidget` (native QPainter QWidget, deleted this increment).
`id` is a plain `str(id(node))` - a stable-for-the-lifetime-of-the-node
wire identifier, not a persisted node property: `ChatNode` carries no
identifier field of its own anywhere in this codebase, and inventing one
that survives a session save/restore would be a real, out-of-scope
schema/serialization change no consumer needs. The minimap only ever
resolves an `id` back to a live node within the SAME running session that
published it, exactly the scope `id(node)` naturally covers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MinimapNodeEntry:
    id: str
    isUser: bool
    preview: str


@dataclass
class MinimapStatePayload:
    """The complete published snapshot, including the envelope fields
    IslandBridge.publish() adds to every island's payload."""

    schemaVersion: int
    revision: int
    nodes: list[MinimapNodeEntry]
    minCompatibleSchemaVersion: int | None = None
