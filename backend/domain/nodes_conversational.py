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

import copy
from datetime import datetime, timezone
from typing import Any

from backend.domain._composed import SceneDocumentParts
from backend.domain.model import CHAT_TITLE_PREVIEW_LENGTH, SceneError, SceneNode
from backend.domain.node_access import require_node
from backend.domain.node_states import ChatState, ChatVersion, MAX_CHAT_VERSIONS


# Limit one instruction before it enters scene history and model context.
# Older histories remain readable; the limit is for new user submissions.
MAX_CONVERSATION_USER_CHARS = 16_000


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
            state=ChatState(
                is_user=bool(is_user), content_parts=content_parts,
                versions=[ChatVersion(content=str(content), created_at=datetime.now(timezone.utc).isoformat())],
            ),
        )
        self.nodes[node_id] = node
        if parent_id is not None:
            self.connect(parent_id, node_id)
        else:
            self.adopt_pending_system_prompt(node_id)
        return node

    def sync_chat_version(self, node_id: str) -> None:
        """Mirror current message/provenance into the selected saved version.

        Async metadata (usage, citations and model selection) can arrive after
        the initial reply command; those callbacks call this after stamping the
        live node so navigation and session saves see the same answer.
        """
        node = require_node(self.nodes, node_id, "chat", ChatState)
        if not node.state.versions:
            # Defensive for older in-memory fixtures constructed directly.
            node.state.versions = [ChatVersion(content=node.content)]
            node.state.version_index = 0
        version = node.state.versions[node.state.version_index]
        version.content = node.content
        version.provider_name = node.state.provider
        version.model_name = node.state.model
        version.incomplete = node.state.response_incomplete
        version.input_tokens = node.state.prompt_tokens
        version.output_tokens = node.state.completion_tokens
        version.cost_usd = node.state.estimated_cost_usd
        version.calls = copy.deepcopy(node.state.tool_invocations)
        version.parts = copy.deepcopy(node.state.content_parts)
        version.history_fingerprint = node.state.reply_history_fingerprint
        version.knowledge_enabled = node.state.reply_knowledge_enabled

    def update_chat_node_content(
        self, node_id: str, content: str, incomplete: bool = False
    ) -> SceneNode:
        """Append a reply alternative rather than destroying the prior text.

        The current projection remains on SceneNode for all existing branch
        history and model-context consumers; only chat-specific history lives
        in ChatState. Regeneration adds a version even for an identical answer.
        """
        node = require_node(self.nodes, node_id, "chat", ChatState)
        self.sync_chat_version(node_id)
        new_content = str(content)
        if new_content != node.content:
            # Verified scientific receipts only describe their original text.
            node.state.science_reference = None
        node.content = new_content
        node.state.provider = None
        node.state.model = None
        # A new attempt must not inherit an older answer's input proof.
        node.state.reply_history_fingerprint = None
        node.state.reply_knowledge_enabled = None
        node.state.response_incomplete = bool(incomplete)
        node.state.prompt_tokens = None
        node.state.completion_tokens = None
        node.state.estimated_cost_usd = None
        # A new version must not discard real tool calls. Regeneration
        # replaces only the stale citation ledger via apply_reply_citations.
        node.state.content_parts = None
        node.state.versions.append(ChatVersion(
            content=new_content, incomplete=bool(incomplete),
            created_at=datetime.now(timezone.utc).isoformat(),
        ))
        node.state.versions = node.state.versions[-MAX_CHAT_VERSIONS:]
        node.state.version_index = len(node.state.versions) - 1
        return node

    def set_chat_version(self, node_id: str, index: int) -> None:
        """Select a saved alternative. The intent records this as one undo step."""
        node = require_node(self.nodes, node_id, "chat", ChatState)
        if node.pending_request_id:
            raise SceneError("Cannot switch versions while a reply is generating.")
        if type(index) is not int or not 0 <= index < len(node.state.versions):
            raise SceneError("Chat version index is out of range.")
        if index == node.state.version_index:
            return
        self.sync_chat_version(node_id)
        version = node.state.versions[index]
        node.state.version_index = index
        node.content = version.content
        node.state.provider = version.provider_name
        node.state.model = version.model_name
        node.state.response_incomplete = version.incomplete
        node.state.prompt_tokens = version.input_tokens
        node.state.completion_tokens = version.output_tokens
        node.state.estimated_cost_usd = version.cost_usd
        node.state.tool_invocations = copy.deepcopy(version.calls)
        node.state.content_parts = copy.deepcopy(version.parts)
        node.state.reply_history_fingerprint = version.history_fingerprint
        node.state.reply_knowledge_enabled = version.knowledge_enabled
        # A scientific source card is only valid for its original snapshot.
        node.state.science_reference = None

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
        if node.kind != "conversation":
            raise SceneError(f"node is not a conversation node: {node_id}")
        if not isinstance(text, str) or not text.strip():
            raise SceneError("Conversation message must be nonempty text.")
        if len(text) > MAX_CONVERSATION_USER_CHARS:
            raise SceneError(f"Conversation message is limited to {MAX_CONVERSATION_USER_CHARS} characters.")
        node.history.append({"role": "user", "content": text})
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
        if type(message_index) is not int or message_index < 0 or message_index >= len(node.history):
            raise SceneError("Conversation message index must be a valid nonnegative integer.")
        node.history.pop(message_index)

    def send_conversation_message(self, node_id: str, text: str) -> SceneNode:
        """The Conversation node's own Send action (R3.25): a thin wrapper
        over append_conversation_user_message, kept as a separate method
        (rather than only calling append_conversation_user_message directly
        from the WS wrapper) so the WS intent name lines up 1:1 with the
        domain method, the same way sendMessage/send_message already do for
        ChatNode."""
        return self.append_conversation_user_message(node_id, text)
