"""The drag-speed island's outbound wire contract (Phase 6 increment 5).

Absorbs `ChatView.control_widget` (native QWidget, deleted this increment).
No "current value" round-trips - the legacy widget never read one back
either (its slider always started at a hardcoded 100), matching
FontControl's own "pure fire-and-forget" precedent. `percentPresets`/
`percentMin`/`percentMax` are Python-owned static configuration published
over the wire rather than hardcoded a second time in React, the same
precedent `ToolbarBridge.MODE_OPTIONS` already established.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DragSpeedStatePayload:
    """The complete published snapshot, including the envelope fields
    IslandBridge.publish() adds to every island's payload."""

    schemaVersion: int
    revision: int
    percentPresets: list[int]
    percentMin: int
    percentMax: int
    minCompatibleSchemaVersion: int | None = None
