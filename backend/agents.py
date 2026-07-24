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
import difflib
import inspect
import logging
import threading
import uuid
from pathlib import Path
from urllib.parse import quote

import api_provider
import graphlink_task_config as config
from graphlink_artifact_agent import ArtifactAgent
from graphlink_chat_agent import ChatAgent
from graphlink_licensing import SettingsManager  # type hint only
from graphlink_plugins.common.github_client import GitHubRestClient
from graphlink_plugins.gitlink.agent import GitlinkAgent, _fingerprint_changes, _is_repo_text_path
from graphlink_plugins.gitlink.repository import (
    GitlinkRepository,
    apply_change_set,
    default_import_root,
    read_local_repo_file,
    validate_pending_changes,
)
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

# R5.3: Gitlink's six timeout constants, each independently reasoned rather
# than reused from an existing constant whose justification doesn't apply
# here.
#
# One LLM completion (same call-count shape as chat/artifact, whose 420s
# already covers that shape) but can carry up to 180,000 chars of input
# context (repository.py's MAX_CONTEXT_CHARS) - an order of magnitude more
# prompt than typical, measurably increasing processing/queueing latency even
# at identical call-count. A deliberate bump over 420s for THIS reason alone -
# NOT web research's 900s reasoning (which exists because that service chains
# ~10 sequential calls inside one outer timeout; Gitlink's run is one call).
GITLINK_WATCHDOG_TIMEOUT_SECONDS = 600
# Local disk I/O only, no network - generous headroom, short enough to fail
# fast.
GITLINK_APPLY_TIMEOUT_SECONDS = 30
# Up to 5 sequential paginated GET /user/repos calls (MAX_REPO_PAGES).
GITLINK_REPO_LIST_TIMEOUT_SECONDS = 150
# One branch-resolve GET (GET /repos/{repo}) + one recursive tree GET.
GITLINK_TREE_TIMEOUT_SECONDS = 60
# One zipball GET (network-timeout-capped at 60s by
# GitlinkRepository.download_repository_snapshot itself) + local
# extract/move.
GITLINK_IMPORT_TIMEOUT_SECONDS = 90
# Bounded by selected-file count when no local_root is set (one GitHub file
# fetch per selected path); local-root-backed builds are pure disk I/O and
# finish well under this.
GITLINK_CONTEXT_TIMEOUT_SECONDS = 300

