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

# ADR-021 stage 21.5: two document attachments on one message fan out
# sideways instead of stacking, the same convention tools_graph.py's own
# sibling placement uses for a builder's parallel children.
DOCUMENT_ATTACHMENT_HORIZONTAL_SPACING = 360


def _merge_staged_attachments(text: str, staged: list) -> "tuple[str, list | None]":
    """R8a's attachment merge: a document attachment's extracted text is
    appended to the message's own plain text, while image/audio attachments
    become multimodal content parts.

    Pure - it reads the staged list and returns (full_text, content_parts),
    touching no document state. content_parts stays None when nothing
    multimodal is staged, which is what keeps a plain send byte-identical to
    the pre-R8a shape (a plain string content, not a one-element list).

    Split out of register_chat_intents at ADR-021 stage 21.5, alongside
    _promote_document_attachments below, to keep that function under the
    ADR-002 stage 2.6/2.7 300-line cap.
    """
    full_text = text
    for item in staged:
        if item.extracted_text is not None:
            full_text += (
                f"\n\n--- Attached: {item.name} ({item.context_label}) ---\n"
                f"{item.extracted_text}\n--- end attachment ---"
            )
    media_parts = [item.content_part for item in staged if item.content_part is not None]
    content_parts = [{"type": "text", "text": full_text}, *media_parts] if media_parts else None
    return full_text, content_parts


def _promote_document_attachments(document: SceneDocument, node, staged: list) -> None:
    """ADR-021 stage 21.5: a document attachment also lands as a real
    document node under the message it was attached to.

    Until now its extracted text was inlined into the message and then
    thrown away: nothing on the canvas recorded that a file had been
    attached at all, DocumentNodeView had no user-facing creation path
    whatsoever (sceneStore.addDocumentNode has never had a caller outside
    its own test), and the file could not be exported, searched, or ingested
    afterwards.

    Deliberately ADDITIVE: send_message's inline attachment text is
    untouched, so the reply's context and every token count stay
    byte-identical to before this existed. The node hangs off the user's
    chat node as a CHILD, which keeps it off that node's own ancestor chain
    - chat_branch_history walks parents, so the document node is never
    visited by the reply's history walk and its text cannot be counted
    twice.

    Image/audio attachments are deliberately out of scope: they already
    render inside the message itself as real content parts, so a second copy
    on the canvas would be duplication, not recovery.

    Split out of register_chat_intents rather than inlined at its one call
    site to keep that function under the ADR-002 stage 2.6/2.7 300-line cap.
    """
    promotable = [
        attachment for attachment in staged
        if attachment.kind == "document" and attachment.extracted_text is not None
    ]
    for index, attachment in enumerate(promotable):
        document.record_command(
            "addDocumentNode", "user",
            lambda attachment=attachment, index=index: document.add_document_node(
                node.x + index * DOCUMENT_ATTACHMENT_HORIZONTAL_SPACING,
                node.y + MESSAGE_VERTICAL_SPACING,
                attachment.name,
                attachment.extracted_text or "",
                "document",
                node.id,
                file_path=attachment.path,
                # `attachment` is a backend/attachments.py StagedAttachment,
                # never a SceneNode - see the matching, file-scoped carve-out
                # in tests/test_node_state_migration.py's own
                # _KNOWN_NON_NODE_FIELD_ACCESS_SHAPES.
                mime_type=attachment.mime_type,
                byte_size=attachment.byte_size,
                preview_label=attachment.context_label,
            ),
            node_ids=[node.id],
        )
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.response_parsing import (
    parse_response,
    PLACEHOLDER_ASSISTANT_REASONING,
    PLACEHOLDER_EMPTY_RESPONSE,
    PLACEHOLDER_GENERATED_CONTENT,
)
from backend.token_counter import TokenCounterState


# ADR-010 stage 10.1: these two live at module level rather than as closures
# inside register_chat_intents for the reason ADR-002 stage 2.7 established -
# tests/test_register_function_length.py caps every register* function at 300
# lines, and wrapping both reply paths in record_command pushed this one over.
# They take `document` explicitly instead of capturing it, exactly like the
# intents_*.py split itself did.
#
# NOTE: the calls below MUST use the `document.` prefix. Before ADR-002 stage
# 2.6, a bare `add_code_node(...)`/`add_thinking_node(...)` would have silently
# resolved to register_canvas's own same-scope async WS-intent wrapper closures
# of the same name instead of raising NameError - producing an unawaited
# coroutine that never ran and never errored, so no node would be created and
# nothing would look wrong until the scene state was actually inspected. At
# module level here that hazard is structurally gone, but the prefix is kept
# because correctness should not depend on which failure mode happens to be
# true today.
def _build_reply_nodes(document, parent_node, placeholder_text, parsed_parts):
    """Creates the assistant's reply node plus every thinking/code child the
    parsed reply calls for, returning the reply node. Wrapped by ONE
    record_command at each call site, so a single undo reverses the whole
    reply rather than peeling off one child per Ctrl+Z."""
    ax, ay = parent_node.x, parent_node.y + MESSAGE_VERTICAL_SPACING
    ai = document.add_chat_node(ax, ay, placeholder_text, False, parent_id=parent_node.id)
    for part in parsed_parts:
        if part["type"] == "thinking":
            document.add_thinking_node(
                ax - MESSAGE_VERTICAL_SPACING, ay + MESSAGE_VERTICAL_SPACING,
                part["content"], parent_id=ai.id,
            )
        elif part["type"] == "code":
            document.add_code_node(
                ax + MESSAGE_VERTICAL_SPACING, ay + MESSAGE_VERTICAL_SPACING,
                part["content"], part["language"], parent_id=ai.id,
            )
    return ai


