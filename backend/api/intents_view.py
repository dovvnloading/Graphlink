"""ADR-002 stage 2.6 (PR3/3, the final slice): canvas view/behavior
toggles, organize, and font.

Relocated VERBATIM from backend/canvas.py's former register_canvas
(closures at lines 411-464; registration calls at lines 445-471, minus
the pin registrations at 435-438 which moved to backend/api/
intents_pins.py instead) - pure code motion, no behavior change. Grouped
with the view toggles (not split into its own module) because font
intents are canvas-appearance state exactly like drag/snap/guides -
see set_font_family's own registration comment.
"""

from __future__ import annotations

from backend.api._shared import make_publish_scene
from backend.domain.graph import SceneDocument
from backend.events import SessionBus


def register_view_intents(bus: SessionBus, document: SceneDocument) -> None:
    publish_scene = make_publish_scene(bus)

    async def set_snap_to_grid(enabled):
        document.snap_to_grid = bool(enabled)
        await publish_scene()

    async def set_fade_connections(enabled):
        document.fade_connections_enabled = bool(enabled)
        await publish_scene()

    async def set_orthogonal_routing(enabled):
        document.orthogonal_routing = bool(enabled)
        await publish_scene()

    async def set_smart_guides(enabled):
        document.smart_guides = bool(enabled)
        await publish_scene()

    async def set_drag_factor(factor):
        document.set_drag_factor(factor)
        await publish_scene()

    async def set_view_state(zoom_factor, scroll_x, scroll_y):
        document.set_view_state(zoom_factor, scroll_x, scroll_y)
        await publish_scene()

    bus.register_intent("scene", "setSnapToGrid", set_snap_to_grid)
    bus.register_intent("scene", "setFadeConnections", set_fade_connections)
    # Intent name matches the legacy GridControlBridge's own
    # setOrthogonalConnections Slot name 1:1, same convention as
    # setSnapToGrid/setFadeConnections above - the Python function name above
    # doesn't need to match.
    bus.register_intent("scene", "setOrthogonalConnections", set_orthogonal_routing)
    bus.register_intent("scene", "setSmartGuides", set_smart_guides)
    bus.register_intent("scene", "setDragFactor", set_drag_factor)
    bus.register_intent("scene", "setViewState", set_view_state)

    async def organize_nodes():
        document.organize()
        await publish_scene()

    async def set_font_family(family):
        document.set_font(family=family)
        await publish_scene()

    async def set_font_size(size_pt):
        document.set_font(size_pt=size_pt)
        await publish_scene()

    async def set_font_color(color_hex):
        document.set_font(color=color_hex)
        await publish_scene()

    bus.register_intent("scene", "organizeNodes", organize_nodes)
    # Font intent names == FontControlBridge's @Slot names, same 1:1 rule as
    # grid; they live on the scene topic because the VALUES are scene state.
    bus.register_intent("scene", "setFontFamily", set_font_family)
    bus.register_intent("scene", "setFontSize", set_font_size)
    bus.register_intent("scene", "setFontColor", set_font_color)