# R5.3 post-review FIX 4(b): the sentinel value backend/canvas.py's
# run_gitlink_change_set stores into node.pending_request_id SYNCHRONOUSLY,
# in the same stretch as its own busy pre-check, immediately before ever
# calling start_gitlink_run below - this closes the real await-spanning gap
# between that pre-check and start_gitlink_run's own synchronous claim
# (spanning run_gitlink_change_set's own `await publish_scene()`). See
# start_gitlink_run's own docstring and run_gitlink_change_set's own comment
# for the full race this closes. start_gitlink_run recognizes ONLY this
# exact value as "already claimed by my own caller, safe to overwrite" - any
# OTHER truthy pending_request_id is still a genuine busy node and is
# rejected exactly as before.
_GITLINK_RUN_CLAIM_PLACEHOLDER = "pending"


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
        # R5.2: a FOURTH independent in-flight-request slot, separate from
        # self._requests (chat/conversation), self._image_requests, and
        # self._web_research_requests - an artifact-generation request must be
        # able to run concurrently with any of those three, same reasoning as
        # every prior independent slot above. request_id -> {"cancel_event":
        # threading.Event, "task": asyncio.Task}.
        self._artifact_requests: dict[str, dict] = {}
        # R5.3: a FIFTH independent in-flight-request slot, separate from
        # self._requests/self._image_requests/self._web_research_requests/
        # self._artifact_requests - a Gitlink Generate Change Set run must be
        # able to run concurrently with any of those four, same reasoning as
        # every prior independent slot above. request_id -> {"cancel_event":
        # threading.Event, "task": asyncio.Task} - Run is cancellable,
        # mirrors self._web_research_requests' exact shape.
        self._gitlink_requests: dict[str, dict] = {}
        # R5.3: a SIXTH independent in-flight-request slot - Gitlink's Apply
        # (the disk-write step) must be able to run concurrently with a
        # Gitlink Run on a DIFFERENT node, or with any other kind's dispatch.
        # request_id -> {"task": asyncio.Task} - NO "cancel_event" key here,
        # matching self._image_requests' shape: legacy has zero cancel
        # affordance for the disk-write step either. (Same-node concurrent
        # Run+Apply is additionally blocked by node.pending_request_id - see
        # register_canvas's own busy checks in backend/canvas.py - this dict
        # split is about cross-request bookkeeping, not the same-node guard.)
        self._gitlink_apply_requests: dict[str, dict] = {}

    def cancel_gitlink(self, request_id: str) -> bool:
        entry = self._gitlink_requests.get(request_id)
        if entry is None:
            return False
        entry["cancel_event"].set()
        return True

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

    def cancel_artifact(self, request_id: str) -> bool:
        entry = self._artifact_requests.get(request_id)
        if entry is None:
            return False
        entry["cancel_event"].set()
        return True

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

    async def start_artifact_reply(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        node,
        current_artifact: str,
        history: list,
        on_reply,
    ) -> None:
        """R5.2: the Artifact/Drafter independent-slot counterpart to
        start_image_reply/start_web_research above - NOT a variant of
        _dispatch, since _dispatch is hardcoded to a single-string on_reply
        contract and a fixed driver function, while _call_artifact_agent
        returns a two-element tuple and must run its own fail-closed
        tag-parsing/raise (see ArtifactAgent.get_response) before any
        mutation callback fires. Guarded by self._artifact_requests, a dict
        kept fully SEPARATE from self._requests (chat/conversation),
        self._image_requests, and self._web_research_requests - see that
        field's own comment in __init__ for why this must stay independent.

        Cooperative cancellation only, via a threading.Event (not the
        CancellationToken web-research uses - ArtifactAgent has no such
        primitive) - same honestly-documented limitation as every other
        dispatch surface: ArtifactAgent.get_response has no cancellation
        checkpoint of its own. The checkpoint is deliberately placed AFTER
        the blocking call returns: if cancel_event is set by then, on_reply
        is simply never called, so the document is left untouched.

        Reuses WATCHDOG_TIMEOUT_SECONDS (420s), not a new constant:
        ArtifactAgent.get_response makes exactly ONE blocking
        api_provider.chat() call (see _call_artifact_agent below), the same
        call-count as chat's own _call_chat_agent - Web Research's own 900s
        bump exists specifically because WebResearchService.run chains ~10
        sequential calls inside one outer timeout, which does not apply
        here."""
        if self._artifact_requests:
            notifications_state.show("An artifact request is already running.", "info")
            await bus.publish("notification")
            return

        request_id = uuid.uuid4().hex
        cancel_event = threading.Event()

        async def _run():
            node.pending_request_id = request_id
            await bus.publish("scene")
            try:
                new_content, ai_message = await asyncio.wait_for(
                    asyncio.to_thread(_call_artifact_agent, current_artifact, history),
                    timeout=WATCHDOG_TIMEOUT_SECONDS,
                )
                if cancel_event.is_set():
                    notifications_state.show("Artifact generation cancelled.", "info")
                    await bus.publish("notification")
                else:
                    if inspect.iscoroutinefunction(on_reply):
                        await on_reply(new_content, ai_message)
                    else:
                        on_reply(new_content, ai_message)
                    await bus.publish("scene")
            except asyncio.TimeoutError:
                cancel_event.set()
                notifications_state.show(
                    "Artifact generation stopped responding before the request completed. "
                    "Please try again.",
                    "error",
                )
                await bus.publish("notification")
            except Exception as exc:
                logger.exception("artifact dispatch failed")
                notifications_state.show(f"Artifact generation failed: {exc}", "error")
                await bus.publish("notification")
            finally:
                self._artifact_requests.pop(request_id, None)
                node.pending_request_id = None
                await bus.publish("scene")

        self._artifact_requests[request_id] = {
            "cancel_event": cancel_event, "task": asyncio.create_task(_run())
        }

    # -- R5.3: Gitlink ------------------------------------------------------
    #
    # Four PLAIN async methods below (fetch_gitlink_repositories/
    # load_gitlink_repo_tree/import_gitlink_snapshot/build_gitlink_context) -
    # NO dict-tracking: the caller (backend/canvas.py's register_canvas) already
    # guards busy-state via node.pending_request_id directly before calling,
    # and each of these is awaited DIRECTLY by that caller (not scheduled via
    # asyncio.create_task the way start_chat_reply/start_web_research/
    # start_artifact_reply/start_gitlink_run/start_gitlink_apply are) - there
    # is no natural intermediate UI state beyond "loading" for a one-shot
    # listing/import/context-build action, and the caller needs the result
    # back in the same round trip. node.pending_request_id is still the busy
    # marker for the duration (see AgentDispatcher.__init__'s own comment on
    # why every Gitlink action - including these four - shares that one
    # field); it is set/cleared inline here rather than via a background task.

    async def fetch_gitlink_repositories(self, *, bus: SessionBus, notifications_state, node) -> list[str]:
        request_id = uuid.uuid4().hex
        node.pending_request_id = request_id
        await bus.publish("scene")
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_list_github_repositories, self._settings_manager),
                timeout=GITLINK_REPO_LIST_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            notifications_state.show(
                "Loading GitHub repositories stopped responding before the request completed. "
                "Please try again.",
                "error",
            )
            await bus.publish("notification")
            return []
        except Exception as exc:
            logger.exception("gitlink repository listing failed")
            notifications_state.show(f"Failed to load GitHub repositories: {exc}", "error")
            await bus.publish("notification")
            return []
        finally:
            node.pending_request_id = None
            await bus.publish("scene")

    async def load_gitlink_repo_tree(self, *, bus: SessionBus, notifications_state, node, repo: str, branch: str):
        request_id = uuid.uuid4().hex
        node.pending_request_id = request_id
        await bus.publish("scene")
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_load_gitlink_tree, self._settings_manager, repo, branch),
                timeout=GITLINK_TREE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            notifications_state.show(
                "Loading the repository file tree stopped responding before the request "
                "completed. Please try again.",
                "error",
            )
            await bus.publish("notification")
            return None
        except Exception as exc:
            logger.exception("gitlink repo tree load failed")
            notifications_state.show(f"Failed to load the repository file tree: {exc}", "error")
            await bus.publish("notification")
            return None
        finally:
            node.pending_request_id = None
            await bus.publish("scene")

    async def import_gitlink_snapshot(
        self, *, bus: SessionBus, notifications_state, node, repo: str, branch: str,
        local_root_hint: str, imported_root_hint: str,
    ):
        request_id = uuid.uuid4().hex
        node.pending_request_id = request_id
        await bus.publish("scene")
        try:
            resolved_repo, resolved_branch, local_root_path = await asyncio.wait_for(
                asyncio.to_thread(
                    _ensure_gitlink_snapshot, self._settings_manager, repo, branch,
                    local_root_hint, imported_root_hint,
                ),
                timeout=GITLINK_IMPORT_TIMEOUT_SECONDS,
            )
            return resolved_repo, resolved_branch, str(local_root_path)
        except asyncio.TimeoutError:
            notifications_state.show(
                "Importing the repository snapshot stopped responding before the request "
                "completed. Please try again.",
                "error",
            )
            await bus.publish("notification")
            return None
        except Exception as exc:
            logger.exception("gitlink snapshot import failed")
            notifications_state.show(f"Failed to import the repository snapshot: {exc}", "error")
            await bus.publish("notification")
            return None
        finally:
            node.pending_request_id = None
            await bus.publish("scene")

    async def build_gitlink_context(
        self, *, bus: SessionBus, notifications_state, node, scope_mode: str, selected_paths: list[str],
    ):
        request_id = uuid.uuid4().hex
        node.pending_request_id = request_id
        await bus.publish("scene")
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    _build_gitlink_context_bundle,
                    self._settings_manager,
                    repo=node.gitlink_repo,
                    branch=node.gitlink_branch,
                    scope_mode=scope_mode,
                    selected_paths=selected_paths,
                    repo_file_paths=list(node.gitlink_repo_file_paths),
                    local_root_hint=node.gitlink_local_root,
                    imported_root_hint=node.gitlink_imported_root,
                ),
                timeout=GITLINK_CONTEXT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            notifications_state.show(
                "Building the repository context stopped responding before the request "
                "completed. Please try again.",
                "error",
            )
            await bus.publish("notification")
            return None
        except Exception as exc:
            logger.exception("gitlink context build failed")
            notifications_state.show(f"Failed to build the repository context: {exc}", "error")
            await bus.publish("notification")
            return None
        finally:
            node.pending_request_id = None
            await bus.publish("scene")

    async def start_gitlink_run(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        node,
        node_id: str,
        repo: str,
        branch: str,
        scope_mode: str,
        task_prompt: str,
        context_xml: str,
        context_summary: str,
        local_root: str,
        on_success,
        on_failure,
    ) -> None:
        """R5.3: Gitlink's Generate Change Set action - the independent
        Gitlink Run slot, mirroring start_web_research/start_artifact_reply's
        own fire-and-forget shape: the caller (register_canvas's
        run_gitlink_change_set) returns immediately after this schedules its
        background task; the eventual result lands via on_success/on_failure
        plus a "scene" republish, same as every other kind's real dispatch.

        Cooperative cancellation only, via a threading.Event
        (GitlinkAgent.get_response has no cancellation primitive of its own)
        - same honestly-documented limitation as every other dispatch
        surface: the checkpoint is placed AFTER the blocking call returns, so
        a cancel requested while the model call is already in flight discards
        the result rather than truly interrupting the underlying network
        call.

        The fingerprint is computed over the EXACT change set about to be
        shown - mirrors legacy's own shown_fingerprint, computed immediately
        before display, never a value captured earlier or later.

        DEFENSE-IN-DEPTH busy guard, checked here too (not only by
        register_canvas's own run_gitlink_change_set pre-check): node.
        pending_request_id is the shared busy marker for EVERY Gitlink
        action on this node, and the whole point of that field is making the
        Run-cannot-start-while-an-Apply-is-in-flight (and vice versa)
        guarantee hold regardless of call site. Checking it again here means
        a future caller that skips the canvas.py pre-check can never
        accidentally start a second concurrent Gitlink action on the same
        node. The ONE exception is _GITLINK_RUN_CLAIM_PLACEHOLDER (see that
        constant's own comment): run_gitlink_change_set stores that exact
        sentinel into node.pending_request_id, synchronously, immediately
        before calling this method - this method recognizes it as "already
        claimed by my own caller" and overwrites it, rather than rejecting a
        request its own caller just admitted.

        R5.3 post-review FIX 4(a): node.pending_request_id is now claimed
        SYNCHRONOUSLY here, immediately after the busy check and BEFORE
        asyncio.create_task(_run()) below - mirroring start_gitlink_apply's
        own claim exactly. Before this fix, the slot stayed empty until
        _run() actually got a turn on the event loop, leaving a real gap
        between "Run was requested" and "Run's sub-task actually started"
        during which a second concurrent Run or an Apply for the same node
        could slip past the busy check above."""
        if node.pending_request_id and node.pending_request_id != _GITLINK_RUN_CLAIM_PLACEHOLDER:
            notifications_state.show("Gitlink is already busy for this node.", "info")
            await bus.publish("notification")
            return

        request_id = uuid.uuid4().hex
        node.pending_request_id = request_id
        cancel_event = threading.Event()
        await bus.publish("scene")

        async def _run():
            try:
                payload = {
                    "task_prompt": task_prompt,
                    "context_xml": context_xml,
                    "repo": repo,
                    "branch": branch,
                    "scope_label": "Full Repo Access" if scope_mode == "full" else "Selected Files",
                    "context_summary": context_summary,
                    "branch_transcript": "",
                }
                result = await asyncio.wait_for(
                    asyncio.to_thread(_call_gitlink_agent, payload),
                    timeout=GITLINK_WATCHDOG_TIMEOUT_SECONDS,
                )
                if cancel_event.is_set():
                    notifications_state.show("Gitlink generation cancelled.", "info")
                    await bus.publish("notification")
                else:
                    proposal_markdown = _build_gitlink_proposal_markdown(repo, branch, result)
                    preview_text = _build_gitlink_preview_text(result["files"], local_root, repo, branch)
                    fingerprint = _fingerprint_changes(result["files"]) if result["files"] else None
                    # R5.3 post-review FIX 2: local_root is now forwarded to
                    # on_success too, so document.complete_gitlink_run can
                    # record exactly which local_root THIS run used (see that
                    # method's own docstring) - the write-destination binding
                    # start_gitlink_apply's fourth check enforces.
                    on_success(proposal_markdown, result["files"], preview_text, fingerprint, local_root)
                    await bus.publish("scene")
            except asyncio.TimeoutError:
                cancel_event.set()
                notifications_state.show(
                    "Gitlink generation stopped responding before the request completed. "
                    "Please try again.",
                    "error",
                )
                await bus.publish("notification")
            except Exception as exc:
                logger.exception("gitlink dispatch failed")
                on_failure(f"Gitlink generation failed: {exc}")
                notifications_state.show(f"Gitlink generation failed: {exc}", "error")
                await bus.publish("notification")
            finally:
                self._gitlink_requests.pop(request_id, None)
                # R5.3 post-review FIX 4(c): only clear if this task's OWN
                # request_id is still the one recorded - a stale,
                # already-superseded task finishing late must never clobber
                # a newer legitimate busy marker.
                if node.pending_request_id == request_id:
                    node.pending_request_id = None
                await bus.publish("scene")

        self._gitlink_requests[request_id] = {
            "cancel_event": cancel_event, "task": asyncio.create_task(_run())
        }

    async def start_gitlink_apply(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        node,
        node_id: str,
        client_fingerprint: str,
        local_root: str,
        on_success,
        on_failure,
    ) -> None:
        """R5.3: Gitlink's Apply action - THE code the whole increment hinges
        on. The fingerprint check and the freeze of the data that will
        actually be written happen in the SAME synchronous stretch of this
        coroutine, with ZERO await between them. Python asyncio is
        single-threaded; only an await yields control - so it is IMPOSSIBLE
        (not merely unlikely) for node.gitlink_pending_changes to be mutated
        between the recompute and the freeze immediately after it. This is a
        STRONGER guarantee than legacy's own check, because legacy's
        confirmation dialog is a real blocking call that pumps the Qt event
        loop (letting a background thread's finished signal run mid-dialog) -
        this coroutine has no equivalent yield point until deliberately
        introduced AFTER the freeze.

        R5.3 post-review FIX 5: node.pending_request_id is now claimed
        SYNCHRONOUSLY here, immediately after the busy check above and
        BEFORE the local_root_text validation - mirroring start_gitlink_run's
        own early synchronous claim (see that method's own docstring). Before
        this fix, the busy slot stayed unclaimed all the way through the
        local_root_text validation, the `await asyncio.to_thread(local_root_
        path.exists)` call below (a real yield point), and the entire atomic
        fingerprint/local_root section, only ever being set at the very end,
        just before scheduling _run(). Two genuinely concurrent Apply calls
        for the SAME node (two different WebSocket connections on the same
        session, e.g. two browser tabs - not a single connection's
        sequential message loop) could both read node.pending_request_id as
        falsy before either claimed it, both proceed through the exists()
        await and the atomic section, and both end up scheduling a write via
        apply_change_set concurrently - a real write-safety issue, since two
        concurrent writers touching the same files' backup/rollback
        bookkeeping is not something apply_change_set was designed to
        tolerate. Every early-return failure path BELOW this claim (empty
        pending_changes, empty local_root, nonexistent local_root,
        fingerprint mismatch, local_root mismatch) now ALSO clears
        node.pending_request_id back to None before returning, since none of
        those paths ever reach _run()'s own finally block - without that
        clear, a legitimately-rejected Apply would leave the node
        permanently stuck "busy"."""
        if node.pending_request_id:
            notifications_state.show("Gitlink is already busy for this node.", "info")
            await bus.publish("notification")
            return

        request_id = uuid.uuid4().hex
        node.pending_request_id = request_id

        if not node.gitlink_pending_changes:
            node.pending_request_id = None
            on_failure("There is no approved change set to write.")
            await bus.publish("scene")
            return

        local_root_text = (local_root or "").strip()
        if not local_root_text:
            node.pending_request_id = None
            on_failure("Select or import a local repository path before applying changes.")
            await bus.publish("scene")
            return
        local_root_path = Path(local_root_text).expanduser()
        # R5.3 post-review FIX 3: wrapped in asyncio.to_thread, like every
        # other filesystem check in this file - this was the sole exception,
        # running synchronously directly on the shared event loop. Placed
        # BEFORE the atomic check-and-freeze section below, so this await
        # does not touch that section's own zero-await guarantee (which only
        # covers the fingerprint-check-through-snapshot-freeze part). R5.3
        # post-review FIX 5: this await is now the reason the busy claim
        # above had to move earlier - a second concurrent call could
        # otherwise slip past the busy check while this await has yielded
        # control.
        local_root_exists = await asyncio.to_thread(local_root_path.exists)
        if not local_root_exists:
            node.pending_request_id = None
            on_failure("The selected local repository path does not exist.")
            await bus.publish("scene")
            return

        # --- Atomic check-and-freeze: NO await between these statements. ---
        current_fingerprint = _fingerprint_changes(node.gitlink_pending_changes)
        if (
            client_fingerprint != current_fingerprint
            or current_fingerprint != node.gitlink_change_fingerprint
        ):
            node.pending_request_id = None
            on_failure("The proposed change set changed after approval. Review it again before applying.")
            await bus.publish("scene")
            return
        # R5.3 post-review FIX 2: the fingerprint above says nothing about
        # WHERE the content is written - _fingerprint_changes only hashes
        # file content/paths/operations, never local_root (deliberately not
        # modified here - it is reused verbatim from gitlink/agent.py, shared
        # with the legacy Qt app). Without this separate check, a
        # gitlink_local_root edited after Run but before Apply would let
        # previously-reviewed content be written into a directory that was
        # never diffed or shown to the user. Compared as raw trimmed text,
        # consistent with how local_root_text itself is derived just above
        # and how document.complete_gitlink_run records
        # gitlink_change_local_root.
        if local_root_text != (node.gitlink_change_local_root or ""):
            node.pending_request_id = None
            on_failure(
                "The local repository path changed since this proposal was generated. "
                "Regenerate the change set before applying."
            )
            await bus.publish("scene")
            return
        changes_snapshot = [dict(item) for item in node.gitlink_pending_changes]
        # --- End atomic section. Everything past this point operates ONLY on
        # changes_snapshot, never on node.gitlink_pending_changes again. ---

        # R5.3 post-review FIX 5: request_id was already generated and
        # claimed into node.pending_request_id right after the busy check
        # above - NOT re-generated here. Only the change_state transition and
        # publish happen at this point now.
        node.gitlink_change_state = "applying"
        await bus.publish("scene")

        async def _run():
            try:
                written_files = await asyncio.wait_for(
                    asyncio.to_thread(_call_gitlink_apply, local_root_path, changes_snapshot),
                    timeout=GITLINK_APPLY_TIMEOUT_SECONDS,
                )
                on_success(written_files)
                notifications_state.show(f"Applied {written_files} file changes.", "info")
                await bus.publish("notification")
            except asyncio.TimeoutError:
                on_failure(
                    "Applying changes stopped responding before the request completed. "
                    "Some files may have been partially written - check the repository "
                    "before retrying."
                )
                notifications_state.show("Gitlink apply timed out.", "error")
                await bus.publish("notification")
            except Exception as exc:
                logger.exception("gitlink apply failed")
                on_failure(f"Failed to write approved changes: {exc}")
                notifications_state.show(f"Gitlink apply failed: {exc}", "error")
                await bus.publish("notification")
            finally:
                self._gitlink_apply_requests.pop(request_id, None)
                # R5.3 post-review FIX 4(c): only clear if this task's OWN
                # request_id is still the one recorded - same stale-task
                # guard as start_gitlink_run's own finally block above.
                if node.pending_request_id == request_id:
                    node.pending_request_id = None
                await bus.publish("scene")

        self._gitlink_apply_requests[request_id] = {"task": asyncio.create_task(_run())}


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


