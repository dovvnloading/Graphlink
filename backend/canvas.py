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

import base64
import binascii
import itertools
import logging
import math
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from graphlink_chart_data import ChartDataError, canonicalize_chart_data, SUPPORTED_CHART_TYPES
from graphlink_chart_rendering import render_chart_png
from graphlink_grid_view_settings import (
    GRID_SIZE_PRESETS,
    GRID_STYLE_PRESETS,
    GridViewSettings,
)
from graphlink_navigation_pins import NavigationPinRecord, NavigationPinStore

from backend.agents import (
    _CODE_EXEC_RUN_CLAIM_PLACEHOLDER,
    _GITLINK_RUN_CLAIM_PLACEHOLDER,
    AgentDispatcher,
)
from backend.composer import ComposerDocument
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.response_parsing import (
    parse_response,
    PLACEHOLDER_GENERATED_CONTENT,
    PLACEHOLDER_ASSISTANT_REASONING,
    PLACEHOLDER_EMPTY_RESPONSE,
)
from backend.token_counter import estimate_tokens


# R7.2: ported from graphlink_app/graphlink_session/content_codec.py, not
# imported. content_codec.py itself is 100% Qt-free (base64/binascii/logging
# only), but the graphlink_session PACKAGE it lives in is not -
# graphlink_session/__init__.py eagerly imports ChatSessionManager and
# SaveWorkerThread, and workers.py in turn imports PySide6.QtCore, so a plain
# `from graphlink_session.content_codec import ...` would run that __init__.py
# first (Python always runs a package's __init__.py, even for a submodule
# import) and pull Qt into backend/'s import graph. Before R7.2 this was
# loaded via a raw importlib.util.spec_from_file_location path load,
# bypassing the package's __init__.py - that workaround existed only because
# every OTHER Qt-free survivor could be reached by relocating the physical
# file (matching R7.2's real destination for the 33 modules it did move); this
# one file can't get the same treatment because graphlink_session/
# deserializers.py and serializers.py - Qt-tainted, still live until the R7.6
# cutover - import it via a normal package-relative
# `from graphlink_session.content_codec import (...)`, which a physical move
# would break. So the file itself stays exactly where it is, and its logic is
# ported here instead - the same "reimplement, don't import" precedent
# backend/chat_library.py and backend/crash_recovery.py already follow for a
# Qt-tainted wrapper around Qt-free logic. The _content_codec namespace below
# exists purely so every one of the 8 call sites across canvas.py/
# session_load.py/session_save.py keeps its existing `_content_codec.foo(...)`
# spelling unchanged.


def _serialize_history(history):
    serialized_history = []
    for message in history or []:
        new_message = message.copy()
        if "content" in new_message:
            new_message["content"] = _process_content_for_serialization(new_message["content"])
        serialized_history.append(new_message)
    return serialized_history


def _deserialize_history(history):
    deserialized_history = []
    for message in history or []:
        new_message = message.copy()
        if "content" in new_message:
            new_message["content"] = _process_content_for_deserialization(new_message["content"])
        deserialized_history.append(new_message)
    return deserialized_history


def _process_content_for_serialization(content):
    """Base64-encode raw image bytes inside multimodal content payloads."""
    if isinstance(content, list):
        processed_parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_bytes" and isinstance(part.get("data"), bytes):
                new_part = part.copy()
                new_part["data"] = base64.b64encode(part["data"]).decode("utf-8")
                processed_parts.append(new_part)
            else:
                processed_parts.append(part)
        return processed_parts
    return content


def _process_content_for_deserialization(content):
    """Decode base64 image payloads back into raw bytes when loading a chat."""
    if isinstance(content, list):
        processed_parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_bytes" and isinstance(part.get("data"), str):
                new_part = part.copy()
                try:
                    new_part["data"] = base64.b64decode(part["data"])
                    processed_parts.append(new_part)
                except (binascii.Error, ValueError):
                    logging.exception("Failed to decode base64 image data during deserialization.")
                    processed_parts.append({"type": "text", "text": "[ERROR: Image Data Corrupted]"})
            else:
                processed_parts.append(part)
        return processed_parts
    return content


def _encode_image_bytes(data):
    return base64.b64encode(data).decode("utf-8")


def _decode_image_bytes(data):
    return base64.b64decode(data)


