"""ADR-002 stage 2.6 (PR3/3, the final slice): grid-control intents (names
== GridControlBridge @Slot names).

Relocated VERBATIM from backend/canvas.py's former register_canvas
(closures at lines 475-491; registration calls at lines 493-496) - pure
code motion, no behavior change. Kept as its own module (not folded into
backend/api/intents_view.py) because these four publish to the
independent "grid-control" topic, not "scene" - a genuinely different
wire contract, not just a size trim.
"""

from __future__ import annotations

from graphlink_grid_view_settings import GRID_STYLE_PRESETS

from backend.api._shared import make_publish_grid
from backend.domain.graph import SceneDocument
from backend.domain.model import SceneError
from backend.events import SessionBus


def register_grid_intents(bus: SessionBus, document: SceneDocument) -> None:
    publish_grid = make_publish_grid(bus)

    async def set_grid_size(size):
        # Clamped: 0/negative would blank or invert the background pattern,
        # and the View popover's spacing slider (4-120) relies on the same
        # floor being enforced where the value actually lands.
        document.grid.grid_size = max(4, min(400, int(size)))
        await publish_grid()

    async def set_grid_opacity_percent(percent):
        document.grid.grid_opacity = max(0, min(100, int(percent))) / 100.0
        await publish_grid()

    async def set_grid_style(style):
        if style not in GRID_STYLE_PRESETS:
            raise SceneError(f"unknown grid style: {style}")
        document.grid.grid_style = str(style)
        await publish_grid()

    async def set_grid_color(color_hex):
        document.grid.grid_color = str(color_hex)
        await publish_grid()

    bus.register_intent("grid-control", "setGridSize", set_grid_size)
    bus.register_intent("grid-control", "setGridOpacityPercent", set_grid_opacity_percent)
    bus.register_intent("grid-control", "setGridStyle", set_grid_style)
    bus.register_intent("grid-control", "setGridColor", set_grid_color)
