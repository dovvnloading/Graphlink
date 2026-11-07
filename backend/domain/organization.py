"""Side-effect-free, scope-aware organization plans for the scene canvas.

A preview is calculated on a shallow *structural* clone: node objects and
mutable group state are copied, but histories, assets and undo stacks are not.
No preview can change the live graph, its connections, or its membership.

Only the server computes placements. Applying a preview re-computes the plan
against a geometry/topology fingerprint and copies *layout fields only*.
No client-provided coordinates are accepted on the write path.
"""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from backend.domain.containment import group_dependency_order, hidden_group_descendants
from backend.domain.geometry_safety import validate_layout_geometry
from backend.domain.layout_invariants import validate_no_new_false_containment
from backend.domain.layout import NODE_GAP_X, _rects_clear
from backend.domain.model import SceneError, SceneNode
from backend.domain.node_states import FrameState, GroupSizedState
from backend.domain.structure import primary_parent_edges

if TYPE_CHECKING:
    from backend.domain.graph import SceneDocument

OrganizationScope = Literal["all", "branch", "group", "selection"]
_GROUP_KINDS = frozenset(("frame", "container"))


def _layout_geometry(node: SceneNode) -> tuple[object, ...]:
    """The exact mutation footprint of the organization engine."""
    group_fields: tuple[object, ...] = ()
    if isinstance(node.state, GroupSizedState):
        group_fields = (node.state.group_width, node.state.group_height)
        if isinstance(node.state, FrameState):
            group_fields += (
                node.state.group_manual_x, node.state.group_manual_y,
                node.state.group_manual_width, node.state.group_manual_height,
            )
    return (node.x, node.y, *group_fields)


def layout_fingerprint(document: SceneDocument) -> str:
    """Hash *all* facts that can affect planning or invalidate its preview."""
    # A preview must expire when *content* changes as well as geometry.
    # Streaming replies can update content before the browser has reported
    # a new measured height; checking sizes alone misses that real race.
    # Keep opaque per-kind state and assets in this fence too: image/HTML/
    # chart edits may change what the canvas renders without changing x/y.
    nodes = tuple(
        (
            n.id, n.kind, n.title, n.content, repr(n.history),
            repr(n.state), n.pending_request_id, n.is_docked, n.is_collapsed,
            tuple(n.item_ids), _layout_geometry(n), document.node_footprint(n),
        )
        for n in document.nodes.values()
    )
    edges = tuple((e.id, e.source, e.target) for e in document.edges.values())
    sizes = tuple(sorted(document.measured_sizes.items()))
    assets = tuple(
        (key, mime, hashlib.sha256(data).digest())
        for key, (data, mime) in sorted(document.image_assets.items())
    )
    return hashlib.sha256(repr((nodes, edges, sizes, assets)).encode("utf-8")).hexdigest()


def _clone_for_layout(document: SceneDocument) -> SceneDocument:
    """Clone layout-mutable state, never any real user content or asset bytes."""
    shadow = copy.copy(document)
    # Compound islands temporarily substitute realistic placement-size
    # fallbacks while refitting group geometry. Never share this observation
    # map with the live document, and keep the temporary values available to
    # the planner's *second* preserve-frame-sizes refit.
    shadow.measured_sizes = dict(document.measured_sizes)
    setattr(shadow, "_layout_projection_shadow", True)
    shadow.nodes = {}
    for node_id, original in document.nodes.items():
        node = copy.copy(original)
        # Group recomputation changes state fields in-place. Copying the
        # state avoids changing the live document while previewing.
        if isinstance(node.state, GroupSizedState):
            node.state = copy.copy(node.state)
        shadow.nodes[node_id] = node
    return shadow


def _group_order(document: SceneDocument) -> list[str]:
    """Dependency-ready, iterative ordering even for deeply nested groups."""
    return group_dependency_order(document.nodes)


def _affected_groups(document: SceneDocument, movable: set[str]) -> set[str]:
    """All enclosing groups, including the parents of changed groups."""
    affected: set[str] = set()
    touched = set(movable)
    changed = True
    while changed:
        changed = False
        for node_id, node in document.nodes.items():
            if (
                node.kind in _GROUP_KINDS
                and node_id not in affected
                and touched.intersection(node.item_ids)
            ):
                affected.add(node_id)
                touched.add(node_id)
                changed = True
    return affected