def _call_artifact_agent(current_artifact, history):
    """Runs inside asyncio.to_thread - a real OS thread, not the event loop.
    Reuses ArtifactAgent.get_response verbatim - same regex/raise
    artifact-tag contract, completely unmodified. Returns
    (new_document, ai_message); the tag-parsing RuntimeError, when raised,
    propagates straight out of this call and is caught by
    start_artifact_reply's own `except Exception` below - the document is
    never touched in that case since mutation only happens in the success
    branch."""
    return ArtifactAgent().get_response(current_artifact, history)


# -- R5.3: Gitlink - blocking helpers, each runs inside asyncio.to_thread ----
#
# These replicate the exact GitHub REST call shapes graphlink_plugin_gitlink.py's
# legacy GitlinkNode uses (load_github_repositories/load_repository_tree/
# _resolve_repo_and_branch/_ensure_repository_snapshot/build_context_bundle),
# confirmed by reading that file directly, as new plain functions here using
# GitHubRestClient.request() directly - repo-listing and tree-loading were
# never extracted into the Qt-free gitlink package, so there is no existing
# Qt-free surface to import for them.

# Up to 5 sequential pages of GET /user/repos, matching legacy's own
# MAX_REPO_PAGES constant.
_GITLINK_MAX_REPO_PAGES = 5


