"""Deterministic, size-aware directed-acyclic canvas layout.

Unlike the legacy first-parent tree packer, this layout considers every
eligible directed connection: converging branches get ranks below *all*
parents, and barycentric sweeps reduce crossings in ordinary DAGs.

This is intentionally a bounded pure-domain solver, not an arbitrary
physics simulation or an ELK replacement. Cyclic/reference edges remain
in the document and renderer; only a deterministic cycle-closing subset
is ignored for ranking. Grouped/very large scenes use the proven tree
packer instead, reported to the preview as an explicit fallback.
"""

from __future__ import annotations

from collections import defaultdict
from heapq import heappop, heappush
from typing import TYPE_CHECKING

from backend.domain.geometry_safety import validate_layout_geometry
from backend.domain.layout import COMPONENT_GAP, NODE_GAP_X, NODE_GAP_Y

if TYPE_CHECKING:
    from backend.domain.graph import SceneDocument


MAX_DAG_LAYOUT_NODES = 400
MAX_DAG_LAYOUT_EDGES = 2_000
_GROUP_KINDS = frozenset(("frame", "container"))
_ROLE_PRIORITY = {"primary": 0, "legacy": 1, "secondary": 2,
                  "reference": 3, "association": 4}


def dag_layout_supported(document: SceneDocument) -> bool:
    """Planner should never run experimental DAG placement inside groups."""
    if any(node.kind in _GROUP_KINDS for node in document.nodes.values()):
        return False
    visible = {nid for nid, node in document.nodes.items() if not node.is_docked}
    if len(visible) > MAX_DAG_LAYOUT_NODES:
        return False
    if sum(1 for edge in document.edges.values()
           if edge.source in visible and edge.target in visible) > MAX_DAG_LAYOUT_EDGES:
        return False
    return True


def _accepted_rank_edges(document: SceneDocument, ids: set[str]) -> list[tuple[str, str]]:
    """Prefer primary/creation-order arrows; omit only cycle-closing ranks."""
    edges = list(enumerate(document.edges.values()))
    edges.sort(key=lambda pair: (_ROLE_PRIORITY.get(pair[1].role, 5), pair[0]))
    children: dict[str, list[str]] = {nid: [] for nid in ids}
    kept: list[tuple[str, str]] = []
    for _, edge in edges:
        source, target = edge.source, edge.target
        if source not in ids or target not in ids or source == target:
            continue
        if target in children[source]:
            continue
        # Topology might contain reference links back to ancestors.
        # They remain as edges, but cannot impose an impossible rank cycle.
        pending, seen = [target], set()
        cyclic = False
        while pending:
            at = pending.pop()
            if at == source:
                cyclic = True
                break
            if at in seen:
                continue
            seen.add(at)
            pending.extend(children[at])
        if not cyclic:
            children[source].append(target)
            kept.append((source, target))
    return kept


def _components(ids: set[str], edges: list[tuple[str, str]], order: dict[str, int]) -> list[list[str]]:
    parent = {nid: nid for nid in ids}

    def find(nid: str) -> str:
        while parent[nid] != nid:
            parent[nid] = parent[parent[nid]]
            nid = parent[nid]
        return nid

    for a, b in edges:
        pa, pb = find(a), find(b)
        if pa != pb:
            if order[pa] < order[pb]:
                parent[pb] = pa
            else:
                parent[pa] = pb
    parts: dict[str, list[str]] = defaultdict(list)
    for nid in sorted(ids, key=order.__getitem__):
        parts[find(nid)].append(nid)
    return list(parts.values())


