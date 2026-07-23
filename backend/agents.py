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
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid

import api_provider
import graphlink_task_config as config
from graphlink_chat_agent import ChatAgent
from graphlink_licensing import SettingsManager  # type hint only
from graphlink_prompts import BASE_SYSTEM_PROMPT

from backend.events import SessionBus  # type hint only

logger = logging.getLogger(__name__)

# The single fixed hard timeout for this increment - see the module
# docstring for why there is no intermediate stall-notice tier here.
WATCHDOG_TIMEOUT_SECONDS = 420


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
    ) -> None:
        """The shared real-dispatch pipeline behind both start_chat_reply
        (Composer, state_topic="app-composer") and start_conversation_reply
        (ConversationNode, state_topic="scene", R4.3) - one in-flight-request
        slot per session regardless of which caller occupies it. `on_begin`/
        `on_end` let each caller record the in-flight request_id on its own
        state (ComposerDocument.begin_request/end_request, or a
        ConversationNode's pending_request_id) without this method knowing
        which; `state_topic` is the topic republished around that state
        change so the right part of the UI refreshes."""
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
                reply_text = await asyncio.wait_for(
                    asyncio.to_thread(_call_chat_agent, conversation_history, self.persona(), cancel_event),
                    timeout=WATCHDOG_TIMEOUT_SECONDS,
                )
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
    ) -> None:
        return await self._dispatch(
            bus=bus,
            notifications_state=notifications_state,
            conversation_history=conversation_history,
            on_reply=on_reply,
            on_begin=composer_document.begin_request,
            on_end=composer_document.end_request,
            state_topic="app-composer",
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
        around that change so the node's own in-flight state refreshes."""
        return await self._dispatch(
            bus=bus,
            notifications_state=notifications_state,
            conversation_history=conversation_history,
            on_reply=on_reply,
            on_begin=lambda request_id: setattr(node, "pending_request_id", request_id),
            on_end=lambda: setattr(node, "pending_request_id", None),
            state_topic="scene",
        )


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


def register_agents(bus, composer_document, notifications_state, settings_manager) -> AgentDispatcher:
    dispatcher = AgentDispatcher(settings_manager)
    # dispatcher.cancel is synchronous (just sets an Event and returns a
    # bool) - no publish/await needed here; the in-flight _run task's own
    # finally block handles the resulting state transition.
    bus.register_intent("app-composer", "cancelChatRequest", lambda request_id: dispatcher.cancel(request_id))
    return dispatcher
