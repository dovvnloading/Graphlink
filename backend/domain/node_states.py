"""ADR-002 stage 2.5 (backend-only): typed per-kind SceneNode state.

SceneNode used to carry all ~95 fields for every one of its 16 kinds
regardless of which kind actually used them. This module holds one
dataclass per kind - only the fields that kind actually uses - attached to
SceneNode.state. Migration proceeds kind-by-kind (see each state class's
own relocation note); a kind not yet migrated still keeps its fields
directly on SceneNode.

Wire-compatibility constraint: SceneDocument.scene_payload() must keep
emitting the exact same flat per-node shape it does today. This module
introduces no wire-layer change by itself - it only relocates where a
field lives in memory; scene_payload's own per-kind read expressions are
updated in lockstep with each kind's migration, in backend/domain/graph.py.

kind values with no state class (no kind-specific fields at all, so
node.state stays None for them permanently): placeholder, thinking,
conversation.
"""

from __future__ import annotations

from dataclasses import dataclass


class NodeState:
    """Marker base for all per-kind node state payloads."""


@dataclass
class ImageState(NodeState):
    """Relocated verbatim from SceneNode.image_asset_id (former
    backend/domain/model.py field, R3.21) - the opaque reference key into
    SceneDocument.image_assets. See that dict's own docstring for the
    transport-decision reasoning (image bytes never live on the node
    itself)."""

    image_asset_id: str = ""
