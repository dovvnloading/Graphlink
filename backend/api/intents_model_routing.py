"""ADR-018 stage 18.3: the node/branch model-override intents.

Two plain setters (setModelOverride/clearModelOverride), same shape as
backend/api/intents_groups.py's setGroupColor - a record_command-wrapped
scalar mutation, no agent dispatch involved. Kept in their own module
rather than folded into intents_groups.py/intents_chat.py: this is a new,
independent concern (ADR-018), not an extension of either of those ADRs'
own scope.
"""

from __future__ import annotations

from backend.api._shared import make_publish_scene
from backend.domain.graph import SceneDocument
from backend.events import SessionBus


def register_model_routing_intents(bus: SessionBus, document: SceneDocument) -> None:
    publish_scene = make_publish_scene(bus)

    async def set_model_override(node_id, provider, model_id):
        document.record_command(
            "setModelOverride", "user",
            lambda: document.set_model_override(node_id, provider, model_id),
            node_ids=[node_id],
        )
        await publish_scene()

    async def clear_model_override(node_id):
        document.record_command(
            "clearModelOverride", "user",
            lambda: document.clear_model_override(node_id),
            node_ids=[node_id],
        )
        await publish_scene()

    bus.register_intent("scene", "setModelOverride", set_model_override)
    bus.register_intent("scene", "clearModelOverride", clear_model_override)
