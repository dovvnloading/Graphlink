"""ADR-002 stage 2.6: Artifact/Drafter node send/cancel.

Relocated VERBATIM from backend/canvas.py's former register_canvas
(closures at lines 373-405; registration calls from the former tail block
at lines 1144-1150) - pure code motion, no behavior change. Node creation
itself lives in backend/plugins.py's executePlugin (the "Artifact /
Drafter" branch), not here - these two intents drive an EXISTING
artifact-kind node, same posture as Web Research's own runWebResearch/
cancelWebResearchRequest pair (backend/api/intents_web_research.py,
ADR-002 stage 2.6).
"""

from __future__ import annotations

from backend.agents import AgentDispatcher
from backend.api._shared import make_publish_scene
from backend.domain.graph import SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState


def register_artifact_intents(
    bus: SessionBus,
    document: SceneDocument,
    notifications: NotificationState,
    agent_dispatcher: AgentDispatcher,
) -> None:
    publish_scene = make_publish_scene(bus)

    async def send_artifact_message(node_id, text):
        # R5.2: the Artifact node's own Send action - appends a real user
        # instruction, then dispatches a real agent reply through
        # AgentDispatcher.start_artifact_reply. No try/except SceneError guard
        # here (an unknown node_id propagates as a generic WS intent error) -
        # same posture as send_conversation_message (backend/api/
        # intents_conversation.py, ADR-002 stage 2.6), not
        # run_web_research's defensive pre-check pattern (backend/api/
        # intents_web_research.py): there is no persisted progress/error
        # state on this node that an unguarded call could corrupt, so a
        # stale click racing a delete has nothing destructive to protect
        # against.
        node = document.send_artifact_message(node_id, text)
        await publish_scene()

        parent_edge = document._branch_parent_edge(node_id)
        branch_history = document.chat_branch_history(parent_edge.source) if parent_edge else []
        full_history = branch_history + node.history

        def _on_reply(new_content, ai_message):
            document.complete_artifact_generation(node_id, new_content, ai_message)

        await agent_dispatcher.start_artifact_reply(
            bus=bus,
            notifications_state=notifications,
            node=node,
            current_artifact=node.artifact_content,
            history=full_history,
            on_reply=_on_reply,
        )
        return node.id

    async def cancel_artifact_request(request_id):
        agent_dispatcher.cancel_artifact(request_id)

    # R5.2: Artifact/Drafter Send/cancel - node creation itself lives in
    # backend/plugins.py's executePlugin (the "Artifact / Drafter" branch),
    # not here; these two intents drive an EXISTING artifact-kind node, same
    # posture as Web Research's own runWebResearch/cancelWebResearchRequest
    # pair above.
    bus.register_intent("scene", "sendArtifactMessage", send_artifact_message)
    bus.register_intent("scene", "cancelArtifactRequest", cancel_artifact_request)