def _refit_groups(
    shadow: SceneDocument,
    original: SceneDocument,
    affected: set[str],
    preserve_frame_sizes: bool,
) -> None:
    """Derived bounds update after all leaf positions have settled."""
    hidden = hidden_group_descendants(shadow.nodes)
    for group_id in _group_order(shadow):
        if group_id not in affected:
            continue
        node = shadow.nodes[group_id]
        if node.is_collapsed or group_id in hidden:
            # Preserve the latent content and any manual frame anchor.
            continue
        if isinstance(node.state, FrameState):
            # Old position anchors cannot survive a reflow without stretching
            # the group across where its children *used* to be.
            node.state.group_manual_x = None
            node.state.group_manual_y = None
            previous_node = original.nodes[group_id]
            if preserve_frame_sizes and isinstance(previous_node.state, FrameState):
                node.state.group_manual_width = previous_node.state.group_manual_width
                node.state.group_manual_height = previous_node.state.group_manual_height
            else:
                node.state.group_manual_width = None
                node.state.group_manual_height = None
        shadow._recompute_group_bounds(group_id)


def _member_leaves(document: SceneDocument, group_id: str) -> set[str]:
    """Expand group membership transitively, with visited guards."""
    visited: set[str] = set()
    leaves: set[str] = set()
    pending = [group_id]
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        group = document.nodes.get(current)
        if group is None or group.kind not in _GROUP_KINDS:
            continue
        for member_id in group.item_ids:
            member = document.nodes.get(member_id)
            if member is None:
                continue
            if member.kind in _GROUP_KINDS:
                pending.append(member_id)
            elif not member.is_docked:
                leaves.add(member_id)
    return leaves


def _selected_leaves(
    document: SceneDocument,
    scope: OrganizationScope,
    root_id: str | None,
    node_ids: list[str] | None,
) -> set[str]:
    hidden = hidden_group_descendants(document.nodes)
    if scope == "all":
        return {
            n.id for n in document.nodes.values()
            if n.kind not in _GROUP_KINDS and not n.is_docked and n.id not in hidden
        }
    if scope == "group":
        group = document.nodes.get(root_id) if root_id else None
        if group is None or group.kind not in _GROUP_KINDS:
            raise SceneError("group organization requires a frame or container")
        if group.is_collapsed or group.id in hidden:
            raise SceneError("expand the collapsed group before organizing its contents")
        return _member_leaves(document, group.id) - hidden
    if scope == "selection":
        if not node_ids:
            raise SceneError("selection organization requires at least one node")
        missing = [node_id for node_id in node_ids if node_id not in document.nodes]
        if missing:
            raise SceneError(f"unknown selected node: {missing[0]}")
        selected: set[str] = set()
        for node_id in node_ids:
            node = document.nodes[node_id]
            if node.kind in _GROUP_KINDS:
                if node.is_collapsed or node.id in hidden:
                    raise SceneError("expand the collapsed group before organizing its contents")
                selected.update(_member_leaves(document, node_id) - hidden)
            elif not node.is_docked and node.id not in hidden:
                selected.add(node_id)
        return selected
    if scope == "branch":
        root = document.nodes.get(root_id) if root_id else None
        if root is None or root.kind in _GROUP_KINDS or root.is_docked or root.id in hidden:
            raise SceneError("branch organization requires a visible content node")
        eligible = {
            n.id for n in document.nodes.values()
            if n.kind not in _GROUP_KINDS and not n.is_docked and n.id not in hidden
        }
        parent_edges = primary_parent_edges(document.nodes, document.edges, eligible)
        children: dict[str, list[str]] = {}
        for target, edge in parent_edges.items():
            children.setdefault(edge.source, []).append(target)
        selected = {root.id}
        stack = [root.id]
        while stack:
            current = stack.pop()
            for child in children.get(current, []):
                if child not in selected:
                    selected.add(child)
                    stack.append(child)
        return selected
    raise SceneError(f"unknown organization scope: {scope}")


