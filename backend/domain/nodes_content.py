"""ContentOps - the SceneDocument methods for the document, thinking, html
and note node kinds.

A MIXIN, composed exactly once, by backend/domain/graph.py's
SceneDocument. Method bodies are relocated VERBATIM from graph.py;
only the class wrapper, its docstring and the imports are new, and the
methods are regrouped by kind rather than left in the order successive
increments happened to append them in.

See backend/domain/nodes_code_review.py's docstring for why the
per-kind method groups are being lifted out of SceneDocument at all.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import TYPE_CHECKING

from backend.domain._composed import SceneDocumentParts
from backend.domain.model import (
    HTML_TITLE_PREVIEW_LENGTH,
    THINKING_TITLE_PREVIEW_LENGTH,
    SceneError,
    SceneNode,
)
from backend.domain.node_access import is_node_of, require_node
from backend.domain.node_states import ArtifactState, DocumentState, HtmlState, NoteState

if TYPE_CHECKING:
    from backend.domain.node_access import _NodeWith


def clear_note_text_readers(nodes: Iterable[SceneNode], removed_sources: set[str]) -> None:
    """Disconnect readers when a source disappears or loses its text output."""
    for node in nodes:
        if isinstance(node.state, NoteState) and node.state.text_input_source_id in removed_sources:
            node.state.clear_text_input()


def _text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()


class ContentOps(SceneDocumentParts):
    """The plain content kinds: document, thinking, html and note.

    Each stores text the user or an agent wrote. Plain notes also accept
    a manually run text input. Note is the one that may float free;
    the other three require a parent, like every R3+ content kind.
    """

    def add_document_node(
        self,
        x: float,
        y: float,
        title: str,
        content: str,
        attachment_kind: str,
        parent_id: str,
        *,
        file_path: str = "",
        mime_type: str = "",
        duration_seconds: float | None = None,
        byte_size: int | None = None,
        preview_label: str = "",
    ) -> SceneNode:
        """R3.9's document-node equivalent of add_chat_node/add_code_node: a
        real file-attachment node (a document or an audio file), for the
        legacy DocumentNode / ChatScene.add_document_node pair. UNLIKE
        chat/code, parent_id is REQUIRED here, not optional: read fresh from
        graphlink_scene.py, add_document_node(title, content,
        parent_user_node, ...) takes parent_user_node as a plain required
        positional with no default, and unconditionally constructs a
        DocumentConnectionItem(parent_user_node, node) - there is no `if
        parent_id` guard around that connection the way chat/code have
        around theirs - so a DocumentNode can never exist unparented.
        Document nodes are also NOT branch points (same as code): there is
        no delete_document_node; deletion goes entirely through the
        existing generic remove_nodes.

        The six attachment fields are stored verbatim - no title-preview
        truncation (DocumentNode.title in the legacy app is just whatever
        descriptive title/filename was passed in, confirmed by reading
        DocumentNode.__init__: `self.title = title`, no slicing), and none
        of the legacy view-layer formatting (byte-size/duration strings,
        preview_label auto-fill, audio-preview suppression) happens here -
        see DocumentState's own docstring (backend/domain/node_states.py)
        for those exact rules.
        """
        if parent_id is not None and parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        # Mirrors DocumentNode.__init__'s `(attachment_kind or
        # "document").lower()` normalization - the attachment_kind param has
        # no default in this signature (per spec), but an empty/None value
        # still needs to fall back to "document" and casing still needs to
        # normalize, since "audio" vs "Audio" is a real behavioral branch
        # (metadata "Type" row, preview label, badge text all key off it).
        normalized_kind = str(attachment_kind or "document").lower()
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title=str(title),
            kind="document",
            content=str(content),
            state=DocumentState(
                attachment_kind=normalized_kind,
                file_path=str(file_path),
                mime_type=str(mime_type),
                duration_seconds=duration_seconds,
                byte_size=byte_size,
                preview_label=str(preview_label),
            ),
        )
        self.nodes[node_id] = node
        if parent_id is not None:
            self.connect(parent_id, node_id)
        return node

    def add_thinking_node(
        self,
        x: float,
        y: float,
        thinking_text: str,
        parent_id: str,
    ) -> SceneNode:
        """R3.13's ThinkingNode equivalent of add_chat_node/add_code_node/
        add_document_node: a real reasoning-panel node. Same as
        add_document_node (and unlike chat/code), parent_id is REQUIRED, not
        optional - a ThinkingNode never exists unparented - so this
        unconditionally connects to its parent, no `if parent_id` guard.

        Thinking text reuses the existing `content` field rather than a new
        one - there is no separate thinking-text field. `is_docked` defaults
        to False: a freshly-created thinking node is never pre-docked: dock()
        is only ever invoked by explicit user action or on session-load
        restore, never at construction time.

        Thinking nodes are also NOT branch points (same as code/document):
        there is no delete_thinking_node; deletion goes entirely through the
        existing generic remove_nodes.
        """
        if parent_id is not None and parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        title = str(thinking_text)[:THINKING_TITLE_PREVIEW_LENGTH] or "Thinking"
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title=title,
            kind="thinking",
            content=str(thinking_text),
        )
        self.nodes[node_id] = node
        if parent_id is not None:
            self.connect(parent_id, node_id)
        return node

    def add_html_node(
        self,
        x: float,
        y: float,
        html_content: str,
        parent_id: str,
    ) -> SceneNode:
        """R3.17's HtmlViewNode equivalent of add_document_node/
        add_thinking_node: a real raw-HTML-source node. Same as
        add_document_node/add_thinking_node (and unlike chat/code), parent_id
        is REQUIRED, not optional - an HtmlViewNode never exists unparented -
        so this unconditionally connects to its parent, no `if parent_id`
        guard.

        The raw HTML source reuses the existing `content` field rather than a
        new one - there is no separate html-content field, same reuse pattern
        as R3.5's code text and R3.13's thinking text. The backend stores it
        VERBATIM as an opaque string: it never parses, sanitizes, validates,
        or otherwise interprets the HTML - that is the frontend's job (the
        preview render is a 100% client-side action that never round-trips
        here).

        Html nodes are also NOT branch points (same as code/document/
        thinking): there is no delete_html_node; deletion goes entirely
        through the existing generic remove_nodes.
        """
        if parent_id is not None and parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        title = str(html_content)[:HTML_TITLE_PREVIEW_LENGTH] or "HTML"
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title=title,
            kind="html",
            content=str(html_content),
            state=HtmlState(),
        )
        self.nodes[node_id] = node
        if parent_id is not None:
            self.connect(parent_id, node_id)
        return node

    def set_html_splitter_state(self, node_id: str, value: float) -> None:
        """R6.3: persists an HtmlViewNode's draggable code/preview splitter
        position. html kind only (SceneError otherwise), matching every
        other kind-specific setter's guard pattern in this file (e.g.
        resize_chart/toggle_frame_lock)."""
        node = require_node(self.nodes, node_id, "html", HtmlState)
        node.state.html_splitter_state = float(value)

    def set_html_content(self, node_id: str, html_content: str) -> None:
        """Commits an HtmlViewNode's edited source. Without this the stored
        source could only ever be what add_html_node was given: the card's
        textarea edits lived in component state alone, so they vanished the
        moment off-viewport culling unmounted the card and were never saved.

        Stored VERBATIM, exactly as add_html_node stores it (see its own doc
        - the backend never parses or sanitizes the HTML). The title is left
        as add_html_node set it: like set_note_content,
        update_chat_node_content and the builderSetContent tool, an in-place
        content mutator never recomputes a title post-creation, so an html
        node's title cannot depend on which of those paths wrote it last.
        html kind only, matching set_html_splitter_state's guard."""
        node = require_node(self.nodes, node_id, "html", HtmlState)
        node.content = str(html_content)

    # -- R6.1: notes -----------------------------------------------------------
    #
    # Legacy canvas decorations. Notes are free-floating markdown sticky-
    # notes with no parent-required posture, unlike almost every R3+ content
    # kind. (Their neighbours in that increment, frames and containers, are
    # group nodes and live in backend/domain/groups.py.)

    def add_note(
        self,
        x: float,
        y: float,
        *,
        is_system_prompt: bool = False,
        is_summary_note: bool = False,
    ) -> SceneNode:
        """A note's creation primitive. UNLIKE every R3+ content kind, no
        parent is required or accepted - notes are free-floating, never
        branch-point children (mirrors the legacy note widget, which the
        canvas places directly, not via a ChatNode-anchored connection)."""
        node_id = f"n{next(self._counter)}"
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title="Note",
            kind="note",
            content="Add note...",
            state=NoteState(
                is_system_prompt=bool(is_system_prompt),
                is_summary_note=bool(is_summary_note),
            ),
        )
        self.nodes[node_id] = node
        return node

    def set_note_content(self, node_id: str, content: str) -> None:
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.content = str(content)

    def _plain_note(self, node_id: str) -> _NodeWith[NoteState]:
        note = self.nodes.get(node_id)
        if not is_node_of(note, "note", NoteState):
            raise SceneError("Choose a plain note for this text input.")
        if note.state.is_system_prompt or note.state.is_summary_note or note.state.is_branch_comparison:
            raise SceneError("Text inputs require a plain note.")
        return note

    def _text_output(self, source_id: str) -> str:
        source = self.nodes.get(source_id)
        if source is None:
            raise SceneError("The text source no longer exists.")
        if source.kind == "note":
            return self._plain_note(source_id).content
        if source.kind == "chat":
            return source.content
        if source.kind == "artifact" and isinstance(source.state, ArtifactState):
            return source.state.artifact_content
        raise SceneError("Choose a plain note, chat message, or Drafter document as the text source.")

    def bind_note_text_input(self, source_id: str, target_id: str, operation: str = "copy") -> None:
        """Connect one text output to a note without evaluating it."""
        target = self._plain_note(target_id)
        self._text_output(source_id)
        if operation not in ("copy", "word_count"):
            raise SceneError("Choose Copy text or Word count for this input.")
        if target.state.text_input_source_id not in ("", source_id):
            raise SceneError("Disconnect the current text source before connecting another.")

        # Only data dependencies count. A structural edge can run either way.
        current_id = source_id
        seen = {target_id}
        while current_id:
            if current_id in seen:
                raise SceneError("A text input cannot form a cycle.")
            seen.add(current_id)
            state = self.nodes[current_id].state
            current_id = state.text_input_source_id if isinstance(state, NoteState) else ""

        if target.state.text_input_source_id != source_id or target.state.text_input_operation != operation:
            target.state.text_input_last_source_sha256 = ""
            target.state.text_input_source_id = source_id
            target.state.text_input_operation = operation

    def unbind_note_text_input(self, target_id: str) -> None:
        self._plain_note(target_id).state.clear_text_input()

    def run_note_text_input(self, target_id: str) -> None:
        """Evaluate exactly this note from its source's current, committed text."""
        note = self._plain_note(target_id)
        if not note.state.text_input_source_id:
            raise SceneError("Connect a text source before running this input.")
        text = self._text_output(note.state.text_input_source_id)
        digest = _text_digest(text)
        note.content = str(len(text.split())) if note.state.text_input_operation == "word_count" else text
        note.state.text_input_last_source_sha256 = digest

    def _text_input_status(self, node: SceneNode, source_digests: dict[str, str] | None = None) -> str:
        state = node.state
        if node.kind != "note" or not isinstance(state, NoteState) or not state.text_input_source_id:
            return "unbound"
        if not state.text_input_last_source_sha256:
            return "unrun"
        source_id = state.text_input_source_id
        digest = source_digests.get(source_id) if source_digests is not None else None
        if digest is None:
            digest = _text_digest(self._text_output(source_id))
            if source_digests is not None:
                source_digests[source_id] = digest
        return "current" if digest == state.text_input_last_source_sha256 else "stale"
