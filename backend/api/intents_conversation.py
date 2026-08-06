"""ADR-002 stage 2.6: ConversationNode lifecycle intents.

Relocated VERBATIM from backend/canvas.py's former register_canvas
(closures at lines 384-471; registration calls from the former tail block
at lines 1688-1701) - pure code motion, no behavior change.
"""

from __future__ import annotations

from backend.agents import AgentDispatcher
from backend.api._shared import make_publish_scene
from backend.domain.graph import SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState


def register_conversation_intents(
    bus: SessionBus,
    document: SceneDocument,
    notifications: NotificationState,
    agent_dispatcher: AgentDispatcher,
) -> None:
    publish_scene = make_publish_scene(bus)

    async def send_conversation_message(node_id, text):
        # R4.3: the real user-message-send action for a conversation node -
        # appends a real user message, then dispatches a real agent reply
        # through AgentDispatcher.start_conversation_reply, the ConversationNode
        # counterpart of send_message's ChatNode dispatch (backend/api/
        # intents_chat.py, ADR-002 stage 2.6). The reply lands via _on_reply
        # calling document.append_conversation_assistant_message directly -
        # same established relationship as send_message's own _on_reply
        # calling document.add_chat_node directly.
        node = document.send_conversation_message(node_id, text)
        await publish_scene()

        def _on_reply(reply_text):
            # R4.3b: deliberate, confirmed-correct omission, NOT an oversight -
            # ConversationNode is exempt from the response_parsing retrofit
            # applied to send_message's _on_reply (backend/api/intents_chat.py).
            # The true legacy
            # handler for a conversation node's reply is
            # graphlink_window_actions.py's WindowActionsMixin.
            # handle_conversation_node_response (NOT handle_response), which
            # just calls target_node.add_ai_message(response_text) directly -
            # it never calls self._parse_response and never creates any
            # child node. A ConversationNode is a self-contained mega-node
            # with a flat plain-text-only history and no child-node concept
            # at all in legacy.
            document.append_conversation_assistant_message(node_id, reply_text)

        await agent_dispatcher.start_conversation_reply(
            bus=bus,
            notifications_state=notifications,
            node=node,
            conversation_history=node.history,
            on_reply=_on_reply,
        )
        return node.id

    async def append_conversation_assistant_message(node_id, text):
        # Unlike send_conversation_message, this represents a real reply
        # landing once ConversationNode gets real agent dispatch, not a
        # deferral - so no notification fires.
        node = document.append_conversation_assistant_message(node_id, text)
        await publish_scene()
        return node.id

    async def delete_conversation_message(node_id, message_index):
        # A delete at sub-node granularity: one message inside a
        # ConversationNode's history list, not a SceneNode. node_ids is
        # required because the node itself survives - the id never leaves
        # self.nodes, so only the in-place before/after diff catches this.
        document.record_command(
            "deleteConversationMessage", "user",
            lambda: document.delete_conversation_message(node_id, message_index),
            node_ids=[node_id],
        )
        await publish_scene()

    async def set_node_docked(node_id, docked):
        document.set_node_docked(node_id, docked)
        await publish_scene()

    async def delete_chat_node(node_id):
        # The SECOND, structurally different delete path (the first being
        # remove_nodes): this REPARENTS the deleted node's children onto its
        # own parent rather than cascade-deleting them, so undoing it has to
        # restore both the node AND every child's original parent edge. The
        # children are named explicitly for that reason - they are mutated
        # (re-parented), not deleted, so the id-set diff alone would miss
        # them. Their new edges are auto-discovered from the ids named here.
        children = [
            edge.target for edge in document.edges.values() if edge.source == node_id
        ]
        parents = [
            edge.source for edge in document.edges.values() if edge.target == node_id
        ]
        document.record_command(
            "deleteChatNode", "user", lambda: document.delete_chat_node(node_id),
            node_ids=[node_id, *children, *parents],
        )
        await publish_scene()

    async def set_chat_collapsed(node_id, collapsed):
        document.set_chat_collapsed(node_id, collapsed)
        await publish_scene()

    # ADR-002 Workstream 1 ("Branch status and lifecycle"): three plain
    # setter intents, same "no try/except SceneError guard, a bad node_id
    # propagates as a generic WS intent error" posture as set_chat_collapsed
    # immediately above (see send_artifact_message's own comment on this
    # accepted pattern for simple setters, as opposed to the defensive
    # pre-check pattern used where a delete could realistically race an
    # in-flight agent dispatch - none of these three ever dispatch an agent).
    async def set_branch_status(node_id, status):
        document.set_branch_status(node_id, status)
        await publish_scene()

    async def set_final_deliverable(node_id, is_final):
        document.set_final_deliverable(node_id, is_final)
        await publish_scene()

    async def collapse_branch(node_id, collapsed):
        document.collapse_branch(node_id, collapsed)
        await publish_scene()

    async def collapse_all_nodes():
        document.set_all_conversational_collapsed(True)
        await publish_scene()

    async def expand_all_nodes():
        document.set_all_conversational_collapsed(False)
        await publish_scene()

    async def set_chat_scroll_value(node_id, value):
        document.set_chat_scroll_value(node_id, value)
        await publish_scene()

    bus.register_intent("scene", "sendConversationMessage", send_conversation_message)
    bus.register_intent(
        "scene", "appendConversationAssistantMessage", append_conversation_assistant_message
    )
    bus.register_intent("scene", "deleteConversationMessage", delete_conversation_message)
    bus.register_intent("scene", "setNodeDocked", set_node_docked)
    bus.register_intent("scene", "deleteChatNode", delete_chat_node)
    bus.register_intent("scene", "setChatCollapsed", set_chat_collapsed)
    bus.register_intent("scene", "setBranchStatus", set_branch_status)
    bus.register_intent("scene", "setFinalDeliverable", set_final_deliverable)
    bus.register_intent("scene", "collapseBranch", collapse_branch)
    bus.register_intent("scene", "collapseAllNodes", collapse_all_nodes)
    bus.register_intent("scene", "expandAllNodes", expand_all_nodes)
    bus.register_intent("scene", "setChatScrollValue", set_chat_scroll_value)