def _list_github_repositories(settings_manager):
    """Replicates load_github_repositories exactly: GET /user/repos with
    per_page=100, sort=updated, visibility=all,
    affiliation=owner,collaborator,organization_member, looped while
    page <= 5, collecting each page's item full_name, stopping early on a
    short/empty page. Returns the sorted, deduplicated list of repo
    full_names."""
    client = GitHubRestClient(settings_manager)
    repos: list[str] = []
    page = 1
    while page <= _GITLINK_MAX_REPO_PAGES:
        page_payload = client.request(
            "https://api.github.com/user/repos",
            params={
                "per_page": 100,
                "page": page,
                "sort": "updated",
                "visibility": "all",
                "affiliation": "owner,collaborator,organization_member",
            },
        )
        if not page_payload:
            break
        repos.extend(item.get("full_name", "") for item in page_payload if item.get("full_name"))
        if len(page_payload) < 100:
            break
        page += 1
    return sorted(set(repos), key=str.lower)


def _resolve_gitlink_branch(client, repo_name, branch_hint):
    """Replicates the branch-resolution half of _resolve_repo_and_branch: an
    explicit branch_hint wins outright; otherwise GET /repos/{repo_name} and
    read default_branch."""
    branch_name = (branch_hint or "").strip()
    if branch_name:
        return branch_name
    repo_payload = client.request(f"https://api.github.com/repos/{repo_name}")
    default_branch = repo_payload.get("default_branch", "")
    if not default_branch:
        raise RuntimeError("GitHub did not provide a default branch for this repository.")
    return default_branch


