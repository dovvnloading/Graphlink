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
# SceneNode.chart_width/chart_height's own dataclass field defaults below,
# and add_chart_node reads them off a freshly-constructed node rather than a
# second literal, so the two can never drift apart.
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
    is_user: bool = False
    is_collapsed: bool = False
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
    # ADR-002 Workstream 1 ("Synthesize Branches"): the provider/model that
    # produced this node's content, e.g. "Anthropic Claude" / "claude-sonnet-5"
    # - resolved from ComposerDocument.route() at creation time. Generic
    # (like pending_request_id above) rather than synthesis-only, since any
    # future agent-authored node could reasonably want the same provenance;
    # today only synthesize_branches populates it. None means "not recorded"
    # (every node created before this field existed, and every ordinary chat
    # reply, which already shows its route live in the Composer rather than
    # per-message).
    provider: str | None = None
    model: str | None = None
    # ADR-002 Workstream 1 ("Synthesize Branches"): marks a chat-kind node as
    # the output of the Synthesize Branches agent (as opposed to an ordinary
    # user/assistant message) - the chat-node equivalent of is_branch_
    # comparison below, which does the same job for note-kind nodes. A
    # distinct flag rather than reusing is_branch_comparison: that flag's
    # own kind-check (mark_branch_comparison_note raises for a non-note
    # node) would need loosening for no benefit, and the two features
    # render completely different UI (a badge on a note vs. a badge + the
    # instructions/provider/model fields below on a chat node).
    is_branch_synthesis: bool = False
    # ADR-002 Workstream 1 ("Synthesize Branches"): the free-text instructions
    # the user typed to steer the synthesis (e.g. "merge the best parts of
    # each"), recorded on the result node so its provenance is fully
    # inspectable later - the ADR's own acceptance criterion for this
    # feature. Chat-kind only in practice; unused (default empty string) for
    # every other kind and for ordinary (non-synthesis) chat nodes.
    synthesis_instructions: str = ""
    # ADR-002 Workstream 1 ("Branch status and lifecycle"): the final,
    # sequenced item after fork/compare/synthesize. One of exactly "active"
    # (the default - every existing and newly-created chat node starts
    # here, no migration needed), "accepted", "rejected", "superseded".
    # Chat-kind only, mirroring is_branch_synthesis's own chat-only scoping
    # - "a branch" is fundamentally a chain of chat nodes in this data
    # model (see chat_branch_history/get_branch_root, both of which only
    # ever walk chat-kind edges). Deliberately PER-NODE with NO write-time
    # inheritance/cascade to ancestors or descendants - every other
    # status-like flag on this dataclass (is_collapsed, is_docked,
    # is_branch_synthesis, is_branch_comparison below) is scoped exactly
    # this way, and there is no materialized "Branch" object anywhere in
    # this file to cascade through even if inheritance were wanted (a
    # branch is discovered by walking _branch_parent_edge upward on
    # demand, never stored as a set - see that method's own comment).
    # "Reduce a graph to its accepted paths" is delivered by a separate,
    # frontend-only, read-time subtree derivation over this field
    # (SceneCanvas.tsx's computeNonAcceptedNodeIds) - the same posture
    # "Hide Other Branches" already uses for a different subtree question,
    # not by inventing write-time cascade bookkeeping here.
    branch_status: str = "active"
    # R5.1: the Web Research node's real persisted shape - `content` (reused,
    # same pattern as code/thinking/html) holds the query text; these six
    # fields track one research run's live progress/outcome. Unused
    # (default) for every other kind.
    #
    # research_stage is one of the empty-string sentinel ("" - never run) or
    # the 9 ResearchStage enum values from
    # graphlink_plugins/web_research/domain.py's own .value strings:
    # "preparing" | "searching" | "fetching" | "extracting" | "validating" |
    # "synthesizing" | "completed" | "cancelled" | "failed".
    research_stage: str = ""
    research_completed: int = 0
    research_total: int = 0
    research_active_source_id: str | None = None
    research_error: str = ""
    # The wire-shaped (camelCase) ResearchResult, or None before the first
    # run ever completes. Deliberate stale-while-revalidate: a NEW run does
    # NOT clear this on start (see start_web_research_run) - the previous
    # answer stays visible until this run replaces it on success, or
    # fails/cancels (leaving the stale result annotated by research_error).
    research_result: dict[str, Any] | None = None
    # R5.3: the Gitlink node's real persisted shape - reads a GitHub repo (or
    # a local checkout) into structured XML context, proposes an LLM change
    # set, and only writes to disk after an explicit, fingerprint-verified
    # approval. Unused (default) for every other kind.
    gitlink_repo: str = ""
    gitlink_branch: str = ""
    gitlink_scope_mode: str = "selected"
    gitlink_local_root: str = ""
    # Mirrors legacy repo_state["imported_root"] - remembers which local path
    # a prior Import Repo Snapshot produced, so a later run can reuse it
    # without re-downloading. Server-side bookkeeping ONLY: deliberately
    # absent from scene_payload()/SceneNodeRow - there is no wire field for
    # it, nothing on the frontend ever needs to read it directly (gitlink_
    # local_root is what's shown/edited).
    gitlink_imported_root: str = ""
    gitlink_repo_file_paths: list[str] = field(default_factory=list)
    gitlink_selected_paths: list[str] = field(default_factory=list)
    gitlink_task_prompt: str = ""
    # DESIGNED ceiling of 180,000 chars (repository.py's MAX_CONTEXT_CHARS) -
    # an order of magnitude above this node's other fields' implicit
    # ceilings. scene_payload() resends every node on roughly 20 undebounced
    # triggers (see the image_assets comment above) - inlining a 180KB text
    # blob there would reproduce that exact cost on every unrelated
    # mutation for the rest of the session. EXCLUDED from scene_payload() on
    # purpose; served on demand via the read-only fetchGitlinkContext intent
    # instead (see fetch_gitlink_context_xml below). Deleted automatically
    # when the node is deleted - no separate eviction bookkeeping needed
    # (unlike image_assets, this never leaves this dataclass instance).
    gitlink_context_xml: str = ""
    # repository.py's build_context_bundle returns a mixed int/str dict
    # (scanned_files/loaded_files/included_files/load_errors/
    # context_omissions are ints; source_root/summary are strings) -
    # store_gitlink_context stringifies every value before assigning here so
    # the wire field this feeds (scene_payload()'s "gitlinkContextStats") stays
    # honestly dict[str, str] end to end, matching how graphlink_scene_payload.py's
    # SceneNodeRow types it for codegen. DEVIATION from a literal-verbatim
    # forward: unlike R5.1's providerSnapshot (typed dict[str, str] but always
    # populated as {} at runtime, so the type is never really exercised),
    # gitlink_context_stats IS genuinely populated with int values at
    # runtime - forwarding it unmodified would make the generated
    # validateSceneState() reject every real context-build result. The
    # str-coercion here is load-bearing, not a defensive formality.
    gitlink_context_stats: dict[str, str] = field(default_factory=dict)
    gitlink_context_summary: str = ""
    # R5.3 post-review FIX 6: a genuine MONOTONIC per-node counter,
    # incremented unconditionally every time store_gitlink_context lands a
    # successful Build Context result (see that method below) - unlike
    # gitlink_context_summary (built purely from aggregate file counts, per
    # repository.py's build_context_bundle - never from paths/content), this
    # can never collide. Without this field, two DIFFERENT Build Context
    # results (e.g. selecting a different single file each time) could
    # produce an IDENTICAL summary string, tricking the frontend's
    # lazy-fetch-once guard (keyed on data.gitlinkContextSummary) into
    # skipping a real refetch and showing stale XML. UNLIKE
    # gitlink_context_xml/gitlink_change_local_root, this DOES need to be on
    # the wire (see scene_payload() below) - the frontend reads it to detect
    # "a new build landed" even when the summary text happens to repeat.
    gitlink_context_version: int = 0
    gitlink_proposal_markdown: str = ""
    gitlink_pending_changes: list[dict[str, Any]] = field(default_factory=list)
    gitlink_preview_text: str = ""
    gitlink_change_fingerprint: str | None = None
    # R5.3 post-review FIX 2: the local_root the approved change set's WRITE
    # DESTINATION was bound to at Run time (see complete_gitlink_run below).
    # _fingerprint_changes only hashes file content/paths/operations, never
    # local_root - deliberately NOT modified, since it is reused verbatim
    # from gitlink/agent.py, shared with the legacy Qt app. Without this
    # separate binding, a still-valid fingerprint would let previously-
    # reviewed content be written into a directory that was never diffed or
    # shown to the user, if gitlink_local_root changes between Run and
    # Apply (see start_gitlink_apply's fourth check in backend/agents.py).
    # Plain internal bookkeeping field, like gitlink_context_xml: NEVER
    # added to scene_payload()/the codegen dataclass source - the frontend
    # never reads this directly, only the backend enforces it.
    gitlink_change_local_root: str | None = None
    # draft | previewed | applying | applied - see complete_gitlink_run/
    # fail_gitlink_apply below for the transitions.
    gitlink_change_state: str = "draft"
    gitlink_error: str = ""
    # R5.4: the Py-Coder node's real persisted shape - reads a natural-
    # language ask (ai_driven mode) or hand-typed code (manual mode), runs it
    # in a persistent REPL subprocess, and reports the AI's analysis of the
    # result. pending_request_id (generic, above) is reused unchanged as the
    # busy marker for the ENTIRE span from Run-click through generation,
    # through the human-approval pause, through execution, through analysis -
    # same posture as Gitlink's Run/Apply. Unused (default) for every other
    # kind.
    pycoder_mode: str = "ai_driven"  # "ai_driven" | "manual"
    pycoder_prompt: str = ""  # last natural-language ask (ai_driven only)
    pycoder_code: str = ""  # current/last code - the thing that actually executes
    pycoder_output: str = ""  # last REPL stdout
    pycoder_analysis: str = ""  # AI's analysis of the last output
    pycoder_last_run_failed: bool = False
    pycoder_awaiting_approval: bool = False
    # ADR-002 P0: a fingerprint (see graphlink_plugins.gitlink.agent's
    # _fingerprint_changes, reused here rather than reinvented) of exactly
    # what the CURRENT pycoder_awaiting_approval gate is asking about -
    # {"code": pycoder_code}. Set the instant the gate opens (mirrors
    # gitlink_change_fingerprint's own timing), checked immediately before
    # the code it covers actually executes (AgentDispatcher.start_pycoder_
    # run), and cleared everywhere pycoder_awaiting_approval itself is
    # cleared, so a resolved/denied/superseded approval can never be
    # replayed. Internal bookkeeping only - EXCLUDED from scene_payload(),
    # same posture as code_sandbox_sandbox_id.
    pycoder_approved_fingerprint: str | None = None
    pycoder_error: str = ""
    # R5.4: the Execution Sandbox node's real persisted shape - runs Python
    # inside an isolated per-node virtualenv (VirtualEnvSandbox, keyed by
    # code_sandbox_sandbox_id) with a per-node requirements.txt manifest.
    # There is no mode field/toggle here (unlike Py-Coder) - the real branch
    # is "prompt blank AND code already exists -> re-run existing code
    # as-is; else -> generate from prompt", resolved by the dispatch method
    # checking code_sandbox_code at call time (see
    # AgentDispatcher.start_code_sandbox_run in backend/agents.py). Unused
    # (default) for every other kind.
    #
    # code_sandbox_sandbox_id is minted ONCE, at node-creation time (see
    # add_code_sandbox_node), and is a pure internal directory-naming key -
    # never shown or edited by the user, never even read by the frontend.
    # EXCLUDED from scene_payload() and from the codegen SceneNodeRow source,
    # mirroring gitlink_imported_root's existing "server-side bookkeeping
    # only, deliberately absent from scene_payload()" precedent exactly.
    code_sandbox_sandbox_id: str = ""
    code_sandbox_requirements: str = ""
    code_sandbox_prompt: str = ""
    code_sandbox_code: str = ""
    code_sandbox_output: str = ""
    code_sandbox_analysis: str = ""
    code_sandbox_awaiting_approval: bool = False
    # R5.4 CODESANDBOX FIX (closing the requirements-disclosure staleness
    # race): a display-only SNAPSHOT of the EXACT requirements manifest
    # string this specific pending approval refers to - distinct from
    # code_sandbox_requirements (the user's still-live, still-editable draft
    # for the NEXT run). The real race this closes: AgentDispatcher.
    # start_code_sandbox_run (backend/agents.py) reads requirements_manifest
    # synchronously, at the very top of its own _run(), into a local
    # `manifest` variable - BEFORE the one real await in that function (the
    # asyncio.to_thread call to the generation agent). A user can send a new
    # setCodeSandboxRequirements intent during that await window (it is
    # ungated by any busy check), changing code_sandbox_requirements to
    # something different before the approval panel is ever shown. Since the
    # old approval panel displayed the LIVE code_sandbox_requirements field,
    # the disclosed package list could differ from the manifest the backend
    # actually installs a moment later - showing the WRONG list is worse
    # than showing none for a security disclosure. This field is instead
    # populated from that SAME already-frozen local `manifest` variable, at
    # the exact moment code_sandbox_awaiting_approval flips True - exposing a
    # value already correctly frozen, not re-reading anything live, so this
    # introduces no new race. Cleared (empty string) everywhere
    # code_sandbox_awaiting_approval itself is cleared: inline in
    # start_code_sandbox_run immediately after the approval future resolves,
    # and in complete_code_sandbox_run/fail_code_sandbox_run below. Unused
    # (default) for every other kind.
    code_sandbox_approval_requirements: str = ""
    # ADR-002 P0: same mechanism as pycoder_approved_fingerprint above,
    # fingerprinting {"code": code_sandbox_code, "manifest":
    # code_sandbox_approval_requirements} instead - see that field's own
    # comment for the full reasoning. Internal bookkeeping only, EXCLUDED
    # from scene_payload().
    code_sandbox_approved_fingerprint: str | None = None
    code_sandbox_error: str = ""
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
    # note kind only - the legacy system-prompt / summary-note badge flags.
    # Both default False; unused for every other kind.
    is_system_prompt: bool = False
    is_summary_note: bool = False
    # ADR-002 Workstream 1 ("Compare Branches") - note kind only, a NEW badge
    # flag alongside the two above, marking a note as the output of the
    # Compare Branches agent rather than a plain/system-prompt/summary note.
    # Deliberately NOT is_summary_note: that flag is legacy's own "Group
    # Summary" concept (an arbitrary multi-select of any node kinds,
    # summarized - never ported, since it depended on a multi-select model
    # the app didn't have yet), which is semantically a different feature
    # from comparing conversation branches specifically. Reusing it here
    # would make a future real port of Group Summary collide with this one.
    is_branch_comparison: bool = False
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
    # frame kind only - legacy default is LOCKED (True), unlike every other
    # bool field on this dataclass (which all default False). Containers
    # have no lock concept at all - see create_container's own docstring for
    # why no lock toggle is exposed for them. Unused for every other kind.
    is_locked: bool = True
    # frame kind only - the MANUAL resize override recorded by resize_frame,
    # cleared back to None by fit_frame_to_content. This pair (unlike
    # group_width/group_height just below) is the single, stable source of
    # truth for "is this frame currently manually sized": it is NEVER
    # auto-populated by _recompute_group_bounds's own auto-fit branch, only
    # ever written by resize_frame/fit_frame_to_content, so its None-ness
    # survives a collapse/expand round-trip untouched (unlike group_width/
    # group_height, which DO get temporarily overwritten with the fixed
    # collapsed-pill size while is_collapsed - see toggle_group_collapsed).
    # Deliberately excluded from scene_payload()/the wire - pure internal
    # bookkeeping, mirrors gitlink_imported_root's own "server-side only"
    # precedent. Unused (always None) for container kind - containers have
    # no manual-resize capability (no resize_container method exists).
    group_manual_width: float | None = None
    group_manual_height: float | None = None
    # frame kind only - the position counterpart to group_manual_width/
    # height above: set by move_node whenever a frame is dragged directly
    # (locked whole-group drag OR an independently-dragged unlocked frame),
    # cleared back to None by fit_frame_to_content. Exists so an unlocked
    # frame's own drag actually sticks - without an explicit anchor, the
    # very next member move would recompute the frame straight back to
    # bbox-of-members-centered, silently undoing the drag (legacy let an
    # unlocked frame's outline be repositioned independently of its
    # members; this is that same capability, ported). See
    # _recompute_group_bounds for how this anchor is unioned with the live
    # bbox so it still can never clip a member, matching legacy's own
    # rect.united() guarantee. Same wire/kind-scoping posture as
    # group_manual_width/height above (server-side only, unused for
    # container).
    group_manual_x: float | None = None
    group_manual_y: float | None = None
    # frame/container's current effective on-canvas size, kept live by
    # _recompute_group_bounds: the fixed GROUP_COLLAPSED_WIDTH/HEIGHT pill
    # while is_collapsed, else group_manual_width/height verbatim while a
    # frame has a manual override active, else the padded bbox-of-members
    # auto-fit size. THIS is the field the contract names group_width/
    # group_height and exposes on the wire as groupWidth/groupHeight -
    # group_manual_width/height above exist purely so a frame's manual
    # override survives a collapse/expand round-trip without the collapsed-
    # pill overwrite (this field) destroying it. Unused (default None) for
    # every other kind.
    group_width: float | None = None
    group_height: float | None = None
    # R6.2: Chart node - graphlink_canvas_chart_item.py's ChartItem, ported
    # to a backend-rendered PNG (see graphlink_chart_rendering.py's own
    # module docstring for why - matplotlib+FigureCanvasAgg was already
    # Qt-free upstream; only the QImage-wrapping step needed replacing).
    #
    # chart_type is one of SUPPORTED_CHART_TYPES (graphlink_chart_data.py):
    # "bar" | "line" | "pie" | "histogram" | "sankey". Unused (default "")
    # for every other kind.
    chart_type: str = ""
    # ALREADY-canonicalized chart data (canonicalize_chart_data's own output
    # shape) - add_chart_node below does NOT call canonicalize_chart_data
    # itself; the caller (backend/agents.py's generateChart intent, via
    # AgentDispatcher.start_chart_generation) is responsible for that, so it
    # can catch ChartDataError itself and still create a placeholder chart
    # with chart_error set on failure, matching legacy's never-hard-fail
    # contract, rather than add_chart_node raising and aborting node
    # creation entirely.
    chart_data: dict[str, Any] = field(default_factory=dict)
    # Non-empty if generation/canonicalization degraded to a placeholder -
    # the chart still has a real (if minimal) chart_asset_id and renders
    # SOMETHING, never a blank/broken state (mirrors ChartDataAgent's own
    # get_response/repair_chart_data/heuristic_chart_data degrade-gracefully
    # chain, which never hard-fails outright for a genuine LLM response).
    chart_error: str = ""
    # Opaque key into the EXISTING SceneDocument.image_assets dict (REUSED,
    # not a parallel store - same dict R3.21's image nodes already use, see
    # that field's own transport-decision comment on image_assets) - the
    # rendered display-resolution PNG. Export (the 3x-resolution download)
    # re-renders fresh rather than reading this asset - see backend/assets.py.
    chart_asset_id: str = ""
    # Incremented every time chart_asset_id's bytes are (re)written (by
    # add_chart_node's initial render, or resize_chart's re-render) - lets
    # the frontend cache-bust the <img> src with a version query param after
    # a resize re-render, since the asset id itself never changes.
    chart_asset_version: int = 0
    # Legacy ChartItem.DEFAULT_WIDTH/DEFAULT_HEIGHT - add_chart_node reads
    # these dataclass defaults directly (not a second literal) when
    # rendering a freshly-created chart's first PNG, so the two can never
    # drift apart. resize_chart clamps any later change into
    # [CHART_MIN_WIDTH, CHART_MAX_WIDTH] / [CHART_MIN_HEIGHT, CHART_MAX_HEIGHT].
    chart_width: float = 680.0
    chart_height: float = 500.0
    # Legacy ChartItem.aspect_ratio_locked's own default (True, unlike most
    # bool fields on this dataclass which default False) - resize_chart
    # consults this to decide whether to re-derive a dimension after
    # clamping; toggle_chart_aspect_lock flips it without touching size or
    # re-rendering.
    chart_aspect_locked: bool = True
    # Provenance: which node's content the chart data was generated from -
    # always the parent branch-point edge's source in this implementation
    # (legacy's rarer "different node" case is a known, accepted
    # simplification, not replicated). Unused (default "") for every other
    # kind.
    chart_source_node_id: str = ""
    # R6.3: ChatNode's persisted scroll position within its own content
    # area (legacy's own scroll_value field). Unlike HtmlState's own
    # html_splitter_state (backend/domain/node_states.py, ADR-002 stage
    # 2.5), 0.0 (scrolled to the top) IS the genuine default for a node
    # that has never been scrolled, so this is a plain float, not an
    # Optional - there is no "unset" state worth distinguishing here. chat
    # kind only; unused (default 0.0) for every other kind.
    chat_scroll_value: float = 0.0
    # R6.3: the RAW (already-decoded - "data" holds real Python bytes, never
    # base64 text) multimodal parts list for a chat node whose legacy
    # raw_content was a list of typed parts (e.g. an inline pasted image)
    # rather than a plain string - see content_codec.py's own
    # process_content_for_serialization/_for_deserialization, which this
    # increment does not call directly (see scene_payload()'s own comment
    # for the wire-side base64 encoding step this field feeds). None for the
    # overwhelmingly common plain-text case, where `content` above is the
    # only source of truth. ADDITIVE, not a replacement for `content`: even
    # when this is populated, `content` continues to hold a flattened-text
    # mirror (join of the text-type parts, or a placeholder like "[Image]"
    # for non-text parts) so every existing piece of code that already reads
    # `content` as a plain string keeps working unchanged. chat kind only;
    # unused (default None) for every other kind. Nothing in this increment
    # creates multimodal content yet (no image-paste-into-composer feature
    # exists) - this is purely the data-model capability so R6.4's session
    # loader has somewhere to put it when an OLD saved session has it.
    content_parts: list[dict[str, Any]] | None = None
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
