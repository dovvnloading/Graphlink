"""LayoutOps - node placement and scene layout math for SceneDocument.

The Qt canvas had find_branch_position packing and a size-aware organize;
neither survived the Qt removal, so every spawn site degenerated to a
fixed offset (parent.y + 160) that lands new nodes on top of their parent
- a chat node routinely renders 500+ px tall - and organize() was a
size-blind 260x180 grid. This module is the rebuilt placement engine:

- node_footprint(): one node's (width, height), best source first -
  the frontend's reported rendered size (measured_sizes), then the kind's
  own intrinsic geometry (chart/frame/container), then a per-kind
  fallback estimate. Same chain as groups.py's _member_footprint, but
  with realistic per-kind fallbacks (the flat 220x120 group default is
  far too small for placement: a chat node alone is 422 wide).
- find_free_position(): deterministic collision resolution - a proposed
  rect advances past whatever occupied rect it overlaps until clear.
- place_child()/place_root()/place_at_scene_right(): the three placement
  shapes every spawn site needs. All spawn coordinates flow through
  these; no caller does its own offset math any more.
- organize_layout(): the size-aware replacement for the placeholder grid
  - a layered tree layout (children below parents, subtrees packed side
  by side by real width), disconnected trees side by side, frames and
  containers re-wrapped around their members afterwards.

Domain-pure (imports only sibling domain modules), mixed into
SceneDocument exactly like BranchOps/GroupOps/CommandOps.
"""

from __future__ import annotations

from backend.domain.model import (
    GROUP_COLLAPSED_HEIGHT,
    GROUP_COLLAPSED_WIDTH,
    SceneNode,
)

# Clearance between neighbouring nodes. Horizontal is a little wider than
# vertical so sibling fans read as distinct columns; both are large enough
# that edge routing has room to breathe between cards.
NODE_GAP_X = 80.0
NODE_GAP_Y = 70.0
# organize(): gap between two disconnected trees' bounding boxes - wider
# than the in-tree gaps so separate conversations read as separate islands.
COMPONENT_GAP = 200.0

# Fallback footprints per kind, used only until the frontend reports the
# node's real rendered size (reportNodeSizes -> measured_sizes). Widths
# track the fixed CSS widths where one exists (.chat-node is 420px, etc.);
# heights are typical-render estimates - placement only needs them to be
# in the right ballpark, and the next report replaces them with truth.
KIND_FALLBACK_FOOTPRINTS: dict[str, tuple[float, float]] = {
    "chat": (422.0, 360.0),
    "conversation": (440.0, 420.0),
    "code": (480.0, 360.0),
    "thinking": (420.0, 260.0),
    "html": (480.0, 400.0),
    "image": (420.0, 420.0),
    "document": (380.0, 240.0),
    "note": (280.0, 200.0),
    "web_research": (440.0, 380.0),
    "artifact": (440.0, 380.0),
    "gitlink": (440.0, 380.0),
    "code_sandbox": (480.0, 420.0),
    "plan": (440.0, 420.0),
    "harness": (440.0, 420.0),
    "chart": (600.0, 440.0),
    "placeholder": (220.0, 120.0),
}
DEFAULT_FALLBACK_FOOTPRINT = (360.0, 240.0)

# find_free_position's loop bound - purely defensive; each step strictly
# advances past an obstacle edge, so a real scene converges in a handful.
_MAX_PLACEMENT_STEPS = 300


def _rects_clear(
    ax: float, ay: float, aw: float, ah: float,
    bx: float, by: float, bw: float, bh: float,
) -> bool:
    """True when the two rects are separated by at least the standard
    clearance on one axis - overlap *or* near-touching both count as a
    collision, so placement never produces visually-glued cards."""
    return (
        ax + aw + NODE_GAP_X <= bx
        or bx + bw + NODE_GAP_X <= ax
        or ay + ah + NODE_GAP_Y <= by
        or by + bh + NODE_GAP_Y <= ay
    )