def _load_gitlink_tree(settings_manager, repo, branch):
    """Replicates load_repository_tree exactly: resolve repo/branch, GET the
    recursive git tree, keep only blob entries whose path passes
    _is_repo_text_path. Returns (repo, resolved_branch, sorted_file_paths)."""
    if not repo or "/" not in repo:
        raise RuntimeError("Enter a repository as `owner/repo`.")
    client = GitHubRestClient(settings_manager)
    resolved_branch = _resolve_gitlink_branch(client, repo, branch)
    tree_payload = client.request(
        f"https://api.github.com/repos/{repo}/git/trees/{quote(resolved_branch, safe='')}",
        params={"recursive": 1},
    )
    tree_items = tree_payload.get("tree", [])
    file_paths = sorted(
        (
            item.get("path", "")
            for item in tree_items
            if item.get("type") == "blob" and item.get("path") and _is_repo_text_path(item.get("path", ""))
        ),
        key=str.lower,
    )
    return repo, resolved_branch, file_paths


def _ensure_gitlink_snapshot(settings_manager, repo, branch, local_root_hint, imported_root_hint):
    """Replicates _ensure_repository_snapshot exactly: an existing
    local_root_hint wins outright (error if it does not exist); else an
    existing imported_root_hint is reused if it still exists; else a fresh
    snapshot is downloaded to default_import_root(repo, branch) (itself
    short-circuiting if that target already exists non-empty). Returns
    (repo, resolved_branch, local_root_path). Shared by both
    import_gitlink_snapshot and build_gitlink_context's own full-scope path -
    factored out once here rather than duplicated, per the design spec."""
    client = GitHubRestClient(settings_manager)
    resolved_branch = _resolve_gitlink_branch(client, repo, branch)

    local_root_text = (local_root_hint or "").strip()
    if local_root_text:
        root_path = Path(local_root_text).expanduser()
        if root_path.exists():
            return repo, resolved_branch, root_path
        raise RuntimeError("The selected local repo path does not exist.")

    imported_root_text = (imported_root_hint or "").strip()
    if imported_root_text:
        imported_path = Path(imported_root_text)
        if imported_path.exists():
            return repo, resolved_branch, imported_path

    target_root = default_import_root(repo, resolved_branch)
    repository = GitlinkRepository(client)
    target_path = repository.download_repository_snapshot(repo, resolved_branch, target_root)
    return repo, resolved_branch, target_path


