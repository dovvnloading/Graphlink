"""ADR-002 stage 2.6: ConversationNode lifecycle intents.

Relocated VERBATIM from backend/canvas.py's former register_canvas
(closures at lines 384-471; registration calls from the former tail block
at lines 1688-1701) - pure code motion, no behavior change.
"""

from __future__ import annotations

from backend.agents import AgentDispatcher
from backend.api._shared import make_publish_scene
from backend.domain.graph import SceneDocument
from backend.domain.model import SceneError, SceneNode
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
        """Commit only admitted user turns and bind replies to their context.

        A shared chat request may be refused because the Composer is busy
        or the provider is unconfigured. Both checks happen in _dispatch
        BEFORE its synchronous on_claim hook; no phantom unsent message is
        saved and the UI can retain the user's text on refusal.
        """
        node = document.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "conversation":
            raise SceneError(f"node is not a conversation node: {node_id}")
        # Store the narrowed, non-optional object under a stable name for
        # callbacks that run after this scope returns.
        bound_node: SceneNode = node

        accepted = False
        sent_history: list[dict] = []

        def _on_claim() -> list[dict]:
            nonlocal accepted, sent_history
            document.record_command(
                "sendConversationMessage", "user",
                lambda: document.send_conversation_message(node_id, text),
                node_ids=[node_id],
            )
            sent_history = [message.copy() for message in bound_node.history]
            accepted = True
            # A frozen snapshot is passed to the provider, not the mutable
            # live history whose messages may later be edited or deleted.
            return [message.copy() for message in sent_history]

        def _still_current() -> bool:
            return (
                document.nodes.get(node_id) is bound_node
                and bound_node.pending_request_id is not None
                and bound_node.history == sent_history
            )

        def _on_reply(reply_text):
            # A cancelled request or changed history must not append an
            # answer to a different question, restored node, or undo state.
            if not _still_current():
                return
            document.record_command(
                "appendConversationAssistantMessage", "agent",
                lambda: document.append_conversation_assistant_message(node_id, reply_text),
                node_ids=[node_id],
            )

        def _on_partial(partial_text):
            # Partial output only belongs to the exact still-live context
            # that produced it; the dispatcher handles request cancellation.
            if not _still_current():
                return
            document.record_command(
                "appendConversationAssistantMessage", "agent",
                lambda: document.append_conversation_assistant_message(
                    node_id, partial_text, incomplete=True
                ),
                node_ids=[node_id],
            )

        await agent_dispatcher.start_conversation_reply(
            bus=bus,
            notifications_state=notifications,
            node=bound_node,
            conversation_history=bound_node.history,
            on_reply=_on_reply,
            on_partial=_on_partial,
            on_claim=_on_claim,
            canvas_document=document,
            node_id=node_id,
        )
        if not accepted:
            return None
        await publish_scene()
        return node_id

    async def append_conversation_assistant_message(node_id, text):
        # Unlike send_conversation_message, this represents a real reply
        # landing once ConversationNode gets real agent dispatch, not a
        # deferral - so no notification fires. This is the standalone
        # registered intent (a direct WS call), distinct from _on_reply's
        # own use of the same document method above - "user" provenance
        # here since nothing marks this specific entry point as agent-driven.
        node, _command = document.record_command(
            "appendConversationAssistantMessage", "user",
            lambda: document.append_conversation_assistant_message(node_id, text),
            node_ids=[node_id],
        )
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
        document.record_command(
            "setNodeDocked", "user", lambda: document.set_node_docked(node_id, docked),
            node_ids=[node_id],
        )
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
        document.record_command(
            "setChatCollapsed", "user", lambda: document.set_chat_collapsed(node_id, collapsed),
            node_ids=[node_id],
        )
        await publish_scene()

    # ADR-002 Workstream 1 ("Branch status and lifecycle"): three plain
    # setter intents, same "no try/except SceneError guard, a bad node_id
    # propagates as a generic WS intent error" posture as set_chat_collapsed
    # immediately above (see send_artifact_message's own comment on this
    # accepted pattern for simple setters, as opposed to the defensive
    # pre-check pattern used where a delete could realistically race an
    # in-flight agent dispatch - none of these three ever dispatch an agent).
    async def set_branch_status(node_id, status):
        document.record_command(
            "setBranchStatus", "user", lambda: document.set_branch_status(node_id, status),
            node_ids=[node_id],
        )
        await publish_scene()

    async def set_final_deliverable(node_id, is_final):
        document.record_command(
            "setFinalDeliverable", "user",
            lambda: document.set_final_deliverable(node_id, is_final),
            node_ids=[node_id],
        )
        await publish_scene()

    async def collapse_branch(node_id, collapsed):
        # Collapse Branch mutates *every* chat descendant in-place, not just
        # its root. Snapshot all of them so Ctrl+Z restores each old flag,
        # including when the root was already in the requested state.
        document.record_command(
            "collapseBranch", "user", lambda: document.collapse_branch(node_id, collapsed),
            node_ids=document._chat_subtree_ids(node_id),
        )
        await publish_scene()

    async def collapse_all_nodes():
        # A bulk mutation across potentially every chat/conversation/html
        # node in the scene - same shape as the already-wrapped
        # organizeNodes, so every node id is named for the same reason:
        # record_command's diff needs to be told what MIGHT change to
        # capture an in-place mutation (an id that never enters/leaves
        # self.nodes on its own gives the id-set diff nothing to see).
        document.record_command(
            "collapseAllNodes", "user",
            lambda: document.set_all_conversational_collapsed(True),
            node_ids=list(document.nodes.keys()),
        )
        await publish_scene()

    async def expand_all_nodes():
        document.record_command(
            "expandAllNodes", "user",
            lambda: document.set_all_conversational_collapsed(False),
            node_ids=list(document.nodes.keys()),
        )
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
