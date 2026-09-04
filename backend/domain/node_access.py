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

from typing import TYPE_CHECKING, Generic, TypeGuard, TypeVar

from backend.domain.model import SceneError, SceneNode
from backend.domain.node_states import NodeState

_S = TypeVar("_S", bound=NodeState)

if TYPE_CHECKING:
    class _NodeWith(SceneNode, Generic[_S]):
        """A SceneNode whose `state` is known to be a particular kind's.

        Type-only: never constructed, never imported at runtime.
        """

        state: _S  # type: ignore[assignment]


def _kind_names(kind: str | tuple[str, ...]) -> tuple[str, ...]:
    """One kind or several, as a tuple. `" or ".join` of a single-element
    tuple is that element, so the one-kind error message is unchanged."""
    return (kind,) if isinstance(kind, str) else kind


def require_node(
    nodes: dict[str, SceneNode], node_id: str, kind: str | tuple[str, ...],
    state_cls: type[_S],
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
    kinds = _kind_names(kind)
    if node.kind not in kinds:
        raise SceneError(f"node is not a {' or '.join(kinds)} node: {node_id}")
    return node  # type: ignore[return-value]


def optional_node(
    nodes: dict[str, SceneNode], node_id: str, kind: str | tuple[str, ...],
    state_cls: type[_S],
) -> "_NodeWith[_S] | None":
    """require_node's silent sibling: None instead of a raise.

    The `fail_*_run` methods deliberately do nothing when their node has
    already been deleted - a run that fails after the user removed its node
    is not an error worth surfacing. They still need the narrowed state type
    for the fields they set on the way through.
    """
    node = nodes.get(node_id)
    if node is None or node.kind not in _kind_names(kind):
        return None
    return node  # type: ignore[return-value]


def with_state(node: SceneNode, state_cls: type[_S]) -> "_NodeWith[_S]":
    """A node whose kind has already been decided, narrowed by its state.

    require_node's form for code that is HANDED a node instead of looking one
    up. backend/session_save.py's per-kind serializers are the case this
    exists for: each is reached through a kind-keyed dispatch table, so the
    kind is settled before the function is entered - what the function needs
    is not another kind check but a way to say which state it is about to
    read, in a form a checker can follow.

    Unlike is_node_of this returns the node rather than a bool, because these
    callers have no wrong-kind branch to take: a serializer handed the wrong
    node cannot produce a correct payload, and writing a half-built one into
    a save file is worse than failing. The isinstance check is redundant on
    every live path for the same reason require_node's is - and, exactly like
    require_node's, it is the difference between a clear error and a
    confusing one if a future dispatch table ever disagrees with itself.
    """
    if not isinstance(node.state, state_cls):
        raise SceneError(
            f"node {node.id} is {node.kind} but has no {state_cls.__name__}"
        )
    return node  # type: ignore[return-value]


def is_node_of(
    node: SceneNode | None, kind: str | tuple[str, ...], state_cls: type[_S],
) -> TypeGuard["_NodeWith[_S]"]:
    """require_node's form for a node you already hold.

    The group-geometry code iterates `self.nodes.values()` and skips
    everything that is not a frame or a container; there is no id to look
    up a second time, and doing so to get the narrowing would be a dict
    read bought purely to satisfy a checker. This narrows the node in hand
    instead.

    TypeGuard, not TypeIs, so that the 3.10 floor this repo declares is
    enough and nothing has to import typing_extensions at runtime. It
    narrows only the positive branch, so a `continue`-on-wrong-kind loop
    reads as `if is_node_of(...)` rather than `if not ...: continue`.

    Unlike require_node this also rejects a node with no state at all. Its
    callers hold a node that reached them from somewhere - a dict of every
    node in the scene, a caller's own parameter - rather than one their
    kind's constructor just made, and every hand-written check it replaces
    carried the same `and node.state is not None` conjunct.
    """
    return (
        node is not None
        and node.kind in _kind_names(kind)
        and node.state is not None
    )
