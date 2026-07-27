"""The grid-control island's outbound wire contract (Phase 6 increment 4).

Absorbs `graphlink_widgets/controls.py`'s native `GridControl` QWidget.
`gridSize`/`gridOpacityPercent`/`gridStyle`/`gridColor` reflect
`GridViewSettings`'s own current values (a real, live-owned model, unlike
the toolbar's `controlsChecked`) - genuinely worth round-tripping since the
model IS the authoritative source `ChatView.drawBackground()` reads.
`gridOpacityPercent` is an int 0-100 (matching the legacy `QSlider`'s own
scale exactly) rather than the model's internal 0.0-1.0 float, avoiding any
float-precision drift over a JSON round-trip for a value a slider only ever
produces as a whole percent anyway.

`sizePresets`/`stylePresets`/`colorPresets` are Python-owned static
configuration published over the wire rather than hardcoded a second time in
React - the same precedent `ToolbarBridge.MODE_OPTIONS` already established.
`colorPresets` is intentionally recomputed fresh on every publish (not cached
at construction) since 3 of its 5 entries are theme-derived
(`get_current_palette()`) - the legacy widget's own presets were frozen at
whatever theme was active when `GridControl.__init__` ran, since
`on_theme_changed()` only ever restyled the panel's own stylesheet, never
recomputed the preset buttons themselves. Republishing on every theme change
(the standard `WebIslandHost.on_theme_changed()` hook) means this island
self-corrects the swatches for free where the legacy widget could not.

The 4 grid/routing checkboxes (snap-to-grid, orthogonal connections, smart
guides, fade connections) are deliberately NOT part of this payload - they
write directly onto `ChatScene`, not `GridViewSettings`, and (matching the
toolbar's own `controlsChecked` precedent) nothing else in the app ever
reads that state back, so there is no server round-trip to model.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GridControlStatePayload:
    """The complete published snapshot, including the envelope fields
    IslandBridge.publish() adds to every island's payload."""

    schemaVersion: int
    revision: int
    gridSize: int
    gridOpacityPercent: int
    gridStyle: str
    gridColor: str
    sizePresets: list[int]
    stylePresets: list[str]
    colorPresets: list[str]
    minCompatibleSchemaVersion: int | None = None
