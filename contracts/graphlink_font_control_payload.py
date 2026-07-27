"""The font-control island's outbound wire contract (Phase 6 increment 4).

Absorbs `graphlink_widgets/controls.py`'s native `FontControl` QWidget.
Unlike grid appearance, font state carries ZERO widget-owned model to
extract - `FontControl`'s 3 controls always fired their Qt signals straight
into `ChatScene.setFontFamily`/`setFontSize`/`setFontColor` (confirmed by
reading `ChatView.__init__`), so `ChatScene` was already the sole owner of
current font state before this increment touched anything. This payload
therefore carries no "current value" fields at all (neither the legacy
widget nor this island have ever round-tripped the scene's live font state
back into the panel - both are, and always were, pure fire-and-forget
controls) - only the static configuration content the legacy widget
hardcoded directly in its own `__init__` (`fontFamilies`/`colorPresets`/
`sizeMin`/`sizeMax`), published once rather than duplicated a second time in
React, the same precedent `ToolbarBridge.MODE_OPTIONS` established.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FontControlStatePayload:
    """The complete published snapshot, including the envelope fields
    IslandBridge.publish() adds to every island's payload."""

    schemaVersion: int
    revision: int
    fontFamilies: list[str]
    colorPresets: list[str]
    sizeMin: int
    sizeMax: int
    minCompatibleSchemaVersion: int | None = None
