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

from functools import partial
import asyncio
import copy
import logging

from backend.context_engine.citations import apply_reply_citations
from backend.knowledge_retrieval import search_chat_knowledge, workspace_has_chat_knowledge

_logger = logging.getLogger(__name__)


from backend.agents import AgentDispatcher
from backend.api._shared import make_publish_scene, make_publish_token_counter
from backend.composer import ComposerDocument
from backend.domain.graph import SceneDocument
from backend.domain.model import SceneError
from backend.domain.staleness import fingerprint_for_reply


async def _history_with_chat_knowledge(
    history: list[dict], query: str, workspace_id: str | int | None, *, enabled: bool = True,
    settings_manager=None,
) -> list[dict]:
    """Add ephemeral retrieval data to the last turn, never to graph history.

    The dispatcher passes the list to ChatWorker unchanged. Only that worker
    reads this private key, then strips it from the wire messages. A missing
    database, empty hit list, or failed local search leaves plain chat alone.
    """
    if not enabled or not history or not isinstance(query, str) or not query.strip():
        return history
    try:
        extra = {"settings_manager": settings_manager} if settings_manager is not None else {}
        hits = await asyncio.to_thread(
            search_chat_knowledge, query[:1500], workspace_id, **extra,
        )
    except Exception:
        _logger.warning("Chat knowledge lookup failed; proceeding without retrieval", exc_info=True)
        return history
    if not hits:
        return history
    return [*history[:-1], {**history[-1], "_knowledge_hits": hits}]


async def _use_knowledge_for_node(document: SceneDocument, node_id: str | None) -> bool:
    """Use a branch override or the workspace's live document-presence default."""
    override = document.resolve_use_knowledge_override(node_id)
    if override is not None:
        return override
    # SQLite operations, including the existence probe, belong off the loop.
    workspace_id = document.current_workspace_id
    return await asyncio.to_thread(workspace_has_chat_knowledge, workspace_id)


