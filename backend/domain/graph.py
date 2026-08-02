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

render_chart_png (matplotlib, via root module graphlink_chart_rendering)
is imported here deliberately: it is a pure (chart_type, data, w, h,
dpi_scale) -> PNG-bytes function with no I/O and no backend
infrastructure, and "a chart node owns a versioned rendered asset"
(chart_asset_id/chart_asset_version/image_assets) genuinely is a document
invariant - the same category of root-module dependency as
NavigationPinStore/GridViewSettings, which this class has always carried
as fields. Keeping it module-level also preserves today's import-order
side effects (matplotlib backend selection at import time).

backend/canvas.py imports SceneDocument back, so every existing
`from backend.canvas import SceneDocument` consumer keeps working
unchanged. NOTE for future test authors: names bound HERE resolve in THIS
module's namespace - patching `backend.canvas.render_chart_png`-style
would silently no-op; patch `backend.domain.graph.<name>` instead.
"""

from __future__ import annotations

import itertools
import math
import uuid
from dataclasses import dataclass, field
from typing import Any

from graphlink_chart_data import SUPPORTED_CHART_TYPES
from graphlink_chart_rendering import render_chart_png
from graphlink_grid_view_settings import (
    GRID_SIZE_PRESETS,
    GRID_STYLE_PRESETS,
    GridViewSettings,
)
from graphlink_navigation_pins import NavigationPinStore
from graphlink_token_estimator import TokenEstimator

from backend.domain.content_codec import _content_codec
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


def _estimate_tokens(text: str) -> int:
    """Verbatim equivalent of backend.token_counter.estimate_tokens - see
    this module's docstring for why the domain layer cannot import that
    module directly."""
    return TokenEstimator().count_tokens(text)


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
        self.final_deliverable_node_id = None
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
        content_parts: list[dict[str, Any]] | None = None,
    ) -> SceneNode:
        """The Qt-free ChatScene.add_chat_node equivalent: a real message-
        bubble node, optionally connected to a parent (the branch it
        continues). Mirrors add_node's id/dict bookkeeping; the only new
        behavior is the parent-edge, ported from the legacy scene's own
        ConnectionItem creation.

        R8a: content_parts is the real multimodal attachment payload
        (image_bytes/audio_file parts) - the data-model capability
        SceneNode.content_parts has carried since R6.3, finally populated by
        a real caller. Optional and additive: every existing caller keeps
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
            is_user=bool(is_user),
            content_parts=content_parts,
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
        node.pycoder_approved_fingerprint = None
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
        node.pycoder_approved_fingerprint = None
        node.pycoder_error = str(message)
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
        node.code_sandbox_approved_fingerprint = None
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
        node.code_sandbox_approved_fingerprint = None
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

    def mark_branch_comparison_note(self, node_id: str, source_node_ids: list[str]) -> None:
        """ADR-002 Workstream 1 ("Compare Branches"): stamps an already-created
        note as the output of the Compare Branches agent and records which
        branches it compared - called once, immediately after add_note +
        set_note_content, mirroring set_group_color's own "extra setter call
        right after creation" shape (see the WS intent wrapper in
        register_canvas)."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "note":
            raise SceneError(f"node is not a note node: {node_id}")
        node.is_branch_comparison = True
        node.item_ids = list(source_node_ids)

    def mark_branch_synthesis(
        self,
        node_id: str,
        source_node_ids: list[str],
        instructions: str,
        provider: str | None,
        model: str | None,
    ) -> None:
        """ADR-002 Workstream 1 ("Synthesize Branches"): stamps an
        already-created CHAT node as the output of the Synthesize Branches
        agent - mirrors mark_branch_comparison_note's own "extra setter call
        right after creation" shape, adapted for a chat-kind result instead
        of a note-kind one (see SceneNode.is_branch_synthesis's own comment
        for why this is a distinct method/flag rather than reusing Compare
        Branches')."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "chat":
            raise SceneError(f"node is not a chat node: {node_id}")
        node.is_branch_synthesis = True
        node.item_ids = list(source_node_ids)
        node.synthesis_instructions = str(instructions)
        node.provider = provider
        node.model = model

    #: ADR-002 Workstream 1 ("Branch status and lifecycle"): the exactly-4
    #: legal values for SceneNode.branch_status - shared by the setter's
    #: validation and session_load.py's own defensive downgrade-to-"active"
    #: read-back, so the one legal set is never duplicated out of sync.
    BRANCH_STATUS_VALUES = frozenset({"active", "accepted", "rejected", "superseded"})

    def set_branch_status(self, node_id: str, status: str) -> None:
        """ADR-002 Workstream 1 ("Branch status and lifecycle"): marks a
        single chat node's own branch_status - deliberately no "has
        siblings" / "is a fork root" requirement (any chat node may be
        marked, mirroring mark_branch_comparison_note's own kind-only
        validation), and deliberately no side effect on any OTHER node -
        marking one branch Accepted does not auto-reject its siblings, the
        first cross-node side-effecting setter would have been a new kind
        of mutation nothing else in this file does, and Synthesize Branches
        already established that 2+ branches can be simultaneously
        legitimate (its own item_ids records multiple sources at once) -
        forcing exclusivity here would fight that existing workflow."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "chat":
            raise SceneError(f"node is not a chat node: {node_id}")
        status = str(status)
        if status not in self.BRANCH_STATUS_VALUES:
            raise SceneError(f"invalid branch status: {status}")
        node.branch_status = status

    def set_final_deliverable(self, node_id: str, is_final: bool) -> None:
        """ADR-002 Workstream 1 ("Branch status and lifecycle"): sets or
        clears final_deliverable_node_id - EXCLUSIVE by construction (the
        single-pointer shape means marking a new node silently supersedes
        whichever one held it before; no separate "clear the old one" step
        needed, unlike a per-node flag would require). Orthogonal to
        branch_status on purpose - no validation ties them together (a
        "rejected" node CAN technically be marked Final Deliverable; this
        is not blocked, though not a realistic path either)."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "chat":
            raise SceneError(f"node is not a chat node: {node_id}")
        if is_final:
            self.final_deliverable_node_id = node_id
        elif self.final_deliverable_node_id == node_id:
            self.final_deliverable_node_id = None

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

    @staticmethod
    def _union_rect(
        a: tuple[float, float, float, float], b: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        """The smallest (x, y, width, height) rect that fully contains both
        inputs - legacy's own QRectF.united(), ported. The single primitive
        _recompute_group_bounds uses to guarantee a frame's manual size
        and/or manually-dragged position never clips a member: whichever
        direction the live content has drifted, the result grows to cover
        it, never shrinks below either input."""
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        left = min(ax, bx)
        top = min(ay, by)
        right = max(ax + aw, bx + bw)
        bottom = max(ay + ah, by + bh)
        return left, top, right - left, bottom - top

    def _recompute_group_bounds(self, node_id: str) -> None:
        """The core "legacy never clips, always auto-grows to enclose
        members" recompute - plain server-side math, NOT a React Flow
        extent/parentId feature. Silent no-op for an unknown id or a
        non-frame/container kind (defensive: every call site below already
        only ever calls this with a live frame/container id, but a caller
        that races a delete must never crash here).

        Priority order:
        1. Collapsed: skip the bbox computation entirely, snap to the fixed
           GROUP_COLLAPSED_WIDTH/HEIGHT pill size. x/y are left untouched -
           a collapsed pill stays wherever it was expanded from.
        2. Frame with a manual size override and/or a manually-dragged
           position (group_manual_width/height and/or group_manual_x/y
           set): build a rect from whichever of those four are set (falling
           back to the frame's current group_width/height for an unset
           size, or the live bbox-of-members' own center for an unset
           position), then UNION that rect with the live bbox-of-members -
           never just substitute it. This is what makes both a manual
           resize AND an independent drag stick (survive the very next
           member move) without ever letting a member visually escape the
           frame: if the live content has grown past the manual rect on any
           edge, the union grows to re-enclose it instead of clipping or
           silently reverting to bbox-centering.
        3. Otherwise (auto-fit - every container, and every frame with
           nothing manual set): x/y/width/height come straight from the
           padded bbox-of-members.
        """
        node = self.nodes.get(node_id)
        if node is None or node.kind not in ("frame", "container"):
            return
        if node.is_collapsed:
            node.group_width = GROUP_COLLAPSED_WIDTH
            node.group_height = GROUP_COLLAPSED_HEIGHT
            return
        bx, by, bw, bh = self._bbox_of_members(node.item_ids)
        has_manual = node.kind == "frame" and (
            node.group_manual_width is not None
            or node.group_manual_height is not None
            or node.group_manual_x is not None
            or node.group_manual_y is not None
        )
        if has_manual:
            width = node.group_manual_width if node.group_manual_width is not None else (node.group_width or bw)
            height = node.group_manual_height if node.group_manual_height is not None else (node.group_height or bh)
            if node.group_manual_x is not None and node.group_manual_y is not None:
                anchor_x, anchor_y = node.group_manual_x, node.group_manual_y
            else:
                anchor_x = bx + bw / 2.0 - width / 2.0
                anchor_y = by + bh / 2.0 - height / 2.0
            node.x, node.y, node.group_width, node.group_height = self._union_rect(
                (anchor_x, anchor_y, width, height), (bx, by, bw, bh)
            )
            return
        node.x, node.y, node.group_width, node.group_height = bx, by, bw, bh

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
        id exists AND is an eligible leaf-content kind BEFORE any mutation
        (fail fast, no partial detach) - GROUP_INELIGIBLE_FRAME_MEMBER_KINDS
        rejects a note or another frame/container, matching legacy's own
        createFrame selection filter (frames never nest, and never absorb a
        note - a note member would also be silently dropped from this
        frame's own membership on save/reload, since frame_source_map has
        no slot for one; see session_save.py). Then detaches each surviving
        candidate from any frame it was already a member of (see
        _detach_from_existing_group). is_locked defaults True (the legacy
        frame default - locked). Initial x/y/width/height come from the
        padded bbox-of-members, computed immediately via
        _recompute_group_bounds right after construction."""
        ids = list(item_ids)
        for member_id in ids:
            member = self.nodes.get(member_id)
            if member is None:
                raise SceneError(f"unknown member node: {member_id}")
            if member.kind in GROUP_INELIGIBLE_FRAME_MEMBER_KINDS:
                raise SceneError(
                    f"node {member_id} (kind={member.kind!r}) cannot be a frame member - "
                    f"frames only group leaf content nodes, never a note or another frame/container"
                )
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
        """Frame kind only. Clears BOTH the manual size override (set by
        resize_frame) AND the manual position anchor (set by move_node
        whenever this frame was dragged directly - see that method's own
        comment) back to None, a full reset to pure auto-fit, then forces
        an immediate bbox recompute. The size half is the exact inverse of
        resize_frame; the position half is what makes this button also undo
        an independent unlocked-frame drag, not just a resize."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "frame":
            raise SceneError(f"node is not a frame node: {node_id}")
        node.group_manual_width = None
        node.group_manual_height = None
        node.group_manual_x = None
        node.group_manual_y = None
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

    def _chat_subtree_ids(self, root_id: str) -> list[str]:
        """ADR-002 Workstream 1 ("Branch status and lifecycle"): the
        DOWNWARD counterpart to _branch_parent_edge's upward walk above -
        every chat-kind node's id in root_id's own subtree (root_id
        itself, plus every descendant reachable through chat-kind edges),
        via BFS. No such downward/forward walk existed anywhere in this
        file before this feature - every other edge scan here is either
        this file's own upward parent-walk pattern or an explicitly
        one-hop-only scan (delete_chat_node's direct-children reparent,
        send_message's sibling-fan-out count) - so this is new, not a
        rename of something existing. Chat-kind only: a code/document/
        thinking/image node hanging directly off a chat node in this
        subtree is NOT included - collapsing a branch does not cascade
        into its content children, matching how a single chat node's own
        collapse already never cascades into ITS children either."""
        result = [root_id]
        visited = {root_id}
        frontier = [root_id]
        while frontier:
            next_frontier = []
            for nid in frontier:
                for edge in self.edges.values():
                    if edge.source != nid:
                        continue
                    target = self.nodes.get(edge.target)
                    if target is None or target.kind != "chat" or target.id in visited:
                        continue
                    visited.add(target.id)
                    result.append(target.id)
                    next_frontier.append(target.id)
            frontier = next_frontier
        return result

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

        # ADR-002 Workstream 1 ("Branch status and lifecycle") - found by
        # adversarial review: unlike last_chat_node_id above, this is
        # cleared entirely rather than re-pointed to the parent. The parent
        # was never itself marked Final Deliverable, so silently promoting
        # it would misattribute a status the user never gave it; requiring
        # a fresh, explicit re-mark is the safe behavior.
        if self.final_deliverable_node_id == node_id:
            self.final_deliverable_node_id = None

        del self.nodes[node_id]
        self._detach_node_from_membership(node_id)

    def send_message(
        self,
        text: str,
        content_parts: list[dict[str, Any]] | None = None,
        branch_from_node_id: str | None = None,
    ) -> SceneNode:
        """The Composer's real Send action (R3.3): create a real user
        ChatNode continuing the current branch (last_chat_node_id), or
        start a fresh root if none exists yet. Positioning is a simple
        deterministic stack, not the legacy find_branch_position packing
        algorithm - real auto-layout is a later refinement; "Organize
        Nodes" already exists as a fallback.

        R8a: content_parts carries real attachments (image/audio) staged in
        the composer - optional, additive, threaded straight to
        add_chat_node.

        ADR-002 Workstream 1 ("Branch from here"): branch_from_node_id, when
        given and still a real node, OVERRIDES last_chat_node_id for this
        one send - the actual fork primitive. Before this, last_chat_node_id
        was the ONLY way to pick a parent, so a second real branch (two
        children of one parent) was reachable only by manual edge
        manipulation, never through the UI - see that field's own comment
        ("until real node selection exists"). A bad/stale id (deleted node,
        typo) falls through to the ordinary last_chat_node_id path rather
        than raising, same defensive posture chat_branch_history's walk
        already uses for an unknown id.

        When branching onto a parent that already has one or more chat-kind
        children (a genuine divergence, not a fresh continuation), the new
        sibling fans out horizontally by BRANCH_HORIZONTAL_SPACING per
        existing child instead of landing on the exact same (x, y) as an
        existing branch - which would render as one node silently hiding
        another.

        last_chat_node_id is updated to the new node afterward exactly as
        an ordinary send would be, override or not - so the branch just
        created becomes the active one for the NEXT (non-overridden) send,
        the same continue-from-here behavior as always."""
        if branch_from_node_id is not None and branch_from_node_id in self.nodes:
            parent_id: str | None = branch_from_node_id
            parent = self.nodes[parent_id]
            sibling_count = sum(
                1
                for e in self.edges.values()
                if e.source == parent_id and (target := self.nodes.get(e.target)) is not None and target.kind == "chat"
            )
            x, y = parent.x + sibling_count * BRANCH_HORIZONTAL_SPACING, parent.y + MESSAGE_VERTICAL_SPACING
        else:
            parent_id = self.last_chat_node_id
            if parent_id is not None and parent_id in self.nodes:
                parent = self.nodes[parent_id]
                x, y = parent.x, parent.y + MESSAGE_VERTICAL_SPACING
            else:
                parent_id = None
                chat_node_count = sum(1 for n in self.nodes.values() if n.kind == "chat")
                x, y = 0.0, chat_node_count * MESSAGE_VERTICAL_SPACING
        node = self.add_chat_node(x, y, text, True, parent_id=parent_id, content_parts=content_parts)
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
            # R8a: content_parts, when populated, IS the complete message
            # content (its own text part plus any image_bytes/audio_file
            # parts) - not an addendum to node.content. A node with no
            # attachments has content_parts=None and this is byte-identical
            # to before: a plain string, exactly as every other consumer of
            # this history already expects.
            history.append({
                "role": "user" if node.is_user else "assistant",
                "content": node.content_parts if node.content_parts else node.content,
            })
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

    def collapse_branch(self, node_id: str, collapsed: bool) -> None:
        """ADR-002 Workstream 1 ("Branch status and lifecycle"): "Collapse
        a rejected branch without deleting it" - reuses the existing,
        already-fully-wired is_collapsed field verbatim (wire sync, save,
        load, and ChatNodeView.tsx's collapsed-pill rendering all already
        work for it), applied across node_id's own chat-kind subtree via
        _chat_subtree_ids instead of just node_id itself - the one
        genuinely new piece this needs. Deliberately NOT automatic when a
        branch is marked "rejected" via set_branch_status: status (a
        semantic label) and collapse (a view state) are kept decoupled on
        purpose, so a branch can be Rejected-but-still-expanded during
        review, and marking status never has a side effect on any other
        node's state (matching set_branch_status's own "no implicit side
        effects" posture). Bulk-sets uniformly across the whole subtree,
        so a node that had previously been individually expanded/collapsed
        differently loses that distinction the first time this runs - an
        accepted, stated tradeoff, not solved here."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "chat":
            raise SceneError(f"node is not a chat node: {node_id}")
        for nid in self._chat_subtree_ids(node_id):
            self.nodes[nid].is_collapsed = bool(collapsed)

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
            node.group_manual_x, node.group_manual_y = node.x, node.y
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
                node.group_manual_x, node.group_manual_y = node.x, node.y
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
                    # ADR-002 Workstream 1 ("Synthesize Branches") - see
                    # SceneNode.provider/model/is_branch_synthesis/
                    # synthesis_instructions's own comments.
                    "provider": n.provider,
                    "model": n.model,
                    "isBranchSynthesis": n.is_branch_synthesis,
                    "synthesisInstructions": n.synthesis_instructions,
                    # ADR-002 Workstream 1 ("Branch status and lifecycle") -
                    # see SceneNode.branch_status/SceneDocument.
                    # final_deliverable_node_id's own comments.
                    "branchStatus": n.branch_status,
                    "isFinalDeliverable": n.id == self.final_deliverable_node_id,
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
                    # ADR-002 Workstream 1 ("Compare Branches") - see
                    # SceneNode.is_branch_comparison's own comment.
                    "isBranchComparison": n.is_branch_comparison,
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
