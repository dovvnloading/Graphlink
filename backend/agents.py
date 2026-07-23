"""Qt-removal plan R4: the agent-dispatch service.

This is where the new FastAPI backend gets its first genuine LLM round trip.
Two separate jobs live here:

1. `bootstrap_provider_state()` - runs exactly ONCE per process, at app
   startup (see backend/app.py's create_app()). It bootstraps api_provider.py's
   module-level provider globals (USE_API_MODE/API_PROVIDER_TYPE/API_CLIENT/
   LOCAL_PROVIDER_TYPE/...) from the SAME shared SettingsManager/session.dat
   file the legacy Qt app reads and writes - so whichever provider/mode the
   user last configured (Ollama, Llama.cpp, or a cloud API) is already live
   the moment the first WS session connects, with no separate "initialize
   the agent layer" step the frontend has to trigger.

2. `AgentDispatcher` - one instance PER SESSION (never a module-level
   singleton: two sessions must never share in-flight request state).
   `start_chat_reply()` is the real Send-to-reply pipeline: it schedules the
   blocking `api_provider.chat()` call off the FastAPI event loop (via
   `asyncio.to_thread`), enforces a single fixed hard timeout
   (`WATCHDOG_TIMEOUT_SECONDS`), and supports cooperative cancellation via a
   `threading.Event` a client can trip mid-flight through the
   `cancelChatRequest` intent this module registers.

   Legacy has a two-tier watchdog for non-audio requests: a 35s "still
   working..." stall notice, then a 420s hard timeout. This increment
   deliberately ships only the hard 420s timeout via `asyncio.wait_for`,
   cutting the intermediate stall-notice tier as an honest simplification -
   the two-tier warning is a UX nicety, not a correctness requirement. Note
   also that legacy itself does not kill the underlying call on timeout
   either, it only stops waiting for it - same limitation here: the
   worker-thread call to `api_provider.chat()` keeps running in its thread
   pool slot after a timeout fires, it is simply no longer awaited.

R4.4 ("true token streaming") adds a `stream: bool` keyword-only parameter to
`_dispatch`, used ONLY by `start_chat_reply` (the Composer/ChatNode reply
path - see backend/composer.py's `app-composer` topic).
`start_conversation_reply` (ConversationNode) is completely unchanged: it
passes no `stream` kwarg at all and keeps calling the plain blocking
`_call_chat_agent` driver, exactly as R4.3 shipped it - streaming that
surface is a deliberately deferred follow-up (see the R4.4 design spec).

When streaming, `_run()` hands raw `on_chunk(delta, reset)` callbacks arriving
on a worker OS thread (inside `asyncio.to_thread`) back to the event loop via
`loop.call_soon_threadsafe(queue.put_nowait, ...)` feeding an `asyncio.Queue`
- the only safe way to cross that thread boundary. A `_pump()` coroutine
drains that queue and batches deltas into `bus.publish_stream(...)` calls
(new sibling to `bus.publish()` on `backend.events.SessionBus`, see that
module) under a fixed flush policy: every ~60ms if anything is buffered, or
immediately once 40 characters have accumulated, whichever comes first - plus
an unconditional final flush the instant the underlying call finishes, on
EVERY exit path (success, cancel, timeout, or any other exception), so the
pump can never leave a stream hanging without its final `done: true` frame.
The completion hand-off (`on_reply(reply_text)` with the full accumulated
text, then `await bus.publish("scene")`) is byte-identical to the
non-streaming path - callers never know or care whether their reply arrived
in one blocking call or was assembled from many small chunks.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import uuid

import api_provider
import graphlink_task_config as config
from graphlink_chat_agent import ChatAgent
from graphlink_licensing import SettingsManager  # type hint only
from graphlink_plugins.web_research.domain import (
    CancellationToken,
    ProgressEvent,
    RequestCancelled,
    ResearchFailure,
    WebResearchRequest,
)
from graphlink_plugins.web_research.service import WebResearchService
from graphlink_prompts import BASE_SYSTEM_PROMPT

from backend.events import SessionBus  # type hint only

logger = logging.getLogger(__name__)

# The single fixed hard timeout for this increment - see the module
# docstring for why there is no intermediate stall-notice tier here.
WATCHDOG_TIMEOUT_SECONDS = 420

# R5.1: Web Research gets its own, longer watchdog rather than reusing
# WATCHDOG_TIMEOUT_SECONDS=420 - a research run can involve up to 4
# sequential source fetches (each individually capped by
# FetchPolicy.total_timeout_seconds, ~30s) plus up to 6 sequential LLM round
# trips (refine_query + up to 4x assess_source + summarize), none of which
# has its own outer timeout beyond this one. Realistic worst-case legitimate
# duration is roughly 660s, so 900s gives headroom without being unbounded.
WEB_RESEARCH_WATCHDOG_TIMEOUT_SECONDS = 900


def bootstrap_provider_state(settings_manager: SettingsManager) -> None:
    """Bootstrap api_provider's module-level provider state from persisted
    settings. Call exactly ONCE per process (this is process-global state,
    not session state) - see backend/app.py's create_app()."""
    # Unconditional and first, regardless of active mode: resolves Auto/
    # inherited Ollama task-model assignments against the cached scan, same
    # as legacy does at startup.
    config.sync_ollama_task_models(settings_manager)

    mode_text = settings_manager.get_current_mode()
    try:
        _apply_mode(mode_text, settings_manager)
        settings_manager.set_current_mode(mode_text)
    except Exception:
        # Funnels BOTH a real initialize_* failure (e.g. a persisted API key
        # that no longer validates) AND a garbage/unrecognized persisted mode
        # string through the same fallback - simpler than legacy's separate
        # handling of those two cases, same practical outcome: the app always
        # comes up in a usable state instead of failing to start.
        logger.warning(
            "failed to apply persisted provider mode %r; falling back to %s",
            mode_text,
            config.MODE_OLLAMA_LOCAL,
            exc_info=True,
        )
        settings_manager.set_current_mode(config.MODE_OLLAMA_LOCAL)
        api_provider.initialize_local_provider(config.LOCAL_PROVIDER_OLLAMA)


