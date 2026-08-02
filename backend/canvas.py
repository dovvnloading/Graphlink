"""Canvas orchestration + wire layer (Qt-removal plan R1; split at ADR-002
stage 2.2).

This module is now the ORCHESTRATION/WIRE/REGISTRATION half of the canvas:
register_canvas() wires every scene/grid topic and intent onto a SessionBus,
translating between the WS layer, the AgentDispatcher, and the pure scene
domain. The domain half - SceneDocument/SceneNode/SceneEdge, the content
codec, and every layout/appearance constant - was relocated VERBATIM to
backend/domain/ (graph.py / model.py / content_codec.py; each carries its
relocation note), whose purity is enforced permanently by
tests/test_domain_purity.py.

canvas.py imports every domain name back for its own use, so every existing
`from backend.canvas import X` consumer (session_load/session_save/plugins/
tests, incl. the private _content_codec namespace - the SAME shared
instance, identity preserved) keeps working unchanged. Wire-only helpers
(_research_result_wire, _history_turn_text, _history_token_text,
_chart_source_text, _format_branches_for_comparison, _placeholder_chart_data)
stay here, as does the entire register_canvas closure set.
"""

from __future__ import annotations

import base64
import os
from typing import Any

from graphlink_chart_data import ChartDataError, canonicalize_chart_data, SUPPORTED_CHART_TYPES
from graphlink_grid_view_settings import GRID_STYLE_PRESETS
from graphlink_navigation_pins import NavigationPinRecord

from backend import native_dialogs
from backend.agents import (
    _CODE_EXEC_RUN_CLAIM_PLACEHOLDER,
    _GITLINK_RUN_CLAIM_PLACEHOLDER,
    AgentDispatcher,
)
from backend.composer import ComposerDocument
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.response_parsing import (
    parse_response,
    PLACEHOLDER_GENERATED_CONTENT,
    PLACEHOLDER_ASSISTANT_REASONING,
    PLACEHOLDER_EMPTY_RESPONSE,
)
from backend.token_counter import TokenCounterState

# ADR-002 stage 2.2: the scene data model + content codec were relocated
# VERBATIM into backend/domain/ (model.py / content_codec.py - see their own
# docstrings). canvas.py imports every name back for its own use, so every
# existing `from backend.canvas import X` consumer (session_load/session_save/
# plugins/tests, incl. the private _content_codec namespace - the SAME shared
# instance, identity preserved) keeps working unchanged.
from backend.domain.content_codec import _content_codec
from backend.domain.graph import SceneDocument
from backend.domain.model import (
    BRANCH_HORIZONTAL_SPACING,
    CHART_MAX_HEIGHT,
    CHART_MAX_WIDTH,
    CHART_MIN_HEIGHT,
    CHART_MIN_WIDTH,
    CHAT_TITLE_PREVIEW_LENGTH,
    CODE_TITLE_PREVIEW_LENGTH,
    DRAG_FACTOR_MAX,
    DRAG_FACTOR_MIN,
    DRAG_PERCENT_MAX,
    DRAG_PERCENT_MIN,
    DRAG_PERCENT_PRESETS,
    FONT_COLOR_PRESETS,
    FONT_FAMILIES,
    FONT_SIZE_MAX,
    FONT_SIZE_MIN,
    GRID_COLOR_PRESETS,
    GROUP_COLLAPSED_HEIGHT,
    GROUP_COLLAPSED_WIDTH,
    GROUP_INELIGIBLE_FRAME_MEMBER_KINDS,
    GROUP_MEMBER_DEFAULT_HEIGHT,
    GROUP_MEMBER_DEFAULT_WIDTH,
    GROUP_PADDING,
    GROUP_PADDING_TOP,
    HTML_TITLE_PREVIEW_LENGTH,
    IMAGE_TITLE_PREVIEW_LENGTH,
    MESSAGE_VERTICAL_SPACING,
    NOTE_AGENT_BODY_COLOR,
    NOTE_AGENT_HEADER_COLOR,
    NOTE_AGENT_X_OFFSET,
    ORGANIZE_SPACING_X,
    ORGANIZE_SPACING_Y,
    SceneEdge,
    SceneEmptyPromptError,
    SceneError,
    SceneNode,
    THINKING_TITLE_PREVIEW_LENGTH,
)




def _research_result_wire(result) -> dict[str, Any]:
    """Camel-cases a ResearchResult (graphlink_plugins/web_research/domain.py)
    for the wire - a pure mapping function, NOT a SceneDocument method.
    Duck-typed on purpose: canvas.py imports nothing from
    graphlink_plugins.web_research (same posture as apply_web_research_progress
    above)."""
    return {
        "requestId": result.request_id,
        "originalQuery": result.original_query,
        "effectiveQuery": result.effective_query,
        "answerMarkdown": result.answer_markdown,
        "sources": [
            {
                "sourceId": s.source_id,
                "title": s.title,
                "url": s.url,
                "canonicalUrl": s.canonical_url,
                "snippet": s.snippet,
                "rank": s.rank,
                "provider": s.provider,
                "finalUrl": s.final_url,
                "status": s.status,
                "errorCode": s.error_code,
                "errorMessage": s.error_message,
                "truncated": s.truncated,
                "contentHash": s.content_hash,
                "citationCount": s.citation_count,
            }
            for s in result.sources
        ],
        "citations": [
            {"sourceId": c.source_id, "marker": c.marker, "claimContext": c.claim_context}
            for c in result.citations
        ],
        "warnings": list(result.warnings),
        "providerSnapshot": dict(result.provider_snapshot),
    }


def _history_turn_text(turn: dict) -> str:
    """Flattens one chat_branch_history entry's "content" into plain text,
    for callers whose interface is a flat string (token estimation here;
    _chart_source_text below has its own, separate flattening for
    ChartDataAgent). A turn's content is a plain str for an ordinary
    message, or (R8a attachments) a content_parts list of {"type", ...}
    dicts - only the "text" part has a token-count analog, so image/audio
    parts are skipped rather than stringified."""
    content = turn.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _history_token_text(history: list[dict]) -> str:
    """Joins an entire chat_branch_history's turns into one string for
    token_counter.py's set_context_text (which, like set_input_text, takes
    a single flat string and estimates it via the real tiktoken-backed
    estimator - ADR-016 stage 16.2)."""
    return "\n\n".join(text for text in (_history_turn_text(turn) for turn in history) if text)


def _chart_source_text(branch_history: list[dict]) -> str:
    """R6.2: flattens chat_branch_history's own {"role","content"} list (the
    SAME branch walk web_research/pycoder/gitlink already reuse via
    document.chat_branch_history - see this module's own docstring
    convention) into the single plain-text string ChartDataAgent.get_response
    expects. NOT a new branch-walking helper (chat_branch_history already IS
    that, reused as-is) - just the formatting step its list-of-dicts shape
    needs before it can be handed to an agent whose interface is a flat
    string, mirroring legacy graphlink_window_actions.py's own
    _build_chart_source_text/history_to_transcript role for the same
    call site. Empty/whitespace-only turns are skipped so an accidental blank
    ChatNode doesn't inject a stray blank line."""
    lines = []
    for turn in branch_history:
        content = str(turn.get("content") or "").strip()
        if not content:
            continue
        speaker = "User" if turn.get("role") == "user" else "Assistant"
        lines.append(f"{speaker}: {content}")
    return "\n\n".join(lines)


def _format_branches_for_comparison(branches: list[tuple[str, list[dict]]]) -> str:
    """ADR-002 Workstream 1 ("Compare Branches"): flattens 2+ labeled
    chat_branch_history results into the single plain-text block
    BranchComparisonAgent.get_response expects - one labeled section per
    branch, each turn formatted "Speaker: text" (same convention as
    _chart_source_text above), but built on _history_turn_text rather than
    that function's own simpler `str(turn.get("content") or "")` - a turn
    carrying R8a attachment content_parts (a list, not a plain string)
    needs _history_turn_text's real flattening or it would stringify the
    raw list instead of extracting the text part."""
    sections = []
    for label, history in branches:
        lines = []
        for turn in history:
            text = _history_turn_text(turn).strip()
            if not text:
                continue
            speaker = "User" if turn.get("role") == "user" else "Assistant"
            lines.append(f"{speaker}: {text}")
        sections.append(f"=== {label} ===\n" + "\n\n".join(lines))
    return "\n\n".join(sections)


