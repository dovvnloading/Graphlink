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
from backend.notifications import NotificationState

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

# R3.3: the Composer's Send action stacks each new message below its parent
# by this much - a simple deterministic layout, not the legacy
# find_branch_position packing algorithm (a later refinement).
MESSAGE_VERTICAL_SPACING = 160


class SceneError(ValueError):
    """A scene intent referenced something that does not exist or is invalid.
    Raised so the WS layer reports it to the caller instead of crashing."""


CHAT_TITLE_PREVIEW_LENGTH = 60
# R3.5: code titles are a language label plus first line, not prose, so a
# shorter preview than chat's 60 is plenty.
CODE_TITLE_PREVIEW_LENGTH = 40


@dataclass
class SceneNode:
    id: str
    x: float
    y: float
    title: str
    kind: str = "placeholder"
    # R3.1 (doc/QT_REMOVAL_PLAN.md): the chat node's real persisted shape -
    # graphlink_session/serializers.py's raw_content/is_user/is_collapsed,
    # minus everything Qt-only (paint state, scroll position, docked-child
    # widgets). Unused (default) for every other kind.
    content: str = ""
    is_user: bool = False
    is_collapsed: bool = False
    # R3.5: the code node's real persisted shape - unused/defaulted for
    # every other kind.
    code: str = ""
    language: str = ""
    # R3.9 (doc/QT_REMOVAL_PLAN.md): the document node's real persisted shape -
    # graphlink_scene.py's add_document_node()/graphlink_node_document.py's
    # DocumentNode.__init__ attachment metadata (title/content above, plus
    # these six fields). The backend stores these VERBATIM, exactly as
    # passed in - none of the legacy view-layer formatting below happens
    # here; reproducing it is the frontend's job (same as the paint()/menu
    # code it replaces). Formatting rules extracted from
    # graphlink_nodes/graphlink_node_document.py + graphlink_audio.py, for
    # the frontend to reproduce exactly in TypeScript:
    #
    # - Byte-size formatting (DocumentNode._format_byte_size): if byte_size
    #   is falsy (None or 0) -> "Unknown". Else repeatedly divide by 1024.0
    #   walking units ("B","KB","MB","GB","TB"), stopping at the first unit
    #   where size < 1024.0 (or unit == "TB"); "B" formats as a bare integer
    #   ("512 B"), every other unit formats with exactly one decimal place
    #   ("1.5 MB").
    # - Duration formatting (graphlink_audio.format_duration): None ->
    #   "Unknown". Else round(seconds) to the nearest whole second, clamp
    #   negative to 0, divmod into hours/minutes/seconds; if hours > 0 format
    #   "H:MM:SS" (hours unpadded, minutes/seconds zero-padded to 2 digits),
    #   else "M:SS" (minutes unpadded, seconds zero-padded).
    # - Metadata rows (DocumentNode._build_metadata_rows), in this exact
    #   order, each omitted entirely when its value is empty/None: Type
    #   ("Audio file" if attachment_kind=="audio" else "Document", always
    #   present) / Duration (formatted, only if duration_seconds is not
    #   None) / Format (mime_type, only if truthy) / Size (formatted byte
    #   size, only if byte_size is truthy) / Path (file_path, only if
    #   truthy).
    # - preview_label auto-fill (DocumentNode._build_preview_label), used
    #   only when the caller didn't supply one: for attachment_kind=="audio"
    #   -> "Audio | {duration formatted, or 'Audio' if duration_seconds is
    #   None}"; otherwise derived from title's file extension via
    #   os.path.splitext: ".pdf" -> "PDF", ".docx" -> "DOCX", any other
    #   extension -> that extension uppercased without its dot, no extension
    #   -> "Document".
    # - Audio-preview-suppression heuristic
    #   (DocumentNode._should_show_audio_preview): normalize both `content`
    #   and the auto-built `audio_details` block the same way (join
    #   right-stripped lines with "\n", strip the whole string, lowercase).
    #   Hide the content-preview panel (show only the metadata table) when:
    #   normalized content is empty; OR normalized content == normalized
    #   audio_details (content is nothing but the auto-generated metadata
    #   block); OR normalized content startswith "audio attachment" AND
    #   contains "duration:" (catches legacy-saved sessions whose persisted
    #   content is an older/differently-valued metadata block). Otherwise
    #   show the preview. `audio_details` itself is the joined lines: "Audio
    #   attachment", then "Duration: {formatted}" if duration_seconds is not
    #   None, "Format: {mime_type}" if truthy, "Size: {formatted byte size}"
    #   if byte_size truthy, "Path: {file_path}" if truthy - same
    #   presence/order rules as the metadata rows above.
    attachment_kind: str = ""
    file_path: str = ""
    mime_type: str = ""
    duration_seconds: float | None = None
    byte_size: int | None = None
    preview_label: str = ""


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
    # R3.3: which chat node the Composer's next Send continues from - the
    # Qt-free stand-in for "the currently active branch" until real node
    # selection exists. None means the next message starts a fresh root.
    last_chat_node_id: str | None = None
    _counter: itertools.count = field(default_factory=itertools.count, repr=False)

    # -- nodes -------------------------------------------------------------

    def add_node(self, x: float, y: float, title: str = "") -> SceneNode:
        node_id = f"n{next(self._counter)}"
        node = SceneNode(id=node_id, x=float(x), y=float(y), title=title or f"Node {node_id[1:]}")
        self.nodes[node_id] = node
        return node

    def add_chat_node(
        self,
        x: float,
        y: float,
        content: str,
        is_user: bool,
        parent_id: str | None = None,
    ) -> SceneNode:
        """The Qt-free ChatScene.add_chat_node equivalent: a real message-
        bubble node, optionally connected to a parent (the branch it
        continues). Mirrors add_node's id/dict bookkeeping; the only new
        behavior is the parent-edge, ported from the legacy scene's own
        ConnectionItem creation."""
        if parent_id is not None and parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        title = content[:CHAT_TITLE_PREVIEW_LENGTH] or ("You" if is_user else "Assistant")
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title=title,
            kind="chat",
            content=str(content),
            is_user=bool(is_user),
        )
        self.nodes[node_id] = node
        if parent_id is not None:
            self.connect(parent_id, node_id)
        return node

    def add_code_node(
        self,
        x: float,
        y: float,
        code: str,
        language: str,
        parent_id: str | None = None,
    ) -> SceneNode:
        """R3.5's code-node equivalent of add_chat_node: a real code-block
        node, optionally connected to a parent. Mirrors add_chat_node's
        id/dict bookkeeping and parent-edge behavior exactly. Code nodes are
        NOT branch points - nothing ever gets reparented through them - so
        unlike chat there is no delete_code_node; deletion goes entirely
        through the existing generic remove_nodes."""
        if parent_id is not None and parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        label = str(language) or "code"
        first_line = str(code).split("\n", 1)[0]
        preview = first_line[:CODE_TITLE_PREVIEW_LENGTH]
        title = f"{label}: {preview}" if preview else label
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title=title,
            kind="code",
            code=str(code),
            language=str(language),
        )
        self.nodes[node_id] = node
        if parent_id is not None:
            self.connect(parent_id, node_id)
        return node

    def add_document_node(
        self,
        x: float,
        y: float,
        title: str,
        content: str,
        attachment_kind: str,
        parent_id: str,
        *,
        file_path: str = "",
        mime_type: str = "",
        duration_seconds: float | None = None,
        byte_size: int | None = None,
        preview_label: str = "",
    ) -> SceneNode:
        """R3.9's document-node equivalent of add_chat_node/add_code_node: a
        real file-attachment node (a document or an audio file), for the
        legacy DocumentNode / ChatScene.add_document_node pair. UNLIKE
        chat/code, parent_id is REQUIRED here, not optional: read fresh from
        graphlink_scene.py, add_document_node(title, content,
        parent_user_node, ...) takes parent_user_node as a plain required
        positional with no default, and unconditionally constructs a
        DocumentConnectionItem(parent_user_node, node) - there is no `if
        parent_id` guard around that connection the way chat/code have
        around theirs - so a DocumentNode can never exist unparented.
        Document nodes are also NOT branch points (same as code): there is
        no delete_document_node; deletion goes entirely through the
        existing generic remove_nodes.

        The six attachment fields are stored verbatim - no title-preview
        truncation (DocumentNode.title in the legacy app is just whatever
        descriptive title/filename was passed in, confirmed by reading
        DocumentNode.__init__: `self.title = title`, no slicing), and none
        of the legacy view-layer formatting (byte-size/duration strings,
        preview_label auto-fill, audio-preview suppression) happens here -
        see the R3.9 comment on the SceneNode dataclass fields for those
        exact rules.
        """
        if parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        # Mirrors DocumentNode.__init__'s `(attachment_kind or
        # "document").lower()` normalization - the attachment_kind param has
        # no default in this signature (per spec), but an empty/None value
        # still needs to fall back to "document" and casing still needs to
        # normalize, since "audio" vs "Audio" is a real behavioral branch
        # (metadata "Type" row, preview label, badge text all key off it).
        normalized_kind = str(attachment_kind or "document").lower()
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title=str(title),
            kind="document",
            content=str(content),
            attachment_kind=normalized_kind,
            file_path=str(file_path),
            mime_type=str(mime_type),
            duration_seconds=duration_seconds,
            byte_size=byte_size,
            preview_label=str(preview_label),
        )
        self.nodes[node_id] = node
        self.connect(parent_id, node_id)
        return node

    def delete_chat_node(self, node_id: str) -> None:
        """Delete one chat node WITHOUT orphaning its branch: children are
        re-parented to the deleted node's own parent (or become roots if it
        had none), mirroring ChatScene.delete_chat_node's load-bearing
        reparent rule - a plain remove_nodes cascade-delete would sever every
        child branch instead of splicing them back together."""
        if node_id not in self.nodes:
            raise SceneError(f"unknown node: {node_id}")
        parent_edge = next((e for e in self.edges.values() if e.target == node_id), None)
        parent_id = parent_edge.source if parent_edge is not None else None
        child_edges = [e for e in self.edges.values() if e.source == node_id]

        for edge in [parent_edge, *child_edges]:
            if edge is not None:
                self.edges.pop(edge.id, None)
        if parent_id is not None:
            for edge in child_edges:
                self.connect(parent_id, edge.target)

        if self.last_chat_node_id == node_id:
            # The active branch continues from wherever it now ends: the
            # deleted node's own parent (None if it had none either).
            self.last_chat_node_id = parent_id

        del self.nodes[node_id]

    def send_message(self, text: str) -> SceneNode:
        """The Composer's real Send action (R3.3): create a real user
        ChatNode continuing the current branch (last_chat_node_id), or
        start a fresh root if none exists yet. Positioning is a simple
        deterministic stack, not the legacy find_branch_position packing
        algorithm - real auto-layout is a later refinement; "Organize
        Nodes" already exists as a fallback."""
        parent_id = self.last_chat_node_id
        if parent_id is not None and parent_id in self.nodes:
            parent = self.nodes[parent_id]
            x, y = parent.x, parent.y + MESSAGE_VERTICAL_SPACING
        else:
            parent_id = None
            chat_node_count = sum(1 for n in self.nodes.values() if n.kind == "chat")
            x, y = 0.0, chat_node_count * MESSAGE_VERTICAL_SPACING
        node = self.add_chat_node(x, y, text, True, parent_id=parent_id)
        self.last_chat_node_id = node.id
        return node

    def set_chat_collapsed(self, node_id: str, collapsed: bool) -> None:
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.is_collapsed = bool(collapsed)

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
                {
                    "id": n.id,
                    "x": n.x,
                    "y": n.y,
                    "title": n.title,
                    "kind": n.kind,
                    "content": n.content,
                    "isUser": n.is_user,
                    "isCollapsed": n.is_collapsed,
                    "code": n.code,
                    "language": n.language,
                    "attachmentKind": n.attachment_kind,
                    "filePath": n.file_path,
                    "mimeType": n.mime_type,
                    "durationSeconds": n.duration_seconds,
                    "byteSize": n.byte_size,
                    "previewLabel": n.preview_label,
                }
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