def _apply_mode(mode_text: str, settings_manager: SettingsManager) -> None:
    """Three-way dispatch mirroring the legacy mode-switch handlers. Raises
    ValueError for any mode_text it does not recognize - see
    bootstrap_provider_state's single fallback branch above."""
    if mode_text == config.MODE_OLLAMA_LOCAL:
        api_provider.initialize_local_provider(
            config.LOCAL_PROVIDER_OLLAMA,
            {"reasoning_mode": settings_manager.get_ollama_reasoning_mode()},
        )
    elif mode_text == config.MODE_LLAMACPP_LOCAL:
        api_provider.initialize_local_provider(
            config.LOCAL_PROVIDER_LLAMACPP,
            settings_manager.get_llama_cpp_settings(),
            preload_model=False,
        )
    elif mode_text == config.MODE_API_ENDPOINT:
        provider = settings_manager.get_api_provider()
        base_url = settings_manager.get_api_base_url()
        for task, model in settings_manager.get_api_models(provider).items():
            api_provider.set_task_model(task, model)
        if provider == config.API_PROVIDER_OPENAI:
            key = settings_manager.get_openai_key()
        elif provider == config.API_PROVIDER_ANTHROPIC:
            key = settings_manager.get_anthropic_key()
        else:
            key = settings_manager.get_gemini_key()
        api_provider.initialize_api(provider, key, base_url)
    else:
        raise ValueError(f"unrecognized provider mode: {mode_text!r}")


