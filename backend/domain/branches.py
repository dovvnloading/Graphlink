"""BranchOps - branch/conversation-tree semantics for SceneDocument
(ADR-002 stage 2.2, slice 3).

A MIXIN, not a standalone class: every method operates on the composing
dataclass's own state (self.nodes/self.edges/self.last_chat_node_id/
self.final_deliverable_node_id) and calls sibling methods that live on
the other mixins or the core (_recompute_group_bounds,
_detach_node_from_membership, add_chat_node, remove_nodes). It is
composed exactly once, by domain/graph.py's
`class SceneDocument(BranchOps, GroupOps)` - see that module's docstring.

Method bodies are relocated VERBATIM from domain/graph.py (themselves
relocated verbatim from backend/canvas.py in slice 2); only the class
wrapper is new. BRANCH_STATUS_VALUES moves here as a class attribute -
its two reference styles (`self.BRANCH_STATUS_VALUES` in
set_branch_status, `SceneDocument.BRANCH_STATUS_VALUES` in
backend/session_load.py) both resolve through the MRO unchanged.
"""

from __future__ import annotations

from typing import Any

from backend.domain.model import (
    BRANCH_HORIZONTAL_SPACING,
    MESSAGE_VERTICAL_SPACING,
    SceneEdge,
    SceneEmptyPromptError,
    SceneError,
    SceneNode,
)