def _placeholder_chart_data(chart_type: str) -> dict[str, Any]:
    """R6.2: a minimal, already-canonical-SHAPED payload (matching
    canonicalize_chart_data's own output keys exactly) for the rare
    defensive case in generate_chart below where ChartDataAgent's response
    carries no top-level "error" key yet still fails
    canonicalize_chart_data - see that function's own call site for the full
    reasoning. One trivial data point per chart kind: just enough for
    render_chart_png to draw SOMETHING rather than leave the request a
    silent no-op, matching this feature's "never a blank/broken state"
    contract. NOT run back through canonicalize_chart_data itself - callers
    that need genuinely validated data should generate it properly instead."""
    if chart_type == "sankey":
        return {"type": "sankey", "title": "Chart", "flows": [{"source": "A", "target": "B", "value": 1.0}]}
    if chart_type == "histogram":
        return {
            "type": "histogram", "title": "Chart", "values": [0.0, 1.0], "bins": 2,
            "xAxis": "Value", "yAxis": "Frequency",
        }
    return {
        "type": chart_type,
        "title": "Chart",
        "labels": ["A", "B"],
        "values": [1.0, 1.0],
        "xAxis": "Category" if chart_type == "bar" else "Sequence",
        "yAxis": "Value",
    }


def register_canvas(
    bus: SessionBus,
    notifications: NotificationState,
    agent_dispatcher: AgentDispatcher,
    composer_document: ComposerDocument,
    token_counter: TokenCounterState | None = None,
) -> SceneDocument:
    """Give a session its canvas document + the scene/grid topics and every
    R1 intent. Intent names for grid mirror GridControlBridge's @Slot names
    1:1 so the R2 island port is a transport swap, not a redesign.

    R4: agent_dispatcher/composer_document are threaded through so
    sendMessage's real Send action (below) can hand off to the real agent
    dispatch pipeline instead of the R3-era deferred notice. token_counter
    (R8a) lets sendMessage/regenerateResponse set real outputTokens/
    contextTokens once a reply completes - see those intents' own comments.
    Optional (default a throwaway, unregistered instance) only so the ~30
    pre-R8a canvas/agents tests that call register_canvas with 4 positional
    args keep working unchanged - the real (and only) production call site,
    backend/app.py's _configure_session, always passes the session's real,
    bus-registered one."""

    if token_counter is None:
        token_counter = TokenCounterState()

    document = SceneDocument()

    bus.register_topic("scene", document.scene_payload)
    bus.register_topic("grid-control", document.grid_payload)
    # Static preset topics, field-for-field the DragSpeedStatePayload /
    # FontControlStatePayload shapes so the generated validators apply
    # unchanged (same reuse as grid-control).
    bus.register_topic(
        "drag-speed",
        lambda: {
            "percentPresets": list(DRAG_PERCENT_PRESETS),
            "percentMin": DRAG_PERCENT_MIN,
            "percentMax": DRAG_PERCENT_MAX,
        },
    )
    bus.register_topic(
        "font-control",
        lambda: {
            "fontFamilies": list(FONT_FAMILIES),
            "colorPresets": list(FONT_COLOR_PRESETS),
            "sizeMin": FONT_SIZE_MIN,
            "sizeMax": FONT_SIZE_MAX,
        },
    )

    async def publish_scene():
        await bus.publish("scene")

    async def publish_grid():
        await bus.publish("grid-control")

    async def publish_token_counter():
        # R8a: guarded, unlike publish_scene/publish_grid above - "scene"
        # and "grid-control" are registered a few lines above in THIS same
        # function, always present by construction. token_counter's own
        # topic is registered elsewhere (backend/token_counter.py's
        # register_token_counter, called once per session in
        # backend/app.py), and the ~30 pre-R8a canvas/agents tests that
        # construct a bare SessionBus + register_canvas directly (with the
        # optional token_counter left at its unregistered default above)
        # never call it - has_topic keeps sendMessage/regenerateResponse
        # working in exactly those tests instead of raising
        # UnknownTopicError the first time either intent runs.
        if bus.has_topic("token-counter"):
            await bus.publish("token-counter")

    # -- scene intents (async: they publish after mutating) ---------------

    async def add_node(x, y, title=""):
        node = document.add_node(x, y, title)
        await publish_scene()
        return node.id

    async def add_chat_node(x, y, content, is_user, parent_id=None):
        node = document.add_chat_node(x, y, content, is_user, parent_id)
        await publish_scene()
        return node.id

    async def add_code_node(x, y, code, language, parent_id=None):
        node = document.add_code_node(x, y, code, language, parent_id)
        await publish_scene()
        return node.id

    async def add_document_node(
        x,
        y,
        title,
        content,
        attachment_kind,
        parent_id,
        file_path="",
        mime_type="",
        duration_seconds=None,
        byte_size=None,
        preview_label="",
    ):
        node = document.add_document_node(
            x,
            y,
            title,
            content,
            attachment_kind,
            parent_id,
            file_path=file_path,
            mime_type=mime_type,
            duration_seconds=duration_seconds,
            byte_size=byte_size,
            preview_label=preview_label,
        )
        await publish_scene()
        return node.id

    async def add_thinking_node(x, y, thinking_text, parent_id):
        node = document.add_thinking_node(x, y, thinking_text, parent_id)
        await publish_scene()
        return node.id

    async def add_html_node(x, y, html_content, parent_id):
        node = document.add_html_node(x, y, html_content, parent_id)
        await publish_scene()
        return node.id

    async def set_html_splitter_state(node_id, value):
        document.set_html_splitter_state(node_id, value)
        await publish_scene()

    async def add_image_node(x, y, image_bytes_base64, prompt, parent_id, mime_type="image/png"):
        # Unlike every prior wrapper, the WS intent transport is JSON, which
        # cannot carry raw bytes - the caller sends base64 text, decoded here
        # before it ever reaches SceneDocument (which only ever deals in real
        # bytes, same as the HTTP asset route on the read side).
        image_bytes = base64.b64decode(image_bytes_base64)
        node = document.add_image_node(x, y, image_bytes, prompt, parent_id, mime_type=mime_type)
        await publish_scene()
        return node.id

    async def add_conversation_node(x, y, parent_id):
        node = document.add_conversation_node(x, y, parent_id)
        await publish_scene()
        return node.id

    async def send_conversation_message(node_id, text):
        # R4.3: the real user-message-send action for a conversation node -
        # appends a real user message, then dispatches a real agent reply
        # through AgentDispatcher.start_conversation_reply, the ConversationNode
        # counterpart of send_message's ChatNode dispatch above. The reply
        # lands via _on_reply calling document.append_conversation_assistant_message
        # directly - same established relationship as send_message's own
        # _on_reply calling document.add_chat_node directly.
        node = document.send_conversation_message(node_id, text)
        await publish_scene()

        def _on_reply(reply_text):
            # R4.3b: deliberate, confirmed-correct omission, NOT an oversight -
            # ConversationNode is exempt from the response_parsing retrofit
            # applied to send_message's _on_reply above. The true legacy
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
        document.delete_conversation_message(node_id, message_index)
        await publish_scene()

    async def set_node_docked(node_id, docked):
        document.set_node_docked(node_id, docked)
        await publish_scene()

    async def delete_chat_node(node_id):
        document.delete_chat_node(node_id)
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

    async def send_message(text, branch_from_node_id=None):
        # R3.3: the real Send action - a real user ChatNode, continuing the
        # active branch. R4: the assistant's reply is now a real agent
        # dispatch call, not a deferred notice - see backend/agents.py.
        #
        # ADR-002 Workstream 1: branch_from_node_id is optional (older
        # frontend builds calling with just [text] still work unchanged -
        # dispatch_intent unpacks positionally, so a missing trailing arg
        # just uses this parameter's own default) - see
        # SceneDocument.send_message's own docstring for the fork mechanics.
        # R8a: consult whatever is staged in the composer right now. This is
        # WHY composer_document is threaded into register_canvas at all (see
        # this function's own docstring) - take_staged_attachments() pops
        # and clears in one step, so a mid-flight staging change can never
        # land on the send that follows it, and never leaks into the next.
        staged = composer_document.take_staged_attachments()
        full_text = text
        for item in staged:
            if item.extracted_text is not None:
                full_text += (
                    f"\n\n--- Attached: {item.name} ({item.context_label}) ---\n"
                    f"{item.extracted_text}\n--- end attachment ---"
                )
        media_parts = [item.content_part for item in staged if item.content_part is not None]
        content_parts = [{"type": "text", "text": full_text}, *media_parts] if media_parts else None

        node = document.send_message(full_text, content_parts=content_parts, branch_from_node_id=branch_from_node_id)
        if staged:
            # The staged list is now empty (popped above) - republish so the
            # composer's attachment chips clear the instant Send fires,
            # rather than waiting for the reply's own later publish().
            await bus.publish("app-composer")
        # R6.3: grow the real, live-growing session token count by the
        # user's own new message text - see add_session_tokens's own
        # comment on SceneDocument.
        document.add_session_tokens(full_text)
        await publish_scene()
        history = document.chat_branch_history(node.id)

        # R8a (token counter wiring): contextTokens is the branch history the
        # reply will be generated FROM - explicitly NOT `history` above,
        # which already includes the message just sent (inputTokens, set
        # live as the draft was typed in Composer.tsx, already owns that
        # text; counting it again here would inflate payload()'s total).
        # Rooted at node's own parent, the same "history before this node"
        # walk every specialized agent flow below (web research/artifact/
        # gitlink/pycoder/sandbox) already uses via _branch_parent_edge -
        # applied to the plain-chat path for the first time here.
        context_parent_edge = document._branch_parent_edge(node.id)
        context_history = (
            document.chat_branch_history(context_parent_edge.source) if context_parent_edge else []
        )
        token_counter.set_context_text(_history_token_text(context_history))
        await publish_token_counter()

        async def _on_reply(reply_text):
            # R6.3: the assistant's reply has completed (regardless of
            # whether parse_response below finds anything node-worthy in
            # it) - grow total_session_tokens by its estimated count too,
            # same as the user's own message text just above.
            document.add_session_tokens(reply_text)
            # R8a: outputTokens reflects what the model actually returned,
            # regardless of whether parse_response below finds anything
            # node-worthy in it (the empty-reply early return just below)
            # - the reply happened and consumed real output tokens either
            # way.
            token_counter.set_output_text(reply_text)
            await publish_token_counter()
            # R4.3b: port legacy handle_response's _parse_response retrofit -
            # split the flat reply into thinking/text/code parts and create
            # separate thinking-kind/code-kind CHILD nodes instead of
            # dumping the raw, unparsed reply into one flat node.
            parsed_parts = parse_response(reply_text)
            if not parsed_parts:
                # Mirrors legacy handle_response's own outer gate
                # (`if text_content or parsed_parts:`) - a genuinely empty/
                # whitespace-only reply creates NO node at all, not a
                # "[Empty Response]" placeholder node. Currently unreachable
                # in practice (api_provider._compose_reasoned_response raises
                # rather than returning blank content), but the gate is kept
                # so a future provider path can never silently diverge from
                # legacy here. last_chat_node_id is deliberately left
                # untouched - it already points at the user's own message
                # node (set by send_message just above), matching legacy's
                # own fallback of leaving current_node at user_node when no
                # assistant node gets created.
                return

            text_parts = [p["content"] for p in parsed_parts if p["type"] == "text"]
            text_content = "\n\n".join(text_parts)

            placeholder_text = text_content
            if not placeholder_text:
                if any(p["type"] == "code" for p in parsed_parts):
                    placeholder_text = PLACEHOLDER_GENERATED_CONTENT
                elif any(p["type"] == "thinking" for p in parsed_parts):
                    placeholder_text = PLACEHOLDER_ASSISTANT_REASONING
                else:
                    # Unreachable given parse_response's own invariants (a
                    # non-empty parts list with no text/code part must
                    # contain a thinking part) - legacy's handle_response has
                    # this exact same dead branch; kept verbatim for
                    # structural parity rather than optimized away.
                    placeholder_text = PLACEHOLDER_EMPTY_RESPONSE

            ax, ay = node.x, node.y + MESSAGE_VERTICAL_SPACING
            ai_node = document.add_chat_node(ax, ay, placeholder_text, False, parent_id=node.id)

            # NOTE: these two calls MUST use the `document.` prefix. Bare
            # `add_code_node(...)` / `add_thinking_node(...)` would silently
            # resolve to this enclosing register_canvas scope's own async WS-
            # intent wrapper closures of the same name (defined earlier,
            # above send_message) instead of raising a NameError - producing
            # an unawaited coroutine that never runs and never errors, so no
            # node would be created and nothing would look wrong until the
            # scene state was actually inspected.
            for part in parsed_parts:
                if part["type"] == "thinking":
                    document.add_thinking_node(
                        ax - MESSAGE_VERTICAL_SPACING, ay + MESSAGE_VERTICAL_SPACING,
                        part["content"], parent_id=ai_node.id,
                    )
                elif part["type"] == "code":
                    document.add_code_node(
                        ax + MESSAGE_VERTICAL_SPACING, ay + MESSAGE_VERTICAL_SPACING,
                        part["content"], part["language"], parent_id=ai_node.id,
                    )

            # Always the real chat node's id, never a code/thinking child's -
            # add_code_node/add_thinking_node are documented above (see their
            # own docstrings) as NOT branch points, and last_chat_node_id
            # specifically drives the next real send's branch-continuation
            # (chat_branch_history), which only makes sense pointed at a real
            # chat node.
            document.last_chat_node_id = ai_node.id

        await agent_dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=history,
            on_reply=_on_reply,
            # R6.1: lets AgentDispatcher resolve a branch-attached System
            # Prompt note override (see backend/agents.py's
            # _resolve_branch_system_prompt) - node.id is the just-created
            # user ChatNode "about to be sent", the walk starts from here.
            canvas_document=document,
            node_id=node.id,
        )
        return node.id

    async def regenerate_response(node_id):
        try:
            node_to_regenerate, parent_id = document.regenerate_response(node_id)
        except SceneError:
            # Deliberate: ALL THREE of regenerate_response's SceneErrors funnel
            # into this ONE legacy-parity message. app.py's _handle_message
            # would otherwise turn a raised SceneError into a generic
            # "intent failed" WS error - and transport.ts's intent() is
            # fire-and-forget (no id), so that error is ONLY ever
            # console.error'd, never shown to the user (confirmed by reading
            # transport.ts's handleMessage). A stale click racing a delete, or
            # a future caller passing a bad kind, must never go silently to the
            # console when legacy's real, reachable case shows a visible
            # banner - so this is the one deliberate divergence from every
            # other register_canvas wrapper's convention of letting SceneError
            # bubble to the generic WS error path.
            notifications.show("This node has no parent and cannot be regenerated.", "warning")
            await bus.publish("notification")
            return None

        history = document.chat_branch_history(parent_id)
        # R8a (token counter wiring): unlike send_message, `history` here
        # already excludes the node being regenerated (rooted at parent_id,
        # not node_id) - there is no fresh draft text to double-count
        # against, so it's usable directly as contextTokens with no
        # adjustment.
        token_counter.set_context_text(_history_token_text(history))
        await publish_token_counter()

        async def _on_reply(reply_text):
            # R8a: outputTokens reflects what the model actually returned,
            # ahead of every early-return below - a regenerate that comes
            # back empty, or lands after the node was deleted mid-flight,
            # still consumed real output tokens.
            token_counter.set_output_text(reply_text)
            await publish_token_counter()
            # (1) Empty/whitespace reply: keep ORIGINAL content, notify, stop.
            # Checked FIRST - exact legacy order (window_actions.py:544-546),
            # even before the liveness check below (see its own comment).
            if not reply_text or not reply_text.strip():
                notifications.show(
                    "The model returned an empty response. The original response has been kept.",
                    "warning",
                )
                await bus.publish("notification")
                return

            # (2) Deleted mid-flight: silent no-op, matches
            # window_actions.py:548 (`if not old_node or not old_node.scene():
            # return` - no notification_banner call there either).
            if node_to_regenerate.id not in document.nodes:
                return

            # (3) Teardown BEFORE parse/mutate - exact legacy step order.
            # Runs unconditionally on any non-empty, still-alive reply, even if
            # the new reply has no code/thinking parts at all - this is why
            # document/image children are deleted but never recreated
            # (parse_response structurally only emits thinking/text/code).
            document.remove_associated_content_children(node_to_regenerate.id)

            parsed_parts = parse_response(reply_text)
            text_parts = [p["content"] for p in parsed_parts if p["type"] == "text"]
            text_content = "\n\n".join(text_parts)

            # THE SIMPLE 1-WAY TERNARY - NOT send_message's 3-way priority
            # chain. PLACEHOLDER_ASSISTANT_REASONING is NEVER touched by this
            # path. Exact match to legacy line 561:
            # `text_content if text_content else "[Generated Content]"`.
            placeholder_text = text_content if text_content else PLACEHOLDER_GENERATED_CONTENT
            document.update_chat_node_content(node_to_regenerate.id, placeholder_text)

            # NOTE: `document.` prefix is REQUIRED - bare add_code_node/
            # add_thinking_node would silently resolve to this same
            # register_canvas scope's own WS-intent wrapper closures instead of
            # raising (identical hazard already documented on send_message's
            # own _on_reply above this function).
            bx, by = node_to_regenerate.x, node_to_regenerate.y
            for part in parsed_parts:
                if part["type"] == "thinking":
                    document.add_thinking_node(
                        bx - MESSAGE_VERTICAL_SPACING, by + MESSAGE_VERTICAL_SPACING,
                        part["content"], parent_id=node_to_regenerate.id,
                    )
                elif part["type"] == "code":
                    document.add_code_node(
                        bx + MESSAGE_VERTICAL_SPACING, by + MESSAGE_VERTICAL_SPACING,
                        part["content"], part["language"], parent_id=node_to_regenerate.id,
                    )

            # last_chat_node_id: DELIBERATELY untouched. See §5.

        await agent_dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=history,
            on_reply=_on_reply,
            # R4.4: deliberately NOT streamed - see the design spec's own
            # deferral list. Regenerate replaces an EXISTING node's content
            # rather than creating a new one, and streaming it would light
            # up the Composer dock's live preview for a click on some other
            # node in the canvas, with no way for the frontend to tell that
            # apart from an actual Composer send.
            stream=False,
            # R6.1: same branch-system-prompt-override resolution as
            # send_message above - parent_id (not node_to_regenerate.id) so
            # the walk starts from the SAME node chat_branch_history just
            # built `history` from, a moment above.
            canvas_document=document,
            node_id=parent_id,
        )
        return node_to_regenerate.id

    async def _dispatch_image(parent_chat_node_id, prompt):
        # R4.4a: shared internal path for both generateImage and
        # regenerateImage below - each resolves its own (parent_chat_node_id,
        # prompt) pair from a different source-node kind, then both funnel
        # through this one dispatch + success-primitive call. Runs on
        # agent_dispatcher's INDEPENDENT self._image_requests slot, never
        # self._requests - see backend/agents.py's AgentDispatcher docstring
        # for why chat and image generation must be able to run concurrently.
        async def _on_reply(image_bytes):
            if parent_chat_node_id not in document.nodes:
                # Mid-flight delete, silent no-op - same posture as
                # regenerate_response's own liveness check above.
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

    async def run_web_research(node_id, query_text):
        if agent_dispatcher.is_web_research_busy():
            # Checked BEFORE touching document state: start_web_research_run
            # resets a node's progress/error fields unconditionally, and the
            # dispatcher only allows one web-research run at a time anyway -
            # without this early check, clicking Run on a different node
            # while one is already in flight would silently wipe that node's
            # prior result/error banner even though no new run actually starts.
            notifications.show("A web research request is already running.", "info")
            await bus.publish("notification")
            return None
        try:
            node = document.start_web_research_run(node_id, query_text)
        except SceneError:
            notifications.show("This node no longer exists.", "warning")
            await bus.publish("notification")
            return None
        await publish_scene()

        parent_edge = document._branch_parent_edge(node_id)
        branch_history = document.chat_branch_history(parent_edge.source) if parent_edge else []

        async def _on_progress(event):
            if node_id not in document.nodes:
                return
            document.apply_web_research_progress(node_id, event)
            await bus.publish("scene")

        async def _on_success(result):
            if node_id not in document.nodes:
                return
            document.complete_web_research_run(node_id, _research_result_wire(result))
            await bus.publish("scene")

        async def _on_failure(exc):
            if node_id not in document.nodes:
                return
            cancelled = type(exc).__name__ == "RequestCancelled"
            document.fail_web_research_run(node_id, cancelled=cancelled, message=str(exc))
            await bus.publish("scene")

        await agent_dispatcher.start_web_research(
            bus=bus,
            notifications_state=notifications,
            node=node,
            node_id=node_id,
            query=query_text,
            branch_history=branch_history,
            on_progress=_on_progress,
            on_success=_on_success,
            on_failure=_on_failure,
        )
        return node_id

    async def cancel_web_research_request(request_id):
        agent_dispatcher.cancel_web_research(request_id)

    async def send_artifact_message(node_id, text):
        # R5.2: the Artifact node's own Send action - appends a real user
        # instruction, then dispatches a real agent reply through
        # AgentDispatcher.start_artifact_reply. No try/except SceneError guard
        # here (an unknown node_id propagates as a generic WS intent error) -
        # same posture as send_conversation_message above, not
        # run_web_research's defensive pre-check pattern: there is no
        # persisted progress/error state on this node that an unguarded call
        # could corrupt, so a stale click racing a delete has nothing
        # destructive to protect against.
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

    # -- R6.2: Chart node ------------------------------------------------------
    #
    # Unlike every branch-point-child kind above (Web Research/Artifact/
    # Gitlink/Py-Coder/Execution Sandbox), Chart has no separate "create an
    # empty node, then run generation on it" split - generateChart is a
    # single combined create+generate action, so there is no addChartNode
    # intent at all: the SceneNode is only ever created (by
    # document.add_chart_node, in _on_success below) once real chart data
    # actually exists, mirroring legacy's own ChartWorkerThread flow (a
    # transient loading state anchored on the SOURCE node, not a pre-created
    # placeholder chart node - see graphlink_window_actions.py's
    # generate_chart/handle_chart_data).

    async def generate_chart(parent_node_id, chart_type):
        if not parent_node_id or parent_node_id not in document.nodes:
            notifications.show(
                "Please select a valid node to branch from before generating a chart.",
                "warning",
            )
            await bus.publish("notification")
            return None

        normalized_chart_type = str(chart_type or "").strip().lower()
        if normalized_chart_type not in SUPPORTED_CHART_TYPES:
            notifications.show(
                "Please choose a valid chart type before generating a chart.",
                "warning",
            )
            await bus.publish("notification")
            return None

        parent = document.nodes[parent_node_id]
        branch_history = document.chat_branch_history(parent_node_id)
        source_text = _chart_source_text(branch_history)

        result_holder: dict[str, str] = {}

        async def _on_success(result):
            try:
                chart_data = canonicalize_chart_data(result, normalized_chart_type)
                chart_error = ""
            except ChartDataError as exc:
                # R6.2 contract: ChartDataAgent's own validate_chart_data
                # pipeline (repair round trip, then heuristic fallback)
                # already tries hard to guarantee canonical output before
                # ever returning successfully - this is the rare defensive
                # case where it still somehow didn't. Never a silent no-op:
                # still create a real chart node with a minimal placeholder
                # shape and chart_error set, same "degrade gracefully, never
                # drop the request" contract as the agent's own internal
                # fallback chain.
                chart_data = _placeholder_chart_data(normalized_chart_type)
                chart_error = f"The generated chart data could not be validated: {exc}"
            node = document.add_chart_node(
                parent.x + MESSAGE_VERTICAL_SPACING,
                parent.y,
                parent_node_id,
                normalized_chart_type,
                chart_data,
                chart_error=chart_error,
            )
            result_holder["node_id"] = node.id
            await bus.publish("scene")

        def _on_failure(message):
            # Matches ChartWorkerThread's own error path (and this feature's
            # explicit contract): a genuinely unrecoverable agent-side
            # failure (get_response's own top-level "error" key, or a
            # timeout/exception) shows a notification and creates nothing -
            # start_chart_generation above already shows that notification,
            # so there is nothing left for this callback to do.
            pass

        await agent_dispatcher.start_chart_generation(
            bus=bus,
            notifications_state=notifications,
            node_id=parent_node_id,
            chart_type=normalized_chart_type,
            source_text=source_text,
            on_success=_on_success,
            on_failure=_on_failure,
        )
        return result_holder.get("node_id")

    async def _generate_note_from_node(source_node_id, note_kind, x_offset, y_offset):
        """R8a: shared path for generateKeyTakeaway and generateExplainerNote.

        Both take one chat node, run its text through an agent, and drop the
        result into a new note beside it - identical except for the agent and
        the note's offset, so they share one implementation rather than two
        that can drift.

        Source text is the node's OWN content, not the branch history that
        generate_chart uses: legacy's takeaway/explainer passed a single
        node's text, and widening that to the whole branch would change what
        the feature summarises.
        """
        if not source_node_id or source_node_id not in document.nodes:
            notifications.show("Please select a valid node first.", "warning")
            await bus.publish("notification")
            return None

        source = document.nodes[source_node_id]
        if source.kind != "chat":
            notifications.show("This node can't be summarised into a note.", "warning")
            await bus.publish("notification")
            return None
        if not source.content or not source.content.strip():
            notifications.show("The selected node has no text to summarise.", "warning")
            await bus.publish("notification")
            return None

        result_holder: dict[str, str] = {}

        async def _on_success(text):
            if source_node_id not in document.nodes:
                # Deleted mid-flight - silent no-op, same posture as
                # _dispatch_image's own liveness check.
                return
            note = document.add_note(source.x + x_offset, source.y + y_offset)
            document.set_note_content(note.id, text)
            # Legacy tinted these notes "Mid Gray" with an info-coloured
            # header. Both values come from the frontend's own palette
            # (GroupColorPicker's GROUP_MONO_COLORS/GROUP_NAMED_COLORS) since
            # the backend stores hex and never resolves a colour name. The
            # legacy note width of 400 is NOT ported: note width is not a
            # modeled field here (it is CSS-driven), so there is nothing to
            # set it on.
            document.set_group_color(note.id, NOTE_AGENT_BODY_COLOR, NOTE_AGENT_HEADER_COLOR)
            result_holder["node_id"] = note.id
            await bus.publish("scene")

        def _on_failure(message):
            # start_note_generation already surfaced the notification.
            pass

        await agent_dispatcher.start_note_generation(
            bus=bus,
            notifications_state=notifications,
            node_id=source_node_id,
            note_kind=note_kind,
            source_text=source.content,
            on_success=_on_success,
            on_failure=_on_failure,
        )
        return result_holder.get("node_id")

    async def generate_key_takeaway(source_node_id):
        return await _generate_note_from_node(source_node_id, "takeaway", NOTE_AGENT_X_OFFSET, 0)

    async def generate_explainer_note(source_node_id):
        # Offset vertically as well as horizontally so a takeaway and an
        # explainer generated from the same node don't land on top of each
        # other - the same 100px stagger legacy used.
        return await _generate_note_from_node(source_node_id, "explainer", NOTE_AGENT_X_OFFSET, 100)

    async def compare_branches(node_ids):
        """ADR-002 Workstream 1 ("Compare Branches") - the second sequenced
        item after "Branch from here" (that same workstream's fork
        primitive). Takes 2+ existing chat nodes, walks each one's own
        chat_branch_history, and drops a single agent-authored comparison
        into a new note linked back to every source branch (note.item_ids
        - see mark_branch_comparison_note).

        Deliberately no auto-selection fallback, unlike the single-node
        note agents above: there is no sensible single "the selected node"
        default here - the caller (App.tsx's own Compare Branches shortcut)
        must supply 2+ real ids up front, the same "the frontend already
        gathered React Flow's own multi-selection" contract create_frame/
        create_container already use."""
        ids = list(dict.fromkeys(str(i) for i in (node_ids or [])))  # de-dupe, preserve order
        if len(ids) < 2:
            notifications.show("Select at least 2 branches to compare.", "warning")
            await bus.publish("notification")
            return None

        sources = []
        for node_id in ids:
            node = document.nodes.get(node_id)
            if node is None or node.kind != "chat":
                notifications.show("Every selected node must be a real chat message to compare.", "warning")
                await bus.publish("notification")
                return None
            sources.append(node)

        branches = [
            (f"Branch {index + 1}", document.chat_branch_history(node.id))
            for index, node in enumerate(sources)
        ]
        formatted = _format_branches_for_comparison(branches)

        # Positioned below-and-right of the source branches, the same
        # "offset to the side" convention _generate_note_from_node uses for
        # a single source - averaged/maxed across all sources here since
        # there's more than one.
        avg_x = sum(node.x for node in sources) / len(sources)
        max_y = max(node.y for node in sources)

        result_holder: dict[str, str] = {}

        async def _on_success(text):
            if any(node_id not in document.nodes for node_id in ids):
                # A source was deleted mid-flight - same liveness posture as
                # _generate_note_from_node's own on_success guard.
                return
            note = document.add_note(avg_x + NOTE_AGENT_X_OFFSET, max_y)
            document.set_note_content(note.id, text)
            document.set_group_color(note.id, NOTE_AGENT_BODY_COLOR, NOTE_AGENT_HEADER_COLOR)
            document.mark_branch_comparison_note(note.id, ids)
            result_holder["node_id"] = note.id
            await bus.publish("scene")

        def _on_failure(message):
            # start_branch_comparison already surfaced the notification.
            pass

        await agent_dispatcher.start_branch_comparison(
            bus=bus,
            notifications_state=notifications,
            source_text=formatted,
            on_success=_on_success,
            on_failure=_on_failure,
        )
        return result_holder.get("node_id")

    async def synthesize_branches(node_ids, instructions):
        """ADR-002 Workstream 1 ("Synthesize Branches") - the third
        sequenced item in that workstream's own "fork -> compare ->
        synthesize -> status/lifecycle UI" order, following compare_
        branches above. Same validation contract as that function (2+
        de-duped ids, every one a real chat node, no auto-selection
        fallback), plus one more: instructions must be non-blank, since an
        empty steering prompt would leave the agent nothing to follow.

        Unlike Compare (whose result is a parentless note), Synthesize's
        result is a real CHAT node continuing the branch tree from the
        FIRST selected source - a genuine next step in the conversation,
        not a side annotation - so last_chat_node_id is updated to it
        exactly like an ordinary send. Every source is still recorded (via
        item_ids, the same multi-purpose-field reuse Compare's note
        already established) so full provenance survives even though only
        one edge can be structural. Provider/model are stamped from
        composer_document.route() - the same route a plain send would
        actually use - onto the result node (see SceneNode.provider/model's
        own comment)."""
        ids = list(dict.fromkeys(str(i) for i in (node_ids or [])))  # de-dupe, preserve order
        if len(ids) < 2:
            notifications.show("Select at least 2 branches to synthesize.", "warning")
            await bus.publish("notification")
            return None

        clean_instructions = str(instructions or "").strip()
        if not clean_instructions:
            notifications.show("Enter instructions for how to combine the branches.", "warning")
            await bus.publish("notification")
            return None

        sources = []
        for node_id in ids:
            node = document.nodes.get(node_id)
            if node is None or node.kind != "chat":
                notifications.show("Every selected node must be a real chat message to synthesize.", "warning")
                await bus.publish("notification")
                return None
            sources.append(node)

        branches = [
            (f"Branch {index + 1}", document.chat_branch_history(node.id))
            for index, node in enumerate(sources)
        ]
        formatted = _format_branches_for_comparison(branches)

        parent = sources[0]
        avg_x = sum(node.x for node in sources) / len(sources)
        max_y = max(node.y for node in sources)
        route = composer_document.route()

        result_holder: dict[str, str] = {}

        async def _on_success(text):
            if any(node_id not in document.nodes for node_id in ids):
                # A source was deleted mid-flight - same liveness posture as
                # compare_branches's own on_success guard.
                return
            node = document.add_chat_node(
                avg_x, max_y + MESSAGE_VERTICAL_SPACING, text, False, parent_id=parent.id,
            )
            document.mark_branch_synthesis(
                node.id, ids, clean_instructions, route.get("provider"), route.get("modelLabel"),
            )
            document.last_chat_node_id = node.id
            result_holder["node_id"] = node.id
            await bus.publish("scene")

        def _on_failure(message):
            # start_branch_synthesis already surfaced the notification.
            pass

        await agent_dispatcher.start_branch_synthesis(
            bus=bus,
            notifications_state=notifications,
            source_text=formatted,
            instructions=clean_instructions,
            on_success=_on_success,
            on_failure=_on_failure,
        )
        return result_holder.get("node_id")

    async def resize_chart(node_id, width, height):
        document.resize_chart(node_id, width, height)
        await publish_scene()

    async def toggle_chart_aspect_lock(node_id):
        document.toggle_chart_aspect_lock(node_id)
        await publish_scene()

    # -- R5.3: Gitlink node --------------------------------------------------
    #
    # Reuses the existing generic pending_request_id field as the busy/
    # in-flight marker for every Gitlink action (list repos, load tree,
    # import, build context, run, apply) - this is exactly that field's
    # documented purpose, and critically it is what makes the
    # fingerprint-recheck race-proof: a Run cannot start while an Apply
    # request_id occupies this node's slot, and vice versa.

    async def fetch_gitlink_repositories(node_id):
        node = document.nodes.get(node_id)
        if node is None or node.pending_request_id:
            notifications.show("Gitlink is busy for this node.", "info")
            await bus.publish("notification")
            return []
        return await agent_dispatcher.fetch_gitlink_repositories(
            bus=bus, notifications_state=notifications, node=node,
        )

    async def load_gitlink_repo_tree(node_id, repo, branch):
        node = document.nodes.get(node_id)
        if node is None or node.pending_request_id:
            notifications.show("Gitlink is busy for this node.", "info")
            await bus.publish("notification")
            return None
        result = await agent_dispatcher.load_gitlink_repo_tree(
            bus=bus, notifications_state=notifications, node=node, repo=repo, branch=branch,
        )
        if result is not None:
            document.store_gitlink_repo_tree(node_id, *result)
            await publish_scene()
        return node_id

    async def set_gitlink_local_root(node_id, local_root):
        document.set_gitlink_local_root(node_id, local_root)
        await publish_scene()

    async def pick_gitlink_local_root(node_id):
        # R8a (UI/UX audit POLISH finding #1): the field's own label used to
        # say "no browse - deferred", dating from before native_dialogs.py
        # existed. pick_folder is already generic and already used by
        # Settings' Ollama/Llama.cpp Scan Folder buttons - this just wires
        # the same primitive to Gitlink's own local-root field instead of
        # requiring the user to type a path by hand. A cancelled dialog is
        # a quiet no-op, matching every other pick_folder call site.
        node = document.nodes.get(node_id)
        if node is None:
            return
        directory = node.gitlink_local_root or os.path.expanduser("~")
        try:
            folder = await native_dialogs.pick_folder(directory=directory)
        except Exception as exc:  # noqa: BLE001 - a local folder path, not a credential
            notifications.show(f"Could not open the folder picker: {exc}", "error")
            await bus.publish("notification")
            return
        if not folder:
            return
        document.set_gitlink_local_root(node_id, folder)
        await publish_scene()

    async def import_gitlink_snapshot(node_id, repo, branch):
        node = document.nodes.get(node_id)
        if node is None or node.pending_request_id:
            notifications.show("Gitlink is busy for this node.", "info")
            await bus.publish("notification")
            return None
        result = await agent_dispatcher.import_gitlink_snapshot(
            bus=bus, notifications_state=notifications, node=node, repo=repo, branch=branch,
            local_root_hint=node.gitlink_local_root, imported_root_hint=node.gitlink_imported_root,
        )
        if result is not None:
            document.store_gitlink_snapshot_root(node_id, *result)
            await publish_scene()
        return node_id

    async def build_gitlink_context(node_id, scope_mode, selected_paths):
        node = document.nodes.get(node_id)
        if node is None or node.pending_request_id:
            notifications.show("Gitlink is busy for this node.", "info")
            await bus.publish("notification")
            return None
        result = await agent_dispatcher.build_gitlink_context(
            bus=bus, notifications_state=notifications, node=node,
            scope_mode=scope_mode, selected_paths=list(selected_paths),
        )
        if result is not None:
            document.store_gitlink_context(node_id, scope_mode=scope_mode,
                                            selected_paths=selected_paths, **result)
            await publish_scene()
        return node_id

    async def fetch_gitlink_context(node_id):
        return document.fetch_gitlink_context_xml(node_id)

    async def run_gitlink_change_set(node_id, task_prompt):
        node_for_check = document.nodes.get(node_id)
        if node_for_check is not None and node_for_check.pending_request_id:
            notifications.show("Gitlink is already busy for this node.", "info")
            await bus.publish("notification")
            return None
        # R5.3 post-review FIX 4(b): claim the busy slot with a placeholder
        # SYNCHRONOUSLY, in the same stretch as the busy pre-check just
        # above - before document.start_gitlink_run or any await - so a
        # second concurrent call for this SAME node_id can never pass that
        # same pre-check during the `await publish_scene()` gap below.
        # agent_dispatcher.start_gitlink_run (the ONLY caller of this dict
        # entry for this node_id, invoked just below) recognizes this exact
        # placeholder and overwrites it with the real request_id, still
        # synchronously - see that method's own docstring.
        if node_for_check is not None:
            node_for_check.pending_request_id = _GITLINK_RUN_CLAIM_PLACEHOLDER
        try:
            node = document.start_gitlink_run(node_id, task_prompt)
        except SceneError:
            # Node deleted (or wrong-kind) concurrently with the claim above -
            # the placeholder must not linger on a node this handler is
            # about to give up on.
            if node_for_check is not None:
                node_for_check.pending_request_id = None
            notifications.show("This node no longer exists.", "warning")
            await bus.publish("notification")
            return None
        await publish_scene()

        def _on_success(proposal_markdown, pending_changes, preview_text, fingerprint, local_root):
            document.complete_gitlink_run(node_id, proposal_markdown, pending_changes,
                                           preview_text, fingerprint, local_root)

        def _on_failure(message):
            document.fail_gitlink_run(node_id, message)

        await agent_dispatcher.start_gitlink_run(
            bus=bus, notifications_state=notifications, node=node, node_id=node_id,
            repo=node.gitlink_repo, branch=node.gitlink_branch,
            scope_mode=node.gitlink_scope_mode, task_prompt=task_prompt,
            context_xml=node.gitlink_context_xml, context_summary=node.gitlink_context_summary,
            local_root=node.gitlink_local_root,
            on_success=_on_success, on_failure=_on_failure,
        )
        return node_id

    async def cancel_gitlink_request(request_id):
        agent_dispatcher.cancel_gitlink(request_id)

    async def apply_gitlink_changes(node_id, fingerprint):
        node = document.nodes.get(node_id)
        if node is None:
            notifications.show("This node no longer exists.", "warning")
            await bus.publish("notification")
            return None

        def _on_success(written_files):
            document.complete_gitlink_apply(node_id, written_files)

        def _on_failure(message):
            document.fail_gitlink_apply(node_id, message)

        await agent_dispatcher.start_gitlink_apply(
            bus=bus, notifications_state=notifications, node=node, node_id=node_id,
            client_fingerprint=fingerprint, local_root=node.gitlink_local_root,
            on_success=_on_success, on_failure=_on_failure,
        )
        return node_id

    bus.register_intent("scene", "fetchGitlinkRepositories", fetch_gitlink_repositories)
    bus.register_intent("scene", "loadGitlinkRepoTree", load_gitlink_repo_tree)
    bus.register_intent("scene", "setGitlinkLocalRoot", set_gitlink_local_root)
    bus.register_intent("scene", "pickGitlinkLocalRoot", pick_gitlink_local_root)
    bus.register_intent("scene", "importGitlinkSnapshot", import_gitlink_snapshot)
    bus.register_intent("scene", "buildGitlinkContext", build_gitlink_context)
    bus.register_intent("scene", "fetchGitlinkContext", fetch_gitlink_context)
    bus.register_intent("scene", "runGitlinkChangeSet", run_gitlink_change_set)
    bus.register_intent("scene", "cancelGitlinkRequest", cancel_gitlink_request)
    # CRITICAL, load-bearing property: applyGitlinkChanges takes ONLY
    # (node_id, fingerprint) as WS intent arguments - there must be NO
    # changes/pending_changes parameter anywhere in this signature or the
    # dispatcher method it calls. This closes the most obvious
    # content-injection bypass by construction, not by a runtime check: the
    # only content that ever reaches apply_change_set is server-held,
    # already-normalized node.gitlink_pending_changes.
    bus.register_intent("scene", "applyGitlinkChanges", apply_gitlink_changes)

    # -- R5.4: Py-Coder node ---------------------------------------------------

    async def set_pycoder_mode(node_id, mode):
        document.set_pycoder_mode(node_id, mode)
        await publish_scene()

    async def run_pycoder(node_id, input_text):
        # R5.3 post-review FIX 4(b)'s own Run-vs-Run race fix, reused
        # verbatim for this new kind: claim the busy slot with a shared
        # placeholder SYNCHRONOUSLY, in the same stretch as the busy
        # pre-check just above - before document.start_pycoder_run or any
        # await - so a second concurrent runPyCoder for this SAME node_id
        # can never pass the same pre-check during the `await
        # publish_scene()` gap below. Critically, this placeholder stays
        # claimed for the ENTIRE span from here through generation, through
        # the human-approval pause, through execution, through analysis - so
        # a second runPyCoder DURING the pause is refused by this SAME
        # check, no new logic needed for that case specifically (see the
        # R5.4 design spec's own section on this).
        node_for_check = document.nodes.get(node_id)
        if node_for_check is not None and node_for_check.pending_request_id:
            notifications.show("Py-Coder is already busy for this node.", "info")
            await bus.publish("notification")
            return None
        if node_for_check is not None:
            node_for_check.pending_request_id = _CODE_EXEC_RUN_CLAIM_PLACEHOLDER
        try:
            node = document.start_pycoder_run(node_id, input_text)
        except SceneError:
            if node_for_check is not None:
                node_for_check.pending_request_id = None
            notifications.show("This node no longer exists.", "warning")
            await bus.publish("notification")
            return None
        await publish_scene()

        parent_edge = document._branch_parent_edge(node_id)
        branch_history = document.chat_branch_history(parent_edge.source) if parent_edge else []

        def _on_success(code, output, analysis, last_run_failed):
            document.complete_pycoder_run(node_id, code, output, analysis, last_run_failed)

        def _on_failure(message):
            document.fail_pycoder_run(node_id, message)

        await agent_dispatcher.start_pycoder_run(
            bus=bus, notifications_state=notifications, node=node, node_id=node_id,
            mode=node.pycoder_mode, prompt=node.pycoder_prompt, code=node.pycoder_code,
            conversation_history=branch_history,
            on_success=_on_success, on_failure=_on_failure,
        )
        return node_id

    async def cancel_pycoder_request(request_id):
        agent_dispatcher.cancel_pycoder(request_id)

    bus.register_intent("scene", "setPyCoderMode", set_pycoder_mode)
    bus.register_intent("scene", "runPyCoder", run_pycoder)
    bus.register_intent("scene", "cancelPyCoderRequest", cancel_pycoder_request)

    # -- R5.4: Execution Sandbox node -------------------------------------------

    async def set_code_sandbox_requirements(node_id, requirements_text):
        document.set_code_sandbox_requirements(node_id, requirements_text)
        await publish_scene()

    async def run_code_sandbox(node_id, input_text):
        # Same busy-claim-placeholder pattern as run_pycoder above (and
        # run_gitlink_change_set before it) - see that function's own
        # comment for the exact race this closes.
        node_for_check = document.nodes.get(node_id)
        if node_for_check is not None and node_for_check.pending_request_id:
            notifications.show("Virtual Environment Runner is already busy for this node.", "info")
            await bus.publish("notification")
            return None
        if node_for_check is not None:
            node_for_check.pending_request_id = _CODE_EXEC_RUN_CLAIM_PLACEHOLDER
        try:
            node = document.start_code_sandbox_run(node_id, input_text)
        except SceneError:
            if node_for_check is not None:
                node_for_check.pending_request_id = None
            notifications.show("This node no longer exists.", "warning")
            await bus.publish("notification")
            return None
        await publish_scene()

        parent_edge = document._branch_parent_edge(node_id)
        branch_history = document.chat_branch_history(parent_edge.source) if parent_edge else []

        def _on_success(code, output, analysis):
            document.complete_code_sandbox_run(node_id, code, output, analysis)

        def _on_failure(message):
            document.fail_code_sandbox_run(node_id, message)

        await agent_dispatcher.start_code_sandbox_run(
            bus=bus, notifications_state=notifications, node=node, node_id=node_id,
            sandbox_id=node.code_sandbox_sandbox_id,
            prompt=node.code_sandbox_prompt, existing_code=node.code_sandbox_code,
            requirements_manifest=node.code_sandbox_requirements,
            conversation_history=branch_history,
            on_success=_on_success, on_failure=_on_failure,
        )
        return node_id

    async def cancel_code_sandbox_request(request_id):
        agent_dispatcher.cancel_code_sandbox(request_id)

    bus.register_intent("scene", "setCodeSandboxRequirements", set_code_sandbox_requirements)
    bus.register_intent("scene", "runCodeSandbox", run_code_sandbox)
    bus.register_intent("scene", "cancelCodeSandboxRequest", cancel_code_sandbox_request)

    # -- R5.4: shared approve/deny - one request_id namespace across both kinds

    async def approve_code_execution(request_id):
        agent_dispatcher.approve_code_execution(request_id)

    async def deny_code_execution(request_id):
        agent_dispatcher.deny_code_execution(request_id)

    bus.register_intent("scene", "approveCodeExecution", approve_code_execution)
    bus.register_intent("scene", "denyCodeExecution", deny_code_execution)

    # -- R6.1: Notes/Frames/Containers -----------------------------------------

    async def add_note(x, y, is_system_prompt=False, is_summary_note=False):
        node = document.add_note(
            x, y, is_system_prompt=is_system_prompt, is_summary_note=is_summary_note,
        )
        await publish_scene()
        return node.id

    async def set_note_content(node_id, content):
        document.set_note_content(node_id, content)
        await publish_scene()

    async def create_frame(item_ids):
        node = document.create_frame(list(item_ids))
        await publish_scene()
        return node.id

    async def create_container(item_ids):
        node = document.create_container(list(item_ids))
        await publish_scene()
        return node.id

    async def set_group_label(node_id, text):
        document.set_group_label(node_id, text)
        await publish_scene()

    async def set_group_color(node_id, color, header_color):
        document.set_group_color(node_id, color, header_color)
        await publish_scene()

    async def toggle_frame_lock(node_id):
        document.toggle_frame_lock(node_id)
        await publish_scene()

    async def toggle_group_collapsed(node_id):
        document.toggle_group_collapsed(node_id)
        await publish_scene()

    async def resize_frame(node_id, width, height):
        document.resize_frame(node_id, width, height)
        await publish_scene()

    async def fit_frame_to_content(node_id):
        document.fit_frame_to_content(node_id)
        await publish_scene()

    async def ungroup(node_id):
        document.ungroup(node_id)
        await publish_scene()

    bus.register_intent("scene", "addNote", add_note)
    bus.register_intent("scene", "setNoteContent", set_note_content)
    bus.register_intent("scene", "createFrame", create_frame)
    bus.register_intent("scene", "createContainer", create_container)
    bus.register_intent("scene", "setGroupLabel", set_group_label)
    bus.register_intent("scene", "setGroupColor", set_group_color)
    bus.register_intent("scene", "toggleFrameLock", toggle_frame_lock)
    bus.register_intent("scene", "toggleGroupCollapsed", toggle_group_collapsed)
    bus.register_intent("scene", "resizeFrame", resize_frame)
    bus.register_intent("scene", "fitFrameToContent", fit_frame_to_content)
    bus.register_intent("scene", "ungroup", ungroup)

    async def move_node(node_id, x, y):
        document.move_node(node_id, x, y)
        await publish_scene()

    async def move_nodes(positions):
        # positions: a JSON array of [node_id, x, y] triples - see
        # SceneDocument.move_nodes's own docstring for why a group drag's
        # commit uses this batched intent instead of N calls to moveNode.
        document.move_nodes([(p[0], p[1], p[2]) for p in positions])
        await publish_scene()

    async def remove_nodes(node_ids):
        ids = list(node_ids)
        # R5.4: a deleted Py-Coder node's REPL subprocess must not outlive
        # it - kind is captured BEFORE document.remove_nodes pops the node,
        # since afterward there is nothing left to read it from.
        pycoder_ids = [
            node_id for node_id in ids
            if document.nodes.get(node_id) is not None and document.nodes[node_id].kind == "pycoder"
        ]
        # R5.4 post-review FIX 2: a deleted pycoder/code_sandbox node's
        # DISPATCHER-SIDE in-flight request must not outlive it either - captured
        # here, BEFORE document.remove_nodes pops the node, for the same reason
        # pycoder_ids above is. dispose_pycoder_repl alone only tears down the
        # REPL subprocess; it does nothing about a request parked on `await
        # approval_future` in AgentDispatcher._pycoder_requests/
        # _code_sandbox_requests, which has NO timeout by design (the whole
        # point is "wait for a human, however long that takes"). Without this,
        # deleting a node mid-approval-pause would leave that future - and the
        # asyncio.Task awaiting it - alive forever, and a stale/duplicate
        # approve-or-deny message arriving later could still resolve it, lazily
        # recreating a REPL or spinning up a fresh sandbox subprocess for a
        # node_id no longer present anywhere in the scene.
        code_exec_cancels = [
            (document.nodes[node_id].kind, document.nodes[node_id].pending_request_id)
            for node_id in ids
            if document.nodes.get(node_id) is not None
            and document.nodes[node_id].kind in ("pycoder", "code_sandbox")
            and document.nodes[node_id].pending_request_id
        ]
        document.remove_nodes(ids)
        for node_id in pycoder_ids:
            await agent_dispatcher.dispose_pycoder_repl(node_id)
        for kind, request_id in code_exec_cancels:
            # cancel_pycoder/cancel_code_sandbox resolve any pending
            # approval_future with False (exactly like a manual Cancel/Deny)
            # and pop the request out of the dispatcher's own dict - a safe
            # no-op if request_id does not name a live entry (e.g. it was only
            # ever the synchronous busy-claim placeholder, never a real
            # dispatcher request_id, or the request already finished on its
            # own between the capture above and here).
            if kind == "pycoder":
                agent_dispatcher.cancel_pycoder(request_id)
            else:
                agent_dispatcher.cancel_code_sandbox(request_id)
        await publish_scene()

    async def connect_nodes(source, target):
        edge = document.connect(source, target)
        await publish_scene()
        return edge.id

    async def remove_edges(edge_ids):
        document.remove_edges(list(edge_ids))
        await publish_scene()

    async def add_pin(title, x, y, note=""):
        record = NavigationPinRecord.create(title=title, x=x, y=y, note=note)
        document.pins.add(record)
        await publish_scene()
        return record.pin_id

    async def move_pin(pin_id, x, y):
        document.pins.move(pin_id, x, y)
        await publish_scene()

    async def remove_pin(pin_id):
        document.pins.remove(pin_id)
        await publish_scene()

    async def update_pin(pin_id, title, note):
        # NavigationPinRecord.create() validation (non-empty/length-bounded
        # title, length-bounded note) runs via with_updates -> create's own
        # field validators, same as add_pin's path - a bad edit raises
        # NavigationPinValidationError, which is a ValueError subclass and
        # therefore already reported to the caller as an intent error.
        document.pins.update(pin_id, title=str(title), note=str(note))
        await publish_scene()

    async def set_snap_to_grid(enabled):
        document.snap_to_grid = bool(enabled)
        await publish_scene()

    async def set_fade_connections(enabled):
        document.fade_connections_enabled = bool(enabled)
        await publish_scene()

    async def set_orthogonal_routing(enabled):
        document.orthogonal_routing = bool(enabled)
        await publish_scene()

    async def set_smart_guides(enabled):
        document.smart_guides = bool(enabled)
        await publish_scene()

    async def set_drag_factor(factor):
        document.set_drag_factor(factor)
        await publish_scene()

    async def set_view_state(zoom_factor, scroll_x, scroll_y):
        document.set_view_state(zoom_factor, scroll_x, scroll_y)
        await publish_scene()

    bus.register_intent("scene", "addNode", add_node)
    bus.register_intent("scene", "addChatNode", add_chat_node)
    bus.register_intent("scene", "addCodeNode", add_code_node)
    bus.register_intent("scene", "addDocumentNode", add_document_node)
    bus.register_intent("scene", "addThinkingNode", add_thinking_node)
    bus.register_intent("scene", "addHtmlNode", add_html_node)
    bus.register_intent("scene", "setHtmlSplitterState", set_html_splitter_state)
    bus.register_intent("scene", "addImageNode", add_image_node)
    bus.register_intent("scene", "addConversationNode", add_conversation_node)
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
    bus.register_intent("scene", "sendMessage", send_message)
    bus.register_intent("scene", "regenerateResponse", regenerate_response)
    # R4.4a: "Generate Image from Text" (ChatNode) and "Regenerate Image"
    # (ImageNode) - two intents because the two entry points resolve from
    # genuinely different source-node kinds with different validation rules,
    # both funneling through the shared _dispatch_image helper above.
    bus.register_intent("scene", "generateImage", generate_image)
    bus.register_intent("scene", "regenerateImage", regenerate_image)
    # R5.1: Web Research node run/cancel - node creation itself lives in
    # backend/plugins.py's executePlugin (the "Web Research" branch), not
    # here; these two intents drive an EXISTING web_research-kind node.
    bus.register_intent("scene", "runWebResearch", run_web_research)
    bus.register_intent("scene", "cancelWebResearchRequest", cancel_web_research_request)
    # R5.2: Artifact/Drafter Send/cancel - node creation itself lives in
    # backend/plugins.py's executePlugin (the "Artifact / Drafter" branch),
    # not here; these two intents drive an EXISTING artifact-kind node, same
    # posture as Web Research's own runWebResearch/cancelWebResearchRequest
    # pair above.
    bus.register_intent("scene", "sendArtifactMessage", send_artifact_message)
    bus.register_intent("scene", "cancelArtifactRequest", cancel_artifact_request)
    # R6.2: Chart - a single combined create+generate action, unlike every
    # node-creation flow above - see generate_chart's own docstring.
    bus.register_intent("scene", "generateChart", generate_chart)
    # R8a: the two note agents restored from the deleted Qt app - see
    # graphlink_note_agent.py's own docstring for why they were dead stubs.
    bus.register_intent("scene", "generateKeyTakeaway", generate_key_takeaway)
    bus.register_intent("scene", "generateExplainerNote", generate_explainer_note)
    bus.register_intent("scene", "compareBranches", compare_branches)
    bus.register_intent("scene", "synthesizeBranches", synthesize_branches)
    bus.register_intent("scene", "resizeChart", resize_chart)
    bus.register_intent("scene", "toggleChartAspectLock", toggle_chart_aspect_lock)
    bus.register_intent("scene", "moveNode", move_node)
    bus.register_intent("scene", "moveNodes", move_nodes)
    bus.register_intent("scene", "removeNodes", remove_nodes)
    bus.register_intent("scene", "connectNodes", connect_nodes)
    bus.register_intent("scene", "removeEdges", remove_edges)
    bus.register_intent("scene", "addPin", add_pin)
    bus.register_intent("scene", "movePin", move_pin)
    bus.register_intent("scene", "removePin", remove_pin)
    bus.register_intent("scene", "updatePin", update_pin)
    bus.register_intent("scene", "setSnapToGrid", set_snap_to_grid)
    bus.register_intent("scene", "setFadeConnections", set_fade_connections)
    # Intent name matches the legacy GridControlBridge's own
    # setOrthogonalConnections Slot name 1:1, same convention as
    # setSnapToGrid/setFadeConnections above - the Python function name above
    # doesn't need to match.
    bus.register_intent("scene", "setOrthogonalConnections", set_orthogonal_routing)
    bus.register_intent("scene", "setSmartGuides", set_smart_guides)
    bus.register_intent("scene", "setDragFactor", set_drag_factor)
    bus.register_intent("scene", "setViewState", set_view_state)
    # R4.3: per-node cancel for a ConversationNode's in-flight reply. Reuses
    # the exact intent NAME "cancelChatRequest" already registered on the
    # "app-composer" topic by R4.2 - SessionBus keys handlers by the
    # (topic, intent) tuple (see backend/events.py), so this is a second,
    # independent registration on a different topic, not a collision. It
    # points at the same underlying agent_dispatcher.cancel, which is purely
    # request_id-keyed and does not care which topic invoked it.
    bus.register_intent("scene", "cancelChatRequest", lambda request_id: agent_dispatcher.cancel(request_id))

    async def organize_nodes():
        document.organize()
        await publish_scene()

    async def set_font_family(family):
        document.set_font(family=family)
        await publish_scene()

    async def set_font_size(size_pt):
        document.set_font(size_pt=size_pt)
        await publish_scene()

    async def set_font_color(color_hex):
        document.set_font(color=color_hex)
        await publish_scene()

    bus.register_intent("scene", "organizeNodes", organize_nodes)
    # Font intent names == FontControlBridge's @Slot names, same 1:1 rule as
    # grid; they live on the scene topic because the VALUES are scene state.
    bus.register_intent("scene", "setFontFamily", set_font_family)
    bus.register_intent("scene", "setFontSize", set_font_size)
    bus.register_intent("scene", "setFontColor", set_font_color)

    # -- grid intents (names == GridControlBridge @Slot names) -------------

    async def set_grid_size(size):
        document.grid.grid_size = int(size)
        await publish_grid()

    async def set_grid_opacity_percent(percent):
        document.grid.grid_opacity = max(0, min(100, int(percent))) / 100.0
        await publish_grid()

    async def set_grid_style(style):
        if style not in GRID_STYLE_PRESETS:
            raise SceneError(f"unknown grid style: {style}")
        document.grid.grid_style = str(style)
        await publish_grid()

    async def set_grid_color(color_hex):
        document.grid.grid_color = str(color_hex)
        await publish_grid()

    bus.register_intent("grid-control", "setGridSize", set_grid_size)
    bus.register_intent("grid-control", "setGridOpacityPercent", set_grid_opacity_percent)
    bus.register_intent("grid-control", "setGridStyle", set_grid_style)
    bus.register_intent("grid-control", "setGridColor", set_grid_color)

    return document
