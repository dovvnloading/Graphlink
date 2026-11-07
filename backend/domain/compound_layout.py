"""Constraint-first islands for expanded groups spanning conversation trees.

The normal fast tree packer is appropriate for conversation forests and for
groups whose members belong to one structural tree. It is NOT appropriate
for a group containing a child of tree A and a root of tree B: tree A's
unrelated ancestors and another root can end up inside the group's final
background because roots are packed independently.

For that case we build separate, immutable-membership *islands*. Each
connected component of transitive group membership is packed using the
existing size-aware forest packer, but independently of edges crossing the
group boundary. Ordinary, ungrouped leaves occupy their own forest island.
The islands are then placed side by side with room for nested group padding
and manually-sized frame extents. All original edges remain untouched; only
their visual routing changes. Complicated overlapping-group configurations
that cannot satisfy containment are subsequently rejected by the planner's
geometry validator rather than silently misrepresented.

This is deliberately a small, testable compound-layout fallback, not an
ELK/Dagre dependency or a change to the single-tree fast path.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import TYPE_CHECKING

from backend.domain.containment import group_dependency_order, hidden_group_descendants
from backend.domain.layout_constants import COMPONENT_GAP, NODE_GAP_X
from backend.domain.model import GROUP_PADDING, GROUP_PADDING_TOP

if TYPE_CHECKING:
    from backend.domain.graph import SceneDocument
    from backend.domain.model import SceneNode


_GROUP_KINDS = frozenset(("frame", "container"))


def _visible_group_members(
    document: SceneDocument,
    eligible: set[str],
) -> dict[str, set[str]]:
    """Transitive visible leaf membership in one group-DAG pass."""
    hidden = hidden_group_descendants(document.nodes)
    members_by_group: dict[str, set[str]] = {}
    for gid in group_dependency_order(document.nodes):
        group = document.nodes[gid]
        if group.is_collapsed or gid in hidden:
            members_by_group[gid] = set()
            continue
        members: set[str] = set()
        for child_id in group.item_ids:
            if child_id in eligible:
                members.add(child_id)
            else:
                members.update(members_by_group.get(child_id, ()))
        members_by_group[gid] = members
    return members_by_group


def _structural_root(
    node_id: str,
    parent_of: Mapping[str, str],
    cache: dict[str, str],
) -> str:
    """Root of the same cycle-free primary forest used by LayoutOps."""
    if node_id not in cache:
        trail = []
        current = node_id
        while current in parent_of and current not in cache:
            trail.append(current)
            current = parent_of[current]
        root = cache.get(current, current)
        cache[current] = root
        for member_id in trail:
            cache[member_id] = root
    return cache[node_id]


def _connected_islands(
    document: SceneDocument,
    group_members: Mapping[str, set[str]],
    eligible: set[str],
) -> list[tuple[set[str], set[str]]]:
    """Disjoint group components plus the ungrouped, primary-tree forest.

    A leaf may belong to a frame AND a container; union of membership
    overlaps keeps the shared leaf in exactly one island. The returned
    group-ID set is useful for calculating padding / manual frame extents.
    """
    parent: dict[str, str] = {nid: nid for nid in eligible}

    def find(nid: str) -> str:
        while parent[nid] != nid:
            parent[nid] = parent[parent[nid]]
            nid = parent[nid]
        return nid

    grouped: set[str] = set()
    for members in group_members.values():
        if not members:
            continue
        grouped.update(members)
        ordered = sorted(members)
        first = ordered[0]
        for nid in ordered[1:]:
            a, b = find(first), find(nid)
            if a != b:
                parent[b] = a

    components: dict[str, set[str]] = {}
    for nid in grouped:
        components.setdefault(find(nid), set()).add(nid)
    ungrouped = eligible - grouped
    result: list[tuple[set[str], set[str]]] = (
        [(ungrouped, set())] if ungrouped else []
    )
    insertion = {nid: i for i, nid in enumerate(document.nodes)}
    chunks = sorted(components.values(), key=lambda members: min(insertion[nid] for nid in members))
    for members in chunks:
        owners = {
            gid for gid, leaves in group_members.items()
            if leaves.intersection(members)
        }
        result.append((members, owners))
    return result


def organize_compound_islands(
    document: SceneDocument,
    layout_nodes: Mapping[str, SceneNode],
    parent_of: Mapping[str, str],
) -> bool:
    """Apply a deterministic fallback only if a group spans multiple trees.

    Returns False if the ordinary forest packer should remain in control.
    Collapsed groups keep their established anchor/latent-members contract:
    they are currently an unsupported hard constraint for the alternative
    packing model and therefore keep the existing path.
    """
    if not layout_nodes:
        return False
    if any(
        n.kind in _GROUP_KINDS and n.is_collapsed
        for n in document.nodes.values()
    ):
        return False

    eligible = set(layout_nodes)
    group_members = _visible_group_members(document, eligible)
    root_cache: dict[str, str] = {}
    crosses_trees = any(
        len({_structural_root(nid, parent_of, root_cache) for nid in members}) > 1
        for members in group_members.values()
    )
    if not crosses_trees:
        return False

    islands = _connected_islands(document, group_members, eligible)
    insertion = {nid: i for i, nid in enumerate(document.nodes)}
    # Maximum nested-group depth and manual frame sizes determine how much
    # padding an island may grow AFTER its content cards have been packed.
    depth: dict[str, int] = {}
    for gid in group_dependency_order(document.nodes):
        group = document.nodes[gid]
        depth[gid] = 1 + max((depth[mid] for mid in group.item_ids if mid in depth), default=0)

    cursor_x = 0.0
    for members, owners in islands:
        subset = copy.copy(document)
        subset.nodes = {}
        for nid in sorted(members, key=lambda key: insertion[key]):
            node = copy.copy(layout_nodes[nid])
            # Fix the layout's own input ordering to *creation* order in
            # this compound mode. If the order depended on current x, a
            # second Organize could reorder its own previously moved cards.
            node.x = float(insertion[nid])
            node.y = 0.0
            subset.nodes[nid] = node
        subset.edges = {
            eid: edge for eid, edge in document.edges.items()
            if edge.source in members and edge.target in members
        }
        subset.organize_layout()

        left = min(node.x for node in subset.nodes.values())
        top = min(node.y for node in subset.nodes.values())
        right = max(
            node.x + document.node_footprint(node)[0]
            for node in subset.nodes.values()
        )
        max_depth = max((depth[gid] for gid in owners), default=0)
        max_manual = max((
            max(
                getattr(document.nodes[gid].state, "group_manual_width", 0) or 0,
                getattr(document.nodes[gid].state, "group_manual_height", 0) or 0,
            )
            for gid in owners
        ), default=0.0)
        halo = max_depth * max(GROUP_PADDING, GROUP_PADDING_TOP) * 2 + max_manual + NODE_GAP_X
        offset_x = cursor_x + halo - left
        offset_y = halo - top if owners else -top
        for nid, node in subset.nodes.items():
            original = document.nodes[nid]
            original.x = node.x + offset_x
            original.y = node.y + offset_y

        cursor_x += (right - left) + 2 * halo + COMPONENT_GAP

    # Match LayoutOps' ordinary post-order pass. Only geometry changes;
    # membership, content, connections, pinned/hidden state are untouched.
    for node in document.nodes.values():
        if not node.is_docked:
            continue
        parent_edge = next(
            (edge for edge in document.edges.values() if edge.target == node.id),
            None,
        )
        if parent_edge is not None and parent_edge.source in document.nodes:
            anchor = document.nodes[parent_edge.source]
            node.x, node.y = anchor.x, anchor.y

    document._organize_groups()
    return True
