"""VisualOps - the SceneDocument methods for the chart and image node kinds.

A MIXIN, composed exactly once, by backend/domain/graph.py's
SceneDocument. Method bodies are relocated VERBATIM from graph.py;
only the class wrapper, its docstring and the imports are new, and the
methods are regrouped by kind rather than left in the order successive
increments happened to append them in.

See backend/domain/nodes_code_review.py's docstring for why the
per-kind method groups are being lifted out of SceneDocument at all.
"""

from __future__ import annotations

import uuid
from typing import Any

from graphlink_chart_data import SUPPORTED_CHART_TYPES

from backend.domain._composed import SceneDocumentParts
from backend.domain.model import (
    CHART_MAX_HEIGHT,
    CHART_MAX_WIDTH,
    CHART_MIN_HEIGHT,
    CHART_MIN_WIDTH,
    IMAGE_TITLE_PREVIEW_LENGTH,
    SceneError,
    SceneNode,
)
from backend.domain.node_access import require_node
from backend.domain.node_states import ChartState, ImageState


class VisualOps(SceneDocumentParts):
    """The two kinds whose payload is a picture: charts and images.

    A chart node holds a spec the client renders interactively and a size
    the user can drag; an image node holds a generated asset plus the reply
    bubble that delivered it. Both therefore carry geometry that the generic
    layout code must not touch, which is why their resize/aspect handling
    lives with them rather than in LayoutOps.
    """

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
        artifact/gitlink/code_sandbox above) for every NEW chart:
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
        node = require_node(self.nodes, node_id, "chart", ChartState)

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
        node = require_node(self.nodes, node_id, "chart", ChartState)
        node.state.chart_aspect_locked = not node.state.chart_aspect_locked

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
        if parent_id is not None and parent_id not in self.nodes:
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
        if parent_id is not None:
            self.connect(parent_id, node_id)
        return node

    def get_image_asset(self, asset_id: str) -> tuple[bytes, str] | None:
        """The read-side of image_assets - the same lookup backend/assets.py's
        GET /api/assets/{id} route calls to serve the raw bytes + mime type."""
        return self.image_assets.get(asset_id)

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
        scope decision. Positions via place_child (backend/domain/layout.py),
        the same collision-resolved placement send_message/regenerate_
        response's own new-child placement uses. last_chat_node_id is DELIBERATELY untouched
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
        ax, ay = self.place_child(parent_chat_node_id, "chat")
        chat_node = self.add_chat_node(
            ax, ay, f'Generated image for prompt: "{prompt}"', False, parent_id=parent_chat_node_id,
        )
        ix, iy = self.place_child(chat_node.id, "image")
        image_node = self.add_image_node(ix, iy, image_bytes, prompt, chat_node.id, mime_type=mime_type)
        return chat_node, image_node
