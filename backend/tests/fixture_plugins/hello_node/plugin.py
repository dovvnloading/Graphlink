"""Reference plugin proving the ADR-014 stage 14.1 discovery loop end to
end: manifest -> discovery -> HostContext.register_node_kind/
register_picker_entry -> a working node kind, created via the existing
executePlugin intent, undone via the existing undo stack, rendered by the
existing generic placeholder-card fallback view. Adds ONE new kind,
"hello_note" (namespaced to "hello_node.hello_note" - collision with any
built-in kind, e.g. "note", is structurally impossible).

No NodeState subclass (this demo's one string fits in content, which
_node_wire already emits unconditionally for every kind) and no custom
intent (creation alone is enough to prove the loop)."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.canvas import SceneDocument
from backend.plugin_sdk import HostContext, PluginNodeSeed, PluginRunContext


def _make_hello_note(
    document: SceneDocument, run_ctx: PluginRunContext, parent_id: str,
) -> PluginNodeSeed:
    parent = document.nodes[parent_id]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return PluginNodeSeed(
        title="Hello Node",
        content=(
            f"Created by plugin '{run_ctx.plugin_id}' at {stamp}, "
            f"branched from '{parent.title}'."
        ),
    )


def register(host: HostContext) -> None:
    host.register_node_kind("hello_note", _make_hello_note, requires_parent=True)
    host.register_picker_entry(
        node_kind="hello_note",
        name="Hello Node",
        description=(
            "Adds a trivial timestamped node - proves the ADR-014 SDK "
            "discovery loop end to end."
        ),
        category="More Plugins",
    )