_content_codec = SimpleNamespace(
    serialize_history=_serialize_history,
    deserialize_history=_deserialize_history,
    process_content_for_serialization=_process_content_for_serialization,
    process_content_for_deserialization=_process_content_for_deserialization,
    encode_image_bytes=_encode_image_bytes,
    decode_image_bytes=_decode_image_bytes,
)

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
    # R3.13 (doc/QT_REMOVAL_PLAN.md): the ThinkingNode/docked-child increment -
    # whether this node is currently docked into its parent's collapsed
    # docked-child slot. A parent's set of docked children is derived at read
    # time from this flag (scan nodes whose parent edge points at it), never
    # stored on the parent itself. Unused (default) for every other kind.
    is_docked: bool = False
    # R3.21 (doc/QT_REMOVAL_PLAN.md): the image node's real persisted shape -
    # an opaque reference key into SceneDocument.image_assets. Image bytes
    # never live on the node itself (see the transport-decision comment on
    # image_assets below). Unused (default) for every other kind.
    image_asset_id: str = ""
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
    # way is_docked/image_asset_id are generic fields even though today only
    # one kind populates them); unused (default None) for every other kind.
    pending_request_id: str | None = None
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
    # R5.2: the Artifact/Drafter node's real persisted shape - the model
    # returns the WHOLE document every turn (whole-document replace, never a
    # diff/patch - see complete_artifact_generation), so this field is
    # bounded by the model's own per-turn output ceiling, not by session
    # length. The turn-by-turn conversation reuses the existing generic
    # `history` list field above (already used by ConversationNode) rather
    # than a new list-typed field - only this one new scalar is needed.
    # Unused (default) for every other kind.
    artifact_content: str = ""
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
    # frame/container membership: the ids of the member nodes this group
    # currently encloses. In this implementation a node can be a member of
    # AT MOST ONE frame AND AT MOST ONE container simultaneously (never two
    # frames, never two containers) - create_frame/create_container's own
    # detach-from-existing-same-kind-group rule enforces this (see below).
    # Unused (default empty list) for every other kind.
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
    # R6.3: HtmlViewNode's persisted draggable code/preview splitter
    # position - legacy's own splitter_state field, not currently modeled at
    # all before this increment. None means "use the frontend's own
    # default", not "0" - a real 0.0 position (fully collapsed to one side)
    # must round-trip distinctly from "never set". html kind only; unused
    # (default None) for every other kind.
    html_splitter_state: float | None = None
    # R6.3: ChatNode's persisted scroll position within its own content
    # area (legacy's own scroll_value field). Unlike html_splitter_state
    # above, 0.0 (scrolled to the top) IS the genuine default for a node
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
    # R7.5b-1: Qt-removal plan R7.5's first canvas-visual parity fix - the
    # legacy scene's fade_connections_enabled bool (graphlink_scene.py),
    # ported 1:1 in shape to snap_to_grid above (bare bool, "scene" topic,
    # dedicated setFadeConnections intent - see register_canvas below).
    fade_connections_enabled: bool = False
    # R7.5b-2: Qt-removal plan R7.5's second canvas-visual parity fix - the
    # legacy scene's orthogonal_routing bool, same bare-bool/"scene"-topic
    # shape as fade_connections_enabled above.
    orthogonal_routing: bool = False
    # R7.5b-3: Qt-removal plan R7.5's third and final canvas-visual parity
    # fix - the legacy scene's smart_guides bool, same bare-bool/"scene"-topic
    # shape as the two toggles above. The snap math itself is 100% client-side
    # (web_ui/src/app/canvas/smartGuides.ts), matching the legacy split where
    # ChatScene owned only the flag and the geometry ran in the view layer.
    smart_guides: bool = False
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
    # R3.21: in-memory, session-scoped store for image-node bytes, keyed by
    # asset id (see SceneNode.image_asset_id) -> (raw_bytes, mime_type).
    # TRANSPORT DECISION: images travel to the client via a dedicated GET
    # /api/assets/{id} HTTP route (backend/assets.py), NEVER inlined into
    # scene_payload(). scene_payload() resends every node on every
    # publish_scene() call - roughly 20 different intents trigger it, none
    # of them debounced - so inlining image bytes there would compound in
    # size on every unrelated mutation for the rest of the session. No disk
    # persistence yet: there is zero live creation trigger for this
    # increment, same posture as every prior node-type increment before its
    # real trigger landed.
    image_assets: dict[str, tuple[bytes, str]] = field(default_factory=dict)
    # R6.3: the canvas viewport's persisted zoom/scroll (legacy's own
    # zoom_factor/scroll_position(x, y)) - document-wide, not per-node, since
    # there is exactly one viewport per session regardless of node count.
    # No min/max clamping here (unlike drag_factor/font_size_pt above): the
    # frontend's own viewport constraints already bound these, so there is
    # nothing meaningful for the server to enforce independently.
    zoom_factor: float = 1.0
    scroll_x: float = 0.0
    scroll_y: float = 0.0
    # R6.3: legacy's running cumulative token count for the whole session,
    # restored into the window's token counter on load - DISTINCT from
    # backend/token_counter.py's TokenCounterState (a transient, in-memory,
    # per-process ESTIMATE that resets every restart and is never
    # persisted). This field is the real, live-growing, save/restore-able
    # counterpart: send_message's WS wrapper (register_canvas, below) grows
    # it by add_session_tokens for both the user's own message and the
    # assistant's completed reply, every time either lands.
    total_session_tokens: int = 0
    # R6.5: the ~/.graphlink/chats.db row this session's scene currently
    # corresponds to, or None for a brand-new/never-saved session - the
    # backend analog of ChatSessionManager.current_chat_id. Determines
    # save_current_chat's own INSERT-vs-UPDATE branch: set on a successful
    # loadChat (backend/chat_library.py), left None for a fresh session, and
    # reset to None by clear_for_load/new_chat below (a freshly loaded OR
    # freshly cleared scene has no id of its own yet until the next load/
    # save assigns one). The id ITSELF stays server-side - the frontend has
    # no use for a chats.db row number, same posture as
    # gitlink_imported_root/code_sandbox_sandbox_id. R7.5c does expose one
    # derived bit of it as scene_payload()'s "hasSavedChat", because legacy's
    # New Chat confirm skips only when the canvas is empty AND there is no
    # current chat - a predicate the frontend cannot evaluate without it.
    current_chat_id: int | None = None
    _counter: itertools.count = field(default_factory=itertools.count, repr=False)

    # -- nodes -------------------------------------------------------------

    def add_node(self, x: float, y: float, title: str = "") -> SceneNode:
        node_id = f"n{next(self._counter)}"
        node = SceneNode(id=node_id, x=float(x), y=float(y), title=title or f"Node {node_id[1:]}")
        self.nodes[node_id] = node
        return node

    def register_restored_node(self, node: SceneNode) -> SceneNode:
        """R6.4: used ONLY by the session loader (backend/session_load.py),
        which builds every node's full field set directly from a legacy
        payload dict before any parent/child edge exists - unlike every
        add_X_node method above, which creates a node AND its parent edge as
        one atomic action, the loader cannot do that: legacy's own restore
        order creates ALL nodes first (in whatever order the saved array
        happens to hold them, parent or child first, no guarantee) and only
        resolves edges afterward in a separate phase (children_indices/
        children_ids, then the 7 connection lists). Assigns a fresh id (the
        node's own `id` field, if the caller set one from the legacy
        payload's `id`/persistent_id, is overwritten here - backend ids are
        a different namespace/format than legacy's uuid-based persistent
        ids, and the loader tracks its OWN payload-id -> new-id mapping
        separately, exactly mirroring legacy's `_nodes_by_id`) and inserts
        the node with no edges - the caller creates those separately once
        every node it might reference has been registered."""
        node.id = f"n{next(self._counter)}"
        self.nodes[node.id] = node
        return node

    def clear_for_load(self) -> None:
        """R6.4: resets every mutable piece of scene state to a fresh-
        session default, immediately before a session load replaces it all -
        mirrors legacy ChatScene.clear() (called first thing in
        restore_chat, deserializers.py:476) plus this backend's own R6.3
        view-state/token additions, which legacy's clear() has no equivalent
        for (they get overwritten by _restore_view_state/the token-counter
        reset a few lines later in restore_chat instead - reset here too so
        a load that fails partway through never leaves a stale value from
        the PREVIOUS session)."""
        self.nodes.clear()
        self.edges.clear()
        self.image_assets.clear()
        self.pins.clear()
        self.last_chat_node_id = None
        self.zoom_factor = 1.0
        self.scroll_x = 0.0
        self.scroll_y = 0.0
        self.total_session_tokens = 0
        self.current_chat_id = None

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

    def add_thinking_node(
        self,
        x: float,
        y: float,
        thinking_text: str,
        parent_id: str,
    ) -> SceneNode:
        """R3.13's ThinkingNode equivalent of add_chat_node/add_code_node/
        add_document_node: a real reasoning-panel node. Same as
        add_document_node (and unlike chat/code), parent_id is REQUIRED, not
        optional - a ThinkingNode never exists unparented - so this
        unconditionally connects to its parent, no `if parent_id` guard.

        Thinking text reuses the existing `content` field rather than a new
        one - there is no separate thinking-text field. `is_docked` defaults
        to False: a freshly-created thinking node is never pre-docked: dock()
        is only ever invoked by explicit user action or on session-load
        restore, never at construction time.

        Thinking nodes are also NOT branch points (same as code/document):
        there is no delete_thinking_node; deletion goes entirely through the
        existing generic remove_nodes.
        """
        if parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        title = str(thinking_text)[:THINKING_TITLE_PREVIEW_LENGTH] or "Thinking"
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title=title,
            kind="thinking",
            content=str(thinking_text),
        )
        self.nodes[node_id] = node
        self.connect(parent_id, node_id)
        return node

    def add_html_node(
        self,
        x: float,
        y: float,
        html_content: str,
        parent_id: str,
    ) -> SceneNode:
        """R3.17's HtmlViewNode equivalent of add_document_node/
        add_thinking_node: a real raw-HTML-source node. Same as
        add_document_node/add_thinking_node (and unlike chat/code), parent_id
        is REQUIRED, not optional - an HtmlViewNode never exists unparented -
        so this unconditionally connects to its parent, no `if parent_id`
        guard.

        The raw HTML source reuses the existing `content` field rather than a
        new one - there is no separate html-content field, same reuse pattern
        as R3.5's code text and R3.13's thinking text. The backend stores it
        VERBATIM as an opaque string: it never parses, sanitizes, validates,
        or otherwise interprets the HTML - that is the frontend's job (the
        preview render is a 100% client-side action that never round-trips
        here).

        Html nodes are also NOT branch points (same as code/document/
        thinking): there is no delete_html_node; deletion goes entirely
        through the existing generic remove_nodes.
        """
        if parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        title = str(html_content)[:HTML_TITLE_PREVIEW_LENGTH] or "HTML"
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title=title,
            kind="html",
            content=str(html_content),
        )
        self.nodes[node_id] = node
        self.connect(parent_id, node_id)
        return node

    def set_html_splitter_state(self, node_id: str, value: float) -> None:
        """R6.3: persists an HtmlViewNode's draggable code/preview splitter
        position. html kind only (SceneError otherwise), matching every
        other kind-specific setter's guard pattern in this file (e.g.
        resize_chart/toggle_frame_lock)."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "html":
            raise SceneError(f"node is not an html node: {node_id}")
        node.html_splitter_state = float(value)

    def add_image_node(
        self,
        x: float,
        y: float,
        image_bytes: bytes,
        prompt: str,
        parent_id: str,
        *,
        mime_type: str = "image/png",
    ) -> SceneNode:
        """R3.21's image-node equivalent of add_document_node/
        add_thinking_node/add_html_node: a real generated-image node. Same as
        document/thinking/html (and unlike chat/code), parent_id is
        REQUIRED, not optional - an image node never exists unparented - so
        this unconditionally connects to its parent, no `if parent_id` guard.

        Image bytes do NOT live on SceneNode (see the transport-decision
        comment on SceneDocument.image_assets) - they go into that
        session-scoped store, keyed by a SEPARATE id. Unlike node/edge ids
        (which only need to be unique within their own SceneDocument, since
        nothing ever looks a node up across sessions), asset ids are read
        back through GET /api/assets/{id}, a route that takes a bare id plus
        an independent session query param - so a per-document counter here
        would let two sessions mint the identical "imgN" id for unrelated
        images (guaranteed, not just probabilistic, for sessions that create
        nodes in the same order), and a caller that omits/mis-supplies the
        session param would silently be served someone else's image instead
        of a 404. A uuid4 hex keeps the id globally unique so cross-session
        collision is not possible regardless of session query correctness.
        image_asset_id on the node is just the opaque reference key into
        that store.

        There is no natural title-preview text for an image the way there is
        for text-based kinds, so the title is the prompt (truncated, same
        60-char convention as chat/thinking/html) when non-empty, else a
        literal "Image".

        Image nodes are also NOT branch points (same as code/document/
        thinking/html): there is no delete_image_node; deletion goes
        entirely through the existing generic remove_nodes, which
        additionally evicts this node's image_assets entry so bytes never
        outlive the node (see remove_nodes).
        """
        if parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        asset_id = f"img{uuid.uuid4().hex}"
        self.image_assets[asset_id] = (image_bytes, mime_type)
        title = str(prompt)[:IMAGE_TITLE_PREVIEW_LENGTH] or "Image"
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title=title,
            kind="image",
            content=str(prompt),
            image_asset_id=asset_id,
        )
        self.nodes[node_id] = node
        self.connect(parent_id, node_id)
        return node

    def get_image_asset(self, asset_id: str) -> tuple[bytes, str] | None:
        """The read-side of image_assets - the same lookup backend/assets.py's
        GET /api/assets/{id} route calls to serve the raw bytes + mime type."""
        return self.image_assets.get(asset_id)

    def add_conversation_node(self, x: float, y: float, parent_id: str) -> SceneNode:
        """R3.25's ConversationNode equivalent of add_document_node/
        add_thinking_node/add_html_node/add_image_node: a real multi-message
        conversation node. Same as document/thinking/html/image (and unlike
        chat/code), parent_id is REQUIRED, not optional - a ConversationNode
        never exists unparented - so this unconditionally connects to its
        parent, no `if parent_id` guard.

        Title is always the fixed literal "Conversation" - never derived or
        truncated from any content, unlike every scalar-content kind before
        it (chat/thinking/html/image all preview their own text). There is
        no natural single preview string for a node whose content is a
        growing LIST of messages, so the title never changes as messages are
        appended (see append_conversation_user_message/
        append_conversation_assistant_message below - neither touches
        title). Mirrors graphlink_conversation_node.py's `title_label =
        QLabel("Conversation")`, a hardcoded literal, not derived state.

        `history` starts empty - a freshly-created conversation node has no
        messages yet, same posture as `is_docked` defaulting False on a
        freshly-created thinking node.

        Conversation nodes are also NOT branch points (same as code/document/
        thinking/html/image): there is no delete_conversation_node; deletion
        goes entirely through the existing generic remove_nodes.
        """
        if parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title="Conversation",
            kind="conversation",
        )
        self.nodes[node_id] = node
        self.connect(parent_id, node_id)
        return node

    def append_conversation_user_message(self, node_id: str, text: str) -> SceneNode:
        """Append a real user message to a conversation node's history -
        mirrors graphlink_conversation_node.py's add_user_message, minus the
        view-layer bubble creation (the frontend's job)."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.history.append({"role": "user", "content": str(text)})
        return node

    def append_conversation_assistant_message(self, node_id: str, text: str) -> SceneNode:
        """Append a real assistant message to a conversation node's history -
        mirrors graphlink_conversation_node.py's add_ai_message, minus the
        view-layer bubble creation. No live caller yet in this increment -
        this exists for R4 to call once real agent dispatch lands, same
        posture as every prior kind's method built ahead of its trigger."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.history.append({"role": "assistant", "content": str(text)})
        return node

    def delete_conversation_message(self, node_id: str, message_index: int) -> None:
        """Prune one message out of a conversation node's history by index -
        mirrors graphlink_conversation_node.py's _remove_message's index-
        synced pop, minus the view-layer bubble removal/re-layout (the
        frontend's job)."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if message_index < 0 or message_index >= len(node.history):
            raise SceneError(f"message index out of range: {message_index}")
        node.history.pop(message_index)

    def send_conversation_message(self, node_id: str, text: str) -> SceneNode:
        """The Conversation node's own Send action (R3.25): a thin wrapper
        over append_conversation_user_message, kept as a separate method
        (rather than only calling append_conversation_user_message directly
        from the WS wrapper) so the WS intent name lines up 1:1 with the
        domain method, the same way sendMessage/send_message already do for
        ChatNode."""
        return self.append_conversation_user_message(node_id, text)

    # -- R5.1: web research node ---------------------------------------------

    def add_web_research_node(self, x: float, y: float, parent_id: str) -> SceneNode:
        """The Web Research node's creation primitive - same required-parent
        posture as document/thinking/html/image/conversation nodes (never
        exists unparented). Title is always the fixed literal "Web Research"
        (mirrors conversation node's own fixed "Conversation" title - there
        is no meaningful single preview string before a query has ever been
        run). Content starts empty; the query text only lands once
        start_web_research_run is called."""
        if parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title="Web Research",
            kind="web_research",
        )
        self.nodes[node_id] = node
        self.connect(parent_id, node_id)
        return node

    def start_web_research_run(self, node_id: str, query: str) -> SceneNode:
        """Begin one research run: stores the query text and resets this
        run's progress fields. Deliberately does NOT clear research_result -
        stale-while-revalidate: the previous run's answer stays visible until
        this run replaces it on success, or fails/cancels (leaving the stale
        result annotated by the new research_error)."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "web_research":
            raise SceneError(f"node is not a web_research node: {node_id}")
        node.content = str(query)
        node.research_stage = ""
        node.research_completed = 0
        node.research_total = 0
        node.research_active_source_id = None
        node.research_error = ""
        return node

    def apply_web_research_progress(self, node_id: str, event) -> SceneNode | None:
        """Apply one duck-typed ProgressEvent-shaped update (.stage/.completed/
        .total/.source_id) - canvas.py deliberately does NOT import anything
        from graphlink_plugins.web_research (mirrors how
        start_conversation_reply's node param is duck-typed without
        agents.py importing backend.canvas.SceneNode). Silent no-op (returns
        None, never raises) if node_id is no longer in self.nodes - the node
        may have been deleted while a background run was still in flight."""
        node = self.nodes.get(node_id)
        if node is None:
            return None
        node.research_stage = event.stage.value
        node.research_completed = event.completed
        node.research_total = event.total
        node.research_active_source_id = event.source_id
        return node

    def complete_web_research_run(self, node_id: str, result_wire: dict) -> SceneNode:
        """Land a successful run's result. Raises SceneError if the node is
        gone - the WS wrapper's own liveness check (in register_canvas)
        guards the actual mid-flight-delete race; this stays a hard
        precondition here, same posture as update_chat_node_content."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.research_stage = "completed"
        node.research_error = ""
        node.research_active_source_id = None
        node.research_result = result_wire
        return node

    def fail_web_research_run(self, node_id: str, *, cancelled: bool, message: str) -> SceneNode:
        """Land a failed or cancelled run. research_result is deliberately
        left untouched (stale-while-revalidate - see start_web_research_run's
        own docstring)."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.research_stage = "cancelled" if cancelled else "failed"
        node.research_error = message
        node.research_active_source_id = None
        return node

    # -- R5.2: artifact/drafter node -----------------------------------------

    def add_artifact_node(self, x: float, y: float, parent_id: str) -> SceneNode:
        """The Artifact/Drafter node's creation primitive - same required-
        parent posture as document/thinking/html/image/conversation/
        web_research nodes (never exists unparented). Title is always the
        fixed literal "Artifact" (mirrors conversation/web_research's own
        fixed titles - there is no meaningful single preview string before a
        document has ever been drafted). artifact_content starts empty; the
        document text only lands once complete_artifact_generation is
        called."""
        if parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title="Artifact",
            kind="artifact",
        )
        self.nodes[node_id] = node
        self.connect(parent_id, node_id)
        return node

    def append_artifact_user_message(self, node_id: str, text: str) -> SceneNode:
        """Append a real user instruction to an artifact node's history -
        mirrors append_conversation_user_message exactly (same shape, same
        error-handling style)."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.history.append({"role": "user", "content": str(text)})
        return node

    def send_artifact_message(self, node_id: str, text: str) -> SceneNode:
        """The Artifact node's own Send action: a thin wrapper over
        append_artifact_user_message, kept as a separate method (rather than
        only calling append_artifact_user_message directly from the WS
        wrapper) so the WS intent name lines up 1:1 with the domain method,
        the same way send_conversation_message/append_conversation_user_message
        already do for ConversationNode."""
        return self.append_artifact_user_message(node_id, text)

    def complete_artifact_generation(self, node_id: str, new_content, ai_message: str) -> SceneNode:
        """Land a successful generation turn: WHOLE-DOCUMENT REPLACE (never an
        append/merge - the model returns the entire document every turn, see
        the artifact_content field's own comment on SceneNode), plus append a
        real assistant turn to history. Raises SceneError if the node is
        gone - this WS wrapper does NOT pre-check liveness before calling
        this, same posture as send_conversation_message's own _on_reply, not
        web_research's more defensive pre-check pattern (there is no
        stage-stepper/persisted-error field here for a mid-flight delete to
        race against)."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.artifact_content = str(new_content)
        node.history.append({"role": "assistant", "content": str(ai_message)})
        return node

    # -- R5.3: gitlink node --------------------------------------------------
    #
    # canvas.py imports NOTHING from graphlink_plugins.gitlink - every method
    # below is pure state mutation on plain fields, matching how
    # apply_web_research_progress already does duck-typed mutation without
    # importing the domain package. The fingerprint mechanism itself
    # (_fingerprint_changes) lives in backend/agents.py, which DOES import
    # from graphlink_plugins.gitlink - same precedent as ArtifactAgent/
    # web_research.domain already being imported there, not here.

    def add_gitlink_node(self, x: float, y: float, parent_id: str) -> SceneNode:
        """The Gitlink node's creation primitive - same required-parent
        posture as document/thinking/html/image/conversation/web_research/
        artifact nodes (never exists unparented - confirmed against
        graphlink_plugin_portal.py's own no_selection_message/
        invalid_parent_message for Gitlink, there is no unparented/root form
        in the domain model). Title is always the fixed literal "Gitlink"
        (mirrors conversation/web_research/artifact's own fixed titles)."""
        if parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title="Gitlink",
            kind="gitlink",
        )
        self.nodes[node_id] = node
        self.connect(parent_id, node_id)
        return node

    def set_gitlink_local_root(self, node_id: str, local_root: str) -> SceneNode:
        """The one dedicated config setter Gitlink needs (see the design
        rationale on every other config field being passed as a direct
        action parameter instead): the user may type/paste a local checkout
        path BEFORE ever clicking Import/Build Context, with no other action
        call site to piggyback on."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.gitlink_local_root = str(local_root)
        return node

    def store_gitlink_repo_tree(self, node_id: str, repo: str, branch: str, file_paths: list[str]) -> SceneNode:
        """Lands a successful loadGitlinkRepoTree result: repo, branch
        (resolved server-side, including any default-branch lookup), and the
        scanned text-file path list."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.gitlink_repo = str(repo)
        node.gitlink_branch = str(branch)
        node.gitlink_repo_file_paths = list(file_paths)
        return node

    def store_gitlink_snapshot_root(self, node_id: str, repo: str, branch: str, local_root: str) -> SceneNode:
        """Lands a successful importGitlinkSnapshot result - sets
        repo/branch/local_root AND gitlink_imported_root (so a later run
        knows this path came from an import, matching legacy repo_state's
        imported_root concept)."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.gitlink_repo = str(repo)
        node.gitlink_branch = str(branch)
        node.gitlink_local_root = str(local_root)
        node.gitlink_imported_root = str(local_root)
        return node

    def store_gitlink_context(
        self,
        node_id: str,
        *,
        scope_mode: str,
        selected_paths: list[str],
        context_xml: str,
        context_stats: dict[str, Any],
        context_summary: str,
    ) -> SceneNode:
        """Lands a successful buildGitlinkContext result: scope_mode,
        selected_paths, and all three context_* fields. context_stats is
        stringified value-by-value here - repository.py's
        build_context_bundle returns a mixed int/str dict, but the wire field
        this feeds (scene_payload()'s "gitlinkContextStats") must stay
        honestly dict[str, str] for the codegen'd validator (see the field's
        own comment on SceneNode).

        R5.3 post-review FIX 6: gitlink_context_version is incremented
        UNCONDITIONALLY every time this method runs - a genuine monotonic
        counter, never reset, never skipped - closing a real bug
        gitlink_context_summary alone could not: two different Build Context
        results (e.g. selecting a different single file each time) can
        produce an IDENTICAL summary string (see that field's own comment on
        SceneNode), which was tricking the frontend's lazy-fetch-once guard
        into skipping a real refetch and showing stale XML."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.gitlink_scope_mode = str(scope_mode)
        node.gitlink_selected_paths = list(selected_paths)
        node.gitlink_context_xml = str(context_xml)
        node.gitlink_context_stats = {str(k): str(v) for k, v in (context_stats or {}).items()}
        node.gitlink_context_summary = str(context_summary)
        node.gitlink_context_version += 1
        return node

    def fetch_gitlink_context_xml(self, node_id: str) -> str:
        """The read-side of the lazy fetch: gitlink_context_xml is EXCLUDED
        from scene_payload() (see the field's own comment on SceneNode) - this
        is the only way the frontend ever gets the full text, via the
        read-only fetchGitlinkContext intent."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        return node.gitlink_context_xml

    def start_gitlink_run(self, node_id: str, task_prompt: str) -> SceneNode:
        """Begin one Generate Change Set run: stores the task prompt and
        clears any previous error. Deliberately does NOT touch
        gitlink_pending_changes/gitlink_proposal_markdown/
        gitlink_change_fingerprint here - those only change once
        complete_gitlink_run lands a real result, same stale-while-revalidate
        posture web research's own start_web_research_run documents for
        research_result."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "gitlink":
            raise SceneError(f"node is not a gitlink node: {node_id}")
        node.gitlink_task_prompt = str(task_prompt)
        node.gitlink_error = ""
        return node

    def complete_gitlink_run(
        self,
        node_id: str,
        proposal_markdown: str,
        pending_changes: list[dict[str, Any]],
        preview_text: str,
        fingerprint: str | None,
        local_root: str,
    ) -> SceneNode:
        """Land a successful run. proposal_markdown/pending_changes/
        preview_text are always set. If pending_changes is non-empty:
        change_state becomes "previewed", fingerprint is recorded, AND
        (R5.3 post-review FIX 2) gitlink_change_local_root records the
        EXACT local_root this run used - the write-destination binding
        start_gitlink_apply's fourth check enforces, since the fingerprint
        alone says nothing about where the content is written. If
        pending_changes is empty (the agent's own write_intent came back
        no_changes or blocked): change_state becomes "draft" and both
        fingerprint and local_root are cleared - mirrors legacy
        set_proposal's own unconditional `change_state = PREVIEWED if
        pending_changes else DRAFT` exactly (an empty proposal is never
        something to approve), extended so an empty proposal never leaves a
        dangling local_root binding behind either.

        `local_root` is compared as raw trimmed text against
        start_gitlink_apply's own local_root_text - stored stripped here so
        that comparison lines up exactly."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.gitlink_proposal_markdown = str(proposal_markdown)
        node.gitlink_pending_changes = list(pending_changes or [])
        node.gitlink_preview_text = str(preview_text)
        if node.gitlink_pending_changes:
            node.gitlink_change_state = "previewed"
            node.gitlink_change_fingerprint = fingerprint
            node.gitlink_change_local_root = str(local_root).strip()
        else:
            node.gitlink_change_state = "draft"
            node.gitlink_change_fingerprint = None
            node.gitlink_change_local_root = None
        return node

    def fail_gitlink_run(self, node_id: str, message: str) -> SceneNode | None:
        """No-op (return None without raising) if the node is gone - a
        background failure landing after node deletion should be silent,
        matching the more defensive posture used for other failure-only
        paths in this file (e.g. apply_web_research_progress). Deliberately
        does NOT clear any existing pending_changes/proposal_markdown/
        change_state - a failed re-run must never wipe out a previously
        staged, still-valid proposal; only the error banner reflects the
        new failure."""
        node = self.nodes.get(node_id)
        if node is None:
            return None
        node.gitlink_error = str(message)
        return node

    def complete_gitlink_apply(self, node_id: str, written_files: int) -> SceneNode:
        """Land a successful apply: change_state becomes "applied", error is
        cleared.

        R5.3 post-review FIX 1 (CRITICAL): ALSO clears gitlink_pending_changes
        and gitlink_change_fingerprint - a successful Apply must invalidate
        the approval it just consumed, or the exact same already-applied
        change set could be replayed via a second applyGitlinkChanges call
        (start_gitlink_apply's fingerprint check would still pass, since
        nothing here previously changed after a successful write).
        gitlink_change_local_root is cleared alongside them (R5.3 post-review
        FIX 2) - a cleared approval must have no dangling bound fields.
        gitlink_proposal_markdown/gitlink_preview_text are DELIBERATELY left
        untouched - they remain visible as a historical record of what was
        applied."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.gitlink_change_state = "applied"
        node.gitlink_error = ""
        node.gitlink_pending_changes = []
        node.gitlink_change_fingerprint = None
        node.gitlink_change_local_root = None
        return node

    def fail_gitlink_apply(self, node_id: str, message: str) -> SceneNode | None:
        """No-op if the node is gone. Reverts change_state to "previewed"
        (NEVER silently "applied"), CLEARS gitlink_change_fingerprint (so a
        stale approval can never be replayed) and gitlink_change_local_root
        (R5.3 post-review FIX 2 - a cleared approval must have no dangling
        bound fields), and sets gitlink_error verbatim. Handles BOTH the
        fingerprint-mismatch refusal path, the local_root-mismatch refusal
        path, and the write-failure path identically - all three are "the
        apply did not happen, here is why"."""
        node = self.nodes.get(node_id)
        if node is None:
            return None
        node.gitlink_change_state = "previewed"
        node.gitlink_change_fingerprint = None
        node.gitlink_change_local_root = None
        node.gitlink_error = str(message)
        return node

    # -- R5.4: Py-Coder node --------------------------------------------------
    #
    # canvas.py imports NOTHING from graphlink_plugins.pycoder - every method
    # below is pure state mutation on plain fields, same posture as the
    # Gitlink section above (apply_web_research_progress's own duck-typed
    # mutation is the original precedent). The actual REPL/agent dispatch
    # lives in backend/agents.py, which DOES import from
    # graphlink_plugins.pycoder.domain.
    #
    # R5.4 post-review FIX 3: request_pycoder_approval (and its Execution
    # Sandbox twin, request_code_sandbox_approval, below) were DELETED here -
    # confirmed genuinely dead code (grepped the whole repo: their only
    # references were this definition and their own dedicated unit tests,
    # zero real call sites). The human-approval gate that actually runs
    # mutates node.pycoder_code/pycoder_awaiting_approval directly inline
    # inside AgentDispatcher.start_pycoder_run (backend/agents.py) - these two
    # SceneDocument methods were a second, never-wired copy of that same
    # mutation, built ahead of the live dispatch path and then never rewired
    # to it. Removing dead code is the correct fix here, not building a
    # redundant call site just to keep them alive.

    def add_pycoder_node(self, x: float, y: float, parent_id: str) -> SceneNode:
        """The Py-Coder node's creation primitive - same required-parent
        posture as every R5 sibling (Web Research/Artifact/Gitlink): never
        exists unparented. Title is always the fixed literal "Py-Coder"
        (matches backend/plugins.py's own plugin display name)."""
        if parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title="Py-Coder",
            kind="pycoder",
        )
        self.nodes[node_id] = node
        self.connect(parent_id, node_id)
        return node

    def set_pycoder_mode(self, node_id: str, mode: str) -> SceneNode:
        """The mode toggle (ai_driven <-> manual). Raises SceneError on an
        unrecognized mode string - mirrors set_font's own unknown-value
        rejection shape (raise, don't silently coerce)."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if mode not in ("ai_driven", "manual"):
            raise SceneError(f"unknown pycoder mode: {mode}")
        node.pycoder_mode = str(mode)
        return node

    def start_pycoder_run(self, node_id: str, input_text: str) -> SceneNode:
        """Begin one Run: stores input_text into the field the CURRENT mode
        actually reads at dispatch time - pycoder_prompt for ai_driven (the
        natural-language ask), pycoder_code for manual (the hand-typed code
        that will execute verbatim) - and clears any previous error. Mirrors
        start_gitlink_run's own "store the input, clear the error, leave
        everything else stale-while-revalidate" posture."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "pycoder":
            raise SceneError(f"node is not a pycoder node: {node_id}")
        if node.pycoder_mode == "manual":
            node.pycoder_code = str(input_text)
        else:
            node.pycoder_prompt = str(input_text)
        node.pycoder_error = ""
        return node

    def complete_pycoder_run(
        self, node_id: str, code: str, output: str, analysis: str, last_run_failed: bool
    ) -> SceneNode | None:
        """Land a successful (or exhausted-repair-loop) run: code/output/
        analysis/last_run_failed are always set verbatim, awaiting_approval
        is cleared (the gate this run was paused on, if any, is resolved by
        definition once a result lands), and any stale error banner is
        cleared. Silent no-op if the node is gone - same posture as
        fail_web_research_run's own liveness handling for a background
        result landing after deletion."""
        node = self.nodes.get(node_id)
        if node is None:
            return None
        node.pycoder_code = str(code)
        node.pycoder_output = str(output)
        node.pycoder_analysis = str(analysis)
        node.pycoder_last_run_failed = bool(last_run_failed)
        node.pycoder_awaiting_approval = False
        node.pycoder_error = ""
        return node

    def fail_pycoder_run(self, node_id: str, message: str) -> SceneNode | None:
        """Land a failed (or denied-approval, or cancelled) run.
        awaiting_approval is ALWAYS cleared here too - a denied/cancelled
        approval must not leave the node stuck showing the approval prompt
        forever. Deliberately does NOT clear pycoder_code/pycoder_output/
        pycoder_analysis - a failed re-run must never wipe out a previously
        completed result, only the error banner reflects the new failure
        (stale-while-revalidate, same posture as fail_gitlink_run)."""
        node = self.nodes.get(node_id)
        if node is None:
            return None
        node.pycoder_awaiting_approval = False
        node.pycoder_error = str(message)
        return node

    # -- R5.4: Execution Sandbox node ------------------------------------------
    #
    # Same import posture as the Py-Coder section above: canvas.py imports
    # NOTHING from graphlink_plugins.code_sandbox.

    def add_code_sandbox_node(self, x: float, y: float, parent_id: str) -> SceneNode:
        """The Execution Sandbox node's creation primitive - same
        required-parent posture as every R5 sibling. Title is always the
        fixed literal "Execution Sandbox" (matches backend/plugins.py's own
        plugin display name). code_sandbox_sandbox_id is minted here, ONCE,
        at creation time - a short uuid4 hex used purely as this node's
        sandbox directory name (VirtualEnvSandbox re-sanitizes it again on
        its own side, but a short, already-safe id keeps the on-disk path
        short and human-scannable)."""
        if parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title="Execution Sandbox",
            kind="code_sandbox",
            code_sandbox_sandbox_id=uuid.uuid4().hex[:12],
        )
        self.nodes[node_id] = node
        self.connect(parent_id, node_id)
        return node

    def set_code_sandbox_requirements(self, node_id: str, requirements_text: str) -> SceneNode:
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.code_sandbox_requirements = str(requirements_text)
        return node

    def start_code_sandbox_run(self, node_id: str, input_text: str) -> SceneNode:
        """Begin one Run: stores input_text into code_sandbox_prompt (there is
        no mode-dependent field split here, unlike Py-Coder - see this
        section's own header comment for why) and clears any previous error.
        Deliberately does NOT touch code_sandbox_code here - the dispatch
        method decides generate-vs-reuse by reading the EXISTING
        code_sandbox_code value at call time, so this must not overwrite it
        before that decision is made."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "code_sandbox":
            raise SceneError(f"node is not a code_sandbox node: {node_id}")
        node.code_sandbox_prompt = str(input_text)
        node.code_sandbox_error = ""
        return node

    def complete_code_sandbox_run(self, node_id: str, code: str, output: str, analysis: str) -> SceneNode | None:
        """Land a successful run - mirrors complete_pycoder_run exactly,
        minus the last_run_failed flag (Execution Sandbox has no such field;
        an unrecovered failure after exhausting its own repair attempts
        surfaces as a failed run, see AgentDispatcher.start_code_sandbox_run,
        not as a "succeeded but flagged" result the way Py-Coder's repair
        loop does)."""
        node = self.nodes.get(node_id)
        if node is None:
            return None
        node.code_sandbox_code = str(code)
        node.code_sandbox_output = str(output)
        node.code_sandbox_analysis = str(analysis)
        node.code_sandbox_awaiting_approval = False
        node.code_sandbox_approval_requirements = ""
        node.code_sandbox_error = ""
        return node

    def fail_code_sandbox_run(self, node_id: str, message: str) -> SceneNode | None:
        """Land a failed (or denied-approval, or cancelled) run - mirrors
        fail_pycoder_run exactly (same stale-while-revalidate posture, same
        unconditional awaiting_approval clear)."""
        node = self.nodes.get(node_id)
        if node is None:
            return None
        node.code_sandbox_awaiting_approval = False
        node.code_sandbox_approval_requirements = ""
        node.code_sandbox_error = str(message)
        return node

    # -- R6.1: Notes/Frames/Containers ----------------------------------------
    #
    # Legacy canvas decorations, ported here for the first time (never
    # covered by any prior increment). Notes are free-floating markdown
    # sticky-notes with no parent-required posture (unlike almost every R3+
    # content kind). Frames/containers are "group" nodes: they never contain
    # their members via any React Flow parent/extent mechanism - membership
    # is plain data (item_ids) and enclosure is plain server-side math
    # (_recompute_group_bounds below), matching the legacy behavior of
    # always auto-growing to enclose members, never clipping them.

    def add_note(
        self,
        x: float,
        y: float,
        *,
        is_system_prompt: bool = False,
        is_summary_note: bool = False,
    ) -> SceneNode:
        """A note's creation primitive. UNLIKE every R3+ content kind, no
        parent is required or accepted - notes are free-floating, never
        branch-point children (mirrors the legacy note widget, which the
        canvas places directly, not via a ChatNode-anchored connection)."""
        node_id = f"n{next(self._counter)}"
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title="Note",
            kind="note",
            content="Add note...",
            is_system_prompt=bool(is_system_prompt),
            is_summary_note=bool(is_summary_note),
        )
        self.nodes[node_id] = node
        return node

    def set_note_content(self, node_id: str, content: str) -> None:
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.content = str(content)

    def _bbox_of_members(self, item_ids: list[str]) -> tuple[float, float, float, float]:
        """Compute the padded union rect (x, y, width, height) enclosing
        every member id's ESTIMATED footprint - GROUP_MEMBER_DEFAULT_WIDTH/
        HEIGHT, since no current SceneNode kind carries its own width/height
        field (see that constant's own comment). Stale/unknown member ids
        (a member deleted out from under a group between mutations) are
        silently skipped, never raise - a bbox recompute must never crash on
        a dangling id. Falls back to a small default rect anchored at the
        origin when item_ids is empty or every id is stale, so callers
        (including resize_frame's own minimum-size clamp) always get a
        well-defined rect back."""
        left = top = right = bottom = None
        for member_id in item_ids:
            member = self.nodes.get(member_id)
            if member is None:
                continue
            mx1, my1 = member.x, member.y
            mx2 = member.x + GROUP_MEMBER_DEFAULT_WIDTH
            my2 = member.y + GROUP_MEMBER_DEFAULT_HEIGHT
            left = mx1 if left is None else min(left, mx1)
            top = my1 if top is None else min(top, my1)
            right = mx2 if right is None else max(right, mx2)
            bottom = my2 if bottom is None else max(bottom, my2)
        if left is None:
            left = top = 0.0
            right, bottom = GROUP_MEMBER_DEFAULT_WIDTH, GROUP_MEMBER_DEFAULT_HEIGHT
        x = left - GROUP_PADDING
        y = top - GROUP_PADDING_TOP
        width = (right - left) + GROUP_PADDING * 2
        height = (bottom - top) + GROUP_PADDING_TOP + GROUP_PADDING
        return x, y, width, height

    def _recompute_group_bounds(self, node_id: str) -> None:
        """The core "legacy never clips, always auto-grows to enclose
        members" recompute - plain server-side math, NOT a React Flow
        extent/parentId feature. Silent no-op for an unknown id or a
        non-frame/container kind (defensive: every call site below already
        only ever calls this with a live frame/container id, but a caller
        that races a delete must never crash here).

        Three cases, in priority order:
        1. Collapsed: skip the bbox computation entirely, snap to the fixed
           GROUP_COLLAPSED_WIDTH/HEIGHT pill size. x/y are left untouched -
           a collapsed pill stays wherever it was expanded from.
        2. Frame with a manual size override (group_manual_width/height both
           set): the override's WIDTH/HEIGHT stay exactly as manually set,
           but x/y still re-centers so the manually-sized rect stays
           centered on the live bbox-of-members center point.
        3. Otherwise (auto-fit - every container, and every frame with no
           override): x/y/width/height all come straight from the padded
           bbox-of-members.
        """
        node = self.nodes.get(node_id)
        if node is None or node.kind not in ("frame", "container"):
            return
        if node.is_collapsed:
            node.group_width = GROUP_COLLAPSED_WIDTH
            node.group_height = GROUP_COLLAPSED_HEIGHT
            return
        if node.kind == "frame" and node.group_manual_width is not None and node.group_manual_height is not None:
            bx, by, bw, bh = self._bbox_of_members(node.item_ids)
            center_x, center_y = bx + bw / 2.0, by + bh / 2.0
            node.group_width = node.group_manual_width
            node.group_height = node.group_manual_height
            node.x = center_x - node.group_width / 2.0
            node.y = center_y - node.group_height / 2.0
            return
        x, y, width, height = self._bbox_of_members(node.item_ids)
        node.x, node.y, node.group_width, node.group_height = x, y, width, height

    def _detach_from_existing_group(self, member_id: str, group_kind: str) -> None:
        """Part of create_frame/create_container's shared validation: if
        member_id is already tracked by some OTHER node of the SAME
        group_kind ("frame" or "container" - membership is scoped per kind,
        since a node may belong to at most one frame AND at most one
        container simultaneously, per item_ids's own field comment), detach
        it from that group first. If the detach empties that group's
        item_ids, the now-empty group is deleted too (mirrors legacy
        auto-delete-when-empty); otherwise the group's bounds are
        recomputed to reflect its shrunk membership. A node can be a member
        of at most one group of a given kind, so at most one match exists -
        the loop stops at the first hit."""
        for other in list(self.nodes.values()):
            if other.kind != group_kind or member_id not in other.item_ids:
                continue
            other.item_ids = [i for i in other.item_ids if i != member_id]
            if not other.item_ids:
                self.nodes.pop(other.id, None)
            else:
                self._recompute_group_bounds(other.id)
            break

    def create_frame(self, item_ids: list[str]) -> SceneNode:
        """Group an existing set of nodes into a new frame. Validates every
        id exists BEFORE any mutation (fail fast, no partial detach), then
        detaches each from any frame it was already a member of (see
        _detach_from_existing_group). is_locked defaults True (the legacy
        frame default - locked). Initial x/y/width/height come from the
        padded bbox-of-members, computed immediately via
        _recompute_group_bounds right after construction."""
        ids = list(item_ids)
        for member_id in ids:
            if member_id not in self.nodes:
                raise SceneError(f"unknown member node: {member_id}")
        for member_id in ids:
            self._detach_from_existing_group(member_id, "frame")
        node_id = f"n{next(self._counter)}"
        node = SceneNode(
            id=node_id,
            x=0.0,
            y=0.0,
            title="Frame",
            kind="frame",
            content="Add note...",
            item_ids=ids,
            is_locked=True,
            is_collapsed=False,
        )
        self.nodes[node_id] = node
        self._recompute_group_bounds(node_id)
        return node

    def create_container(self, item_ids: list[str]) -> SceneNode:
        """Group an existing set of nodes into a new container. Same
        validation/detach posture as create_frame, scoped to "container"
        membership instead of "frame" - a node may simultaneously be a
        member of one frame AND one container, so this never touches a
        node's frame membership. UNLIKE create_frame, item_ids here may
        include note/frame/container ids too - container membership can
        nest (a container may hold another container or a frame as one of
        its members). is_locked is left at its dataclass default (True) but
        is MEANINGLESS for containers - see the field's own comment; no
        toggle_container_lock exists and none should be added."""
        ids = list(item_ids)
        for member_id in ids:
            if member_id not in self.nodes:
                raise SceneError(f"unknown member node: {member_id}")
        for member_id in ids:
            self._detach_from_existing_group(member_id, "container")
        node_id = f"n{next(self._counter)}"
        node = SceneNode(
            id=node_id,
            x=0.0,
            y=0.0,
            title="Container",
            kind="container",
            content="New Container",
            item_ids=ids,
            is_collapsed=False,
        )
        self.nodes[node_id] = node
        self._recompute_group_bounds(node_id)
        return node

    def set_group_label(self, node_id: str, text: str) -> None:
        """Sets the header-note / title text for a frame or container -
        reuses the generic `content` field, same reuse pattern as R3.5's
        code text / R3.13's thinking text living in that same field."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind not in ("frame", "container"):
            raise SceneError(f"node is not a frame/container node: {node_id}")
        node.content = str(text)

    def set_group_color(self, node_id: str, color: str | None, header_color: str | None) -> None:
        """Shared color setter for note/frame/container kinds - see the
        color/header_color fields' own comments on SceneNode for what each
        controls. Either may be cleared back to None (default) by passing
        None explicitly."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind not in ("note", "frame", "container"):
            raise SceneError(f"node is not a note/frame/container node: {node_id}")
        node.color = str(color) if color is not None else None
        node.header_color = str(header_color) if header_color is not None else None

    def toggle_frame_lock(self, node_id: str) -> None:
        """Frame kind only. Recomputes bounds afterward for consistency with
        every other group mutator here - locked vs unlocked does not change
        the bbox math itself in this implementation (there is no
        drag-suppression concept at the domain-model layer, only at the
        frontend interaction layer), but keeping the call is cheap and
        future-proofs a later change to that math."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "frame":
            raise SceneError(f"node is not a frame node: {node_id}")
        node.is_locked = not node.is_locked
        self._recompute_group_bounds(node_id)

    def toggle_group_collapsed(self, node_id: str) -> None:
        """Shared frame/container collapse toggle. A single call to
        _recompute_group_bounds after flipping is_collapsed correctly
        handles BOTH directions: collapsing snaps to the fixed pill size
        (that helper's own is_collapsed branch), expanding recomputes from
        the bbox of members - respecting a frame's manual size override if
        one is still set (group_manual_width/height survive the collapsed
        state untouched, see those fields' own comments)."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind not in ("frame", "container"):
            raise SceneError(f"node is not a frame/container node: {node_id}")
        node.is_collapsed = not node.is_collapsed
        self._recompute_group_bounds(node_id)

    def resize_frame(self, node_id: str, width: float, height: float) -> None:
        """Frame kind only. Records a manual size override, clamped to never
        go below the padded bbox-of-members minimum size - computed via the
        exact same _bbox_of_members helper the auto-fit path itself uses, so
        "minimum" and "auto-fit size" can never drift apart. Recomputes
        immediately afterward so x/y re-centers on the current member bbox
        around the new size right away, same posture as toggle_frame_lock's
        own trailing recompute call."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "frame":
            raise SceneError(f"node is not a frame node: {node_id}")
        _, _, min_width, min_height = self._bbox_of_members(node.item_ids)
        node.group_manual_width = max(float(width), min_width)
        node.group_manual_height = max(float(height), min_height)
        self._recompute_group_bounds(node_id)

    def fit_frame_to_content(self, node_id: str) -> None:
        """Frame kind only. Clears the manual size override back to None
        (auto-fit) and forces an immediate bbox recompute - the exact
        inverse of resize_frame."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "frame":
            raise SceneError(f"node is not a frame node: {node_id}")
        node.group_manual_width = None
        node.group_manual_height = None
        self._recompute_group_bounds(node_id)

    def ungroup(self, node_id: str) -> None:
        """Deletes a frame/container node itself. Members are NOT deleted
        and keep their current absolute x/y positions unchanged - they
        simply stop being tracked in any item_ids list (the deleted group's
        own item_ids goes with it). Also drops any edges touching the group
        node, mirroring remove_nodes' own "edges die with either endpoint"
        invariant (frame/container nodes are not normally edge-connected the
        way chat nodes are, but this keeps the invariant airtight rather
        than relying on that never happening)."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind not in ("frame", "container"):
            raise SceneError(f"node is not a frame/container node: {node_id}")
        del self.nodes[node_id]
        self.edges = {
            eid: e for eid, e in self.edges.items()
            if e.source != node_id and e.target != node_id
        }
        # A group can itself be a member of an outer container (nesting) -
        # detach it there too, or the outer group is left tracking a
        # dangling id, same failure mode remove_nodes already guards
        # against for every other node kind.
        self._detach_node_from_membership(node_id)

    # -- R6.2: chart node ----------------------------------------------------

    def add_chart_node(
        self,
        x: float,
        y: float,
        parent_id: str | None,
        chart_type: str,
        chart_data: dict[str, Any],
        *,
        chart_error: str = "",
    ) -> SceneNode:
        """The Chart node's creation primitive - same required-parent
        posture as every other branch-point-child kind (web_research/
        artifact/gitlink/pycoder/code_sandbox above) for every NEW chart:
        the UI-driven generateChart intent always passes a real parent_id,
        since a chart is always generated FROM some other node's content in
        that flow. chart_type MUST be one of SUPPORTED_CHART_TYPES
        (SceneError otherwise, same "validate up front, never construct a
        half-invalid node" posture create_frame/create_container use for
        their own item_ids checks).

        R6.4: parent_id is None-able for the session LOADER only - legacy
        genuinely allows a chart with no parent at all (both
        parent_node_index/parent_node_id absent in the persisted payload is
        a real, valid legacy state, confirmed by recon), which the original
        required-parent signature could not represent. When parent_id is
        None, no parent-existence check runs and no edge is created -
        chart_source_node_id stays "" rather than getting a real node id.

        chart_data is assumed ALREADY canonicalized by the CALLER - this
        method deliberately does NOT call canonicalize_chart_data itself
        (see chart_data's own field comment on SceneNode for the full
        reasoning: the WS-intent wrapper needs to be able to catch
        ChartDataError itself and still create a placeholder chart with
        chart_error set, rather than have creation abort entirely).

        Title mirrors legacy ChartItem's own `self.title = str(self.data.
        get("title") or "Chart")` - the chart's own title field if present,
        else the literal "Chart" (not a chart-type-specific default; that is
        genuinely what legacy does).

        Renders the display PNG immediately at the freshly-constructed
        node's OWN chart_width/chart_height (the dataclass defaults, 680x500
        - read off `node`, not a second literal, so they can never drift
        apart - see those fields' own comments), stores the bytes in the
        SAME image_assets dict R3.21's image nodes already use (reused, not
        a parallel store), and sets chart_asset_version = 1 for this first
        render."""
        if parent_id is not None and parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        normalized_type = str(chart_type or "").strip().lower()
        if normalized_type not in SUPPORTED_CHART_TYPES:
            raise SceneError(f"unsupported chart type: {chart_type}")

        node_id = f"n{next(self._counter)}"
        safe_chart_data = dict(chart_data) if isinstance(chart_data, dict) else {}
        title = str(safe_chart_data.get("title") or "Chart")
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title=title,
            kind="chart",
            chart_type=normalized_type,
            chart_data=safe_chart_data,
            chart_error=str(chart_error),
            chart_source_node_id=parent_id or "",
        )

        png_bytes = render_chart_png(
            node.chart_type, node.chart_data, node.chart_width, node.chart_height, dpi_scale=1.0,
        )
        asset_id = f"chart{uuid.uuid4().hex}"
        self.image_assets[asset_id] = (png_bytes, "image/png")
        node.chart_asset_id = asset_id
        node.chart_asset_version = 1

        self.nodes[node_id] = node
        if parent_id is not None:
            self.connect(parent_id, node_id)
        return node

    def resize_chart(self, node_id: str, width: float, height: float) -> None:
        """Chart kind only (SceneError otherwise). Clamps (width, height)
        into [CHART_MIN_WIDTH, CHART_MAX_WIDTH] / [CHART_MIN_HEIGHT,
        CHART_MAX_HEIGHT]. If chart_aspect_locked, preserves the aspect
        ratio of the REQUESTED (width, height) pair AS SENT - the frontend/
        NodeResizer is responsible for computing a ratio-correct pair before
        ever calling this; UNLIKE legacy ChartItem._clamp_size (which
        consults self.resize_start_aspect_ratio, a value frozen at drag
        START), this method has no concept of an in-progress gesture, so it
        only ever has the two numbers it was given to work from. After the
        plain min/max clamp, if aspect-locked, re-derives whichever
        dimension keeps the REQUESTED ratio relative to the (already-
        clamped) other dimension - same "pick whichever correction moves the
        clamped pair least" algorithm legacy's own _clamp_size uses - then
        re-clamps once more, so the final stored size never violates either
        the lock or the min/max bounds even after that re-derivation.

        Re-renders the PNG at the final clamped size, OVERWRITING
        self.image_assets[chart_asset_id] IN PLACE (same id, new bytes - the
        old bytes are simply replaced, never left behind) and increments
        chart_asset_version so the frontend can cache-bust the <img> src."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "chart":
            raise SceneError(f"node is not a chart node: {node_id}")

        requested_width = float(width)
        requested_height = float(height)
        clamped_width = min(CHART_MAX_WIDTH, max(CHART_MIN_WIDTH, requested_width))
        clamped_height = min(CHART_MAX_HEIGHT, max(CHART_MIN_HEIGHT, requested_height))

        if node.chart_aspect_locked and requested_width > 0 and requested_height > 0:
            aspect_ratio = requested_width / requested_height
            width_from_height = clamped_height * aspect_ratio
            height_from_width = clamped_width / aspect_ratio
            if abs(width_from_height - clamped_width) < abs(height_from_width - clamped_height):
                clamped_width = width_from_height
                clamped_height = clamped_width / aspect_ratio
            else:
                clamped_height = height_from_width
                clamped_width = clamped_height * aspect_ratio
            # Re-deriving one dimension from the other can overshoot the
            # opposite bound for an extreme aspect ratio - one more clamp
            # keeps the final pair inside both bounds unconditionally.
            clamped_width = min(CHART_MAX_WIDTH, max(CHART_MIN_WIDTH, clamped_width))
            clamped_height = min(CHART_MAX_HEIGHT, max(CHART_MIN_HEIGHT, clamped_height))

        node.chart_width = clamped_width
        node.chart_height = clamped_height

        png_bytes = render_chart_png(
            node.chart_type, node.chart_data, node.chart_width, node.chart_height, dpi_scale=1.0,
        )
        self.image_assets[node.chart_asset_id] = (png_bytes, "image/png")
        node.chart_asset_version += 1

    def toggle_chart_aspect_lock(self, node_id: str) -> None:
        """Chart kind only (SceneError otherwise). Flips chart_aspect_locked.
        No re-render - the current PNG is still correct for the current
        size regardless of the lock flag; only a later resize_chart call's
        BEHAVIOR changes."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "chart":
            raise SceneError(f"node is not a chart node: {node_id}")
        node.chart_aspect_locked = not node.chart_aspect_locked

    def _branch_parent_edge(self, node_id: str) -> SceneEdge | None:
        """R6.1: the shared 'find the edge whose target == node_id' lookup
        chat_branch_history/get_branch_root/regenerate_response/
        delete_chat_node all use to walk BRANCH structure (parent -> child,
        source -> target) - factored out once this increment introduced a
        second, unrelated edge shape that can also target a chat node: a
        System Prompt note's note -> root edge (backend/plugins.py's
        "System Prompt" branch, direction confirmed against
        backend/agents.py's _resolve_branch_system_prompt). That edge is
        METADATA (which note decorates this root) - a note is never a real
        branch "parent", so a branch history/root walk must never traverse
        through it (doing so would both corrupt chat_branch_history's real
        conversation_history with the note's own content as a fake turn, AND
        make get_branch_root resolve to the note instead of the true chat
        root, silently defeating the override it exists to find). Skips
        edges whose source is a kind="note" node for exactly that reason;
        otherwise identical to the plain `next((e for e in self.edges.
        values() if e.target == node_id), None)` pattern this replaces."""
        for edge in self.edges.values():
            if edge.target != node_id:
                continue
            source_node = self.nodes.get(edge.source)
            if source_node is not None and source_node.kind == "note":
                continue
            return edge
        return None

    def delete_chat_node(self, node_id: str) -> None:
        """Delete one chat node WITHOUT orphaning its branch: children are
        re-parented to the deleted node's own parent (or become roots if it
        had none), mirroring ChatScene.delete_chat_node's load-bearing
        reparent rule - a plain remove_nodes cascade-delete would sever every
        child branch instead of splicing them back together."""
        if node_id not in self.nodes:
            raise SceneError(f"unknown node: {node_id}")
        parent_edge = self._branch_parent_edge(node_id)
        parent_id = parent_edge.source if parent_edge is not None else None
        child_edges = [e for e in self.edges.values() if e.source == node_id]
        # R6.1: a System Prompt note attached to node_id (a note -> node_id
        # edge - the exact shape _branch_parent_edge deliberately skips
        # above, so it is NOT parent_edge) still dies with this endpoint,
        # same "edges die with either endpoint" invariant remove_nodes
        # already enforces elsewhere - otherwise it would dangle, pointing
        # at a node_id that no longer exists in self.nodes. The note ITSELF
        # is not deleted (mirrors ungroup's own "detach, don't
        # cascade-delete" precedent) - only this now-stale edge.
        note_edges = []
        for edge in self.edges.values():
            if edge.target != node_id:
                continue
            source_node = self.nodes.get(edge.source)
            if source_node is not None and source_node.kind == "note":
                note_edges.append(edge)

        for edge in [parent_edge, *child_edges, *note_edges]:
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
        self._detach_node_from_membership(node_id)

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

    def chat_branch_history(self, node_id: str) -> list[dict]:
        """Walk the branch from node_id up to its root, collecting one
        {"role", "content"} entry per node visited (including node_id
        itself), then reverse so the result reads root-to-leaf (oldest
        message first) - the direct new-backend replacement for legacy
        conversation_history built by walking the QGraphicsScene parent
        chain. Follows edges generically (by target match) rather than
        asserting a chat-kind node shape: the walk itself only ever visits
        chat-kind nodes in practice given how they are the only kind chained
        this way, but a bad/unknown node_id or a stray edge shape should
        stop the walk quietly rather than raise."""
        history: list[dict] = []
        current_id: str | None = node_id
        while current_id is not None:
            node = self.nodes.get(current_id)
            if node is None:
                break
            history.append({"role": "user" if node.is_user else "assistant", "content": node.content})
            parent_edge = self._branch_parent_edge(current_id)
            current_id = parent_edge.source if parent_edge is not None else None
        history.reverse()
        return history

    def get_branch_root(self, node_id: str) -> SceneNode | None:
        """R6.1 addition (backend/agents.py's system-prompt-override
        resolution and backend/plugins.py's System Prompt plugin both need
        this same walk): find node_id's topmost ancestor by walking the
        parent-edge chain up - the SAME by-target-match walk chat_branch_
        history/regenerate_response/delete_chat_node already use (via
        _branch_parent_edge) - until reaching a node with no incoming parent
        edge. Returns node_id's own node when it already has no parent (it
        IS the root), or None for an unknown node_id. Deliberately generic,
        not scoped to kind="chat" - any node reachable via this edge-chain
        shape can be walked."""
        current_id: str | None = node_id
        root: SceneNode | None = None
        while current_id is not None:
            node = self.nodes.get(current_id)
            if node is None:
                break
            root = node
            parent_edge = self._branch_parent_edge(current_id)
            current_id = parent_edge.source if parent_edge is not None else None
        return root

    def regenerate_response(self, node_id: str) -> tuple[SceneNode, str]:
        """Validate + resolve a regenerate target. Mirrors legacy's regenerate_node
        single precondition (window_actions.py:512-514: no parent -> can't
        regenerate), extended with two defensive checks legacy cannot hit (it
        always holds a live scene-graph object, never a string id to resolve):
        unknown node_id, and a non-chat-kind node_id (code/document/etc. can
        never be regenerate targets directly - see Q2, the frontend always
        resolves to a chat-node id before calling in). All three raise
        SceneError; the WS-intent wrapper in register_canvas catches it and
        shows ONE friendly notification for all three cases - see that wrapper
        for why."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "chat":
            raise SceneError(f"node is not a chat node: {node_id}")
        parent_edge = self._branch_parent_edge(node_id)
        if parent_edge is None:
            raise SceneError(f"node has no parent and cannot be regenerated: {node_id}")
        return node, parent_edge.source

    def update_chat_node_content(self, node_id: str, content: str) -> SceneNode:
        """The regenerate primitive: mutate an EXISTING chat node's content in
        place - the first in-place mutation of a content-bearing field in this
        file (move_node/set_chat_collapsed/set_node_docked all mutate a
        position/flag, never displayed text). Scope confirmed against legacy's
        ChatNode.update_content (graphlink_nodes/graphlink_node_chat.py:677-686):
        sets content ONLY. Does not touch title (legacy's update_content never
        recomputes any title-like state either, and every other in-place mutator
        here already leaves title untouched post-creation - consistent, not a
        new carve-out). Does not touch is_user/is_collapsed/kind."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.content = str(content)
        return node

    def remove_associated_content_children(self, chat_node_id: str) -> None:
        """The regenerate teardown: remove every code/document/image/thinking
        node ONE HOP directly off chat_node_id. Built entirely on the existing
        generic remove_nodes (edge cleanup + image-asset eviction come free).
        Mirrors graphlink_scene.py's remove_associated_content_nodes exactly in
        SCOPE (one-hop only, same four kinds, no cascade to any grandchild) but
        resolved via this backend's one edge-encoded parent/child relationship
        instead of legacy's four parallel per-kind lists. html/conversation
        kinds are excluded on purpose - grep confirms neither ever has a
        parent_content_node attribute in legacy, so they structurally can never
        attach to a ChatNode this way."""
        child_ids = []
        for edge in self.edges.values():
            if edge.source != chat_node_id:
                continue
            child = self.nodes.get(edge.target)
            if child is not None and child.kind in ("code", "document", "image", "thinking"):
                child_ids.append(child.id)
        self.remove_nodes(child_ids)

    def resolve_generate_image(self, chat_node_id: str) -> tuple[str, str]:
        """'Generate Image from Text' target resolution (R4.4a). Returns
        (parent_chat_node_id, prompt) = (chat_node_id, node.content) - the
        selected ChatNode's own id becomes the new image's parent chat node,
        and its own text becomes the prompt, mirroring legacy's real
        "Generate Image from Text" entry point (window_actions.py's
        generate_image(chat_node), called with node.text as the prompt).
        Raises SceneError for an unknown node id or a non-chat kind
        (defensive - the frontend always resolves this from a real ChatNode's
        own menu, same posture as regenerate_response's own defensive checks
        above), and the SceneEmptyPromptError subclass specifically for
        empty/whitespace content - mirrors legacy's own "no text to use as a
        prompt" guard (window_actions.py:989-991), kept as a DISTINCT
        SceneError subclass (not a plain SceneError) so the WS wrapper in
        register_canvas can show a distinct message for this case without
        string-sniffing."""
        node = self.nodes.get(chat_node_id)
        if node is None:
            raise SceneError(f"unknown node: {chat_node_id}")
        if node.kind != "chat":
            raise SceneError(f"node is not a chat node: {chat_node_id}")
        if not node.content or not node.content.strip():
            raise SceneEmptyPromptError(f"node has no text to use as a prompt: {chat_node_id}")
        return chat_node_id, node.content

    def resolve_regenerate_image(self, image_node_id: str) -> tuple[str, str]:
        """'Regenerate Image' target resolution (R4.4a). Returns
        (parent_chat_node_id, prompt) = (the ImageNode's own parent chat node
        id via one-hop edge lookup, node.content - the ImageNode's OWN stored
        prompt). This is the deliberate improvement over legacy's real
        regenerate mechanism, which instead re-derives the prompt from the
        parent ChatNode's live .text - a real, reproducible legacy quirk that
        re-wraps its own wrapped "Generated image for prompt: ..." string on
        every subsequent regenerate. Raises SceneError for an unknown node
        id, a non-image kind, or a missing parent edge (defensive only -
        add_image_node requires parent_id, so an unparented image node can
        never actually be constructed; this exists purely so a future bug
        elsewhere fails loud instead of crashing downstream), and the
        SceneEmptyPromptError subclass for empty/whitespace content
        (defensive - mirrors legacy's own conditional-visibility guard `if
        parent_content_node and prompt` around showing the menu action at
        all)."""
        node = self.nodes.get(image_node_id)
        if node is None:
            raise SceneError(f"unknown node: {image_node_id}")
        if node.kind != "image":
            raise SceneError(f"node is not an image node: {image_node_id}")
        parent_edge = self._branch_parent_edge(image_node_id)
        if parent_edge is None:
            raise SceneError(f"image node has no parent: {image_node_id}")
        if not node.content or not node.content.strip():
            raise SceneEmptyPromptError(f"image node has no prompt to regenerate from: {image_node_id}")
        return parent_edge.source, node.content

    def add_generated_image_reply(
        self,
        parent_chat_node_id: str,
        prompt: str,
        image_bytes: bytes,
        mime_type: str = "image/png",
    ) -> tuple[SceneNode, SceneNode]:
        """The Generate/Regenerate Image success primitive (R4.4a) - mirrors
        legacy's handle_image_response exactly: unconditionally creates a NEW
        assistant ChatNode (content=f'Generated image for prompt: "{prompt}"',
        is_user=False, parent_id=parent_chat_node_id) then a NEW ImageNode
        (content=prompt, parent_id=<the new ChatNode's id>) - built entirely
        from the existing add_chat_node/add_image_node primitives, zero new
        mutation-in-place logic, matching this feature's create-new-nodes
        scope decision. Positions via the same MESSAGE_VERTICAL_SPACING
        offset convention send_message/regenerate_response's own new-child
        placement already uses. last_chat_node_id is DELIBERATELY untouched
        - mirrors legacy: handle_image_response never assigns
        self.current_node either, since image generation is side content,
        not a branch-continuation point (same posture as
        regenerate_response's own documented "last_chat_node_id:
        DELIBERATELY untouched"). Raises SceneError if parent_chat_node_id is
        unknown - defensive: a delete could race the in-flight generation
        request (see the mid-flight-delete handling in the WS wrapper in
        register_canvas)."""
        parent = self.nodes.get(parent_chat_node_id)
        if parent is None:
            raise SceneError(f"unknown parent node: {parent_chat_node_id}")
        ax, ay = parent.x, parent.y + MESSAGE_VERTICAL_SPACING
        chat_node = self.add_chat_node(
            ax, ay, f'Generated image for prompt: "{prompt}"', False, parent_id=parent_chat_node_id,
        )
        ix, iy = ax, ay + MESSAGE_VERTICAL_SPACING
        image_node = self.add_image_node(ix, iy, image_bytes, prompt, chat_node.id, mime_type=mime_type)
        return chat_node, image_node

    def set_chat_collapsed(self, node_id: str, collapsed: bool) -> None:
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.is_collapsed = bool(collapsed)
        # R6.1: unlike every other kind this generic setter already served,
        # a frame/container's is_collapsed also drives derived geometry
        # (the collapsed pill size vs the auto-fit/manual bbox) - recompute
        # here too so this stays correct no matter which entry point sets
        # it, rather than only being safe via toggle_group_collapsed.
        if node.kind in ("frame", "container"):
            self._recompute_group_bounds(node_id)

    def set_chat_scroll_value(self, node_id: str, value: float) -> None:
        """R6.3: persists a chat node's own scroll position within its
        content area. chat kind only (SceneError otherwise), matching every
        other kind-specific setter's guard pattern in this file (e.g.
        resize_chart/toggle_frame_lock)."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "chat":
            raise SceneError(f"node is not a chat node: {node_id}")
        node.chat_scroll_value = float(value)

    def set_node_docked(self, node_id: str, docked: bool) -> None:
        """R3.13: a single generic setter handling both dock (docked=True)
        and undock (docked=False) - mirrors set_chat_collapsed's generic-
        setter shape (despite its kind-specific name, it looks up ANY node by
        id with no kind restriction)."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.is_docked = bool(docked)

    def move_node(self, node_id: str, x: float, y: float) -> SceneNode:
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.x, node.y = float(x), float(y)
        # R6.1: keep every frame/container this node is a member of enclosing
        # it - a node is a member of at most one frame AND at most one
        # container simultaneously (see item_ids's own field comment), so
        # this is at most 2 matches, not an expensive scan; a plain
        # iteration over self.nodes.values() is correctness-first, no reverse
        # index needed for this dataset size.
        for group in self.nodes.values():
            if group.kind in ("frame", "container") and node_id in group.item_ids:
                self._recompute_group_bounds(group.id)
        return node

    def remove_nodes(self, node_ids: list[str]) -> None:
        for node_id in node_ids:
            node = self.nodes.pop(node_id, None)
            if node is not None:
                # Edges die with either endpoint - same invariant ChatScene
                # enforced on node removal.
                self.edges = {
                    eid: e
                    for eid, e in self.edges.items()
                    if e.source != node_id and e.target != node_id
                }
                # R3.21: an image node's bytes must not outlive the node -
                # evict its image_assets entry too, or a long session's
                # deleted images would accumulate in memory forever.
                if node.image_asset_id:
                    self.image_assets.pop(node.image_asset_id, None)
                # R6.2: a chart node's rendered PNG lives in the SAME
                # image_assets dict (reused, not a parallel store - see
                # chart_asset_id's own field comment on SceneNode) - same
                # leak-prevention reasoning as image_asset_id just above.
                if node.chart_asset_id:
                    self.image_assets.pop(node.chart_asset_id, None)
                self._detach_node_from_membership(node_id)

    def _detach_node_from_membership(self, node_id: str) -> None:
        """R6.1: if the deleted node was itself a frame/container, its own
        item_ids simply goes with it (already popped from self.nodes by the
        caller) - members are NOT cascade-deleted, they just stop being
        tracked, same "release, don't destroy" rule ungroup() uses. If the
        deleted node was instead a MEMBER of some other frame/container (or,
        since containers can nest, a group nested inside another group),
        detach it from that group's item_ids - auto-deleting the group if
        that empties it out (mirrors create_frame/create_container's own
        detach rule), else recomputing its bounds to reflect the shrunk
        membership. Shared by remove_nodes() and delete_chat_node() - the
        latter deletes via its own reparent-children path rather than
        remove_nodes, so it would otherwise leave stale item_ids behind.
        list(...) over a live view since a match can mutate self.nodes
        (popping an emptied group) mid-iteration."""
        for group in list(self.nodes.values()):
            if group.kind not in ("frame", "container"):
                continue
            if node_id not in group.item_ids:
                continue
            group.item_ids = [i for i in group.item_ids if i != node_id]
            if not group.item_ids:
                self.nodes.pop(group.id, None)
            else:
                self._recompute_group_bounds(group.id)

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

    def set_view_state(self, zoom_factor: float, scroll_x: float, scroll_y: float) -> None:
        """R6.3: plain setter for the canvas viewport's persisted zoom/
        scroll, no validation beyond basic float coercion - see
        zoom_factor's own field comment for why there is nothing meaningful
        to clamp server-side."""
        self.zoom_factor = float(zoom_factor)
        self.scroll_x = float(scroll_x)
        self.scroll_y = float(scroll_y)

    def add_session_tokens(self, text: str) -> None:
        """R6.3: grow total_session_tokens by `text`'s estimate_tokens count
        - the one piece of real accumulation logic behind that field. Called
        from register_canvas's sendMessage wrapper below, once for the
        user's own new message text and once for the assistant's completed
        reply text, so the counter genuinely grows as the conversation
        continues rather than sitting fixed at 0."""
        self.total_session_tokens += estimate_tokens(str(text))

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
                    "isDocked": n.is_docked,
                    "imageAssetId": n.image_asset_id,
                    "history": [
                        {"role": m["role"], "content": m["content"]} for m in n.history
                    ],
                    "pendingRequestId": n.pending_request_id,
                    "researchStage": n.research_stage,
                    "researchCompleted": n.research_completed,
                    "researchTotal": n.research_total,
                    "researchActiveSourceId": n.research_active_source_id,
                    "researchError": n.research_error,
                    "researchResult": n.research_result,
                    "artifactContent": n.artifact_content,
                    "gitlinkRepo": n.gitlink_repo,
                    "gitlinkBranch": n.gitlink_branch,
                    "gitlinkScopeMode": n.gitlink_scope_mode,
                    "gitlinkLocalRoot": n.gitlink_local_root,
                    "gitlinkRepoFilePaths": list(n.gitlink_repo_file_paths),
                    "gitlinkSelectedPaths": list(n.gitlink_selected_paths),
                    "gitlinkTaskPrompt": n.gitlink_task_prompt,
                    # gitlinkContextXml is DELIBERATELY OMITTED - see the
                    # field's own comment on SceneNode. Served on demand via
                    # the fetchGitlinkContext intent instead.
                    "gitlinkContextStats": dict(n.gitlink_context_stats),
                    "gitlinkContextSummary": n.gitlink_context_summary,
                    # R5.3 post-review FIX 6: UNLIKE gitlinkContextXml (and
                    # unlike gitlink_change_local_root, never on the wire at
                    # all), this genuinely needs to be here - see the field's
                    # own comment on SceneNode for why gitlinkContextSummary
                    # alone cannot be trusted as a lazy-fetch-once cache key.
                    "gitlinkContextVersion": n.gitlink_context_version,
                    "gitlinkProposalMarkdown": n.gitlink_proposal_markdown,
                    "gitlinkPendingChanges": [dict(c) for c in n.gitlink_pending_changes],
                    "gitlinkPreviewText": n.gitlink_preview_text,
                    "gitlinkChangeFingerprint": n.gitlink_change_fingerprint,
                    "gitlinkChangeState": n.gitlink_change_state,
                    "gitlinkError": n.gitlink_error,
                    "pycoderMode": n.pycoder_mode,
                    "pycoderPrompt": n.pycoder_prompt,
                    "pycoderCode": n.pycoder_code,
                    "pycoderOutput": n.pycoder_output,
                    "pycoderAnalysis": n.pycoder_analysis,
                    "pycoderLastRunFailed": n.pycoder_last_run_failed,
                    "pycoderAwaitingApproval": n.pycoder_awaiting_approval,
                    "pycoderError": n.pycoder_error,
                    # codeSandboxSandboxId is DELIBERATELY OMITTED - see the
                    # field's own comment on SceneNode (pure internal
                    # directory-naming key, mirrors gitlink_imported_root's
                    # own "server-side bookkeeping only" precedent).
                    "codeSandboxRequirements": n.code_sandbox_requirements,
                    "codeSandboxPrompt": n.code_sandbox_prompt,
                    "codeSandboxCode": n.code_sandbox_code,
                    "codeSandboxOutput": n.code_sandbox_output,
                    "codeSandboxAnalysis": n.code_sandbox_analysis,
                    "codeSandboxAwaitingApproval": n.code_sandbox_awaiting_approval,
                    # R5.4 CODESANDBOX FIX: the frozen-at-approval-time
                    # snapshot, deliberately distinct from
                    # codeSandboxRequirements above (that one is the user's
                    # still-live, still-editable draft for the NEXT run) -
                    # see the field's own comment on SceneNode.
                    "codeSandboxApprovalRequirements": n.code_sandbox_approval_requirements,
                    "codeSandboxError": n.code_sandbox_error,
                    # R6.1: Notes/Frames/Containers. groupManualWidth/Height
                    # are DELIBERATELY OMITTED, same "server-side bookkeeping
                    # only" posture as codeSandboxSandboxId/gitlinkImportedRoot
                    # above - see those fields' own comments on SceneNode.
                    "color": n.color,
                    "headerColor": n.header_color,
                    "isSystemPrompt": n.is_system_prompt,
                    "isSummaryNote": n.is_summary_note,
                    "itemIds": list(n.item_ids),
                    "isLocked": n.is_locked,
                    "groupWidth": n.group_width,
                    "groupHeight": n.group_height,
                    # R6.2: Chart node.
                    "chartType": n.chart_type,
                    "chartData": dict(n.chart_data),
                    "chartError": n.chart_error,
                    "chartAssetId": n.chart_asset_id,
                    "chartAssetVersion": n.chart_asset_version,
                    "chartWidth": n.chart_width,
                    "chartHeight": n.chart_height,
                    "chartAspectLocked": n.chart_aspect_locked,
                    "chartSourceNodeId": n.chart_source_node_id,
                    # R6.3: HTML splitter + chat scroll gaps.
                    "htmlSplitterState": n.html_splitter_state,
                    "chatScrollValue": n.chat_scroll_value,
                    # R6.3: null (not []) when content_parts is None - "no
                    # multimodal content" must stay distinguishable from
                    # "multimodal content that happens to be empty". Any
                    # part carrying raw bytes under "data" gets that key
                    # base64-encoded for the wire via content_codec's own
                    # encode_image_bytes - the WIRE shape this produces
                    # matches content_codec.process_content_for_serialization's
                    # own output shape exactly, while n.content_parts itself
                    # (in memory) keeps holding real bytes, per the field's
                    # own contract.
                    "contentParts": _content_parts_wire(n.content_parts),
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
                    # R6.3: NavigationPinRecord already carries these three -
                    # they just weren't exposed on the wire until now.
                    "anchorItemId": p.anchor_item_id,
                    "sortOrder": p.sort_order,
                    "createdAt": p.created_at,
                }
                for p in self.pins.records
            ],
            "snapToGrid": self.snap_to_grid,
            "fadeConnectionsEnabled": self.fade_connections_enabled,
            "orthogonalRouting": self.orthogonal_routing,
            "smartGuides": self.smart_guides,
            # R7.5c: the one derived bit of current_chat_id the frontend needs
            # (see that field's own comment) - whether this scene corresponds
            # to a saved chats.db row. Never the id itself.
            "hasSavedChat": self.current_chat_id is not None,
            "dragFactor": self.drag_factor,
            "fontFamily": self.font_family,
            "fontSizePt": self.font_size_pt,
            "fontColor": self.font_color,
            # R6.3: canvas view state + the real, live-growing session token
            # count - see these fields' own comments on SceneDocument.
            "zoomFactor": self.zoom_factor,
            "scrollX": self.scroll_x,
            "scrollY": self.scroll_y,
            "totalSessionTokens": self.total_session_tokens,
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


def _content_parts_wire(parts: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """R6.3: scene_payload()'s wire-side transform for SceneNode.content_parts
    - a pure mapping function, NOT a SceneDocument method (same posture as
    _research_result_wire below). None stays None (never []), so "no
    multimodal content" and "multimodal content that happens to be empty"
    remain distinguishable on the wire. Any part that is a dict carrying raw
    bytes under its "data" key gets that key base64-encoded as a string via
    content_codec.encode_image_bytes - matching content_codec.
    process_content_for_serialization's own output shape exactly - while
    leaving every other key/part untouched. Builds fresh dicts throughout;
    never mutates the SceneNode's own in-memory parts (which must keep
    holding real bytes, per content_parts's own field contract)."""
    if parts is None:
        return None
    wire_parts: list[dict[str, Any]] = []
    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("data"), (bytes, bytearray)):
            wire_part = dict(part)
            wire_part["data"] = _content_codec.encode_image_bytes(bytes(part["data"]))
            wire_parts.append(wire_part)
        elif isinstance(part, dict):
            wire_parts.append(dict(part))
        else:
            wire_parts.append(part)
    return wire_parts


def _research_result_wire(result) -> dict[str, Any]:
    """Camel-cases a ResearchResult (graphlink_plugins/web_research/domain.py)
    for the wire - a pure mapping function, NOT a SceneDocument method.
    Duck-typed on purpose: canvas.py imports nothing from
    graphlink_plugins.web_research (same posture as apply_web_research_progress
    above)."""
    return {
        "requestId": result.request_id,
        "originalQuery": result.original_query,
        "effectiveQuery": result.effective_query,
        "answerMarkdown": result.answer_markdown,
        "sources": [
            {
                "sourceId": s.source_id,
                "title": s.title,
                "url": s.url,
                "canonicalUrl": s.canonical_url,
                "snippet": s.snippet,
                "rank": s.rank,
                "provider": s.provider,
                "finalUrl": s.final_url,
                "status": s.status,
                "errorCode": s.error_code,
                "errorMessage": s.error_message,
                "truncated": s.truncated,
                "contentHash": s.content_hash,
                "citationCount": s.citation_count,
            }
            for s in result.sources
        ],
        "citations": [
            {"sourceId": c.source_id, "marker": c.marker, "claimContext": c.claim_context}
            for c in result.citations
        ],
        "warnings": list(result.warnings),
        "providerSnapshot": dict(result.provider_snapshot),
    }


def _chart_source_text(branch_history: list[dict]) -> str:
    """R6.2: flattens chat_branch_history's own {"role","content"} list (the
    SAME branch walk web_research/pycoder/gitlink already reuse via
    document.chat_branch_history - see this module's own docstring
    convention) into the single plain-text string ChartDataAgent.get_response
    expects. NOT a new branch-walking helper (chat_branch_history already IS
    that, reused as-is) - just the formatting step its list-of-dicts shape
    needs before it can be handed to an agent whose interface is a flat
    string, mirroring legacy graphlink_window_actions.py's own
    _build_chart_source_text/history_to_transcript role for the same
    call site. Empty/whitespace-only turns are skipped so an accidental blank
    ChatNode doesn't inject a stray blank line."""
    lines = []
    for turn in branch_history:
        content = str(turn.get("content") or "").strip()
        if not content:
            continue
        speaker = "User" if turn.get("role") == "user" else "Assistant"
        lines.append(f"{speaker}: {content}")
    return "\n\n".join(lines)


def _placeholder_chart_data(chart_type: str) -> dict[str, Any]:
    """R6.2: a minimal, already-canonical-SHAPED payload (matching
    canonicalize_chart_data's own output keys exactly) for the rare
    defensive case in generate_chart below where ChartDataAgent's response
    carries no top-level "error" key yet still fails
    canonicalize_chart_data - see that function's own call site for the full
    reasoning. One trivial data point per chart kind: just enough for
    render_chart_png to draw SOMETHING rather than leave the request a
    silent no-op, matching this feature's "never a blank/broken state"
    contract. NOT run back through canonicalize_chart_data itself - callers
    that need genuinely validated data should generate it properly instead."""
    if chart_type == "sankey":
        return {"type": "sankey", "title": "Chart", "flows": [{"source": "A", "target": "B", "value": 1.0}]}
    if chart_type == "histogram":
        return {
            "type": "histogram", "title": "Chart", "values": [0.0, 1.0], "bins": 2,
            "xAxis": "Value", "yAxis": "Frequency",
        }
    return {
        "type": chart_type,
        "title": "Chart",
        "labels": ["A", "B"],
        "values": [1.0, 1.0],
        "xAxis": "Category" if chart_type == "bar" else "Sequence",
        "yAxis": "Value",
    }


def register_canvas(
    bus: SessionBus,
    notifications: NotificationState,
    agent_dispatcher: AgentDispatcher,
    composer_document: ComposerDocument,
) -> SceneDocument:
    """Give a session its canvas document + the scene/grid topics and every
    R1 intent. Intent names for grid mirror GridControlBridge's @Slot names
    1:1 so the R2 island port is a transport swap, not a redesign.

    R4: agent_dispatcher/composer_document are threaded through so
    sendMessage's real Send action (below) can hand off to the real agent
    dispatch pipeline instead of the R3-era deferred notice."""

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

    async def add_thinking_node(x, y, thinking_text, parent_id):
        node = document.add_thinking_node(x, y, thinking_text, parent_id)
        await publish_scene()
        return node.id

    async def add_html_node(x, y, html_content, parent_id):
        node = document.add_html_node(x, y, html_content, parent_id)
        await publish_scene()
        return node.id

    async def set_html_splitter_state(node_id, value):
        document.set_html_splitter_state(node_id, value)
        await publish_scene()

    async def add_image_node(x, y, image_bytes_base64, prompt, parent_id, mime_type="image/png"):
        # Unlike every prior wrapper, the WS intent transport is JSON, which
        # cannot carry raw bytes - the caller sends base64 text, decoded here
        # before it ever reaches SceneDocument (which only ever deals in real
        # bytes, same as the HTTP asset route on the read side).
        image_bytes = base64.b64decode(image_bytes_base64)
        node = document.add_image_node(x, y, image_bytes, prompt, parent_id, mime_type=mime_type)
        await publish_scene()
        return node.id

    async def add_conversation_node(x, y, parent_id):
        node = document.add_conversation_node(x, y, parent_id)
        await publish_scene()
        return node.id

    async def send_conversation_message(node_id, text):
        # R4.3: the real user-message-send action for a conversation node -
        # appends a real user message, then dispatches a real agent reply
        # through AgentDispatcher.start_conversation_reply, the ConversationNode
        # counterpart of send_message's ChatNode dispatch above. The reply
        # lands via _on_reply calling document.append_conversation_assistant_message
        # directly - same established relationship as send_message's own
        # _on_reply calling document.add_chat_node directly.
        node = document.send_conversation_message(node_id, text)
        await publish_scene()

        def _on_reply(reply_text):
            # R4.3b: deliberate, confirmed-correct omission, NOT an oversight -
            # ConversationNode is exempt from the response_parsing retrofit
            # applied to send_message's _on_reply above. The true legacy
            # handler for a conversation node's reply is
            # graphlink_window_actions.py's WindowActionsMixin.
            # handle_conversation_node_response (NOT handle_response), which
            # just calls target_node.add_ai_message(response_text) directly -
            # it never calls self._parse_response and never creates any
            # child node. A ConversationNode is a self-contained mega-node
            # with a flat plain-text-only history and no child-node concept
            # at all in legacy.
            document.append_conversation_assistant_message(node_id, reply_text)

        await agent_dispatcher.start_conversation_reply(
            bus=bus,
            notifications_state=notifications,
            node=node,
            conversation_history=node.history,
            on_reply=_on_reply,
        )
        return node.id

    async def append_conversation_assistant_message(node_id, text):
        # Unlike send_conversation_message, this represents a real reply
        # landing once ConversationNode gets real agent dispatch, not a
        # deferral - so no notification fires.
        node = document.append_conversation_assistant_message(node_id, text)
        await publish_scene()
        return node.id

    async def delete_conversation_message(node_id, message_index):
        document.delete_conversation_message(node_id, message_index)
        await publish_scene()

    async def set_node_docked(node_id, docked):
        document.set_node_docked(node_id, docked)
        await publish_scene()

    async def delete_chat_node(node_id):
        document.delete_chat_node(node_id)
        await publish_scene()

    async def set_chat_collapsed(node_id, collapsed):
        document.set_chat_collapsed(node_id, collapsed)
        await publish_scene()

    async def set_chat_scroll_value(node_id, value):
        document.set_chat_scroll_value(node_id, value)
        await publish_scene()

    async def send_message(text):
        # R3.3: the real Send action - a real user ChatNode, continuing the
        # active branch. R4: the assistant's reply is now a real agent
        # dispatch call, not a deferred notice - see backend/agents.py.
        node = document.send_message(text)
        # R6.3: grow the real, live-growing session token count by the
        # user's own new message text - see add_session_tokens's own
        # comment on SceneDocument.
        document.add_session_tokens(text)
        await publish_scene()
        history = document.chat_branch_history(node.id)

        def _on_reply(reply_text):
            # R6.3: the assistant's reply has completed (regardless of
            # whether parse_response below finds anything node-worthy in
            # it) - grow total_session_tokens by its estimated count too,
            # same as the user's own message text just above.
            document.add_session_tokens(reply_text)
            # R4.3b: port legacy handle_response's _parse_response retrofit -
            # split the flat reply into thinking/text/code parts and create
            # separate thinking-kind/code-kind CHILD nodes instead of
            # dumping the raw, unparsed reply into one flat node.
            parsed_parts = parse_response(reply_text)
            if not parsed_parts:
                # Mirrors legacy handle_response's own outer gate
                # (`if text_content or parsed_parts:`) - a genuinely empty/
                # whitespace-only reply creates NO node at all, not a
                # "[Empty Response]" placeholder node. Currently unreachable
                # in practice (api_provider._compose_reasoned_response raises
                # rather than returning blank content), but the gate is kept
                # so a future provider path can never silently diverge from
                # legacy here. last_chat_node_id is deliberately left
                # untouched - it already points at the user's own message
                # node (set by send_message just above), matching legacy's
                # own fallback of leaving current_node at user_node when no
                # assistant node gets created.
                return

            text_parts = [p["content"] for p in parsed_parts if p["type"] == "text"]
            text_content = "\n\n".join(text_parts)

            placeholder_text = text_content
            if not placeholder_text:
                if any(p["type"] == "code" for p in parsed_parts):
                    placeholder_text = PLACEHOLDER_GENERATED_CONTENT
                elif any(p["type"] == "thinking" for p in parsed_parts):
                    placeholder_text = PLACEHOLDER_ASSISTANT_REASONING
                else:
                    # Unreachable given parse_response's own invariants (a
                    # non-empty parts list with no text/code part must
                    # contain a thinking part) - legacy's handle_response has
                    # this exact same dead branch; kept verbatim for
                    # structural parity rather than optimized away.
                    placeholder_text = PLACEHOLDER_EMPTY_RESPONSE

            ax, ay = node.x, node.y + MESSAGE_VERTICAL_SPACING
            ai_node = document.add_chat_node(ax, ay, placeholder_text, False, parent_id=node.id)

            # NOTE: these two calls MUST use the `document.` prefix. Bare
            # `add_code_node(...)` / `add_thinking_node(...)` would silently
            # resolve to this enclosing register_canvas scope's own async WS-
            # intent wrapper closures of the same name (defined earlier,
            # above send_message) instead of raising a NameError - producing
            # an unawaited coroutine that never runs and never errors, so no
            # node would be created and nothing would look wrong until the
            # scene state was actually inspected.
            for part in parsed_parts:
                if part["type"] == "thinking":
                    document.add_thinking_node(
                        ax - MESSAGE_VERTICAL_SPACING, ay + MESSAGE_VERTICAL_SPACING,
                        part["content"], parent_id=ai_node.id,
                    )
                elif part["type"] == "code":
                    document.add_code_node(
                        ax + MESSAGE_VERTICAL_SPACING, ay + MESSAGE_VERTICAL_SPACING,
                        part["content"], part["language"], parent_id=ai_node.id,
                    )

            # Always the real chat node's id, never a code/thinking child's -
            # add_code_node/add_thinking_node are documented above (see their
            # own docstrings) as NOT branch points, and last_chat_node_id
            # specifically drives the next real send's branch-continuation
            # (chat_branch_history), which only makes sense pointed at a real
            # chat node.
            document.last_chat_node_id = ai_node.id

        await agent_dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=history,
            on_reply=_on_reply,
            # R6.1: lets AgentDispatcher resolve a branch-attached System
            # Prompt note override (see backend/agents.py's
            # _resolve_branch_system_prompt) - node.id is the just-created
            # user ChatNode "about to be sent", the walk starts from here.
            canvas_document=document,
            node_id=node.id,
        )
        return node.id

    async def regenerate_response(node_id):
        try:
            node_to_regenerate, parent_id = document.regenerate_response(node_id)
        except SceneError:
            # Deliberate: ALL THREE of regenerate_response's SceneErrors funnel
            # into this ONE legacy-parity message. app.py's _handle_message
            # would otherwise turn a raised SceneError into a generic
            # "intent failed" WS error - and transport.ts's intent() is
            # fire-and-forget (no id), so that error is ONLY ever
            # console.error'd, never shown to the user (confirmed by reading
            # transport.ts's handleMessage). A stale click racing a delete, or
            # a future caller passing a bad kind, must never go silently to the
            # console when legacy's real, reachable case shows a visible
            # banner - so this is the one deliberate divergence from every
            # other register_canvas wrapper's convention of letting SceneError
            # bubble to the generic WS error path.
            notifications.show("This node has no parent and cannot be regenerated.", "warning")
            await bus.publish("notification")
            return None

        history = document.chat_branch_history(parent_id)

        async def _on_reply(reply_text):
            # (1) Empty/whitespace reply: keep ORIGINAL content, notify, stop.
            # Checked FIRST - exact legacy order (window_actions.py:544-546),
            # even before the liveness check below (see its own comment).
            if not reply_text or not reply_text.strip():
                notifications.show(
                    "The model returned an empty response. The original response has been kept.",
                    "warning",
                )
                await bus.publish("notification")
                return

            # (2) Deleted mid-flight: silent no-op, matches
            # window_actions.py:548 (`if not old_node or not old_node.scene():
            # return` - no notification_banner call there either).
            if node_to_regenerate.id not in document.nodes:
                return

            # (3) Teardown BEFORE parse/mutate - exact legacy step order.
            # Runs unconditionally on any non-empty, still-alive reply, even if
            # the new reply has no code/thinking parts at all - this is why
            # document/image children are deleted but never recreated
            # (parse_response structurally only emits thinking/text/code).
            document.remove_associated_content_children(node_to_regenerate.id)

            parsed_parts = parse_response(reply_text)
            text_parts = [p["content"] for p in parsed_parts if p["type"] == "text"]
            text_content = "\n\n".join(text_parts)

            # THE SIMPLE 1-WAY TERNARY - NOT send_message's 3-way priority
            # chain. PLACEHOLDER_ASSISTANT_REASONING is NEVER touched by this
            # path. Exact match to legacy line 561:
            # `text_content if text_content else "[Generated Content]"`.
            placeholder_text = text_content if text_content else PLACEHOLDER_GENERATED_CONTENT
            document.update_chat_node_content(node_to_regenerate.id, placeholder_text)

            # NOTE: `document.` prefix is REQUIRED - bare add_code_node/
            # add_thinking_node would silently resolve to this same
            # register_canvas scope's own WS-intent wrapper closures instead of
            # raising (identical hazard already documented on send_message's
            # own _on_reply above this function).
            bx, by = node_to_regenerate.x, node_to_regenerate.y
            for part in parsed_parts:
                if part["type"] == "thinking":
                    document.add_thinking_node(
                        bx - MESSAGE_VERTICAL_SPACING, by + MESSAGE_VERTICAL_SPACING,
                        part["content"], parent_id=node_to_regenerate.id,
                    )
                elif part["type"] == "code":
                    document.add_code_node(
                        bx + MESSAGE_VERTICAL_SPACING, by + MESSAGE_VERTICAL_SPACING,
                        part["content"], part["language"], parent_id=node_to_regenerate.id,
                    )

            # last_chat_node_id: DELIBERATELY untouched. See §5.

        await agent_dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=history,
            on_reply=_on_reply,
            # R4.4: deliberately NOT streamed - see the design spec's own
            # deferral list. Regenerate replaces an EXISTING node's content
            # rather than creating a new one, and streaming it would light
            # up the Composer dock's live preview for a click on some other
            # node in the canvas, with no way for the frontend to tell that
            # apart from an actual Composer send.
            stream=False,
            # R6.1: same branch-system-prompt-override resolution as
            # send_message above - parent_id (not node_to_regenerate.id) so
            # the walk starts from the SAME node chat_branch_history just
            # built `history` from, a moment above.
            canvas_document=document,
            node_id=parent_id,
        )
        return node_to_regenerate.id

    async def _dispatch_image(parent_chat_node_id, prompt):
        # R4.4a: shared internal path for both generateImage and
        # regenerateImage below - each resolves its own (parent_chat_node_id,
        # prompt) pair from a different source-node kind, then both funnel
        # through this one dispatch + success-primitive call. Runs on
        # agent_dispatcher's INDEPENDENT self._image_requests slot, never
        # self._requests - see backend/agents.py's AgentDispatcher docstring
        # for why chat and image generation must be able to run concurrently.
        async def _on_reply(image_bytes):
            if parent_chat_node_id not in document.nodes:
                # Mid-flight delete, silent no-op - same posture as
                # regenerate_response's own liveness check above.
                return
            document.add_generated_image_reply(parent_chat_node_id, prompt, image_bytes)
            await bus.publish("scene")

        await agent_dispatcher.start_image_reply(
            bus=bus,
            notifications_state=notifications,
            prompt=prompt,
            on_reply=_on_reply,
        )

    async def generate_image(chat_node_id):
        try:
            parent_chat_node_id, prompt = document.resolve_generate_image(chat_node_id)
        except SceneError as exc:
            # Two genuinely distinct SceneErrors here, NOT collapsed into one
            # generic message: SceneEmptyPromptError lets this wrapper tell
            # "empty prompt" apart from "wrong kind/unknown node" via
            # isinstance, without string-sniffing exc's own text.
            if isinstance(exc, SceneEmptyPromptError):
                notifications.show("The selected node has no text to use as a prompt.", "warning")
            else:
                notifications.show("This node can't be used to generate an image.", "warning")
            await bus.publish("notification")
            return None
        await _dispatch_image(parent_chat_node_id, prompt)
        return None

    async def regenerate_image(image_node_id):
        try:
            parent_chat_node_id, prompt = document.resolve_regenerate_image(image_node_id)
        except SceneError:
            # Unlike generate_image above, both of resolve_regenerate_image's
            # SceneErrors (unknown/wrong-kind/no-parent, and the
            # SceneEmptyPromptError empty-content variant) share ONE message
            # here - the exact wording this feature's design spec settled on.
            notifications.show("This image has no prompt to regenerate from.", "warning")
            await bus.publish("notification")
            return None
        await _dispatch_image(parent_chat_node_id, prompt)
        return None

    async def run_web_research(node_id, query_text):
        if agent_dispatcher.is_web_research_busy():
            # Checked BEFORE touching document state: start_web_research_run
            # resets a node's progress/error fields unconditionally, and the
            # dispatcher only allows one web-research run at a time anyway -
            # without this early check, clicking Run on a different node
            # while one is already in flight would silently wipe that node's
            # prior result/error banner even though no new run actually starts.
            notifications.show("A web research request is already running.", "info")
            await bus.publish("notification")
            return None
        try:
            node = document.start_web_research_run(node_id, query_text)
        except SceneError:
            notifications.show("This node no longer exists.", "warning")
            await bus.publish("notification")
            return None
        await publish_scene()

        parent_edge = document._branch_parent_edge(node_id)
        branch_history = document.chat_branch_history(parent_edge.source) if parent_edge else []

        async def _on_progress(event):
            if node_id not in document.nodes:
                return
            document.apply_web_research_progress(node_id, event)
            await bus.publish("scene")

        async def _on_success(result):
            if node_id not in document.nodes:
                return
            document.complete_web_research_run(node_id, _research_result_wire(result))
            await bus.publish("scene")

        async def _on_failure(exc):
            if node_id not in document.nodes:
                return
            cancelled = type(exc).__name__ == "RequestCancelled"
            document.fail_web_research_run(node_id, cancelled=cancelled, message=str(exc))
            await bus.publish("scene")

        await agent_dispatcher.start_web_research(
            bus=bus,
            notifications_state=notifications,
            node=node,
            node_id=node_id,
            query=query_text,
            branch_history=branch_history,
            on_progress=_on_progress,
            on_success=_on_success,
            on_failure=_on_failure,
        )
        return node_id

    async def cancel_web_research_request(request_id):
        agent_dispatcher.cancel_web_research(request_id)

    async def send_artifact_message(node_id, text):
        # R5.2: the Artifact node's own Send action - appends a real user
        # instruction, then dispatches a real agent reply through
        # AgentDispatcher.start_artifact_reply. No try/except SceneError guard
        # here (an unknown node_id propagates as a generic WS intent error) -
        # same posture as send_conversation_message above, not
        # run_web_research's defensive pre-check pattern: there is no
        # persisted progress/error state on this node that an unguarded call
        # could corrupt, so a stale click racing a delete has nothing
        # destructive to protect against.
        node = document.send_artifact_message(node_id, text)
        await publish_scene()

        parent_edge = document._branch_parent_edge(node_id)
        branch_history = document.chat_branch_history(parent_edge.source) if parent_edge else []
        full_history = branch_history + node.history

        def _on_reply(new_content, ai_message):
            document.complete_artifact_generation(node_id, new_content, ai_message)

        await agent_dispatcher.start_artifact_reply(
            bus=bus,
            notifications_state=notifications,
            node=node,
            current_artifact=node.artifact_content,
            history=full_history,
            on_reply=_on_reply,
        )
        return node.id

    async def cancel_artifact_request(request_id):
        agent_dispatcher.cancel_artifact(request_id)

    # -- R6.2: Chart node ------------------------------------------------------
    #
    # Unlike every branch-point-child kind above (Web Research/Artifact/
    # Gitlink/Py-Coder/Execution Sandbox), Chart has no separate "create an
    # empty node, then run generation on it" split - generateChart is a
    # single combined create+generate action, so there is no addChartNode
    # intent at all: the SceneNode is only ever created (by
    # document.add_chart_node, in _on_success below) once real chart data
    # actually exists, mirroring legacy's own ChartWorkerThread flow (a
    # transient loading state anchored on the SOURCE node, not a pre-created
    # placeholder chart node - see graphlink_window_actions.py's
    # generate_chart/handle_chart_data).

    async def generate_chart(parent_node_id, chart_type):
        if not parent_node_id or parent_node_id not in document.nodes:
            notifications.show(
                "Please select a valid node to branch from before generating a chart.",
                "warning",
            )
            await bus.publish("notification")
            return None

        normalized_chart_type = str(chart_type or "").strip().lower()
        if normalized_chart_type not in SUPPORTED_CHART_TYPES:
            notifications.show(
                "Please choose a valid chart type before generating a chart.",
                "warning",
            )
            await bus.publish("notification")
            return None

        parent = document.nodes[parent_node_id]
        branch_history = document.chat_branch_history(parent_node_id)
        source_text = _chart_source_text(branch_history)

        result_holder: dict[str, str] = {}

        async def _on_success(result):
            try:
                chart_data = canonicalize_chart_data(result, normalized_chart_type)
                chart_error = ""
            except ChartDataError as exc:
                # R6.2 contract: ChartDataAgent's own validate_chart_data
                # pipeline (repair round trip, then heuristic fallback)
                # already tries hard to guarantee canonical output before
                # ever returning successfully - this is the rare defensive
                # case where it still somehow didn't. Never a silent no-op:
                # still create a real chart node with a minimal placeholder
                # shape and chart_error set, same "degrade gracefully, never
                # drop the request" contract as the agent's own internal
                # fallback chain.
                chart_data = _placeholder_chart_data(normalized_chart_type)
                chart_error = f"The generated chart data could not be validated: {exc}"
            node = document.add_chart_node(
                parent.x + MESSAGE_VERTICAL_SPACING,
                parent.y,
                parent_node_id,
                normalized_chart_type,
                chart_data,
                chart_error=chart_error,
            )
            result_holder["node_id"] = node.id
            await bus.publish("scene")

        def _on_failure(message):
            # Matches ChartWorkerThread's own error path (and this feature's
            # explicit contract): a genuinely unrecoverable agent-side
            # failure (get_response's own top-level "error" key, or a
            # timeout/exception) shows a notification and creates nothing -
            # start_chart_generation above already shows that notification,
            # so there is nothing left for this callback to do.
            pass

        await agent_dispatcher.start_chart_generation(
            bus=bus,
            notifications_state=notifications,
            node_id=parent_node_id,
            chart_type=normalized_chart_type,
            source_text=source_text,
            on_success=_on_success,
            on_failure=_on_failure,
        )
        return result_holder.get("node_id")

    async def resize_chart(node_id, width, height):
        document.resize_chart(node_id, width, height)
        await publish_scene()

    async def toggle_chart_aspect_lock(node_id):
        document.toggle_chart_aspect_lock(node_id)
        await publish_scene()

    # -- R5.3: Gitlink node --------------------------------------------------
    #
    # Reuses the existing generic pending_request_id field as the busy/
    # in-flight marker for every Gitlink action (list repos, load tree,
    # import, build context, run, apply) - this is exactly that field's
    # documented purpose, and critically it is what makes the
    # fingerprint-recheck race-proof: a Run cannot start while an Apply
    # request_id occupies this node's slot, and vice versa.

    async def fetch_gitlink_repositories(node_id):
        node = document.nodes.get(node_id)
        if node is None or node.pending_request_id:
            notifications.show("Gitlink is busy for this node.", "info")
            await bus.publish("notification")
            return []
        return await agent_dispatcher.fetch_gitlink_repositories(
            bus=bus, notifications_state=notifications, node=node,
        )

    async def load_gitlink_repo_tree(node_id, repo, branch):
        node = document.nodes.get(node_id)
        if node is None or node.pending_request_id:
            notifications.show("Gitlink is busy for this node.", "info")
            await bus.publish("notification")
            return None
        result = await agent_dispatcher.load_gitlink_repo_tree(
            bus=bus, notifications_state=notifications, node=node, repo=repo, branch=branch,
        )
        if result is not None:
            document.store_gitlink_repo_tree(node_id, *result)
            await publish_scene()
        return node_id

    async def set_gitlink_local_root(node_id, local_root):
        document.set_gitlink_local_root(node_id, local_root)
        await publish_scene()

    async def import_gitlink_snapshot(node_id, repo, branch):
        node = document.nodes.get(node_id)
        if node is None or node.pending_request_id:
            notifications.show("Gitlink is busy for this node.", "info")
            await bus.publish("notification")
            return None
        result = await agent_dispatcher.import_gitlink_snapshot(
            bus=bus, notifications_state=notifications, node=node, repo=repo, branch=branch,
            local_root_hint=node.gitlink_local_root, imported_root_hint=node.gitlink_imported_root,
        )
        if result is not None:
            document.store_gitlink_snapshot_root(node_id, *result)
            await publish_scene()
        return node_id

    async def build_gitlink_context(node_id, scope_mode, selected_paths):
        node = document.nodes.get(node_id)
        if node is None or node.pending_request_id:
            notifications.show("Gitlink is busy for this node.", "info")
            await bus.publish("notification")
            return None
        result = await agent_dispatcher.build_gitlink_context(
            bus=bus, notifications_state=notifications, node=node,
            scope_mode=scope_mode, selected_paths=list(selected_paths),
        )
        if result is not None:
            document.store_gitlink_context(node_id, scope_mode=scope_mode,
                                            selected_paths=selected_paths, **result)
            await publish_scene()
        return node_id

    async def fetch_gitlink_context(node_id):
        return document.fetch_gitlink_context_xml(node_id)

    async def run_gitlink_change_set(node_id, task_prompt):
        node_for_check = document.nodes.get(node_id)
        if node_for_check is not None and node_for_check.pending_request_id:
            notifications.show("Gitlink is already busy for this node.", "info")
            await bus.publish("notification")
            return None
        # R5.3 post-review FIX 4(b): claim the busy slot with a placeholder
        # SYNCHRONOUSLY, in the same stretch as the busy pre-check just
        # above - before document.start_gitlink_run or any await - so a
        # second concurrent call for this SAME node_id can never pass that
        # same pre-check during the `await publish_scene()` gap below.
        # agent_dispatcher.start_gitlink_run (the ONLY caller of this dict
        # entry for this node_id, invoked just below) recognizes this exact
        # placeholder and overwrites it with the real request_id, still
        # synchronously - see that method's own docstring.
        if node_for_check is not None:
            node_for_check.pending_request_id = _GITLINK_RUN_CLAIM_PLACEHOLDER
        try:
            node = document.start_gitlink_run(node_id, task_prompt)
        except SceneError:
            # Node deleted (or wrong-kind) concurrently with the claim above -
            # the placeholder must not linger on a node this handler is
            # about to give up on.
            if node_for_check is not None:
                node_for_check.pending_request_id = None
            notifications.show("This node no longer exists.", "warning")
            await bus.publish("notification")
            return None
        await publish_scene()

        def _on_success(proposal_markdown, pending_changes, preview_text, fingerprint, local_root):
            document.complete_gitlink_run(node_id, proposal_markdown, pending_changes,
                                           preview_text, fingerprint, local_root)

        def _on_failure(message):
            document.fail_gitlink_run(node_id, message)

        await agent_dispatcher.start_gitlink_run(
            bus=bus, notifications_state=notifications, node=node, node_id=node_id,
            repo=node.gitlink_repo, branch=node.gitlink_branch,
            scope_mode=node.gitlink_scope_mode, task_prompt=task_prompt,
            context_xml=node.gitlink_context_xml, context_summary=node.gitlink_context_summary,
            local_root=node.gitlink_local_root,
            on_success=_on_success, on_failure=_on_failure,
        )
        return node_id

    async def cancel_gitlink_request(request_id):
        agent_dispatcher.cancel_gitlink(request_id)

    async def apply_gitlink_changes(node_id, fingerprint):
        node = document.nodes.get(node_id)
        if node is None:
            notifications.show("This node no longer exists.", "warning")
            await bus.publish("notification")
            return None

        def _on_success(written_files):
            document.complete_gitlink_apply(node_id, written_files)

        def _on_failure(message):
            document.fail_gitlink_apply(node_id, message)

        await agent_dispatcher.start_gitlink_apply(
            bus=bus, notifications_state=notifications, node=node, node_id=node_id,
            client_fingerprint=fingerprint, local_root=node.gitlink_local_root,
            on_success=_on_success, on_failure=_on_failure,
        )
        return node_id

    bus.register_intent("scene", "fetchGitlinkRepositories", fetch_gitlink_repositories)
    bus.register_intent("scene", "loadGitlinkRepoTree", load_gitlink_repo_tree)
    bus.register_intent("scene", "setGitlinkLocalRoot", set_gitlink_local_root)
    bus.register_intent("scene", "importGitlinkSnapshot", import_gitlink_snapshot)
    bus.register_intent("scene", "buildGitlinkContext", build_gitlink_context)
    bus.register_intent("scene", "fetchGitlinkContext", fetch_gitlink_context)
    bus.register_intent("scene", "runGitlinkChangeSet", run_gitlink_change_set)
    bus.register_intent("scene", "cancelGitlinkRequest", cancel_gitlink_request)
    # CRITICAL, load-bearing property: applyGitlinkChanges takes ONLY
    # (node_id, fingerprint) as WS intent arguments - there must be NO
    # changes/pending_changes parameter anywhere in this signature or the
    # dispatcher method it calls. This closes the most obvious
    # content-injection bypass by construction, not by a runtime check: the
    # only content that ever reaches apply_change_set is server-held,
    # already-normalized node.gitlink_pending_changes.
    bus.register_intent("scene", "applyGitlinkChanges", apply_gitlink_changes)

    # -- R5.4: Py-Coder node ---------------------------------------------------

    async def set_pycoder_mode(node_id, mode):
        document.set_pycoder_mode(node_id, mode)
        await publish_scene()

    async def run_pycoder(node_id, input_text):
        # R5.3 post-review FIX 4(b)'s own Run-vs-Run race fix, reused
        # verbatim for this new kind: claim the busy slot with a shared
        # placeholder SYNCHRONOUSLY, in the same stretch as the busy
        # pre-check just above - before document.start_pycoder_run or any
        # await - so a second concurrent runPyCoder for this SAME node_id
        # can never pass the same pre-check during the `await
        # publish_scene()` gap below. Critically, this placeholder stays
        # claimed for the ENTIRE span from here through generation, through
        # the human-approval pause, through execution, through analysis - so
        # a second runPyCoder DURING the pause is refused by this SAME
        # check, no new logic needed for that case specifically (see the
        # R5.4 design spec's own section on this).
        node_for_check = document.nodes.get(node_id)
        if node_for_check is not None and node_for_check.pending_request_id:
            notifications.show("Py-Coder is already busy for this node.", "info")
            await bus.publish("notification")
            return None
        if node_for_check is not None:
            node_for_check.pending_request_id = _CODE_EXEC_RUN_CLAIM_PLACEHOLDER
        try:
            node = document.start_pycoder_run(node_id, input_text)
        except SceneError:
            if node_for_check is not None:
                node_for_check.pending_request_id = None
            notifications.show("This node no longer exists.", "warning")
            await bus.publish("notification")
            return None
        await publish_scene()

        parent_edge = document._branch_parent_edge(node_id)
        branch_history = document.chat_branch_history(parent_edge.source) if parent_edge else []

        def _on_success(code, output, analysis, last_run_failed):
            document.complete_pycoder_run(node_id, code, output, analysis, last_run_failed)

        def _on_failure(message):
            document.fail_pycoder_run(node_id, message)

        await agent_dispatcher.start_pycoder_run(
            bus=bus, notifications_state=notifications, node=node, node_id=node_id,
            mode=node.pycoder_mode, prompt=node.pycoder_prompt, code=node.pycoder_code,
            conversation_history=branch_history,
            on_success=_on_success, on_failure=_on_failure,
        )
        return node_id

    async def cancel_pycoder_request(request_id):
        agent_dispatcher.cancel_pycoder(request_id)

    bus.register_intent("scene", "setPyCoderMode", set_pycoder_mode)
    bus.register_intent("scene", "runPyCoder", run_pycoder)
    bus.register_intent("scene", "cancelPyCoderRequest", cancel_pycoder_request)

    # -- R5.4: Execution Sandbox node -------------------------------------------

    async def set_code_sandbox_requirements(node_id, requirements_text):
        document.set_code_sandbox_requirements(node_id, requirements_text)
        await publish_scene()

    async def run_code_sandbox(node_id, input_text):
        # Same busy-claim-placeholder pattern as run_pycoder above (and
        # run_gitlink_change_set before it) - see that function's own
        # comment for the exact race this closes.
        node_for_check = document.nodes.get(node_id)
        if node_for_check is not None and node_for_check.pending_request_id:
            notifications.show("Execution Sandbox is already busy for this node.", "info")
            await bus.publish("notification")
            return None
        if node_for_check is not None:
            node_for_check.pending_request_id = _CODE_EXEC_RUN_CLAIM_PLACEHOLDER
        try:
            node = document.start_code_sandbox_run(node_id, input_text)
        except SceneError:
            if node_for_check is not None:
                node_for_check.pending_request_id = None
            notifications.show("This node no longer exists.", "warning")
            await bus.publish("notification")
            return None
        await publish_scene()

        parent_edge = document._branch_parent_edge(node_id)
        branch_history = document.chat_branch_history(parent_edge.source) if parent_edge else []

        def _on_success(code, output, analysis):
            document.complete_code_sandbox_run(node_id, code, output, analysis)

        def _on_failure(message):
            document.fail_code_sandbox_run(node_id, message)

        await agent_dispatcher.start_code_sandbox_run(
            bus=bus, notifications_state=notifications, node=node, node_id=node_id,
            sandbox_id=node.code_sandbox_sandbox_id,
            prompt=node.code_sandbox_prompt, existing_code=node.code_sandbox_code,
            requirements_manifest=node.code_sandbox_requirements,
            conversation_history=branch_history,
            on_success=_on_success, on_failure=_on_failure,
        )
        return node_id

    async def cancel_code_sandbox_request(request_id):
        agent_dispatcher.cancel_code_sandbox(request_id)

    bus.register_intent("scene", "setCodeSandboxRequirements", set_code_sandbox_requirements)
    bus.register_intent("scene", "runCodeSandbox", run_code_sandbox)
    bus.register_intent("scene", "cancelCodeSandboxRequest", cancel_code_sandbox_request)

    # -- R5.4: shared approve/deny - one request_id namespace across both kinds

    async def approve_code_execution(request_id):
        agent_dispatcher.approve_code_execution(request_id)

    async def deny_code_execution(request_id):
        agent_dispatcher.deny_code_execution(request_id)

    bus.register_intent("scene", "approveCodeExecution", approve_code_execution)
    bus.register_intent("scene", "denyCodeExecution", deny_code_execution)

    # -- R6.1: Notes/Frames/Containers -----------------------------------------

    async def add_note(x, y, is_system_prompt=False, is_summary_note=False):
        node = document.add_note(
            x, y, is_system_prompt=is_system_prompt, is_summary_note=is_summary_note,
        )
        await publish_scene()
        return node.id

    async def set_note_content(node_id, content):
        document.set_note_content(node_id, content)
        await publish_scene()

    async def create_frame(item_ids):
        node = document.create_frame(list(item_ids))
        await publish_scene()
        return node.id

    async def create_container(item_ids):
        node = document.create_container(list(item_ids))
        await publish_scene()
        return node.id

    async def set_group_label(node_id, text):
        document.set_group_label(node_id, text)
        await publish_scene()

    async def set_group_color(node_id, color, header_color):
        document.set_group_color(node_id, color, header_color)
        await publish_scene()

    async def toggle_frame_lock(node_id):
        document.toggle_frame_lock(node_id)
        await publish_scene()

    async def toggle_group_collapsed(node_id):
        document.toggle_group_collapsed(node_id)
        await publish_scene()

    async def resize_frame(node_id, width, height):
        document.resize_frame(node_id, width, height)
        await publish_scene()

    async def fit_frame_to_content(node_id):
        document.fit_frame_to_content(node_id)
        await publish_scene()

    async def ungroup(node_id):
        document.ungroup(node_id)
        await publish_scene()

    bus.register_intent("scene", "addNote", add_note)
    bus.register_intent("scene", "setNoteContent", set_note_content)
    bus.register_intent("scene", "createFrame", create_frame)
    bus.register_intent("scene", "createContainer", create_container)
    bus.register_intent("scene", "setGroupLabel", set_group_label)
    bus.register_intent("scene", "setGroupColor", set_group_color)
    bus.register_intent("scene", "toggleFrameLock", toggle_frame_lock)
    bus.register_intent("scene", "toggleGroupCollapsed", toggle_group_collapsed)
    bus.register_intent("scene", "resizeFrame", resize_frame)
    bus.register_intent("scene", "fitFrameToContent", fit_frame_to_content)
    bus.register_intent("scene", "ungroup", ungroup)

    async def move_node(node_id, x, y):
        document.move_node(node_id, x, y)
        await publish_scene()

    async def remove_nodes(node_ids):
        ids = list(node_ids)
        # R5.4: a deleted Py-Coder node's REPL subprocess must not outlive
        # it - kind is captured BEFORE document.remove_nodes pops the node,
        # since afterward there is nothing left to read it from.
        pycoder_ids = [
            node_id for node_id in ids
            if document.nodes.get(node_id) is not None and document.nodes[node_id].kind == "pycoder"
        ]
        # R5.4 post-review FIX 2: a deleted pycoder/code_sandbox node's
        # DISPATCHER-SIDE in-flight request must not outlive it either - captured
        # here, BEFORE document.remove_nodes pops the node, for the same reason
        # pycoder_ids above is. dispose_pycoder_repl alone only tears down the
        # REPL subprocess; it does nothing about a request parked on `await
        # approval_future` in AgentDispatcher._pycoder_requests/
        # _code_sandbox_requests, which has NO timeout by design (the whole
        # point is "wait for a human, however long that takes"). Without this,
        # deleting a node mid-approval-pause would leave that future - and the
        # asyncio.Task awaiting it - alive forever, and a stale/duplicate
        # approve-or-deny message arriving later could still resolve it, lazily
        # recreating a REPL or spinning up a fresh sandbox subprocess for a
        # node_id no longer present anywhere in the scene.
        code_exec_cancels = [
            (document.nodes[node_id].kind, document.nodes[node_id].pending_request_id)
            for node_id in ids
            if document.nodes.get(node_id) is not None
            and document.nodes[node_id].kind in ("pycoder", "code_sandbox")
            and document.nodes[node_id].pending_request_id
        ]
        document.remove_nodes(ids)
        for node_id in pycoder_ids:
            await agent_dispatcher.dispose_pycoder_repl(node_id)
        for kind, request_id in code_exec_cancels:
            # cancel_pycoder/cancel_code_sandbox resolve any pending
            # approval_future with False (exactly like a manual Cancel/Deny)
            # and pop the request out of the dispatcher's own dict - a safe
            # no-op if request_id does not name a live entry (e.g. it was only
            # ever the synchronous busy-claim placeholder, never a real
            # dispatcher request_id, or the request already finished on its
            # own between the capture above and here).
            if kind == "pycoder":
                agent_dispatcher.cancel_pycoder(request_id)
            else:
                agent_dispatcher.cancel_code_sandbox(request_id)
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

    bus.register_intent("scene", "addNode", add_node)
    bus.register_intent("scene", "addChatNode", add_chat_node)
    bus.register_intent("scene", "addCodeNode", add_code_node)
    bus.register_intent("scene", "addDocumentNode", add_document_node)
    bus.register_intent("scene", "addThinkingNode", add_thinking_node)
    bus.register_intent("scene", "addHtmlNode", add_html_node)
    bus.register_intent("scene", "setHtmlSplitterState", set_html_splitter_state)
    bus.register_intent("scene", "addImageNode", add_image_node)
    bus.register_intent("scene", "addConversationNode", add_conversation_node)
    bus.register_intent("scene", "sendConversationMessage", send_conversation_message)
    bus.register_intent(
        "scene", "appendConversationAssistantMessage", append_conversation_assistant_message
    )
    bus.register_intent("scene", "deleteConversationMessage", delete_conversation_message)
    bus.register_intent("scene", "setNodeDocked", set_node_docked)
    bus.register_intent("scene", "deleteChatNode", delete_chat_node)
    bus.register_intent("scene", "setChatCollapsed", set_chat_collapsed)
    bus.register_intent("scene", "setChatScrollValue", set_chat_scroll_value)
    bus.register_intent("scene", "sendMessage", send_message)
    bus.register_intent("scene", "regenerateResponse", regenerate_response)
    # R4.4a: "Generate Image from Text" (ChatNode) and "Regenerate Image"
    # (ImageNode) - two intents because the two entry points resolve from
    # genuinely different source-node kinds with different validation rules,
    # both funneling through the shared _dispatch_image helper above.
    bus.register_intent("scene", "generateImage", generate_image)
    bus.register_intent("scene", "regenerateImage", regenerate_image)
    # R5.1: Web Research node run/cancel - node creation itself lives in
    # backend/plugins.py's executePlugin (the "Web Research" branch), not
    # here; these two intents drive an EXISTING web_research-kind node.
    bus.register_intent("scene", "runWebResearch", run_web_research)
    bus.register_intent("scene", "cancelWebResearchRequest", cancel_web_research_request)
    # R5.2: Artifact/Drafter Send/cancel - node creation itself lives in
    # backend/plugins.py's executePlugin (the "Artifact / Drafter" branch),
    # not here; these two intents drive an EXISTING artifact-kind node, same
    # posture as Web Research's own runWebResearch/cancelWebResearchRequest
    # pair above.
    bus.register_intent("scene", "sendArtifactMessage", send_artifact_message)
    bus.register_intent("scene", "cancelArtifactRequest", cancel_artifact_request)
    # R6.2: Chart - a single combined create+generate action, unlike every
    # node-creation flow above - see generate_chart's own docstring.
    bus.register_intent("scene", "generateChart", generate_chart)
    bus.register_intent("scene", "resizeChart", resize_chart)
    bus.register_intent("scene", "toggleChartAspectLock", toggle_chart_aspect_lock)
    bus.register_intent("scene", "moveNode", move_node)
    bus.register_intent("scene", "removeNodes", remove_nodes)
    bus.register_intent("scene", "connectNodes", connect_nodes)
    bus.register_intent("scene", "removeEdges", remove_edges)
    bus.register_intent("scene", "addPin", add_pin)
    bus.register_intent("scene", "movePin", move_pin)
    bus.register_intent("scene", "removePin", remove_pin)
    bus.register_intent("scene", "updatePin", update_pin)
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
    # R4.3: per-node cancel for a ConversationNode's in-flight reply. Reuses
    # the exact intent NAME "cancelChatRequest" already registered on the
    # "app-composer" topic by R4.2 - SessionBus keys handlers by the
    # (topic, intent) tuple (see backend/events.py), so this is a second,
    # independent registration on a different topic, not a collision. It
    # points at the same underlying agent_dispatcher.cancel, which is purely
    # request_id-keyed and does not care which topic invoked it.
    bus.register_intent("scene", "cancelChatRequest", lambda request_id: agent_dispatcher.cancel(request_id))

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
