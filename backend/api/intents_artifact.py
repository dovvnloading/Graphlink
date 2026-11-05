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

from backend.agents import AgentDispatcher, _NODE_RUN_CLAIM_PLACEHOLDER
from backend.domain.node_access import require_node
from backend.domain.node_states import ArtifactState
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
        node = require_node(document.nodes, node_id, "artifact", ArtifactState)
        if node.state.artifact_proposal_id:
            notifications.show("Review or discard the pending Artifact proposal before generating again.", "info")
            await bus.publish("notification")
            return None
        # Guard before appending to history: otherwise an extra Send while the
        # model is busy adds an instruction that was never dispatched.
        # A cancelled handle is no longer live even while its old request id
        # remains on the node until the worker unwinds.
        is_live = getattr(agent_dispatcher, "is_node_run_live", None)
        if node.pending_request_id and (
            is_live(node.pending_request_id) if callable(is_live) else True
        ):
            notifications.show("Artifact generation is already running for this node.", "info")
            await bus.publish("notification")
            return None

        # Claim synchronously before publish_scene's first await, the same
        # placeholder contract used by the other per-node async tools.
        node.pending_request_id = _NODE_RUN_CLAIM_PLACEHOLDER
        try:
            node, _command = document.record_command(
                "sendArtifactMessage", "user",
                lambda: document.send_artifact_message(node_id, text),
                node_ids=[node_id],
            )
        except Exception:
            if node.pending_request_id == _NODE_RUN_CLAIM_PLACEHOLDER:
                node.pending_request_id = None
            raise
        draft_at_send = node.state.artifact_content
        history_at_send = [message.copy() for message in node.history]
        await publish_scene()

        parent_edge = document._branch_parent_edge(node_id)
        branch_history = document.chat_branch_history(parent_edge.source) if parent_edge else []
        full_history = branch_history + node.history

        def _on_reply(new_content, ai_message):
            current_node = document.nodes.get(node_id)
            if current_node is not node:
                # Deleted/replaced mid-flight: a stale model result must
                # never land on an unrelated node with the same id.
                return
            if (
                current_node.state.artifact_content != draft_at_send
                or current_node.history != history_at_send
            ):
                raise RuntimeError(
                    "The Artifact changed while generation was running. "
                    "The new response was discarded to preserve your edits; retry from the latest draft."
                )
            # Same content-mutation shape as intents_chat.py's chatReply /
            # intents_conversation.py's appendConversationAssistantMessage -
            # "agent" provenance since this is a model-produced reply, not a
            # direct user action.
            document.record_command(
                "stageArtifactProposal", "agent",
                lambda: document.stage_artifact_proposal(
                    node_id, new_content, ai_message, draft_at_send,
                ),
                node_ids=[node_id],
            )

        def _on_failure(message):
            # Do not attach a stale error to a deleted or replaced node.
            if document.nodes.get(node_id) is not node:
                return
            document.record_command(
                "failArtifactGeneration", "agent",
                lambda: document.fail_artifact_generation(node_id, message),
                node_ids=[node_id],
            )

        await agent_dispatcher.start_artifact_reply(
            bus=bus,
            notifications_state=notifications,
            node=node,
            on_failure=_on_failure,
            current_artifact=node.state.artifact_content,
            history=full_history,
            on_reply=_on_reply,
        )
        return node.id

    async def set_artifact_content(node_id, new_content, expected_content):
        # User editing is undoable and compare-and-swap guarded: a stale
        # browser tab may not overwrite a newer model result or undo.
        document.record_command(
            "setArtifactContent", "user",
            lambda: document.set_artifact_content(node_id, new_content, expected_content),
            node_ids=[node_id],
        )
        await publish_scene()
        return node_id

    async def fetch_artifact_proposal(node_id):
        # Explicit lazy fetch: the scene wire exposes only the proposal id.
        return document.fetch_artifact_proposal(node_id)

    async def accept_artifact_proposal(node_id, proposal_id):
        document.record_command(
            "acceptArtifactProposal", "user",
            lambda: document.accept_artifact_proposal(node_id, proposal_id),
            node_ids=[node_id],
        )
        await publish_scene()
        return node_id

    async def discard_artifact_proposal(node_id, proposal_id):
        document.record_command(
            "discardArtifactProposal", "user",
            lambda: document.discard_artifact_proposal(node_id, proposal_id),
            node_ids=[node_id],
        )
        await publish_scene()
        return node_id

    async def cancel_artifact_request(request_id):
        agent_dispatcher.cancel_artifact(request_id)

    # R5.2: Artifact/Drafter Send/cancel - node creation itself lives in
    # backend/plugins.py's executePlugin (the "Artifact / Drafter" branch),
    # not here; these two intents drive an EXISTING artifact-kind node, same
    # posture as Web Research's own runWebResearch/cancelWebResearchRequest
    # pair above.
    bus.register_intent("scene", "sendArtifactMessage", send_artifact_message)
    bus.register_intent("scene", "setArtifactContent", set_artifact_content)
    bus.register_intent("scene", "fetchArtifactProposal", fetch_artifact_proposal)
    bus.register_intent("scene", "acceptArtifactProposal", accept_artifact_proposal)
    bus.register_intent("scene", "discardArtifactProposal", discard_artifact_proposal)
    bus.register_intent("scene", "cancelArtifactRequest", cancel_artifact_request)
