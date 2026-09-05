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
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

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
from backend.domain.layout import LayoutOps
from backend.domain.model import (
    CODE_TITLE_PREVIEW_LENGTH,
    DRAG_FACTOR_MAX,
    DRAG_FACTOR_MIN,
    FONT_FAMILIES,
    FONT_SIZE_MAX,
    FONT_SIZE_MIN,
    GRID_COLOR_PRESETS,
    SceneEdge,
    SceneError,
    SceneNode,
)
from backend.domain.node_access import is_node_of, require_node
from backend.domain.node_states import (
    ArtifactState,
    ChartState,
    ChatState,
    CodeReviewState,
    CodeSandboxState,
    CodeState,
    ContainerState,
    DocumentState,
    FrameState,
    GitlinkState,
    HarnessState,
    HtmlState,
    ImageState,
    NodeState,
    NoteState,
    PlanState,
    WebResearchState,
)
from backend.domain.nodes_agent_runs import AgentRunOps
from backend.domain.nodes_code_review import CodeReviewOps
from backend.domain.nodes_content import ContentOps
from backend.domain.nodes_conversational import ConversationalOps
from backend.domain.nodes_gitlink import GitlinkOps
from backend.domain.nodes_planning import PlanningOps
from backend.domain.nodes_visual import VisualOps


def _estimate_tokens(text: str) -> int:
    """Verbatim equivalent of backend.token_counter.estimate_tokens - see
    this module's docstring for why the domain layer cannot import that
    module directly."""
    return TokenEstimator().count_tokens(text)


# -- Review Lens nested wire rows ------------------------------------------
#
# These five builders exist because the Review Lens node stores its nested
# rows as the review engine's own snake_case dicts (graphlink_plugins/
# review_lens/review_engine.py) while the wire contract - and the generated
# client validator built from it - is camelCase, like every other nested
# row on SceneNodeRow. scene_payload used to forward them with a bare
# `dict(row)`, which shipped `patch_truncated`/`previous_path`/
# `group_title` where CodeReviewFileRow and CodeReviewWalkthroughGroupRow
# declare `patchTruncated`/`previousPath`/`groupTitle`.
#
# That was not a cosmetic mismatch. validateSceneState treats a missing
# required field as a hard error, and web_ui/src/lib/api-contract/
# bindTopic.ts DROPS a snapshot that fails validation, so from the first
# successful PR fetch onward every scene snapshot for the whole session
# was rejected client-side - the canvas froze for every node, not just
# this one. Building each row explicitly (the `toolCalls` precedent
# below) fixes the casing AND guarantees each row carries exactly the
# contract's fields with the contract's types, so a row that reached the
# state from an old save file cannot put an unexpected key on the wire.
#
# The domain keeps snake_case on purpose: it is what the engine emits and
# what session_save.py has already written to every existing save file.
# The conversion belongs at the wire builder, the same place
# codeReviewScores is coerced to dict[str, str].


def _code_review_file_wire(row: dict[str, Any]) -> dict[str, Any]:
    wire: dict[str, Any] = {
        "path": str(row.get("path", "")),
        "status": str(row.get("status", "modified")),
        "additions": _non_negative_wire_int(row.get("additions")),
        "deletions": _non_negative_wire_int(row.get("deletions")),
        # `patch` is DELIBERATELY not forwarded - see the caller's comment.
        "patch": "",
        "patchTruncated": bool(row.get("patch_truncated", False)),
    }
    # Genuinely absent (not "") for every non-rename, which is why
    # CodeReviewFileRow.previousPath is the one Optional field on the row.
    previous = str(row.get("previous_path", "") or "")
    if previous:
        wire["previousPath"] = previous
    return wire


def _code_review_walkthrough_wire(row: dict[str, Any]) -> dict[str, Any]:
    raw_paths = row.get("paths")
    return {
        "groupTitle": str(row.get("group_title", "")),
        "paths": [str(path) for path in raw_paths] if isinstance(raw_paths, list) else [],
        "explanation": str(row.get("explanation", "")),
    }


def _code_review_finding_wire(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id", "")),
        "severity": str(row.get("severity", "")),
        "tier": str(row.get("tier", "")),
        "category": str(row.get("category", "")),
        "path": str(row.get("path", "")),
        "line": _non_negative_wire_int(row.get("line")),
        "title": str(row.get("title", "")),
        "evidence": str(row.get("evidence", "")),
        "impact": str(row.get("impact", "")),
        "recommendation": str(row.get("recommendation", "")),
    }


