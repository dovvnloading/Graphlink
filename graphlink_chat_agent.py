"""Qt-free chat-agent core (Qt-removal plan R4.2 prerequisite).

`ChatWorker`/`ChatAgent` moved out of graphlink_agents_core.py: that
module's unconditional `from PySide6.QtCore import QThread, Signal,
QPointF` (needed only by its `*WorkerThread` classes) meant importing
anything from it - including these Qt-free symbols - pulled PySide6 into
the process. That made them unimportable from backend/ despite containing
zero Qt code themselves, exactly the R4.1 problem R4.1 itself didn't reach
(it only split graphlink_config.py).

This file must stay Qt-free forever - it exists to be importable from
backend/, which test_no_qt_anywhere.py holds to zero tolerance.

ADR-002 stage 2.1: this module used to also define
`resolve_branch_system_prompt`, a Qt-era fallback (walked live
QGraphicsScene objects - parent_node/scene()/system_prompt_connections)
for when ChatWorker.run() was called without a pre-resolved system prompt.
Deleted as confirmed-dead: every real caller (backend/agents.py's
_call_chat_agent and _call_chat_agent_stream, both via ChatAgent.__init__'s
self.system_prompt, which is never empty/None) always passes
resolved_system_prompt, so the fallback branch was unreachable in the
current backend and would have crashed immediately if it were ever
reached (it dereferences QGraphicsScene APIs that do not exist on a
backend SceneNode). The real, live equivalent is
AgentDispatcher._resolve_branch_system_prompt in backend/agents.py - an
independent reimplementation against SceneDocument, not this function.
"""

import json
import logging
from collections import OrderedDict

import graphlink_task_config as config
import api_provider
from graphlink_prompts import CONTEXT_SUMMARY_SYSTEM_PROMPT
from graphlink_token_estimator import TokenEstimator
from graphlink_memory import clone_history, history_to_transcript, trim_history

logger = logging.getLogger(__name__)


# ADR-006 stage 6.8 review fix (summary re-run + toast spam): dropped-turn
# summaries are cached (LRU, small and bounded) keyed by the exact dropped
# (role, content) tuple. Every turn after the first drop re-drops a superset
# of the same prefix, so without this cache the summarizer re-ran - and the
# "turns were summarized" toast re-fired - on EVERY message forever. On a
# miss with a cached PREFIX of the dropped tuple, only (that prefix's
# summary + the remainder) is summarized - bounded incremental work per
# turn instead of everything from scratch.
_SUMMARY_CACHE_MAX_ENTRIES = 32
_SUMMARY_CACHE: "OrderedDict[tuple, str]" = OrderedDict()


def _summary_cache_key(messages) -> tuple:
    return tuple((str(m.get("role")), str(m.get("content"))) for m in messages)


def _longest_cached_prefix(key: tuple) -> tuple:
    """(prefix_length, summary) of the longest cached STRICT prefix of
    `key`, or (0, None) when nothing cached applies."""
    best_len, best_summary = 0, None
    for cached_key, cached_summary in _SUMMARY_CACHE.items():
        n = len(cached_key)
        if 0 < n < len(key) and n > best_len and key[:n] == cached_key:
            best_len, best_summary = n, cached_summary
    return best_len, best_summary


def clear_summary_cache() -> None:
    """Test hook / model-switch hygiene: drop every cached summary."""
    _SUMMARY_CACHE.clear()


