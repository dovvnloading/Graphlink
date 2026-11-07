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
from backend.domain.model import SceneError
from backend.domain.organization import apply_organization_plan, make_organization_plan
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
        # ADR-010 stage 10.3: auto-layout repositions potentially EVERY node
        # in the scene. One Ctrl+Z has to put the whole layout back - which
        # it does here because every node is named, so a single command
        # captures all of their before/after positions at once.
        document.record_command(
            "organizeNodes", "user", document.organize,
            node_ids=list(document.nodes.keys()),
        )
        await publish_scene()

    async def preview_organize(
        scope="all", root_id=None, node_ids=None, fixed_node_ids=None,
        preserve_frame_sizes=True,
    ):
        """Read-only: compute a complete layout preview without publishing."""
        plan = make_organization_plan(
            document, scope, root_id=root_id, node_ids=node_ids,
            fixed_node_ids=fixed_node_ids,
            preserve_frame_sizes=preserve_frame_sizes,
        )
        return plan.preview(document)

    async def apply_organize(
        scope, root_id, node_ids, fixed_node_ids, preserve_frame_sizes, expected_plan_id,
    ):
        """Recompute on the server and reject any stale/mismatched preview."""
        plan = make_organization_plan(
            document, scope, root_id=root_id, node_ids=node_ids,
            fixed_node_ids=fixed_node_ids,
            preserve_frame_sizes=preserve_frame_sizes,
        )
        if not isinstance(expected_plan_id, str) or expected_plan_id != plan.plan_id:
            raise SceneError("organization preview changed; preview again")
        document.record_command(
            "organizeNodes", "user",
            lambda: apply_organization_plan(document, plan),
            node_ids=list(document.nodes.keys()),
        )
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
    bus.register_intent("scene", "previewOrganize", preview_organize)
    bus.register_intent("scene", "applyOrganize", apply_organize)
    # Font intent names == FontControlBridge's @Slot names, same 1:1 rule as
    # grid; they live on the scene topic because the VALUES are scene state.
    bus.register_intent("scene", "setFontFamily", set_font_family)
    bus.register_intent("scene", "setFontSize", set_font_size)
    bus.register_intent("scene", "setFontColor", set_font_color)
