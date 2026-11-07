"""Read-only semantic layout invariants for organization plans.

The same group can own a descendant of tree A and a root of tree C; ordinary
tree-packing may put an unrelated B in the rectangle wrapped around A and C.
That does not change graph membership but creates a false visual cue. Keep
the invariant separate from the layout algorithm so future DAG/ELK engines
must pass exactly the same check.

Pre-existing manual overlaps are not automatically rewritten by a scoped
organize. We prohibit *new* false membership on every preview, while
tests for whole-canvas cross-tree layout require a fully clean result.
"""

from __future__ import annotations

from collections.abc import Mapping

from backend.domain._composed import SceneDocumentParts
from backend.domain.containment import hidden_group_descendants
from backend.domain.model import SceneError, SceneNode

_GROUP_KINDS = frozenset(("frame", "container"))


def _intersects(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return (ax < bx + bw and bx < ax + aw
            and ay < by + bh and by < ay + ah)


def _transitive_members(
    nodes: Mapping[str, SceneNode], group_id: str,
) -> set[str]:
    """Every descendant ID, not only visible leaves; cycle guarded."""
    members: set[str] = set()
    pending = [group_id]
    while pending:
        gid = pending.pop()
        group = nodes.get(gid)
        if group is None or group.kind not in _GROUP_KINDS:
            continue
        for member_id in group.item_ids:
            if member_id in members or member_id == group_id:
                continue
            members.add(member_id)
            child = nodes.get(member_id)
            if child is not None and child.kind in _GROUP_KINDS:
                pending.append(member_id)
    return members


def visual_membership_violations(
    document: SceneDocumentParts,
) -> set[tuple[str, str]]:
    """Visible (group, outsider-card) intersections that imply false ownership.

    Expanded group backgrounds legitimately overlap other group backgrounds,
    and a node shared between a frame and container is legitimately inside
    *both*. Only unrelated visible leaf cards / collapsed-group pills count.
    """
    hidden = hidden_group_descendants(document.nodes)
    solid: dict[str, SceneNode] = {
        nid: node for nid, node in document.nodes.items()
        if not node.is_docked and nid not in hidden
        and (node.kind not in _GROUP_KINDS or node.is_collapsed)
    }
    rects = {
        nid: (n.x, n.y, *document.node_footprint(n))
        for nid, n in solid.items()
    }
    violations: set[tuple[str, str]] = set()
    for gid, group in document.nodes.items():
        if (group.kind not in _GROUP_KINDS or group.is_collapsed
                or group.is_docked or gid in hidden):
            continue
        allowed = _transitive_members(document.nodes, gid)
        group_rect = (group.x, group.y, *document.node_footprint(group))
        for other_id, rect in rects.items():
            if other_id not in allowed and _intersects(group_rect, rect):
                violations.add((gid, other_id))
    return violations


def validate_no_new_false_containment(
    original: SceneDocumentParts,
    projected: SceneDocumentParts,
) -> None:
    """Reject any false containment in groups handled by compound packing.

    A legacy single-tree frame may deliberately be manually expanded over
    an unrelated content card, or be resized around an attached chart with
    an outgoing edge to its external parent. A universal validator without
    a matching constraint-aware solver would reject formerly-valid normal
    layouts; the old experimental PR #478 failed in exactly that way.
    Restrict this hard gate to cross-tree groups actually handled by the
    new island solver. Full-scene constraints remain a separate #476 phase.
    """
    from backend.domain.compound_layout import cross_tree_group_ids

    constrained = cross_tree_group_ids(original)
    if not constrained:
        return
    violations = sorted(
        (gid, nid) for gid, nid in visual_membership_violations(projected)
        if gid in constrained
    )
    if violations:
        gid, outsider = violations[0]
        raise SceneError(
            f"organization would visually enclose unrelated node {outsider} "
            f"inside group {gid}"
        )
