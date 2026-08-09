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
class GitlinkState(NodeState):
    """Relocated verbatim from SceneNode's nineteen gitlink fields (former
    backend/domain/model.py fields, R5.3) - the Gitlink node's real
    persisted shape: reads a GitHub repo (or a local checkout) into
    structured XML context, proposes an LLM change set, and only writes to
    disk after an explicit, fingerprint-verified approval.

    - gitlink_repo/gitlink_branch/gitlink_scope_mode/gitlink_local_root:
      the node's live repo/branch/scope-mode/local-checkout-path
      selection, set by store_gitlink_repo_tree, store_gitlink_snapshot_
      root, set_gitlink_local_root and store_gitlink_context (backend/
      domain/graph.py).
    - gitlink_imported_root: mirrors legacy repo_state["imported_root"] -
      remembers which local path a prior Import Repo Snapshot produced, so
      a later run can reuse it without re-downloading. Server-side
      bookkeeping ONLY: deliberately absent from scene_payload()/
      SceneNodeRow - there is no wire field for it, nothing on the
      frontend ever needs to read it directly (gitlink_local_root is
      what's shown/edited).
    - gitlink_repo_file_paths/gitlink_selected_paths: the scanned
      text-file path list and the user's Build-Context selection out of
      it.
    - gitlink_task_prompt: the natural-language ask for the current
      Generate Change Set run.
    - gitlink_context_xml: DESIGNED ceiling of 180,000 chars (repository.
      py's MAX_CONTEXT_CHARS) - an order of magnitude above this state's
      other fields' implicit ceilings. scene_payload() resends every node
      on roughly 20 undebounced triggers (see SceneDocument.image_assets'
      own comment) - inlining a 180KB text blob there would reproduce
      that exact cost on every unrelated mutation for the rest of the
      session. EXCLUDED from scene_payload() on purpose; served on demand
      via the read-only fetchGitlinkContext intent instead (see
      fetch_gitlink_context_xml, backend/domain/graph.py). Deleted
      automatically when the node is deleted - no separate eviction
      bookkeeping needed (unlike image_assets, this never leaves this
      dataclass instance).
    - gitlink_context_stats: repository.py's build_context_bundle returns
      a mixed int/str dict (scanned_files/loaded_files/included_files/
      load_errors/context_omissions are ints; source_root/summary are
      strings) - store_gitlink_context stringifies every value before
      assigning here so the wire field this feeds (scene_payload()'s
      "gitlinkContextStats") stays honestly dict[str, str] end to end,
      matching how graphlink_scene_payload.py's SceneNodeRow types it for
      codegen. DEVIATION from a literal-verbatim forward: unlike R5.1's
      providerSnapshot (typed dict[str, str] but always populated as {}
      at runtime, so the type is never really exercised),
      gitlink_context_stats IS genuinely populated with int values at
      runtime - forwarding it unmodified would make the generated
      validateSceneState() reject every real context-build result. The
      str-coercion in store_gitlink_context is load-bearing, not a
      defensive formality.
    - gitlink_context_summary: built purely from aggregate file counts
      (repository.py's build_context_bundle), never from paths/content -
      two different Build Context results (e.g. selecting a different
      single file each time) can produce an IDENTICAL summary string.
    - gitlink_context_version: R5.3 post-review FIX 6 - a genuine
      MONOTONIC per-node counter, incremented unconditionally every time
      store_gitlink_context lands a successful Build Context result -
      unlike gitlink_context_summary above, this can never collide.
      Without this field, two DIFFERENT Build Context results could
      produce an identical summary string, tricking the frontend's
      lazy-fetch-once guard (keyed on data.gitlinkContextSummary) into
      skipping a real refetch and showing stale XML. UNLIKE
      gitlink_context_xml/gitlink_change_local_root, this DOES need to be
      on the wire (see scene_payload()) - the frontend reads it to detect
      "a new build landed" even when the summary text happens to repeat.
    - gitlink_proposal_markdown/gitlink_pending_changes/
      gitlink_preview_text: landed by complete_gitlink_run - the
      human-readable proposal, the structured change list Apply actually
      acts on, and the diff-style preview text.
    - gitlink_change_fingerprint: a fingerprint (see
      graphlink_plugins.gitlink.agent's _fingerprint_changes) of the
      currently-previewed gitlink_pending_changes, checked by
      start_gitlink_apply (backend/agents.py) immediately before writing,
      so a stale/superseded approval can never be replayed.
    - gitlink_change_local_root: R5.3 post-review FIX 2 - the local_root
      the approved change set's WRITE DESTINATION was bound to at Run
      time (see complete_gitlink_run). _fingerprint_changes only hashes
      file content/paths/operations, never local_root - this field is
      deliberately NOT hashed itself, since it is reused verbatim from
      gitlink/agent.py, shared with the legacy Qt app. Without this
      separate binding, a still-valid fingerprint would let previously-
      reviewed content be written into a directory that was never diffed
      or shown to the user, if gitlink_local_root changes between Run and
      Apply (see start_gitlink_apply's fourth check in backend/agents.py).
      Plain internal bookkeeping field, like gitlink_context_xml: NEVER
      added to scene_payload()/the codegen dataclass source - the
      frontend never reads this directly, only the backend enforces it.
    - gitlink_change_state: draft | previewed | applying | applied - see
      complete_gitlink_run/fail_gitlink_apply (backend/domain/graph.py)
      for the transitions.
    - gitlink_error: the current run/apply's error banner text, cleared
      on the next attempt."""

    gitlink_repo: str = ""
    gitlink_branch: str = ""
    gitlink_scope_mode: str = "selected"
    gitlink_local_root: str = ""
    gitlink_imported_root: str = ""
    gitlink_repo_file_paths: list[str] = field(default_factory=list)
    gitlink_selected_paths: list[str] = field(default_factory=list)
    gitlink_task_prompt: str = ""
    gitlink_context_xml: str = ""
    gitlink_context_stats: dict[str, str] = field(default_factory=dict)
    gitlink_context_summary: str = ""
    gitlink_context_version: int = 0
    gitlink_proposal_markdown: str = ""
    gitlink_pending_changes: list[dict[str, Any]] = field(default_factory=list)
    gitlink_preview_text: str = ""
    gitlink_change_fingerprint: str | None = None
    gitlink_change_local_root: str | None = None
    gitlink_change_state: str = "draft"
    gitlink_error: str = ""