def _build_gitlink_context_bundle(
    settings_manager, *, repo, branch, scope_mode, selected_paths, repo_file_paths,
    local_root_hint, imported_root_hint,
):
    """Replicates the build_context_bundle wrapper: resolve local_root from
    local_root_hint (None if blank; error if set but does not exist); if
    scope_mode is "full" and local_root is still None, ensure a snapshot
    first (reusing _ensure_gitlink_snapshot rather than duplicating it); then
    delegate to GitlinkRepository.build_context_bundle. Returns a dict with
    context_xml/context_stats/context_summary keys, matching
    store_gitlink_context(node_id, scope_mode=..., selected_paths=...,
    **result)'s call shape in backend/canvas.py.

    DEVIATION from a strict line-for-line legacy replication, noted
    explicitly: legacy's own build_context_bundle wrapper unconditionally
    calls _resolve_repo_and_branch() at its very top (one GET
    /repos/{repo_name} every time, even for a purely local-root/selected-
    files build). This function only resolves the branch via GitHub when a
    snapshot actually needs to be ensured (scope_mode == "full" and no
    local_root) - matching the design spec's own literal parameter passing
    (`branch_name=node.gitlink_branch`) rather than legacy's more eager
    resolution. A local-root-backed build with a blank branch therefore
    proceeds using an empty branch string (harmless: build_context_bundle
    only reads files from local_root in that case, never from GitHub, and
    branch only ends up in cosmetic XML attributes)."""
    local_root_text = (local_root_hint or "").strip()
    local_root = None
    if local_root_text:
        local_root = Path(local_root_text).expanduser()
        if not local_root.exists():
            raise RuntimeError("The selected local repo path does not exist.")

    resolved_branch = branch
    if scope_mode == "full" and local_root is None:
        _, resolved_branch, local_root = _ensure_gitlink_snapshot(
            settings_manager, repo, branch, local_root_hint, imported_root_hint
        )

    client = GitHubRestClient(settings_manager)
    repository = GitlinkRepository(client)
    result = repository.build_context_bundle(
        repo_name=repo,
        branch_name=resolved_branch,
        scope_mode=scope_mode,
        selected_paths=selected_paths,
        repo_file_paths=repo_file_paths,
        local_root=local_root,
    )
    return {
        "context_xml": result.context_xml,
        "context_stats": dict(result.context_stats),
        "context_summary": result.context_summary,
    }