def _place_subset(
    shadow: SceneDocument,
    original: SceneDocument,
    movable: set[str],
) -> None:
    """Run the existing measured, deterministic tree packer *only* on movable leaves.

    Anchor the new arrangement near its former top-left and translate the
    whole arrangement past fixed obstacles. The block moves as a unit so a
    collision never distorts the edges' relative geometry.
    """
    if not movable:
        return
    original_left = min(original.nodes[nid].x for nid in movable)
    original_top = min(original.nodes[nid].y for nid in movable)
    subset = copy.copy(shadow)
    subset.nodes = {nid: shadow.nodes[nid] for nid in shadow.nodes if nid in movable}
    subset.edges = {
        eid: e for eid, e in shadow.edges.items()
        if e.source in movable and e.target in movable
    }
    subset.organize_layout()

    layout_left = min(shadow.nodes[nid].x for nid in movable)
    layout_top = min(shadow.nodes[nid].y for nid in movable)
    dx, dy = original_left - layout_left, original_top - layout_top
    for nid in movable:
        node = shadow.nodes[nid]
        node.x += dx
        node.y += dy

    # A frame/container is a *semantic* background. Allow selected members
    # to reflow within their own groups, but keep every unrelated expanded
    # group as a solid obstacle. Ignoring those boxes makes unrelated nodes
    # visually appear to belong to a group without changing item_ids.
    # Docked nodes render inside a parent rather than as standalone cards.
    enclosing_groups = _affected_groups(original, movable)
    obstacles = []
    for node in original.nodes.values():
        if node.id in movable or node.is_docked:
            continue
        if node.kind in _GROUP_KINDS and not node.is_collapsed and node.id in enclosing_groups:
            continue
        width, height = original.node_footprint(node)
        obstacles.append((node.x, node.y, width, height))

    left = min(shadow.nodes[nid].x for nid in movable)
    top = min(shadow.nodes[nid].y for nid in movable)
    right = max(shadow.nodes[nid].x + shadow.node_footprint(shadow.nodes[nid])[0] for nid in movable)
    bottom = max(shadow.nodes[nid].y + shadow.node_footprint(shadow.nodes[nid])[1] for nid in movable)
    width, height = right - left, bottom - top
    # Once this bounding rect clears an obstacle to its right it can never
    # collide with that obstacle again. Thus <= #obstacles advances suffice.
    for _ in range(len(obstacles) + 1):
        blockers = [
            rect for rect in obstacles
            if not _rects_clear(left, top, width, height, *rect)
        ]
        if not blockers:
            break
        new_left = min(rect[0] + rect[2] + NODE_GAP_X for rect in blockers)
        shift = new_left - left
        left = new_left
        for nid in movable:
            shadow.nodes[nid].x += shift
    else:
        raise SceneError("could not find space for organized selection")


@dataclass(frozen=True)
class OrganizationPlan:
    source_fingerprint: str
    plan_id: str
    scope: OrganizationScope
    selected_ids: frozenset[str]
    changed_ids: frozenset[str]
    nodes: dict[str, SceneNode]

    def preview(self, document: SceneDocument) -> dict[str, object]:
        """Give the client a read-only before/after diagram with live edges.

        The original and proposed rectangles use the same footprint rules.
        No client-side proposal is ever accepted back by Apply; its plan ID
        alone is checked against a freshly recomputed server-side layout.
        """
        rows: list[dict[str, object]] = []
        visible_ids: set[str] = set()
        hidden = hidden_group_descendants(self.nodes)

        def footprint(node: SceneNode) -> tuple[float, float]:
            width, height = document.node_footprint(node)
            if isinstance(node.state, GroupSizedState) and not node.is_collapsed:
                width = float(node.state.group_width or width)
                height = float(node.state.group_height or height)
            return width, height

        before_bounds: list[tuple[float, float, float, float]] = []
        after_bounds: list[tuple[float, float, float, float]] = []
        for nid, node in self.nodes.items():
            if node.is_docked or nid in hidden:
                continue
            original = document.nodes[nid]
            width, height = footprint(node)
            original_width, original_height = footprint(original)
            rows.append({
                "id": nid, "kind": node.kind, "title": node.title[:80],
                "x": node.x, "y": node.y, "width": width, "height": height,
                "previousX": original.x, "previousY": original.y,
                "previousWidth": original_width, "previousHeight": original_height,
                "changed": nid in self.changed_ids,
            })
            visible_ids.add(nid)
            before_bounds.append((original.x, original.y, original_width, original_height))
            after_bounds.append((node.x, node.y, width, height))

        def area(rects: list[tuple[float, float, float, float]]) -> float:
            if not rects:
                return 0.0
            left = min(x for x, _, _, _ in rects)
            top = min(y for _, y, _, _ in rects)
            right = max(x + w for x, _, w, _ in rects)
            bottom = max(y + h for _, y, _, h in rects)
            return max(0.0, right - left) * max(0.0, bottom - top)

        return {
            "fingerprint": self.source_fingerprint,
            "planId": self.plan_id,
            "scope": self.scope,
            "selectedCount": len(self.selected_ids),
            "movedCount": len(self.changed_ids),
            "areaBefore": area(before_bounds),
            "areaAfter": area(after_bounds),
            "nodes": rows,
            "edges": [
                {"id": edge.id, "source": edge.source, "target": edge.target}
                for edge in document.edges.values()
                if edge.source in visible_ids and edge.target in visible_ids
            ],
        }