@dataclass
class PycoderState(NodeState):
    """Relocated verbatim from SceneNode's nine pycoder fields (former
    backend/domain/model.py fields, R5.4) - the Py-Coder node's real
    persisted shape: reads a natural-language ask (ai_driven mode) or
    hand-typed code (manual mode), runs it in a persistent REPL
    subprocess, and reports the AI's analysis of the result.
    SceneNode's own pending_request_id field (core, not duplicated here)
    is reused unchanged as the busy marker for the ENTIRE span from
    Run-click through generation, through the human-approval pause,
    through execution, through analysis - same posture as Gitlink's
    Run/Apply.

    - pycoder_mode: "ai_driven" | "manual".
    - pycoder_prompt: last natural-language ask (ai_driven only).
    - pycoder_code: current/last code - the thing that actually executes.
    - pycoder_output: last REPL stdout.
    - pycoder_analysis: AI's analysis of the last output.
    - pycoder_approved_fingerprint: ADR-002 P0 - a fingerprint (see
      graphlink_plugins.gitlink.agent's _fingerprint_changes, reused here
      rather than reinvented) of exactly what the CURRENT
      pycoder_awaiting_approval gate is asking about - {"code":
      pycoder_code}. Set the instant the gate opens (mirrors
      gitlink_change_fingerprint's own timing), checked immediately
      before the code it covers actually executes
      (AgentDispatcher.start_pycoder_run), and cleared everywhere
      pycoder_awaiting_approval itself is cleared, so a
      resolved/denied/superseded approval can never be replayed.
      Internal bookkeeping only - EXCLUDED from scene_payload(), same
      posture as CodeSandboxState's own sandbox_id.
    - pycoder_repl_id: ADR-005 stage 5.3 (review-fix) - minted ONCE, at
      node-creation time (see add_pycoder_node), mirroring
      CodeSandboxState.code_sandbox_sandbox_id exactly: a stable,
      internal directory-naming key for PythonREPL's own scratch cwd,
      independent of this node's own `id`. Needed because SceneNode.id is
      NOT durable - session_load.py's register_restored_node reassigns a
      fresh sequential id, purely by array position, on every session
      load - so keying the on-disk REPL directory by node.id let a reload
      silently swap which directory a node's REPL resolved to (one node
      could lose its own accumulated files, or inherit a different node's
      leftovers) any time a node ahead of it in save order was deleted
      before the next load. code_sandbox_sandbox_id never had this
      problem because it was already a separate, stable field; pycoder
      had no equivalent until this field was added. Internal bookkeeping
      only - EXCLUDED from scene_payload(), same posture as
      code_sandbox_sandbox_id."""

    pycoder_mode: str = "ai_driven"
    pycoder_prompt: str = ""
    pycoder_code: str = ""
    pycoder_output: str = ""
    pycoder_analysis: str = ""
    pycoder_last_run_failed: bool = False
    pycoder_awaiting_approval: bool = False
    pycoder_approved_fingerprint: str | None = None
    pycoder_error: str = ""
    pycoder_repl_id: str = ""