def _call_gitlink_agent(payload):
    """Runs inside asyncio.to_thread. Reuses GitlinkAgent.get_response
    verbatim - same defensive-by-construction dict-in/dict-out contract,
    completely unmodified."""
    return GitlinkAgent().get_response(payload)


def _build_gitlink_proposal_markdown(repo, branch, result):
    """Replicates _build_proposal_markdown exactly, as a plain function
    operating on GitlinkAgent.get_response's own result dict instead of a
    widget's repo_state."""
    summary = result.get("summary") or "No summary returned."
    rationale = result.get("rationale") or "No rationale returned."
    notes = result.get("notes") or []
    write_intent = result.get("write_intent", "blocked")
    files = result.get("files") or []

    lines = [
        "## Gitlink Proposal",
        "",
        f"- Repository: {repo or 'Unknown repo'}",
        f"- Branch: {branch or 'Unknown branch'}",
        f"- Intent: {str(write_intent).replace('_', ' ').title()}",
        f"- Files Returned: {len(files)}",
        "",
        "### Summary",
        summary,
        "",
        "### Rationale",
        rationale,
    ]

    if notes:
        lines.extend(["", "### Notes"])
        lines.extend(f"- {note}" for note in notes)

    if files:
        lines.extend(["", "### Proposed File Writes"])
        for file_item in files:
            lines.append(
                f"- `{file_item.get('path', '')}` [{file_item.get('operation', 'update')}] - "
                f"{file_item.get('reason', 'No reason supplied.')}"
            )

    return "\n".join(lines)


