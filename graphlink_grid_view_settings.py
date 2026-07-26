"""Plain Python model for the canvas grid's appearance settings (Phase 6
increment 4) - extracted from the widget-as-model anti-pattern where
`ChatView.drawBackground()`, `ChatScene`'s node-drag snap math, and
`ChatCanvasChartItem`'s resize-to-grid math all read `grid_size`/
`grid_opacity`/`grid_style`/`grid_color` directly off the live, fully-styled
`GridControl` QWidget on every call.

Deliberately Qt-free (plain dataclass, `grid_color` stored as a hex string
rather than a `QColor`) - nothing about grid appearance requires a live Qt
object, and every consumer already constructs its own `QColor` at the point
of use. No persistence: this always resets to these defaults on app launch,
matching the legacy widget's own construction-time-only defaults (nothing in
`SettingsManager` ever saved/restored grid state).

The 4 grid/routing checkboxes (snap-to-grid, orthogonal connections, smart
guides, fade connections) are NOT part of this model - they already write
directly onto `ChatScene` (`snap_to_grid`/`orthogonal_routing`/
`smart_guides`/`fade_connections_enabled`), confirmed by reading
`ChatView._on_snap_toggled()` and friends; only the 4 appearance fields
below were ever genuinely widget-owned state.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_GRID_SIZE = 10
DEFAULT_GRID_OPACITY = 0.3
DEFAULT_GRID_STYLE = "Dots"
DEFAULT_GRID_COLOR = "#555555"

GRID_SIZE_PRESETS = (10, 20, 50, 100)
GRID_STYLE_PRESETS = ("Dots", "Lines", "Cross")


@dataclass
class GridViewSettings:
    grid_size: int = DEFAULT_GRID_SIZE
    grid_opacity: float = DEFAULT_GRID_OPACITY
    grid_style: str = DEFAULT_GRID_STYLE
    grid_color: str = DEFAULT_GRID_COLOR
