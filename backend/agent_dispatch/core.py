"""DispatcherCoreOps - AgentDispatcher's shared core: construction,
provider/model resolution, cancellation, approvals, and the `_dispatch`
run engine every start_* surface funnels through.

A MIXIN, not a standalone class: every method operates on the composing
class's shared state established by DispatcherCoreOps.__init__ - it is
composed exactly once, by backend/agents.py's
`class AgentDispatcher(DispatcherCoreOps, ...)`.

Method bodies are relocated VERBATIM from backend/agents.py; only the class
wrapper, imports, and the patch-seam rewrites below are new. Any name that
lives in backend/agents.py's module namespace (module helpers, constants,
names imported into it) is accessed late-bound as `agents_module.<name>`
through an in-body deferred import, NEVER via a module-top import here: a
top-level `from backend.agents import X` would be a circular import
(agents.py imports this module) AND would freeze the name at import time,
making the test suite's `monkeypatch.setattr(backend.agents, "X", ...)`
patches invisible to these methods. The deferred-import-then-attribute
pattern resolves the name on backend.agents at call time, so those patch
seams keep working with zero test changes.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.events import SessionBus
    from graphlink_settings_store import SettingsManager


class DispatcherCoreOps:
    """The shared dispatcher core: construction, model resolution, cancellation, approvals, and `_dispatch` (mixin - see module docstring)."""

    def __init__(self, settings_manager: SettingsManager, provider_runtime=None, diagnostics=None):
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        self._settings_manager = settings_manager
        # ADR-016 stage 16.3: optional - None (every existing call site
        # outside backend/app.py's real _configure_session) means "no
        # diagnostics recording", exactly RunRegistry's own on_claim/on_end
        # default. See backend/diagnostics.py's own module docstring.
        self._diagnostics = diagnostics
        # ADR-006 stage 6.5: the session's ProviderRuntime. None means "the
        # default session": every provider call keeps going through
        # api_provider's module-level functions exactly as before (which the
        # entire existing test suite monkeypatches), so default-session
        # behavior stays byte-identical. A non-None runtime (a non-default
        # session - see backend/app.py's _configure_session) is threaded
        # explicitly into the chat drivers and generate_image via their
        # additive `runtime=` kwarg.
        self._provider_runtime = provider_runtime
        # ADR-002 stage 2.3: the chat/conversation, chart, and note pilot
        # surfaces below claim into this ONE shared registry instead of
        # three independent dicts - see backend/run_lifecycle.py's own
        # docstring for the full reasoning and for why the other 9
        # dispatch surfaces still keep their own dict for now (deferred to
        # stage 2.4). "chat" is one shared kind for both start_chat_reply
        # and start_conversation_reply, mirroring the single dict they
        # already shared before this migration.
        self._runs = agents_module.RunRegistry(
            on_claim=diagnostics.record_run_claimed if diagnostics is not None else None,
            on_end=diagnostics.record_run_ended if diagnostics is not None else None,
        )
        # R4.4a/ADR-002 stage 2.4c: image generation ("image" kind, also
        # sharing self._runs now) is an INDEPENDENT single-slot kind,
        # separate from chat - preserves legacy's real, verified concurrent
        # capability (graphlink_window.py's self.chat_thread/
        # self.image_gen_thread were separate, never-aliased attributes, so
        # a chat request and an image-generation request genuinely run
        # concurrently today). No cancel_event/on_cancel: image generation
        # has no cancellation at all (see start_image_reply's own
        # docstring) - legacy's own generate_image() silently overwrote
        # self.image_gen_thread with no guard if fired twice (a latent bug,
        # not a deliberate concurrent-multi-image feature); start_image_
        # reply gives an honest "already generating" refusal instead of
        # replicating that hazard.
        #
        # R5.1/ADR-002 stage 2.4e: web research ("web_research" kind, also
        # sharing self._runs now) is a THIRD independent single-slot kind,
        # separate from chat/image - a web research run must be able to run
        # concurrently with either, same reasoning as image being
        # independent from chat. Cancellable via RunHandle.on_cancel
        # (cancel_token.cancel), not cancel_event - CancellationToken
        # (graphlink_plugins/web_research/domain.py) is a structurally
        # different class WebResearchService.run's own pipeline stages
        # already accept as `token:`, not a threading.Event.
        #
        # R5.2/ADR-002 stage 2.4d: artifact generation ("artifact" kind,
        # also sharing self._runs now) is a FOURTH independent single-slot
        # kind, separate from chat/image - an artifact-generation request
        # must be able to run concurrently with any of those two plus web
        # research, same reasoning as every prior independent slot above.
        # Cancellable via a plain threading.Event, same shape as chat -
        # unlike web_research's CancellationToken.
        #
        # R5.3/ADR-002 stage 2.4f: Gitlink Run ("gitlink_run" kind) and
        # Gitlink Apply ("gitlink_apply" kind), both also sharing self._runs
        # now, must be able to run concurrently with any of chat/image/
        # artifact/web_research, same reasoning as every prior independent
        # kind above. Run is cancellable via a plain threading.Event, same
        # shape as chat/artifact. Apply has no cancel_event at all -
        # matching image's own shape, legacy has zero cancel affordance for
        # the disk-write step either.
        #
        # UNLIKE every kind migrated so far, self._runs's is_busy("gitlink_
        # run"/"gitlink_apply") is NOT the real busy guard for either of
        # these two - node.pending_request_id (a per-SceneNode field) is,
        # shared across BOTH kinds so a Run cannot start while an Apply is
        # in flight on the SAME node, and vice versa (see
        # start_gitlink_run's/start_gitlink_apply's own docstrings for the
        # full synchronous-claim reasoning this preserves unchanged). This
        # registry is pure task/cancel_event bookkeeping for these two
        # kinds, deliberately NEVER consulted as the busy gate - a
        # session could have Gitlink Runs in flight on two DIFFERENT nodes
        # simultaneously, which self._runs's own kind-scoped is_busy() alone
        # could never distinguish (it has no per-node concept - see
        # RunHandle's own docstring in backend/run_lifecycle.py).
        # R5.4/ADR-002 stage 2.4g: Execution Sandbox Run ("code_sandbox"
        # kind), sharing self._runs, must be able to run concurrently with
        # any of the kinds above, same reasoning as every prior independent
        # kind. Same per-node busy-guard shape as gitlink_run/gitlink_apply
        # above (node.pending_request_id via
        # _CODE_EXEC_RUN_CLAIM_PLACEHOLDER, this registry pure task/
        # cancel_event/approval_future bookkeeping, never the busy gate) -
        # and the first kind to use RunHandle.approval_future, the ENTIRE
        # "waiting for human approval" mechanism (see start_code_sandbox_
        # run's own docstring), created eagerly at claim time, before the
        # background task even starts, so cancel_code_sandbox/
        # cancel_all_pending_approvals can always resolve it even if the
        # pipeline has not reached its own `await approval_future` yet.
        # Mutated IN PLACE on handle (a plain, non-frozen dataclass) on
        # every repair-loop iteration - a fresh Future replaces the old one
        # on the SAME handle object, never a new claim - see
        # start_code_sandbox_run's own repair-loop comments for why callers
        # must always re-read this field fresh, never cache a captured
        # reference.
        # R6.2/R8a/ADR-002 Workstream 1, migrated to self._runs by ADR-002
        # stage 2.3 (chart, note) and stage 2.4 (branch_comparison, branch_
        # synthesis): chart generation, Key Takeaway/Explainer Note
        # generation (ONE guard covering both agents deliberately - they
        # are the same user-facing gesture ("summarise this node into a
        # note") differing only in prompt, and letting a takeaway and an
        # explainer run concurrently would race two notes onto overlapping
        # canvas positions for no benefit), Compare Branches, and Synthesize
        # Branches are all FOUR independent single-slot kinds sharing this
        # one registry, all DIRECTLY AWAITED by their caller rather than
        # scheduled via asyncio.create_task (see start_chart_generation's
        # own docstring for why: each is a single combined create+generate
        # action with no pre-existing node to attach a spinner to, so the
        # caller genuinely needs the result back in the same round trip).
        # None of the four has a cancel_event: none of their agents have a
        # cancellation checkpoint of their own, and their legacy callers
        # had no stop() method either. Compare and Synthesize are kept as
        # separate kinds from each other and from note, not folded into
        # one - they are unrelated user gestures over possibly-overlapping
        # selections, so one running must never block another.
        # Execution Sandbox needs no persistent-process manager of its own -
        # VirtualEnvSandbox is request-scoped by design, constructed fresh
        # per run inside start_code_sandbox_run's own
        # asyncio.to_thread-wrapped worker function (exactly like
        # _call_gitlink_agent constructs a fresh GitlinkAgent per call) -
        # the only state that must survive between runs is the plain string
        # node.state.code_sandbox_sandbox_id, real SceneNode state, not a
        # live object.

    def _runtime_kwargs(self) -> dict:
        """ADR-006 stage 6.5: `{"runtime": self._provider_runtime}` for a
        non-default session, `{}` for the default one. The kwarg is OMITTED
        (not passed as None) for the default session, deliberately: many
        tests monkeypatch _call_chat_agent/_call_chat_agent_stream/
        api_provider.generate_image with fakes of the exact pre-6.5 arity,
        and the default session's calls must stay byte-identical to pre-6.5
        anyway - the module-global path IS the default runtime."""
        if self._provider_runtime is None:
            return {}
        return {"runtime": self._provider_runtime}

    # -- ADR-008 stage 8.3: the Builder --------------------------------------

    def active_provider_model(self) -> tuple[str, str]:
        """ADR-006 stage 6.8: the (provider, model) pair a chat dispatch
        would use right now, from THIS session's runtime (default session ->
        module-backed DEFAULT_RUNTIME). intents_chat stamps it onto reply
        nodes and hands it to the token counter for cost estimation."""
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        return agents_module.api_provider.describe_active_model(agents_module.config.TASK_CHAT, self._provider_runtime)

    def persona(self) -> str:
        """Mirror legacy graphlink_window.py's `_get_current_system_prompt`:
        fully suppressed (empty string) when the user has disabled the
        system prompt in Settings, otherwise the base persona text.

        Deliberate simplification vs legacy: legacy also prefixes
        THINKING_INSTRUCTIONS_PROMPT ahead of BASE_SYSTEM_PROMPT when the
        active provider's reasoning mode is "Thinking" (branching further on
        Ollama's vs Llama.cpp's own reasoning-mode setting). That branch is
        out of scope for this increment - see the final report."""
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        if not self._settings_manager.get_enable_system_prompt():
            return ""
        return agents_module.BASE_SYSTEM_PROMPT

    def _resolve_model_ref_for_dispatch(
        self, canvas_document, node_id: str | None, bus: "SessionBus | None" = None,
    ):
        """ADR-018 stage 18.2 + ADR-020 stage 20.3: the full node -> branch
        -> workspace-default rungs of graphlink_model_catalog.resolve_
        model_ref (graphlink_model_catalog.py:381-419), computed here
        (mirroring _resolve_branch_system_prompt's own "only when
        canvas_document/node_id are supplied" restriction) and returned
        already resolved. auto (which needs the unified catalog and the
        session's task-keyed default) stays OUT of scope here exactly like
        before 20.3 - `catalog=()` below makes resolve_model_ref's own auto
        rung come back empty whenever node/branch/workspace are ALL unset,
        so this still returns None in exactly that case, falling through to
        api_provider's existing task-keyed lookup UNCHANGED (see
        _provider_for_model_ref's own docstring in api_provider.py).
        Diagnostics-worthy (the "why this model" rung name) is thrown away
        at this layer on purpose - stage 18.3's explain-resolution intent
        recomputes it fresh from the SAME SceneDocument state the UI can
        already read, rather than this already-in-flight dispatch trying to
        smuggle it back out.

        ADR-020 stage 20.3: `bus`, when supplied, is the one extra piece of
        context needed to find `canvas_document.current_workspace_id`'s own
        workspace row - via `bus.chat_db_path` (stashed by backend/
        chat_library.py's register_chat_library; the SAME attribute backend/
        app.py's own eviction-flush call site already reads via
        `getattr(bus, "chat_db_path", None)`). Optional and defaulted to
        None so this method's pre-20.3 test call sites (an AgentDispatcher/
        SceneDocument pair with no real SessionBus at all) keep passing
        unchanged - a None bus (or a bare test double with no chat_db_path
        attribute) simply skips the workspace rung, exactly like the
        pre-20.3 code path when canvas_document/node_id were absent."""
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        if canvas_document is None or node_id is None:
            return None
        resolve_model_for_node = getattr(canvas_document, "resolve_model_for_node", None)
        if resolve_model_for_node is None:
            return None
        node_ref, branch_ref = resolve_model_for_node(node_id)

        workspace_ref = None
        chats_db_path = getattr(bus, "chat_db_path", None) if bus is not None else None
        workspace_id = getattr(canvas_document, "current_workspace_id", None)
        if chats_db_path is not None and workspace_id is not None:
            # Local import - backend.chat_library imports backend.canvas,
            # which imports THIS module (AgentDispatcher) at module level -
            # a top-level import here would be circular. Mirrors this
            # codebase's own established "deferred import to break a real
            # cycle" precedent (e.g. backend/api/intents_web_research.py's
            # own `from backend.canvas import _research_result_wire`).
            from backend.chat_library import get_workspace_default_model

            default = get_workspace_default_model(chats_db_path, workspace_id)
            if default is not None:
                provider, model_id = default
                workspace_ref = agents_module.ModelRef(provider, model_id)

        resolved = agents_module.resolve_model_ref(
            agents_module.config.TASK_CHAT, node_ref=node_ref, branch_ref=branch_ref, workspace_ref=workspace_ref, catalog=(),
        )
        return resolved.ref if resolved is not None else None

    def is_node_run_live(self, request_id: "str | None") -> bool:
        """True when `request_id` is a run this registry still holds.

        The per-node busy guard used by gitlink/code_sandbox/artifact/
        web_research reads node.pending_request_id, but that field outlives
        a cancelled run: cancelling releases the registry slot immediately
        while the worker unwinds in its own time. Asking the registry
        instead means "cancel, then Run again" works the instant the cancel
        lands, rather than being refused until the dead worker finishes.

        The synchronous claim placeholder counts as live - it is a claim its
        own caller is about to convert into a real handle."""
        from backend import agents as agents_module

        if not request_id:
            return False
        if request_id == agents_module._NODE_RUN_CLAIM_PLACEHOLDER:
            return True
        return self._runs.get(request_id) is not None

    def _resolve_branch_system_prompt(self, canvas_document, node_id: str | None) -> str | None:
        """R6.1 port of legacy graphlink_chat_agent.py's
        resolve_branch_system_prompt: given the id of a chat node about to be
        sent, walk its branch up to the root (SceneDocument.get_branch_root -
        the same parent-edge walk chat_branch_history/regenerate_response
        already use for this codebase's own precedent), then look for an
        edge whose source is a kind="note"/is_system_prompt=True node and
        whose target is that root. If one exists, its `content` REPLACES
        persona()'s resolution entirely for this send - legacy does not
        concatenate the two. Returns None (never "") when there is no such
        note, so callers can tell "no override, fall back to the default"
        apart from "the override IS a genuinely empty string" (not reachable
        via add_note's own default content, but kept as a clean contract).

        `canvas_document` is duck-typed, like start_conversation_reply's own
        `node` parameter above - this module deliberately does not import
        backend/canvas.py's SceneDocument (canvas.py imports FROM this
        module, so importing it back here would be circular). Both
        `canvas_document` and `node_id` are optional: callers that have no
        canvas context at all (there are none in this increment, but future
        dispatch surfaces might not) simply get None back, same as "no note
        attached"."""
        if canvas_document is None or node_id is None:
            return None
        root = canvas_document.get_branch_root(node_id)
        if root is None:
            return None
        for edge in canvas_document.edges.values():
            if edge.target != root.id:
                continue
            source_node = canvas_document.nodes.get(edge.source)
            if (
                source_node is not None
                and getattr(source_node, "kind", None) == "note"
                and getattr(source_node.state, "is_system_prompt", False)
            ):
                return source_node.content
        return None

    def cancel(self, request_id: str) -> bool:
        """kind="chat": ADR-002 stage 2.4b hardening - once more than one
        cancellable kind can share self._runs, a stale or mismatched
        request_id sent via the cancelChatRequest WS intent must never be
        able to trip a DIFFERENT kind's in-flight run instead of being
        safely rejected. Harmless no-op today (chat is still the only
        cancel_event-bearing kind in self._runs), but load-bearing the
        moment a second one joins - see RunRegistry.cancel's own
        docstring."""
        return self._runs.cancel(request_id, kind="chat")

    def cancel_all(self) -> None:
        """Trip the cancel event on every in-flight request for this
        session that has one. Called when a session's last WS connection
        disconnects (backend/app.py's ws_endpoint) - without this, a client
        that sends a message and immediately closes the tab leaves the real
        outbound LLM call (potentially a billed API request) running
        server-side, untethered, for up to WATCHDOG_TIMEOUT_SECONDS with no
        way for the client to ever cancel it (cancelChatRequest needs a live
        socket). Same cooperative-cancellation semantics as cancel() - this
        does not forcibly kill the in-flight thread, it only requests it
        stop at its next checkpoint, same as the timeout path already does.

        Delegates to self._runs.cancel_all(), which walks every claimed
        handle (chat/chart/note as of ADR-002 stage 2.3) and silently
        no-ops on kinds with no cancel_event - see
        backend/run_lifecycle.py's own docstring."""
        self._runs.cancel_all()

    def has_in_flight_runs(self) -> bool:
        """ADR-004 stage 4.3: the veto backend/app.py's session-eviction
        callback checks before tearing an idle session down. A monotonic-
        time TTL alone is not a substitute for actually knowing whether
        cooperative cancellation (cancel_all() above) has genuinely
        finished - this is a direct, cheap check of the same registry
        cancel_all() itself walks (self._runs, one claimed RunHandle per
        in-flight request across every kind), not a second bookkeeping
        mechanism that could drift from it.

        ADR-006 stage 6.2: release-on-cancel empties the claimed-handle map
        the instant cancel_all() fires, but the cancelled workers' tasks are
        still unwinding against this session's objects - has_any_live_work()
        counts those orphaned tasks too, so eviction still waits for the
        real work to actually end, exactly as it did before."""
        return self._runs.has_any_live_work()

    async def _dispatch(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        conversation_history,
        on_reply,
        on_begin,
        on_end,
        state_topic: str,
        stream: bool = False,
        canvas_document=None,
        node_id: str | None = None,
        on_partial=None,
        on_usage=None,
    ) -> None:
        """The shared real-dispatch pipeline behind both start_chat_reply
        (Composer, state_topic="app-composer") and start_conversation_reply
        (ConversationNode, state_topic="scene", R4.3) - one in-flight-request
        slot per session regardless of which caller occupies it. `on_begin`/
        `on_end` let each caller record the in-flight request_id on its own
        state (ComposerDocument.begin_request/end_request, or a
        ConversationNode's pending_request_id) without this method knowing
        which; `state_topic` is the topic republished around that state
        change so the right part of the UI refreshes.

        `stream` (R4.4, keyword-only, default False): when True, the reply is
        assembled from incremental `on_chunk` callbacks - see `_run`'s own
        streaming branch below - and broadcast live via
        `bus.publish_stream(...)` as it arrives, instead of waiting for one
        blocking call to return the full text. start_chat_reply is the ONLY
        caller that passes stream=True; start_conversation_reply omits the
        kwarg entirely and is completely unchanged by this addition. Either
        way, the completion hand-off below (`on_reply(reply_text)` then
        `await bus.publish("scene")`) is identical - callers never see a
        difference once the reply is ready.

        `canvas_document`/`node_id` (R6.1, both keyword-only, default None):
        optional branch-system-prompt-override context - see
        _resolve_branch_system_prompt. Only start_chat_reply's send_message/
        regenerate_response call sites (backend/canvas.py) pass these today;
        every other caller (including start_conversation_reply) omits them,
        which simply falls back to persona()'s existing resolution, byte-
        identical to this method's pre-R6.1 behavior.

        `on_partial(text)` (ADR-006 stage 6.4, closes H5): called on the
        failure/cancel/timeout paths of a STREAMING dispatch with whatever
        text had accumulated before the stream died, instead of that text
        being destroyed with the worker frame. The accumulator lives on the
        EVENT-LOOP side (inside _pump), because the full text otherwise
        exists only in the provider generator's frame on the worker thread -
        unreachable from the except blocks below. Never called with
        blank/whitespace-only text (nothing worth preserving), never called
        on the non-streaming path (nothing accumulated), and always followed
        by a "scene" publish so the committed partial renders immediately.
        Callers own the commit semantics (create a node, update in place,
        append a message) AND any liveness guards their target needs."""
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        async def _finalize() -> None:
            # ADR-006 stage 6.2: the user-visible end transition, run by
            # RunRegistry.cancel() the moment a cancel lands (slot freed +
            # composer back to idle immediately), OR by _run's own finally on
            # a normal completion - never both (release() returning True is
            # the arbiter; see RunHandle.finalize). Defined up here, above the
            # busy check, so the check-to-claim stretch below stays free of
            # statements containing awaits - test_dispatch_claim_ordering's
            # AST gate scans that stretch recursively.
            on_end()
            await bus.publish(state_topic)

        if self._runs.is_busy("chat"):
            # Single-request-per-session guard: never start a second
            # concurrent request while one is already in flight.
            notifications_state.show("A response is already being generated.", "info")
            await bus.publish("notification")
            return

        # ADR-006 stage 6.5: gate on THIS session's runtime when one was
        # injected; the default session keeps calling the module-level
        # api_provider.is_configured() so existing monkeypatches of that
        # function still intercept the gate.
        is_configured = (
            self._provider_runtime.is_configured
            if self._provider_runtime is not None
            else agents_module.api_provider.is_configured
        )
        if not is_configured():
            # Fail fast and clean, synchronously, before touching any thread -
            # a never-configured install gets an honest, actionable error.
            notifications_state.show(
                "No AI provider is configured yet. Open Settings to choose Ollama, "
                "Llama.cpp, or an API provider.",
                "error",
            )
            await bus.publish("notification")
            return

        # Claimed SYNCHRONOUSLY, with no `await` between the is_busy() check
        # above and this claim - see backend/run_lifecycle.py's own
        # docstring for why that ordering is load-bearing, not incidental.
        cancel_event = threading.Event()
        handle = self._runs.claim(
            "chat", node_id=node_id, cancel_event=cancel_event, finalize=_finalize
        )
        request_id = handle.request_id

        async def _run():
            async def _commit_partial() -> None:
                # ADR-006 stage 6.4 (H5): commit whatever streamed before the
                # failure/timeout instead of destroying it. Guarded on a
                # caller actually opting in AND on there being real text - a
                # stream that died before its first delta has nothing worth
                # preserving, and the pre-6.4 discard behavior stays exact
                # for it. NOT called on cancel (see the cancel except block).
                #
                # 6.4 review fix (HIGH): also gated on this run still being
                # REGISTERED - the same staleness gate 6.2 put on
                # web_research's terminal callbacks. cancel/cancel_all pop
                # the handle immediately while the worker can take
                # arbitrarily long to observe cancel_event (a stalled
                # provider read); by the time it unwinds here, a replacement
                # run may already be streaming into the same node, and a
                # stale commit would clobber its state (or a post-cancel
                # undo's restored state). A popped handle means some
                # authority already decided this run's outputs no longer
                # land - partials included.
                if self._runs.get(request_id) is None:
                    return
                if on_partial is None or not accumulated["text"].strip():
                    return
                if inspect.iscoroutinefunction(on_partial):
                    await on_partial(accumulated["text"])
                else:
                    on_partial(accumulated["text"])
                await bus.publish("scene")

            on_begin(request_id)
            await bus.publish(state_topic)
            try:
                # R6.1: a branch-attached System Prompt note (see
                # _resolve_branch_system_prompt) REPLACES persona()'s
                # resolution entirely when present - computed once up front,
                # shared by both the streaming and non-streaming branches
                # below, exactly like persona() itself was before this
                # addition (each branch used to call self.persona() fresh -
                # now both read this single resolved value instead).
                override = self._resolve_branch_system_prompt(canvas_document, node_id)
                persona_text = override if override is not None else self.persona()
                # ADR-018 stage 18.2: computed once, shared by both branches
                # below, exactly like persona_text/override_kwargs above.
                model_ref = self._resolve_model_ref_for_dispatch(canvas_document, node_id, bus=bus)
                model_ref_kwargs = {"model_ref": model_ref} if model_ref is not None else {}
                # ADR-018 stage 18.4: the auto-policy rung's own catalog/
                # settings access lives in api_provider, not here (mirrors
                # model_ref's own node/branch-only scope at this layer) -
                # this only threads the SettingsManager reference down,
                # same omit-when-None posture as every other additive kwarg
                # in this dispatch.
                settings_manager_kwargs = (
                    {"settings_manager": self._settings_manager} if self._settings_manager is not None else {}
                )
                # ADR-006 stage 6.7: a note override reaches the wire RAW
                # (never wrapped in "You are Graphlink Assistant. ...") -
                # flagged to _call_chat_agent(_stream) only when an override
                # is actually present, so every default-path test fake of
                # the exact pre-6.7 arity keeps working (same omit-when-
                # default pattern as _runtime_kwargs).
                override_kwargs = {"persona_is_override": True} if override is not None else {}

                # ADR-006 stage 6.6: trim/summarize notification. ChatWorker
                # invokes this on the WORKER thread when older turns had to
                # be dropped to fit the model's context window - marshal to
                # the loop (run_coroutine_threadsafe, the coroutine sibling
                # of _thread_on_chunk's call_soon_threadsafe pattern) and
                # surface it as an info notification.
                dispatch_loop = asyncio.get_running_loop()

                def _thread_on_context_trimmed(dropped_count: int, summarized: bool) -> None:
                    message = (
                        "Older conversation turns were summarized to fit the "
                        "model's context window."
                        if summarized
                        else "Older conversation turns were dropped to fit the "
                        "model's context window."
                    )

                    async def _notify() -> None:
                        notifications_state.show(message, "info")
                        await bus.publish("notification")

                    asyncio.run_coroutine_threadsafe(_notify(), dispatch_loop)

                # ADR-018 stage 18.5: fallback-substitution notification.
                # api_provider's chat()/chat_stream() outer wrapper invokes
                # this on the WORKER thread the instant it decides to
                # dispatch a SECOND time against a different provider -
                # "never a silent swap" per the ADR's own decision #4. Same
                # marshal-to-loop pattern as _thread_on_context_trimmed
                # above; always supplied (unconditionally, matching
                # on_context_trimmed's own posture), since notifications_state/
                # bus/dispatch_loop are always available in this scope.
                def _thread_on_fallback(failed_provider: str, fallback_ref, exc: Exception) -> None:
                    message = (
                        f"{failed_provider} is unavailable right now - this reply used "
                        f"{fallback_ref.provider} ({fallback_ref.model_id}) instead."
                    )

                    async def _notify() -> None:
                        notifications_state.show(message, "warning")
                        await bus.publish("notification")

                    asyncio.run_coroutine_threadsafe(_notify(), dispatch_loop)

                # ADR-006 stage 6.8: real-usage capture. The worker writes
                # the provider's normalized usage dict into this holder
                # BEFORE its to_thread future resolves (ChatWorker.run calls
                # on_usage before returning), so the read in the success
                # path below is ordered-after the write by the future's own
                # happens-before edge - no marshaling needed for a single
                # pre-join write. Passed to the drivers omit-when-None (only
                # when the caller actually supplied on_usage), preserving
                # the strict-arity compat pin for every other dispatch.
                usage_holder = {"usage": None}

                def _thread_on_usage(usage_dict) -> None:
                    usage_holder["usage"] = usage_dict

                usage_kwargs = {"on_usage": _thread_on_usage} if on_usage is not None else {}
                # ADR-006 stage 6.4: the loop-side partial-text accumulator.
                # A dict, not a str, so _pump (a different coroutine) can
                # mutate it and the except blocks below can read it after the
                # pump has drained - by the time any except runs, the inner
                # finally has already awaited pump_task, so this holds every
                # delta that arrived before the stream died.
                accumulated = {"text": ""}
                if stream:
                    loop = asyncio.get_running_loop()
                    queue: asyncio.Queue = asyncio.Queue()
                    _STREAM_DONE = object()

                    def _thread_on_chunk(delta: str, reset: bool) -> None:
                        # Runs on the WORKER THREAD inside asyncio.to_thread -
                        # this is the only safe way to hand data to the event
                        # loop from another OS thread; never touch
                        # `queue`/`bus` directly here, only via
                        # call_soon_threadsafe.
                        loop.call_soon_threadsafe(queue.put_nowait, (delta, reset))

                    async def _pump() -> None:
                        # Batches raw on_chunk deltas into WS "stream" frames:
                        # flush every FLUSH_INTERVAL_S if anything is
                        # buffered, or immediately once FLUSH_CHARS is
                        # reached, whichever comes first. A `reset` item
                        # (discarding a failed reasoning-retry attempt) always
                        # flushes whatever is buffered first, then emits its
                        # own reset frame immediately - never batched away.
                        seq = 0
                        buffer = ""
                        FLUSH_INTERVAL_S, FLUSH_CHARS = 0.06, 40
                        finished = False
                        last_flush = loop.time()

                        async def _emit(text: str, *, done: bool = False, reset: bool = False) -> None:
                            nonlocal seq
                            await bus.publish_stream(
                                topic=state_topic,
                                request_id=request_id,
                                seq=seq,
                                delta=text,
                                done=done,
                                reset=reset,
                            )
                            seq += 1

                        while not finished:
                            got = False
                            try:
                                item = await asyncio.wait_for(queue.get(), timeout=FLUSH_INTERVAL_S)
                                got = True
                            except asyncio.TimeoutError:
                                pass
                            if got:
                                pending = [item]
                                while not queue.empty():  # drain a burst without waiting
                                    pending.append(queue.get_nowait())
                                for it in pending:
                                    if finished:
                                        # A delta queued essentially
                                        # concurrently with _STREAM_DONE (the
                                        # background thread is never actually
                                        # interrupted on timeout - see this
                                        # module's own docstring) could still
                                        # land in the same drained burst AFTER
                                        # the done marker. Discard it rather
                                        # than buffering a stray, cosmetic
                                        # trailing update that would arrive
                                        # after the request already ended.
                                        continue
                                    if it is _STREAM_DONE:
                                        finished = True
                                    else:
                                        delta, reset = it
                                        if reset:
                                            if buffer:
                                                await _emit(buffer)
                                                buffer = ""
                                            await _emit("", reset=True)
                                            last_flush = loop.time()
                                            # A reset discards the prior
                                            # attempt's text everywhere -
                                            # including the partial-commit
                                            # accumulator (6.4).
                                            accumulated["text"] = ""
                                        else:
                                            buffer += delta
                                            accumulated["text"] += delta
                            now = loop.time()
                            if buffer and (
                                finished or len(buffer) >= FLUSH_CHARS or (now - last_flush) >= FLUSH_INTERVAL_S
                            ):
                                await _emit(buffer)
                                buffer = ""
                                last_flush = now
                        # Guaranteed final flush, unconditional and always
                        # last, on EVERY exit path (success, cancel, timeout,
                        # other error) - see the `finally` below that always
                        # queues _STREAM_DONE before awaiting this task.
                        await _emit("", done=True)

                    pump_task = asyncio.create_task(_pump())
                    try:
                        reply_text = await asyncio.wait_for(
                            asyncio.to_thread(
                                agents_module._call_chat_agent_stream,
                                conversation_history,
                                persona_text,
                                cancel_event,
                                _thread_on_chunk,
                                # ADR-006 stage 6.5: non-default sessions only
                                # - see _runtime_kwargs' own docstring.
                                **self._runtime_kwargs(),
                                **override_kwargs,
                                **usage_kwargs,
                                **model_ref_kwargs,
                                **settings_manager_kwargs,
                                on_context_trimmed=_thread_on_context_trimmed,
                                on_fallback=_thread_on_fallback,
                            ),
                            timeout=agents_module.WATCHDOG_TIMEOUT_SECONDS,
                        )
                    finally:
                        # Guarantees the pump always terminates and sends its
                        # final done:true frame, on EVERY exit path - success,
                        # timeout, cancel, or any other exception raised out
                        # of the to_thread call above.
                        queue.put_nowait(_STREAM_DONE)
                        await pump_task
                else:
                    reply_text = await asyncio.wait_for(
                        asyncio.to_thread(
                            agents_module._call_chat_agent,
                            conversation_history,
                            persona_text,
                            cancel_event,
                            **self._runtime_kwargs(),
                            **override_kwargs,
                            **usage_kwargs,
                            **model_ref_kwargs,
                            **settings_manager_kwargs,
                            on_context_trimmed=_thread_on_context_trimmed,
                            on_fallback=_thread_on_fallback,
                        ),
                        timeout=agents_module.WATCHDOG_TIMEOUT_SECONDS,
                    )
                if inspect.iscoroutinefunction(on_reply):
                    await on_reply(reply_text)
                else:
                    on_reply(reply_text)
                # ADR-006 stage 6.8: hand real usage to the caller AFTER
                # on_reply (same success-path ordering as on_reply itself) -
                # only on success, only when the provider reported counts.
                if on_usage is not None and usage_holder["usage"]:
                    if inspect.iscoroutinefunction(on_usage):
                        await on_usage(usage_holder["usage"])
                    else:
                        on_usage(usage_holder["usage"])
                await bus.publish("scene")
            except asyncio.TimeoutError:
                cancel_event.set()
                notifications_state.show(
                    "The model stopped responding before the request completed. "
                    "Please try again or choose a faster model.",
                    "error",
                )
                await bus.publish("notification")
                await _commit_partial()
            except agents_module.api_provider.RequestCancelledError:
                notifications_state.show("Request cancelled.", "info")
                await bus.publish("notification")
                # DELIBERATELY no _commit_partial (6.4 review fix): cancel is
                # the user saying "stop - keep what I had", not a failure.
                # Committing here would replace a regenerated node's COMPLETE
                # original answer with a truncated partial and tell the user
                # to redo the very thing they just aborted; discarding keeps
                # R4.2's pinned cancel-discards-everything semantics. H5's
                # partial preservation is for streams that DIE (error/
                # timeout), where the text would otherwise be lost against
                # the user's will.
            except Exception as exc:
                logging.getLogger(__name__).exception("chat dispatch failed")
                notifications_state.show(f"AI response failed: {exc}", "error")
                await bus.publish("notification")
                await _commit_partial()
            finally:
                # ADR-006 stage 6.2: gated on release() actually popping the
                # handle. On a normal completion it does, and the end
                # transition runs here as before. After a CANCEL, release()
                # returns False (cancel already popped the handle and ran
                # _finalize itself) - re-running on_end here would be at best
                # redundant and at worst would clobber a NEWER run's
                # "generating" state, since the slot was freed the moment the
                # cancel landed and a new claim may already be active.
                if self._runs.release(request_id):
                    on_end()
                    await bus.publish(state_topic)

        # NOT awaited here - start_chat_reply/start_conversation_reply return
        # immediately after scheduling the task. This is load-bearing: the WS
        # connection this session serves runs a plain sequential
        # `while True: message = await websocket.receive_json(); ...` read
        # loop (backend/app.py) - if this handler awaited the full chat call
        # inline, no further message on that same socket (including a
        # cancelChatRequest intent) would even be read off the wire until the
        # handler returned, making cooperative cancellation impossible. The
        # claim itself already landed above, before this task was even
        # created - this line only attaches the task reference (anti-GC
        # only, see backend/run_lifecycle.py - never used for real
        # cancellation).
        self._runs.attach_task(handle, asyncio.create_task(_run()))

    def _resolve_approval(self, request_id: str, approved: bool) -> bool:
        """The shared approve/deny primitive backing approve_code_execution/
        deny_code_execution below - looks up request_id directly in
        self._runs (a shared uuid4 namespace across every migrated kind, not
        just code_sandbox), mirroring the WS intent layer's own
        two-shared-intents design (approveCodeExecution/denyCodeExecution,
        not one intent per kind). No explicit kind check needed here (unlike
        cancel_code_sandbox above): handle.approval_future is None for every
        kind except code_sandbox (only that kind ever passes one to
        claim()), so that field alone is already the correct discriminator -
        a chat/chart/.../gitlink request_id is naturally rejected by the
        `is None` check below.

        Guarding with `future.done()` is LOAD-BEARING, not defensive fluff -
        a duplicate/stale approve-or-deny message (e.g. a double-click, or a
        message that arrives after cancel_code_sandbox/
        cancel_all_pending_approvals already resolved this same future)
        would otherwise raise asyncio.InvalidStateError.

        ADR-005 stage 5.5 review-fix: when `handle.approval_snapshot_fn` is
        set (code_sandbox only - see RunHandle's own doc), snapshot it into
        `handle.approval_snapshot` HERE, in this same uninterruptible
        synchronous stretch as `future.set_result()`, never after. This
        method has no `await` anywhere in its own call chain, so nothing else
        can run between the read and the resolve - closing a real race an
        adversarial review found: `future.set_result()` only SCHEDULES the
        awaiting `_run()` task's resumption rather than running it inline, so
        a second WS connection's setCodeSandboxAllowSourceBuilds could
        otherwise land in that gap and silently change what an already-
        decided approval installs. Only snapshotted on an actual approval -
        a denied run never consumes it, so there is nothing to protect."""
        handle = self._runs.get(request_id)
        if handle is None or handle.approval_future is None:
            return False
        future = handle.approval_future
        if not future.done():
            if approved and handle.approval_snapshot_fn is not None:
                handle.approval_snapshot = handle.approval_snapshot_fn()
            future.set_result(approved)
        return True

    def approve_code_execution(self, request_id: str) -> bool:
        return self._resolve_approval(request_id, True)

    def deny_code_execution(self, request_id: str) -> bool:
        return self._resolve_approval(request_id, False)

    def cancel_all_pending_approvals(self) -> None:
        """Called ONLY from backend/app.py's ws_endpoint disconnect handler,
        ONLY when the session's last connection drops (session.connection_
        count == 0) - a DELIBERATE, SCOPED extension of that existing
        disconnect contract, applied ONLY to these two kinds (see
        backend/app.py's own comment for why this is not retrofitted onto
        the other migrated kinds: every one of those already self-
        terminates via asyncio.wait_for(..., timeout=...), but an approval
        pause has NO timeout by design - the whole point is "wait for a
        human, however long that takes" - so without this auto-deny it
        would hang forever, permanently locking node.pending_request_id on
        an abandoned tab).

        Delegates to self._runs.cancel_all_pending_approvals(), which walks
        both kinds and resolves any undone future with False (auto-deny) -
        the same future.done() guard as _resolve_approval applies here for
        the same reason (a request that already resolved, e.g. because a
        human approved it a moment before the last tab closed, must not be
        clobbered). backend/app.py's ws_endpoint calls this AFTER cancel_all()
        - by then cancel_all() has already tripped these kinds' cancel_event
        too (now that they share self._runs with every other cancellable
        kind), closing a real pre-existing gap: a disconnect mid-EXECUTION
        (past the approval gate) previously left code_sandbox's cancel_event
        untripped entirely, since it did not live in the dict cancel_all()
        used to walk."""
        # H2: "harness" joins for the same reason "code_sandbox" is here -
        # its approval pause has no timeout by design, so a last-tab
        # disconnect would otherwise park it forever.
        self._runs.cancel_all_pending_approvals(("code_sandbox", "harness"))

    def _cancel_with_pending_approval_denied(self, request_id: str, kind: str) -> bool:
        """Shared shape behind cancel_builder/cancel_harness/
        cancel_code_sandbox: cancel means deny any parked approval FIRST
        (before self._runs.cancel's own kind check below ever runs), then
        the standard release-on-cancel.

        The `handle.kind == kind` check is load-bearing, not defensive
        fluff: without it, a stale or foreign request_id belonging to a
        DIFFERENT approval-gated kind currently parked mid-gate would have
        its approval_future wrongly resolved to False here - silently
        denying a human approval that was never meant for this call at
        all."""
        handle = self._runs.get(request_id)
        if (
            handle is not None and handle.kind == kind
            and handle.approval_future is not None and not handle.approval_future.done()
        ):
            handle.approval_future.set_result(False)
        return self._runs.cancel(request_id, kind=kind)