class BranchOps:

    def mark_branch_comparison_note(self, node_id: str, source_node_ids: list[str]) -> None:
        """ADR-002 Workstream 1 ("Compare Branches"): stamps an already-created
        note as the output of the Compare Branches agent and records which
        branches it compared - called once, immediately after add_note +
        set_note_content, mirroring set_group_color's own "extra setter call
        right after creation" shape (see the WS intent wrapper in
        register_canvas)."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "note":
            raise SceneError(f"node is not a note node: {node_id}")
        node.state.is_branch_comparison = True
        node.item_ids = list(source_node_ids)

    def mark_branch_synthesis(
        self,
        node_id: str,
        source_node_ids: list[str],
        instructions: str,
        provider: str | None,
        model: str | None,
    ) -> None:
        """ADR-002 Workstream 1 ("Synthesize Branches"): stamps an
        already-created CHAT node as the output of the Synthesize Branches
        agent - mirrors mark_branch_comparison_note's own "extra setter call
        right after creation" shape, adapted for a chat-kind result instead
        of a note-kind one (see ChatState's own comment, backend/domain/
        node_states.py, for why this is a distinct method/flag rather than
        reusing Compare Branches')."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "chat":
            raise SceneError(f"node is not a chat node: {node_id}")
        node.state.is_branch_synthesis = True
        node.item_ids = list(source_node_ids)
        node.state.synthesis_instructions = str(instructions)
        node.state.provider = provider
        node.state.model = model

    #: ADR-002 Workstream 1 ("Branch status and lifecycle"): the exactly-4
    #: legal values for ChatState's own branch_status (backend/domain/
    #: node_states.py) - shared by the setter's validation and
    #: session_load.py's own defensive downgrade-to-"active" read-back, so
    #: the one legal set is never duplicated out of sync.
    BRANCH_STATUS_VALUES = frozenset({"active", "accepted", "rejected", "superseded"})

    def set_branch_status(self, node_id: str, status: str) -> None:
        """ADR-002 Workstream 1 ("Branch status and lifecycle"): marks a
        single chat node's own branch_status - deliberately no "has
        siblings" / "is a fork root" requirement (any chat node may be
        marked, mirroring mark_branch_comparison_note's own kind-only
        validation), and deliberately no side effect on any OTHER node -
        marking one branch Accepted does not auto-reject its siblings, the
        first cross-node side-effecting setter would have been a new kind
        of mutation nothing else in this file does, and Synthesize Branches
        already established that 2+ branches can be simultaneously
        legitimate (its own item_ids records multiple sources at once) -
        forcing exclusivity here would fight that existing workflow."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "chat":
            raise SceneError(f"node is not a chat node: {node_id}")
        status = str(status)
        if status not in self.BRANCH_STATUS_VALUES:
            raise SceneError(f"invalid branch status: {status}")
        node.state.branch_status = status

    def set_final_deliverable(self, node_id: str, is_final: bool) -> None:
        """ADR-002 Workstream 1 ("Branch status and lifecycle"): sets or
        clears final_deliverable_node_id - EXCLUSIVE by construction (the
        single-pointer shape means marking a new node silently supersedes
        whichever one held it before; no separate "clear the old one" step
        needed, unlike a per-node flag would require). Orthogonal to
        branch_status on purpose - no validation ties them together (a
        "rejected" node CAN technically be marked Final Deliverable; this
        is not blocked, though not a realistic path either)."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "chat":
            raise SceneError(f"node is not a chat node: {node_id}")
        if is_final:
            self.final_deliverable_node_id = node_id
        elif self.final_deliverable_node_id == node_id:
            self.final_deliverable_node_id = None

    def set_model_override(self, node_id: str, provider: str, model_id: str) -> None:
        """ADR-018 stage 18.2: pin the model this node (and, when it is a
        branch root, every descendant that doesn't pin its own - see
        resolve_model_for_node's own docstring) resolves to. Both fields
        write together, mirroring set_group_color's own "no partial value"
        posture - a pin is a real (provider, model_id) pair or nothing."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "chat":
            raise SceneError(f"node is not a chat node: {node_id}")
        provider = str(provider or "").strip()
        model_id = str(model_id or "").strip()
        if not provider or not model_id:
            raise SceneError("set_model_override requires both provider and model_id")
        node.state.override_provider = provider
        node.state.override_model_id = model_id

    def clear_model_override(self, node_id: str) -> None:
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "chat":
            raise SceneError(f"node is not a chat node: {node_id}")
        node.state.override_provider = ""
        node.state.override_model_id = ""

    def set_chat_index_into_knowledge(self, node_id: str, enabled: bool) -> None:
        """ADR-017 stage 17.5: sets the branch-indexing opt-in flag - see
        backend/domain/node_states.py's own comment on ChatState.
        index_into_knowledge for what this flag means and why it lives on
        the node it's set on (the caller's job to pass the branch ROOT's
        node_id, mirroring set_model_override's own "branch root" framing
        for override fields). PURE - this method only flips the flag; the
        actual one-time indexing pass over the branch's text is
        backend/api/intents_knowledge.py's own job (that intent calls
        chat_branch_history() + backend.knowledge_ingest.ingest_text()
        BEFORE calling this method, so a document I/O failure never leaves
        the flag set without the indexing having actually happened) -
        mirroring this whole file's own "SceneDocument mutates the graph,
        intents own the side effects" separation (chat_library persistence
        is owned by intents_chat_library.py, never by graph.py/branches.py
        directly)."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "chat":
            raise SceneError(f"node is not a chat node: {node_id}")
        node.state.index_into_knowledge = bool(enabled)

    def resolve_model_for_node(self, node_id: str | None):
        """ADR-018 stage 18.2: the node-override -> branch-override half of
        graphlink_model_catalog.resolve_model_ref's chain - returns
        (node_ref, branch_ref), both `ModelRef | None`, for the caller
        (backend/agents.py's _dispatch) to pass straight through to that
        function alongside the workspace-default/catalog/policy it already
        owns. Mirrors _resolve_branch_system_prompt's own root-walk shape
        exactly (get_branch_root, then read one field off the root) - the
        two features share the same "root pin, inherited down the branch"
        semantics, just against a different field."""
        from graphlink_model_catalog import ModelRef

        if node_id is None:
            return None, None
        node = self.nodes.get(node_id)
        if node is None:
            return None, None

        def _ref_from(candidate) -> "ModelRef | None":
            state = getattr(candidate, "state", None)
            provider = getattr(state, "override_provider", "") if state is not None else ""
            model_id = getattr(state, "override_model_id", "") if state is not None else ""
            return ModelRef(provider, model_id) if provider and model_id else None

        node_ref = _ref_from(node) if node.kind == "chat" else None
        root = self.get_branch_root(node_id)
        branch_ref = _ref_from(root) if root is not None and root.id != node_id and root.kind == "chat" else None
        return node_ref, branch_ref

    def _branch_parent_edge(self, node_id: str) -> SceneEdge | None:
        """R6.1: the shared 'find the edge whose target == node_id' lookup
        chat_branch_history/get_branch_root/regenerate_response/
        delete_chat_node all use to walk BRANCH structure (parent -> child,
        source -> target) - factored out once this increment introduced a
        second, unrelated edge shape that can also target a chat node: a
        System Prompt note's note -> root edge (backend/plugins.py's
        "System Prompt" branch, direction confirmed against
        backend/agents.py's _resolve_branch_system_prompt). That edge is
        METADATA (which note decorates this root) - a note is never a real
        branch "parent", so a branch history/root walk must never traverse
        through it (doing so would both corrupt chat_branch_history's real
        conversation_history with the note's own content as a fake turn, AND
        make get_branch_root resolve to the note instead of the true chat
        root, silently defeating the override it exists to find). Skips
        edges whose source is a kind="note" node for exactly that reason;
        otherwise identical to the plain `next((e for e in self.edges.
        values() if e.target == node_id), None)` pattern this replaces."""
        for edge in self.edges.values():
            if edge.target != node_id:
                continue
            source_node = self.nodes.get(edge.source)
            if source_node is not None and source_node.kind == "note":
                continue
            return edge
        return None

    def _chat_subtree_ids(self, root_id: str) -> list[str]:
        """ADR-002 Workstream 1 ("Branch status and lifecycle"): the
        DOWNWARD counterpart to _branch_parent_edge's upward walk above -
        every chat-kind node's id in root_id's own subtree (root_id
        itself, plus every descendant reachable through chat-kind edges),
        via BFS. No such downward/forward walk existed anywhere in this
        file before this feature - every other edge scan here is either
        this file's own upward parent-walk pattern or an explicitly
        one-hop-only scan (delete_chat_node's direct-children reparent,
        send_message's sibling-fan-out count) - so this is new, not a
        rename of something existing. Chat-kind only: a code/document/
        thinking/image node hanging directly off a chat node in this
        subtree is NOT included - collapsing a branch does not cascade
        into its content children, matching how a single chat node's own
        collapse already never cascades into ITS children either."""
        result = [root_id]
        visited = {root_id}
        frontier = [root_id]
        while frontier:
            next_frontier = []
            for nid in frontier:
                for edge in self.edges.values():
                    if edge.source != nid:
                        continue
                    target = self.nodes.get(edge.target)
                    if target is None or target.kind != "chat" or target.id in visited:
                        continue
                    visited.add(target.id)
                    result.append(target.id)
                    next_frontier.append(target.id)
            frontier = next_frontier
        return result

    def delete_chat_node(self, node_id: str) -> None:
        """Delete one chat node WITHOUT orphaning its branch: children are
        re-parented to the deleted node's own parent (or become roots if it
        had none), mirroring ChatScene.delete_chat_node's load-bearing
        reparent rule - a plain remove_nodes cascade-delete would sever every
        child branch instead of splicing them back together."""
        if node_id not in self.nodes:
            raise SceneError(f"unknown node: {node_id}")
        parent_edge = self._branch_parent_edge(node_id)
        parent_id = parent_edge.source if parent_edge is not None else None
        child_edges = [e for e in self.edges.values() if e.source == node_id]
        # R6.1: a System Prompt note attached to node_id (a note -> node_id
        # edge - the exact shape _branch_parent_edge deliberately skips
        # above, so it is NOT parent_edge) still dies with this endpoint,
        # same "edges die with either endpoint" invariant remove_nodes
        # already enforces elsewhere - otherwise it would dangle, pointing
        # at a node_id that no longer exists in self.nodes. The note ITSELF
        # is not deleted (mirrors ungroup's own "detach, don't
        # cascade-delete" precedent) - only this now-stale edge.
        note_edges = []
        for edge in self.edges.values():
            if edge.target != node_id:
                continue
            source_node = self.nodes.get(edge.source)
            if source_node is not None and source_node.kind == "note":
                note_edges.append(edge)

        for edge in [parent_edge, *child_edges, *note_edges]:
            if edge is not None:
                self.edges.pop(edge.id, None)
        if parent_id is not None:
            for edge in child_edges:
                self.connect(parent_id, edge.target)

        if self.last_chat_node_id == node_id:
            # The active branch continues from wherever it now ends: the
            # deleted node's own parent (None if it had none either).
            self.last_chat_node_id = parent_id

        # ADR-002 Workstream 1 ("Branch status and lifecycle") - found by
        # adversarial review: unlike last_chat_node_id above, this is
        # cleared entirely rather than re-pointed to the parent. The parent
        # was never itself marked Final Deliverable, so silently promoting
        # it would misattribute a status the user never gave it; requiring
        # a fresh, explicit re-mark is the safe behavior.
        if self.final_deliverable_node_id == node_id:
            self.final_deliverable_node_id = None

        del self.nodes[node_id]
        self._detach_node_from_membership(node_id)

    def send_message(
        self,
        text: str,
        content_parts: list[dict[str, Any]] | None = None,
        branch_from_node_id: str | None = None,
    ) -> SceneNode:
        """The Composer's real Send action (R3.3): create a real user
        ChatNode continuing the current branch (last_chat_node_id), or
        start a fresh root if none exists yet. Positioning is a simple
        deterministic stack, not the legacy find_branch_position packing
        algorithm - real auto-layout is a later refinement; "Organize
        Nodes" already exists as a fallback.

        R8a: content_parts carries real attachments (image/audio) staged in
        the composer - optional, additive, threaded straight to
        add_chat_node.

        ADR-002 Workstream 1 ("Branch from here"): branch_from_node_id, when
        given and still a real node, OVERRIDES last_chat_node_id for this
        one send - the actual fork primitive. Before this, last_chat_node_id
        was the ONLY way to pick a parent, so a second real branch (two
        children of one parent) was reachable only by manual edge
        manipulation, never through the UI - see that field's own comment
        ("until real node selection exists"). A bad/stale id (deleted node,
        typo) falls through to the ordinary last_chat_node_id path rather
        than raising, same defensive posture chat_branch_history's walk
        already uses for an unknown id.

        When branching onto a parent that already has one or more chat-kind
        children (a genuine divergence, not a fresh continuation), the new
        sibling fans out horizontally by BRANCH_HORIZONTAL_SPACING per
        existing child instead of landing on the exact same (x, y) as an
        existing branch - which would render as one node silently hiding
        another.

        last_chat_node_id is updated to the new node afterward exactly as
        an ordinary send would be, override or not - so the branch just
        created becomes the active one for the NEXT (non-overridden) send,
        the same continue-from-here behavior as always."""
        if branch_from_node_id is not None and branch_from_node_id in self.nodes:
            parent_id: str | None = branch_from_node_id
            parent = self.nodes[parent_id]
            sibling_count = sum(
                1
                for e in self.edges.values()
                if e.source == parent_id and (target := self.nodes.get(e.target)) is not None and target.kind == "chat"
            )
            x, y = parent.x + sibling_count * BRANCH_HORIZONTAL_SPACING, parent.y + MESSAGE_VERTICAL_SPACING
        else:
            parent_id = self.last_chat_node_id
            if parent_id is not None and parent_id in self.nodes:
                parent = self.nodes[parent_id]
                x, y = parent.x, parent.y + MESSAGE_VERTICAL_SPACING
            else:
                parent_id = None
                chat_node_count = sum(1 for n in self.nodes.values() if n.kind == "chat")
                x, y = 0.0, chat_node_count * MESSAGE_VERTICAL_SPACING
        node = self.add_chat_node(x, y, text, True, parent_id=parent_id, content_parts=content_parts)
        self.last_chat_node_id = node.id
        return node

    def chat_branch_history(self, node_id: str) -> list[dict]:
        """Walk the branch from node_id up to its root, collecting one
        {"role", "content"} entry per node visited (including node_id
        itself), then reverse so the result reads root-to-leaf (oldest
        message first) - the direct new-backend replacement for legacy
        conversation_history built by walking the QGraphicsScene parent
        chain. Follows edges generically (by target match) rather than
        asserting a chat-kind node shape: the walk itself only ever visits
        chat-kind nodes in practice given how they are the only kind chained
        this way, but a bad/unknown node_id or a stray edge shape should
        stop the walk quietly rather than raise."""
        history: list[dict] = []
        current_id: str | None = node_id
        while current_id is not None:
            node = self.nodes.get(current_id)
            if node is None:
                break
            # R8a: content_parts, when populated, IS the complete message
            # content (its own text part plus any image_bytes/audio_file
            # parts) - not an addendum to node.content. A node with no
            # attachments has content_parts=None and this is byte-identical
            # to before: a plain string, exactly as every other consumer of
            # this history already expects.
            #
            # ADR-002 stage 2.5: is_user/content_parts live on node.state
            # now (ChatState), not directly on SceneNode - getattr with the
            # field's own original default (False/None) rather than a
            # node.kind == "chat" check, since this method's own docstring
            # promises to stop quietly on a stray non-chat node/edge shape,
            # never raise. Same duck-typed posture as remove_nodes's own
            # image_asset_id/chart_asset_id reads (backend/domain/graph.py).
            history.append({
                "role": "user" if getattr(node.state, "is_user", False) else "assistant",
                "content": getattr(node.state, "content_parts", None) or node.content,
            })
            parent_edge = self._branch_parent_edge(current_id)
            current_id = parent_edge.source if parent_edge is not None else None
        history.reverse()
        return history

    def get_branch_root(self, node_id: str) -> SceneNode | None:
        """R6.1 addition (backend/agents.py's system-prompt-override
        resolution and backend/plugins.py's System Prompt plugin both need
        this same walk): find node_id's topmost ancestor by walking the
        parent-edge chain up - the SAME by-target-match walk chat_branch_
        history/regenerate_response/delete_chat_node already use (via
        _branch_parent_edge) - until reaching a node with no incoming parent
        edge. Returns node_id's own node when it already has no parent (it
        IS the root), or None for an unknown node_id. Deliberately generic,
        not scoped to kind="chat" - any node reachable via this edge-chain
        shape can be walked."""
        current_id: str | None = node_id
        root: SceneNode | None = None
        while current_id is not None:
            node = self.nodes.get(current_id)
            if node is None:
                break
            root = node
            parent_edge = self._branch_parent_edge(current_id)
            current_id = parent_edge.source if parent_edge is not None else None
        return root

    def regenerate_response(self, node_id: str) -> tuple[SceneNode, str]:
        """Validate + resolve a regenerate target. Mirrors legacy's regenerate_node
        single precondition (window_actions.py:512-514: no parent -> can't
        regenerate), extended with two defensive checks legacy cannot hit (it
        always holds a live scene-graph object, never a string id to resolve):
        unknown node_id, and a non-chat-kind node_id (code/document/etc. can
        never be regenerate targets directly - see Q2, the frontend always
        resolves to a chat-node id before calling in). All three raise
        SceneError; the WS-intent wrapper in register_canvas catches it and
        shows ONE friendly notification for all three cases - see that wrapper
        for why."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "chat":
            raise SceneError(f"node is not a chat node: {node_id}")
        parent_edge = self._branch_parent_edge(node_id)
        if parent_edge is None:
            raise SceneError(f"node has no parent and cannot be regenerated: {node_id}")
        return node, parent_edge.source

    def remove_associated_content_children(self, chat_node_id: str) -> None:
        """The regenerate teardown: remove every code/document/image/thinking
        node ONE HOP directly off chat_node_id. Built entirely on the existing
        generic remove_nodes (edge cleanup + image-asset eviction come free).
        Mirrors graphlink_scene.py's remove_associated_content_nodes exactly in
        SCOPE (one-hop only, same four kinds, no cascade to any grandchild) but
        resolved via this backend's one edge-encoded parent/child relationship
        instead of legacy's four parallel per-kind lists. html/conversation
        kinds are excluded on purpose - grep confirms neither ever has a
        parent_content_node attribute in legacy, so they structurally can never
        attach to a ChatNode this way."""
        child_ids = []
        for edge in self.edges.values():
            if edge.source != chat_node_id:
                continue
            child = self.nodes.get(edge.target)
            if child is not None and child.kind in ("code", "document", "image", "thinking"):
                child_ids.append(child.id)
        self.remove_nodes(child_ids)

    def resolve_generate_image(self, chat_node_id: str) -> tuple[str, str]:
        """'Generate Image from Text' target resolution (R4.4a). Returns
        (parent_chat_node_id, prompt) = (chat_node_id, node.content) - the
        selected ChatNode's own id becomes the new image's parent chat node,
        and its own text becomes the prompt, mirroring legacy's real
        "Generate Image from Text" entry point (window_actions.py's
        generate_image(chat_node), called with node.text as the prompt).
        Raises SceneError for an unknown node id or a non-chat kind
        (defensive - the frontend always resolves this from a real ChatNode's
        own menu, same posture as regenerate_response's own defensive checks
        above), and the SceneEmptyPromptError subclass specifically for
        empty/whitespace content - mirrors legacy's own "no text to use as a
        prompt" guard (window_actions.py:989-991), kept as a DISTINCT
        SceneError subclass (not a plain SceneError) so the WS wrapper in
        register_canvas can show a distinct message for this case without
        string-sniffing."""
        node = self.nodes.get(chat_node_id)
        if node is None:
            raise SceneError(f"unknown node: {chat_node_id}")
        if node.kind != "chat":
            raise SceneError(f"node is not a chat node: {chat_node_id}")
        if not node.content or not node.content.strip():
            raise SceneEmptyPromptError(f"node has no text to use as a prompt: {chat_node_id}")
        return chat_node_id, node.content

    def resolve_regenerate_image(self, image_node_id: str) -> tuple[str, str]:
        """'Regenerate Image' target resolution (R4.4a). Returns
        (parent_chat_node_id, prompt) = (the ImageNode's own parent chat node
        id via one-hop edge lookup, node.content - the ImageNode's OWN stored
        prompt). This is the deliberate improvement over legacy's real
        regenerate mechanism, which instead re-derives the prompt from the
        parent ChatNode's live .text - a real, reproducible legacy quirk that
        re-wraps its own wrapped "Generated image for prompt: ..." string on
        every subsequent regenerate. Raises SceneError for an unknown node
        id, a non-image kind, or a missing parent edge (defensive only -
        add_image_node requires parent_id, so an unparented image node can
        never actually be constructed; this exists purely so a future bug
        elsewhere fails loud instead of crashing downstream), and the
        SceneEmptyPromptError subclass for empty/whitespace content
        (defensive - mirrors legacy's own conditional-visibility guard `if
        parent_content_node and prompt` around showing the menu action at
        all)."""
        node = self.nodes.get(image_node_id)
        if node is None:
            raise SceneError(f"unknown node: {image_node_id}")
        if node.kind != "image":
            raise SceneError(f"node is not an image node: {image_node_id}")
        parent_edge = self._branch_parent_edge(image_node_id)
        if parent_edge is None:
            raise SceneError(f"image node has no parent: {image_node_id}")
        if not node.content or not node.content.strip():
            raise SceneEmptyPromptError(f"image node has no prompt to regenerate from: {image_node_id}")
        return parent_edge.source, node.content

    def collapse_branch(self, node_id: str, collapsed: bool) -> None:
        """ADR-002 Workstream 1 ("Branch status and lifecycle"): "Collapse
        a rejected branch without deleting it" - reuses the existing,
        already-fully-wired is_collapsed field verbatim (wire sync, save,
        load, and ChatNodeView.tsx's collapsed-pill rendering all already
        work for it), applied across node_id's own chat-kind subtree via
        _chat_subtree_ids instead of just node_id itself - the one
        genuinely new piece this needs. Deliberately NOT automatic when a
        branch is marked "rejected" via set_branch_status: status (a
        semantic label) and collapse (a view state) are kept decoupled on
        purpose, so a branch can be Rejected-but-still-expanded during
        review, and marking status never has a side effect on any other
        node's state (matching set_branch_status's own "no implicit side
        effects" posture). Bulk-sets uniformly across the whole subtree,
        so a node that had previously been individually expanded/collapsed
        differently loses that distinction the first time this runs - an
        accepted, stated tradeoff, not solved here."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "chat":
            raise SceneError(f"node is not a chat node: {node_id}")
        for nid in self._chat_subtree_ids(node_id):
            self.nodes[nid].is_collapsed = bool(collapsed)