def _build_gitlink_preview_text(files, local_root, repo, branch):
    """Replicates _build_preview_text's diff-building shape, reusing
    read_local_repo_file for the original-content side of each update/delete
    diff. DEVIATION from legacy, noted explicitly: legacy's own
    _read_original_text_for_preview falls back to a live GitHub fetch when no
    local_root is configured; this function does NOT - it degrades
    gracefully (shows the proposed content with an explicit warning banner
    instead of a diff) rather than spending a GitHub API call per changed
    file purely for a preview render. `repo`/`branch` are used only in that
    warning's text, never for a network fetch."""
    preview_parts = []
    for file_item in files:
        path_text = file_item.get("path", "")
        operation = file_item.get("operation", "update")
        original_text = None
        if local_root:
            try:
                original_text = read_local_repo_file(local_root, path_text)
            except Exception:
                original_text = None
        proposed_text = file_item.get("content", "") if operation in {"update", "create"} else ""

        # None = the original could not be read (as opposed to "" = a real
        # empty file). For update/delete that means no honest diff exists -
        # say so explicitly instead of diffing against "" and rendering a
        # misleading all-additions "create" diff. Creates never need the
        # original, so they render normally either way. Mirrors the A2 fix
        # already shipped for the legacy widget's own preview builder.
        if original_text is None and operation in {"update", "delete"}:
            preview_parts.append(f"### {path_text} [{operation}]\n")
            preview_parts.append(
                "!! WARNING: the original file could not be read (no local checkout is "
                f"configured for {repo or 'this repository'}@{branch or 'unknown branch'}), "
                "so no diff can be shown for this change."
            )
            if operation == "update":
                preview_parts.append(
                    "!! Applying will OVERWRITE the existing file with the full "
                    "proposed content below:\n"
                )
                preview_parts.append(proposed_text if proposed_text else "[No content in proposal]")
            else:
                preview_parts.append("!! Applying will DELETE the file.")
            preview_parts.append("")
            continue
        original_text = original_text or ""

        if operation == "create":
            diff_lines = list(
                difflib.unified_diff(
                    [], proposed_text.splitlines(), fromfile=f"a/{path_text}", tofile=f"b/{path_text}", lineterm="",
                )
            )
        elif operation == "delete":
            diff_lines = list(
                difflib.unified_diff(
                    original_text.splitlines(), [], fromfile=f"a/{path_text}", tofile=f"b/{path_text}", lineterm="",
                )
            )
        else:
            diff_lines = list(
                difflib.unified_diff(
                    original_text.splitlines(), proposed_text.splitlines(),
                    fromfile=f"a/{path_text}", tofile=f"b/{path_text}", lineterm="",
                )
            )

        preview_parts.append(f"### {path_text} [{operation}]\n")
        if diff_lines:
            preview_parts.append("\n".join(diff_lines))
        else:
            preview_parts.append("No textual diff available.")
        preview_parts.append("")

    return "\n".join(preview_parts).strip()


def _call_gitlink_apply(local_root, pending_changes):
    """Runs inside asyncio.to_thread. Reuses validate_pending_changes/
    apply_change_set UNMODIFIED, verbatim - the path-safety boundary is never
    reimplemented, only invoked."""
    validate_pending_changes(pending_changes)
    return apply_change_set(local_root, pending_changes)


def register_agents(bus, composer_document, notifications_state, settings_manager) -> AgentDispatcher:
    dispatcher = AgentDispatcher(settings_manager)
    # dispatcher.cancel is synchronous (just sets an Event and returns a
    # bool) - no publish/await needed here; the in-flight _run task's own
    # finally block handles the resulting state transition.
    bus.register_intent("app-composer", "cancelChatRequest", lambda request_id: dispatcher.cancel(request_id))
    return dispatcher
