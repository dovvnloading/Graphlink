"""SceneDocument - the scene graph document (ADR-002 stage 2.2).

Relocated from backend/canvas.py (its post-slice-1 lines 106-2664: the
whole SceneDocument dataclass through grid_payload, plus the
_content_parts_wire helper scene_payload calls) with exactly ONE
deliberate edit, called out below; everything else is verbatim.

The one edit: SceneDocument.add_session_tokens used to call
backend.token_counter.estimate_tokens, but backend/token_counter.py
imports backend.events at module level, which tests/test_domain_purity.py
forbids for the domain layer. The _estimate_tokens helper below replicates
that function verbatim (`TokenEstimator().count_tokens(text)` - same
fresh-instance-per-call shape), so token counts - which feed
total_session_tokens, saved chats.db rows, and autosave's change hash -
are bit-identical.

ADR-013 stage 13.4 retired this module's own render_chart_png import: a
chart node's PNG used to be rendered here (add_chart_node's initial
render, resize_chart's re-render), but that output went dead the moment
stage 13.2 shipped the client-side interactive renderer - nothing has
fetched it since. Matplotlib rendering now lives only behind the export/
copy endpoint (backend/assets.py), off the event loop via asyncio.to_thread
- a domain method staying synchronous (record_command's own contract)
could never do that itself, so it was never this layer's job to keep.

backend/canvas.py imports SceneDocument back, so every existing
`from backend.canvas import SceneDocument` consumer keeps working
unchanged. NOTE for future test authors: names bound HERE resolve in THIS
module's namespace - patching `backend.canvas.render_chart_png`-style
would silently no-op; patch `backend.domain.graph.<name>` instead.
"""

from __future__ import annotations

from collections import deque
import itertools
import json
import math
import uuid
from dataclasses import dataclass, field
from typing import Any

from graphlink_chart_data import SUPPORTED_CHART_TYPES
from graphlink_grid_view_settings import (
    GRID_SIZE_PRESETS,
    GRID_STYLE_PRESETS,
    GridViewSettings,
)
from graphlink_navigation_pins import NavigationPinStore
from graphlink_token_estimator import TokenEstimator

from backend.domain.branches import BranchOps
from backend.domain.commands import CommandOps
from backend.domain.content_codec import _content_codec
from backend.domain.groups import GroupOps
from backend.domain.model import (
    BRANCH_HORIZONTAL_SPACING,
    CHART_MAX_HEIGHT,
    CHART_MAX_WIDTH,
    CHART_MIN_HEIGHT,
    CHART_MIN_WIDTH,
    CHAT_TITLE_PREVIEW_LENGTH,
    CODE_TITLE_PREVIEW_LENGTH,
    DRAG_FACTOR_MAX,
    DRAG_FACTOR_MIN,
    FONT_FAMILIES,
    FONT_SIZE_MAX,
    FONT_SIZE_MIN,
    GRID_COLOR_PRESETS,
    GROUP_COLLAPSED_HEIGHT,
    GROUP_COLLAPSED_WIDTH,
    GROUP_INELIGIBLE_FRAME_MEMBER_KINDS,
    GROUP_MEMBER_DEFAULT_HEIGHT,
    GROUP_MEMBER_DEFAULT_WIDTH,
    GROUP_PADDING,
    GROUP_PADDING_TOP,
    HTML_TITLE_PREVIEW_LENGTH,
    IMAGE_TITLE_PREVIEW_LENGTH,
    MESSAGE_VERTICAL_SPACING,
    ORGANIZE_SPACING_X,
    ORGANIZE_SPACING_Y,
    SceneEdge,
    SceneEmptyPromptError,
    SceneError,
    SceneNode,
    THINKING_TITLE_PREVIEW_LENGTH,
)
from backend.domain.node_states import (
    ArtifactState,
    ChartState,
    ChatState,
    CodeSandboxState,
    CodeState,
    ContainerState,
    DocumentState,
    FrameState,
    GitlinkState,
    HtmlState,
    ImageState,
    NodeState,
    NoteState,
    PlanState,
    PycoderState,
    WebResearchState,
)


def _estimate_tokens(text: str) -> int:
    """Verbatim equivalent of backend.token_counter.estimate_tokens - see
    this module's docstring for why the domain layer cannot import that
    module directly."""
    return TokenEstimator().count_tokens(text)


