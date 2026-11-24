"""First-party registration of the persistent MCP Connect canvas card."""
from __future__ import annotations

import json
from weakref import WeakKeyDictionary

from backend.domain.model import SceneError
from backend.domain.node_states import PersistedPluginState, PluginStateSnapshot
from backend.plugin_sdk import HostContext, PluginNodeSeed

from .core import HISTORY_LIMIT, MAX_STATE_BYTES, new_state, state_data, valid_result
from .schema import canonical

_WIRE_CACHE: WeakKeyDictionary[PluginStateSnapshot, str] = WeakKeyDictionary()


def serialize_saved(node) -> dict:
    return node.state.data if type(node.state) in (PersistedPluginState, PluginStateSnapshot) else {}


def serialize(node) -> dict[str, str]:
    if type(node.state) is PluginStateSnapshot:
        cached = _WIRE_CACHE.get(node.state)
        if cached is not None:
            return {"mcp": cached}
    try:
        data = state_data(node)
        canonical(data, limit=MAX_STATE_BYTES)
        history = data.get("history", [])
        if (data.get("latest") is not None and not valid_result(data["latest"])) or (
            not isinstance(history, list) or len(history) > HISTORY_LIMIT or any(not valid_result(result) for result in history)
        ):
            raise SceneError("The saved MCP result receipt is damaged. Restore a verified graph version.")
    except SceneError as exc:
        # Keep damaged-state rendering deterministic; changing a random key on
        # every scene publication would invalidate the canvas memoization.
        data = {"version": 1, "nodeKey": "", "config": None, "latest": None,
                "history": [], "status": "error", "error": str(exc)}
    wire = json.dumps(data, separators=(",", ":"), allow_nan=False)
    if type(node.state) is PluginStateSnapshot:
        _WIRE_CACHE[node.state] = wire
    return {"mcp": wire}


def deserialize(data: dict) -> PluginStateSnapshot:
    # Retain an unsupported saved version for recovery; it is shown as an
    # actionable error and cannot pass the execution validator.
    canonical(data, limit=MAX_STATE_BYTES)
    return PluginStateSnapshot.from_data(data)


def register(host: HostContext) -> None:
    def seed(document, context, parent):
        return PluginNodeSeed(title="MCP tool", state=PluginStateSnapshot.from_data(new_state()))

    host.register_node_kind(
        "tool", seed, requires_parent=False, serialize=serialize,
        serialize_saved=serialize_saved, deserialize=deserialize, externalize_state=True,
    )
    host.register_picker_entry(
        node_kind="tool", name="MCP Connect",
        description="Connect a tool's inputs to your graph and save its structured output with a run receipt.",
        category="Workflow & Drafting",
    )
