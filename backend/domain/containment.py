"""Shared backend containment visibility for collapsed semantic groups.

A collapsed frame/container is a visible proxy for its transitive members.
Both layout and preview must agree with the React Flow rendering policy.
Node and edge topology is unchanged; visibility is a view concern only.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping

from backend.domain.model import SceneError, SceneNode

GROUP_KINDS = frozenset(("frame", "container"))


def hidden_group_descendants(nodes: Mapping[str, SceneNode]) -> set[str]:
    hidden: set[str] = set()
    pending = [
        member_id
        for node in nodes.values()
        if node.kind in GROUP_KINDS and node.is_collapsed
        for member_id in node.item_ids
    ]
    while pending:
        member_id = pending.pop()
        if member_id in hidden:
            continue
        member = nodes.get(member_id)
        if member is None:
            continue
        hidden.add(member_id)
        if member.kind in GROUP_KINDS:
            pending.extend(member.item_ids)
    return hidden


def group_dependency_order(nodes: Mapping[str, SceneNode]) -> list[str]:
    """All groups innermost-first, iteratively, with cycle detection.

    A thousand nested containers must never overflow the Python call stack,
    and malformed legacy cycles must fail before any layout is committed.
    """
    groups = {
        node.id: node for node in nodes.values()
        if node.kind in GROUP_KINDS
    }
    prerequisites = {
        gid: {member for member in group.item_ids if member in groups}
        for gid, group in groups.items()
    }
    parents: dict[str, list[str]] = {}
    for gid, children in prerequisites.items():
        for child in children:
            parents.setdefault(child, []).append(gid)
    ready = deque(gid for gid in groups if not prerequisites[gid])
    ordered: list[str] = []
    while ready:
        gid = ready.popleft()
        ordered.append(gid)
        for parent in parents.get(gid, ()):
            prerequisites[parent].discard(gid)
            if not prerequisites[parent]:
                ready.append(parent)
    if len(ordered) != len(groups):
        raise SceneError("cannot organize cyclic group membership")
    return ordered