def make_organization_plan(
    document: SceneDocument,
    scope: OrganizationScope = "all",
    *,
    root_id: str | None = None,
    node_ids: list[str] | None = None,
    fixed_node_ids: list[str] | None = None,
    preserve_frame_sizes: bool = True,
) -> OrganizationPlan:
    """Plan a scoped organization. No mutation of document is permitted."""
    if scope not in ("all", "branch", "group", "selection"):
        raise SceneError(f"unknown organization scope: {scope}")
    if root_id is not None and not isinstance(root_id, str):
        raise SceneError("root_id must be a node id")
    for name, ids in (("node_ids", node_ids), ("fixed_node_ids", fixed_node_ids)):
        if ids is not None and (
            not isinstance(ids, list)
            or any(not isinstance(nid, str) for nid in ids)
        ):
            raise SceneError(f"{name} must be a list of node ids")
    if not isinstance(preserve_frame_sizes, bool):
        raise SceneError("preserve_frame_sizes must be a boolean")
    # Reject malformed coordinates and dimensions before calculating even a
    # read-only preview, including stale browser measurements.
    validate_layout_geometry(document)
    source_fingerprint = layout_fingerprint(document)
    selected = _selected_leaves(document, scope, root_id, node_ids)
    fixed = set(fixed_node_ids or ())
    unknown = fixed.difference(document.nodes)
    if unknown:
        raise SceneError(f"unknown fixed node: {sorted(unknown)[0]}")
    # A pinned group is a hard *region*, not merely a frozen background:
    # moving its members could silently violate the group's enclosure.
    # Freeze the whole transitive membership DAG, including nested groups
    # and dual-membership cards, without recursing on arbitrary legacy depth.
    pending = [
        nid for nid in fixed if document.nodes[nid].kind in _GROUP_KINDS
    ]
    inspected: set[str] = set()
    while pending:
        group_id = pending.pop()
        if group_id in inspected:
            continue
        inspected.add(group_id)
        for member_id in document.nodes[group_id].item_ids:
            member = document.nodes.get(member_id)
            if member is None:
                continue
            fixed.add(member_id)
            if member.kind in _GROUP_KINDS and member_id not in inspected:
                pending.append(member_id)
    movable = selected - fixed
    shadow = _clone_for_layout(document)

    if scope == "all" and not fixed:
        shadow.organize_layout()
        if preserve_frame_sizes:
            # Organize's historical default clears manual frame sizing.
            # Explicit preview/apply preserves the dimensions by default.
            _refit_groups(
                shadow, document,
                {nid for nid, n in shadow.nodes.items() if n.kind in _GROUP_KINDS},
                True,
            )
    else:
        _place_subset(shadow, document, movable)
        _refit_groups(shadow, document, _affected_groups(shadow, movable), preserve_frame_sizes)

    # A fixed frame/container is as immutable as a fixed content card.
    # Refitting group bounds can otherwise move the pinned background when
    # some of its other members are reflowed.
    for fixed_id in fixed:
        if _layout_geometry(shadow.nodes[fixed_id]) != _layout_geometry(document.nodes[fixed_id]):
            raise SceneError(f"organization would change fixed node {fixed_id}")

    # Before offering this read-only preview, enforce semantic group
    # integrity: no rearranged card may newly appear inside a background
    # whose item_ids do not contain it. This uses the same node rectangles
    # as the on-canvas renderer and permits legitimate dual membership.
    validate_layout_geometry(shadow)
    validate_no_new_false_containment(document, shadow)
    changed = frozenset(
        nid for nid, node in shadow.nodes.items()
        if _layout_geometry(node) != _layout_geometry(document.nodes[nid])
    )
    signature = hashlib.sha256(
        repr((
            source_fingerprint, scope, root_id, tuple(sorted(selected)),
            tuple(sorted(fixed)), preserve_frame_sizes,
            tuple((nid, _layout_geometry(shadow.nodes[nid])) for nid in sorted(changed)),
        )).encode("utf-8")
    ).hexdigest()
    return OrganizationPlan(source_fingerprint, signature, scope, frozenset(selected), changed, shadow.nodes)


def apply_organization_plan(document: SceneDocument, plan: OrganizationPlan) -> None:
    """Apply *server-calculated* geometry, with an atomic stale-preview fence."""
    if layout_fingerprint(document) != plan.source_fingerprint:
        raise SceneError("graph changed after organization preview; preview again")
    validate_layout_geometry(document)
    # Even direct in-process callers cannot smuggle a malformed projected
    # group width or coordinate through a valid stale-preview fingerprint.
    # Check all rows first so no earlier row is partially committed.
    projected_scene = copy.copy(document)
    projected_scene.nodes = plan.nodes
    validate_layout_geometry(projected_scene)
    validate_no_new_false_containment(document, projected_scene)
    for nid in plan.changed_ids:
        target = document.nodes[nid]
        projected = plan.nodes[nid]
        target.x, target.y = projected.x, projected.y
        if isinstance(target.state, GroupSizedState) and isinstance(projected.state, GroupSizedState):
            target.state.group_width = projected.state.group_width
            target.state.group_height = projected.state.group_height
            if isinstance(target.state, FrameState) and isinstance(projected.state, FrameState):
                target.state.group_manual_x = projected.state.group_manual_x
                target.state.group_manual_y = projected.state.group_manual_y
                target.state.group_manual_width = projected.state.group_manual_width
                target.state.group_manual_height = projected.state.group_manual_height
