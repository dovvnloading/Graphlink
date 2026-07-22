"""Canvas domain state for the new architecture (Qt-removal plan R1).

The backend half of the React Flow canvas: the scene document (nodes, edges,
navigation pins, canvas settings) plus the grid-appearance topic, all
session-scoped and WS-published as full snapshots.

Model sources are the surviving Qt-free modules, per the plan's R1 line
"sourced from the existing Qt-free scene model files":
- `GridViewSettings` (graphlink_grid_view_settings) - the exact model
  `ChatView.drawBackground()` reads today, republished here unchanged so the
  R2 grid-control port keeps its intent names 1:1 with GridControlBridge.
- `NavigationPinStore`/`NavigationPinRecord` (graphlink_navigation_pins) -
  the pin domain store, reused verbatim (validation, ordering, ids).

R1 nodes are PLACEHOLDERS by design (plan acceptance: "create/move/connect
placeholder nodes"); real node types arrive one-per-increment in R3. Scene
persistence to the session DB is R6 - for R1 the backend's in-memory
document IS the source of truth the window can reload against.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any

from graphlink_grid_view_settings import (
    GRID_SIZE_PRESETS,
    GRID_STYLE_PRESETS,
    GridViewSettings,
)
from graphlink_navigation_pins import NavigationPinRecord, NavigationPinStore

from backend.events import SessionBus

# Dark-theme grid swatches. The Qt bridge derived 3 of 5 from the live
# QPalette; the backend is Qt-free by law (test_no_qt_anywhere.py), so until
# the R2 theme service exists these are the dark theme's actual values,
# frozen here as data, not styling.
GRID_COLOR_PRESETS = ["#404040", "#555555", "#4a90d9", "#2f5b3c", "#5b2f4f"]

DRAG_FACTOR_MIN = 0.05
DRAG_FACTOR_MAX = 1.0

# R2: the View popover's Drag/Font sections. Values carried over verbatim
# from graphlink_drag_speed_bridge.py / graphlink_font_control_bridge.py
# (both Qt modules scheduled for deletion; these constants are pure data).
DRAG_PERCENT_PRESETS = [25, 50, 75, 100]
DRAG_PERCENT_MIN = 5
DRAG_PERCENT_MAX = 100
FONT_FAMILIES = [
    "Segoe UI", "Arial", "Verdana", "Tahoma", "Consolas",
    "Calibri", "Cambria", "Lucida Grande", "Trebuchet MS",
    "Courier New", "Times New Roman", "Georgia", "System UI",
    "DejaVu Sans", "Segoe UI Variable", "Arial Rounded MT Bold",
]
FONT_COLOR_PRESETS = ["#F0F0F0", "#C7C7C7", "#949494", "#818181"]
FONT_SIZE_MIN = 8
FONT_SIZE_MAX = 16

# Organize: the R2 tidy-layout for placeholder nodes (the Qt organize used
# node-size-aware packing; that returns with real nodes in R3).
ORGANIZE_SPACING_X = 260
ORGANIZE_SPACING_Y = 180


class SceneError(ValueError):
    """A scene intent referenced something that does not exist or is invalid.
    Raised so the WS layer reports it to the caller instead of crashing."""


@dataclass
class SceneNode:
    id: str
    x: float
    y: float
    title: str
    kind: str = "placeholder"


@dataclass
class SceneEdge:
    id: str
    source: str
    target: str


@dataclass
class SceneDocument:
    """The canvas document for one session. Plain data + invariants; the
    R6 serializer will read/write exactly this shape."""

    nodes: dict[str, SceneNode] = field(default_factory=dict)
    edges: dict[str, SceneEdge] = field(default_factory=dict)
    pins: NavigationPinStore = field(default_factory=NavigationPinStore)
    grid: GridViewSettings = field(default_factory=GridViewSettings)
    snap_to_grid: bool = False
    drag_factor: float = 1.0
    # Canvas font (ChatScene's setFontFamily/-Size/-Color state, R2): defaults
    # match the legacy scene's own construction-time values.
    font_family: str = "Segoe UI"
    font_size_pt: int = 9
    font_color: str = "#F0F0F0"
    _counter: itertools.count = field(default_factory=itertools.count, repr=False)

    # -- nodes -------------------------------------------------------------

    def add_node(self, x: float, y: float, title: str = "") -> SceneNode:
        node_id = f"n{next(self._counter)}"
        node = SceneNode(id=node_id, x=float(x), y=float(y), title=title or f"Node {node_id[1:]}")
        self.nodes[node_id] = node
        return node

    def move_node(self, node_id: str, x: float, y: float) -> SceneNode:
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.x, node.y = float(x), float(y)
        return node

    def remove_nodes(self, node_ids: list[str]) -> None:
        for node_id in node_ids:
            if self.nodes.pop(node_id, None) is not None:
                # Edges die with either endpoint - same invariant ChatScene
                # enforced on node removal.
                self.edges = {
                    eid: e
                    for eid, e in self.edges.items()
                    if e.source != node_id and e.target != node_id
                }

    # -- edges -------------------------------------------------------------

    def connect(self, source: str, target: str) -> SceneEdge:
        if source not in self.nodes or target not in self.nodes:
            raise SceneError(f"cannot connect unknown nodes: {source} -> {target}")
        if source == target:
            raise SceneError("cannot connect a node to itself")
        for edge in self.edges.values():
            if edge.source == source and edge.target == target:
                return edge  # idempotent, matching ChatScene's duplicate guard
        edge_id = f"e{next(self._counter)}"
        edge = SceneEdge(id=edge_id, source=source, target=target)
        self.edges[edge_id] = edge
        return edge

    def remove_edges(self, edge_ids: list[str]) -> None:
        for edge_id in edge_ids:
            self.edges.pop(edge_id, None)

    # -- settings ----------------------------------------------------------

    def set_drag_factor(self, factor: float) -> None:
        self.drag_factor = max(DRAG_FACTOR_MIN, min(DRAG_FACTOR_MAX, float(factor)))

    def set_font(self, *, family: str | None = None, size_pt: int | None = None, color: str | None = None) -> None:
        if family is not None:
            if family not in FONT_FAMILIES:
                raise SceneError(f"unknown font family: {family}")
            self.font_family = family
        if size_pt is not None:
            self.font_size_pt = max(FONT_SIZE_MIN, min(FONT_SIZE_MAX, int(size_pt)))
        if color is not None:
            self.font_color = str(color)

    def organize(self) -> None:
        """Tidy layout: nodes into a near-square grid, stable id order."""
        ordered = sorted(self.nodes.values(), key=lambda n: n.id)
        if not ordered:
            return
        columns = max(1, math.ceil(math.sqrt(len(ordered))))
        for index, node in enumerate(ordered):
            node.x = float((index % columns) * ORGANIZE_SPACING_X)
            node.y = float((index // columns) * ORGANIZE_SPACING_Y)

    # -- snapshots ---------------------------------------------------------

    def scene_payload(self) -> dict[str, Any]:
        return {
            "nodes": [
                {"id": n.id, "x": n.x, "y": n.y, "title": n.title, "kind": n.kind}
                for n in self.nodes.values()
            ],
            "edges": [
                {"id": e.id, "source": e.source, "target": e.target}
                for e in self.edges.values()
            ],
            "pins": [
                {
                    "id": p.pin_id,
                    "title": p.title,
                    "note": p.note,
                    "x": p.position[0],
                    "y": p.position[1],
                }
                for p in self.pins.records
            ],
            "snapToGrid": self.snap_to_grid,
            "dragFactor": self.drag_factor,
            "fontFamily": self.font_family,
            "fontSizePt": self.font_size_pt,
            "fontColor": self.font_color,
        }

    def grid_payload(self) -> dict[str, Any]:
        # Field-for-field the GridControlStatePayload shape (whole-percent
        # opacity, presets on the wire) so the generated validator the
        # grid-control island already uses validates this topic untouched.
        return {
            "gridSize": self.grid.grid_size,
            "gridOpacityPercent": round(self.grid.grid_opacity * 100),
            "gridStyle": self.grid.grid_style,
            "gridColor": self.grid.grid_color,
            "sizePresets": list(GRID_SIZE_PRESETS),
            "stylePresets": list(GRID_STYLE_PRESETS),
            "colorPresets": list(GRID_COLOR_PRESETS),
        }


def register_canvas(bus: SessionBus) -> SceneDocument:
    """Give a session its canvas document + the scene/grid topics and every
    R1 intent. Intent names for grid mirror GridControlBridge's @Slot names
    1:1 so the R2 island port is a transport swap, not a redesign."""

    document = SceneDocument()

    bus.register_topic("scene", document.scene_payload)
    bus.register_topic("grid-control", document.grid_payload)
    # Static preset topics, field-for-field the DragSpeedStatePayload /
    # FontControlStatePayload shapes so the generated validators apply
    # unchanged (same reuse as grid-control).
    bus.register_topic(
        "drag-speed",
        lambda: {
            "percentPresets": list(DRAG_PERCENT_PRESETS),
            "percentMin": DRAG_PERCENT_MIN,
            "percentMax": DRAG_PERCENT_MAX,
        },
    )
    bus.register_topic(
        "font-control",
        lambda: {
            "fontFamilies": list(FONT_FAMILIES),
            "colorPresets": list(FONT_COLOR_PRESETS),
            "sizeMin": FONT_SIZE_MIN,
            "sizeMax": FONT_SIZE_MAX,
        },
    )

    async def publish_scene():
        await bus.publish("scene")

    async def publish_grid():
        await bus.publish("grid-control")

    # -- scene intents (async: they publish after mutating) ---------------

    async def add_node(x, y, title=""):
        node = document.add_node(x, y, title)
        await publish_scene()
        return node.id

    async def move_node(node_id, x, y):
        document.move_node(node_id, x, y)
        await publish_scene()

    async def remove_nodes(node_ids):
        document.remove_nodes(list(node_ids))
        await publish_scene()

    async def connect_nodes(source, target):
        edge = document.connect(source, target)
        await publish_scene()
        return edge.id

    async def remove_edges(edge_ids):
        document.remove_edges(list(edge_ids))
        await publish_scene()

    async def add_pin(title, x, y, note=""):
        record = NavigationPinRecord.create(title=title, x=x, y=y, note=note)
        document.pins.add(record)
        await publish_scene()
        return record.pin_id

    async def move_pin(pin_id, x, y):
        document.pins.move(pin_id, x, y)
        await publish_scene()

    async def remove_pin(pin_id):
        document.pins.remove(pin_id)
        await publish_scene()

    async def set_snap_to_grid(enabled):
        document.snap_to_grid = bool(enabled)
        await publish_scene()

    async def set_drag_factor(factor):
        document.set_drag_factor(factor)
        await publish_scene()

    bus.register_intent("scene", "addNode", add_node)
    bus.register_intent("scene", "moveNode", move_node)
    bus.register_intent("scene", "removeNodes", remove_nodes)
    bus.register_intent("scene", "connectNodes", connect_nodes)
    bus.register_intent("scene", "removeEdges", remove_edges)
    bus.register_intent("scene", "addPin", add_pin)
    bus.register_intent("scene", "movePin", move_pin)
    bus.register_intent("scene", "removePin", remove_pin)
    bus.register_intent("scene", "setSnapToGrid", set_snap_to_grid)
    bus.register_intent("scene", "setDragFactor", set_drag_factor)

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

    # -- grid intents (names == GridControlBridge @Slot names) -------------

    async def set_grid_size(size):
        document.grid.grid_size = int(size)
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

    return document
