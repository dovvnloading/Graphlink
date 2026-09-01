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

# NOTE on the F401-suppressed imports below: after the ADR split of
# AgentDispatcher into backend/agent_dispatch/ mixins, several of these
# names are no longer referenced by code in THIS file - but they must stay
# in this module's namespace regardless: the mixins deliberately resolve
# them late-bound as `agents_module.<name>` at call time (never via their
# own module-top imports), precisely so the test suite's
# `monkeypatch.setattr(backend.agents, "<name>", ...)` patch seams keep
# working unchanged. Removing an "unused" import here silently breaks
# those seams and the mixins' name resolution at runtime.
import asyncio  # noqa: F401
import difflib
import inspect  # noqa: F401
import logging
import threading
import uuid  # noqa: F401
from pathlib import Path
from urllib.parse import quote

import api_provider
import graphlink_task_config as config
from graphlink_artifact_agent import ArtifactAgent
from graphlink_chart_data import CHART_JSON_SCHEMAS, chart_generation_messages
from graphlink_chat_agent import ChatAgent
from graphlink_note_agent import BranchComparisonAgent, BranchSynthesisAgent, ExplainerAgent, KeyTakeawayAgent
from graphlink_settings_store import SettingsManager  # type hint only
from graphlink_plugins.common.github_client import GitHubRestClient
from graphlink_plugins.gitlink.agent import GitlinkAgent, _fingerprint_changes, _is_repo_text_path  # noqa: F401
from graphlink_plugins.gitlink.repository import (
    GitlinkRepository,
    apply_change_set,
    default_import_root,
    read_local_repo_file,
    validate_pending_changes,
)
from graphlink_plugins.common.python_repl import CodeAnalysisAgent
from graphlink_plugins.code_sandbox.domain import (
    SandboxGenerationAgent,
    SandboxRepairAgent,
    VirtualEnvSandbox,  # noqa: F401
    _extract_python_block,  # noqa: F401
    _normalize_requirements,  # noqa: F401
)
from graphlink_scratch_dirs import EXECUTION_SANDBOX_ROOT  # noqa: F401
from graphlink_scratch_dirs import remove_scratch_dir_for_id  # noqa: F401
# ADR-020 stage 20.3: the workspace-default rung of resolve_model_ref's
# chain, wired into _resolve_model_ref_for_dispatch below - safe as a
# top-level import (graphlink_model_catalog is dependency-free, see its own
# ModelRef docstring; several other modules already import it this way,
# e.g. api_provider.py).
from graphlink_model_catalog import ModelRef, resolve_model_ref  # noqa: F401
from graphlink_plugins.web_research.domain import (
    CancellationToken,  # noqa: F401
    RequestCancelled,  # noqa: F401
    ResearchFailure,  # noqa: F401
    WebResearchRequest,  # noqa: F401
)
from graphlink_plugins.web_research.service import WebResearchService  # noqa: F401
from graphlink_prompts import BASE_SYSTEM_PROMPT  # noqa: F401

from backend.events import SessionBus  # type hint only  # noqa: F401
from backend.run_lifecycle import RunRegistry, run_single_shot  # noqa: F401
from backend.structured_output import StructuredOutputError, respond_json
from backend.agent_dispatch.builder import BuilderDispatchOps
from backend.agent_dispatch.chat import ChatDispatchOps
from backend.agent_dispatch.code_sandbox import CodeSandboxDispatchOps
from backend.agent_dispatch.content import ContentDispatchOps
from backend.agent_dispatch.core import DispatcherCoreOps
from backend.agent_dispatch.gitlink import GitlinkDispatchOps
from backend.agent_dispatch.harness import HarnessDispatchOps
from backend.agent_dispatch.research import ResearchDispatchOps

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