class ChatWorker:
    """
    A stateless worker class that encapsulates the logic for a single chat API call.
    It determines the correct system prompt to use based on the conversation context.
    """
    def __init__(self, system_prompt):
        """
        Initializes the ChatWorker.

        Args:
            system_prompt (str): The default system prompt to use if no custom one is found.
        """
        self.system_prompt = system_prompt
        self.token_estimator = TokenEstimator()
        # ADR-006 stage 6.6: no longer the budget itself - run() derives the
        # real budget from the active model's context window. Kept as the
        # last-resort fallback when the window lookup fails entirely.
        self.MAX_TOKENS = 8000

    def run(self, conversation_history, current_node, cancellation_event=None, resolved_system_prompt=None,
            on_chunk=None, *, runtime=None, on_context_trimmed=None, on_usage=None, model_ref=None,
            settings_manager=None, on_fallback=None):
        """
        Executes the chat logic for a single turn.

        Args:
            conversation_history (list): The list of messages in the conversation.
            current_node: Kept for signature compatibility with every real caller
                (backend/agents.py) and ChatAgent.get_response, which forwards it
                unchanged - no longer read inside this method (ADR-002 stage 2.1:
                its only use was the deleted Qt-era fallback branch below).
            resolved_system_prompt (str, optional): The branch system prompt already
                resolved by the caller (AgentDispatcher._resolve_branch_system_prompt
                in backend/agents.py). Every real caller supplies this.
            on_chunk (callable, optional): Qt-removal R4.4 token streaming. When provided,
                routes through api_provider.chat_stream(...) instead of api_provider.chat(...),
                invoking on_chunk(delta, reset) as incremental text arrives. Additive and
                default-None: every existing call site (this legacy Qt path included) that
                omits it gets byte-identical behavior via the unchanged chat() call below.
            runtime (api_provider.ProviderRuntime, optional): ADR-006 stage 6.5 per-session
                provider runtime. Forwarded to api_provider.chat/chat_stream only when
                non-None - None (every pre-6.5 caller, and every default-session call)
                keeps the call byte-identical, routing through the module globals.
            on_context_trimmed (callable, optional): ADR-006 stage 6.6. Called (from
                THIS worker thread) with (dropped_count, summarized: bool) when
                trim_history had to drop older turns to fit the model's context
                window - after the optional summarization attempt, before the main
                request. Never called when nothing was dropped.
            on_usage (callable, optional): ADR-006 stage 6.8. Called (from THIS
                worker thread) with the provider's normalized usage dict
                ({"prompt_tokens": int | None, "completion_tokens": int | None})
                when the response carried real counts. Never called when the
                provider reported nothing.
            model_ref (graphlink_model_catalog.ModelRef, optional): ADR-018
                stage 18.2. Already resolved by the caller (AgentDispatcher's
                node/branch-override chain, mirroring resolved_system_prompt's
                own "resolved on the caller's side, this worker never walks
                the scene itself" posture) - forwarded straight through to
                api_provider.chat/chat_stream, which use it instead of their
                own task-keyed model lookup when supplied. Additive,
                keyword-only, default-None.
            settings_manager (graphlink_settings_store.SettingsManager,
                optional): ADR-018 stage 18.4. Forwarded straight through to
                api_provider.chat/chat_stream's own settings_manager kwarg,
                which consults it ONLY when model_ref is absent AND its own
                task-keyed lookup found nothing configured - the auto-policy
                rung of the resolution chain. Never used by this method
                directly, and never threaded onto the nested trim-
                summarization call below (that call always uses
                TASK_WEB_SUMMARIZE's own workspace default - an auto-picked
                fallback is scoped to the reply itself, same posture as
                model_ref_kwargs). Additive, keyword-only, default-None.
            on_fallback (callable, optional): ADR-018 stage 18.5. Forwarded
                straight through to api_provider.chat/chat_stream's own
                on_fallback kwarg, called (on THIS worker thread) with
                (failed_provider, fallback_ref, exc) the instant a retryable/
                unavailable failure is substituted for a different provider -
                "never a silent swap" per the ADR's own decision #4. Same
                main-request-only scoping as settings_manager above.
                Additive, keyword-only, default-None.

        Returns:
            str: The AI-generated response text.

        Raises:
            Exception: Propagates exceptions from the API provider.
        """
        final_system_prompt = resolved_system_prompt
        use_system_prompt = bool((final_system_prompt or "").strip())

        try:
            sys_tokens = 0
            messages = []
            if use_system_prompt:
                system_msg = {'role': 'system', 'content': final_system_prompt}
                sys_tokens = self.token_estimator.count_tokens(json.dumps(system_msg))
                messages.append(system_msg)
            # ADR-006 stage 6.5: the runtime kwarg is only passed when a
            # per-session runtime was actually supplied, so default-session
            # calls (runtime=None) stay byte-identical for every existing
            # api_provider.chat/chat_stream monkeypatch.
            runtime_kwargs = {"runtime": runtime} if runtime is not None else {}
            # ADR-018 stage 18.2: same omit-when-None posture as runtime_kwargs
            # above - only threaded onto the MAIN request below, never onto
            # the trim-summarization's own nested chat() call (that call
            # always uses TASK_WEB_SUMMARIZE's own workspace default; a
            # node/branch model pin is scoped to the reply itself).
            model_ref_kwargs = {"model_ref": model_ref} if model_ref is not None else {}
            # ADR-018 stage 18.4: same omit-when-None, main-request-only
            # posture as model_ref_kwargs above.
            settings_manager_kwargs = (
                {"settings_manager": settings_manager} if settings_manager is not None else {}
            )
            # ADR-018 stage 18.5: same omit-when-None, main-request-only
            # posture as model_ref_kwargs/settings_manager_kwargs above -
            # never threaded onto the nested trim-summarization call either.
            on_fallback_kwargs = {"on_fallback": on_fallback} if on_fallback is not None else {}

            # ADR-006 stage 6.6: the history budget derives from the ACTIVE
            # model's real context window (llama.cpp n_ctx / Ollama show() /
            # documented API-family table - see ProviderRuntime.context_window)
            # instead of a hardcoded 8000. A slice of the window is reserved
            # for the model's own output; the fallback on any lookup failure
            # is the legacy 8000 budget.
            try:
                active_runtime = runtime if runtime is not None else api_provider.DEFAULT_RUNTIME
                window = int(active_runtime.context_window(config.TASK_CHAT))
            except Exception:
                window = self.MAX_TOKENS
            reserve = min(4096, window // 4)
            history_budget = max(1024, window - reserve)

            # trim_history's contiguous-window semantics are deliberate and
            # preserved: it breaks at the first message that no longer fits,
            # so the kept conversation is always a contiguous suffix - a
            # window with holes in the middle would present the model an
            # incoherent dialogue.
            normalized_history = clone_history(conversation_history)
            trimmed_history, _ = trim_history(
                normalized_history,
                self.token_estimator,
                max_tokens=history_budget,
                system_prompt_estimate=sys_tokens,
            )
            dropped = len(normalized_history) - len(trimmed_history)
            if dropped > 0:
                # ADR-006 stage 6.6: summarize the dropped older turns with
                # ONE nested blocking chat() call (legal on this worker
                # thread - web_research does the same) and inject the result
                # ahead of the kept turns as a user-role message (a system
                # slot would fight the persona and Anthropic's single system
                # string). ANY summarization failure degrades to today's
                # silent-drop behavior - the main request must never fail
                # because the summarizer died. 6.8 review fix: summaries are
                # cached (see _SUMMARY_CACHE), and on_context_trimmed fires
                # ONLY on a cache miss - new content actually summarized -
                # so the toast doesn't repeat on every subsequent message.
                summary, cache_miss, summarized = None, False, False
                dropped_messages = normalized_history[:dropped]
                if cancellation_event is None or not cancellation_event.is_set():
                    summary, cache_miss = self._summary_for_dropped_turns(
                        dropped_messages, cancellation_event, runtime_kwargs
                    )
                if summary:
                    trimmed_history.insert(0, {
                        "role": "user",
                        "content": "[Summary of earlier conversation]\n" + summary,
                    })
                    summarized = True
                if cache_miss and on_context_trimmed is not None:
                    try:
                        on_context_trimmed(dropped, summarized)
                    except Exception:
                        pass

            messages.extend(trimmed_history)
            if on_chunk is not None:
                response = api_provider.chat_stream(
                    task=config.TASK_CHAT,
                    messages=messages,
                    on_chunk=on_chunk,
                    cancellation_event=cancellation_event,
                    **runtime_kwargs,
                    **model_ref_kwargs,
                    **settings_manager_kwargs,
                    **on_fallback_kwargs,
                )
            else:
                response = api_provider.chat(
                    task=config.TASK_CHAT,
                    messages=messages,
                    cancellation_event=cancellation_event,
                    **runtime_kwargs,
                    **model_ref_kwargs,
                    **settings_manager_kwargs,
                    **on_fallback_kwargs,
                )
            # ADR-006 stage 6.8: surface the provider's real usage counts
            # when present (chat_stream always includes the key; blocking
            # chat() only for the local branches - .get keeps both shapes,
            # and every test fake that returns a bare {"message": ...}).
            if on_usage is not None:
                usage = response.get("usage") if isinstance(response, dict) else None
                if usage:
                    try:
                        on_usage(usage)
                    except Exception:
                        pass  # accounting must never fail the reply
            ai_message = response['message']['content']
            return ai_message
        except Exception:
            logger.exception("ChatWorker API call failed")
            raise

    def _summary_for_dropped_turns(self, dropped_messages, cancellation_event, runtime_kwargs):
        """Cache-aware wrapper around _summarize_dropped_turns (6.8 review
        fix - see _SUMMARY_CACHE's comment). Returns (summary_or_None,
        cache_miss): a hit returns the cached text without any model call;
        a miss summarizes (incrementally, when a cached prefix exists),
        caches the result, and degrades to (None, True) on any failure."""
        key = _summary_cache_key(dropped_messages)
        cached = _SUMMARY_CACHE.get(key)
        if cached is not None:
            _SUMMARY_CACHE.move_to_end(key)
            return cached, False

        prefix_len, prefix_summary = _longest_cached_prefix(key)
        to_summarize = dropped_messages
        if prefix_summary is not None:
            to_summarize = [
                {
                    "role": "user",
                    "content": "[Summary of earlier conversation]\n" + prefix_summary,
                },
                *dropped_messages[prefix_len:],
            ]
        try:
            summary = self._summarize_dropped_turns(
                to_summarize, cancellation_event, runtime_kwargs
            )
        except Exception:
            return None, True  # miss + failure -> silent drop, toast still honest
        if summary:
            _SUMMARY_CACHE[key] = summary
            while len(_SUMMARY_CACHE) > _SUMMARY_CACHE_MAX_ENTRIES:
                _SUMMARY_CACHE.popitem(last=False)
        return (summary or None), True

    def _summarize_dropped_turns(self, dropped_messages, cancellation_event, runtime_kwargs):
        """ADR-006 stage 6.6: one blocking summarization call over the turns
        trim_history dropped (bounded output contract - see the registered
        "context-summary" prompt). Raises on any failure; run() catches and
        degrades to silent drop."""
        transcript = history_to_transcript(
            dropped_messages, max_messages=20, max_chars_per_message=600
        )
        response = api_provider.chat(
            task=config.TASK_WEB_SUMMARIZE,
            messages=[
                {"role": "system", "content": CONTEXT_SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": (
                    "Summarize these earlier conversation turns that no longer "
                    f"fit the context window:\n\n{transcript}"
                )},
            ],
            cancellation_event=cancellation_event,
            **runtime_kwargs,
        )
        return str(response["message"]["content"] or "").strip()