@dataclass
class SceneDocument(BranchOps, GroupOps, CommandOps):
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
    # ADR-002 Workstream 1 ("Branch status and lifecycle"): the one node
    # currently marked as this document's Final Deliverable, or None if
    # none is marked. A document-level singular pointer, mirroring
    # last_chat_node_id above, rather than a per-node SceneNode flag -
    # deliberately, since "Final Deliverable" is inherently singular ("the
    # one true output of this document") and a pointer makes exclusivity
    # free (set_final_deliverable just overwrites it) instead of requiring
    # a hunt for whichever other node currently holds the flag to clear it
    # first. Reset in clear_for_load below, same list last_chat_node_id is
    # already in.
    final_deliverable_node_id: str | None = None
    # R3.21: in-memory, session-scoped store for image-node bytes, keyed by
    # asset id (see backend/domain/node_states.py's ImageState.image_asset_id,
    # ADR-002 stage 2.5) -> (raw_bytes, mime_type).
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
    # ADR-010 stage 10.1: every invertible mutation this session has
    # recorded, oldest first. Bounded per the ADR's own durability boundary
    # ("session-scoped and in-memory (bounded, e.g. 100 composite commands),
    # not persisted") - a deque's maxlen drops the oldest silently, which is
    # the correct behavior for undo history specifically: losing the ability
    # to undo something from 100 operations ago is the intended limit, not a
    # data loss (the scene itself is untouched).
    #
    # This IS the undo stack (newest last). ADR-010 stage 10.2 added
    # undo()/redo() on top of it - see backend/domain/commands.py's CommandOps
    # for the cursor semantics, live-run refusal and composite grouping.
    command_log: deque = field(default_factory=lambda: deque(maxlen=100), repr=False)
    # Commands that have been undone and can be re-applied. Cleared the
    # moment any NEW command is performed (the standard redo-branch discard).
    # Unbounded is fine: it can never exceed command_log's own 100, since
    # every entry here came out of there.
    redo_stack: list = field(default_factory=list, repr=False)
    # ADR-010 stage 10.3: composite() bookkeeping. While depth > 0, recorded
    # commands buffer here instead of hitting the stack, and merge into one
    # command when the outermost composite closes.
    _composite_depth: int = field(default=0, repr=False)
    _composite_buffer: list = field(default_factory=list, repr=False)
    _counter: itertools.count = field(default_factory=itertools.count, repr=False)
    # ADR-003 stage 3.4: the last wire state actually published, kept so
    # take_dirty_patch_ops below can diff against it. None until the first
    # publish (there is nothing to diff a first snapshot against).
    _published_nodes: dict[str, dict[str, Any]] | None = field(default=None, repr=False, compare=False)
    _published_edges: dict[str, dict[str, Any]] | None = field(default=None, repr=False, compare=False)
    _published_pins: list[dict[str, Any]] | None = field(default=None, repr=False, compare=False)
    _published_view: dict[str, Any] | None = field(default=None, repr=False, compare=False)
    _published_meta: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    # -- ADR-003 stage 3.4: the scene topic's patch protocol ---------------

    def _view_wire(self) -> dict[str, Any]:
        return {"zoomFactor": self.zoom_factor, "scrollX": self.scroll_x, "scrollY": self.scroll_y}

    def published_scene_payload(self) -> dict[str, Any] | None:
        """The wire state as of the LAST publish - what every already-connected
        client currently holds - or None before the first publish.

        REVIEW-FIX, closing a real permanent-divergence bug. A subscribing
        connection used to be served the LIVE document stamped with the
        last-published revision, which are two different states whenever the
        document was mutated between publishes (routine: several intent
        handlers await another topic's publish in between, and agents.py sets
        then clears pending_request_id around an await). The patch protocol's
        whole correctness argument rests on "revision R means you hold the
        state as of publish R" - hand a client something NEWER than R and its
        baseRevision still chains perfectly forever while the diff, which is
        computed against the published baseline, never emits the op that
        would fix the difference.

        Reproduced: publish (baseline={n0}); add n1 WITHOUT publishing; a
        second window subscribes and receives n0+n1 stamped revision 1;
        delete n1 and move n0; publish. The diff never saw n1, so it emits
        only upsertNode(n0) - no removeNodes - and baseRevision 1 matches, so
        the client applies it happily and shows the deleted node for the rest
        of the session with nothing able to detect it.

        Serving the published baseline instead makes the subscriber exactly
        as up to date as everyone else, and the very next patch carries the
        intervening changes to all of them identically. Whole-node ops are
        absolute assignments, so there is no ordering hazard in that catch-up.
        """
        if self._published_nodes is None or self._published_edges is None:
            return None
        return {
            "nodes": list(self._published_nodes.values()),
            "edges": list(self._published_edges.values()),
            "pins": list(self._published_pins or []),
            **(self._published_meta or {}),
            **(self._published_view or {}),
        }

    def take_dirty_patch_ops(self) -> list[dict[str, Any]] | None:
        """The scene topic's patch_builder (wired in backend/canvas.py,
        SessionBus's patch-aware sibling to the full-snapshot `builder`
        callable - see events.py's own _Topic docstring). Returns the ops
        that carry this document from its last-published wire state to its
        current one, or None meaning "send a full snapshot instead".

        MECHANISM NOTE - this deliberately diverges from the mechanism
        ADR-003's own Decision text sketches ("server bookkeeping to a
        dirty-id set" fed by the mutation sites). That was implemented
        first and abandoned for a real, empirically-reproduced
        silent-data-loss bug, not a taste preference. A dirty-id set is
        only safe if EVERY mutator marks; with ~60 mutators across this
        file, groups.py and branches.py reached from ~146 publish sites,
        any partial rollout breaks catastrophically rather than gracefully:
        an UNinstrumented mutation that publishes while some EARLIER
        instrumented mutation's marks are still pending emits a patch
        describing only the stale earlier change, and the real one never
        reaches the client at all. Reproduced against a real intent
        (setCodeSandboxAllowSourceBuilds, uninstrumented, publishing after
        an instrumented connect() during node setup): the patch carried
        only the setup edge and the actual flag change vanished.
        "Instrument everything, perfectly, forever" is not a property a
        test can enforce, so the whole-node DIFF below is used instead - it
        is correct by construction for every mutator, present and future,
        instrumented or not, and needs no discipline at any mutation site.
        The granularity the ADR actually specifies is unchanged: whole-node
        ops, never field-level diffing, no CRDT.

        The cost this trades away is real but small in context: building
        every node's wire dict on each publish. That is exactly what the
        pre-3.4 full-snapshot path ALREADY did on every publish, so it adds
        no work relative to today - it only changes what gets SENT, which
        is where this stage's own exit criterion (bytes on the wire) lives.

        None (send a full snapshot) is returned for the first publish, and
        whenever the pins list changed - pins have no op in the ADR's own
        op set, so a pin mutation is honestly outside what a patch can
        express and falls back rather than being silently dropped."""
        nodes = {node_id: self._node_wire(n) for node_id, n in self.nodes.items()}
        edges = {edge_id: self._edge_wire(e) for edge_id, e in self.edges.items()}
        pins = self._pins_wire()
        view = self._view_wire()
        meta = self._meta_wire()
        previous_nodes = self._published_nodes
        previous_edges = self._published_edges
        previous_view = self._published_view
        previous_meta = self._published_meta
        pins_changed = pins != self._published_pins
        # Record BEFORE any early return: whichever branch the caller takes
        # (patch or snapshot), what it sends reflects exactly this state.
        self._published_nodes = nodes
        self._published_edges = edges
        self._published_pins = pins
        self._published_view = view
        self._published_meta = meta
        if previous_nodes is None or previous_edges is None or pins_changed:
            return None
        ops: list[dict[str, Any]] = []
        for node_id, wire in nodes.items():
            if previous_nodes.get(node_id) != wire:
                ops.append({"op": "upsertNode", "node": wire})
        removed_node_ids = sorted(set(previous_nodes) - set(nodes))
        if removed_node_ids:
            ops.append({"op": "removeNodes", "ids": removed_node_ids})
        for edge_id, wire in edges.items():
            if previous_edges.get(edge_id) != wire:
                ops.append({"op": "upsertEdge", "edge": wire})
        removed_edge_ids = sorted(set(previous_edges) - set(edges))
        if removed_edge_ids:
            ops.append({"op": "removeEdges", "ids": removed_edge_ids})
        if view != previous_view:
            ops.append({"op": "setView", "view": view})
        if meta != previous_meta:
            ops.append({"op": "setMeta", "meta": meta})
        return ops

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
        self.final_deliverable_node_id = None
        self.zoom_factor = 1.0
        self.scroll_x = 0.0
        self.scroll_y = 0.0
        self.total_session_tokens = 0
        self.current_chat_id = None
        # ADR-010 stage 10.1: undo history does NOT survive a session load.
        # Every command in it references node/edge ids from the document
        # being replaced, so inverting one afterward would resurrect a node
        # from the PREVIOUS chat into this one. The ADR scopes undo history
        # to a session deliberately ("session-scoped and in-memory ... not
        # persisted"); this is the boundary that enforces it.
        self.command_log.clear()
        self.redo_stack.clear()
        # A load landing mid-composite would otherwise leave the buffer
        # holding commands against the OLD document, which the next composite
        # close would merge into a command referencing nodes that no longer
        # exist.
        self._composite_buffer.clear()
        self._composite_depth = 0
        # ADR-003 stage 3.4 review-fix: drop the patch baseline too. Loading a
        # chat replaces the entire document, so diffing the new scene against
        # the OLD one produced a patch no smaller than the snapshot it
        # replaced - measured at 1,666,170 bytes vs 1,677,403 for a
        # 500-node-to-500-node reload (500 upsertNode ops plus removeNodes
        # and removeEdges listing every old id). Correct output, zero
        # benefit, on the single most bandwidth-heavy operation in the app.
        # Clearing the baseline makes the next publish a plain full snapshot,
        # which is both smaller and what a wholesale replacement actually is.
        self._published_nodes = None
        self._published_edges = None
        self._published_pins = None
        self._published_view = None
        self._published_meta = None

    def add_chat_node(
        self,
        x: float,
        y: float,
        content: str,
        is_user: bool,
        parent_id: str | None = None,
        content_parts: list[dict[str, Any]] | None = None,
    ) -> SceneNode:
        """The Qt-free ChatScene.add_chat_node equivalent: a real message-
        bubble node, optionally connected to a parent (the branch it
        continues). Mirrors add_node's id/dict bookkeeping; the only new
        behavior is the parent-edge, ported from the legacy scene's own
        ConnectionItem creation.

        R8a: content_parts is the real multimodal attachment payload
        (image_bytes/audio_file parts) - the data-model capability
        ChatState.content_parts (backend/domain/node_states.py) has carried
        since R6.3, finally populated by a real caller. Optional and
        additive: every existing caller keeps
        passing only (x, y, content, is_user, parent_id) and gets exactly
        the plain-text node it always did."""
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
            state=ChatState(is_user=bool(is_user), content_parts=content_parts),
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
            state=CodeState(code=str(code), language=str(language)),
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
        see DocumentState's own docstring (backend/domain/node_states.py)
        for those exact rules.
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
            state=DocumentState(
                attachment_kind=normalized_kind,
                file_path=str(file_path),
                mime_type=str(mime_type),
                duration_seconds=duration_seconds,
                byte_size=byte_size,
                preview_label=str(preview_label),
            ),
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
            state=HtmlState(),
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
        node.state.html_splitter_state = float(value)

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
            state=ImageState(image_asset_id=asset_id),
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

    def append_conversation_assistant_message(
        self, node_id: str, text: str, incomplete: bool = False
    ) -> SceneNode:
        """Append a real assistant message to a conversation node's history -
        mirrors graphlink_conversation_node.py's add_ai_message, minus the
        view-layer bubble creation.

        ADR-006 stage 6.4: `incomplete=True` marks a PARTIAL reply whose
        stream died mid-generation (H5 - the accumulated text is preserved
        instead of lost). The key is only written when set - completed
        messages keep their exact two-key {role, content} shape, so every
        existing history consumer (session round-trip, agent context
        assembly) sees byte-identical data for the normal path."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        message: dict[str, Any] = {"role": "assistant", "content": str(text)}
        if incomplete:
            message["incomplete"] = True
        node.history.append(message)
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
            state=WebResearchState(),
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
        node.state.research_stage = ""
        node.state.research_completed = 0
        node.state.research_total = 0
        node.state.research_active_source_id = None
        node.state.research_error = ""
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
        node.state.research_stage = event.stage.value
        node.state.research_completed = event.completed
        node.state.research_total = event.total
        node.state.research_active_source_id = event.source_id
        return node

    def complete_web_research_run(self, node_id: str, result_wire: dict) -> SceneNode:
        """Land a successful run's result. Raises SceneError if the node is
        gone - the WS wrapper's own liveness check (in register_canvas)
        guards the actual mid-flight-delete race; this stays a hard
        precondition here, same posture as update_chat_node_content."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.state.research_stage = "completed"
        node.state.research_error = ""
        node.state.research_active_source_id = None
        node.state.research_result = result_wire
        return node

    def fail_web_research_run(self, node_id: str, *, cancelled: bool, message: str) -> SceneNode:
        """Land a failed or cancelled run. research_result is deliberately
        left untouched (stale-while-revalidate - see start_web_research_run's
        own docstring)."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.state.research_stage = "cancelled" if cancelled else "failed"
        node.state.research_error = message
        node.state.research_active_source_id = None
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
            state=ArtifactState(),
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
        ArtifactState's own comment, backend/domain/node_states.py), plus
        append a real assistant turn to history. Raises SceneError if the node is
        gone - this WS wrapper does NOT pre-check liveness before calling
        this, same posture as send_conversation_message's own _on_reply, not
        web_research's more defensive pre-check pattern (there is no
        stage-stepper/persisted-error field here for a mid-flight delete to
        race against)."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.state.artifact_content = str(new_content)
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
            state=GitlinkState(),
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
        if node.kind != "gitlink":
            raise SceneError(f"node is not a gitlink node: {node_id}")
        node.state.gitlink_local_root = str(local_root)
        return node

    def store_gitlink_repo_tree(self, node_id: str, repo: str, branch: str, file_paths: list[str]) -> SceneNode:
        """Lands a successful loadGitlinkRepoTree result: repo, branch
        (resolved server-side, including any default-branch lookup), and the
        scanned text-file path list."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "gitlink":
            raise SceneError(f"node is not a gitlink node: {node_id}")
        node.state.gitlink_repo = str(repo)
        node.state.gitlink_branch = str(branch)
        node.state.gitlink_repo_file_paths = list(file_paths)
        return node

    def store_gitlink_snapshot_root(self, node_id: str, repo: str, branch: str, local_root: str) -> SceneNode:
        """Lands a successful importGitlinkSnapshot result - sets
        repo/branch/local_root AND gitlink_imported_root (so a later run
        knows this path came from an import, matching legacy repo_state's
        imported_root concept)."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "gitlink":
            raise SceneError(f"node is not a gitlink node: {node_id}")
        node.state.gitlink_repo = str(repo)
        node.state.gitlink_branch = str(branch)
        node.state.gitlink_local_root = str(local_root)
        node.state.gitlink_imported_root = str(local_root)
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
        if node.kind != "gitlink":
            raise SceneError(f"node is not a gitlink node: {node_id}")
        node.state.gitlink_scope_mode = str(scope_mode)
        node.state.gitlink_selected_paths = list(selected_paths)
        node.state.gitlink_context_xml = str(context_xml)
        node.state.gitlink_context_stats = {str(k): str(v) for k, v in (context_stats or {}).items()}
        node.state.gitlink_context_summary = str(context_summary)
        node.state.gitlink_context_version += 1
        return node

    def fetch_gitlink_context_xml(self, node_id: str) -> str:
        """The read-side of the lazy fetch: gitlink_context_xml is EXCLUDED
        from scene_payload() (see the field's own comment on SceneNode) - this
        is the only way the frontend ever gets the full text, via the
        read-only fetchGitlinkContext intent."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "gitlink":
            raise SceneError(f"node is not a gitlink node: {node_id}")
        return node.state.gitlink_context_xml

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
        node.state.gitlink_task_prompt = str(task_prompt)
        node.state.gitlink_error = ""
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
        node.state.gitlink_proposal_markdown = str(proposal_markdown)
        node.state.gitlink_pending_changes = list(pending_changes or [])
        node.state.gitlink_preview_text = str(preview_text)
        if node.state.gitlink_pending_changes:
            node.state.gitlink_change_state = "previewed"
            node.state.gitlink_change_fingerprint = fingerprint
            node.state.gitlink_change_local_root = str(local_root).strip()
        else:
            node.state.gitlink_change_state = "draft"
            node.state.gitlink_change_fingerprint = None
            node.state.gitlink_change_local_root = None
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
        node.state.gitlink_error = str(message)
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
        node.state.gitlink_change_state = "applied"
        node.state.gitlink_error = ""
        node.state.gitlink_pending_changes = []
        node.state.gitlink_change_fingerprint = None
        node.state.gitlink_change_local_root = None
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
        node.state.gitlink_change_state = "previewed"
        node.state.gitlink_change_fingerprint = None
        node.state.gitlink_change_local_root = None
        node.state.gitlink_error = str(message)
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
    # mutates node.state.pycoder_code/pycoder_awaiting_approval directly inline
    # inside AgentDispatcher.start_pycoder_run (backend/agents.py) - these two
    # SceneDocument methods were a second, never-wired copy of that same
    # mutation, built ahead of the live dispatch path and then never rewired
    # to it. Removing dead code is the correct fix here, not building a
    # redundant call site just to keep them alive.

    def add_pycoder_node(self, x: float, y: float, parent_id: str) -> SceneNode:
        """The Py-Coder node's creation primitive - same required-parent
        posture as every R5 sibling (Web Research/Artifact/Gitlink): never
        exists unparented. Title is always the fixed literal "Py-Coder"
        (matches backend/plugins.py's own plugin display name).
        pycoder_repl_id is minted here, ONCE, exactly like
        code_sandbox_sandbox_id below - see PycoderState's own docstring
        for why node.id itself is not durable enough for this."""
        if parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title="Py-Coder",
            kind="pycoder",
            state=PycoderState(pycoder_repl_id=uuid.uuid4().hex[:12]),
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
        if node.kind != "pycoder":
            raise SceneError(f"node is not a pycoder node: {node_id}")
        if mode not in ("ai_driven", "manual"):
            raise SceneError(f"unknown pycoder mode: {mode}")
        node.state.pycoder_mode = str(mode)
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
        if node.state.pycoder_mode == "manual":
            node.state.pycoder_code = str(input_text)
        else:
            node.state.pycoder_prompt = str(input_text)
        node.state.pycoder_error = ""
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
        result landing after deletion. Not kind-guarded: only ever reached
        via run_pycoder's own on_success closure (backend/api/
        intents_pycoder.py), whose node_id was already validated by
        start_pycoder_run's own guard earlier in the same request - same
        posture as complete_gitlink_run/complete_gitlink_apply."""
        node = self.nodes.get(node_id)
        if node is None:
            return None
        node.state.pycoder_code = str(code)
        node.state.pycoder_output = str(output)
        node.state.pycoder_analysis = str(analysis)
        node.state.pycoder_last_run_failed = bool(last_run_failed)
        node.state.pycoder_awaiting_approval = False
        node.state.pycoder_approved_fingerprint = None
        node.state.pycoder_error = ""
        return node

    def fail_pycoder_run(self, node_id: str, message: str) -> SceneNode | None:
        """Land a failed (or denied-approval, or cancelled) run.
        awaiting_approval is ALWAYS cleared here too - a denied/cancelled
        approval must not leave the node stuck showing the approval prompt
        forever. Deliberately does NOT clear pycoder_code/pycoder_output/
        pycoder_analysis - a failed re-run must never wipe out a previously
        completed result, only the error banner reflects the new failure
        (stale-while-revalidate, same posture as fail_gitlink_run). Not
        kind-guarded - see complete_pycoder_run's own comment."""
        node = self.nodes.get(node_id)
        if node is None:
            return None
        node.state.pycoder_awaiting_approval = False
        node.state.pycoder_approved_fingerprint = None
        node.state.pycoder_error = str(message)
        return node

    # -- R5.4: Execution Sandbox node ------------------------------------------
    #
    # Same import posture as the Py-Coder section above: canvas.py imports
    # NOTHING from graphlink_plugins.code_sandbox.

    def add_code_sandbox_node(self, x: float, y: float, parent_id: str) -> SceneNode:
        """The Virtual Environment Runner node's creation primitive - same
        required-parent posture as every R5 sibling. Title is always the
        fixed literal "Virtual Environment Runner" (matches
        backend/plugins.py's own plugin display name - renamed under
        ADR-002 P0 from "Execution Sandbox", which oversold what is
        actually a plain OS subprocess running inside a venv, not an
        OS-level sandbox; the internal kind="code_sandbox" identifier is
        UNCHANGED, since it's persisted wire/save-format state, not a
        display string). code_sandbox_sandbox_id is minted here, ONCE, at
        creation time - a short uuid4 hex used purely as this node's
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
            title="Virtual Environment Runner",
            kind="code_sandbox",
            state=CodeSandboxState(code_sandbox_sandbox_id=uuid.uuid4().hex[:12]),
        )
        self.nodes[node_id] = node
        self.connect(parent_id, node_id)
        return node

    def set_code_sandbox_requirements(self, node_id: str, requirements_text: str) -> SceneNode:
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "code_sandbox":
            raise SceneError(f"node is not a code_sandbox node: {node_id}")
        node.state.code_sandbox_requirements = str(requirements_text)
        return node

    def set_code_sandbox_allow_source_builds(self, node_id: str, allow: bool) -> SceneNode:
        """ADR-005 stage 5.5: the approval panel's own source-build opt-in
        checkbox, fired on every toggle (same "ungated, fires immediately"
        posture as set_code_sandbox_requirements above). Setting this outside
        an open approval gate is harmless, not just permitted - agents.py
        resets the field to False at the top of every gate-open, so a value
        set here while no gate is open never reaches an actual run; the
        approval panel is the only surface that ever renders this control,
        and it only renders while awaiting_approval is true."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "code_sandbox":
            raise SceneError(f"node is not a code_sandbox node: {node_id}")
        node.state.code_sandbox_approval_allow_source_builds = bool(allow)
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
        node.state.code_sandbox_prompt = str(input_text)
        node.state.code_sandbox_error = ""
        return node

    def complete_code_sandbox_run(self, node_id: str, code: str, output: str, analysis: str) -> SceneNode | None:
        """Land a successful run - mirrors complete_pycoder_run exactly,
        minus the last_run_failed flag (Execution Sandbox has no such field;
        an unrecovered failure after exhausting its own repair attempts
        surfaces as a failed run, see AgentDispatcher.start_code_sandbox_run,
        not as a "succeeded but flagged" result the way Py-Coder's repair
        loop does). Not kind-guarded: only ever reached via run_code_
        sandbox's own on_success closure (backend/api/
        intents_code_sandbox.py), whose node_id was already validated by
        start_code_sandbox_run's own guard earlier in the same request -
        same posture as complete_pycoder_run/complete_gitlink_run."""
        node = self.nodes.get(node_id)
        if node is None:
            return None
        node.state.code_sandbox_code = str(code)
        node.state.code_sandbox_output = str(output)
        node.state.code_sandbox_analysis = str(analysis)
        node.state.code_sandbox_awaiting_approval = False
        node.state.code_sandbox_approval_requirements = ""
        node.state.code_sandbox_approved_fingerprint = None
        node.state.code_sandbox_approval_allow_source_builds = False
        node.state.code_sandbox_approval_is_repair = False
        node.state.code_sandbox_error = ""
        return node

    def fail_code_sandbox_run(self, node_id: str, message: str) -> SceneNode | None:
        """Land a failed (or denied-approval, or cancelled) run - mirrors
        fail_pycoder_run exactly (same stale-while-revalidate posture, same
        unconditional awaiting_approval clear). Not kind-guarded - see
        complete_code_sandbox_run's own comment."""
        node = self.nodes.get(node_id)
        if node is None:
            return None
        node.state.code_sandbox_awaiting_approval = False
        node.state.code_sandbox_approval_requirements = ""
        node.state.code_sandbox_approved_fingerprint = None
        node.state.code_sandbox_approval_allow_source_builds = False
        node.state.code_sandbox_approval_is_repair = False
        node.state.code_sandbox_error = str(message)
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
            state=NoteState(
                is_system_prompt=bool(is_system_prompt),
                is_summary_note=bool(is_summary_note),
            ),
        )
        self.nodes[node_id] = node
        return node

    def set_note_content(self, node_id: str, content: str) -> None:
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.content = str(content)

    # -- ADR-008 stage 8.3: plan node (the Builder's checklist) --------------

    def add_plan_node(
        self,
        x: float,
        y: float,
        goal: str,
        *,
        mode: str = "copilot",
        max_steps: int = 12,
        max_tokens: int = 150_000,
        max_wall_seconds: int = 900,
    ) -> SceneNode:
        """The Builder plan node's creation primitive. Free-floating like a
        note (a build STARTS from a goal, it does not continue an existing
        branch - the nodes the build creates are the ones that connect);
        `content` reuses the goal text the same way web_research reuses
        content for its query. Everything else lives on PlanState - see its
        own docstring for the state machine and the plan-node-as-resume-
        point contract."""
        if mode not in ("copilot", "autopilot"):
            raise SceneError(f"unknown builder mode: {mode}")
        node_id = f"n{next(self._counter)}"
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title=f"Build: {str(goal)[:CHAT_TITLE_PREVIEW_LENGTH]}" if goal else "Build",
            kind="plan",
            content=str(goal),
            state=PlanState(
                plan_goal=str(goal),
                builder_mode=mode,
                builder_max_steps=int(max_steps),
                builder_max_tokens=int(max_tokens),
                builder_max_wall_seconds=int(max_wall_seconds),
            ),
        )
        self.nodes[node_id] = node
        return node

    _PLAN_STEP_STATUSES = ("pending", "running", "done", "failed", "skipped")

    def set_plan_steps(self, node_id: str, steps: list) -> SceneNode:
        """Replaces the plan's step list - the one plan mutator that goes
        through record_command (a user editing the checklist, or the
        model's replan tool): step CONTENT is document state a Ctrl+Z must
        reach, unlike the run-lifecycle fields (builder_status/spent_*/
        awaiting_*) which the loop writes directly, exactly as pycoder's
        run pipeline writes its own awaiting/progress fields.

        Steps whose status is not "pending" are immutable history - a
        replacement must carry every non-pending step through unchanged
        (same id, title, status), enforced here so neither a user edit nor
        a model replan can rewrite what already happened."""
        node = self.nodes.get(node_id)
        if node is None or not isinstance(node.state, PlanState):
            raise SceneError(f"not a plan node: {node_id}")
        normalized: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for raw in steps:
            if not isinstance(raw, dict):
                raise SceneError("each step must be an object")
            step_id = str(raw.get("id") or f"s{len(normalized) + 1}")
            if step_id in seen_ids:
                raise SceneError(f"duplicate step id: {step_id}")
            seen_ids.add(step_id)
            status = str(raw.get("status") or "pending")
            if status not in self._PLAN_STEP_STATUSES:
                raise SceneError(f"unknown step status: {status}")
            title = str(raw.get("title") or "").strip()
            if not title:
                raise SceneError("each step needs a title")
            normalized.append({
                "id": step_id, "title": title, "status": status,
                "detail": str(raw.get("detail") or ""),
            })
        frozen = {s["id"]: s for s in node.state.plan_steps if s.get("status") != "pending"}
        for step_id, original in frozen.items():
            replacement = next((s for s in normalized if s["id"] == step_id), None)
            if replacement is None:
                raise SceneError(
                    f"step {step_id!r} has already run ({original['status']}) and cannot be removed"
                )
            if replacement["title"] != original["title"] or replacement["status"] != original["status"]:
                raise SceneError(
                    f"step {step_id!r} has already run ({original['status']}) and cannot be rewritten"
                )
        node.state.plan_steps = normalized
        return node

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

        ADR-013 stage 13.4: no longer renders a PNG here - the client-side
        interactive renderer (stage 13.2) draws straight from chart_data,
        and nothing has consumed the backend-rendered display asset since.
        A chart's ONLY remaining matplotlib render is the export/copy
        endpoint (backend/assets.py), a fresh re-render on every request."""
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
            state=ChartState(
                chart_type=normalized_type,
                chart_data=safe_chart_data,
                chart_error=str(chart_error),
                chart_source_node_id=parent_id or "",
            ),
        )

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

        ADR-013 stage 13.4: no longer re-renders a PNG here - see
        add_chart_node's own docstring for why."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "chart":
            raise SceneError(f"node is not a chart node: {node_id}")

        requested_width = float(width)
        requested_height = float(height)
        clamped_width = min(CHART_MAX_WIDTH, max(CHART_MIN_WIDTH, requested_width))
        clamped_height = min(CHART_MAX_HEIGHT, max(CHART_MIN_HEIGHT, requested_height))

        if node.state.chart_aspect_locked and requested_width > 0 and requested_height > 0:
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

        node.state.chart_width = clamped_width
        node.state.chart_height = clamped_height

    def toggle_chart_aspect_lock(self, node_id: str) -> None:
        """Chart kind only (SceneError otherwise). Flips chart_aspect_locked."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "chart":
            raise SceneError(f"node is not a chart node: {node_id}")
        node.state.chart_aspect_locked = not node.state.chart_aspect_locked

    def update_chat_node_content(
        self, node_id: str, content: str, incomplete: bool = False
    ) -> SceneNode:
        """The regenerate primitive: mutate an EXISTING chat node's content in
        place - the first in-place mutation of a content-bearing field in this
        file (move_node/set_chat_collapsed/set_node_docked all mutate a
        position/flag, never displayed text). Scope confirmed against legacy's
        ChatNode.update_content (graphlink_nodes/graphlink_node_chat.py:677-686):
        sets content ONLY. Does not touch title (legacy's update_content never
        recomputes any title-like state either, and every other in-place mutator
        here already leaves title untouched post-creation - consistent, not a
        new carve-out). Does not touch is_user/is_collapsed/kind.

        ADR-006 stage 6.4: `incomplete` marks a PARTIAL reply committed after
        its stream died (see ChatState.response_incomplete). A normal full
        regenerate passes the default False, which doubles as the CLEAR for a
        previously interrupted node - retry succeeds, banner goes away."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.content = str(content)
        if isinstance(node.state, ChatState):
            node.state.response_incomplete = bool(incomplete)
        return node

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

    def set_all_conversational_collapsed(self, collapsed: bool) -> None:
        """R7.5e: Collapse All / Expand All - the bulk counterpart to
        set_chat_collapsed above. Mirrors legacy's
        graphlink_window_navigation.py collapse_all/expand_all exactly:
        those iterate ONLY scene._all_conversational_nodes() (chat +
        conversation + html_view nodes) and call set_collapsed(bool) on
        each - NOT code/document/image/thinking/chart nodes, and NOT
        frame/container groups (whose is_collapsed drives derived geometry
        via _recompute_group_bounds, same carve-out set_chat_collapsed's
        own comment documents - a frame/container never opts into this
        bulk op, only into the per-node setter above)."""
        collapsed = bool(collapsed)
        for node in self.nodes.values():
            if node.kind in ("chat", "conversation", "html"):
                node.is_collapsed = collapsed

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
        node.state.chat_scroll_value = float(value)

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
        if node.kind == "frame":
            # R6.1 follow-up: dragging a frame directly - a locked whole-
            # group drag (the frontend commits the frame's own position
            # before its members') OR an unlocked frame dragged
            # independently of its members - pins an explicit position
            # anchor. Without this, the very next member move would
            # recompute this frame straight back to bbox-of-members-
            # centered, silently undoing the drag. Harmless for a locked
            # drag: members move by the identical delta, so the anchor and
            # the live bbox stay in agreement. See _recompute_group_bounds
            # for how this anchor is unioned with live content so it still
            # can never clip a member.
            node.state.group_manual_x, node.state.group_manual_y = node.x, node.y
            self._recompute_group_bounds(node_id)
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

    def move_nodes(self, positions: list[tuple[str, float, float]]) -> None:
        """Atomically commit a BATCH of node positions - the group-drag
        counterpart to move_node above. A group drag's commit touches the
        group's own node AND every one of its (possibly nested-transitive)
        members; calling move_node once per node in that set publishes a
        scene snapshot after EACH individual position lands, so the
        frontend briefly renders N genuinely inconsistent intermediate
        states (some members caught up, some not) before the last one
        commits - and since _recompute_group_bounds now correctly grows a
        frame/container's box to enclose whatever the CURRENT bbox-of-
        members is (see that method's own comment), those intermediate
        states aren't just "slightly stale", they visibly stretch and
        resettle each time - a real, user-visible glitch on every group
        drag release, not a cosmetic footnote.

        This updates every position in ONE pass first, THEN recomputes
        bounds exactly once per affected group (deduplicated - both "this
        IS a group that moved" and "this owns a node that moved" collapse
        to the same set), using the fully-settled positions throughout.
        Callers still using move_node for a single, non-group node stay
        unaffected - this is purely additive."""
        moved_ids: set[str] = set()
        for node_id, x, y in positions:
            node = self.nodes.get(node_id)
            if node is None:
                continue
            node.x, node.y = float(x), float(y)
            moved_ids.add(node_id)
            if node.kind == "frame":
                node.state.group_manual_x, node.state.group_manual_y = node.x, node.y
        affected_groups: set[str] = set()
        for moved_id in moved_ids:
            moved_node = self.nodes.get(moved_id)
            if moved_node is not None and moved_node.kind in ("frame", "container"):
                affected_groups.add(moved_id)
        for group in self.nodes.values():
            if group.kind in ("frame", "container") and moved_ids.intersection(group.item_ids):
                affected_groups.add(group.id)
        for group_id in affected_groups:
            self._recompute_group_bounds(group_id)

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
                # ADR-002 stage 2.5: image_asset_id lives on node.state now
                # (ImageState), not directly on SceneNode - getattr rather
                # than a `node.kind == "image"` check since node.state is
                # None for every non-image kind (nothing to evict either
                # way), same duck-typed posture as this file's other
                # cross-kind scans.
                image_asset_id = getattr(node.state, "image_asset_id", None)
                if image_asset_id:
                    self.image_assets.pop(image_asset_id, None)
                # R6.2 minted a chart node's rendered PNG into this same
                # image_assets dict, evicted here on delete - ADR-013 stage
                # 13.4 retired that render (add_chart_node/resize_chart no
                # longer produce one; see ChartState's own docstring), so
                # there is no longer a chart-owned image_assets entry to
                # clean up here.
                self._detach_node_from_membership(node_id)

    # -- ADR-014 stage 14.1: Plugin SDK node-creation primitive -------------

    def add_plugin_node(
        self, kind: str, x: float, y: float, parent_id: str, *,
        title: str = "", content: str = "", state: NodeState | None = None,
    ) -> SceneNode:
        """Generic node-creation primitive for ADR-014's Plugin SDK
        (backend/plugin_sdk.py). Mirrors add_web_research_node's own body
        exactly - mint an id off the SAME self._counter every add_X_node
        method already uses, insert into self.nodes, connect to the
        parent. `kind` arrives here ALREADY namespaced as
        f"{plugin_id}.{local_kind}" (backend/plugin_sdk.py's HostContext.
        register_node_kind) - this method itself does no namespacing or
        validation of its own, same "trust the caller, one required-parent
        posture" contract as add_web_research_node/add_gitlink_node/etc."""
        if parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        node = SceneNode(
            id=node_id, x=float(x), y=float(y),
            title=str(title) or kind, kind=str(kind), content=str(content), state=state,
        )
        self.nodes[node_id] = node
        self.connect(parent_id, node_id)
        return node

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
        self.total_session_tokens += _estimate_tokens(str(text))

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

    def _node_wire(self, n: SceneNode) -> dict[str, Any]:
        """The per-node wire shape - ADR-003 stage 3.4 factored this out of
        scene_payload() verbatim (byte-identical output) so both the full
        snapshot AND take_dirty_patch_ops's upsertNode ops build a node's
        payload from exactly ONE place, rather than a second copy drifting
        out of sync with this one field by field."""
        return {
            "id": n.id,
            "x": n.x,
            "y": n.y,
            "title": n.title,
            "kind": n.kind,
            "content": n.content,
            "isUser": n.state.is_user if isinstance(n.state, ChatState) else False,
            "isCollapsed": n.is_collapsed,
            "code": n.state.code if isinstance(n.state, CodeState) else "",
            "language": n.state.language if isinstance(n.state, CodeState) else "",
            "attachmentKind": n.state.attachment_kind if isinstance(n.state, DocumentState) else "",
            "filePath": n.state.file_path if isinstance(n.state, DocumentState) else "",
            "mimeType": n.state.mime_type if isinstance(n.state, DocumentState) else "",
            "durationSeconds": n.state.duration_seconds if isinstance(n.state, DocumentState) else None,
            "byteSize": n.state.byte_size if isinstance(n.state, DocumentState) else None,
            "previewLabel": n.state.preview_label if isinstance(n.state, DocumentState) else "",
            "isDocked": n.is_docked,
            "imageAssetId": n.state.image_asset_id if isinstance(n.state, ImageState) else "",
            "history": [
                # ADR-006 stage 6.4: the projection stays a strict allow-list
                # (never spread the raw dict - legacy entries can carry
                # arbitrary keys), widened by exactly one optional marker:
                # "incomplete" flags a partial assistant reply whose stream
                # died (see append_conversation_assistant_message).
                {
                    "role": m["role"],
                    "content": m["content"],
                    "incomplete": bool(m.get("incomplete", False)),
                }
                for m in n.history
            ],
            "pendingRequestId": n.pending_request_id,
            # ADR-002 Workstream 1 ("Synthesize Branches") - see
            # ChatState's own comment, backend/domain/node_states.py.
            "provider": n.state.provider if isinstance(n.state, ChatState) else None,
            "model": n.state.model if isinstance(n.state, ChatState) else None,
            # ADR-018 stage 18.3: see ChatState's own comment on override_
            # provider/override_model_id for why this is a distinct pair
            # from provider/model directly above (output provenance vs.
            # input routing pin).
            "overrideProvider": n.state.override_provider if isinstance(n.state, ChatState) else "",
            "overrideModelId": n.state.override_model_id if isinstance(n.state, ChatState) else "",
            # ADR-017 stage 17.5: see ChatState's own comment on
            # index_into_knowledge.
            "indexIntoKnowledge": n.state.index_into_knowledge if isinstance(n.state, ChatState) else False,
            "isBranchSynthesis": n.state.is_branch_synthesis if isinstance(n.state, ChatState) else False,
            "synthesisInstructions": (
                n.state.synthesis_instructions if isinstance(n.state, ChatState) else ""
            ),
            # ADR-002 Workstream 1 ("Branch status and lifecycle") -
            # see ChatState's own comment/SceneDocument.
            # final_deliverable_node_id's own comment. "active" (not
            # "") is the correct non-chat fallback - it is
            # branch_status's own real pre-migration default, not an
            # empty placeholder.
            "branchStatus": n.state.branch_status if isinstance(n.state, ChatState) else "active",
            # ADR-006 stage 6.4 (H5): partial reply preserved after a dead
            # stream - see ChatState.response_incomplete's own comment.
            "responseIncomplete": (
                n.state.response_incomplete if isinstance(n.state, ChatState) else False
            ),
            # ADR-006 stage 6.8: provider-reported usage stamped on chat
            # replies - see ChatState's own comment. Nullable, like
            # provider/model above.
            "promptTokens": n.state.prompt_tokens if isinstance(n.state, ChatState) else None,
            "completionTokens": (
                n.state.completion_tokens if isinstance(n.state, ChatState) else None
            ),
            # ADR-016 stage 16.2: the cost snapshot taken when usage was
            # stamped - see ChatState.estimated_cost_usd's own comment.
            "estimatedCostUsd": (
                n.state.estimated_cost_usd if isinstance(n.state, ChatState) else None
            ),
            # ADR-007 stage 7.4: see ChatState.tool_invocations' own comment
            # and ToolInvocationRow's own docstring (contracts/graphlink_
            # scene_payload.py) for why `arguments` is JSON-encoded here
            # rather than passed through as a nested object.
            "toolCalls": (
                [
                    {
                        "id": str(call.get("id", "")),
                        "name": str(call.get("name", "")),
                        "argumentsJson": json.dumps(call.get("arguments") or {}, sort_keys=True),
                        "result": str(call.get("result", "")),
                        "isError": bool(call.get("is_error", False)),
                    }
                    for call in n.state.tool_invocations
                ]
                if isinstance(n.state, ChatState)
                else []
            ),
            "isFinalDeliverable": n.id == self.final_deliverable_node_id,
            "researchStage": n.state.research_stage if isinstance(n.state, WebResearchState) else "",
            "researchCompleted": n.state.research_completed if isinstance(n.state, WebResearchState) else 0,
            "researchTotal": n.state.research_total if isinstance(n.state, WebResearchState) else 0,
            "researchActiveSourceId": (
                n.state.research_active_source_id if isinstance(n.state, WebResearchState) else None
            ),
            "researchError": n.state.research_error if isinstance(n.state, WebResearchState) else "",
            "researchResult": n.state.research_result if isinstance(n.state, WebResearchState) else None,
            "artifactContent": n.state.artifact_content if isinstance(n.state, ArtifactState) else "",
            "gitlinkRepo": n.state.gitlink_repo if isinstance(n.state, GitlinkState) else "",
            "gitlinkBranch": n.state.gitlink_branch if isinstance(n.state, GitlinkState) else "",
            "gitlinkScopeMode": (
                n.state.gitlink_scope_mode if isinstance(n.state, GitlinkState) else "selected"
            ),
            "gitlinkLocalRoot": n.state.gitlink_local_root if isinstance(n.state, GitlinkState) else "",
            "gitlinkRepoFilePaths": (
                list(n.state.gitlink_repo_file_paths) if isinstance(n.state, GitlinkState) else []
            ),
            "gitlinkSelectedPaths": (
                list(n.state.gitlink_selected_paths) if isinstance(n.state, GitlinkState) else []
            ),
            "gitlinkTaskPrompt": n.state.gitlink_task_prompt if isinstance(n.state, GitlinkState) else "",
            # gitlinkContextXml is DELIBERATELY OMITTED - see
            # GitlinkState's own comment. Served on demand via the
            # fetchGitlinkContext intent instead.
            "gitlinkContextStats": (
                dict(n.state.gitlink_context_stats) if isinstance(n.state, GitlinkState) else {}
            ),
            "gitlinkContextSummary": (
                n.state.gitlink_context_summary if isinstance(n.state, GitlinkState) else ""
            ),
            # R5.3 post-review FIX 6: UNLIKE gitlinkContextXml (and
            # unlike gitlink_change_local_root, never on the wire at
            # all), this genuinely needs to be here - see
            # GitlinkState's own comment for why gitlinkContextSummary
            # alone cannot be trusted as a lazy-fetch-once cache key.
            "gitlinkContextVersion": (
                n.state.gitlink_context_version if isinstance(n.state, GitlinkState) else 0
            ),
            "gitlinkProposalMarkdown": (
                n.state.gitlink_proposal_markdown if isinstance(n.state, GitlinkState) else ""
            ),
            "gitlinkPendingChanges": (
                [dict(c) for c in n.state.gitlink_pending_changes] if isinstance(n.state, GitlinkState) else []
            ),
            "gitlinkPreviewText": n.state.gitlink_preview_text if isinstance(n.state, GitlinkState) else "",
            "gitlinkChangeFingerprint": (
                n.state.gitlink_change_fingerprint if isinstance(n.state, GitlinkState) else None
            ),
            "gitlinkChangeState": (
                n.state.gitlink_change_state if isinstance(n.state, GitlinkState) else "draft"
            ),
            "gitlinkError": n.state.gitlink_error if isinstance(n.state, GitlinkState) else "",
            "pycoderMode": n.state.pycoder_mode if isinstance(n.state, PycoderState) else "ai_driven",
            "pycoderPrompt": n.state.pycoder_prompt if isinstance(n.state, PycoderState) else "",
            "pycoderCode": n.state.pycoder_code if isinstance(n.state, PycoderState) else "",
            "pycoderOutput": n.state.pycoder_output if isinstance(n.state, PycoderState) else "",
            "pycoderAnalysis": n.state.pycoder_analysis if isinstance(n.state, PycoderState) else "",
            "pycoderLastRunFailed": (
                n.state.pycoder_last_run_failed if isinstance(n.state, PycoderState) else False
            ),
            "pycoderAwaitingApproval": (
                n.state.pycoder_awaiting_approval if isinstance(n.state, PycoderState) else False
            ),
            "pycoderError": n.state.pycoder_error if isinstance(n.state, PycoderState) else "",
            # codeSandboxSandboxId is DELIBERATELY OMITTED - see
            # CodeSandboxState's own comment (pure internal
            # directory-naming key, mirrors gitlink_imported_root's
            # own "server-side bookkeeping only" precedent).
            "codeSandboxRequirements": (
                n.state.code_sandbox_requirements if isinstance(n.state, CodeSandboxState) else ""
            ),
            "codeSandboxPrompt": (
                n.state.code_sandbox_prompt if isinstance(n.state, CodeSandboxState) else ""
            ),
            "codeSandboxCode": n.state.code_sandbox_code if isinstance(n.state, CodeSandboxState) else "",
            "codeSandboxOutput": (
                n.state.code_sandbox_output if isinstance(n.state, CodeSandboxState) else ""
            ),
            "codeSandboxAnalysis": (
                n.state.code_sandbox_analysis if isinstance(n.state, CodeSandboxState) else ""
            ),
            "codeSandboxAwaitingApproval": (
                n.state.code_sandbox_awaiting_approval if isinstance(n.state, CodeSandboxState) else False
            ),
            # R5.4 CODESANDBOX FIX: the frozen-at-approval-time
            # snapshot, deliberately distinct from
            # codeSandboxRequirements above (that one is the user's
            # still-live, still-editable draft for the NEXT run) -
            # see CodeSandboxState's own comment.
            "codeSandboxApprovalRequirements": (
                n.state.code_sandbox_approval_requirements if isinstance(n.state, CodeSandboxState) else ""
            ),
            # ADR-005 stage 5.5: the user's live source-build opt-in
            # for the CURRENT pending approval - see CodeSandboxState.
            # code_sandbox_approval_allow_source_builds's own comment.
            "codeSandboxApprovalAllowSourceBuilds": (
                n.state.code_sandbox_approval_allow_source_builds
                if isinstance(n.state, CodeSandboxState)
                else False
            ),
            # ADR-005 stage 5.5 review-fix: True only during a
            # repair-loop re-gate - see CodeSandboxState.
            # code_sandbox_approval_is_repair's own comment.
            "codeSandboxApprovalIsRepair": (
                n.state.code_sandbox_approval_is_repair
                if isinstance(n.state, CodeSandboxState)
                else False
            ),
            "codeSandboxError": n.state.code_sandbox_error if isinstance(n.state, CodeSandboxState) else "",
            # R6.1: Notes/Frames/Containers. groupManualWidth/Height
            # are DELIBERATELY OMITTED, same "server-side bookkeeping
            # only" posture as codeSandboxSandboxId/gitlinkImportedRoot
            # above - see those fields' own comments on
            # CodeSandboxState/GitlinkState.
            "color": n.color,
            "headerColor": n.header_color,
            "isSystemPrompt": n.state.is_system_prompt if isinstance(n.state, NoteState) else False,
            "isSummaryNote": n.state.is_summary_note if isinstance(n.state, NoteState) else False,
            # ADR-002 Workstream 1 ("Compare Branches") - see
            # NoteState's own comment, backend/domain/node_states.py.
            "isBranchComparison": n.state.is_branch_comparison if isinstance(n.state, NoteState) else False,
            "itemIds": list(n.item_ids),
            "isLocked": n.state.is_locked if isinstance(n.state, FrameState) else True,
            "groupWidth": n.state.group_width if isinstance(n.state, (FrameState, ContainerState)) else None,
            "groupHeight": (
                n.state.group_height if isinstance(n.state, (FrameState, ContainerState)) else None
            ),
            # R6.2: Chart node.
            "chartType": n.state.chart_type if isinstance(n.state, ChartState) else "",
            "chartData": dict(n.state.chart_data) if isinstance(n.state, ChartState) else {},
            "chartError": n.state.chart_error if isinstance(n.state, ChartState) else "",
            "chartWidth": n.state.chart_width if isinstance(n.state, ChartState) else 680.0,
            "chartHeight": n.state.chart_height if isinstance(n.state, ChartState) else 500.0,
            "chartAspectLocked": n.state.chart_aspect_locked if isinstance(n.state, ChartState) else True,
            "chartSourceNodeId": n.state.chart_source_node_id if isinstance(n.state, ChartState) else "",
            # R6.3: HTML splitter + chat scroll gaps.
            "htmlSplitterState": n.state.html_splitter_state if isinstance(n.state, HtmlState) else None,
            "chatScrollValue": n.state.chat_scroll_value if isinstance(n.state, ChatState) else 0.0,
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
            "contentParts": _content_parts_wire(n.state.content_parts if isinstance(n.state, ChatState) else None),
            # ADR-008 stage 8.3: plan node (the Builder's checklist). Steps
            # cross as typed PlanStepRow dicts - see PlanState's own
            # docstring for the state machine these render.
            "planGoal": n.state.plan_goal if isinstance(n.state, PlanState) else "",
            "planSteps": (
                [
                    {
                        "id": s["id"], "title": s["title"],
                        "status": s["status"], "detail": s["detail"],
                    }
                    for s in n.state.plan_steps
                ]
                if isinstance(n.state, PlanState) else []
            ),
            "builderStatus": n.state.builder_status if isinstance(n.state, PlanState) else "",
            "builderMode": n.state.builder_mode if isinstance(n.state, PlanState) else "",
            "builderRunId": n.state.builder_run_id if isinstance(n.state, PlanState) else "",
            "builderMaxSteps": n.state.builder_max_steps if isinstance(n.state, PlanState) else 0,
            "builderMaxTokens": n.state.builder_max_tokens if isinstance(n.state, PlanState) else 0,
            "builderMaxWallSeconds": n.state.builder_max_wall_seconds if isinstance(n.state, PlanState) else 0,
            "builderSpentSteps": n.state.builder_spent_steps if isinstance(n.state, PlanState) else 0,
            "builderSpentTokens": n.state.builder_spent_tokens if isinstance(n.state, PlanState) else 0,
            "builderSpentWallSeconds": (
                n.state.builder_spent_wall_seconds if isinstance(n.state, PlanState) else 0
            ),
            "builderAwaitingToolApproval": (
                n.state.builder_awaiting_tool_approval if isinstance(n.state, PlanState) else False
            ),
            "builderApprovalToolName": (
                n.state.builder_approval_tool_name if isinstance(n.state, PlanState) else ""
            ),
            "builderApprovalSummary": (
                n.state.builder_approval_summary if isinstance(n.state, PlanState) else ""
            ),
            "builderStatusDetail": n.state.builder_status_detail if isinstance(n.state, PlanState) else "",
        }

    def _edge_wire(self, e: SceneEdge) -> dict[str, Any]:
        return {"id": e.id, "source": e.source, "target": e.target}

    def _pins_wire(self) -> list[dict[str, Any]]:
        return [
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
        ]

    def _meta_wire(self) -> dict[str, Any]:
        """The document-level "meta" fields (everything on the wire that
        isn't a node/edge/pin/view-position) - ADR-003 stage 3.4's setMeta
        patch op carries this SAME whole blob (not a partial diff), matching
        the "whole item, not field-level" granularity every other op already
        uses (see the ADR's own Decision text)."""
        return {
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
            "totalSessionTokens": self.total_session_tokens,
            # ADR-010 stage 10.2: the undo/redo affordance's own state. Rides
            # the scene meta blob rather than a separate topic because it
            # changes on exactly the events the scene does - every mutation,
            # every undo/redo, every load - so a separate topic would need a
            # publish bolted onto all ~80 mutating intents to stay in sync,
            # and would go stale the first time one was missed.
            # The LABELS travel, not just the booleans: the button says
            # "Undo Delete", and only the backend knows what the top of the
            # stack actually is.
            "canUndo": self.can_undo(),
            "canRedo": self.can_redo(),
            "undoLabel": self.undo_label(),
            "redoLabel": self.redo_label(),
        }

    def scene_payload(self) -> dict[str, Any]:
        return {
            "nodes": [self._node_wire(n) for n in self.nodes.values()],
            "edges": [self._edge_wire(e) for e in self.edges.values()],
            "pins": self._pins_wire(),
            **self._meta_wire(),
            # R6.3: canvas view state - see these fields' own comments on
            # SceneDocument.
            **self._view_wire(),
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
    """R6.3: scene_payload()'s wire-side transform for ChatState's own
    content_parts (backend/domain/node_states.py) - a pure mapping
    function, NOT a SceneDocument method (same posture as
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