# R5.4: shared by both start_pycoder_run (retired, PLAN-2026-08-24 H5) and
# start_code_sandbox_run - same exact mechanism and reasoning as
# _GITLINK_RUN_CLAIM_PLACEHOLDER above (see that constant's own comment for
# the full race this closes), just named for this pair of kinds rather than
# reusing the Gitlink-specific name. code_sandbox's WS-intent wrapper in
# backend/api/intents_code_sandbox.py (run_code_sandbox) claims
# node.pending_request_id with this exact sentinel, synchronously, before
# any await - and AgentDispatcher.start_code_sandbox_run below recognizes
# ONLY this exact value as "already claimed by my own caller, safe to
# overwrite".
_CODE_EXEC_RUN_CLAIM_PLACEHOLDER = "pending"

# ADR-002: artifact and web_research moved off the session-wide,
# kind-scoped RunRegistry.is_busy() guard onto the same per-node guard
# gitlink/code_sandbox already use, and inherit the same race with it -
# both of their WS-intent wrappers publish a scene update BEFORE calling
# the dispatcher, so without a synchronous claim two rapid clicks on ONE
# node could both pass the guard. Claimed by the wrappers in
# backend/api/intents_artifact.py and intents_web_research.py before any
# await; the dispatch methods recognize ONLY this exact value as "already
# claimed by my own caller, safe to overwrite".
_NODE_RUN_CLAIM_PLACEHOLDER = "pending"


def bootstrap_provider_state(settings_manager: SettingsManager) -> None:
    """Bootstrap api_provider's module-level provider state from persisted
    settings. Call exactly ONCE per process (this is process-global state,
    not session state) - see backend/app.py's create_app()."""
    # Unconditional and first, regardless of active mode: resolves Auto/
    # inherited Ollama task-model assignments against the cached scan, same
    # as legacy does at startup.
    # ADR-006 stage 6.5 (H6): the locked writer wrapper, so the table
    # can't change mid-snapshot - see api_provider.sync_ollama_models.
    api_provider.sync_ollama_models(settings_manager)

    mode_text = settings_manager.get_current_mode()
    try:
        apply_provider_mode(mode_text, settings_manager)
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