def register_canvas(bus: SessionBus, notifications: NotificationState) -> SceneDocument:
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

    async def add_chat_node(x, y, content, is_user, parent_id=None):
        node = document.add_chat_node(x, y, content, is_user, parent_id)
        await publish_scene()
        return node.id

    async def add_code_node(x, y, code, language, parent_id=None):
        node = document.add_code_node(x, y, code, language, parent_id)
        await publish_scene()
        return node.id

    async def add_document_node(
        x,
        y,
        title,
        content,
        attachment_kind,
        parent_id,
        file_path="",
        mime_type="",
        duration_seconds=None,
        byte_size=None,
        preview_label="",
    ):
        node = document.add_document_node(
            x,
            y,
            title,
            content,
            attachment_kind,
            parent_id,
            file_path=file_path,
            mime_type=mime_type,
            duration_seconds=duration_seconds,
            byte_size=byte_size,
            preview_label=preview_label,
        )
        await publish_scene()
        return node.id

    async def delete_chat_node(node_id):
        document.delete_chat_node(node_id)
        await publish_scene()

    async def set_chat_collapsed(node_id, collapsed):
        document.set_chat_collapsed(node_id, collapsed)
        await publish_scene()

    async def send_message(text):
        # R3.3: the real Send action - a real user ChatNode, continuing the
        # active branch. The assistant's reply needs the agent layer
        # (graphlink_config.py's Qt/non-Qt split is an R4 prerequisite - see
        # doc/QT_REMOVAL_PLAN.md's R3 scoping note), so this is an honest
        # deferred notice rather than a fake/stubbed response.
        node = document.send_message(text)
        await publish_scene()
        notifications.show("AI response generation lands in R4.", "info")
        await bus.publish("notification")
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

    async def update_pin(pin_id, title, note):
        # NavigationPinRecord.create() validation (non-empty/length-bounded
        # title, length-bounded note) runs via with_updates -> create's own
        # field validators, same as add_pin's path - a bad edit raises
        # NavigationPinValidationError, which is a ValueError subclass and
        # therefore already reported to the caller as an intent error.
        document.pins.update(pin_id, title=str(title), note=str(note))
        await publish_scene()

    async def set_snap_to_grid(enabled):
        document.snap_to_grid = bool(enabled)
        await publish_scene()

    async def set_drag_factor(factor):
        document.set_drag_factor(factor)
        await publish_scene()

    bus.register_intent("scene", "addNode", add_node)
    bus.register_intent("scene", "addChatNode", add_chat_node)
    bus.register_intent("scene", "addCodeNode", add_code_node)
    bus.register_intent("scene", "addDocumentNode", add_document_node)
    bus.register_intent("scene", "deleteChatNode", delete_chat_node)
    bus.register_intent("scene", "setChatCollapsed", set_chat_collapsed)
    bus.register_intent("scene", "sendMessage", send_message)
    bus.register_intent("scene", "moveNode", move_node)
    bus.register_intent("scene", "removeNodes", remove_nodes)
    bus.register_intent("scene", "connectNodes", connect_nodes)
    bus.register_intent("scene", "removeEdges", remove_edges)
    bus.register_intent("scene", "addPin", add_pin)
    bus.register_intent("scene", "movePin", move_pin)
    bus.register_intent("scene", "removePin", remove_pin)
    bus.register_intent("scene", "updatePin", update_pin)
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
