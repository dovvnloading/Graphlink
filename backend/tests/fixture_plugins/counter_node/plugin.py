"""Reference plugin proving ADR-014 stage 14.2's generic persistence
mechanism end to end: a plugin's OWN NodeState subclass (CounterState below)
attaches via PluginNodeSeed.state exactly like plugins/hello_node/ proved
title/content do, but ALSO opts into HostContext.register_node_kind's
serialize/deserialize hooks - so a save -> reload cycle provably restores
`count`/`label`, not just the universal title/content fields every plugin
kind gets for free.

Adds ONE new kind, "tally" (namespaced to "counter_node.tally" - collision
with any built-in kind is structurally impossible, same as hello_node's own
"hello_note")."""

from __future__ import annotations

from dataclasses import dataclass

from backend.canvas import SceneDocument
from backend.domain.node_states import NodeState
from backend.plugin_sdk import HostContext, PluginNodeSeed, PluginRunContext


@dataclass
class CounterState(NodeState):
    """This plugin's own per-node state - a plain dataclass subclassing the
    same NodeState marker every built-in per-kind state class subclasses
    (backend/domain/node_states.py). Nothing beyond that inheritance is
    required of a plugin's own state class."""

    count: int = 0
    label: str = ""


def _make_tally(
    document: SceneDocument, run_ctx: PluginRunContext, parent_id: str,
) -> PluginNodeSeed:
    parent = document.nodes[parent_id]
    return PluginNodeSeed(
        title="Counter",
        content=f"A running tally branched from '{parent.title}'.",
        state=CounterState(count=0, label=f"from {run_ctx.plugin_id}"),
    )


def _serialize_tally(node) -> dict:
    """Called both by backend/domain/graph.py's _node_wire (the live WS
    wire's pluginState field) and by backend/session_save.py (the
    persisted save file's plugin_state field) - see HostContext.
    register_node_kind's own docstring for the shared-hook contract. Must
    tolerate `node.state` not (yet) being a CounterState instance - a node
    of this kind freshly minted by _make_tally above always has one, but a
    defensive isinstance check costs nothing and keeps this function honest
    about its own precondition."""
    state = node.state
    if not isinstance(state, CounterState):
        return {}
    return {"count": state.count, "label": state.label}


def _deserialize_tally(data: dict) -> CounterState | None:
    """The save-side-only mirror of _serialize_tally above, called by
    backend/session_load.py with whatever dict serialize() most recently
    produced for this node."""
    try:
        count = int(data.get("count", 0))
    except (TypeError, ValueError):
        count = 0
    return CounterState(count=count, label=str(data.get("label", "") or ""))


def register(host: HostContext) -> None:
    host.register_node_kind(
        "tally", _make_tally, requires_parent=True,
        serialize=_serialize_tally, deserialize=_deserialize_tally,
    )
    host.register_picker_entry(
        node_kind="tally",
        name="Counter Node",
        description=(
            "Adds a node with a real per-node counter - proves the "
            "ADR-014 stage 14.2 generic persistence mechanism end to end."
        ),
        category="More Plugins",
    )