def _regenerate_in_place(document, node_to_regenerate, reply_text):
    """Regenerate's teardown/parse/mutate sequence, in the exact legacy step
    order: tear down the old content children FIRST (unconditionally, even if
    the new reply parses to nothing), then replace the node's own content,
    then rebuild children from the new parse."""
    document.remove_associated_content_children(node_to_regenerate.id)

    parsed_parts = parse_response(reply_text)
    text_parts = [p["content"] for p in parsed_parts if p["type"] == "text"]
    text_content = "\n\n".join(text_parts)

    # THE SIMPLE 1-WAY TERNARY - NOT send_message's 3-way priority chain.
    # PLACEHOLDER_ASSISTANT_REASONING is NEVER touched by this path. Exact
    # match to legacy line 561:
    # `text_content if text_content else "[Generated Content]"`.
    placeholder_text = text_content if text_content else PLACEHOLDER_GENERATED_CONTENT
    document.update_chat_node_content(node_to_regenerate.id, placeholder_text)

    # ADR-006 stage 6.8 review fix: the old reply's token stamps describe
    # content that no longer exists - reset to None ("not reported"); fresh
    # usage for the NEW reply, when the provider reports it, overwrites via
    # _make_on_usage (which _dispatch invokes after this commit).
    node_to_regenerate.state.prompt_tokens = None
    node_to_regenerate.state.completion_tokens = None

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


def _land_partial_reply_node(document, parent_node, partial_text):
    """ADR-006 stage 6.4 (H5): a Composer send's stream died mid-reply - land
    the accumulated text as a real assistant node flagged incomplete instead
    of losing it. NOT parsed into thinking/code children (a truncated reply
    can end inside an unterminated fence); the user retries via the node's
    own Regenerate action, which is why last_chat_node_id/parent wiring must
    match the complete path (_build_reply_nodes) exactly. Module-level for
    the same 300-line-cap reason as its two siblings above."""
    def _commit():
        ax, ay = parent_node.x, parent_node.y + MESSAGE_VERTICAL_SPACING
        ai = document.add_chat_node(ax, ay, partial_text, False, parent_id=parent_node.id)
        ai.state.response_incomplete = True
        return ai

    ai_node, _command = document.record_command(
        "chatReply", "agent", _commit, node_ids=[parent_node.id],
    )
    document.last_chat_node_id = ai_node.id


def _snapshot_active_provider_model(agent_dispatcher):
    """ADR-006 leftover #3: resolved ONCE at request-dispatch time (call
    this before agent_dispatcher.start_chat_reply is awaited), never at
    completion time - _stamp_reply_provenance used to re-read the live
    dispatcher state from inside the completion callback, which
    mis-attributes a reply to whatever provider happened to be active when
    the LLM call FINISHED rather than the one that actually generated it
    (a real divergence if the user flips Settings mid-flight). Best-effort:
    provenance must never fail the reply."""
    try:
        return agent_dispatcher.active_provider_model()
    except Exception:
        return None, None


def _stamp_reply_provenance(node, provider, model) -> None:
    """ADR-006 stage 6.8: stamp provider/model on an ordinary chat reply
    node (previously only branch-synthesis recorded them). `provider`/
    `model` are the request-time snapshot from
    _snapshot_active_provider_model, not re-resolved here."""
    node.state.provider = provider or None
    node.state.model = model or None