class ChatAgent:
    """
    The primary agent for handling general-purpose chat conversations.
    This agent is stateless; it relies on the conversation history passed to it for context.
    """
    def __init__(self, name, persona):
        """
        Initializes the ChatAgent.

        Args:
            name (str): The name of the AI assistant.
            persona (str): The detailed system prompt defining the AI's behavior and knowledge.
        """
        self.name = name or "AI Assistant"
        # ADR-006 stage 6.7: disable actually disables. The old
        # `persona or "(default persona)"` fallback meant a blank persona
        # (system prompt disabled in Settings) still produced a non-empty
        # system_prompt ("You are ... (default persona)"), defeating
        # ChatWorker.run's use_system_prompt guard. A blank persona now
        # yields system_prompt == "" so no system message is sent at all.
        self.persona = persona or ""
        if self.persona.strip():
            self.system_prompt = f"You are {self.name}. {self.persona}"
        else:
            self.system_prompt = ""

    def get_response(self, conversation_history, current_node, cancellation_event=None, resolved_system_prompt=None,
                      on_chunk=None, *, runtime=None, on_context_trimmed=None, on_usage=None, model_ref=None,
                      settings_manager=None, on_fallback=None):
        """
        Gets an AI response for a given conversation history.

        Args:
            conversation_history (list): The list of messages in the conversation.
            current_node (QGraphicsItem): The current node context.
            resolved_system_prompt (str, optional): Branch system prompt already resolved
                on the GUI thread; passed straight through so the worker never walks the
                scene itself (#20).
            on_chunk (callable, optional): Qt-removal R4.4 token streaming; passed straight
                through to ChatWorker.run (see its docstring). Additive, default-None.
            runtime (api_provider.ProviderRuntime, optional): ADR-006 stage 6.5 per-session
                provider runtime; passed straight through to ChatWorker.run (see its
                docstring). Additive, keyword-only, default-None.
            on_context_trimmed (callable, optional): ADR-006 stage 6.6 trim/summarize
                signal; passed straight through to ChatWorker.run (see its docstring).
                Additive, keyword-only, default-None.
            model_ref (graphlink_model_catalog.ModelRef, optional): ADR-018 stage
                18.2; passed straight through to ChatWorker.run (see its docstring).
                Additive, keyword-only, default-None.
            settings_manager (graphlink_settings_store.SettingsManager, optional):
                ADR-018 stage 18.4; passed straight through to ChatWorker.run
                (see its docstring). Additive, keyword-only, default-None.
            on_fallback (callable, optional): ADR-018 stage 18.5; passed straight
                through to ChatWorker.run (see its docstring). Additive,
                keyword-only, default-None.

        Returns:
            str: The AI-generated response text.
        """
        # This agent is stateless. It does not store conversation_history.
        # It creates a temporary ChatWorker to handle the API call.
        chat_worker = ChatWorker(self.system_prompt)
        ai_response = chat_worker.run(
            conversation_history,
            current_node,
            cancellation_event=cancellation_event,
            resolved_system_prompt=resolved_system_prompt,
            on_chunk=on_chunk,
            runtime=runtime,
            on_context_trimmed=on_context_trimmed,
            on_usage=on_usage,
            model_ref=model_ref,
            settings_manager=settings_manager,
            on_fallback=on_fallback,
        )
        return ai_response