@dataclass
class CodeSandboxState(NodeState):
    """Relocated verbatim from SceneNode's ten code_sandbox fields (former
    backend/domain/model.py fields, R5.4) - the Execution Sandbox node's
    real persisted shape: runs Python inside an isolated per-node
    virtualenv (VirtualEnvSandbox, keyed by code_sandbox_sandbox_id) with
    a per-node requirements.txt manifest. There is no mode field/toggle
    here (unlike Py-Coder) - the real branch is "prompt blank AND code
    already exists -> re-run existing code as-is; else -> generate from
    prompt", resolved by the dispatch method checking code_sandbox_code
    at call time (see AgentDispatcher.start_code_sandbox_run in
    backend/agents.py).

    - code_sandbox_sandbox_id: minted ONCE, at node-creation time (see
      add_code_sandbox_node), and is a pure internal directory-naming
      key - never shown or edited by the user, never even read by the
      frontend. EXCLUDED from scene_payload() and from the codegen
      SceneNodeRow source, mirroring gitlink_imported_root's own
      "server-side bookkeeping only" precedent exactly.
    - code_sandbox_requirements/code_sandbox_prompt/code_sandbox_code/
      code_sandbox_output/code_sandbox_analysis: the user's live,
      still-editable requirements manifest and prompt/code draft, and
      the last run's stdout/analysis.
    - code_sandbox_approval_requirements: R5.4 CODESANDBOX FIX (closing
      the requirements-disclosure staleness race) - a display-only
      SNAPSHOT of the EXACT requirements manifest string this specific
      pending approval refers to - distinct from code_sandbox_
      requirements (the user's still-live, still-editable draft for the
      NEXT run). The real race this closes: AgentDispatcher.
      start_code_sandbox_run (backend/agents.py) reads requirements_
      manifest synchronously, at the very top of its own _run(), into a
      local `manifest` variable - BEFORE the one real await in that
      function (the asyncio.to_thread call to the generation agent). A
      user can send a new setCodeSandboxRequirements intent during that
      await window (it is ungated by any busy check), changing
      code_sandbox_requirements to something different before the
      approval panel is ever shown. Since the old approval panel
      displayed the LIVE code_sandbox_requirements field, the disclosed
      package list could differ from the manifest the backend actually
      installs a moment later - showing the WRONG list is worse than
      showing none for a security disclosure. This field is instead
      populated from that SAME already-frozen local `manifest` variable,
      at the exact moment code_sandbox_awaiting_approval flips True -
      exposing a value already correctly frozen, not re-reading
      anything live, so this introduces no new race. Cleared (empty
      string) everywhere code_sandbox_awaiting_approval itself is
      cleared: inline in start_code_sandbox_run immediately after the
      approval future resolves, and in complete_code_sandbox_run/
      fail_code_sandbox_run.
    - code_sandbox_approved_fingerprint: ADR-002 P0 - same mechanism as
      pycoder_approved_fingerprint, fingerprinting {"code":
      code_sandbox_code, "manifest": code_sandbox_approval_requirements}
      instead - see that field's own reasoning above. Internal
      bookkeeping only, EXCLUDED from scene_payload().
    - code_sandbox_approval_allow_source_builds: ADR-005 stage 5.5's
      "source-build escalation" - the user's live opt-in, made WHILE this
      specific approval is pending, to let this one run's dependency
      install build a source-only package instead of the
      --only-binary :all: default (VirtualEnvSandbox.sync_requirements,
      graphlink_plugins/code_sandbox/domain.py). Deliberately NOT part of
      code_sandbox_approved_fingerprint above: that fingerprint pins
      CONTENT identity (the code/manifest actually being approved must
      not silently change before it runs); this field is a permission
      granted alongside approving that content, decided by the user
      during the same approval window rather than frozen at gate-open.
      Reset to False every time a gate opens (both the initial gate AND
      each repair-loop re-gate in AgentDispatcher.start_code_sandbox_run,
      backend/agents.py - a review-fix: an earlier version of this stage
      only reset it at the initial gate, leaving a stale True able to
      render a repair round's checkbox as checked despite no user action
      that round) so a source-build opt-in never silently carries over to
      code the user has not yet seen.

      ADR-005 stage 5.5 review-fix (real race found by a 4-lens
      adversarial review): AgentDispatcher does NOT read this field again
      after the approval future resolves. `future.set_result()`
      (AgentDispatcher._resolve_approval) only SCHEDULES the waiting
      coroutine's resumption rather than running it inline, so a second
      WS connection's setCodeSandboxAllowSourceBuilds could land in that
      scheduling gap and change what an already-decided approval installs.
      Instead, _resolve_approval snapshots this field into
      RunHandle.approval_snapshot (backend/run_lifecycle.py) SYNCHRONOUSLY,
      in the same uninterruptible stretch as future.set_result() itself -
      see that field's own doc for the full mechanism - and
      start_code_sandbox_run reads the handle's snapshot, never this field
      directly, once approval resolves.

      Cleared back to False alongside code_sandbox_approval_requirements
      at every point that field is cleared.
    - code_sandbox_approval_is_repair: ADR-005 stage 5.5 review-fix -
      distinguishes the INITIAL approval gate (False) from any repair-loop
      re-gate (True). VirtualEnvSandbox.sync_requirements is only ever
      called once per run, before the repair loop starts, so the source-
      build checkbox has no dependency install left to affect on any
      repair round - CodeExecutionApprovalPanel.tsx uses this field to
      hide that otherwise-genuinely-inert control on repair rounds, rather
      than let a user take an action that silently does nothing. Reset at
      every gate-open (False at the initial gate, True at each repair
      re-gate) alongside the other gate-open fields.
    - code_sandbox_error: the current run's error banner text, cleared
      on the next attempt."""

    code_sandbox_sandbox_id: str = ""
    code_sandbox_requirements: str = ""
    code_sandbox_prompt: str = ""
    code_sandbox_code: str = ""
    code_sandbox_output: str = ""
    code_sandbox_analysis: str = ""
    code_sandbox_awaiting_approval: bool = False
    code_sandbox_approval_requirements: str = ""
    code_sandbox_approved_fingerprint: str | None = None
    code_sandbox_approval_allow_source_builds: bool = False
    code_sandbox_approval_is_repair: bool = False
    code_sandbox_error: str = ""


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
    # ADR-006 stage 6.4 (H5, partial-output preservation): True when this
    # node's content is a PARTIAL reply committed after its stream died
    # (failure/cancel/timeout mid-generation) rather than a completed one.
    # The frontend renders an "interrupted - Regenerate to retry" banner on
    # it; a successful regenerate (update_chat_node_content) clears it.
    response_incomplete: bool = False
    # ADR-006 stage 6.8: the provider-reported token counts for the reply
    # this node holds (normalized in backend/providers/base.py's
    # normalize_usage). None means "not reported" - every node created
    # before this field existed, every user message, and every provider
    # path that reports no usage (llama.cpp streams). Rides the same
    # provenance posture as provider/model above, which ordinary chat
    # replies also stamp as of 6.8.
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    # ADR-016 stage 16.2: the USD cost estimated AT THE TIME this reply's
    # usage was stamped (backend/api/intents_chat.py's _on_usage, via
    # TokenCounterState.estimate_cost_for) - a snapshot, not a live
    # recomputation, so an override changed later does not retroactively
    # rewrite what an already-completed reply is shown to have cost. None
    # for every node prompt_tokens/completion_tokens is also None for, plus
    # local providers' genuine $0.00 and unknown-model's genuine "no guess".
    estimated_cost_usd: float | None = None
    # ADR-007 stage 7.4: this turn's tool calls + their results, in call
    # order - empty for the overwhelming majority of chat nodes (no tool-use
    # loop exists yet to populate this; ADR-008 is the first real writer).
    # Each item: {"id", "name", "arguments" (a real dict - only JSON-encoded
    # at the wire boundary, see graph.py's scene_payload()), "result",
    # "is_error"} - directly ToolCall's own three fields plus ToolResult's
    # two (backend/providers/base.py, backend/tools.py), not a new shape.
    # Rendered as a collapsible section in ChatNodeView.tsx (the ADR's own
    # "an assistant turn that calls tools renders the calls and their
    # results (collapsible)").
    tool_invocations: list[dict[str, Any]] = field(default_factory=list)
    # ADR-018 stage 18.2: an explicit model PIN, the opposite direction from
    # provider/model above - those record what a completed reply's content
    # was actually generated by (output provenance); these two decide what
    # the NEXT reply from this node (or, when this node is a branch root,
    # every node in the branch that doesn't pin its own) will be generated
    # by (input routing). Both empty means "no pin here" - resolution falls
    # through to the branch root's own pin, then the workspace task
    # default, then auto (see graphlink_model_catalog.resolve_model_ref).
    # A pin is a real (provider, model_id) pair or nothing - there is no
    # partial-pin state; set_model_override always writes both together and
    # clear_model_override always clears both together.
    override_provider: str = ""
    override_model_id: str = ""
