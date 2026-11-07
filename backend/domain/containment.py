"""Shared backend containment visibility for collapsed semantic groups.

A collapsed frame/container is a visible proxy for its transitive members.
Both layout and preview must agree with the React Flow rendering policy.
Node and edge topology is unchanged; visibility is a view concern only.
"""

from __future__ import annotations

from collections.abc import Mapping

from backend.domain.model import SceneNode

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
