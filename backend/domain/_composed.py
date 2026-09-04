"""What every backend/domain mixin relies on SceneDocument to provide.

BranchOps, GroupOps, LayoutOps and CommandOps are mixins, not standalone
types: each is composed exactly once, by
`class SceneDocument(BranchOps, GroupOps, LayoutOps, CommandOps)` in
backend/domain/graph.py. They freely use `self.nodes`, `self.edges`,
`self.command_log` and a handful of SceneDocument's own methods - correct at
runtime, and invisible to a type checker looking at one mixin in isolation.

That invisibility is most of why `[tool.mypy].files` could not be widened to
backend/domain/. Nothing declared the contract between a mixin and the class
composing it, so the tree could not be checked at all - not because it was
badly typed, but because it was untypeable.

Same shape, and same fix, as settings_store/_composed.py's
SettingsManagerParts. See that module's docstring for the full reasoning.

EVERYTHING HERE IS TYPE_CHECKING-ONLY. At runtime this class is empty, so
inheriting it adds no attributes, no methods and no `__init__` - the real
implementations still come from SceneDocument's own body and from the
mixins themselves. It declares what the composed object has; it is never a
second source of it.
"""

from __future__ import annotations

import itertools
from collections import deque
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.domain.model import SceneEdge, SceneNode


class SceneDocumentParts:
    """Type-only declaration of the composed SceneDocument's shared surface.

    Every mixin in this package inherits it. CommandOps supplies
    command_log/redo_stack itself and consumes the rest, exactly as
    PersistenceOps does in settings_store - a mixin both providing and
    consuming is normal here.
    """

    if TYPE_CHECKING:
        # Core graph state, held on SceneDocument itself.
        nodes: dict[str, SceneNode]
        edges: dict[str, SceneEdge]
        image_assets: dict[str, Any]
        measured_sizes: dict[str, Any]
        pins: Any
        _counter: itertools.count

        # The undo/redo command layer (CommandOps' own, consumed by siblings).
        command_log: deque
        redo_stack: list
        _composite_depth: int
        _composite_buffer: list

        # SceneDocument methods the mixins call across the composition.
        def connect(self, source: str, target: str) -> SceneEdge: ...

        def remove_nodes(self, node_ids: list[str]) -> None: ...

        def add_chat_node(
            self, x: float, y: float, content: str, is_user: bool,
            parent_id: str | None = None, *args: Any, **kwargs: Any,
        ) -> SceneNode: ...

        def place_root(self, kind: str) -> tuple[float, float]: ...

        def place_child(
            self, parent_id: str | None, kind: str, *, prefer: str = "below",
        ) -> tuple[float, float]: ...

        def _recompute_group_bounds(self, node_id: str) -> None: ...

        def _reaches(self, start: str, goal: str) -> bool: ...

        def _detach_node_from_membership(self, node_id: str) -> None: ...
