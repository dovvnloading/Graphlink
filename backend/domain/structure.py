"""Canonical interpretation of legacy connection roles for branch operations.

SceneEdge is deliberately still a three-field, untyped persisted record. A
note -> node link represents metadata (for example a System Prompt), never a
conversation parent. Keep that rule in one place so branch history and layout
cannot assign different parents to the same node.

An explicit persisted edge-role schema is a separate migration; this module
makes the existing legacy interpretation deterministic without changing disk
formats or removing user-created links.
"""

from __future__ import annotations

from collections.abc import Mapping

from backend.domain.model import SceneEdge, SceneNode


def is_structural_edge(nodes: Mapping[str, SceneNode], edge: SceneEdge) -> bool:
    """Whether an edge may define ancestry in a conversation/tree layout."""
    source = nodes.get(edge.source)
    target = nodes.get(edge.target)
    return source is not None and target is not None and source.kind != "note"


def primary_parent_edges(
    nodes: Mapping[str, SceneNode],
    edges: Mapping[str, SceneEdge],
    eligible_ids: set[str],
) -> dict[str, SceneEdge]:
    """Select a cycle-free forest of primary parents in *creation order*.

    Extra incoming links remain in the graph as references. They never steal
    the first eligible parent. Invalid legacy cycles are ignored for layout,
    without deleting or mutating the underlying edges.
    """
    chosen: dict[str, SceneEdge] = {}
    for edge in edges.values():
        if (
            edge.source not in eligible_ids
            or edge.target not in eligible_ids
            or edge.target in chosen
            or edge.source == edge.target
            or not is_structural_edge(nodes, edge)
        ):
            continue
        # A later cross-link may not close a cycle in the chosen forest.
        seen: set[str] = set()
        ancestor: str | None = edge.source
        while ancestor is not None and ancestor not in seen:
            seen.add(ancestor)
            parent_edge = chosen.get(ancestor)
            ancestor = parent_edge.source if parent_edge is not None else None
        if edge.target in seen:
            continue
        chosen[edge.target] = edge
    return chosen