def _ranked_component(
    nodes: list[str],
    edges: list[tuple[str, str]],
    existing: dict[str, tuple[float, int]],
) -> dict[int, list[str]]:
    """Longest-path topological ranks, then four stable barycentric passes."""
    ids = set(nodes)
    incoming: dict[str, list[str]] = {nid: [] for nid in nodes}
    outgoing: dict[str, list[str]] = {nid: [] for nid in nodes}
    indegree = {nid: 0 for nid in nodes}
    for source, target in edges:
        if source in ids and target in ids:
            incoming[target].append(source)
            outgoing[source].append(target)
            indegree[target] += 1
    ready: list[tuple[float, int, str]] = []
    for nid in nodes:
        if indegree[nid] == 0:
            heappush(ready, (*existing[nid], nid))
    rank = {nid: 0 for nid in nodes}
    while ready:
        _, _, nid = heappop(ready)
        for child in outgoing[nid]:
            rank[child] = max(rank[child], rank[nid] + 1)
            indegree[child] -= 1
            if indegree[child] == 0:
                heappush(ready, (*existing[child], child))
    if any(indegree.values()):
        raise RuntimeError("DAG ranker retained a directed cycle")

    levels: dict[int, list[str]] = defaultdict(list)
    for nid in nodes:
        levels[rank[nid]].append(nid)
    for level in levels.values():
        level.sort(key=existing.__getitem__)
    last = max(levels)

    def sweep(forward: bool) -> None:
        indices = range(1, last + 1) if forward else range(last - 1, -1, -1)
        for depth in indices:
            side = incoming if forward else outgoing
            adjacent = depth - 1 if forward else depth + 1
            neighbor_positions = {
                nid: (pos + 0.5) / len(levels[adjacent])
                for pos, nid in enumerate(levels[adjacent])
            }
            previous = {nid: pos for pos, nid in enumerate(levels[depth])}
            # list.sort() temporarily presents its own list as empty while
            # evaluating key functions. Capture this count *before* sorting:
            # reading len(levels[depth]) from key() can divide by zero on
            # isolated/unconstrained vertices in a partially scoped DAG.
            current_count = len(previous)
            def key(nid: str) -> tuple[float, int]:
                positions = [neighbor_positions[n]
                             for n in side[nid] if n in neighbor_positions]
                if positions:
                    return sum(positions) / len(positions), previous[nid]
                return (previous[nid] + 0.5) / current_count, previous[nid]
            levels[depth].sort(key=key)

    for _ in range(4):
        sweep(True)
        sweep(False)
    return dict(levels)


def organize_dag_layout(document: SceneDocument) -> bool:
    """Arrange an ungrouped directed scene, or safely use existing tree mode.

    Returns True exactly when DAG ranks were used. Does not change edges,
    contents, group membership, or any undo state: the owning organization
    plan already performs clone/preview and one-command apply.
    """
    validate_layout_geometry(document)
    if not dag_layout_supported(document):
        document.organize_layout()
        return False
    nodes = {nid: node for nid, node in document.nodes.items() if not node.is_docked}
    if not nodes:
        return True
    order = {nid: i for i, nid in enumerate(document.nodes)}
    original = {nid: (node.x, order[nid]) for nid, node in nodes.items()}
    accepted = _accepted_rank_edges(document, set(nodes))
    footprints = {nid: document.node_footprint(node) for nid, node in nodes.items()}
    parts = _components(set(nodes), accepted, order)
    parts.sort(key=lambda part: min(original[nid] for nid in part))

    cursor_x = 0.0
    for component in parts:
        levels = _ranked_component(component, accepted, original)
        width_by_rank = {
            level: sum(footprints[nid][0] for nid in members) +
                   NODE_GAP_X * (len(members) - 1)
            for level, members in levels.items()
        }
        width = max(width_by_rank.values())
        y = 0.0
        for depth in sorted(levels):
            layer = levels[depth]
            x = cursor_x + (width - width_by_rank[depth]) / 2.0
            for nid in layer:
                node = nodes[nid]
                node.x, node.y = x, y
                x += footprints[nid][0] + NODE_GAP_X
            y += max(footprints[nid][1] for nid in layer) + NODE_GAP_Y
        cursor_x += width + COMPONENT_GAP

    # Docked cards occupy their parent card's position, as in the
    # established tree organizer. They are not standalone collision boxes.
    for node in document.nodes.values():
        if not node.is_docked:
            continue
        parent_edge = document._branch_parent_edge(node.id)
        if parent_edge and parent_edge.source in document.nodes:
            parent = document.nodes[parent_edge.source]
            node.x, node.y = parent.x, parent.y
    validate_layout_geometry(document)
    return True
