"""GroupOps - frame/container geometry for SceneDocument (ADR-002 stage
2.2, slice 3).

A MIXIN, not a standalone class: every method operates on the composing
dataclass's own state (self.nodes/self.edges) - it is composed exactly
once, by domain/graph.py's `class SceneDocument(BranchOps, GroupOps)`.
_recompute_group_bounds/_detach_node_from_membership are also called
UPWARD from core methods (move_node/move_nodes/remove_nodes/
set_chat_collapsed) and laterally from BranchOps.delete_chat_node -
the documented core->groups inversion that made mixin composition the
behavior-identical split (a strictly layered core would have needed a
post-mutation hook, a real behavior change).

Method bodies are relocated VERBATIM from domain/graph.py (themselves
relocated verbatim from backend/canvas.py in slice 2); only the class
wrapper is new.
"""

from __future__ import annotations

from backend.domain.model import (
    GROUP_COLLAPSED_HEIGHT,
    GROUP_COLLAPSED_WIDTH,
    GROUP_INELIGIBLE_FRAME_MEMBER_KINDS,
    GROUP_MEMBER_DEFAULT_HEIGHT,
    GROUP_MEMBER_DEFAULT_WIDTH,
    GROUP_PADDING,
    GROUP_PADDING_TOP,
    SceneError,
    SceneNode,
)


class GroupOps:

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
