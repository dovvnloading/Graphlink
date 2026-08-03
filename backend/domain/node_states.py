"""ADR-002 stage 2.5 (backend-only): typed per-kind SceneNode state.

SceneNode used to carry all ~95 fields for every one of its 16 kinds
regardless of which kind actually used them. This module holds one
dataclass per kind - only the fields that kind actually uses - attached to
SceneNode.state. Migration proceeds kind-by-kind (see each state class's
own relocation note); a kind not yet migrated still keeps its fields
directly on SceneNode.

Wire-compatibility constraint: SceneDocument.scene_payload() must keep
emitting the exact same flat per-node shape it does today. This module
introduces no wire-layer change by itself - it only relocates where a
field lives in memory; scene_payload's own per-kind read expressions are
updated in lockstep with each kind's migration, in backend/domain/graph.py.

kind values with no state class (no kind-specific fields at all, so
node.state stays None for them permanently): placeholder, thinking,
conversation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class NodeState:
    """Marker base for all per-kind node state payloads."""


@dataclass
class ImageState(NodeState):
    """Relocated verbatim from SceneNode.image_asset_id (former
    backend/domain/model.py field, R3.21) - the opaque reference key into
    SceneDocument.image_assets. See that dict's own docstring for the
    transport-decision reasoning (image bytes never live on the node
    itself)."""

    image_asset_id: str = ""


@dataclass
class HtmlState(NodeState):
    """Relocated verbatim from SceneNode.html_splitter_state (former
    backend/domain/model.py field, R6.3) - an HtmlViewNode's persisted
    draggable code/preview splitter position. None means "use the
    frontend's own default", not "0" - a real 0.0 position (fully
    collapsed to one side) must round-trip distinctly from "never set"."""

    html_splitter_state: float | None = None


@dataclass
class ArtifactState(NodeState):
    """Relocated verbatim from SceneNode.artifact_content (former
    backend/domain/model.py field, R5.2) - the Artifact/Drafter node's
    whole-document text. The model returns the WHOLE document every turn
    (never a diff/patch - see complete_artifact_generation), so this
    field is bounded by the model's own per-turn output ceiling, not by
    session length. The turn-by-turn conversation reuses SceneNode's own
    generic `history` list field (already used by ConversationNode)
    rather than a second list-typed field here - only this one scalar is
    needed."""

    artifact_content: str = ""


@dataclass
class CodeState(NodeState):
    """Relocated verbatim from SceneNode.code/SceneNode.language (former
    backend/domain/model.py fields, R3.5) - a code-block node's raw text
    and its declared language label (used for both the title's language
    prefix and the frontend's syntax-highlighting choice)."""

    code: str = ""
    language: str = ""


@dataclass
class NoteState(NodeState):
    """Relocated verbatim from SceneNode.is_system_prompt/is_summary_note/
    is_branch_comparison (former backend/domain/model.py fields) - a
    note's three mutually-independent badge flags. is_system_prompt/
    is_summary_note are the legacy system-prompt/summary-note badges;
    is_branch_comparison (ADR-002 Workstream 1, "Compare Branches") marks
    a note as the output of the Compare Branches agent - deliberately a
    separate flag from is_summary_note rather than reusing it, since that
    flag is legacy's own unrelated "Group Summary" concept."""

    is_system_prompt: bool = False
    is_summary_note: bool = False
    is_branch_comparison: bool = False


@dataclass
class DocumentState(NodeState):
    """Relocated verbatim from SceneNode's six document-attachment fields
    (former backend/domain/model.py fields, R3.9) - graphlink_scene.py's
    add_document_node()/graphlink_node_document.py's DocumentNode.__init__
    attachment metadata (title/content live on SceneNode's own core).
    Stored VERBATIM, exactly as passed in - none of the legacy view-layer
    formatting below happens here; reproducing it is the frontend's job
    (same as the paint()/menu code it replaces). Formatting rules
    extracted from graphlink_nodes/graphlink_node_document.py +
    graphlink_audio.py, for the frontend to reproduce exactly in
    TypeScript:

    - Byte-size formatting (DocumentNode._format_byte_size): if byte_size
      is falsy (None or 0) -> "Unknown". Else repeatedly divide by 1024.0
      walking units ("B","KB","MB","GB","TB"), stopping at the first unit
      where size < 1024.0 (or unit == "TB"); "B" formats as a bare integer
      ("512 B"), every other unit formats with exactly one decimal place
      ("1.5 MB").
    - Duration formatting (graphlink_audio.format_duration): None ->
      "Unknown". Else round(seconds) to the nearest whole second, clamp
      negative to 0, divmod into hours/minutes/seconds; if hours > 0 format
      "H:MM:SS" (hours unpadded, minutes/seconds zero-padded to 2 digits),
      else "M:SS" (minutes unpadded, seconds zero-padded).
    - Metadata rows (DocumentNode._build_metadata_rows), in this exact
      order, each omitted entirely when its value is empty/None: Type
      ("Audio file" if attachment_kind=="audio" else "Document", always
      present) / Duration (formatted, only if duration_seconds is not
      None) / Format (mime_type, only if truthy) / Size (formatted byte
      size, only if byte_size is truthy) / Path (file_path, only if
      truthy).
    - preview_label auto-fill (DocumentNode._build_preview_label), used
      only when the caller didn't supply one: for attachment_kind=="audio"
      -> "Audio | {duration formatted, or 'Audio' if duration_seconds is
      None}"; otherwise derived from title's file extension via
      os.path.splitext: ".pdf" -> "PDF", ".docx" -> "DOCX", any other
      extension -> that extension uppercased without its dot, no extension
      -> "Document".
    - Audio-preview-suppression heuristic
      (DocumentNode._should_show_audio_preview): normalize both `content`
      and the auto-built `audio_details` block the same way (join
      right-stripped lines with "\\n", strip the whole string, lowercase).
      Hide the content-preview panel (show only the metadata table) when:
      normalized content is empty; OR normalized content == normalized
      audio_details (content is nothing but the auto-generated metadata
      block); OR normalized content startswith "audio attachment" AND
      contains "duration:" (catches legacy-saved sessions whose persisted
      content is an older/differently-valued metadata block). Otherwise
      show the preview. `audio_details` itself is the joined lines: "Audio
      attachment", then "Duration: {formatted}" if duration_seconds is not
      None, "Format: {mime_type}" if truthy, "Size: {formatted byte size}"
      if byte_size truthy, "Path: {file_path}" if truthy - same
      presence/order rules as the metadata rows above."""

    attachment_kind: str = ""
    file_path: str = ""
    mime_type: str = ""
    duration_seconds: float | None = None
    byte_size: int | None = None
    preview_label: str = ""


@dataclass
class WebResearchState(NodeState):
    """Relocated verbatim from SceneNode's six web-research fields (former
    backend/domain/model.py fields, R5.1) - one research run's live
    progress/outcome. SceneNode's own `content` field (reused, same
    pattern as code/thinking/html) holds the query text - not duplicated
    here. research_stage is one of the empty-string sentinel ("" - never
    run) or the 9 ResearchStage enum values from
    graphlink_plugins/web_research/domain.py's own .value strings:
    "preparing" | "searching" | "fetching" | "extracting" | "validating" |
    "synthesizing" | "completed" | "cancelled" | "failed".
    research_result is the wire-shaped (camelCase) ResearchResult, or None
    before the first run ever completes - deliberate stale-while-
    revalidate (a NEW run does not clear this on start; see
    start_web_research_run's own docstring)."""

    research_stage: str = ""
    research_completed: int = 0
    research_total: int = 0
    research_active_source_id: str | None = None
    research_error: str = ""
    research_result: dict | None = None


@dataclass
class ChartState(NodeState):
    """Relocated verbatim from SceneNode's nine chart fields (former
    backend/domain/model.py fields, R6.2) - graphlink_canvas_chart_item.py's
    ChartItem, ported to a backend-rendered PNG (see
    graphlink_chart_rendering.py's own module docstring for why -
    matplotlib+FigureCanvasAgg was already Qt-free upstream; only the
    QImage-wrapping step needed replacing).

    - chart_type: one of SUPPORTED_CHART_TYPES (graphlink_chart_data.py):
      "bar" | "line" | "pie" | "histogram" | "sankey".
    - chart_data: ALREADY-canonicalized (canonicalize_chart_data's own
      output shape) - add_chart_node does NOT call canonicalize_chart_data
      itself; the caller (backend/agents.py's generateChart intent, via
      AgentDispatcher.start_chart_generation) is responsible for that, so
      it can catch ChartDataError itself and still create a placeholder
      chart with chart_error set on failure, matching legacy's never-
      hard-fail contract, rather than add_chart_node raising and aborting
      node creation entirely.
    - chart_error: non-empty if generation/canonicalization degraded to a
      placeholder - the chart still has a real (if minimal) chart_asset_id
      and renders SOMETHING, never a blank/broken state (mirrors
      ChartDataAgent's own get_response/repair_chart_data/
      heuristic_chart_data degrade-gracefully chain, which never
      hard-fails outright for a genuine LLM response).
    - chart_asset_id: opaque key into the EXISTING
      SceneDocument.image_assets dict (REUSED, not a parallel store - same
      dict R3.21's image nodes already use, see that field's own
      transport-decision comment on image_assets) - the rendered
      display-resolution PNG. Export (the 3x-resolution download)
      re-renders fresh rather than reading this asset - see
      backend/assets.py.
    - chart_asset_version: incremented every time chart_asset_id's bytes
      are (re)written (by add_chart_node's initial render, or
      resize_chart's re-render) - lets the frontend cache-bust the <img>
      src with a version query param after a resize re-render, since the
      asset id itself never changes.
    - chart_width/chart_height: default to legacy ChartItem.DEFAULT_WIDTH/
      DEFAULT_HEIGHT - add_chart_node reads these dataclass defaults
      directly off a freshly-constructed node's state (not a second
      literal) when rendering a freshly-created chart's first PNG, so the
      two can never drift apart. resize_chart clamps any later change into
      [CHART_MIN_WIDTH, CHART_MAX_WIDTH] / [CHART_MIN_HEIGHT,
      CHART_MAX_HEIGHT] (backend/domain/model.py).
    - chart_aspect_locked: legacy ChartItem.aspect_ratio_locked's own
      default (True, unlike most bool fields in this module which default
      False) - resize_chart consults this to decide whether to re-derive a
      dimension after clamping; toggle_chart_aspect_lock flips it without
      touching size or re-rendering.
    - chart_source_node_id: provenance - which node's content the chart
      data was generated from, always the parent branch-point edge's
      source in this implementation (legacy's rarer "different node" case
      is a known, accepted simplification, not replicated)."""

    chart_type: str = ""
    chart_data: dict = field(default_factory=dict)
    chart_error: str = ""
    chart_asset_id: str = ""
    chart_asset_version: int = 0
    chart_width: float = 680.0
    chart_height: float = 500.0
    chart_aspect_locked: bool = True
    chart_source_node_id: str = ""


@dataclass
class FrameState(NodeState):
    """Relocated verbatim from SceneNode's frame-only fields (former
    backend/domain/model.py fields, R6.1).

    - is_locked: legacy default is LOCKED (True), unlike every other bool
      field in this module (which all default False). Containers have no
      lock concept at all - see create_container's own docstring for why
      no lock toggle is exposed for them.
    - group_manual_width/group_manual_height: the MANUAL resize override
      recorded by resize_frame, cleared back to None by
      fit_frame_to_content. This pair (unlike group_width/group_height
      below) is the single, stable source of truth for "is this frame
      currently manually sized": it is NEVER auto-populated by
      _recompute_group_bounds's own auto-fit branch, only ever written by
      resize_frame/fit_frame_to_content, so its None-ness survives a
      collapse/expand round-trip untouched (unlike group_width/
      group_height, which DO get temporarily overwritten with the fixed
      collapsed-pill size while is_collapsed - see toggle_group_collapsed).
      Deliberately excluded from scene_payload()/the wire - pure internal
      bookkeeping, mirrors gitlink_imported_root's own "server-side only"
      precedent.
    - group_manual_x/group_manual_y: the position counterpart to
      group_manual_width/height above - set by move_node whenever a frame
      is dragged directly (locked whole-group drag OR an independently-
      dragged unlocked frame), cleared back to None by
      fit_frame_to_content. Exists so an unlocked frame's own drag
      actually sticks - without an explicit anchor, the very next member
      move would recompute the frame straight back to bbox-of-members-
      centered, silently undoing the drag (legacy let an unlocked frame's
      outline be repositioned independently of its members; this is that
      same capability, ported). See _recompute_group_bounds for
      how this anchor is unioned with the live bbox so it still can never
      clip a member, matching legacy's own rect.united() guarantee. Same
      wire/kind-scoping posture as group_manual_width/height above
      (server-side only).
    - group_width/group_height: the frame's current effective on-canvas
      size, kept live by _recompute_group_bounds - the fixed
      GROUP_COLLAPSED_WIDTH/HEIGHT pill while is_collapsed, else
      group_manual_width/height verbatim while a manual override is
      active, else the padded bbox-of-members auto-fit size. THIS is the
      pair the wire contract names group_width/group_height and exposes as
      groupWidth/groupHeight - group_manual_width/height above exist
      purely so a frame's manual override survives a collapse/expand
      round-trip without the collapsed-pill overwrite (this pair)
      destroying it.

    NOT shared with ContainerState despite group_width/group_height being
    common to both: is_locked/group_manual_* are explicitly meaningless
    for containers (no lock concept, no manual-resize capability - no
    resize_container method exists), so forcing them onto a shared base
    would resurrect the exact "every kind carries fields it never uses"
    problem this migration exists to remove."""

    is_locked: bool = True
    group_manual_width: float | None = None
    group_manual_height: float | None = None
    group_manual_x: float | None = None
    group_manual_y: float | None = None
    group_width: float | None = None
    group_height: float | None = None


@dataclass
class ContainerState(NodeState):
    """Relocated verbatim from SceneNode's group_width/group_height fields
    (former backend/domain/model.py fields, R6.1), as they apply to
    container kind specifically - see FrameState's own docstring for why
    this is a separate class rather than a shared base with FrameState,
    despite the field-name overlap."""

    group_width: float | None = None
    group_height: float | None = None


@dataclass
class ChatState(NodeState):
    """Relocated verbatim from SceneNode's eight chat-only fields (former
    backend/domain/model.py fields). SceneNode's own `content` field
    (core, not duplicated here) holds the chat message text - is_user/
    content_parts describe HOW that text should render, never a second
    copy of it.

    - is_user: True for a user-authored message, False for an assistant
      reply - graphlink_session/serializers.py's own raw_content/is_user/
      is_collapsed shape, minus everything Qt-only (paint state, scroll
      position, docked-child widgets).
    - chat_scroll_value: persisted scroll position within this node's own
      content area (legacy's own scroll_value field). Unlike HtmlState's
      own html_splitter_state, 0.0 (scrolled to the top) IS the genuine
      default for a node that has never been scrolled, so this is a plain
      float, not an Optional - there is no "unset" state worth
      distinguishing here.
    - content_parts: the RAW (already-decoded - "data" holds real Python
      bytes, never base64 text) multimodal parts list for a chat node
      whose legacy raw_content was a list of typed parts (e.g. an inline
      pasted image) rather than a plain string - see content_codec.py's
      own process_content_for_serialization/_for_deserialization, which
      this class does not call directly (see scene_payload()'s own
      comment for the wire-side base64 encoding step this field feeds).
      None for the overwhelmingly common plain-text case, where `content`
      is the only source of truth. ADDITIVE, not a replacement for
      `content`: even when this is populated, `content` continues to hold
      a flattened-text mirror (join of the text-type parts, or a
      placeholder like "[Image]" for non-text parts) so every existing
      piece of code that already reads `content` as a plain string keeps
      working unchanged.
    - provider/model: the provider/model that produced this node's
      content, e.g. "Anthropic Claude" / "claude-sonnet-5" - resolved from
      ComposerDocument.route() at creation time. Not literally restricted
      to synthesize_branches specifically, even though today only that one
      flow populates it - any future agent-authored chat node could
      reasonably want the same provenance recorded. None means "not
      recorded" (every node created before this field existed, and every
      ordinary chat reply, which already shows its route live in the
      Composer rather than per-message).
    - is_branch_synthesis: ADR-002 Workstream 1 ("Synthesize Branches") -
      marks this node as the output of the Synthesize Branches agent (as
      opposed to an ordinary user/assistant message) - the chat-node
      equivalent of NoteState's own is_branch_comparison, which does the
      same job for note-kind nodes. A distinct flag rather than reusing
      is_branch_comparison: that flag's own kind-check
      (mark_branch_comparison_note raises for a non-note node) would need
      loosening for no benefit, and the two features render completely
      different UI (a badge on a note vs. a badge + the instructions/
      provider/model fields on a chat node).
    - synthesis_instructions: the free-text instructions the user typed to
      steer the synthesis (e.g. "merge the best parts of each"), recorded
      on the result node so its provenance is fully inspectable later -
      the ADR's own acceptance criterion for this feature.
    - branch_status: ADR-002 Workstream 1 ("Branch status and lifecycle") -
      the final, sequenced item after fork/compare/synthesize. One of
      exactly "active" (the default - every existing and newly-created
      chat node starts here), "accepted", "rejected", "superseded".
      Chat-kind only, mirroring is_branch_synthesis's own chat-only
      scoping - "a branch" is fundamentally a chain of chat nodes in this
      data model (see chat_branch_history/get_branch_root, both of which
      only ever walk chat-kind edges). Deliberately PER-NODE with NO
      write-time inheritance/cascade to ancestors or descendants - there
      is no materialized "Branch" object anywhere to cascade through even
      if inheritance were wanted (a branch is discovered by walking
      _branch_parent_edge upward on demand, never stored as a set).
      "Reduce a graph to its accepted paths" is delivered by a separate,
      frontend-only, read-time subtree derivation over this field
      (SceneCanvas.tsx's own computeNonAcceptedNodeIds) - the same posture
      "Hide Other Branches" already uses for a different subtree question,
      not by inventing write-time cascade bookkeeping here."""

    is_user: bool = False
    chat_scroll_value: float = 0.0
    content_parts: list[dict[str, Any]] | None = None
    provider: str | None = None
    model: str | None = None
    is_branch_synthesis: bool = False
    synthesis_instructions: str = ""
    branch_status: str = "active"
