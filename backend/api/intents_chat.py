"""ADR-002 stage 2.6: the Composer's real Send/Regenerate actions.

Relocated VERBATIM from backend/canvas.py's former register_canvas
(closures at lines 473-735, cancelChatRequest lambda at line 1759;
registration calls from the former tail block at lines 1702-1703, 1759) -
pure code motion, no behavior change.

Imports `_history_token_text` back from backend.canvas rather than
duplicating it - that helper (and its 5 siblings) deliberately stayed in
canvas.py rather than moving here, since backend/session_load.py and
backend/tests/test_canvas.py both import some of them directly from
backend.canvas (see canvas.py's own module docstring). This DOES create a
two-way relationship between canvas.py and this module (canvas.py's
register_canvas calls register_chat_intents; this module imports
_history_token_text back from canvas.py) - resolved by import ORDER, not
avoided: canvas.py imports this module only AFTER _history_token_text's
own def, never at the very top of the file alongside its other imports.
See canvas.py's own comment at that import site before changing either
file's import order.

PRESERVED VERBATIM, not fixed: regenerate_response's own _on_reply never
calls bus.publish("scene") on its success path (only the empty-reply
branch publishes "notification") - an asymmetry with every sibling
mutation intent in this file, found during this stage's own scoping pass.
This is a genuine, pre-existing behavior gap, not something introduced by
this relocation - see this ADR's own "Consequences" section on why a pure
code-motion stage must never smuggle in a drive-by fix. File a defect
against a future increment if this needs fixing.
"""

from __future__ import annotations

from backend.agents import AgentDispatcher
from backend.api._shared import make_publish_scene, make_publish_token_counter
from backend.composer import ComposerDocument
from backend.domain.graph import SceneDocument
from backend.domain.model import MESSAGE_VERTICAL_SPACING, SceneError
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.response_parsing import (
    parse_response,
    PLACEHOLDER_ASSISTANT_REASONING,
    PLACEHOLDER_EMPTY_RESPONSE,
    PLACEHOLDER_GENERATED_CONTENT,
)
from backend.token_counter import TokenCounterState


def register_chat_intents(
    bus: SessionBus,
    document: SceneDocument,
    notifications: NotificationState,
    agent_dispatcher: AgentDispatcher,
    composer_document: ComposerDocument,
    token_counter: TokenCounterState,
) -> None:
    # Deferred import - see this module's own docstring for why
    # _history_token_text cannot be imported at module level here.
    from backend.canvas import _history_token_text

    publish_scene = make_publish_scene(bus)
    publish_token_counter = make_publish_token_counter(bus)

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

            # NOTE: these two calls MUST use the `document.` prefix. Before
            # ADR-002 stage 2.6, a bare `add_code_node(...)`/
            # `add_thinking_node(...)` here would have silently resolved to
            # register_canvas's own same-scope async WS-intent wrapper
            # closures of the same name instead of raising a NameError -
            # producing an unawaited coroutine that never ran and never
            # errored, so no node would be created and nothing would look
            # wrong until the scene state was actually inspected. Those
            # wrapper closures now live in backend/api/intents_nodes.py, a
            # different module - a bare call here would raise NameError
            # immediately instead, a strictly SAFER failure mode than the
            # original silent one. The `document.` prefix requirement is
            # kept regardless, since correctness should not depend on which
            # of the two failure modes happens to be true today.
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

            # NOTE: `document.` prefix is REQUIRED - see send_message's own
            # identical _on_reply comment earlier in this same file for the
            # full history of this hazard and why the prefix requirement is
            # kept regardless of which failure mode a bare call would hit.
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

    bus.register_intent("scene", "sendMessage", send_message)
    bus.register_intent("scene", "regenerateResponse", regenerate_response)
    # R4.3: per-node cancel for a ConversationNode's in-flight reply. Reuses
    # the exact intent NAME "cancelChatRequest" already registered on the
    # "app-composer" topic by R4.2 - SessionBus keys handlers by the
    # (topic, intent) tuple (see backend/events.py), so this is a second,
    # independent registration on a different topic, not a collision. It
    # points at the same underlying agent_dispatcher.cancel, which is purely
    # request_id-keyed and does not care which topic invoked it.
    bus.register_intent("scene", "cancelChatRequest", lambda request_id: agent_dispatcher.cancel(request_id))
