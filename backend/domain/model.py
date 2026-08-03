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
from typing import Any

from backend.domain.node_states import NodeState

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

# ADR-002 Workstream 1: how far apart real branch siblings (2+ chat-kind
# children of the same parent, from "Branch from here") fan out
# horizontally, so a genuine divergence doesn't render as two nodes stacked
# exactly on top of each other. 460px clears a chat node's own current CSS
# width (420px, styles.css's .chat-node) with room to spare, same "clears
# the node's width, not just MESSAGE_VERTICAL_SPACING" reasoning the Key
# Takeaway/Explainer Note offset just below already uses.
BRANCH_HORIZONTAL_SPACING = 460

# R8a: where a generated Key Takeaway / Explainer Note lands relative to its
# source chat node, and how it is tinted. 400px clears a chat node's own
# width (~292px) with room to spare, matching the legacy offset. The colours
# are hex because the backend never resolves colour NAMES (see SceneNode's
# own comment) - these two are the frontend palette's "Mid Gray" body and
# "Blue" header, the closest surviving equivalents to legacy's Mid Gray +
# status_info pairing (there is no status_info token in the new stack).
NOTE_AGENT_X_OFFSET = 400
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
# renders. GROUP_MEMBER_DEFAULT_WIDTH/HEIGHT is the flat per-member footprint
# estimate used when a member's own kind has no width/height field of its
# own - true of every one of the 12 existing SceneNode kinds today (none
# carries one), so this is always what is actually used in practice, not
# just a defensive fallback. GROUP_COLLAPSED_WIDTH/HEIGHT is the fixed pill
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
    # R3.1 (doc/QT_REMOVAL_PLAN.md): the chat node's real persisted shape -
    # graphlink_session/serializers.py's raw_content/is_user/is_collapsed,
    # minus everything Qt-only (paint state, scroll position, docked-child
    # widgets). Unused (default) for every other kind.
    # R3.17: also reused verbatim as the html node's raw HTML source string -
    # no separate field, same reuse pattern as R3.5's code text and R3.13's
    # thinking text living in this same field.
    content: str = ""
    is_collapsed: bool = False
    # R3.13 (doc/QT_REMOVAL_PLAN.md): the ThinkingNode/docked-child increment -
    # whether this node is currently docked into its parent's collapsed
    # docked-child slot. A parent's set of docked children is derived at read
    # time from this flag (scan nodes whose parent edge points at it), never
    # stored on the parent itself. Unused (default) for every other kind.
    is_docked: bool = False
    # R3.25 (doc/QT_REMOVAL_PLAN.md): the ConversationNode's real persisted
    # shape - graphlink_conversation_node.py's conversation_history, a
    # growing list of {"role": "user"|"assistant", "content": text} dicts
    # rendered as multiple bubbles inside one node card. This is the one R3
    # kind whose OWN field is a LIST rather than a scalar - every prior kind
    # (chat/code/document/thinking/html/image) stores one scalar value per
    # node; a conversation node instead owns a whole message history. Unused
    # (default empty list) for every other kind.
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

    # -- ADR-002 stage 2.5 PR8a: transitional gitlink property shim --------
    #
    # GitlinkState (backend/domain/node_states.py) now owns all 19 gitlink_*
    # fields - see that class's own docstring for what each one means. The
    # properties below exist ONLY so backend/agents.py, backend/api/
    # intents_gitlink.py, and the existing test suite keep working completely
    # unchanged for the rest of this PR; each is a plain, unconditional
    # delegation to self.state. Safe by construction, not by luck: every real
    # `node.gitlink_x` call site was swept before this PR and is only ever
    # reached on a node already established as kind="gitlink" (no getattr/
    # hasattr-style defensive access anywhere touches these fields). Removed
    # in the shim-removal follow-up PR, once every external site is converted
    # to `.state.gitlink_x` directly and "gitlink" is added to
    # tests/test_node_state_migration.py's MIGRATED_KIND_FIELDS.

    @property
    def gitlink_repo(self) -> str:
        return self.state.gitlink_repo

    @gitlink_repo.setter
    def gitlink_repo(self, value: str) -> None:
        self.state.gitlink_repo = value

    @property
    def gitlink_branch(self) -> str:
        return self.state.gitlink_branch

    @gitlink_branch.setter
    def gitlink_branch(self, value: str) -> None:
        self.state.gitlink_branch = value

    @property
    def gitlink_scope_mode(self) -> str:
        return self.state.gitlink_scope_mode

    @gitlink_scope_mode.setter
    def gitlink_scope_mode(self, value: str) -> None:
        self.state.gitlink_scope_mode = value

    @property
    def gitlink_local_root(self) -> str:
        return self.state.gitlink_local_root

    @gitlink_local_root.setter
    def gitlink_local_root(self, value: str) -> None:
        self.state.gitlink_local_root = value

    @property
    def gitlink_imported_root(self) -> str:
        return self.state.gitlink_imported_root

    @gitlink_imported_root.setter
    def gitlink_imported_root(self, value: str) -> None:
        self.state.gitlink_imported_root = value

    @property
    def gitlink_repo_file_paths(self) -> list[str]:
        return self.state.gitlink_repo_file_paths

    @gitlink_repo_file_paths.setter
    def gitlink_repo_file_paths(self, value: list[str]) -> None:
        self.state.gitlink_repo_file_paths = value

    @property
    def gitlink_selected_paths(self) -> list[str]:
        return self.state.gitlink_selected_paths

    @gitlink_selected_paths.setter
    def gitlink_selected_paths(self, value: list[str]) -> None:
        self.state.gitlink_selected_paths = value

    @property
    def gitlink_task_prompt(self) -> str:
        return self.state.gitlink_task_prompt

    @gitlink_task_prompt.setter
    def gitlink_task_prompt(self, value: str) -> None:
        self.state.gitlink_task_prompt = value

    @property
    def gitlink_context_xml(self) -> str:
        return self.state.gitlink_context_xml

    @gitlink_context_xml.setter
    def gitlink_context_xml(self, value: str) -> None:
        self.state.gitlink_context_xml = value

    @property
    def gitlink_context_stats(self) -> dict[str, str]:
        return self.state.gitlink_context_stats

    @gitlink_context_stats.setter
    def gitlink_context_stats(self, value: dict[str, str]) -> None:
        self.state.gitlink_context_stats = value

    @property
    def gitlink_context_summary(self) -> str:
        return self.state.gitlink_context_summary

    @gitlink_context_summary.setter
    def gitlink_context_summary(self, value: str) -> None:
        self.state.gitlink_context_summary = value

    @property
    def gitlink_context_version(self) -> int:
        return self.state.gitlink_context_version

    @gitlink_context_version.setter
    def gitlink_context_version(self, value: int) -> None:
        self.state.gitlink_context_version = value

    @property
    def gitlink_proposal_markdown(self) -> str:
        return self.state.gitlink_proposal_markdown

    @gitlink_proposal_markdown.setter
    def gitlink_proposal_markdown(self, value: str) -> None:
        self.state.gitlink_proposal_markdown = value

    @property
    def gitlink_pending_changes(self) -> list[dict[str, Any]]:
        return self.state.gitlink_pending_changes

    @gitlink_pending_changes.setter
    def gitlink_pending_changes(self, value: list[dict[str, Any]]) -> None:
        self.state.gitlink_pending_changes = value

    @property
    def gitlink_preview_text(self) -> str:
        return self.state.gitlink_preview_text

    @gitlink_preview_text.setter
    def gitlink_preview_text(self, value: str) -> None:
        self.state.gitlink_preview_text = value

    @property
    def gitlink_change_fingerprint(self) -> str | None:
        return self.state.gitlink_change_fingerprint

    @gitlink_change_fingerprint.setter
    def gitlink_change_fingerprint(self, value: str | None) -> None:
        self.state.gitlink_change_fingerprint = value

    @property
    def gitlink_change_local_root(self) -> str | None:
        return self.state.gitlink_change_local_root

    @gitlink_change_local_root.setter
    def gitlink_change_local_root(self, value: str | None) -> None:
        self.state.gitlink_change_local_root = value

    @property
    def gitlink_change_state(self) -> str:
        return self.state.gitlink_change_state

    @gitlink_change_state.setter
    def gitlink_change_state(self, value: str) -> None:
        self.state.gitlink_change_state = value

    @property
    def gitlink_error(self) -> str:
        return self.state.gitlink_error

    @gitlink_error.setter
    def gitlink_error(self, value: str) -> None:
        self.state.gitlink_error = value

    # -- ADR-002 stage 2.5 PR9a: transitional pycoder property shim --------
    #
    # PycoderState (backend/domain/node_states.py) now owns all 9 pycoder_*
    # fields - see that class's own docstring. Same transitional shim as the
    # gitlink block above: exists ONLY so backend/agents.py and the existing
    # test suite keep working completely unchanged for the rest of this PR.
    # Removed in the shim-removal follow-up PR, once every external site is
    # converted to `.state.pycoder_x` directly and "pycoder" is added to
    # tests/test_node_state_migration.py's MIGRATED_KIND_FIELDS.

    @property
    def pycoder_mode(self) -> str:
        return self.state.pycoder_mode

    @pycoder_mode.setter
    def pycoder_mode(self, value: str) -> None:
        self.state.pycoder_mode = value

    @property
    def pycoder_prompt(self) -> str:
        return self.state.pycoder_prompt

    @pycoder_prompt.setter
    def pycoder_prompt(self, value: str) -> None:
        self.state.pycoder_prompt = value

    @property
    def pycoder_code(self) -> str:
        return self.state.pycoder_code

    @pycoder_code.setter
    def pycoder_code(self, value: str) -> None:
        self.state.pycoder_code = value

    @property
    def pycoder_output(self) -> str:
        return self.state.pycoder_output

    @pycoder_output.setter
    def pycoder_output(self, value: str) -> None:
        self.state.pycoder_output = value

    @property
    def pycoder_analysis(self) -> str:
        return self.state.pycoder_analysis

    @pycoder_analysis.setter
    def pycoder_analysis(self, value: str) -> None:
        self.state.pycoder_analysis = value

    @property
    def pycoder_last_run_failed(self) -> bool:
        return self.state.pycoder_last_run_failed

    @pycoder_last_run_failed.setter
    def pycoder_last_run_failed(self, value: bool) -> None:
        self.state.pycoder_last_run_failed = value

    @property
    def pycoder_awaiting_approval(self) -> bool:
        return self.state.pycoder_awaiting_approval

    @pycoder_awaiting_approval.setter
    def pycoder_awaiting_approval(self, value: bool) -> None:
        self.state.pycoder_awaiting_approval = value

    @property
    def pycoder_approved_fingerprint(self) -> str | None:
        return self.state.pycoder_approved_fingerprint

    @pycoder_approved_fingerprint.setter
    def pycoder_approved_fingerprint(self, value: str | None) -> None:
        self.state.pycoder_approved_fingerprint = value

    @property
    def pycoder_error(self) -> str:
        return self.state.pycoder_error

    @pycoder_error.setter
    def pycoder_error(self, value: str) -> None:
        self.state.pycoder_error = value

    # -- ADR-002 stage 2.5 PR10a: transitional code_sandbox property shim --
    #
    # CodeSandboxState (backend/domain/node_states.py) now owns all 10
    # code_sandbox_* fields - see that class's own docstring. Same
    # transitional shim as the gitlink/pycoder blocks above: exists ONLY so
    # backend/agents.py and the existing test suite keep working completely
    # unchanged for the rest of this PR. Removed in the shim-removal
    # follow-up PR, once every external site is converted to
    # `.state.code_sandbox_x` directly and "code_sandbox" is added to
    # tests/test_node_state_migration.py's MIGRATED_KIND_FIELDS.

    @property
    def code_sandbox_sandbox_id(self) -> str:
        return self.state.code_sandbox_sandbox_id

    @code_sandbox_sandbox_id.setter
    def code_sandbox_sandbox_id(self, value: str) -> None:
        self.state.code_sandbox_sandbox_id = value

    @property
    def code_sandbox_requirements(self) -> str:
        return self.state.code_sandbox_requirements

    @code_sandbox_requirements.setter
    def code_sandbox_requirements(self, value: str) -> None:
        self.state.code_sandbox_requirements = value

    @property
    def code_sandbox_prompt(self) -> str:
        return self.state.code_sandbox_prompt

    @code_sandbox_prompt.setter
    def code_sandbox_prompt(self, value: str) -> None:
        self.state.code_sandbox_prompt = value

    @property
    def code_sandbox_code(self) -> str:
        return self.state.code_sandbox_code

    @code_sandbox_code.setter
    def code_sandbox_code(self, value: str) -> None:
        self.state.code_sandbox_code = value

    @property
    def code_sandbox_output(self) -> str:
        return self.state.code_sandbox_output

    @code_sandbox_output.setter
    def code_sandbox_output(self, value: str) -> None:
        self.state.code_sandbox_output = value

    @property
    def code_sandbox_analysis(self) -> str:
        return self.state.code_sandbox_analysis

    @code_sandbox_analysis.setter
    def code_sandbox_analysis(self, value: str) -> None:
        self.state.code_sandbox_analysis = value

    @property
    def code_sandbox_awaiting_approval(self) -> bool:
        return self.state.code_sandbox_awaiting_approval

    @code_sandbox_awaiting_approval.setter
    def code_sandbox_awaiting_approval(self, value: bool) -> None:
        self.state.code_sandbox_awaiting_approval = value

    @property
    def code_sandbox_approval_requirements(self) -> str:
        return self.state.code_sandbox_approval_requirements

    @code_sandbox_approval_requirements.setter
    def code_sandbox_approval_requirements(self, value: str) -> None:
        self.state.code_sandbox_approval_requirements = value

    @property
    def code_sandbox_approved_fingerprint(self) -> str | None:
        return self.state.code_sandbox_approved_fingerprint

    @code_sandbox_approved_fingerprint.setter
    def code_sandbox_approved_fingerprint(self, value: str | None) -> None:
        self.state.code_sandbox_approved_fingerprint = value

    @property
    def code_sandbox_error(self) -> str:
        return self.state.code_sandbox_error

    @code_sandbox_error.setter
    def code_sandbox_error(self, value: str) -> None:
        self.state.code_sandbox_error = value


@dataclass
class SceneEdge:
    id: str
    source: str
    target: str