def _latest_user_query(history: list[dict]) -> str:
    for turn in reversed(history):
        if turn.get("role") != "user":
            continue
        content = turn.get("content", "")
        if isinstance(content, str):
            return content.split("\n\n--- Attached:", 1)[0]
        if isinstance(content, list):
            return " ".join(
                str(part.get("text", "")) for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ).split("\n\n--- Attached:", 1)[0]
    return ""


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
    for attachment in promotable:
        # place_child resolves collisions against already-landed siblings,
        # so two attachments on one message fan out sideways instead of
        # stacking (ADR-021 stage 21.5's requirement, now via the shared
        # placement engine instead of a local index offset).
        ax, ay = document.place_child(node.id, "document")
        # functools.partial rather than the `lambda attachment=attachment,
        # ax=ax, ay=ay:` this used to be. The default arguments were there only
        # to bind this iteration's values eagerly, which is exactly what
        # partial does - and record_command runs the mutator synchronously,
        # inside this same iteration, so nothing here was ever read late.
        # A lambda carrying defaults cannot be checked against
        # record_command's zero-argument `Callable[[], T]` parameter at all.
        document.record_command(
            "addDocumentNode", "user",
            partial(
                document.add_document_node,
                ax,
                ay,
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
    ax, ay = document.place_child(parent_node.id, "chat")
    ai = document.add_chat_node(ax, ay, placeholder_text, False, parent_id=parent_node.id)
    # Thinking children hang off the reply's left flank, code children off
    # its right - the legacy left/right convention, but via place_child so
    # each lands clear of the reply's real footprint and of its own earlier
    # siblings instead of at a fixed 160px offset.
    for part in parsed_parts:
        if part["type"] == "thinking":
            tx, ty = document.place_child(ai.id, "thinking", prefer="left")
            document.add_thinking_node(tx, ty, part["content"], parent_id=ai.id)
        elif part["type"] == "code":
            cx, cy = document.place_child(ai.id, "code", prefer="right")
            document.add_code_node(cx, cy, part["content"], part["language"], parent_id=ai.id)
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

    # Same left/right flank convention as _build_reply_nodes above.
    for part in parsed_parts:
        if part["type"] == "thinking":
            tx, ty = document.place_child(node_to_regenerate.id, "thinking", prefer="left")
            document.add_thinking_node(tx, ty, part["content"], parent_id=node_to_regenerate.id)
        elif part["type"] == "code":
            cx, cy = document.place_child(node_to_regenerate.id, "code", prefer="right")
            document.add_code_node(
                cx, cy, part["content"], part["language"], parent_id=node_to_regenerate.id,
            )


def _land_partial_reply_node(
    document, parent_node, partial_text, fingerprint=None, knowledge_enabled=None,
    provider=None, model=None,
):
    """ADR-006 stage 6.4 (H5): a Composer send's stream died mid-reply - land
    the accumulated text as a real assistant node flagged incomplete instead
    of losing it. NOT parsed into thinking/code children (a truncated reply
    can end inside an unterminated fence); the user retries via the node's
    own Regenerate action, which is why last_chat_node_id/parent wiring must
    match the complete path (_build_reply_nodes) exactly. Module-level for
    the same 300-line-cap reason as its two siblings above."""
    def _commit():
        ax, ay = document.place_child(parent_node.id, "chat")
        ai = document.add_chat_node(ax, ay, partial_text, False, parent_id=parent_node.id)
        ai.state.response_incomplete = True
        _stamp_reply_provenance(ai, provider, model)
        ai.state.reply_history_fingerprint = fingerprint
        ai.state.reply_knowledge_enabled = knowledge_enabled
        document.sync_chat_version(ai.id)
        # Inside the mutator so the command's after-snapshot of the chat
        # pointers names this node - see _make_send_on_reply's _commit.
        document.last_chat_node_id = ai.id
        return ai

    document.record_command("chatReply", "agent", _commit, node_ids=[parent_node.id])


def _snapshot_active_provider_model(agent_dispatcher, canvas_document=None, node_id=None, bus=None):
    """ADR-006 leftover #3: resolved ONCE at request-dispatch time (call
    this before agent_dispatcher.start_chat_reply is awaited), never at
    completion time - _stamp_reply_provenance used to re-read the live
    dispatcher state from inside the completion callback, which
    mis-attributes a reply to whatever provider happened to be active when
    the LLM call FINISHED rather than the one that actually generated it
    (a real divergence if the user flips Settings mid-flight). Best-effort:
    provenance must never fail the reply.

    With `canvas_document`, the node/branch/workspace model pin is resolved
    by the same rungs _dispatch uses (pass the same canvas_document/node_id/
    bus the caller hands start_chat_reply), and the pair names THAT model: a
    reply pinned to one of two configured models must not be badged and
    priced as the task's default model."""
    try:
        model_ref = None
        if canvas_document is not None:
            model_ref = agent_dispatcher._resolve_model_ref_for_dispatch(canvas_document, node_id, bus=bus)
        if model_ref is not None:
            return agent_dispatcher.active_provider_model(model_ref)
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
    # Older call sites and test doubles may have no version history.
    versions = getattr(node.state, "versions", None)
    if versions:
        selected = versions[getattr(node.state, "version_index", 0)]
        selected.provider_name = node.state.provider
        selected.model_name = node.state.model


def _make_on_usage(
    document, token_counter, publish_token_counter, publish_scene, node_ref,
    provider=None, model=None, command_ref=None,
):
    """ADR-006 stage 6.8: the shared real-usage completion callback for both
    reply flows (module-level for the same 300-line-cap reason as its
    siblings). `node_ref` is a callable returning the id of the node the
    counts belong to (send: the reply node created by _on_reply; regenerate:
    the regenerated node). Records the counter's real usage (with
    provider/model for cost estimation) and stamps the counts on the node.
    Partials and cancels never reach here - no usage exists for dead
    streams, so they keep the estimator.

    `provider`/`model` are the request-time pair from
    _snapshot_active_provider_model, for the same reason
    _stamp_reply_provenance takes them: re-reading the dispatcher when the
    usage arrives would price the reply against whatever is active THEN (a
    provider switch mid-stream) or against the task's default model when
    the reply ran on a pinned one."""

    async def _on_usage(usage):
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
            document.sync_chat_version(node_id)
            # Real usage arrives after record_command() deep-copies the
            # reply. Amend only these run-owned metadata fields in that
            # command's redo snapshot; otherwise Undo -> Redo resurrects
            # reply text with the old/missing usage and cost badge.
            # Leave the command's before snapshot untouched: regenerating
            # then undoing must restore the ORIGINAL reply's usage.
            command = command_ref() if command_ref is not None else None
            snapshot = command.node_after.get(node_id) if command is not None else None
            if snapshot is not None and snapshot.state is not None:
                snapshot.state.prompt_tokens = target.state.prompt_tokens
                snapshot.state.completion_tokens = target.state.completion_tokens
                snapshot.state.estimated_cost_usd = target.state.estimated_cost_usd
                # The redo snapshot also owns the per-version usage receipt.
                snapshot.state.versions = copy.deepcopy(target.state.versions)
                snapshot.state.version_index = target.state.version_index
            await publish_scene()

    return _on_usage


def _make_send_on_reply(
    document, node, token_counter, publish_token_counter, reply_ref,
    provenance_provider=None, provenance_model=None,
    request_fingerprint=None, knowledge_enabled=None,
):
    """send_message's completion callback, built at module level for the
    same 300-line-cap reason as _make_regenerate_on_reply below. `node` is
    the user's just-sent chat node; `reply_ref` is send_message's
    {"node_id": ...} box, filled here once the reply node exists so
    _on_usage can stamp the real counts onto it. `provenance_provider`/
    `provenance_model` are the request-time pair from
    _snapshot_active_provider_model."""

    async def _on_reply(reply_text):
        # R8a: outputTokens reflects what the model actually returned,
        # regardless of whether parse_response below finds anything
        # node-worthy in it (the empty-reply early return below) or the
        # user's node is still there to hang it off (the liveness check
        # just below) - the reply happened and consumed real output
        # tokens either way. Same ahead-of-every-early-return placement
        # as regenerate's own _on_reply (_make_regenerate_on_reply).
        token_counter.set_output_text(reply_text)
        await publish_token_counter()
        # Deleted mid-flight: silent no-op, the same posture as
        # regenerate's _on_reply, send_message's own _on_partial,
        # and intents_conversation.py's _on_reply. The user's node can be
        # removed (removeNodes/deleteChatNode) while the reply is still
        # generating - _dispatch runs the LLM call as an un-awaited task,
        # so the socket is free to process the delete first - and
        # _build_reply_nodes would then raise SceneError("unknown parent
        # node"), which _dispatch's generic handler reports as "AI
        # response failed" for a request that succeeded. Returning here,
        # BEFORE add_session_tokens and before reply_ref is set, is what
        # keeps the document's bookkeeping honest: the persisted session
        # total only grows for replies that land, and _on_usage (which
        # still runs after this returns) finds reply_ref unset and
        # stamps no node, while the request-level counter keeps the
        # real usage the provider billed.
        if node.id not in document.nodes:
            return
        # R6.3: the assistant's reply has completed (regardless of
        # whether parse_response below finds anything node-worthy in
        # it) - grow total_session_tokens by its estimated count too,
        # same as the user's own message text just above.
        document.add_session_tokens(reply_text)
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
            # node (set by send_message), matching legacy's
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
        def _commit():
            ai = _build_reply_nodes(document, node, placeholder_text, parsed_parts)
            # Always the real chat node's id, never a code/thinking child's -
            # add_code_node/add_thinking_node are documented (see their own
            # docstrings) as NOT branch points, and last_chat_node_id
            # specifically drives the next real send's branch-continuation
            # (chat_branch_history), which only makes sense pointed at a real
            # chat node. Set inside the mutator, not after record_command
            # returns, so the command's after-snapshot of the chat pointers
            # names this reply: undo puts the continuation point back on the
            # user's message and redo brings it forward to the reply again.
            document.last_chat_node_id = ai.id
            # The command's after-snapshot must contain the request-time
            # provider/model, not an unstamped copy of this reply.
            _stamp_reply_provenance(ai, provenance_provider, provenance_model)
            ai.state.reply_history_fingerprint = request_fingerprint
            ai.state.reply_knowledge_enabled = knowledge_enabled
            apply_reply_citations(ai, reply_text)
            document.sync_chat_version(ai.id)
            return ai

        ai_node, command = document.record_command(
            "chatReply", "agent", _commit, node_ids=[node.id],
        )
        reply_ref["node_id"] = ai_node.id
        reply_ref["command"] = command

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

    return _on_reply


def _make_regenerate_on_reply(
    document, bus, notifications, token_counter, publish_token_counter, node_to_regenerate,
    provenance_provider=None, provenance_model=None, command_ref=None,
    request_fingerprint=None, knowledge_enabled=None,
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
        def _commit():
            _regenerate_in_place(document, node_to_regenerate, reply_text)
            # Stamp inside record_command so Undo/Redo retains the model.
            _stamp_reply_provenance(node_to_regenerate, provenance_provider, provenance_model)
            node_to_regenerate.state.reply_history_fingerprint = request_fingerprint
            node_to_regenerate.state.reply_knowledge_enabled = knowledge_enabled
            apply_reply_citations(node_to_regenerate, reply_text)
            document.sync_chat_version(node_to_regenerate.id)

        _, command = document.record_command(
            "regenerateResponse", "agent", _commit,
            node_ids=[node_to_regenerate.id, *existing_children],
        )
        if command_ref is not None:
            command_ref["command"] = command

        # last_chat_node_id: DELIBERATELY untouched. See §5.

    return _on_reply


def _land_partial_regenerate(
    document, node_to_regenerate, partial_text, fingerprint=None, knowledge_enabled=None,
    provider=None, model=None,
):
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
        _stamp_reply_provenance(node_to_regenerate, provider, model)
        node_to_regenerate.state.reply_history_fingerprint = fingerprint
        node_to_regenerate.state.reply_knowledge_enabled = knowledge_enabled
        # A failed stream has no trusted included-passage snapshot. Drop
        # the previous reply's ledger within this same undoable mutation,
        # preserving real tool-call rows.
        apply_reply_citations(node_to_regenerate, str(partial_text))
        document.sync_chat_version(node_to_regenerate.id)

    document.record_command(
        "regenerateResponse", "agent", _commit,
        node_ids=[node_to_regenerate.id, *existing_children],
    )



class _ComparisonChatRuns:
    """Map the existing dispatcher's 'chat' kind onto one sibling-only slot.

    All handles live in the SESSION's original RunRegistry: disconnect,
    cancellation, diagnostics and eviction still see every comparison run.
    Only its busy bucket is separate. Ordinary chat keeps its old bucket.
    """

    def __init__(self, registry, kind: str):
        self._registry = registry
        self._kind = kind

    def is_busy(self, kind: str) -> bool:
        return self._registry.is_busy(self._kind if kind == "chat" else kind)

    def claim(self, kind: str, **kwargs):
        return self._registry.claim(self._kind if kind == "chat" else kind, **kwargs)

    def get(self, request_id):
        return self._registry.get(request_id)

    def release(self, request_id):
        return self._registry.release(request_id)

    def attach_task(self, handle, task):
        return self._registry.attach_task(handle, task)


def _comparison_content(node, *, answer: str, status: str, elapsed: float | None) -> str:
    """Persistent, human-readable metrics without altering the scene schema."""
    prompt = node.state.prompt_tokens
    completion = node.state.completion_tokens
    cost = node.state.estimated_cost_usd
    latency = f"{elapsed:.2f}s" if elapsed is not None else "unavailable"
    tokens = (
        f"{prompt if prompt is not None else '?'} input / "
        f"{completion if completion is not None else '?'} output"
        if prompt is not None or completion is not None else "estimate unavailable"
    )
    cost_text = f"${cost:.6f} estimated" if cost is not None else "estimate unavailable"
    heading = (
        f"**{node.state.provider} · {node.state.model}**\n\n"
        f"**{status}** · Latency: {latency} · Tokens: {tokens} · "
        f"Cost: {cost_text}"
    )
    return heading + ("\n\n---\n\n" + answer if answer else "")


async def _send_model_comparison(
    *, bus, document, notifications, agent_dispatcher, composer_document,
    token_counter, publish_scene, publish_token_counter, text, models,
    branch_from_node_id=None, cancelled_requests=None, use_knowledge=None,
):
    """One user turn, 2-4 independently streamed and cancellable sibling replies.

    Each worker is a normal AgentDispatcher.start_chat_reply call, NOT a
    shortcut to api_provider: persona/system-prompt overrides, context
    assembly, token budgets, retries and the provider safety gates are
    inherited intact from that path. Response nodes use existing chat-node
    streaming, badges, branching and undo command snapshots.
    """
    import copy
    import time
    from dataclasses import asdict

    import api_provider
    from backend.canvas import _history_image_tokens, _history_token_text
    from backend.context_engine.model_comparison import validate_comparison_models

    settings = agent_dispatcher._settings_manager
    refs = validate_comparison_models(models, settings, agent_dispatcher._provider_runtime)
    if not isinstance(text, str) or not text.strip():
        raise SceneError("Enter a message before comparing models.")
    if agent_dispatcher._runs.is_busy("chat"):
        raise SceneError("Finish the current chat request before comparing models.")
    if use_knowledge is not None and not isinstance(use_knowledge, bool):
        raise SceneError("use_knowledge must be a boolean or null")

    # Validate the selection BEFORE consuming staged attachments or writing
    # any graph content. An individual provider connection can still fail
    # independently AFTER the shared user turn and placeholders are created.
    staged = composer_document.take_staged_attachments()
    full_text, content_parts = _merge_staged_attachments(text, staged)
    def _create_comparison_user():
        created = document.send_message(
            full_text, content_parts=content_parts, branch_from_node_id=branch_from_node_id,
        )
        if use_knowledge is not None:
            document.set_use_knowledge_override(created.id, use_knowledge)
        return created

    user, _ = document.record_command(
        "sendMessage", "user", _create_comparison_user,
        node_ids=[branch_from_node_id] if branch_from_node_id else (),
    )
    _promote_document_attachments(document, user, staged)
    if staged:
        await bus.publish("app-composer")
    document.add_session_tokens(full_text)

    knowledge_enabled = await _use_knowledge_for_node(document, user.id)
    # Snapshot the selected-version history and all model routes before retrieval awaits.
    history = document.chat_branch_history(user.id)
    contexts = []
    for ref in refs:
        provider, model = api_provider.describe_active_model(
            __import__("graphlink_task_config").TASK_CHAT, model_ref=ref,
        )
        contexts.append((provider, model, fingerprint_for_reply(
            document, user.id, provider, model, knowledge_enabled,
        )))
    history = await _history_with_chat_knowledge(
        history, text, document.current_workspace_id,
        enabled=knowledge_enabled,
        settings_manager=getattr(agent_dispatcher, "_settings_manager", None),
    )
    parent_edge = document._branch_parent_edge(user.id)
    context_history = (
        document.chat_branch_history(parent_edge.source) if parent_edge else []
    )
    token_counter.set_context_text(
        _history_token_text(context_history), image_tokens=_history_image_tokens(context_history),
    )
    token_counter.reset_real_usage()
    await publish_token_counter()

    def _place_siblings():
        created = []
        for ref, (provider, model, proof) in zip(refs, contexts):
            x, y = document.place_child(user.id, "chat")
            reply = document.add_chat_node(x, y, "Waiting for response…", False, parent_id=user.id)
            reply.title = f"{ref.model_id} · comparison"
            # Input pin is resolved by the SAME node/branch/workspace chain
            # as ordinary chat; it is not merely a display badge.
            # ModelRef is a dataclass; destructuring its fields avoids
            # confusing the project's SceneNode state-migration guard with
            # an unrelated ModelRef.provider property.
            ref_fields = asdict(ref)
            reply.state.override_provider = ref_fields["provider"]
            reply.state.override_model_id = ref_fields["model_id"]
            reply.state.provider, reply.state.model = provider, model
            reply.state.reply_history_fingerprint = proof
            reply.state.reply_knowledge_enabled = knowledge_enabled
            created.append(reply)
        # Deliberately leave last_chat_node_id pointing at the USER turn:
        # selecting one of these parallel branches is an explicit action.
        return created

    replies, command = document.record_command(
        "chatReply", "agent", _place_siblings, node_ids=[user.id],
    )
    await publish_scene()

    def persist_reply(node):
        # Comparison siblings replace a pending placeholder, not an answer
        # the user has already seen. Keep their one initial version in sync.
        document.sync_chat_version(node.id)
        # The initial placeholder's undo/redo snapshot must be updated when
        # its asynchronously generated content and usage arrive.
        if node.id in command.node_after:
            command.node_after[node.id] = copy.deepcopy(node)

    for index, (ref, reply) in enumerate(zip(refs, replies)):
        run = {
            "started": None, "request_id": None, "finished": False,
            "answer": "", "partial": "", "cancelled": False,
        }

        def _begin(request_id, *, reply=reply, run=run):
            run["started"] = time.monotonic()
            run["request_id"] = request_id
            if reply.id in document.nodes:
                reply.pending_request_id = request_id

        def _end(*, reply=reply, run=run):
            # A late finalizer must never clear a newer regenerate's id
            # after a cancel freed this sibling's registry slot.
            if reply.id not in document.nodes or reply.pending_request_id != run["request_id"]:
                return
            reply.pending_request_id = None
            if not run["finished"]:
                was_cancelled = cancelled_requests is not None and run["request_id"] in cancelled_requests
                status = "Canceled" if was_cancelled else "Failed or interrupted"
                reply.state.response_incomplete = True
                reply.content = _comparison_content(
                    reply, answer=run["partial"], status=status,
                    elapsed=(time.monotonic() - run["started"]) if run["started"] is not None else None,
                )
            persist_reply(reply)
            if cancelled_requests is not None:
                cancelled_requests.discard(run["request_id"])

        async def _on_reply(answer, *, reply=reply, run=run):
            if reply.id not in document.nodes or reply.pending_request_id != run["request_id"]:
                return
            run["finished"] = True
            run["answer"] = answer
            document.add_session_tokens(answer)
            elapsed = time.monotonic() - run["started"] if run["started"] is not None else None
            reply.state.response_incomplete = False
            apply_reply_citations(reply, answer)
            reply.content = _comparison_content(reply, answer=answer, status="Complete", elapsed=elapsed)
            persist_reply(reply)

        async def _on_partial(partial_text, *, reply=reply, run=run):
            # The shared dispatcher calls this only on a failed stream, not
            # on explicit cancellation; preserve any real partial on error.
            run["partial"] = partial_text
            if reply.id in document.nodes and reply.pending_request_id == run["request_id"]:
                document.add_session_tokens(partial_text)

        provenance_provider, provenance_model = reply.state.provider, reply.state.model
        usage_callback = _make_on_usage(
            document, token_counter, publish_token_counter, publish_scene,
            lambda reply=reply: reply.id,
            provenance_provider, provenance_model,
            command_ref=lambda: command,
        )

        async def _on_usage(usage, *, reply=reply, run=run, usage_callback=usage_callback):
            if reply.id not in document.nodes or reply.pending_request_id != run["request_id"]:
                return
            await usage_callback(usage)
            elapsed = time.monotonic() - run["started"] if run["started"] is not None else None
            reply.content = _comparison_content(reply, answer=run["answer"], status="Complete", elapsed=elapsed)
            persist_reply(reply)

        try:
            runtime = api_provider.comparison_runtime(
                ref, settings, runtime=agent_dispatcher._provider_runtime,
            )
            # One lightweight dispatcher per sibling, with the same settings
            # manager and the SAME session run registry. It retains all
            # dispatch policy; only the busy kind and provider snapshot vary.
            scoped = AgentDispatcher(settings, provider_runtime=runtime)
            scoped._runs = _ComparisonChatRuns(
                agent_dispatcher._runs, f"chat_comparison:{user.id}:{index}",
            )
            await scoped.start_chat_reply(
                bus=bus,
                notifications_state=notifications,
                composer_document=composer_document,
                conversation_history=history,
                on_reply=_on_reply,
                on_usage=_on_usage,
                canvas_document=document,
                node_id=reply.id,
                stream=True,
                state_topic="scene",
                on_begin=_begin,
                on_end=_end,
                on_partial=_on_partial,
            )
        except Exception:
            # Do not write a provider exception into persistent canvas text:
            # some SDKs include endpoint and credential details in errors.
            # A broken profile must not prevent sibling models from running.
            reply.state.response_incomplete = True
            reply.content = _comparison_content(
                reply, answer="This model could not start. Check its provider settings.",
                status="Failed", elapsed=None,
            )
            persist_reply(reply)
            await publish_scene()
    return user.id



def register_chat_model_intents(
    bus: SessionBus, document: SceneDocument, notifications: NotificationState,
    agent_dispatcher: AgentDispatcher, composer_document: ComposerDocument,
    token_counter: TokenCounterState, publish_scene, publish_token_counter,
    comparison_cancelled_requests: set[str],
) -> None:
    """Register model-picker and comparison actions separately from chat send."""
    def list_comparison_models():
        from backend.context_engine.model_comparison import available_comparison_models
        return {"models": available_comparison_models(
            agent_dispatcher._settings_manager, agent_dispatcher._provider_runtime,
        )}

    async def compare_models(text, models, branch_from_node_id=None, use_knowledge=None):
        return await _send_model_comparison(
            bus=bus, document=document, notifications=notifications,
            agent_dispatcher=agent_dispatcher, composer_document=composer_document,
            token_counter=token_counter, publish_scene=publish_scene,
            publish_token_counter=publish_token_counter,
            text=text, models=models, branch_from_node_id=branch_from_node_id,
            use_knowledge=use_knowledge,
            cancelled_requests=comparison_cancelled_requests,
        )

    async def list_picker_models():
        # Ollama /api/tags and /api/show perform I/O; never block the event
        # loop or the compositor's draft keystrokes while opening the picker.
        from backend.context_engine.model_comparison import available_picker_models

        rows = await asyncio.to_thread(
            available_picker_models, agent_dispatcher._settings_manager, agent_dispatcher._provider_runtime,
        )
        return {"models": rows}

    bus.register_intent("scene", "listPickerModels", list_picker_models)
    bus.register_intent("scene", "listComparisonModels", list_comparison_models)
    bus.register_intent("scene", "compareModels", compare_models)



def register_chat_knowledge_intents(
    bus: SessionBus, document: SceneDocument,
    agent_dispatcher: AgentDispatcher, publish_scene,
) -> None:
    """Register read-only inspection and per-branch knowledge preferences."""
    # C3 preview is read-only: no graph command, provider call or publication.
    async def inspect_chat_context(node_id, draft_text="", use_knowledge=None):
        from backend.context_engine.inspector import inspect_next_chat_send
        return await asyncio.to_thread(
            inspect_next_chat_send, document, node_id, agent_dispatcher,
            bus=bus, draft_text=draft_text, use_knowledge=use_knowledge,
        )

    async def get_chat_knowledge_preference(node_id=None):
        target_id = node_id if node_id is not None else document.last_chat_node_id
        if target_id is not None:
            node = document.nodes.get(target_id)
            if node is None or node.kind != "chat":
                raise SceneError(f"node is not a chat node: {target_id}")
        override = document.resolve_use_knowledge_override(target_id)
        workspace_id = document.current_workspace_id
        has_documents = await asyncio.to_thread(workspace_has_chat_knowledge, workspace_id)
        return {
            "nodeId": target_id,
            "hasDocuments": has_documents,
            "enabled": override if override is not None else has_documents,
            "override": override,
        }

    async def set_chat_use_knowledge(node_id, enabled):
        if not isinstance(enabled, bool):
            raise SceneError("use_knowledge must be a boolean")
        document.record_command(
            "setChatUseKnowledge", "user",
            lambda: document.set_use_knowledge_override(node_id, enabled),
            node_ids=[node_id],
        )
        await publish_scene()

    bus.register_intent("scene", "inspectChatContext", inspect_chat_context)
    bus.register_intent("scene", "getChatKnowledgePreference", get_chat_knowledge_preference)
    bus.register_intent("scene", "setChatUseKnowledge", set_chat_use_knowledge)


def register_chat_cancel_intent(
    bus: SessionBus, agent_dispatcher: AgentDispatcher,
    comparison_cancelled_requests: set[str],
) -> None:
    """Register node-scoped cancellation for chat and model comparisons."""
    # R4.3: per-node cancel for a ConversationNode's in-flight reply. Reuses
    # the exact intent NAME "cancelChatRequest" already registered on the
    # "app-composer" topic by R4.2 - SessionBus keys handlers by the
    # (topic, intent) tuple (see backend/events.py), so this is a second,
    # independent registration on a different topic, not a collision. It
    # points at the same underlying agent_dispatcher.cancel, which is purely
    # request_id-keyed and does not care which topic invoked it.
    def cancel_chat_request(request_id):
        registry = getattr(agent_dispatcher, "_runs", None)
        handle = registry.get(request_id) if registry is not None else None
        if handle is not None and handle.kind.startswith("chat_comparison:"):
            # The node-scoped Stop button already sends this intent. Mark
            # exactly its own run cancelled; do not touch sibling handles.
            comparison_cancelled_requests.add(request_id)
            return agent_dispatcher._runs.cancel(request_id, kind=handle.kind)
        return agent_dispatcher.cancel(request_id)

    bus.register_intent("scene", "cancelChatRequest", cancel_chat_request)


def register_chat_version_intents(
    bus: SessionBus,
    document: SceneDocument,
    notifications: NotificationState,
    agent_dispatcher: AgentDispatcher,
    composer_document: ComposerDocument,
    token_counter: TokenCounterState,
    publish_scene,
    publish_token_counter,
) -> None:
    """Register version-producing regeneration and undoable version selection."""
    # Deferred import - same canvas import-order constraint as sendMessage.
    from backend.canvas import _history_image_tokens, _history_token_text

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

        knowledge_enabled = await _use_knowledge_for_node(document, parent_id)
        # Read selected-version context and route together, before retrieval awaits.
        history = document.chat_branch_history(parent_id)
        request_provider, request_model = _snapshot_active_provider_model(
            agent_dispatcher, document, parent_id, bus,
        )
        request_fingerprint = fingerprint_for_reply(
            document, parent_id, request_provider, request_model, knowledge_enabled,
        )
        history = await _history_with_chat_knowledge(
            history, _latest_user_query(history), document.current_workspace_id,
            enabled=knowledge_enabled,
            settings_manager=getattr(agent_dispatcher, "_settings_manager", None),
        )
        # R8a (token counter wiring): unlike send_message, `history` here
        # already excludes the node being regenerated (rooted at parent_id,
        # not node_id) - there is no fresh draft text to double-count
        # against, so it's usable directly as contextTokens with no
        # adjustment.
        token_counter.set_context_text(
            _history_token_text(history), image_tokens=_history_image_tokens(history),
        )
        # 6.8 review fix: same request-start reset as send_message above.
        token_counter.reset_real_usage()
        await publish_token_counter()

        # The route and selected-version input were snapshotted together above.
        command_ref = {}
        _on_reply = _make_regenerate_on_reply(
            document, bus, notifications, token_counter, publish_token_counter,
            node_to_regenerate, request_provider, request_model, command_ref,
            request_fingerprint, knowledge_enabled,
        )

        def _on_partial(partial_text):
            # See _land_partial_regenerate; same liveness check as _on_reply.
            if node_to_regenerate.id not in document.nodes:
                return
            _land_partial_regenerate(
                document, node_to_regenerate, partial_text, request_fingerprint,
                knowledge_enabled, request_provider, request_model,
            )

        _on_usage = _make_on_usage(
            document, token_counter, publish_token_counter, publish_scene,
            lambda: node_to_regenerate.id, request_provider, request_model,
            command_ref=lambda: command_ref.get("command"),
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

    async def set_chat_version(node_id, index):
        document.record_command(
            "setChatVersion", "user", lambda: document.set_chat_version(node_id, index),
            node_ids=[node_id],
        )
        await publish_scene()

    bus.register_intent("scene", "regenerateResponse", regenerate_response)
    bus.register_intent("scene", "setChatVersion", set_chat_version)


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
    from backend.canvas import _history_image_tokens, _history_token_text

    publish_scene = make_publish_scene(bus)
    publish_token_counter = make_publish_token_counter(bus)
    comparison_cancelled_requests: set[str] = set()

    async def send_message(text, branch_from_node_id=None, use_knowledge=None):
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
        if use_knowledge is not None and not isinstance(use_knowledge, bool):
            raise SceneError("use_knowledge must be a boolean or null")
        staged = composer_document.take_staged_attachments()
        full_text, content_parts = _merge_staged_attachments(text, staged)

        # ADR-010 stage 10.1: a hidden create - send_message internally calls
        # add_chat_node and updates last_chat_node_id, so it is a real node
        # creation despite not being named add*. branch_from_node_id is
        # watched because the new node connects to it.
        def _create_user_message():
            created = document.send_message(
                full_text, content_parts=content_parts, branch_from_node_id=branch_from_node_id,
            )
            if use_knowledge is not None:
                document.set_use_knowledge_override(created.id, use_knowledge)
            return created

        node, _command = document.record_command(
            "sendMessage", "user", _create_user_message,
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
        knowledge_enabled = await _use_knowledge_for_node(document, node.id)
        # Capture current selected-version context without yielding to retrieval.
        history = document.chat_branch_history(node.id)
        request_provider, request_model = _snapshot_active_provider_model(
            agent_dispatcher, document, node.id, bus,
        )
        request_fingerprint = fingerprint_for_reply(
            document, node.id, request_provider, request_model, knowledge_enabled,
        )
        history = await _history_with_chat_knowledge(
            history, text, document.current_workspace_id,
            enabled=knowledge_enabled,
            settings_manager=getattr(agent_dispatcher, "_settings_manager", None),
        )

        # R8a (token counter wiring): contextTokens is the branch history the
        # reply will be generated FROM - explicitly NOT `history` above,
        # which already includes the message just sent (inputTokens, set
        # live as the draft was typed in Composer.tsx, already owns that
        # text; counting it again here would inflate payload()'s total).
        # Rooted at node's own parent, the same "history before this node"
        # walk every specialized agent flow below (web research/artifact/
        # gitlink/sandbox) already uses via _branch_parent_edge - applied
        # to the plain-chat path for the first time here.
        context_parent_edge = document._branch_parent_edge(node.id)
        context_history = (
            document.chat_branch_history(context_parent_edge.source) if context_parent_edge else []
        )
        token_counter.set_context_text(
            _history_token_text(context_history), image_tokens=_history_image_tokens(context_history),
        )
        # 6.8 review fix: a new request starts on estimates - see
        # TokenCounterState.reset_real_usage.
        token_counter.reset_real_usage()
        await publish_token_counter()

        # ADR-006 stage 6.8: set by _on_reply once the reply node exists, so
        # _on_usage (which _dispatch invokes AFTER on_reply on success) can
        # stamp the real counts onto that node.
        reply_ref = {"node_id": None}
        # The route and selected-version input were captured together above.

        _on_reply = _make_send_on_reply(
            document, node, token_counter, publish_token_counter, reply_ref,
            request_provider, request_model, request_fingerprint, knowledge_enabled,
        )

        async def _on_partial(partial_text):
            # See _land_partial_reply_node; token accounting mirrors
            # _on_reply's preamble (the partial consumed real tokens).
            if node.id not in document.nodes:
                return
            document.add_session_tokens(partial_text)
            token_counter.set_output_text(partial_text)
            await publish_token_counter()
            _land_partial_reply_node(
                document, node, partial_text, request_fingerprint, knowledge_enabled,
                request_provider, request_model,
            )

        _on_usage = _make_on_usage(
            document, token_counter, publish_token_counter, publish_scene,
            lambda: reply_ref["node_id"], request_provider, request_model,
            command_ref=lambda: reply_ref.get("command"),
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

    register_chat_model_intents(
        bus, document, notifications, agent_dispatcher, composer_document,
        token_counter, publish_scene, publish_token_counter, comparison_cancelled_requests,
    )

    register_chat_knowledge_intents(bus, document, agent_dispatcher, publish_scene)
    bus.register_intent("scene", "sendMessage", send_message)
    register_chat_version_intents(
        bus, document, notifications, agent_dispatcher, composer_document,
        token_counter, publish_scene, publish_token_counter,
    )
    register_chat_cancel_intent(bus, agent_dispatcher, comparison_cancelled_requests)