class AgentDispatcher:
    """One instance per session - never a module-level singleton, since two
    sessions must never share in-flight request state."""

    def __init__(self, settings_manager: SettingsManager):
        self._settings_manager = settings_manager
        # request_id -> {"cancel_event": threading.Event, "task": asyncio.Task}
        self._requests: dict[str, dict] = {}
        # R4.4a: an INDEPENDENT in-flight slot for image generation, separate
        # from self._requests (chat/conversation). Preserves legacy's real,
        # verified concurrent capability - graphlink_window.py's
        # self.chat_thread/self.image_gen_thread are separate, never-aliased
        # attributes, so a chat request and an image-generation request
        # genuinely run concurrently today. Reusing self._requests for image
        # generation too would be a real, visible behavior regression (a user
        # could no longer send a chat message while an image generates), so
        # this stays a second, independent dict rather than a new key inside
        # the existing one. Still single-slot PER KIND (one image request at
        # a time, same as chat): legacy's generate_image() silently
        # overwrites self.image_gen_thread with no guard if fired twice (a
        # latent bug - the orphaned old QThread keeps running unreferenced),
        # not a deliberate concurrent-multi-image feature; start_image_reply
        # below gives an honest "already generating" refusal instead of
        # replicating that hazard. request_id -> {"task": asyncio.Task} - no
        # "cancel_event" key here, unlike self._requests: image generation
        # has no cancellation at all (see start_image_reply's own docstring).
        self._image_requests: dict[str, dict] = {}
        # R5.1: a THIRD independent in-flight-request slot, separate from both
        # self._requests (chat/conversation) and self._image_requests - a web
        # research run and a chat/image request must be able to run
        # concurrently, same reasoning R4.4a used for _image_requests being
        # independent from _requests. request_id -> {"cancel_token":
        # CancellationToken, "task": asyncio.Task}.
        self._web_research_requests: dict[str, dict] = {}

    def persona(self) -> str:
        """Mirror legacy graphlink_window.py's `_get_current_system_prompt`:
        fully suppressed (empty string) when the user has disabled the
        system prompt in Settings, otherwise the base persona text.

        Deliberate simplification vs legacy: legacy also prefixes
        THINKING_INSTRUCTIONS_PROMPT ahead of BASE_SYSTEM_PROMPT when the
        active provider's reasoning mode is "Thinking" (branching further on
        Ollama's vs Llama.cpp's own reasoning-mode setting). That branch is
        out of scope for this increment - see the final report."""
        if not self._settings_manager.get_enable_system_prompt():
            return ""
        return BASE_SYSTEM_PROMPT

    def cancel(self, request_id: str) -> bool:
        entry = self._requests.get(request_id)
        if entry is None:
            return False
        entry["cancel_event"].set()
        return True

    def cancel_all(self) -> None:
        """Trip the cancel event on every in-flight request for this
        session. Called when a session's last WS connection disconnects
        (backend/app.py's ws_endpoint) - without this, a client that sends a
        message and immediately closes the tab leaves the real outbound LLM
        call (potentially a billed API request) running server-side,
        untethered, for up to WATCHDOG_TIMEOUT_SECONDS with no way for the
        client to ever cancel it (cancelChatRequest needs a live socket).
        Same cooperative-cancellation semantics as cancel() - this does not
        forcibly kill the in-flight thread, it only requests it stop at its
        next checkpoint, same as the timeout path already does."""
        for entry in self._requests.values():
            entry["cancel_event"].set()

    def cancel_web_research(self, request_id: str) -> bool:
        entry = self._web_research_requests.get(request_id)
        if entry is None:
            return False
        entry["cancel_token"].cancel()
        return True

    def is_web_research_busy(self) -> bool:
        """Lets callers check the single-slot guard before mutating scene
        state, so a Run click on a node other than the one already running
        doesn't reset that node's progress/error fields only to be rejected
        a moment later by start_web_research's own busy check."""
        return bool(self._web_research_requests)

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
        difference once the reply is ready."""
        if self._requests:
            # Single-request-per-session guard: never start a second
            # concurrent request while one is already in flight.
            notifications_state.show("A response is already being generated.", "info")
            await bus.publish("notification")
            return

        if not api_provider.is_configured():
            # Fail fast and clean, synchronously, before touching any thread -
            # a never-configured install gets an honest, actionable error.
            notifications_state.show(
                "No AI provider is configured yet. Open Settings to choose Ollama, "
                "Llama.cpp, or an API provider.",
                "error",
            )
            await bus.publish("notification")
            return

        request_id = uuid.uuid4().hex
        cancel_event = threading.Event()

        async def _run():
            on_begin(request_id)
            await bus.publish(state_topic)
            try:
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
                                        else:
                                            buffer += delta
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
                                _call_chat_agent_stream,
                                conversation_history,
                                self.persona(),
                                cancel_event,
                                _thread_on_chunk,
                            ),
                            timeout=WATCHDOG_TIMEOUT_SECONDS,
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
                        asyncio.to_thread(_call_chat_agent, conversation_history, self.persona(), cancel_event),
                        timeout=WATCHDOG_TIMEOUT_SECONDS,
                    )
                if inspect.iscoroutinefunction(on_reply):
                    await on_reply(reply_text)
                else:
                    on_reply(reply_text)
                await bus.publish("scene")
            except asyncio.TimeoutError:
                cancel_event.set()
                notifications_state.show(
                    "The model stopped responding before the request completed. "
                    "Please try again or choose a faster model.",
                    "error",
                )
                await bus.publish("notification")
            except api_provider.RequestCancelledError:
                notifications_state.show("Request cancelled.", "info")
                await bus.publish("notification")
            except Exception as exc:
                logging.getLogger(__name__).exception("chat dispatch failed")
                notifications_state.show(f"AI response failed: {exc}", "error")
                await bus.publish("notification")
            finally:
                # Unconditional on every exit path (success, timeout, cancel,
                # other error) so the caller's state always returns to a
                # usable idle state.
                self._requests.pop(request_id, None)
                on_end()
                await bus.publish(state_topic)

        # NOT awaited here - start_chat_reply/start_conversation_reply return
        # immediately after scheduling the task. This is load-bearing: the WS
        # connection this session serves runs a plain sequential
        # `while True: message = await websocket.receive_json(); ...` read
        # loop (backend/app.py) - if this handler awaited the full chat call
        # inline, no further message on that same socket (including a
        # cancelChatRequest intent) would even be read off the wire until the
        # handler returned, making cooperative cancellation impossible.
        self._requests[request_id] = {"cancel_event": cancel_event, "task": asyncio.create_task(_run())}

    async def start_chat_reply(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        composer_document,
        conversation_history,
        on_reply,
        stream: bool = True,
    ) -> None:
        # R4.4: defaults to True for send_message's Composer-send call site
        # (the only surface this increment's design intends to stream), but
        # is a real, caller-controlled parameter, NOT hardcoded - regenerate_
        # response's own call site below passes stream=False explicitly,
        # since it REPLACES an existing node's content rather than creating
        # a new one, and the design spec's own deferral list explicitly
        # scoped Regenerate Response streaming out of this increment ("a
        # small follow-up once this mechanism is proven"). Hardcoding
        # stream=True here would have silently activated the Composer's live
        # preview UI for every Regenerate click too, with no way for the
        # frontend to distinguish "a send is in flight" from "a regenerate
        # elsewhere in the canvas is in flight" - a real, confusing surprise
        # this parameter exists specifically to prevent.
        return await self._dispatch(
            bus=bus,
            notifications_state=notifications_state,
            conversation_history=conversation_history,
            on_reply=on_reply,
            on_begin=composer_document.begin_request,
            on_end=composer_document.end_request,
            state_topic="app-composer",
            stream=stream,
        )

    async def start_conversation_reply(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        node,
        conversation_history,
        on_reply,
    ) -> None:
        """R4.3's ConversationNode equivalent of start_chat_reply: same
        _dispatch pipeline, but the in-flight request_id lives on the
        ConversationNode itself (`node.pending_request_id`, duck-typed - this
        module does not import canvas.py's SceneNode) rather than on
        ComposerDocument, and "scene" (not "app-composer") is republished
        around that change so the node's own in-flight state refreshes.

        R4.4: deliberately UNCHANGED by the new streaming addition - no
        `stream` kwarg is passed here, so _dispatch's default (False) applies
        and this keeps calling the plain blocking `_call_chat_agent` driver
        exactly as before. Streaming ConversationNode replies is an explicit,
        separate deferral (see the R4.4 design spec)."""
        return await self._dispatch(
            bus=bus,
            notifications_state=notifications_state,
            conversation_history=conversation_history,
            on_reply=on_reply,
            on_begin=lambda request_id: setattr(node, "pending_request_id", request_id),
            on_end=lambda: setattr(node, "pending_request_id", None),
            state_topic="scene",
        )

    async def start_image_reply(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        prompt: str,
        on_reply,  # on_reply(image_bytes: bytes) -> None | Awaitable
    ) -> None:
        """R4.4a: the independent-slot counterpart to _dispatch, NOT a
        variant of it - image generation has no conversation_history/
        persona/on_begin/on_end/state_topic shape (there is no per-node
        "generating" flag to toggle the way ComposerDocument.request_state or
        a ConversationNode's pending_request_id do; the frontend shows a
        transient "Generating image..." notification instead of a per-node
        spinner). Guarded by self._image_requests, a dict kept fully
        SEPARATE from self._requests (chat/conversation) - see that field's
        own comment in __init__ for why this must stay independent rather
        than reusing the existing single-slot guard.

        No cancel_event is constructed or passed - api_provider.generate_image
        has no cancellation_event parameter at all and its body has no
        checkpoint to insert one at (it is one blocking network POST), and
        legacy itself has zero real cancel affordance for image generation
        either (ImageGenerationWorkerThread.stop() exists but is never called
        from any UI path). The WATCHDOG_TIMEOUT_SECONDS ceiling IS still
        applied here even though legacy has none for image generation - a
        deliberate, explicitly-flagged improvement (leaving this as the only
        dispatch surface with no ceiling against a hung external HTTP call
        would be an unforced gap, not considered legacy design), not silent
        parity.
        """
        if self._image_requests:
            # Single in-flight-image-request-per-session guard, mirroring
            # _dispatch's own "A response is already being generated." guard
            # in shape but tracked on the independent self._image_requests
            # dict, never self._requests.
            notifications_state.show("An image is already being generated.", "info")
            await bus.publish("notification")
            return

        request_id = uuid.uuid4().hex

        async def _run():
            try:
                image_bytes = await asyncio.wait_for(
                    asyncio.to_thread(api_provider.generate_image, prompt),
                    timeout=WATCHDOG_TIMEOUT_SECONDS,
                )
                if inspect.iscoroutinefunction(on_reply):
                    await on_reply(image_bytes)
                else:
                    on_reply(image_bytes)
                # Unlike _dispatch, "scene" is NOT published here on success -
                # on_reply itself (canvas.py's _dispatch_image._on_reply)
                # already publishes "scene" after mutating the document, so a
                # second unconditional publish here would be redundant.
            except asyncio.TimeoutError:
                notifications_state.show(
                    "Image generation stopped responding before the request "
                    "completed. Please try again.",
                    "error",
                )
                await bus.publish("notification")
            except Exception as exc:
                # Catches api_provider.generate_image's real gating
                # RuntimeErrors (not API mode / no client / Anthropic
                # unsupported / no model configured / quota exceeded) and any
                # other failure the same way, matching _dispatch's own
                # generic "AI response failed: {exc}" catch-all shape - exc's
                # own text is forwarded verbatim after one shared prefix so
                # api_provider.py's distinct messages stay distinguishable to
                # the user without the WS layer duplicating that gating
                # knowledge.
                logger.exception("image generation dispatch failed")
                notifications_state.show(f"Image generation failed: {exc}", "error")
                await bus.publish("notification")
            finally:
                # Unconditional on every exit path so the slot never leaks -
                # a future request must always be admitted once this one is
                # done, success or failure.
                self._image_requests.pop(request_id, None)

        # NOT awaited here, same load-bearing reason _dispatch's own _run
        # task is not awaited inline - the WS connection's read loop must
        # keep reading further messages on this same socket while a
        # generation is in flight.
        self._image_requests[request_id] = {"task": asyncio.create_task(_run())}

    async def start_web_research(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        node,
        node_id: str,
        query: str,
        branch_history: list,
        on_progress,
        on_success,
        on_failure,
    ) -> None:
        """R5.1: the Web Research independent-slot counterpart to
        start_image_reply above - NOT a variant of _dispatch, since there is
        exactly one caller (backend/canvas.py's run_web_research), so
        on_begin/on_end are inlined here directly rather than taking
        _dispatch's generic parameters. Guarded by self._web_research_requests,
        a dict kept fully SEPARATE from both self._requests (chat/
        conversation) and self._image_requests - see that field's own
        comment in __init__ for why this must stay independent.

        Cooperative cancellation only, via a CancellationToken (not a
        threading.Event, since WebResearchService.run's own pipeline stages
        already accept `token: CancellationToken` - see
        graphlink_plugins/web_research/domain.py) - same honestly-documented
        limitation as existing chat/image dispatch: this does not force-kill
        a call already blocked inside a single blocking call with no
        checkpoint until it returns."""
        if self._web_research_requests:
            notifications_state.show("A web research request is already running.", "info")
            await bus.publish("notification")
            return

        request_id = uuid.uuid4().hex
        cancel_token = CancellationToken()
        request = WebResearchRequest(
            request_id=request_id,
            node_id=node_id,
            chat_epoch=0,
            original_query=query,
            branch_history=list(branch_history),
        )

        async def _invoke(fn, *a):
            if inspect.iscoroutinefunction(fn):
                await fn(*a)
            else:
                fn(*a)

        async def _run():
            node.pending_request_id = request_id
            await bus.publish("scene")
            loop = asyncio.get_running_loop()
            service = WebResearchService()

            async def _guarded_progress(event) -> None:
                # asyncio.to_thread's underlying thread is NOT actually
                # killed by wait_for's timeout (Future.cancel() on an
                # already-running thread is a no-op - see the watchdog
                # comment on WATCHDOG_TIMEOUT_SECONDS above for the chat
                # path's identical limitation), so a slow service.run() can
                # keep calling progress() well after this request's own
                # finally block has already popped _web_research_requests
                # and cleared node.pending_request_id. Re-check liveness here
                # (on the loop thread, so no race with the pop above) and
                # drop the event if this request is no longer the active one
                # - otherwise a stale progress tick can resurrect a
                # since-failed/cancelled node's stage, or clobber a brand
                # new run started on the same node in the meantime.
                if request_id not in self._web_research_requests:
                    return
                await _invoke(on_progress, event)

            def _thread_on_progress(event) -> None:
                # Runs on the WORKER THREAD (inside asyncio.to_thread). Given
                # the low event frequency (<=16 events per run), this
                # deliberately does NOT need the token-streaming pipeline's
                # Queue+_pump batching machinery - a single
                # run_coroutine_threadsafe per event is simpler and still
                # correctly ordered, because service.run() calls progress()
                # synchronously and single-threaded, and each event's
                # coroutine mutates SceneNode fields synchronously before its
                # first await, so asyncio's FIFO call_soon scheduling
                # preserves emission order even if the subsequent
                # bus.publish("scene") awaits interleave.
                asyncio.run_coroutine_threadsafe(_guarded_progress(event), loop)

            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        service.run, request, token=cancel_token, progress=_thread_on_progress
                    ),
                    timeout=WEB_RESEARCH_WATCHDOG_TIMEOUT_SECONDS,
                )
                await _invoke(on_success, result)
                await bus.publish("scene")
            except asyncio.TimeoutError:
                cancel_token.cancel()
                message = (
                    "Web research stopped responding before the request completed. "
                    "Please try again."
                )
                await _invoke(on_failure, ResearchFailure(message, code="watchdog_timeout"))
                notifications_state.show(message, "error")
                await bus.publish("notification")
                await bus.publish("scene")
            except RequestCancelled as exc:
                await _invoke(on_failure, exc)
                notifications_state.show("Web research cancelled.", "info")
                await bus.publish("notification")
                await bus.publish("scene")
            except ResearchFailure as exc:
                await _invoke(on_failure, exc)
                notifications_state.show(f"Web research failed: {exc}", "error")
                await bus.publish("notification")
                await bus.publish("scene")
            except Exception as exc:
                logger.exception("web research dispatch failed")
                await _invoke(on_failure, exc)
                notifications_state.show(f"Web research failed: {exc}", "error")
                await bus.publish("notification")
                await bus.publish("scene")
            finally:
                self._web_research_requests.pop(request_id, None)
                node.pending_request_id = None
                await bus.publish("scene")

        self._web_research_requests[request_id] = {
            "cancel_token": cancel_token, "task": asyncio.create_task(_run())
        }


def _call_chat_agent(conversation_history, persona_text, cancel_event) -> str:
    """Runs inside asyncio.to_thread - a real OS thread, not the event loop."""
    agent = ChatAgent("Graphlink Assistant", persona_text)
    # KNOWN PRE-EXISTING LEGACY QUIRK, ported as-is (not fixed here - see the
    # final report): ChatAgent.__init__ does
    # `self.persona = persona or "(default persona)"`, so when persona_text
    # is "" (system prompt disabled), self.persona becomes the literal
    # "(default persona)" and system_prompt ends up
    # "You are Graphlink Assistant. (default persona)." - not truly
    # empty/suppressed the way disabling the setting is clearly meant to.
    return agent.get_response(
        conversation_history,
        # current_node=None is never dereferenced: ChatWorker.run only walks
        # current_node when resolved_system_prompt is None, and a real value
        # (agent.system_prompt, NOT the raw persona_text - see below) is
        # always supplied here.
        current_node=None,
        cancellation_event=cancel_event,
        # Pass agent.system_prompt (the "You are {name}. {persona}." string
        # ChatAgent always builds), NOT the raw persona_text - getting this
        # backwards would silently drop the "You are Graphlink Assistant. "
        # prefix.
        resolved_system_prompt=agent.system_prompt,
    )


def _call_chat_agent_stream(conversation_history, persona_text, cancel_event, on_chunk) -> str:
    """Runs inside asyncio.to_thread - a real OS thread, not the event loop.
    Streaming counterpart to _call_chat_agent (R4.4) - same persona/
    current_node/resolved_system_prompt quirks and guarantees as that
    function (see its own docstring for the "(default persona)" note, which
    applies identically here since both build the ChatAgent the same way).

    The only difference is the trailing `on_chunk` argument, forwarded
    straight through to ChatAgent.get_response's additive `on_chunk` kwarg
    (see graphlink_app/graphlink_chat_agent.py): when non-None, get_response
    routes the call through api_provider.chat_stream instead of
    api_provider.chat, invoking `on_chunk(delta, reset)` zero or more times
    before returning the same full-text shape `_call_chat_agent` returns.
    `on_chunk` itself is `_dispatch`'s `_thread_on_chunk` closure - a plain
    callable safe to invoke from this worker thread, since it only ever does
    `loop.call_soon_threadsafe(...)` internally rather than touching the
    event loop directly."""
    agent = ChatAgent("Graphlink Assistant", persona_text)
    return agent.get_response(
        conversation_history,
        current_node=None,
        cancellation_event=cancel_event,
        resolved_system_prompt=agent.system_prompt,
        on_chunk=on_chunk,
    )


def register_agents(bus, composer_document, notifications_state, settings_manager) -> AgentDispatcher:
    dispatcher = AgentDispatcher(settings_manager)
    # dispatcher.cancel is synchronous (just sets an Event and returns a
    # bool) - no publish/await needed here; the in-flight _run task's own
    # finally block handles the resulting state transition.
    bus.register_intent("app-composer", "cancelChatRequest", lambda request_id: dispatcher.cancel(request_id))
    return dispatcher
