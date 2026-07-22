"""The scene topic's wire contract (Qt-removal plan R1) - the canvas
document the React Flow canvas renders: nodes, edges, navigation pins, and
canvas settings, published as full snapshots over the WS event bus.

THIS IS A WIRE FORMAT, NOT A DOMAIN MODEL (the graphlink_composer_payload.py
convention): the domain lives in backend/canvas.py's SceneDocument, which
sources GridViewSettings and NavigationPinStore - this module only fixes the
JSON shape and generates the TS type + runtime validator the SPA consumes.

R1 nodes are placeholders (`kind: "placeholder"`); R3 extends `kind` per
migrated node type - additive only, per the schema-versioning contract.

R3.1 adds the `chat` kind's fields (content/isUser/isCollapsed - the real
persisted shape from the legacy ChatNode's serializer, minus everything
Qt-only): populated for kind=="chat" rows, defaulted (empty/false) for every
other kind, so the schema stays additive-only as new kinds land.

R3.5 adds the `code` kind's fields (code/language): populated for kind=="code"
rows, defaulted (empty string) for every other kind, same additive rule.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SceneNodeRow:
    id: str
    x: float
    y: float
    title: str
    kind: str
    content: str = ""
    isUser: bool = False
    isCollapsed: bool = False
    # R3.5: the code node's real persisted shape - populated for kind=="code"
    # rows, defaulted (empty string) for every other kind.
    code: str = ""
    language: str = ""


@dataclass
class SceneEdgeRow:
    id: str
    source: str
    target: str


@dataclass
class ScenePinRow:
    id: str
    title: str
    note: str
    x: float
    y: float


@dataclass
class SceneStatePayload:
    """The complete published snapshot, including the envelope fields the
    event bus stamps onto every topic's payload."""

    schemaVersion: int
    revision: int
    nodes: list[SceneNodeRow]
    edges: list[SceneEdgeRow]
    pins: list[ScenePinRow]
    snapToGrid: bool
    dragFactor: float
    fontFamily: str
    fontSizePt: int
    fontColor: str
    minCompatibleSchemaVersion: int | None = None
