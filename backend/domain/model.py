"""Scene data model: nodes, edges, errors, layout/appearance constants
(ADR-002 stage 2.2).

Relocated VERBATIM from backend/canvas.py (its lines 157-811). Pure data +
dataclasses - no backend infrastructure. backend/canvas.py imports every
name back (real usage by SceneDocument and its register_canvas closures),
so every existing `from backend.canvas import X` consumer keeps working
unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.domain.node_states import NodeState

# Grid swatches. The first three neutrals cover the subtle-texture case at
# increasing prominence; the five hues are tuned to read on the dark canvas
# without shouting, and to survive the light theme. The old set was frozen
# verbatim from the deleted Qt bridge's live-palette derivation (2 neutrals
# + 3 dark muddy hues that were nearly invisible against the canvas) - kept
# values #404040/#555555/#4a90d9 remain so an existing session's saved
# color still matches a swatch.
GRID_COLOR_PRESETS = [
    "#404040", "#555555", "#6E6E6E",
    "#4a90d9", "#3FA37E", "#C9A227", "#C96A6A", "#8A63C9",
]

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
# Node-text swatches: three neutral steps plus four soft tints that stay
# readable on the node-card surfaces in both themes. The old set was four
# barely-distinguishable grays (two of them 19 units apart) carried over
# verbatim from the deleted Qt bridge.
FONT_COLOR_PRESETS = [
    "#F0F0F0", "#C7C7C7", "#949494",
    "#9EC1E8", "#9FD0B5", "#E3C577", "#E0A3A3",
]
FONT_SIZE_MIN = 8
FONT_SIZE_MAX = 16

# All spawn placement and the Organize layout live in
# backend/domain/layout.py (LayoutOps) - the fixed-offset spacing constants
# that used to sit here (ORGANIZE_SPACING_X/Y, MESSAGE_VERTICAL_SPACING,
# BRANCH_HORIZONTAL_SPACING, NOTE_AGENT_X_OFFSET) were size-blind stopgaps
# from the Qt removal and are retired; placement now uses each node's real
# measured footprint plus collision resolution.

# R8a: how a generated Key Takeaway / Explainer Note is tinted. The colours
# are hex because the backend never resolves colour NAMES (see SceneNode's
# own comment) - these two are the frontend palette's "Mid Gray" body and
# "Blue" header, the closest surviving equivalents to legacy's Mid Gray +
# status_info pairing (there is no status_info token in the new stack).
NOTE_AGENT_BODY_COLOR = "#7a7a7a"
NOTE_AGENT_HEADER_COLOR = "#3f7dc9"

# R6.1: Notes/Frames/Containers - legacy canvas decorations, ported for the
# first time. _recompute_group_bounds (below, on SceneDocument) is plain
# server-side math, NOT a React Flow extent/parentId feature - it computes a
# padded union rect around a frame/container's own members every time
# membership or a member's position changes, so a group visually always
# encloses its members and never clips them (the legacy behavior these
# numbers reproduce). GROUP_PADDING applies to all 4 sides of the union rect;
# GROUP_PADDING_TOP is the (larger) top-edge allowance instead of
# GROUP_PADDING, leaving room for the header/label row every frame/container
# renders. GROUP_MEMBER_DEFAULT_WIDTH/HEIGHT is the last-resort
# per-member footprint estimate, used only for a member the frontend has
# not measured yet (SceneDocument.measured_sizes) AND whose kind carries
# no intrinsic size of its own -
# see _member_footprint (backend/domain/groups.py) for the full fallback
# chain. It used to be what EVERY member was measured by, which is
# precisely why frames did not enclose their contents: no real rendered
# node is 220x120 (a chat node alone is 422 wide and routinely 500+ tall). GROUP_COLLAPSED_WIDTH/HEIGHT is the fixed pill
# size a frame/container shrinks to while is_collapsed.
GROUP_PADDING = 40.0
GROUP_PADDING_TOP = 50.0
GROUP_MEMBER_DEFAULT_WIDTH = 220.0
GROUP_MEMBER_DEFAULT_HEIGHT = 120.0
GROUP_COLLAPSED_WIDTH = 260.0
GROUP_COLLAPSED_HEIGHT = 50.0
# create_frame's member-kind allowlist gate, as an EXCLUDE-list rather than
# an allowlist of the ~12 leaf content kinds: legacy's own createFrame only
# ever accepted leaf content nodes (never a Note, never another Frame or
# Container - see graphlink_scene.py's own selection filter), which this
# reproduces the semantics of without needing to enumerate (and keep in
# sync with) every current or future leaf kind. create_container has no
# equivalent restriction - container membership can legitimately nest
# (a container may hold another container or a frame).
GROUP_INELIGIBLE_FRAME_MEMBER_KINDS = frozenset({"note", "frame", "container"})

# R6.2: Chart node - legacy ChartItem's own MIN_WIDTH/MIN_HEIGHT/MAX_WIDTH/
# MAX_HEIGHT bounds, enforced server-side by resize_chart below (the
# frontend NodeResizer is the one that would normally clamp a drag, but the
# backend is the actual source of truth for the stored size, so it clamps
# independently rather than trusting whatever the client sends).
# DEFAULT_WIDTH/DEFAULT_HEIGHT are NOT repeated here - they live directly as
# ChartState's own dataclass field defaults (backend/domain/node_states.py,
# ADR-002 stage 2.5), and add_chart_node reads them off a freshly-
# constructed node's state rather than a second literal, so the two can
# never drift apart.
CHART_MIN_WIDTH = 440.0
CHART_MIN_HEIGHT = 320.0
CHART_MAX_WIDTH = 2400.0
CHART_MAX_HEIGHT = 1800.0


class SceneError(ValueError):
    """A scene intent referenced something that does not exist or is invalid.
    Raised so the WS layer reports it to the caller instead of crashing."""


class SceneEmptyPromptError(SceneError):
    """R4.4a: a distinct SceneError subclass carrying one extra bit of
    information - the resolved node had no non-whitespace text to use as an
    image-generation prompt. Kept as a real subclass (not a shared message
    string on the base SceneError) so the WS wrapper in register_canvas can
    tell "empty prompt" apart from "wrong kind/unknown node" via isinstance,
    without string-sniffing exception text in production code."""


CHAT_TITLE_PREVIEW_LENGTH = 60
# R3.5: code titles are a language label plus first line, not prose, so a
# shorter preview than chat's 60 is plenty.
CODE_TITLE_PREVIEW_LENGTH = 40
# R3.13: thinking-node titles are prose (a preview of the reasoning text),
# same as chat's, so it reuses chat's 60-char length rather than code's 40.
THINKING_TITLE_PREVIEW_LENGTH = 60
# R3.17: html-node titles preview the raw HTML source, which is prose-like
# text for truncation purposes (not code), so it reuses chat/thinking's
# 60-char length rather than code's 40.
HTML_TITLE_PREVIEW_LENGTH = 60
# R3.21: image-node titles preview the generation prompt, which is prose
# like chat/thinking/html, so it reuses their 60-char length rather than
# code's 40.
IMAGE_TITLE_PREVIEW_LENGTH = 60


@dataclass
class SceneNode:
    id: str
    x: float
    y: float
    title: str
    kind: str = "placeholder"
    # The node's single free-text body. Originally R3.1
    # (doc/QT_REMOVAL_PLAN.md), the chat node's persisted shape -
    # graphlink_session/serializers.py's raw_content, minus everything
    # Qt-only (paint state, scroll position, docked-child widgets).
    #
    # It is NOT chat-only and has not been for a long time. This comment used
    # to end "Unused (default) for every other kind" while a later line in the
    # same block already contradicted it, and the list has kept growing since.
    # TWELVE kinds populate it: artifact, chat, container, document, frame,
    # harness, html, image, note, plan, thinking, web_research. Pinned by
    # tests/test_shared_node_field_docs.py, which DISCOVERS the modules that
    # construct a SceneNode rather than naming them - an earlier revision of
    # this comment said TEN because the gate hard-coded graph.py and
    # session_load.py and never looked at groups.py, where the frame and
    # container nodes are built.
    #
    # That shared use is deliberate, and it is why `content` did not move to a
    # per-kind class in the ADR-002 stage 2.5 migration: a field twelve kinds
    # write would have to be duplicated across twelve state classes to live
    # there, which is worse than one core field. Treat it as core, like
    # title - not as a kind-specific leftover.
    content: str = ""
    is_collapsed: bool = False
    # R3.13 (doc/QT_REMOVAL_PLAN.md): the ThinkingNode/docked-child increment -
    # whether this node is currently docked into its parent's collapsed
    # docked-child slot. A parent's set of docked children is derived at read
    # time from this flag (scan nodes whose parent edge points at it), never
    # stored on the parent itself. Unused (default) for every other kind.
    is_docked: bool = False
    # A node's own message history: a growing list of
    # {"role": "user"|"assistant", "content": text} dicts. Originally R3.25
    # (doc/QT_REMOVAL_PLAN.md), the ConversationNode's persisted shape, ported
    # from graphlink_conversation_node.py's conversation_history.
    #
    # It is NOT conversation-only and has not been since the plugin kinds
    # landed. This comment used to say "Unused (default empty list) for every
    # other kind"; verified 2026-09-04 against the restorers in
    # every module under backend/ that builds a SceneNode, SEVEN kinds
    # populate it: artifact, chat, code_sandbox, conversation, gitlink, html,
    # web_research. Any kind that holds a back-and-forth with a model keeps it
    # here. Pinned by tests/test_shared_node_field_docs.py.
    #
    # Shared for the same reason `content` is, and left on SceneNode by the
    # ADR-002 stage 2.5 migration for the same reason - see that field's own
    # note above. backend/domain/node_states.py's docstring is right to list
    # `conversation` among the kinds with no state class: its history is not a
    # kind-specific field, it is this shared one.
    history: list[dict[str, str]] = field(default_factory=list)
    # R4.3 (doc/QT_REMOVAL_PLAN.md): transient per-node in-flight-request
    # marker - the id of the AgentDispatcher request currently generating a
    # reply for this node, or None when idle. Generic across any kind that
    # ever gets its own real dispatch slot (not conversation-only, the same
    # way is_docked is a generic field even though today only one kind
    # populates it); unused (default None) for every other kind.
    pending_request_id: str | None = None
    # R6.1: Notes/Frames/Containers - shared color override for note/frame/
    # container kinds. Hex string like "#4a7c59"; None means "use the kind's
    # own default color", a rendering fallback that is entirely the
    # frontend's job (canvas.py never resolves a color itself, same posture
    # as gitlink_context_xml being opaque server-held data the frontend
    # interprets). Unused (default None) for every other kind.
    color: str | None = None
    # Header/title-bar color override, settable independently from the body
    # `color` above (a frame/container's header row can be tinted separately
    # from its body fill) - None means "derive from color/default", again
    # entirely a frontend rendering decision. Unused for every other kind.
    header_color: str | None = None
    # frame/container membership: the ids of the member nodes this group
    # currently encloses. In this implementation a node can be a member of
    # AT MOST ONE frame AND AT MOST ONE container simultaneously (never two
    # frames, never two containers) - create_frame/create_container's own
    # detach-from-existing-same-kind-group rule enforces this (see below).
    # ALSO reused (ADR-002 Workstream 1) by an is_branch_comparison note to
    # record the source chat-node ids it was compared from - a second,
    # unrelated "list of node ids this node references" use of the same
    # field rather than a new one, exactly like frame/container's own
    # membership use above. Unused (default empty list) for every other
    # case.
    item_ids: list[str] = field(default_factory=list)
    # ADR-002 stage 2.5 (backend-only): the typed per-kind payload - see
    # backend/domain/node_states.py's own docstring. None for a kind not
    # yet migrated, or for a kind (placeholder/thinking/conversation) that
    # has no kind-specific fields at all; otherwise the NodeState subclass
    # matching this node's own kind (e.g. ImageState for kind="image").
    state: NodeState | None = None


@dataclass
class SceneEdge:
    id: str
    source: str
    target: str