class LayoutOps:
    """Placement/layout mixin for SceneDocument (same pattern as GroupOps).
    Reads self.nodes / self.edges / self.measured_sizes."""

    # -- footprints --------------------------------------------------------

    def node_footprint(self, node: SceneNode) -> tuple[float, float]:
        """One node's (width, height) for placement purposes: measured ->
        intrinsic -> per-kind fallback. Non-positive values from any source
        fall through, same posture as groups.py's _member_footprint."""
        if node.kind in ("frame", "container") and node.is_collapsed:
            # The pill size wins over a measured entry: the measurement may
            # be the EXPANDED box from before the collapse (the client only
            # re-reports on the next dimensions change), and a stale
            # 1200px-wide "obstacle" would shove every subsequent spawn
            # past a phantom rect.
            return GROUP_COLLAPSED_WIDTH, GROUP_COLLAPSED_HEIGHT
        measured = self.measured_sizes.get(node.id)
        width, height = measured if measured is not None else (None, None)
        if not (width and width > 0 and height and height > 0):
            if node.kind == "chart" and node.state is not None:
                width, height = node.state.chart_width, node.state.chart_height
            elif node.kind in ("frame", "container") and node.state is not None:
                width, height = node.state.group_width, node.state.group_height
        if not (width and width > 0) or not (height and height > 0):
            fw, fh = KIND_FALLBACK_FOOTPRINTS.get(node.kind, DEFAULT_FALLBACK_FOOTPRINT)
            width = width if (width and width > 0) else fw
            height = height if (height and height > 0) else fh
        return float(width), float(height)

    def kind_fallback_footprint(self, kind: str) -> tuple[float, float]:
        """The pre-creation footprint estimate for a node that does not
        exist yet (place_child sizes the child before add_*_node runs)."""
        return KIND_FALLBACK_FOOTPRINTS.get(kind, DEFAULT_FALLBACK_FOOTPRINT)

    def _placement_obstacles(
        self, exclude_ids: frozenset[str] | set[str] = frozenset(),
    ) -> list[tuple[float, float, float, float]]:
        """Every rect a new node must not land on: all nodes except docked
        ones (they render inside their parent, not at their own x/y) and
        expanded frames/containers - a group's box is derived from its
        members and auto-grows, so its MEMBERS are the real obstacles;
        treating the whole group rect as solid would shove a reply to a
        framed node clear outside its own frame. A collapsed group is a
        fixed pill with no visible members, so it stays an obstacle."""
        rects = []
        for node in self.nodes.values():
            if node.is_docked or node.id in exclude_ids:
                continue
            if node.kind in ("frame", "container") and not node.is_collapsed:
                continue
            w, h = self.node_footprint(node)
            rects.append((node.x, node.y, w, h))
        return rects

    # -- collision resolution ----------------------------------------------

    def find_free_position(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        advance: str = "right",
        exclude_ids: frozenset[str] | set[str] = frozenset(),
    ) -> tuple[float, float]:
        """First clear (x, y) for a width x height rect, starting at the
        proposed spot and advancing past each blocking node ("right" or
        "down"). Deterministic: always steps past the blocking obstacle
        whose far edge is nearest, so repeated spawns fan out compactly in
        a stable order."""
        obstacles = self._placement_obstacles(exclude_ids)
        for _ in range(_MAX_PLACEMENT_STEPS):
            blockers = [
                r for r in obstacles
                if not _rects_clear(x, y, width, height, *r)
            ]
            if not blockers:
                break
            if advance == "down":
                edge = min(r[1] + r[3] for r in blockers)
                y = max(edge + NODE_GAP_Y, y + 1.0)
            else:
                edge = min(r[0] + r[2] for r in blockers)
                x = max(edge + NODE_GAP_X, x + 1.0)
        return x, y

    # -- placement shapes ---------------------------------------------------

    def place_child(
        self, parent_id: str | None, kind: str, *, prefer: str = "below",
    ) -> tuple[float, float]:
        """Where a new `kind` node spawned from parent_id should land:

        - "below": directly under the parent's real bottom edge; if that
          slot is taken (an earlier sibling), fan right until clear.
        - "right": beside the parent's real right edge; fan down.
        - "left": beside the parent's left edge; fan down.
        - "above": directly over the parent's top edge; fan right.

        The parent's *measured* footprint drives the offset, so a tall
        reply pushes its children fully clear instead of the old fixed
        160px that buried them inside the parent card. Falls back to
        place_root when parent_id is missing/unknown."""
        parent = self.nodes.get(parent_id) if parent_id else None
        if parent is None:
            return self.place_root(kind)
        pw, ph = self.node_footprint(parent)
        cw, ch = self.kind_fallback_footprint(kind)
        if prefer == "right":
            x, y = parent.x + pw + NODE_GAP_X, parent.y
            return self.find_free_position(x, y, cw, ch, advance="down")
        if prefer == "left":
            x, y = parent.x - NODE_GAP_X - cw, parent.y
            return self.find_free_position(x, y, cw, ch, advance="down")
        if prefer == "above":
            x, y = parent.x, parent.y - NODE_GAP_Y - ch
            return self.find_free_position(x, y, cw, ch, advance="right")
        x, y = parent.x, parent.y + ph + NODE_GAP_Y
        return self.find_free_position(x, y, cw, ch, advance="right")

    def place_root(self, kind: str) -> tuple[float, float]:
        """Where a fresh parentless node lands: below the scene's current
        content, aligned with its left edge (origin for an empty scene),
        collision-resolved rightward like any other spawn."""
        cw, ch = self.kind_fallback_footprint(kind)
        anchored = [n for n in self.nodes.values() if not n.is_docked]
        if not anchored:
            return 0.0, 0.0
        min_x = min(n.x for n in anchored)
        bottom = max(n.y + self.node_footprint(n)[1] for n in anchored)
        return self.find_free_position(min_x, bottom + NODE_GAP_Y, cw, ch, advance="right")

    def place_at_scene_right(self, kind: str) -> tuple[float, float]:
        """Where a launcher-created node with no canvas anchor (a Builder
        plan, an Agent harness) lands: clear of the scene's right edge -
        the real right edge (x + width), not just max x, so it can never
        overlap the widest existing node."""
        cw, ch = self.kind_fallback_footprint(kind)
        anchored = [n for n in self.nodes.values() if not n.is_docked]
        if not anchored:
            return 120.0, 120.0
        right = max(n.x + self.node_footprint(n)[0] for n in anchored)
        top = max(min(n.y for n in anchored), 120.0)
        return self.find_free_position(right + NODE_GAP_X, top, cw, ch, advance="down")

    # -- organize -----------------------------------------------------------

    def organize_layout(self) -> None:
        """Size-aware tidy layout - the rebuilt Organize Nodes.

        Nodes form a forest via edges (first edge in id order wins as a
        node's structural parent; a link that would close a cycle is
        skipped). Each tree lays out top-down: a child row sits below the
        parent's real bottom edge, and sibling subtrees pack side by side
        by their real subtree widths - so nothing can overlap, whatever
        each node measured. Disconnected trees line up left to right.

        Frames/containers are not laid out as tree nodes: their geometry
        is derived from their members, so after members move each group is
        re-wrapped via _recompute_group_bounds (stale manual frame anchors
        are cleared first - organize is an explicit whole-scene re-layout,
        so a pinned position from before it is meaningless). Collapsed
        group pills keep no member bbox to wrap, so they line up in a row
        below everything else. Docked nodes ride along at their parent's
        position (invisible while docked; sane if later undocked)."""
        group_kinds = ("frame", "container")
        layout_nodes = {
            n.id: n for n in self.nodes.values()
            if n.kind not in group_kinds and not n.is_docked
        }
        if not layout_nodes:
            self._organize_groups()
            return

        parent_of: dict[str, str] = {}
        # Insertion order, NOT sorted by id: edge ids are "e<counter>"
        # strings, so a lexicographic sort puts e10 before e9 and a
        # later-added cross-link could steal a node's structural parent
        # from its real creation edge. self.edges preserves creation order
        # (and a loaded session restores edges in saved order), which is
        # exactly the "first edge wins" rule wanted here.
        for edge in self.edges.values():
            source, target = edge.source, edge.target
            if (
                source == target
                or source not in layout_nodes
                or target not in layout_nodes
                or target in parent_of
            ):
                continue
            # Refuse a link that would close a cycle under the links
            # assigned so far (walk source's ancestry looking for target).
            ancestor: str | None = source
            seen: set[str] = set()
            while ancestor is not None and ancestor not in seen:
                seen.add(ancestor)
                ancestor = parent_of.get(ancestor)
            if target in seen:
                continue
            parent_of[target] = source

        children: dict[str, list[str]] = {}
        for child, parent in parent_of.items():
            children.setdefault(parent, []).append(child)
        # Stable, spatial-intent-preserving order: current x, then id.
        def order_key(node_id: str) -> tuple[float, str]:
            node = layout_nodes[node_id]
            return (node.x, node.id)
        for child_list in children.values():
            child_list.sort(key=order_key)
        roots = sorted(
            (nid for nid in layout_nodes if nid not in parent_of), key=order_key,
        )
        # Keep roots that belong to the same frame/container adjacent, so a
        # re-wrapped group's box does not stretch across another group's
        # members sitting interleaved between its own. Stable sort: within
        # one owner (and among the un-owned) the spatial x-order above is
        # preserved; owner blocks line up in owner-id order.
        owner_of: dict[str, str] = {}
        for group in self.nodes.values():
            if group.kind in ("frame", "container"):
                for member_id in group.item_ids:
                    owner_of.setdefault(member_id, group.id)
        roots.sort(key=lambda nid: owner_of.get(nid, ""))

        # Subtree widths, iterative post-order (chat chains can be deep).
        # packed_width is the children row's own span (0 for a leaf) -
        # remembered so the assign pass below centres over the same number
        # instead of re-deriving it.
        footprints = {nid: self.node_footprint(n) for nid, n in layout_nodes.items()}
        subtree_width: dict[str, float] = {}
        packed_width: dict[str, float] = {}
        stack: list[tuple[str, bool]] = [(r, False) for r in reversed(roots)]
        while stack:
            node_id, expanded = stack.pop()
            kids = children.get(node_id, [])
            if not expanded and kids:
                stack.append((node_id, True))
                stack.extend((k, False) for k in reversed(kids))
                continue
            packed = (
                sum(subtree_width[k] for k in kids) + NODE_GAP_X * (len(kids) - 1)
                if kids else 0.0
            )
            packed_width[node_id] = packed
            subtree_width[node_id] = max(footprints[node_id][0], packed)

        # Assign positions, iterative pre-order: each node centred over its
        # own subtree span, children on a row below its real bottom edge.
        cursor_x = 0.0
        assign: list[tuple[str, float, float]] = []
        for root in roots:
            assign.append((root, cursor_x, 0.0))
            cursor_x += subtree_width[root] + COMPONENT_GAP
        while assign:
            node_id, left, y = assign.pop()
            node = layout_nodes[node_id]
            width, height = footprints[node_id]
            node.x = left + (subtree_width[node_id] - width) / 2.0
            node.y = y
            child_left = left + (subtree_width[node_id] - packed_width[node_id]) / 2.0
            for kid in children.get(node_id, []):
                assign.append((kid, child_left, y + height + NODE_GAP_Y))
                child_left += subtree_width[kid] + NODE_GAP_X

        # Docked nodes ride along at their parent's spot.
        for node in self.nodes.values():
            if not node.is_docked:
                continue
            parent_edge = next(
                (e for e in self.edges.values() if e.target == node.id), None,
            )
            if parent_edge is not None and parent_edge.source in self.nodes:
                parent = self.nodes[parent_edge.source]
                node.x, node.y = parent.x, parent.y

        self._organize_groups()

    def _organize_groups(self) -> None:
        """Post-layout group pass: line collapsed pills up in a row below
        the laid-out content, then re-wrap every expanded frame/container
        around its members' new positions - pills first, so a container
        holding a collapsed pill wraps the pill's final spot, and
        innermost groups first, so an outer container unions its inner
        group's re-wrapped rect rather than a stale pre-organize one
        (containers can legitimately nest - see create_container)."""
        groups = [n for n in self.nodes.values() if n.kind in ("frame", "container")]
        expanded = [g for g in groups if not g.is_collapsed]
        collapsed = [g for g in groups if g.is_collapsed]

        if collapsed:
            collapsed_set = {g.id for g in collapsed}
            others = [
                n for n in self.nodes.values()
                if not n.is_docked and n.id not in collapsed_set
            ]
            if others:
                left = min(n.x for n in others)
                bottom = max(n.y + self.node_footprint(n)[1] for n in others)
            else:
                left, bottom = 0.0, -GROUP_COLLAPSED_HEIGHT - COMPONENT_GAP
            x = left
            y = bottom + COMPONENT_GAP
            for group in sorted(collapsed, key=lambda g: g.id):
                group.x, group.y = x, y
                x += self.node_footprint(group)[0] + NODE_GAP_X

        # Innermost-first: a group's nesting height is 1 + the tallest
        # height among its member groups (0 when it holds no group).
        height_cache: dict[str, int] = {}

        def nesting_height(group_id: str, trail: frozenset[str] = frozenset()) -> int:
            if group_id in height_cache:
                return height_cache[group_id]
            if group_id in trail:  # membership cycles cannot happen; belt and braces
                return 0
            group = self.nodes[group_id]
            member_heights = [
                nesting_height(member_id, trail | {group_id})
                for member_id in group.item_ids
                if (member := self.nodes.get(member_id)) is not None
                and member.kind in ("frame", "container")
            ]
            height_cache[group_id] = 1 + max(member_heights, default=0) if member_heights else 0
            return height_cache[group_id]

        for group in sorted(expanded, key=lambda g: (nesting_height(g.id), g.id)):
            state = group.state
            if state is not None:
                for attr in (
                    "group_manual_x", "group_manual_y",
                    "group_manual_width", "group_manual_height",
                ):
                    if hasattr(state, attr):
                        setattr(state, attr, None)
            self._recompute_group_bounds(group.id)