def _make_on_usage(
    document, agent_dispatcher, token_counter, publish_token_counter, publish_scene, node_ref
):
    """ADR-006 stage 6.8: the shared real-usage completion callback for both
    reply flows (module-level for the same 300-line-cap reason as its
    siblings). `node_ref` is a callable returning the id of the node the
    counts belong to (send: the reply node created by _on_reply; regenerate:
    the regenerated node). Records the counter's real usage (with
    provider/model for cost estimation) and stamps the counts on the node.
    Partials and cancels never reach here - no usage exists for dead
    streams, so they keep the estimator."""

    async def _on_usage(usage):
        provider, model = agent_dispatcher.active_provider_model()
        token_counter.set_real_usage(
            usage.get("prompt_tokens"), usage.get("completion_tokens"),
            provider=provider, model=model,
        )
        await publish_token_counter()
        node_id = node_ref()
        if node_id and node_id in document.nodes:
            target = document.nodes[node_id]
            target.state.prompt_tokens = usage.get("prompt_tokens")
            target.state.completion_tokens = usage.get("completion_tokens")
            # ADR-016 stage 16.2: a point-in-time cost snapshot - see
            # ChatState.estimated_cost_usd's own comment.
            target.state.estimated_cost_usd = token_counter.estimate_cost_for(
                usage.get("prompt_tokens"), usage.get("completion_tokens"),
                provider=provider, model=model,
            )
            await publish_scene()

    return _on_usage


def _make_regenerate_on_reply(
    document, bus, notifications, token_counter, publish_token_counter, node_to_regenerate,
    provenance_provider=None, provenance_model=None,
):
    """regenerate_response's completion callback, built at module level for
    the same 300-line-cap reason as its siblings above (ADR-006 stage 6.4's
    streaming/partial additions pushed register_chat_intents over). Body
    moved VERBATIM from the former inline closure - no step reordered.
    ADR-006 stage 6.8: `provenance_provider`/`provenance_model` (additive,
    default-None for older direct callers) stamp provider/model provenance
    on the regenerated node - a request-time snapshot the caller resolved
    via _snapshot_active_provider_model BEFORE dispatch, per leftover #3."""

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
        # ADR-010 stage 10.1: regenerate is delete-then-recreate, and one
        # Ctrl+Z has to reverse the WHOLE thing - the torn-down children,
        # the replaced content, and the newly parsed children - so it is
        # one command, not three. The pre-existing children have to be
        # named explicitly: remove_associated_content_children deletes
        # nodes that are not this command's own target, which
        # record_command would otherwise refuse to lose silently (by
        # design - see its AssertionError).
        #
        # The teardown/parse/mutate ORDER is unchanged from the legacy
        # step order documented just above - see _regenerate_in_place at
        # module level; wrapping it moves no step relative to any other.
        existing_children = [
            edge.target
            for edge in document.edges.values()
            if edge.source == node_to_regenerate.id
        ]
        document.record_command(
            "regenerateResponse", "agent",
            lambda: _regenerate_in_place(document, node_to_regenerate, reply_text),
            node_ids=[node_to_regenerate.id, *existing_children],
        )
        # ADR-006 stage 6.8: provenance for the regenerated content.
        _stamp_reply_provenance(node_to_regenerate, provenance_provider, provenance_model)

        # last_chat_node_id: DELIBERATELY untouched. See §5.

    return _on_reply


