"""ConversationalOps - the SceneDocument methods for the chat and
conversation node kinds.

A MIXIN, composed exactly once, by backend/domain/graph.py's
SceneDocument. Method bodies are relocated VERBATIM from graph.py;
only the class wrapper, its docstring and the imports are new, and the
methods are regrouped by kind rather than left in the order successive
increments happened to append them in.

See backend/domain/nodes_code_review.py's docstring for why the
per-kind method groups are being lifted out of SceneDocument at all.
"""

from __future__ import annotations

from typing import Any

from backend.domain._composed import SceneDocumentParts
from backend.domain.model import CHAT_TITLE_PREVIEW_LENGTH, SceneError, SceneNode
from backend.domain.node_access import require_node
from backend.domain.node_states import ChatState


class ConversationalOps(SceneDocumentParts):
    """The two message-threaded kinds: chat nodes (one bubble each) and
    conversation nodes (a whole thread in one node).

    They are the same family - `set_all_conversational_collapsed` on
    SceneDocument names it - and they share the history/content_parts
    bookkeeping that no other kind has.
    """

    def add_chat_node(
        self,
        x: float,
        y: float,
        content: str,
        is_user: bool,
        parent_id: str | None = None,
        content_parts: list[dict[str, Any]] | None = None,
    ) -> SceneNode:
        """The Qt-free ChatScene.add_chat_node equivalent: a real message-
        bubble node, optionally connected to a parent (the branch it
        continues). Mirrors add_node's id/dict bookkeeping; the only new
        behavior is the parent-edge, ported from the legacy scene's own
        ConnectionItem creation.

        R8a: content_parts is the real multimodal attachment payload
        (image_bytes/audio_file parts) - the data-model capability
        ChatState.content_parts (backend/domain/node_states.py) has carried
        since R6.3, finally populated by a real caller. Optional and
        additive: every existing caller keeps
        passing only (x, y, content, is_user, parent_id) and gets exactly
        the plain-text node it always did."""
        if parent_id is not None and parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        title = content[:CHAT_TITLE_PREVIEW_LENGTH] or ("You" if is_user else "Assistant")
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title=title,
            kind="chat",
            content=str(content),
            state=ChatState(is_user=bool(is_user), content_parts=content_parts),
        )
        self.nodes[node_id] = node
        if parent_id is not None:
            self.connect(parent_id, node_id)
        else:
            self.adopt_pending_system_prompt(node_id)
        return node

    def update_chat_node_content(
        self, node_id: str, content: str, incomplete: bool = False
    ) -> SceneNode:
        """The regenerate primitive: mutate an EXISTING chat node's content in
        place - the first in-place mutation of a content-bearing field in this
        file (move_node/set_chat_collapsed/set_node_docked all mutate a
        position/flag, never displayed text). Scope confirmed against legacy's
        ChatNode.update_content (graphlink_nodes/graphlink_node_chat.py:677-686):
        sets content ONLY. Does not touch title (legacy's update_content never
        recomputes any title-like state either, and every other in-place mutator
        here already leaves title untouched post-creation - consistent, not a
        new carve-out). Does not touch is_user/is_collapsed/kind.

        ADR-006 stage 6.4: `incomplete` marks a PARTIAL reply committed after
        its stream died (see ChatState.response_incomplete). A normal full
        regenerate passes the default False, which doubles as the CLEAR for a
        previously interrupted node - retry succeeds, banner goes away."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.content = str(content)
        if isinstance(node.state, ChatState):
            node.state.response_incomplete = bool(incomplete)
        return node

    def set_chat_collapsed(self, node_id: str, collapsed: bool) -> None:
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.is_collapsed = bool(collapsed)
        # R6.1: unlike every other kind this generic setter already served,
        # a frame/container's is_collapsed also drives derived geometry
        # (the collapsed pill size vs the auto-fit/manual bbox) - recompute
        # here too so this stays correct no matter which entry point sets
        # it, rather than only being safe via toggle_group_collapsed.
        if node.kind in ("frame", "container"):
            self._recompute_group_bounds(node_id)

    def set_chat_scroll_value(self, node_id: str, value: float) -> None:
        """R6.3: persists a chat node's own scroll position within its
        content area. chat kind only (SceneError otherwise), matching every
        other kind-specific setter's guard pattern in this file (e.g.
        resize_chart/toggle_frame_lock)."""
        node = require_node(self.nodes, node_id, "chat", ChatState)
        node.state.chat_scroll_value = float(value)

    # -- conversation node (a full thread in a single node) ------------------

    def add_conversation_node(self, x: float, y: float, parent_id: str | None) -> SceneNode:
        """R3.25's ConversationNode equivalent of add_document_node/
        add_thinking_node/add_html_node/add_image_node: a real multi-message
        conversation node. Same as document/thinking/html/image (and unlike
        chat/code), parent_id is REQUIRED, not optional - a ConversationNode
        never exists unparented - so this unconditionally connects to its
        parent, no `if parent_id` guard.

        Title is always the fixed literal "Conversation" - never derived or
        truncated from any content, unlike every scalar-content kind before
        it (chat/thinking/html/image all preview their own text). There is
        no natural single preview string for a node whose content is a
        growing LIST of messages, so the title never changes as messages are
        appended (see append_conversation_user_message/
        append_conversation_assistant_message below - neither touches
        title). Mirrors graphlink_conversation_node.py's `title_label =
        QLabel("Conversation")`, a hardcoded literal, not derived state.

        `history` starts empty - a freshly-created conversation node has no
        messages yet, same posture as `is_docked` defaulting False on a
        freshly-created thinking node.

        Conversation nodes are also NOT branch points (same as code/document/
        thinking/html/image): there is no delete_conversation_node; deletion
        goes entirely through the existing generic remove_nodes.
        """
        if parent_id is not None and parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title="Conversation",
            kind="conversation",
        )
        self.nodes[node_id] = node
        if parent_id is not None:
            self.connect(parent_id, node_id)
        return node

    def append_conversation_user_message(self, node_id: str, text: str) -> SceneNode:
        """Append a real user message to a conversation node's history -
        mirrors graphlink_conversation_node.py's add_user_message, minus the
        view-layer bubble creation (the frontend's job)."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.history.append({"role": "user", "content": str(text)})
        return node

    def append_conversation_assistant_message(
        self, node_id: str, text: str, incomplete: bool = False
    ) -> SceneNode:
        """Append a real assistant message to a conversation node's history -
        mirrors graphlink_conversation_node.py's add_ai_message, minus the
        view-layer bubble creation.

        ADR-006 stage 6.4: `incomplete=True` marks a PARTIAL reply whose
        stream died mid-generation (H5 - the accumulated text is preserved
        instead of lost). The key is only written when set - completed
        messages keep their exact two-key {role, content} shape, so every
        existing history consumer (session round-trip, agent context
        assembly) sees byte-identical data for the normal path."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        message: dict[str, Any] = {"role": "assistant", "content": str(text)}
        if incomplete:
            message["incomplete"] = True
        node.history.append(message)
        return node

    def delete_conversation_message(self, node_id: str, message_index: int) -> None:
        """Prune one message out of a conversation node's history by index -
        mirrors graphlink_conversation_node.py's _remove_message's index-
        synced pop, minus the view-layer bubble removal/re-layout (the
        frontend's job)."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if message_index < 0 or message_index >= len(node.history):
            raise SceneError(f"message index out of range: {message_index}")
        node.history.pop(message_index)

    def send_conversation_message(self, node_id: str, text: str) -> SceneNode:
        """The Conversation node's own Send action (R3.25): a thin wrapper
        over append_conversation_user_message, kept as a separate method
        (rather than only calling append_conversation_user_message directly
        from the WS wrapper) so the WS intent name lines up 1:1 with the
        domain method, the same way sendMessage/send_message already do for
        ChatNode."""
        return self.append_conversation_user_message(node_id, text)