def _code_review_error_wire(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id", "")),
        "severity": str(row.get("severity", "")),
        "tier": str(row.get("tier", "")),
        "kind": str(row.get("kind", "")),
        "path": str(row.get("path", "")),
        "line": _non_negative_wire_int(row.get("line")),
        "title": str(row.get("title", "")),
        "evidence": str(row.get("evidence", "")),
        "fix": str(row.get("fix", "")),
    }


def _code_review_qa_wire(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "question": str(row.get("question", "")),
        "answer": str(row.get("answer", "")),
    }


def _non_negative_wire_int(value: Any) -> int:
    """int for the wire, never raising. A row can reach the wire from a
    hand-edited save file as well as from the engine, and a ValueError
    here would fail the whole scene republish, not just one row."""
    try:
        return max(0, int(value))  # type: ignore[call-overload]
    except (TypeError, ValueError, OverflowError):
        return 0




@dataclass
class SceneDocument(
    BranchOps, GroupOps, LayoutOps, CommandOps,
    AgentRunOps, CodeReviewOps, ContentOps, ConversationalOps, GitlinkOps,
    PlanningOps, VisualOps,
):
    """The canvas document for one session. Plain data + invariants; the
    R6 serializer will read/write exactly this shape."""

    nodes: dict[str, SceneNode] = field(default_factory=dict)
    edges: dict[str, SceneEdge] = field(default_factory=dict)
    # node id -> (width, height): each node's ACTUAL rendered footprint in
    # flow units, as last reported by the frontend (the reportNodeSizes
    # intent -> set_measured_node_sizes). Only ids the client has measured
    # appear here.
    #
    # This is the one piece of node geometry the backend cannot derive for
    # itself - a chat node's height is whatever its markdown laid out to,
    # which only the browser knows - and frame/container bounds math is the
    # sole consumer (see _member_footprint in domain/groups.py for the
    # measured -> intrinsic -> estimate fallback chain).
    #
    # Kept OFF SceneNode deliberately, in its own map rather than two more
    # node fields: this is an observation about rendering, not document
    # state. It is never persisted (session_save writes nodes, not this),
    # never reaches the client (scene_payload emits node fields, and the
    # client is where these came from), and never enters the undo stack -
    # all three fall out structurally from living here instead of on the
    # node, rather than depending on three separate places remembering to
    # skip a field. tests/test_node_state_migration.py's ADR-002 gate locks
    # SceneNode's own shape to its 14 core fields for closely related
    # reasons.
    measured_sizes: dict[str, tuple[float, float]] = field(default_factory=dict)
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
    # Canvas font (ChatScene's setFontFamily/-Size/-Color state, R2): family
    # and size default to the legacy scene's own construction-time values.
    # font_color's default is the EMPTY STRING, meaning "follow the theme":
    # the frontend only writes --gl-node-font-color when this is non-empty,
    # and the CSS falls back to var(--gl-surface-text-primary) otherwise
    # (styles.css). The legacy default was a literal "#F0F0F0" - white-ish
    # text chosen for the dark theme - which made every node body unreadable
    # the moment the light palette was active, because a stored hex cannot
    # adapt. Only an explicit user pick stores a hex now, and an explicit
    # pick applies as-is in both themes, which is what "I chose this color"
    # means.
    font_family: str = "Segoe UI"
    font_size_pt: int = 9
    font_color: str = ""
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
    # ADR-020 stage 20.2: which workspace the NEXT save of this document
    # should INSERT a brand-new graph row into, or None to fall back to the
    # Default workspace (see save_chat_atomically_row's own workspace_id
    # docstring in backend/chat_library.py for exactly how this is
    # consumed). Set by the newChat intent's own optional workspaceId
    # argument (backend/chat_library.py's new_chat closure, ALREADY
    # validated against a real, current workspace row by that closure before
    # it ever reaches here - this field trusts whatever it holds), left None
    # for a session that never called newChat with an explicit workspace
    # (every pre-20.2 caller, and the zero-arg newChat() every non-library
    # caller like commands.ts's palette command still uses). Deliberately
    # NOT consulted for an UPDATE of an EXISTING graph (current_chat_id
    # truthy) - resaving a chat never moves it to a different workspace, no
    # matter what this field holds; only a fresh INSERT reads it. Reset to
    # None by clear_for_load below, same "fresh session default" list
    # current_chat_id is already in - a freshly loaded OR freshly cleared
    # scene has no pending workspace assignment of its own until the next
    # newChat call sets one.
    current_workspace_id: int | None = None
    # ADR-014 stage 14.2: the live-wire half of the Plugin SDK's generic
    # persistence seam - keyed by a plugin's ALREADY-namespaced node kind
    # (f"{plugin_id}.{kind}", HostContext.register_node_kind's own
    # contract), value is that plugin's own `serialize(node) -> dict` hook,
    # verbatim (the SAME callable HostContext.register_node_kind's
    # `serialize` parameter stores on NodeKindSpec - see backend/plugin_sdk.
    # py's own docstring there for the full contract shared with
    # session_save.py's persisted-file use of the same hook).
    #
    # WHY A PLAIN dict[str, Callable] HERE, RATHER THAN THIS MODULE
    # IMPORTING backend.plugin_sdk.PluginRegistry AND CONSULTING IT
    # DIRECTLY: backend/plugin_sdk.py imports backend.canvas (for
    # SceneDocument's own type), and backend/canvas.py imports THIS module
    # back (see this module's own docstring) - a domain-layer import of
    # plugin_sdk would be a real circular import (ImportError on a
    # partially-initialized backend.canvas), not just a domain-purity
    # style violation tests/test_domain_purity.py happens not to enumerate
    # by name. A bare, duck-typed dict of callables carries zero import
    # coupling either direction.
    #
    # POPULATED BY: backend/plugins.py's register_plugins(), once per
    # session activation, from the (already-discovered) PluginRegistry -
    # NOT by this dataclass's own constructor (defaults empty), and NOT
    # reset by clear_for_load below (a plugin's live registration is a
    # session-lifecycle capability, exactly like grid/font settings below,
    # not per-loaded-chat-file data - reloading a DIFFERENT chat into the
    # same session must not un-register a still-active plugin).
    #
    # A lookup miss (the overwhelming majority of nodes: every built-in
    # kind, AND any plugin kind whose author never opted into
    # register_node_kind(..., serialize=...)) is not an error - see
    # _plugin_state_wire's own docstring.
    plugin_node_serializers: dict[str, Callable[["SceneNode"], dict[str, Any]]] = field(
        default_factory=dict, repr=False, compare=False,
    )
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
        self.current_workspace_id = None
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

    def adopt_pending_system_prompt(self, root_id: str) -> SceneEdge | None:
        """Connects an unattached system-prompt note to a new branch root.

        plugins/system_prompt/ can create its note with nothing selected, so
        that a prompt can be written BEFORE the conversation it governs -
        the natural order, and the one the old "select a node first" rule
        made impossible without a wasted send. Such a note starts with no
        edges, and AgentDispatcher._resolve_branch_system_prompt only ever
        looks for a note -> root edge, so the note would stay silently inert
        until something drew that edge. This is that something: the first
        parentless chat node - a new branch root - adopts it.

        Deliberately narrow. It fires only for a node created with no
        parent, only when exactly one unattached system-prompt note exists,
        and only while that root has no system prompt of its own; anything
        more ambiguous is left alone rather than guessed at. Returns the
        edge it created, or None when it did nothing."""
        root = self.nodes.get(root_id)
        if root is None:
            return None
        sources = {edge.source for edge in self.edges.values()}
        pending = [
            node
            for node in self.nodes.values()
            if node.kind == "note"
            and getattr(node.state, "is_system_prompt", False)
            and node.id not in sources
        ]
        if len(pending) != 1:
            return None
        already_attached = any(
            edge.target == root_id
            and (source := self.nodes.get(edge.source)) is not None
            and source.kind == "note"
            and getattr(source.state, "is_system_prompt", False)
            for edge in self.edges.values()
        )
        if already_attached:
            return None
        return self.connect(pending[0].id, root_id)

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
            # Cannot raise: the node is present and its kind was just
            # checked. Re-fetched under FrameState purely so the two
            # frame-only writes below are checkable.
            frame = require_node(self.nodes, node_id, "frame", FrameState)
            frame.state.group_manual_x, frame.state.group_manual_y = node.x, node.y
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
            if is_node_of(node, "frame", FrameState):
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
        self, kind: str, x: float, y: float, parent_id: str | None, *,
        title: str = "", content: str = "", state: NodeState | None = None,
    ) -> SceneNode:
        """Generic node-creation primitive for ADR-014's Plugin SDK
        (backend/plugin_sdk.py). Mirrors add_web_research_node's own body
        exactly - mint an id off the SAME self._counter every add_X_node
        method already uses, insert into self.nodes, connect to the
        parent. `kind` arrives here ALREADY namespaced as
        f"{plugin_id}.{local_kind}" (backend/plugin_sdk.py's HostContext.
        register_node_kind) - this method itself does no namespacing or
        validation of its own, same "trust the caller" contract as
        add_web_research_node/add_gitlink_node/etc.

        `parent_id=None` creates the node UNCONNECTED, at the given x/y.
        That is the one deliberate difference from its add_X_node siblings,
        and it is what a plugin registered with requires_parent=False needs:
        such a plugin is creatable on an empty canvas, where by definition
        there is no parent to attach to."""
        if parent_id is not None and parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        node = SceneNode(
            id=node_id, x=float(x), y=float(y),
            title=str(title) or kind, kind=str(kind), content=str(content), state=state,
        )
        self.nodes[node_id] = node
        if parent_id is not None:
            self.connect(parent_id, node_id)
        return node

    # -- edges -------------------------------------------------------------

    def connect(self, source: str, target: str) -> SceneEdge:
        self._validate_connect_endpoints(source, target)
        for edge in self.edges.values():
            if edge.source == source and edge.target == target:
                return edge  # idempotent, matching ChatScene's duplicate guard
        # REVIEW-FIX: refuse an edge that would close a cycle. self.edges is
        # written ONLY through this method and connect_unchecked below
        # (grep-confirmed - every add_*_node method and every live/tool/
        # intent call site goes through connect(); nothing ever assigns
        # self.edges directly), so this pair is the one true chokepoint for
        # the whole graph's shape. Before this check, two ordinary
        # connectNodes calls in opposite directions (a user dragging A->B
        # then B->A on the canvas, or the Builder's builderConnect tool
        # doing the same) silently created a 2-node cycle - and
        # get_branch_root/chat_branch_history (branches.py), which walk a
        # node's parent chain on nearly every chat/branch action, have no
        # cycle guard of their own and hung forever the moment one existed,
        # freezing the single-process backend for every session. Proven
        # empirically before this fix: doc.connect(a,b); doc.connect(b,a);
        # doc.get_branch_root(a) never returned.
        #
        # A cycle is never a legitimate state for a LIVE edit - branch
        # structure and note attachment are both meant to be a DAG - so
        # this is a real invariant, not an over-broad restriction: refuses
        # target -> ... -> source already existing (adding source -> target
        # would close it), regardless of which of this app's edge "kinds"
        # (branch parent-child, note attachment, a plain user-drawn
        # connection - all the same SceneEdge shape) the existing path is
        # made of. See connect_unchecked's own docstring for the one real
        # exception this needs (loading pre-unification saved data).
        if self._reaches(target, source):
            raise SceneError(f"cannot connect {source} -> {target}: would create a cycle")
        return self._insert_edge(source, target)

    def connect_unchecked(self, source: str, target: str) -> SceneEdge:
        """Everything connect() validates EXCEPT the cycle check - unknown
        nodes and a self-loop still raise, an exact duplicate is still
        idempotent. The one legitimate caller: backend/session_load.py's
        restore of the pre-stage-9.6 per-kind connection buckets (system
        prompt / group summary / the 12 generic ones), which is READING
        DATA an OLDER classification scheme produced - one where two
        connections between the same node pair in opposite directions
        (e.g. one saved under pycoder_connections, another under
        gitlink_connections) legitimately encoded two DIFFERENT semantic
        relationship kinds, not a mistake. Rejecting that on load would
        silently drop real historical data the user never asked to lose -
        exactly the kind of destructive migration this codebase's own
        WRITE-NEW/READ-BOTH discipline exists to avoid. The CURRENT flat
        `edges` format (stage 9.6 onward, session_load.py's own
        _restore_flat_edges) is NOT exempted - it is the single unified
        model with no such per-kind distinction, so a bidirectional pair
        surviving there could only be an artifact of this exact bug from
        before it was fixed, which self-healing by dropping (via the
        ordinary, cycle-checked connect()) is correct."""
        self._validate_connect_endpoints(source, target)
        for edge in self.edges.values():
            if edge.source == source and edge.target == target:
                return edge
        return self._insert_edge(source, target)

    def _validate_connect_endpoints(self, source: str, target: str) -> None:
        if source not in self.nodes or target not in self.nodes:
            raise SceneError(f"cannot connect unknown nodes: {source} -> {target}")
        if source == target:
            raise SceneError("cannot connect a node to itself")

    def _insert_edge(self, source: str, target: str) -> SceneEdge:
        edge_id = f"e{next(self._counter)}"
        edge = SceneEdge(id=edge_id, source=source, target=target)
        self.edges[edge_id] = edge
        return edge

    def _reaches(self, start: str, goal: str) -> bool:
        """True if `goal` is reachable from `start` by following existing
        edges forward (edge.source -> edge.target). Plain BFS with a
        visited set - the primitive connect() uses to refuse a
        cycle-closing edge before it is ever created. See connect()'s own
        comment for why a cycle here is a real hazard, not just an
        oddity."""
        if start == goal:
            return True
        visited = {start}
        frontier = [start]
        while frontier:
            next_frontier = []
            for node_id in frontier:
                for edge in self.edges.values():
                    if edge.source != node_id or edge.target in visited:
                        continue
                    if edge.target == goal:
                        return True
                    visited.add(edge.target)
                    next_frontier.append(edge.target)
            frontier = next_frontier
        return False

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
            # "" is a valid value: reset to theme-following (see the field's
            # own comment). Anything else is stored verbatim as the user's
            # explicit choice.
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
        """Tidy layout: size-aware layered tree layout - see LayoutOps
        .organize_layout (backend/domain/layout.py) for the algorithm."""
        self.organize_layout()

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
            "researchRetainToKnowledge": (
                n.state.research_retain_to_knowledge if isinstance(n.state, WebResearchState) else False
            ),
            "artifactContent": n.state.artifact_content if isinstance(n.state, ArtifactState) else "",
            "artifactError": n.state.artifact_error if isinstance(n.state, ArtifactState) else "",
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
            "codeReviewPrUrl": n.state.code_review_pr_url if isinstance(n.state, CodeReviewState) else "",
            "codeReviewRepo": n.state.code_review_repo if isinstance(n.state, CodeReviewState) else "",
            "codeReviewPrNumber": (
                n.state.code_review_pr_number if isinstance(n.state, CodeReviewState) else 0
            ),
            "codeReviewPrTitle": (
                n.state.code_review_pr_title if isinstance(n.state, CodeReviewState) else ""
            ),
            "codeReviewPrState": (
                n.state.code_review_pr_state if isinstance(n.state, CodeReviewState) else ""
            ),
            "codeReviewPrHtmlUrl": (
                n.state.code_review_pr_html_url if isinstance(n.state, CodeReviewState) else ""
            ),
            "codeReviewBaseRef": (
                n.state.code_review_base_ref if isinstance(n.state, CodeReviewState) else ""
            ),
            "codeReviewHeadRef": (
                n.state.code_review_head_ref if isinstance(n.state, CodeReviewState) else ""
            ),
            "codeReviewAdditions": (
                n.state.code_review_additions if isinstance(n.state, CodeReviewState) else 0
            ),
            "codeReviewDeletions": (
                n.state.code_review_deletions if isinstance(n.state, CodeReviewState) else 0
            ),
            "codeReviewChangedFiles": (
                n.state.code_review_changed_files if isinstance(n.state, CodeReviewState) else 0
            ),
            # Rebuilt row by row rather than forwarded as `dict(f)` - see
            # _code_review_file_wire's own comment for the casing bug that
            # cost every scene snapshot after a PR fetch.
            #
            # `patch` rides as "" on purpose. The per-file patches are
            # capped at MAX_FILE_PATCH_CHARS (6000) each and MAX_PR_FILES
            # (100) of them, so forwarding them put up to ~600KB of diff
            # text on EVERY scene republish - roughly ten times the
            # 60KB codeReviewDiffText that was excluded from this payload
            # for exactly that reason (see CodeReviewState's own comment).
            # No frontend code reads it: CodeReviewNodeView renders the
            # unified diff it lazily fetches via fetchCodeReviewDiffText,
            # never these per-file patches. The field stays on the row
            # because the contract declares it required; the engine still
            # reads the real patches from node.state, which is where the
            # fallback pre-screen scans them.
            "codeReviewFiles": (
                [_code_review_file_wire(f) for f in n.state.code_review_files]
                if isinstance(n.state, CodeReviewState)
                else []
            ),
            "codeReviewFilesTruncated": (
                n.state.code_review_files_truncated if isinstance(n.state, CodeReviewState) else False
            ),
            # codeReviewDiffText is DELIBERATELY OMITTED - see
            # CodeReviewState's own comment (the gitlinkContextXml
            # precedent). Served on demand via fetchCodeReviewDiffText.
            "codeReviewDiffTruncated": (
                n.state.code_review_diff_truncated if isinstance(n.state, CodeReviewState) else False
            ),
            "codeReviewDiffChars": (
                n.state.code_review_diff_chars if isinstance(n.state, CodeReviewState) else 0
            ),
            # The lazy-diff cache key (the R5.3 post-review FIX 6
            # precedent): bumped by every successful fetch, so the
            # frontend never serves a previous fetch's text for this one.
            "codeReviewDiffVersion": (
                n.state.code_review_diff_version if isinstance(n.state, CodeReviewState) else 0
            ),
            "codeReviewWalkthrough": (
                [_code_review_walkthrough_wire(g) for g in n.state.code_review_walkthrough]
                if isinstance(n.state, CodeReviewState)
                else []
            ),
            "codeReviewFindings": (
                [_code_review_finding_wire(f) for f in n.state.code_review_findings]
                if isinstance(n.state, CodeReviewState)
                else []
            ),
            "codeReviewErrors": (
                [_code_review_error_wire(e) for e in n.state.code_review_errors]
                if isinstance(n.state, CodeReviewState)
                else []
            ),
            "codeReviewDismissedIds": (
                list(n.state.code_review_dismissed_ids)
                if isinstance(n.state, CodeReviewState)
                else []
            ),
            "codeReviewTitle": (
                n.state.code_review_title if isinstance(n.state, CodeReviewState) else ""
            ),
            "codeReviewOverview": (
                n.state.code_review_overview if isinstance(n.state, CodeReviewState) else ""
            ),
            "codeReviewConfidence": (
                n.state.code_review_confidence if isinstance(n.state, CodeReviewState) else ""
            ),
            # Scores ride the wire as dict[str, str] - coerced here, the
            # store_gitlink_context str-coercion precedent for
            # gitlinkContextStats (contracts only admit string-valued
            # dicts on SceneNodeRow).
            "codeReviewScores": (
                {str(k): str(v) for k, v in n.state.code_review_scores.items()}
                if isinstance(n.state, CodeReviewState)
                else {}
            ),
            "codeReviewQualityScore": (
                n.state.code_review_quality_score if isinstance(n.state, CodeReviewState) else 0
            ),
            "codeReviewVerdict": (
                n.state.code_review_verdict if isinstance(n.state, CodeReviewState) else "none"
            ),
            "codeReviewRisk": n.state.code_review_risk if isinstance(n.state, CodeReviewState) else "",
            "codeReviewQualitySummary": (
                n.state.code_review_quality_summary if isinstance(n.state, CodeReviewState) else ""
            ),
            "codeReviewQa": (
                [_code_review_qa_wire(entry) for entry in n.state.code_review_qa]
                if isinstance(n.state, CodeReviewState)
                else []
            ),
            "codeReviewState": (
                n.state.code_review_state if isinstance(n.state, CodeReviewState) else "draft"
            ),
            "codeReviewError": n.state.code_review_error if isinstance(n.state, CodeReviewState) else "",
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
            "chartWidth": n.state.chart_width if isinstance(n.state, ChartState) else 480.0,
            "chartHeight": n.state.chart_height if isinstance(n.state, ChartState) else 340.0,
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
            # ADR-008 stage 8.7: the run's own activity log - see PlanState's
            # docstring for why this is untouched by undo.
            "builderActivity": (
                [
                    {
                        "tool": a["tool"], "summary": a["summary"],
                        "outcome": a["outcome"], "stepId": a["stepId"],
                        "elapsedMs": a["elapsedMs"],
                    }
                    for a in n.state.builder_activity
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
            # PLAN-2026-08-24 H1: harness node (the workspace agent).
            # Conversation history deliberately does NOT cross the wire -
            # the transcript lives in the workspace; only the render
            # surface does (see HarnessState's own docstring).
            "harnessGoal": n.state.harness_goal if isinstance(n.state, HarnessState) else "",
            "harnessReply": n.state.harness_reply if isinstance(n.state, HarnessState) else "",
            "harnessStatus": n.state.harness_status if isinstance(n.state, HarnessState) else "",
            "harnessStatusDetail": (
                n.state.harness_status_detail if isinstance(n.state, HarnessState) else ""
            ),
            "harnessRunId": n.state.harness_run_id if isinstance(n.state, HarnessState) else "",
            "harnessActivity": (
                [
                    {
                        "tool": a["tool"], "summary": a["summary"],
                        "outcome": a["outcome"], "elapsedMs": a["elapsedMs"],
                    }
                    for a in n.state.harness_activity
                ]
                if isinstance(n.state, HarnessState) else []
            ),
            "harnessAwaitingApproval": (
                n.state.harness_awaiting_approval if isinstance(n.state, HarnessState) else False
            ),
            "harnessApprovalToolName": (
                n.state.harness_approval_tool_name if isinstance(n.state, HarnessState) else ""
            ),
            "harnessApprovalSummary": (
                n.state.harness_approval_summary if isinstance(n.state, HarnessState) else ""
            ),
            "harnessApprovalSessionOffered": (
                n.state.harness_approval_session_offered
                if isinstance(n.state, HarnessState) else False
            ),
            "harnessPlan": (
                [{"text": s["text"], "status": s["status"]} for s in n.state.harness_plan]
                if isinstance(n.state, HarnessState) else []
            ),
            "harnessAwaitingQuestion": (
                n.state.harness_awaiting_question if isinstance(n.state, HarnessState) else False
            ),
            "harnessQuestion": (
                n.state.harness_question if isinstance(n.state, HarnessState) else ""
            ),
            "harnessContextTokens": (
                n.state.harness_context_tokens if isinstance(n.state, HarnessState) else 0
            ),
            "harnessMaxContextTokens": (
                n.state.harness_max_context_tokens if isinstance(n.state, HarnessState) else 0
            ),
            "harnessCompactions": (
                n.state.harness_compactions if isinstance(n.state, HarnessState) else 0
            ),
            "harnessWorkspacePath": (
                n.state.harness_workspace_path if isinstance(n.state, HarnessState) else ""
            ),
            "harnessWorkspaceActive": (
                n.state.harness_workspace_active if isinstance(n.state, HarnessState) else ""
            ),
            "harnessMaxTurns": n.state.harness_max_turns if isinstance(n.state, HarnessState) else 0,
            "harnessSpentTurns": (
                n.state.harness_spent_turns if isinstance(n.state, HarnessState) else 0
            ),
            "harnessSpentTokens": (
                n.state.harness_spent_tokens if isinstance(n.state, HarnessState) else 0
            ),
            # ADR-014 stage 14.2: the Plugin SDK's generic live-wire
            # fallback - see plugin_node_serializers' own field comment and
            # _plugin_state_wire's own docstring below.
            "pluginState": self._plugin_state_wire(n),
        }

    def _plugin_state_wire(self, n: SceneNode) -> dict[str, str]:
        """ADR-014 stage 14.2: the live-WS-wire analog of session_save.py's
        generic plugin persistence fallback. `plugin_node_serializers` is
        keyed by a plugin's already-namespaced kind string (never a
        built-in kind - no built-in kind string contains "."), so a lookup
        miss covers every non-plugin node AND any plugin node whose author
        never opted into HostContext.register_node_kind(...,
        serialize=...) - both are the ordinary case, not an error.

        Coerced to dict[str, str] (never a richer nested shape): mirrors
        contracts/graphlink_scene_payload.py's own ResearchResultRow.
        providerSnapshot precedent for "genuinely free-form data under a
        schema generator with a closed, dict[str, X]-only type set" - see
        that field's own comment. A plugin's serializer raising, or
        returning something that isn't a plain dict, degrades to {} rather
        than breaking the whole scene publish that every other node on the
        canvas rides along with."""
        serializer = self.plugin_node_serializers.get(n.kind)
        if serializer is None:
            return {}
        try:
            raw = serializer(n)
        except Exception:
            logging.warning("plugin node %s (%s): serialize hook raised", n.id, n.kind, exc_info=True)
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(k): str(v) for k, v in raw.items()}

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