def _land_partial_regenerate(document, node_to_regenerate, partial_text):
    """ADR-006 stage 6.4 (H5): a regenerate's stream died - preserve the
    accumulated text instead of silently keeping the old content with the
    new text lost. The old content children are torn down (they annotated
    content that no longer exists) but the partial is NOT parsed into new
    children; the incomplete flag tells the user to Regenerate anyway."""
    existing_children = [
        edge.target
        for edge in document.edges.values()
        if edge.source == node_to_regenerate.id
    ]

    def _commit():
        document.remove_associated_content_children(node_to_regenerate.id)
        document.update_chat_node_content(
            node_to_regenerate.id, partial_text, incomplete=True
        )

    document.record_command(
        "regenerateResponse", "agent", _commit,
        node_ids=[node_to_regenerate.id, *existing_children],
    )


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
        full_text, content_parts = _merge_staged_attachments(text, staged)

        # ADR-010 stage 10.1: a hidden create - send_message internally calls
        # add_chat_node and updates last_chat_node_id, so it is a real node
        # creation despite not being named add*. branch_from_node_id is
        # watched because the new node connects to it.
        node, _command = document.record_command(
            "sendMessage", "user",
            lambda: document.send_message(
                full_text, content_parts=content_parts, branch_from_node_id=branch_from_node_id,
            ),
            node_ids=[branch_from_node_id] if branch_from_node_id else (),
        )
        _promote_document_attachments(document, node, staged)

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
        # 6.8 review fix: a new request starts on estimates - see
        # TokenCounterState.reset_real_usage.
        token_counter.reset_real_usage()
        await publish_token_counter()

        # ADR-006 stage 6.8: set by _on_reply once the reply node exists, so
        # _on_usage (which _dispatch invokes AFTER on_reply on success) can
        # stamp the real counts onto that node.
        reply_ref = {"node_id": None}
        # ADR-006 leftover #3: resolved here, at request-dispatch time - not
        # inside _on_reply below, which only fires once the reply completes
        # (see _snapshot_active_provider_model's own note).
        request_provider, request_model = _snapshot_active_provider_model(agent_dispatcher)

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

            # ADR-010 stage 10.1: reply node + children as ONE command (see
            # _build_reply_nodes). "agent" provenance, not "user" - stage 10.5
            # ("undo this build") is what consumes that distinction.
            ai_node, _command = document.record_command(
                "chatReply", "agent",
                lambda: _build_reply_nodes(document, node, placeholder_text, parsed_parts),
                node_ids=[node.id],
            )
            # ADR-006 stage 6.8: stamp provider/model provenance on the
            # reply (previously only branch-synthesis nodes carried it),
            # and remember the node for _on_usage's per-node token stamp.
            _stamp_reply_provenance(ai_node, request_provider, request_model)
            reply_ref["node_id"] = ai_node.id

            # NOTE: the calls inside _create_reply_nodes above MUST use the
            # `document.` prefix. Before ADR-002 stage 2.6, a bare
            # `add_code_node(...)`/`add_thinking_node(...)` here would have
            # silently resolved to register_canvas's own same-scope async
            # WS-intent wrapper closures of the same name instead of raising
            # a NameError - producing an unawaited coroutine that never ran
            # and never errored, so no node would be created and nothing
            # would look wrong until the scene state was actually inspected.
            # Those wrapper closures now live in backend/api/intents_nodes.py,
            # a different module - a bare call here would raise NameError
            # immediately instead, a strictly SAFER failure mode than the
            # original silent one. The `document.` prefix requirement is
            # kept regardless, since correctness should not depend on which
            # of the two failure modes happens to be true today.

            # Always the real chat node's id, never a code/thinking child's -
            # add_code_node/add_thinking_node are documented above (see their
            # own docstrings) as NOT branch points, and last_chat_node_id
            # specifically drives the next real send's branch-continuation
            # (chat_branch_history), which only makes sense pointed at a real
            # chat node.
            document.last_chat_node_id = ai_node.id

        async def _on_partial(partial_text):
            # See _land_partial_reply_node; token accounting mirrors
            # _on_reply's preamble (the partial consumed real tokens).
            if node.id not in document.nodes:
                return
            document.add_session_tokens(partial_text)
            token_counter.set_output_text(partial_text)
            await publish_token_counter()
            _land_partial_reply_node(document, node, partial_text)

        _on_usage = _make_on_usage(
            document, agent_dispatcher, token_counter, publish_token_counter,
            publish_scene, lambda: reply_ref["node_id"],
        )

        await agent_dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=history,
            on_reply=_on_reply,
            on_usage=_on_usage,
            # R6.1: lets AgentDispatcher resolve a branch-attached System
            # Prompt note override (see backend/agents.py's
            # _resolve_branch_system_prompt) - node.id is the just-created
            # user ChatNode "about to be sent", the walk starts from here.
            canvas_document=document,
            node_id=node.id,
            on_partial=_on_partial,
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
        # 6.8 review fix: same request-start reset as send_message above.
        token_counter.reset_real_usage()
        await publish_token_counter()

        # ADR-006 leftover #3: resolved here, before start_chat_reply is
        # awaited below - not inside _on_reply, which only fires once the
        # reply completes (see _snapshot_active_provider_model's own note).
        request_provider, request_model = _snapshot_active_provider_model(agent_dispatcher)
        _on_reply = _make_regenerate_on_reply(
            document, bus, notifications, token_counter, publish_token_counter,
            node_to_regenerate, request_provider, request_model,
        )

        def _on_partial(partial_text):
            # See _land_partial_regenerate; same liveness check as _on_reply.
            if node_to_regenerate.id not in document.nodes:
                return
            _land_partial_regenerate(document, node_to_regenerate, partial_text)

        _on_usage = _make_on_usage(
            document, agent_dispatcher, token_counter, publish_token_counter,
            publish_scene, lambda: node_to_regenerate.id,
        )

        await agent_dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=history,
            on_reply=_on_reply,
            on_usage=_on_usage,
            # ADR-006 stage 6.4: streams, closing R4.4's deferral. The old
            # Composer-preview objection is dissolved by IDENTITY, not
            # suppression - the overrides below key the frames to the target
            # node's own pending_request_id on "scene" (CodeSandboxNodeView's
            # subscription contract), so the Composer never sees this request.
            stream=True,
            on_begin=lambda request_id: setattr(
                node_to_regenerate, "pending_request_id", request_id
            ),
            on_end=lambda: setattr(node_to_regenerate, "pending_request_id", None),
            state_topic="scene",
            on_partial=_on_partial,
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
