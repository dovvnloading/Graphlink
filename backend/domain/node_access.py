"""require_node - fetch a node, check its kind, and narrow its state type.

Every per-kind SceneDocument method opens with the same five lines: look the
node up, raise if it is missing, raise if it is the wrong kind, then read or
write `node.state.<kind>_<field>`. Eighteen copies of that preamble existed
in the two extracted per-kind mixins alone.

The repetition is the smaller half. The larger half is that
`SceneNode.state` is typed `NodeState | None` against a marker class with no
fields, so EVERY one of those field accesses is unverifiable - 327 of the
845 remaining `mypy backend` errors are exactly this, and it is why
[tool.mypy].files could not widen to backend/domain/.

WHY THIS WORKS, when a `state = node.state` alias does not:
tests/test_node_state_migration.py requires every migrated field to be read
as `<anything>.state.<field>` - see its own `_is_via_state`, which checks
only that the attribute being accessed sits on something whose attribute is
`state`. It constrains the SHAPE of the access, not how the node was
obtained. So narrowing the NODE is allowed where aliasing the STATE is not,
and `node.state.code_review_pr_url` satisfies the gate and the type checker
at the same time.

_NodeWith exists only for the checker: at runtime require_node returns the
plain SceneNode it looked up, unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Generic, TypeVar

from backend.domain.model import SceneError, SceneNode
from backend.domain.node_states import NodeState

_S = TypeVar("_S", bound=NodeState)

if TYPE_CHECKING:
    class _NodeWith(SceneNode, Generic[_S]):
        """A SceneNode whose `state` is known to be a particular kind's.

        Type-only: never constructed, never imported at runtime.
        """

        state: _S  # type: ignore[assignment]


def require_node(
    nodes: dict[str, SceneNode], node_id: str, kind: str, state_cls: type[_S],
) -> _NodeWith[_S]:
    """The node with `node_id`, guaranteed to exist and to be `kind`.

    Raises SceneError with the same two messages every hand-written copy of
    this preamble used, so the wire-level error text callers already depend
    on is unchanged.

    `state_cls` is not checked at runtime - the kind string is the real
    guarantee, and the per-kind state class is assigned by the one
    `add_*_node` constructor for that kind. It is here to carry the type
    through, and to make the call site say which state it expects.
    """
    node = nodes.get(node_id)
    if node is None:
        raise SceneError(f"unknown node: {node_id}")
    if node.kind != kind:
        raise SceneError(f"node is not a {kind} node: {node_id}")
    return node  # type: ignore[return-value]


def optional_node(
    nodes: dict[str, SceneNode], node_id: str, kind: str, state_cls: type[_S],
) -> "_NodeWith[_S] | None":
    """require_node's silent sibling: None instead of a raise.

    The `fail_*_run` methods deliberately do nothing when their node has
    already been deleted - a run that fails after the user removed its node
    is not an error worth surfacing. They still need the narrowed state type
    for the fields they set on the way through.
    """
    node = nodes.get(node_id)
    if node is None or node.kind != kind:
        return None
    return node  # type: ignore[return-value]