def apply_provider_mode(mode_text: str, settings_manager: SettingsManager) -> None:
    """Three-way dispatch mirroring the legacy mode-switch handlers. Raises
    ValueError for any mode_text it does not recognize - see
    bootstrap_provider_state's single fallback branch above.

    ADR-006 stage 6.5: public (formerly _apply_mode) so the Settings
    dialog's setProviderMode intent (backend/api/intents_settings_general.py)
    can switch the live provider at runtime through the exact same logic
    bootstrap uses - previously there was no path back to Ollama/Llama.cpp
    from API mode without a restart. Acts on api_provider's module-level
    functions, i.e. the DEFAULT session's runtime - correct while the
    shipped app has exactly one session; per-session settings routing is
    deferred to 6.5b/ADR-012."""
    if mode_text == config.MODE_OLLAMA_LOCAL:
        api_provider.initialize_local_provider(
            config.LOCAL_PROVIDER_OLLAMA,
            {"reasoning_level": settings_manager.get_ollama_reasoning_level()},
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


# ADR-006 stage 6.5: thin compatibility alias for the pre-6.5 private name -
# existing tests exercise the ValueError contract through it.
_apply_mode = apply_provider_mode


class AgentDispatcher(
    DispatcherCoreOps,
    BuilderDispatchOps,
    HarnessDispatchOps,
    ChatDispatchOps,
    ResearchDispatchOps,
    GitlinkDispatchOps,
    CodeSandboxDispatchOps,
    ContentDispatchOps,
):
    """One instance per session - never a module-level singleton, since two
    sessions must never share in-flight request state."""


# R8a: the two note agents, keyed by the `note_kind` start_note_generation
# takes. Kept as data rather than an if/elif so adding a third note agent is
# one entry, not another branch in three places.
NOTE_AGENT_LABELS = {"takeaway": "Key takeaway", "explainer": "Explainer note"}
_NOTE_AGENTS = {"takeaway": KeyTakeawayAgent, "explainer": ExplainerAgent}


def _call_note_agent(note_kind: str, source_text: str) -> str:
    """Runs inside asyncio.to_thread - the blocking driver for
    start_note_generation above, mirroring _call_chart_agent's own shape
    (fresh agent instance per call). Returns the agent's already-cleaned
    text; unlike the chart agent there is no JSON to parse."""
    agent_cls = _NOTE_AGENTS.get(note_kind)
    if agent_cls is None:
        raise ValueError(f"unknown note kind: {note_kind}")
    return agent_cls().get_response(source_text)


def _call_branch_comparison_agent(source_text: str) -> str:
    """Runs inside asyncio.to_thread - the blocking driver for
    start_branch_comparison above, mirroring _call_note_agent's own shape
    (fresh agent instance per call)."""
    return BranchComparisonAgent().get_response(source_text)


def _call_branch_synthesis_agent(source_text: str, instructions: str) -> str:
    """Runs inside asyncio.to_thread - the blocking driver for
    start_branch_synthesis above, mirroring _call_branch_comparison_agent's
    own shape (fresh agent instance per call)."""
    return BranchSynthesisAgent().get_response(source_text, instructions)


def _call_code_analysis_agent(original_prompt, code, code_output) -> str:
    """Runs inside asyncio.to_thread. Reuses CodeAnalysisAgent.get_response
    verbatim - Execution Sandbox's own final analysis step (originally
    written for, and shared with, the now-retired Py-Coder plugin - see
    graphlink_plugins/common/python_repl.py)."""
    return CodeAnalysisAgent().get_response(original_prompt, code, code_output)


def _call_sandbox_generation_agent(conversation_history, user_prompt, requirements_manifest) -> str:
    """Runs inside asyncio.to_thread. Reuses SandboxGenerationAgent.get_response
    verbatim."""
    return SandboxGenerationAgent().get_response(conversation_history, user_prompt, requirements_manifest)


def _call_sandbox_repair_agent(code, error_output, requirements_manifest, original_prompt) -> str:
    """Runs inside asyncio.to_thread. Reuses SandboxRepairAgent.get_response
    verbatim."""
    return SandboxRepairAgent().get_response(
        code, error_output, requirements_manifest, original_prompt=original_prompt
    )


# R5.4: replicates CodeSandboxExecutionWorker._is_error_output exactly - that
# method never moved to graphlink_plugins/code_sandbox/domain.py (it is a
# worker-instance method, not a free function any moved domain piece calls -
# see that module's own docstring for why), so this is a second, independent
# copy of the same keyword-based heuristic, not a shared import.
_SANDBOX_ERROR_KEYWORDS = (
    "traceback (most recent call last)",
    "modulenotfounderror",
    "importerror",
    "nameerror:",
    "syntaxerror:",
    "typeerror:",
    "valueerror:",
    "exception:",
)


def _is_sandbox_error_output(output_text, return_code) -> bool:
    if return_code != 0:
        return True
    lowered = (output_text or "").lower()
    return any(keyword in lowered for keyword in _SANDBOX_ERROR_KEYWORDS)


def _call_chat_agent(conversation_history, persona_text, cancel_event, *, runtime=None,
                     persona_is_override=False, on_context_trimmed=None, on_usage=None,
                     model_ref=None, settings_manager=None, on_fallback=None) -> str:
    """Runs inside asyncio.to_thread - a real OS thread, not the event loop.

    ADR-006 stage 6.5: `runtime` is an additive keyword-only kwarg, forwarded
    to ChatAgent.get_response only when non-None - _dispatch's call site only
    passes it for a non-default session (see AgentDispatcher._runtime_kwargs),
    so every test fake of the exact pre-6.5 arity keeps working.

    ADR-006 stage 6.7: `persona_is_override` (additive keyword-only, passed
    by _dispatch only when True - same omit-when-default pattern as runtime)
    marks persona_text as a user's branch-attached System Prompt note
    override. An override reaches the wire RAW, never wrapped in
    "You are Graphlink Assistant. {override}" - the user wrote the exact
    system prompt they want. The old "(default persona)" QUIRK this
    function's comment used to document is also fixed: a blank persona_text
    (system prompt disabled in Settings) now yields an EMPTY system prompt
    (no system message at all) - see ChatAgent.__init__."""
    agent = ChatAgent("Graphlink Assistant", persona_text)
    resolved = persona_text if persona_is_override else agent.system_prompt
    return agent.get_response(
        conversation_history,
        # current_node=None is never dereferenced: ChatWorker.run only walks
        # current_node when resolved_system_prompt is None, and a real value
        # is always supplied here ("" when disabled counts: run()'s
        # use_system_prompt guard suppresses the system message for it).
        current_node=None,
        cancellation_event=cancel_event,
        # Default-persona path: agent.system_prompt (the composed
        # "You are {name}. {persona}" string, or "" when disabled).
        # Override path: the RAW note text, uncomposed.
        resolved_system_prompt=resolved,
        **({"runtime": runtime} if runtime is not None else {}),
        # ADR-006 stage 6.6: trim/summarize signal - forwarded omit-when-None.
        **({"on_context_trimmed": on_context_trimmed} if on_context_trimmed is not None else {}),
        # ADR-006 stage 6.8: real-usage signal - forwarded omit-when-None.
        **({"on_usage": on_usage} if on_usage is not None else {}),
        # ADR-018 stage 18.2: resolved node/branch model pin - forwarded
        # omit-when-None, same posture as every other additive kwarg here.
        **({"model_ref": model_ref} if model_ref is not None else {}),
        # ADR-018 stage 18.4: the session's SettingsManager, forwarded
        # omit-when-None - only ever consumed by api_provider's auto-policy
        # fallback (see its own docstring), never by anything in this
        # module or ChatAgent/ChatWorker themselves.
        **({"settings_manager": settings_manager} if settings_manager is not None else {}),
        # ADR-018 stage 18.5: fallback-substitution notification - forwarded
        # omit-when-None, same posture as every other additive kwarg here.
        **({"on_fallback": on_fallback} if on_fallback is not None else {}),
    )


def _call_chat_agent_stream(conversation_history, persona_text, cancel_event, on_chunk, *, runtime=None,
                            persona_is_override=False, on_context_trimmed=None, on_usage=None,
                            model_ref=None, settings_manager=None, on_fallback=None) -> str:
    """Runs inside asyncio.to_thread - a real OS thread, not the event loop.
    Streaming counterpart to _call_chat_agent (R4.4) - same persona/
    current_node/resolved_system_prompt guarantees as that function (see its
    own docstring; the 6.7 disable/override fixes apply identically here
    since both build the ChatAgent the same way).

    The only difference is the trailing `on_chunk` argument, forwarded
    straight through to ChatAgent.get_response's additive `on_chunk` kwarg
    (see graphlink_app/graphlink_chat_agent.py): when non-None, get_response
    routes the call through api_provider.chat_stream instead of
    api_provider.chat, invoking `on_chunk(delta, reset)` zero or more times
    before returning the same full-text shape `_call_chat_agent` returns.
    `on_chunk` itself is `_dispatch`'s `_thread_on_chunk` closure - a plain
    callable safe to invoke from this worker thread, since it only ever does
    `loop.call_soon_threadsafe(...)` internally rather than touching the
    event loop directly.

    ADR-006 stage 6.5: `runtime` follows _call_chat_agent's contract exactly
    (additive keyword-only, forwarded only when non-None - see its own
    docstring). ADR-006 stage 6.7: `persona_is_override` follows
    _call_chat_agent's contract exactly too (raw override passthrough,
    passed by _dispatch only when True)."""
    agent = ChatAgent("Graphlink Assistant", persona_text)
    resolved = persona_text if persona_is_override else agent.system_prompt
    return agent.get_response(
        conversation_history,
        current_node=None,
        cancellation_event=cancel_event,
        resolved_system_prompt=resolved,
        on_chunk=on_chunk,
        **({"runtime": runtime} if runtime is not None else {}),
        # ADR-006 stage 6.6: trim/summarize signal - forwarded omit-when-None.
        **({"on_context_trimmed": on_context_trimmed} if on_context_trimmed is not None else {}),
        # ADR-006 stage 6.8: real-usage signal - forwarded omit-when-None.
        **({"on_usage": on_usage} if on_usage is not None else {}),
        # ADR-018 stage 18.2: resolved node/branch model pin - forwarded
        # omit-when-None, same posture as every other additive kwarg here.
        **({"model_ref": model_ref} if model_ref is not None else {}),
        # ADR-018 stage 18.4: forwarded omit-when-None, same posture as
        # _call_chat_agent's own settings_manager kwarg.
        **({"settings_manager": settings_manager} if settings_manager is not None else {}),
        # ADR-018 stage 18.5: forwarded omit-when-None, same posture as
        # _call_chat_agent's own on_fallback kwarg.
        **({"on_fallback": on_fallback} if on_fallback is not None else {}),
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


def _call_chart_agent(source_text: str, chart_type: str, cancel_event: threading.Event | None = None) -> dict:
    """Runs inside asyncio.to_thread - the blocking driver for
    start_chart_generation above, mirroring _call_artifact_agent's own
    shape. ADR-013 stage 13.3: retired graphlink_chart_agent.py's whole
    ChartDataAgent pipeline (five hand-maintained per-type prompts, a
    bespoke clean_response/repair_chart_data pair, a manual per-provider
    JSON-mode if/elif, and a heuristic-regex fallback of last resort) in
    favor of ONE respond_json call against a real JSON Schema - the module
    ChartDataAgent existed specifically to replace (see structured_output.py's
    own docstring). graphlink_chart_data.CHART_JSON_SCHEMAS/
    chart_generation_messages are shared with backend/evals/runner.py's own
    chart eval fixture, so the eval drives the identical shape this
    actually ships.

    `cancel_event`, when set, is respond_json's own cancellation_event -
    forwarded all the way to api_provider.chat(), a REAL interruption
    (api_provider.RequestCancelledError, checked before the request is sent
    and between any transport retries) rather than the pre-13.3 gap this
    surface's own start_chart_generation docstring used to document ("no
    cancellation checkpoint of its own"). A cancellation raises straight
    through - run_single_shot's own exception handling already treats a
    caught exception while cancel_event.is_set() as a silent cancel, not a
    failure, so this function does not catch it itself.

    Returns a dict either way: the canonical shape on success, or
    {"error": <message>} on respond_json's own StructuredOutputError (the
    model could not produce schema-conforming JSON even after one repair
    attempt) - the exact contract start_chart_generation/_run_chart already
    expect (top-level "error" key => surfaced as a failure, never reaches
    on_success/add_chart_node)."""
    try:
        schema = CHART_JSON_SCHEMAS[chart_type]
    except KeyError:
        # Defensive only - both real callers (intents_chart.py's
        # generate_chart, tools_graph.py's _run_chart) already validate
        # chart_type against SUPPORTED_CHART_TYPES before ever reaching
        # here; this guards against a raw KeyError if a future caller
        # regresses that, matching the retired ChartDataAgent's own
        # equivalent internal guard.
        return {"error": f"Unsupported chart type: {chart_type!r}"}
    try:
        return respond_json(
            config.TASK_CHART,
            chart_generation_messages(source_text, chart_type),
            schema,
            schema_name=f"{chart_type}_chart",
            cancellation_event=cancel_event,
        )
    except StructuredOutputError as exc:
        return {"error": str(exc)}


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
    (`branch_name=node.state.gitlink_branch`) rather than legacy's more eager
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


def register_agents(
    bus, composer_document, notifications_state, settings_manager, provider_runtime=None, diagnostics=None
) -> AgentDispatcher:
    # ADR-006 stage 6.5: provider_runtime is None for the default session
    # (module-global path, byte-identical behavior) - see
    # AgentDispatcher.__init__'s own comment.
    dispatcher = AgentDispatcher(settings_manager, provider_runtime=provider_runtime, diagnostics=diagnostics)
    # dispatcher.cancel is synchronous (just sets an Event and returns a
    # bool) - no publish/await needed here; the in-flight _run task's own
    # finally block handles the resulting state transition.
    bus.register_intent("app-composer", "cancelChatRequest", lambda request_id: dispatcher.cancel(request_id))
    return dispatcher
