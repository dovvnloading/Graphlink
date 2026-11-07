"""Canonical structural-parent selection for typed and legacy graph edges.

Explicit primary edges win before untyped legacy links, regardless of
creation order. Other typed edges remain persisted and drawn but do not
silently become ancestry; old files without a role preserve their original
first-eligible-incoming behavior. The same interpretation is shared by the
organizer and branch traversal.
"""

from __future__ import annotations

from collections.abc import Mapping

from backend.domain.model import SceneEdge, SceneNode


def is_structural_edge(nodes: Mapping[str, SceneNode], edge: SceneEdge) -> bool:
    """Whether the edge can define primary tree ancestry."""
    source = nodes.get(edge.source)
    target = nodes.get(edge.target)
    return (
        source is not None
        and target is not None
        and source.kind != "note"
        and edge.role in ("legacy", "primary")
    )


def preferred_parent_edge(
    nodes: Mapping[str, SceneNode],
    edges: Mapping[str, SceneEdge],
    target_id: str,
) -> SceneEdge | None:
    """One node's parent: explicit primary takes precedence over legacy.

    For hot branch traversal, this scans the edge sequence twice instead
    of rebuilding the whole forest for every ancestor hop.
    """
    for role in ("primary", "legacy"):
        for edge in edges.values():
            if (
                edge.target == target_id
                and edge.role == role
                and is_structural_edge(nodes, edge)
            ):
                return edge
    return None


def primary_parent_edges(
    nodes: Mapping[str, SceneNode],
    edges: Mapping[str, SceneEdge],
    eligible_ids: set[str],
) -> dict[str, SceneEdge]:
    """Select one cycle-free parent per target, explicit before legacy.

    All edges remain in the graph. Unselected incoming legacy connections,
    typed secondary parents, references and associations do not influence
    the tree layout. On an untyped legacy graph this is creation-order
    selection with exactly the pre-v2 cycle guard.
    """
    chosen: dict[str, SceneEdge] = {}
    for role in ("primary", "legacy"):
        for edge in edges.values():
            if (
                edge.role != role
                or edge.source not in eligible_ids
                or edge.target not in eligible_ids
                or edge.target in chosen
                or edge.source == edge.target
                or not is_structural_edge(nodes, edge)
            ):
                continue
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
