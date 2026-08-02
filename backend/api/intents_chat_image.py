"""ADR-002 stage 2.6: image generation/regeneration for ChatNode/ImageNode.

Relocated VERBATIM from backend/canvas.py's former register_canvas
(closures at lines 737-789; registration calls from the former tail block
at lines 1708-1709) - pure code motion, no behavior change. Kept as its
own module (not folded into intents_chat.py) because _dispatch_image runs
on AgentDispatcher's independent "image" run kind, not "chat" - a genuine
different feature, not just a size trim (see _dispatch_image's own
docstring below).
"""

from __future__ import annotations

from backend.agents import AgentDispatcher
from backend.domain.graph import SceneDocument
from backend.domain.model import SceneEmptyPromptError, SceneError
from backend.events import SessionBus
from backend.notifications import NotificationState


def register_chat_image_intents(
    bus: SessionBus,
    document: SceneDocument,
    notifications: NotificationState,
    agent_dispatcher: AgentDispatcher,
) -> None:
    async def _dispatch_image(parent_chat_node_id, prompt):
        # R4.4a: shared internal path for both generateImage and
        # regenerateImage below - each resolves its own (parent_chat_node_id,
        # prompt) pair from a different source-node kind, then both funnel
        # through this one dispatch + success-primitive call. Runs on
        # agent_dispatcher's INDEPENDENT "image" run kind, never its "chat"
        # kind - see backend/agents.py's AgentDispatcher docstring for why
        # chat and image generation must be able to run concurrently.
        async def _on_reply(image_bytes):
            if parent_chat_node_id not in document.nodes:
                # Mid-flight delete, silent no-op - same posture as
                # regenerate_response's own liveness check (backend/api/
                # intents_chat.py, ADR-002 stage 2.6).
                return
            document.add_generated_image_reply(parent_chat_node_id, prompt, image_bytes)
            await bus.publish("scene")

        await agent_dispatcher.start_image_reply(
            bus=bus,
            notifications_state=notifications,
            prompt=prompt,
            on_reply=_on_reply,
        )

    async def generate_image(chat_node_id):
        try:
            parent_chat_node_id, prompt = document.resolve_generate_image(chat_node_id)
        except SceneError as exc:
            # Two genuinely distinct SceneErrors here, NOT collapsed into one
            # generic message: SceneEmptyPromptError lets this wrapper tell
            # "empty prompt" apart from "wrong kind/unknown node" via
            # isinstance, without string-sniffing exc's own text.
            if isinstance(exc, SceneEmptyPromptError):
                notifications.show("The selected node has no text to use as a prompt.", "warning")
            else:
                notifications.show("This node can't be used to generate an image.", "warning")
            await bus.publish("notification")
            return None
        await _dispatch_image(parent_chat_node_id, prompt)
        return None

    async def regenerate_image(image_node_id):
        try:
            parent_chat_node_id, prompt = document.resolve_regenerate_image(image_node_id)
        except SceneError:
            # Unlike generate_image above, both of resolve_regenerate_image's
            # SceneErrors (unknown/wrong-kind/no-parent, and the
            # SceneEmptyPromptError empty-content variant) share ONE message
            # here - the exact wording this feature's design spec settled on.
            notifications.show("This image has no prompt to regenerate from.", "warning")
            await bus.publish("notification")
            return None
        await _dispatch_image(parent_chat_node_id, prompt)
        return None

    # R4.4a: "Generate Image from Text" (ChatNode) and "Regenerate Image"
    # (ImageNode) - two intents because the two entry points resolve from
    # genuinely different source-node kinds with different validation rules,
    # both funneling through the shared _dispatch_image helper above.
    bus.register_intent("scene", "generateImage", generate_image)
    bus.register_intent("scene", "regenerateImage", regenerate_image)
