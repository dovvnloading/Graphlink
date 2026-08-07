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
import json
import logging
import re
import threading
import uuid
from pathlib import Path
from urllib.parse import quote

import api_provider
import graphlink_task_config as config
from graphlink_artifact_agent import ArtifactAgent
from graphlink_chart_agent import ChartDataAgent
from graphlink_chat_agent import ChatAgent
from graphlink_note_agent import BranchComparisonAgent, BranchSynthesisAgent, ExplainerAgent, KeyTakeawayAgent
from graphlink_settings_store import SettingsManager  # type hint only
from graphlink_plugins.common.github_client import GitHubRestClient
from graphlink_plugins.gitlink.agent import GitlinkAgent, _fingerprint_changes, _is_repo_text_path
from graphlink_plugins.gitlink.repository import (
    GitlinkRepository,
    apply_change_set,
    default_import_root,
    read_local_repo_file,
    validate_pending_changes,
)
from graphlink_plugins.pycoder.domain import (
    PyCoderAnalysisAgent,
    PyCoderExecutionAgent,
    PyCoderRepairAgent,
    PythonREPL,
)
from graphlink_plugins.code_sandbox.domain import (
    SandboxGenerationAgent,
    SandboxRepairAgent,
    VirtualEnvSandbox,
    _extract_python_block,
    _normalize_requirements,
)
from graphlink_scratch_dirs import EXECUTION_SANDBOX_ROOT, PYCODER_REPL_ROOT
from graphlink_scratch_dirs import remove_scratch_dir_for_id
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
from backend.run_lifecycle import RunRegistry, run_single_shot

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

# R5.4: the security-boundary section's own minimal, genuinely free
# mitigation - a hard wall-clock timeout on Py-Coder's REPL execute() call,
# closing the one real asymmetry recon found: Execution Sandbox already
# times out its own subprocess internally (VirtualEnvSandbox.execute_code's
# baked-in timeout_seconds=240, unchanged by this increment), but Py-Coder's
# REPL had NONE before this - an AI-generated infinite loop ran forever until
# a human clicked Stop. 240 is not an independently-derived number for THIS
# constant - it is deliberately the exact same value as Execution Sandbox's
# own existing ceiling, for cross-kind consistency. This is a hang guard, not
# a security control - see the module-level PyCoderNode/CodeSandboxNode
# security-boundary comment on AgentDispatcher.start_pycoder_run below for
# the full, unsoftened statement of what this boundary actually is.
PYCODER_EXECUTE_TIMEOUT_SECONDS = 240

# R5.4: shared by both start_pycoder_run and start_code_sandbox_run - same
# exact mechanism and reasoning as _GITLINK_RUN_CLAIM_PLACEHOLDER above (see
# that constant's own comment for the full race this closes), just named for
# this pair of new kinds rather than reusing the Gitlink-specific name. Both
# kinds' WS-intent wrappers in backend/canvas.py (run_pycoder/
# run_code_sandbox) claim node.pending_request_id with this exact sentinel,
# synchronously, before any await - and both
# AgentDispatcher.start_pycoder_run/start_code_sandbox_run below recognize
# ONLY this exact value as "already claimed by my own caller, safe to
# overwrite".
_CODE_EXEC_RUN_CLAIM_PLACEHOLDER = "pending"


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


class AgentDispatcher:
    """One instance per session - never a module-level singleton, since two
    sessions must never share in-flight request state."""

    def __init__(self, settings_manager: SettingsManager, provider_runtime=None):
        self._settings_manager = settings_manager
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
        self._runs = RunRegistry()
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
        # R5.4/ADR-002 stage 2.4g: Py-Coder Run ("pycoder" kind) and
        # Execution Sandbox Run ("code_sandbox" kind), also sharing
        # self._runs now, must be able to run concurrently with any of the
        # kinds above, same reasoning as every prior independent kind. Same
        # per-node busy-guard shape as gitlink_run/gitlink_apply above
        # (node.pending_request_id via _CODE_EXEC_RUN_CLAIM_PLACEHOLDER,
        # this registry pure task/cancel_event/approval_future bookkeeping,
        # never the busy gate) - and the FIRST two kinds to use RunHandle.
        # approval_future, the ENTIRE "waiting for human approval"
        # mechanism (see start_pycoder_run's own docstring), created
        # eagerly at claim time, before the background task even starts,
        # so cancel_pycoder/cancel_all_pending_approvals can always resolve
        # it even if the pipeline has not reached its own `await
        # approval_future` yet. Mutated IN PLACE on handle (a plain,
        # non-frozen dataclass) on every repair-loop iteration - a fresh
        # Future replaces the old one on the SAME handle object, never a
        # new claim - see start_pycoder_run's/start_code_sandbox_run's own
        # repair-loop comments for why callers must always re-read this
        # field fresh, never cache a captured reference.
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
        # R5.4: Py-Coder's REPL subprocess outlives any single run (state
        # persists between calls, same as legacy's own PyCoderReplManager -
        # see that class's own docstring in graphlink_plugins/pycoder/domain.py
        # for why its weakref.WeakKeyDictionary keying strategy does not
        # survive the port). Keyed by node_id (a plain string) instead:
        # explicit teardown via dispose_pycoder_repl, not GC. Execution
        # Sandbox needs NO equivalent manager - VirtualEnvSandbox is
        # request-scoped by design, constructed fresh per run inside
        # start_code_sandbox_run's own asyncio.to_thread-wrapped worker
        # function (exactly like _call_gitlink_agent constructs a fresh
        # GitlinkAgent per call) - the only state that must survive between
        # runs is the plain string node.state.code_sandbox_sandbox_id, real
        # SceneNode state, not a live object.
        self._pycoder_repls: dict[str, PythonREPL] = {}

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

    def get_pycoder_repl(self, node_id: str, repl_id: str) -> PythonREPL:
        """Lazy-create-or-reuse - mirrors PyCoderReplManager.get_repl's own
        shape, keyed by node_id (transient, session-scoped: this dict and
        node_id are both rebuilt from scratch together on every session
        reload, so reusing node_id here is safe even though it is not
        durable across reloads - see PythonREPL.cwd's own docstring for
        why the ON-DISK directory needs a different, stable identity
        instead). repl_id is node.state.pycoder_repl_id, minted once at
        node creation (ADR-005 stage 5.3 review-fix) - passed through to
        PythonREPL so its scratch cwd survives a reload even though this
        node's own id would not."""
        repl = self._pycoder_repls.get(node_id)
        if repl is None:
            repl = PythonREPL(repl_id=repl_id)
            self._pycoder_repls[node_id] = repl
        return repl

    async def dispose_pycoder_repl(
        self, node_id: str, *, repl_id: str | None = None, remove_scratch_dir: bool = False
    ) -> None:
        """Explicit teardown of one node's REPL subprocess. Tolerates a
        missing node_id silently (pop with a default) - called from exactly
        two places: backend/api/intents_nodes.py's remove_nodes WS-intent
        wrapper (for every deleted pycoder node), and start_pycoder_run's
        own execute-timeout guard below (a hung REPL must not be left
        alive). NOT called on disconnect/session-end - the REPL persists
        across disconnects exactly like every other piece of node state in
        SceneDocument already does; only explicit node deletion (or process
        shutdown) ends it. stop() does a blocking kill()+wait(), so it runs
        inside asyncio.to_thread rather than directly on the event loop.

        ADR-005 stage 5.3: remove_scratch_dir=True additionally deletes the
        REPL's scratch directory from disk - correct ONLY for the real
        node-deletion caller, where the node is gone for good. The
        execute-timeout guard passes the default False: a timeout means
        this one run misbehaved, not that the node's accumulated scratch
        files should be thrown away.

        Review-fix: removal is keyed off the passed-in repl_id (recomputed
        via remove_scratch_dir_for_id), NOT off whatever `repl` this pop
        happened to find - the two are NOT reliably the same thing. A REPL
        already popped by an earlier execute timeout (this method's OTHER
        caller, which never repopulates the dict - only a fresh
        get_pycoder_repl call does, i.e. running Py-Coder again) leaves
        node_id absent from _pycoder_repls; a subsequent real delete used
        to make this pop return None and silently skip the directory
        removal entirely; with a deterministic recompute the removal still
        happens even though there is no live object left to ask. Callers
        that pass remove_scratch_dir=True must also pass repl_id (the
        node's stable pycoder_repl_id, not its node_id)."""
        repl = self._pycoder_repls.pop(node_id, None)
        if repl is not None:
            await asyncio.to_thread(repl.stop)
        if remove_scratch_dir and repl_id:
            await asyncio.to_thread(remove_scratch_dir_for_id, PYCODER_REPL_ROOT, repl_id)

    def dispose_all_pycoder_repls(self) -> None:
        """Bulk, non-blocking teardown for every currently-tracked REPL
        subprocess - called from backend/app.py's _evict_idle_session right
        before the SessionBus itself is dropped from EventBus._sessions.
        Its one caller (_evict_idle_session, via EventBus's own
        synchronous sweep_idle_sessions/_eviction_loop chain) runs on the
        live asyncio event loop, so - review-fix - each repl.stop() (a
        documented-blocking kill()+wait(), possibly a Windows Job-Object
        guard.close() too) is fired on its own daemon thread rather than
        called inline: this method's async sibling dispose_pycoder_repl
        explicitly offloads the identical call via asyncio.to_thread for
        exactly this reason, and calling it directly here would stall
        every other connected client's WS/HTTP handling for however long
        the OS takes to kill+reap each process. Not awaited/joined - unlike
        the delete path, nothing here needs the stop to have completed
        before returning (this method deliberately never removes any
        directory afterward, see below), so there is nothing to wait for.
        PythonREPL.stop()'s own RLock already makes firing these
        concurrently (with each other, and with any other in-flight
        start()/stop() on the same instance) safe (the stage 5.2
        concurrent-stop() fix covers exactly that race).

        Deliberately does NOT remove each REPL's scratch directory, unlike
        the node-delete path above: eviction means "no one is currently
        connected", not "this node's work should be discarded" - the
        directory is exactly the kind of state a REPL restart already
        preserves across process restarts by design (see PythonREPL's own
        cwd docstring), and a reconnecting user may expect their files
        still there. What eviction genuinely leaks if left alone is the
        subprocess itself: once this SessionBus is gone from
        EventBus._sessions, nothing else holds a reference that could ever
        call stop() on it again."""
        for node_id in list(self._pycoder_repls.keys()):
            repl = self._pycoder_repls.pop(node_id, None)
            if repl is not None:
                threading.Thread(target=repl.stop, daemon=True).start()

    async def remove_code_sandbox_scratch_dir(self, sandbox_id: str) -> None:
        """ADR-005 stage 5.3: node-delete counterpart of
        dispose_pycoder_repl's remove_scratch_dir=True, for Execution
        Sandbox. Unlike Py-Coder's REPL, VirtualEnvSandbox is never cached
        on this dispatcher (see this class's own __init__ docstring) - the
        only state that survives a run is the plain sandbox_id string on
        the node itself - so there is no live object to ask for its
        base_dir; the path is recomputed the same deterministic way
        VirtualEnvSandbox.__init__ builds it (remove_scratch_dir_for_id
        also refuses to act on a blank sandbox_id, rather than rmtree-ing
        the shared "default" bucket a blank id resolves to - see that
        function's own docstring). A venv tree can be large, so the
        removal runs in a thread, same reasoning as dispose_pycoder_repl's
        own stop()/rmtree calls.

        Best-effort: an in-flight run for this node may still be exiting
        when a delete races it (cancelled moments earlier by remove_nodes'
        own code_exec_cancels loop), in which case removal can fail (e.g. a
        file still open on Windows) and is simply logged - the age sweep in
        graphlink_scratch_dirs.py is the backstop for anything left behind
        here."""
        await asyncio.to_thread(remove_scratch_dir_for_id, EXECUTION_SANDBOX_ROOT, sandbox_id)

    def cancel_pycoder(self, request_id: str) -> bool:
        """Cooperative cancel, same honestly-documented limitation as every
        other dispatch surface (the checkpoint is a cancel_event check
        between stages, not a true mid-call interrupt - EXCEPT for the
        approval pause itself, which this DOES immediately and definitely
        unblock by resolving approval_future - see start_pycoder_run's own
        docstring). Mirrors legacy's own stop() calling
        self._approval_event.set() to unblock a parked worker - otherwise
        Cancel would only work pre- or post-pause, never during it.

        ADR-002 stage 2.4g: kind="pycoder" is checked explicitly (unlike
        _resolve_approval below, which needs no such check) because this
        method unconditionally calls handle.cancel_event.set() - a foreign
        kind's handle could have cancel_event=None (chart/note/...) and
        AttributeError. Before this migration each kind's own private dict
        gave this isolation for free; self._runs sharing one namespace
        across every kind means it must be checked explicitly now - same
        reasoning as cancel_artifact's own kind= filter (stage 2.4d)."""
        handle = self._runs.get(request_id)
        if handle is None or handle.kind != "pycoder":
            return False
        # ADR-006 stage 6.2: the approval future is resolved BEFORE routing
        # through RunRegistry.cancel(), because cancel() now pops the handle
        # (release-on-cancel) and the future lives on it. cancel() then sets
        # the cancel_event and frees the slot immediately.
        future = handle.approval_future
        if future is not None and not future.done():
            future.set_result(False)
        return self._runs.cancel(request_id, kind="pycoder")

    def cancel_code_sandbox(self, request_id: str) -> bool:
        """Mirrors cancel_pycoder exactly (same shape, same reasoning)."""
        handle = self._runs.get(request_id)
        if handle is None or handle.kind != "code_sandbox":
            return False
        future = handle.approval_future
        if future is not None and not future.done():
            future.set_result(False)
        return self._runs.cancel(request_id, kind="code_sandbox")

    def _resolve_approval(self, request_id: str, approved: bool) -> bool:
        """The shared approve/deny primitive backing approve_code_execution/
        deny_code_execution below - looks up request_id directly in
        self._runs (a shared uuid4 namespace across every migrated kind,
        not just pycoder/code_sandbox), mirroring the WS intent layer's own
        two-shared-intents design (approveCodeExecution/denyCodeExecution,
        not four separate per-kind intents). No explicit kind check needed
        here (unlike cancel_pycoder/cancel_code_sandbox above): handle.
        approval_future is None for every kind except pycoder/code_sandbox
        (only those two ever pass one to claim()), so that field alone is
        already the correct discriminator - a chat/chart/.../gitlink
        request_id is naturally rejected by the `is None` check below,
        exactly as it was naturally absent from the old two-dict lookup.

        Guarding with `future.done()` is LOAD-BEARING, not defensive fluff -
        a duplicate/stale approve-or-deny message (e.g. a double-click, or a
        message that arrives after cancel_pycoder/cancel_code_sandbox/
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
        - by then cancel_all() has already tripped these two kinds'
        cancel_event too (now that they share self._runs with every other
        cancellable kind), closing a real pre-existing gap: a disconnect
        mid-EXECUTION (past the approval gate) previously left pycoder/
        code_sandbox's cancel_event untripped entirely, since neither
        lived in the dict cancel_all() used to walk."""
        self._runs.cancel_all_pending_approvals(("pycoder", "code_sandbox"))

    def cancel_gitlink(self, request_id: str) -> bool:
        """kind="gitlink_run": ADR-002 stage 2.4f - see RunRegistry.cancel's
        own docstring for why kind= is passed now that gitlink_run shares
        self._runs with other cancel_event-bearing kinds."""
        return self._runs.cancel(request_id, kind="gitlink_run")

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

    def cancel_web_research(self, request_id: str) -> bool:
        """kind="web_research": ADR-002 stage 2.4e - the first surface to
        actually exercise RunHandle.on_cancel (added in stage 2.4b), since
        CancellationToken.cancel is not a threading.Event.set. See
        RunRegistry.cancel's own docstring for why kind= is passed."""
        return self._runs.cancel(request_id, kind="web_research")

    def is_web_research_busy(self) -> bool:
        """Lets callers check the single-slot guard before mutating scene
        state, so a Run click on a node other than the one already running
        doesn't reset that node's progress/error fields only to be rejected
        a moment later by start_web_research's own busy check."""
        return self._runs.is_busy("web_research")

    def cancel_artifact(self, request_id: str) -> bool:
        """kind="artifact": ADR-002 stage 2.4d - the first surface to
        actually exercise RunRegistry.cancel()'s kind= filter (added in
        stage 2.4b) for real, now that artifact shares self._runs
        alongside chat/image, both also cancel_event-bearing. Without
        this filter a stale or mismatched request_id could trip the wrong
        kind's in-flight run - see RunRegistry.cancel's own docstring."""
        return self._runs.cancel(request_id, kind="artifact")

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
            else api_provider.is_configured
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
                                _call_chat_agent_stream,
                                conversation_history,
                                persona_text,
                                cancel_event,
                                _thread_on_chunk,
                                # ADR-006 stage 6.5: non-default sessions only
                                # - see _runtime_kwargs' own docstring.
                                **self._runtime_kwargs(),
                                **override_kwargs,
                                **usage_kwargs,
                                on_context_trimmed=_thread_on_context_trimmed,
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
                        asyncio.to_thread(
                            _call_chat_agent,
                            conversation_history,
                            persona_text,
                            cancel_event,
                            **self._runtime_kwargs(),
                            **override_kwargs,
                            **usage_kwargs,
                            on_context_trimmed=_thread_on_context_trimmed,
                        ),
                        timeout=WATCHDOG_TIMEOUT_SECONDS,
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
            except api_provider.RequestCancelledError:
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

    async def start_chat_reply(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        composer_document,
        conversation_history,
        on_reply,
        stream: bool = True,
        canvas_document=None,
        node_id: str | None = None,
        on_partial=None,
        on_usage=None,
        on_begin=None,
        on_end=None,
        state_topic: str | None = None,
    ) -> None:
        # `canvas_document`/`node_id` (R6.1): optional, forwarded straight
        # through to _dispatch for branch-system-prompt-override resolution -
        # see that method's own docstring. Both default None so every
        # pre-R6.1 caller (there are many across test_agents.py) keeps
        # working unchanged, falling back to persona()'s existing
        # resolution.
        #
        # ADR-006 stage 6.4: `on_begin`/`on_end`/`state_topic` become
        # overridable. The defaults keep the Composer identity (its live
        # preview binds stream frames via the app-composer snapshot's
        # request.id) - regenerate_response overrides all three to a
        # NODE-scoped identity (the target node's own pending_request_id,
        # republished on "scene"), which is what lets regenerate stream INTO
        # its node without ever lighting the Composer preview - the exact
        # confusion the old stream=False deferral existed to prevent, now
        # dissolved by giving the frames a different subscriber identity
        # instead of suppressing them. `on_partial` forwards to _dispatch's
        # partial-output preservation (see its docstring).
        return await self._dispatch(
            bus=bus,
            notifications_state=notifications_state,
            conversation_history=conversation_history,
            on_reply=on_reply,
            on_begin=on_begin if on_begin is not None else composer_document.begin_request,
            on_end=on_end if on_end is not None else composer_document.end_request,
            state_topic=state_topic if state_topic is not None else "app-composer",
            stream=stream,
            canvas_document=canvas_document,
            node_id=node_id,
            on_partial=on_partial,
            # ADR-006 stage 6.8: caller-supplied real-usage callback (see
            # _dispatch) - intents_chat wires it to the token counter.
            on_usage=on_usage,
        )

    async def start_conversation_reply(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        node,
        conversation_history,
        on_reply,
        on_partial=None,
    ) -> None:
        """R4.3's ConversationNode equivalent of start_chat_reply: same
        _dispatch pipeline, but the in-flight request_id lives on the
        ConversationNode itself (`node.pending_request_id`, duck-typed - this
        module does not import canvas.py's SceneNode) rather than on
        ComposerDocument, and "scene" (not "app-composer") is republished
        around that change so the node's own in-flight state refreshes.

        ADR-006 stage 6.4: streams. R4.4's deferral is closed - the frames
        are keyed by the request_id on_begin just wrote into
        node.pending_request_id (published on "scene"), which is exactly the
        node-scoped subscription contract CodeSandboxNodeView already
        established, so the Composer preview never lights up for a
        conversation reply. `on_partial` commits accumulated text when the
        stream dies mid-reply (H5)."""
        return await self._dispatch(
            bus=bus,
            notifications_state=notifications_state,
            conversation_history=conversation_history,
            on_reply=on_reply,
            on_begin=lambda request_id: setattr(node, "pending_request_id", request_id),
            on_end=lambda: setattr(node, "pending_request_id", None),
            state_topic="scene",
            stream=True,
            on_partial=on_partial,
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
        spinner). Guarded by self._runs's "image" kind, kept fully SEPARATE
        from chat/conversation's own "chat" kind - see that field's own
        comment in __init__ for why this must stay independent rather than
        reusing the existing single-slot guard.

        ADR-006 stage 6.2: claim() now passes a cancel_event -
        api_provider.generate_image still has no cancellation_event
        parameter and no mid-call checkpoint (it is one blocking network
        POST), so the event's effect is post-return suppression plus
        immediate slot release on cancel/disconnect; it cannot shorten the
        POST itself. (Legacy had zero cancel affordance here at all -
        ImageGenerationWorkerThread.stop() existed but was never called
        from any UI path.) The WATCHDOG_TIMEOUT_SECONDS
        ceiling IS still applied here even though legacy has none for image
        generation - a deliberate, explicitly-flagged improvement (leaving
        this as the only dispatch surface with no ceiling against a hung
        external HTTP call would be an unforced gap, not considered legacy
        design), not silent parity.

        ADR-002 stage 2.4c: migrated onto self._runs (RunRegistry) -
        claim()/release()/attach_task() directly, the same fire-and-forget
        pattern _dispatch's own chat/conversation migration established in
        stage 2.3 (claim SYNCHRONOUSLY in this coroutine, before scheduling
        the background task; release happens inside that task's own
        finally). Not run_single_shot: this surface is fire-and-forget
        (NOT awaited by its own caller), the other of the two dispatch
        shapes that primitive does not cover - see backend/run_lifecycle.py's
        own docstring."""
        if self._runs.is_busy("image"):
            # Single in-flight-image-request-per-session guard, mirroring
            # _dispatch's own "A response is already being generated." guard
            # in shape but tracked on the independent "image" kind, never
            # chat's own.
            notifications_state.show("An image is already being generated.", "info")
            await bus.publish("notification")
            return

        # Claimed SYNCHRONOUSLY, with no `await` between the is_busy() check
        # above and this claim - same load-bearing ordering _dispatch's own
        # claim relies on, see backend/run_lifecycle.py's own docstring.
        # ADR-006 stage 6.2: image gains a cancel_event. generate_image
        # still has no mid-call checkpoint (one blocking POST - the
        # docstring above remains true), so cancellation is post-return
        # suppression, the same shape artifact already uses: cancel frees
        # the slot immediately (RunRegistry.cancel), and when the POST
        # eventually returns, the result is discarded instead of applied.
        cancel_event = threading.Event()
        handle = self._runs.claim("image", cancel_event=cancel_event)
        request_id = handle.request_id

        async def _run():
            try:
                image_bytes = await asyncio.wait_for(
                    asyncio.to_thread(
                        api_provider.generate_image,
                        prompt,
                        # ADR-006 stage 6.5: non-default sessions only - see
                        # _runtime_kwargs' own docstring.
                        **self._runtime_kwargs(),
                    ),
                    timeout=WATCHDOG_TIMEOUT_SECONDS,
                )
                if cancel_event.is_set():
                    return
                if inspect.iscoroutinefunction(on_reply):
                    await on_reply(image_bytes)
                else:
                    on_reply(image_bytes)
                # Unlike _dispatch, "scene" is NOT published here on success -
                # on_reply itself (canvas.py's _dispatch_image._on_reply)
                # already publishes "scene" after mutating the document, so a
                # second unconditional publish here would be redundant.
            except asyncio.TimeoutError:
                if cancel_event.is_set():
                    return  # cancelled runs end quietly, not with a timeout toast
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
                self._runs.release(request_id)

        # NOT awaited here, same load-bearing reason _dispatch's own _run
        # task is not awaited inline - the WS connection's read loop must
        # keep reading further messages on this same socket while a
        # generation is in flight. The claim itself already landed above,
        # before this task was even created.
        self._runs.attach_task(handle, asyncio.create_task(_run()))

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
        _dispatch's generic parameters. Guarded by self._runs's
        "web_research" kind, kept fully SEPARATE from both
        chat/conversation's own "chat" kind and image's own "image" kind -
        see that field's own comment in __init__ for why this must stay
        independent.

        Cooperative cancellation only, via a CancellationToken (not a
        threading.Event, since WebResearchService.run's own pipeline stages
        already accept `token: CancellationToken` - see
        graphlink_plugins/web_research/domain.py) - same honestly-documented
        limitation as existing chat/image dispatch: this does not force-kill
        a call already blocked inside a single blocking call with no
        checkpoint until it returns.

        ADR-002 stage 2.4e: migrated onto self._runs, using RunHandle.
        on_cancel (cancel_token.cancel, a plain bound-method callable) -
        the first surface to actually need it, since CancellationToken has
        no cancel_event-compatible Event to pass instead."""
        if self._runs.is_busy("web_research"):
            notifications_state.show("A web research request is already running.", "info")
            await bus.publish("notification")
            return

        # Claimed SYNCHRONOUSLY, with no `await` between the is_busy()
        # check above and this claim - same load-bearing ordering every
        # other migrated surface's claim relies on, see
        # backend/run_lifecycle.py's own docstring.
        cancel_token = CancellationToken()
        handle = self._runs.claim("web_research", node_id=node_id, on_cancel=cancel_token.cancel)
        request_id = handle.request_id
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
                # finally block has already released its registry claim
                # and cleared node.pending_request_id. Re-check liveness here
                # (on the loop thread, so no race with the release above) and
                # drop the event if this request is no longer the active one
                # - otherwise a stale progress tick can resurrect a
                # since-failed/cancelled node's stage, or clobber a brand
                # new run started on the same node in the meantime.
                if self._runs.get(request_id) is None:
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
                if self._runs.get(request_id) is None:
                    # 6.2 review fix: a cancel popped this handle while the
                    # blocking call was finishing - the same discriminator
                    # _guarded_progress already uses for progress events, now
                    # applied to the TERMINAL callbacks too. Without it, a
                    # cancelled run's late result (or its RequestCancelled
                    # below) writes stale stage/result state onto a node a
                    # replacement run may already own, since release-on-cancel
                    # freed the "web_research" slot the instant cancel landed.
                    return
                await _invoke(on_success, result)
                await bus.publish("scene")
            except asyncio.TimeoutError:
                cancel_token.cancel()
                message = (
                    "Web research stopped responding before the request completed. "
                    "Please try again."
                )
                if self._runs.get(request_id) is not None:
                    await _invoke(on_failure, ResearchFailure(message, code="watchdog_timeout"))
                notifications_state.show(message, "error")
                await bus.publish("notification")
                await bus.publish("scene")
            except RequestCancelled as exc:
                if self._runs.get(request_id) is not None:
                    await _invoke(on_failure, exc)
                notifications_state.show("Web research cancelled.", "info")
                await bus.publish("notification")
                await bus.publish("scene")
            except ResearchFailure as exc:
                if self._runs.get(request_id) is not None:
                    await _invoke(on_failure, exc)
                notifications_state.show(f"Web research failed: {exc}", "error")
                await bus.publish("notification")
                await bus.publish("scene")
            except Exception as exc:
                logger.exception("web research dispatch failed")
                if self._runs.get(request_id) is not None:
                    await _invoke(on_failure, exc)
                notifications_state.show(f"Web research failed: {exc}", "error")
                await bus.publish("notification")
                await bus.publish("scene")
            finally:
                self._runs.release(request_id)
                # 6.2 review fix: same stale-task guard as artifact/gitlink -
                # a cancelled run's late unwind must not wipe a replacement
                # run's in-flight marker on this same node.
                if node.pending_request_id == request_id:
                    node.pending_request_id = None
                await bus.publish("scene")

        self._runs.attach_task(handle, asyncio.create_task(_run()))

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
        mutation callback fires. Guarded by self._runs's "artifact" kind,
        kept fully SEPARATE from chat/conversation's own "chat" kind,
        image's own "image" kind, and web_research's own "web_research"
        kind - see that field's own comment in __init__ for why this must
        stay independent.

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
        here.

        ADR-002 stage 2.4d: migrated onto self._runs - claim()/release()/
        attach_task() directly, the exact same fire-and-forget pattern
        chat's own _dispatch and image's own start_image_reply already
        use. node.pending_request_id below is set inside _run() itself,
        AFTER the claim already landed in this outer coroutine - it is a
        UI-bookkeeping side channel only (never consulted for the busy
        guard, unlike gitlink_run/pycoder/code_sandbox's use of the same
        field), so it needs no claim-ordering treatment of its own."""
        if self._runs.is_busy("artifact"):
            notifications_state.show("An artifact request is already running.", "info")
            await bus.publish("notification")
            return

        # Claimed SYNCHRONOUSLY, with no `await` between the is_busy()
        # check above and this claim - same load-bearing ordering
        # _dispatch's/start_image_reply's own claims rely on, see
        # backend/run_lifecycle.py's own docstring.
        cancel_event = threading.Event()
        handle = self._runs.claim("artifact", node_id=getattr(node, "id", None), cancel_event=cancel_event)
        request_id = handle.request_id

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
                if cancel_event.is_set():
                    # 6.2 review fix: a cancelled run whose provider call then
                    # errors ends quietly - the user already asked for it to
                    # stop; an "Artifact generation failed" toast would be
                    # noise about work they abandoned.
                    return
                logger.exception("artifact dispatch failed")
                notifications_state.show(f"Artifact generation failed: {exc}", "error")
                await bus.publish("notification")
            finally:
                self._runs.release(request_id)
                # 6.2 review fix (reproduced live): only clear if this task's
                # OWN request_id is still the one recorded - the same
                # stale-task guard every gitlink/pycoder/sandbox finally has.
                # Release-on-cancel frees the "artifact" slot the instant a
                # cancel lands, so a NEW artifact run can claim and stamp
                # this same node before this old worker unwinds; the old
                # unconditional clear wiped the new run's in-flight marker.
                if node.pending_request_id == request_id:
                    node.pending_request_id = None
                await bus.publish("scene")

        self._runs.attach_task(handle, asyncio.create_task(_run()))

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
                    repo=node.state.gitlink_repo,
                    branch=node.state.gitlink_branch,
                    scope_mode=scope_mode,
                    selected_paths=selected_paths,
                    repo_file_paths=list(node.state.gitlink_repo_file_paths),
                    local_root_hint=node.state.gitlink_local_root,
                    imported_root_hint=node.state.gitlink_imported_root,
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
        could slip past the busy check above.

        ADR-002 stage 2.4f: self._runs.claim() now happens in that SAME
        synchronous stretch, immediately alongside node.pending_request_id's
        own claim - not at the old dict-literal write site (which sat
        after this method's first `await`, see backend/run_lifecycle.py's
        own docstring for why that would reopen a race for a kind that DID
        need one). Unlike every prior migrated kind, is_busy("gitlink_run")
        is never checked anywhere - see this field's own comment in
        __init__ for why node.pending_request_id remains the sole real
        guard, this registry claim exists purely to carry cancel_event/task
        bookkeeping into the shared cancel()/cancel_all() sweep."""
        if node.pending_request_id and node.pending_request_id != _GITLINK_RUN_CLAIM_PLACEHOLDER:
            notifications_state.show("Gitlink is already busy for this node.", "info")
            await bus.publish("notification")
            return

        cancel_event = threading.Event()
        handle = self._runs.claim("gitlink_run", node_id=node_id, cancel_event=cancel_event)
        request_id = handle.request_id
        node.pending_request_id = request_id
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
                self._runs.release(request_id)
                # R5.3 post-review FIX 4(c): only clear if this task's OWN
                # request_id is still the one recorded - a stale,
                # already-superseded task finishing late must never clobber
                # a newer legitimate busy marker.
                if node.pending_request_id == request_id:
                    node.pending_request_id = None
                await bus.publish("scene")

        self._runs.attach_task(handle, asyncio.create_task(_run()))

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
        (not merely unlikely) for node.state.gitlink_pending_changes to be
        mutated between the recompute and the freeze immediately after it. This is a
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
        permanently stuck "busy".

        ADR-002 stage 2.4f: self._runs.claim() now happens in that SAME
        synchronous stretch as node.pending_request_id's own claim, for
        the same reason start_gitlink_run's own claim moved there (see
        this field's own comment in __init__: node.pending_request_id
        remains the sole real busy guard, this registry claim is pure
        task bookkeeping). Consequently EVERY one of the 5 early-return
        branches below - which already clear node.pending_request_id
        before returning - must ALSO release this registry claim, or it
        leaks forever on every rejected Apply (none of those branches
        ever reach _run()'s own finally, the only other place a release
        happens)."""
        if node.pending_request_id:
            notifications_state.show("Gitlink is already busy for this node.", "info")
            await bus.publish("notification")
            return

        # ADR-006 stage 6.2: gitlink_apply gains a cancel_event, checked at
        # the worker's ENTRY only (below, before any file is written) - once
        # writing begins the apply deliberately runs to completion, because
        # stopping between file writes would leave the working tree in a
        # half-applied state the UI has no way to represent. So cancel (and
        # session-disconnect cancel_all) covers the queued-but-not-started
        # window and frees the slot immediately; a mid-write apply finishes.
        cancel_event = threading.Event()
        handle = self._runs.claim("gitlink_apply", node_id=node_id, cancel_event=cancel_event)
        request_id = handle.request_id
        node.pending_request_id = request_id

        if not node.state.gitlink_pending_changes:
            node.pending_request_id = None
            self._runs.release(request_id)
            on_failure("There is no approved change set to write.")
            await bus.publish("scene")
            return

        local_root_text = (local_root or "").strip()
        if not local_root_text:
            node.pending_request_id = None
            self._runs.release(request_id)
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
            self._runs.release(request_id)
            on_failure("The selected local repository path does not exist.")
            await bus.publish("scene")
            return

        # --- Atomic check-and-freeze: NO await between these statements. ---
        current_fingerprint = _fingerprint_changes(node.state.gitlink_pending_changes)
        if (
            client_fingerprint != current_fingerprint
            or current_fingerprint != node.state.gitlink_change_fingerprint
        ):
            node.pending_request_id = None
            self._runs.release(request_id)
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
        if local_root_text != (node.state.gitlink_change_local_root or ""):
            node.pending_request_id = None
            self._runs.release(request_id)
            on_failure(
                "The local repository path changed since this proposal was generated. "
                "Regenerate the change set before applying."
            )
            await bus.publish("scene")
            return
        changes_snapshot = [dict(item) for item in node.state.gitlink_pending_changes]
        # --- End atomic section. Everything past this point operates ONLY on
        # changes_snapshot, never on node.state.gitlink_pending_changes again. ---

        # R5.3 post-review FIX 5: request_id was already generated and
        # claimed into node.pending_request_id right after the busy check
        # above - NOT re-generated here. Only the change_state transition and
        # publish happen at this point now.
        node.state.gitlink_change_state = "applying"
        await bus.publish("scene")

        async def _run():
            try:
                if cancel_event.is_set():
                    # Cancelled before any file was written (see the claim's
                    # own comment) - report it on the node rather than leaving
                    # "applying" stuck.
                    on_failure("Apply cancelled before any files were written.")
                    return
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
                self._runs.release(request_id)
                # R5.3 post-review FIX 4(c): only clear if this task's OWN
                # request_id is still the one recorded - same stale-task
                # guard as start_gitlink_run's own finally block above.
                if node.pending_request_id == request_id:
                    node.pending_request_id = None
                await bus.publish("scene")

        self._runs.attach_task(handle, asyncio.create_task(_run()))

    # -- R5.4: Py-Coder / Execution Sandbox -----------------------------------
    #
    # SECURITY BOUNDARY (stated plainly, not softened): PyCoderNode and
    # CodeSandboxNode execute code with the full privileges of the user's
    # account. The only two protections are the WS-Origin handshake check
    # and a mandatory human-approval step. There is no code-level sandbox -
    # no container, VM, or OS-level resource/permission restriction - for
    # either kind. Py-Coder's new execution timeout is a hang guard, not a
    # security control: it does not stop a malicious script from reading
    # files, exfiltrating data, or (for Execution Sandbox specifically)
    # running arbitrary code during pip install via a hostile package's
    # build backend, before the approved script itself ever runs.
    #
    # Both methods below run their entire pipeline as ONE coroutine on the
    # event loop - the blocking LLM/REPL/subprocess calls are wrapped in
    # asyncio.to_thread, but the PAUSE between them (waiting for a human to
    # approve or deny the candidate code) needs no thread-crossing at all: it
    # collapses into a plain `asyncio.Future[bool]`
    # (self._runs's "pycoder"/"code_sandbox" handle's own approval_future
    # field), created BEFORE the background task even starts. `approved = await
    # approval_future` IS the entire "waiting for approval" state - nothing
    # else is needed. This replaces legacy's two independently-blocking
    # mechanisms on two different threads (a QThread worker parked on a
    # threading.Event, the GUI thread parked inside a modal
    # QMessageBox.exec()), coordinated only through the shared worker object.

    async def start_pycoder_run(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        node,
        node_id: str,
        mode: str,
        prompt: str,
        code: str,
        conversation_history: list,
        on_success,  # on_success(code, output, analysis, last_run_failed)
        on_failure,  # on_failure(message)
    ) -> None:
        """R5.4: Py-Coder's Run action.

        ai_driven mode mirrors legacy's PyCoderExecutionWorker: generate code
        via PyCoderExecutionAgent -> human-approval pause -> execute in the
        persistent REPL with up to 4 attempts, repairing via
        PyCoderRepairAgent between failures -> analyze the final result via
        PyCoderAnalysisAgent. A successful run through the repair loop AND a
        run that exhausts every retry both call on_success (never
        on_failure) - exactly mirroring legacy's own `finished.emit(result)`
        for both cases, distinguished only by the `last_run_failed` flag and
        a "**PROCESS FAILED**" analysis prefix.

        manual mode mirrors legacy's CodeExecutionWorker + PyCoderAgentWorker
        pair: execute the hand-typed code once (no repair loop), then
        analyze the result. Deliberately ungated - no approval_future is
        awaited on this path at all, mirroring legacy's own documented
        posture exactly ("MANUAL mode is deliberately ungated - there the
        user authored the code themselves and clicking Run *is* the
        approval").

        Every execute() call, on both paths, is wrapped in
        asyncio.wait_for(..., timeout=PYCODER_EXECUTE_TIMEOUT_SECONDS) - the
        one real asymmetry recon found versus Execution Sandbox (which
        already self-limits via VirtualEnvSandbox.execute_code's own baked-in
        timeout). On timeout, the REPL is torn down via dispose_pycoder_repl
        rather than left alive as a runaway subprocess.

        Cooperative cancellation only for the EXECUTE stage itself (same
        honestly-documented limitation as gitlink/artifact/web_research: the
        checkpoint is a cancel_event check between stages, not a true
        mid-call interrupt on an in-flight REPL execute() - the REPL has no
        polling hook the way Execution Sandbox's subprocess does) - but the
        approval PAUSE itself is genuinely, immediately interruptible by
        Cancel, since cancel_pycoder resolves this same approval_future.

        ADR-002 stage 2.4g: self._runs.claim() now happens in the SAME
        synchronous stretch as node.pending_request_id's own claim - same
        pattern as gitlink_run/gitlink_apply (stage 2.4f): node.pending_
        request_id remains the sole real busy guard, this registry claim
        is pure task/cancel_event/approval_future bookkeeping."""
        if node.pending_request_id and node.pending_request_id != _CODE_EXEC_RUN_CLAIM_PLACEHOLDER:
            notifications_state.show("Py-Coder is already busy for this node.", "info")
            await bus.publish("notification")
            return

        cancel_event = threading.Event()
        approval_future: asyncio.Future = asyncio.get_running_loop().create_future()
        handle = self._runs.claim(
            "pycoder", node_id=node_id, cancel_event=cancel_event, approval_future=approval_future
        )
        request_id = handle.request_id
        node.pending_request_id = request_id
        await bus.publish("scene")

        async def _run():
            try:
                if mode == "manual":
                    manual_code = code or ""
                    if not manual_code.strip():
                        # Guard-rail message, routed through pycoder_error
                        # (not pycoder_analysis, unlike legacy's own
                        # `set_ai_analysis`) - see the R5.4 report's own note
                        # on unifying every guard-rail message through the
                        # one error field this port actually has.
                        on_failure("Add Python code before running Py-Coder.")
                        await bus.publish("scene")
                        return

                    repl = self.get_pycoder_repl(node_id, node.state.pycoder_repl_id)
                    try:
                        output = await asyncio.wait_for(
                            asyncio.to_thread(repl.execute, manual_code),
                            timeout=PYCODER_EXECUTE_TIMEOUT_SECONDS,
                        )
                    except asyncio.TimeoutError:
                        await self.dispose_pycoder_repl(node_id)
                        message = (
                            "Py-Coder execution stopped responding before the request "
                            "completed and was terminated. Please try again."
                        )
                        on_failure(message)
                        notifications_state.show(message, "error")
                        await bus.publish("notification")
                        return

                    if cancel_event.is_set():
                        notifications_state.show("Py-Coder execution cancelled.", "info")
                        await bus.publish("notification")
                        return

                    last_run_failed = getattr(repl, "last_run_failed", False)
                    output_text = output if output else "[No output produced]"
                    analysis = await asyncio.to_thread(
                        _call_pycoder_analysis_agent, None, manual_code, output_text
                    )
                    on_success(manual_code, output_text, analysis, last_run_failed)
                    await bus.publish("scene")
                    return

                # ai_driven mode
                prompt_text = (prompt or "").strip()
                if not prompt_text:
                    on_failure("Please enter a prompt.")
                    await bus.publish("scene")
                    return

                initial_response = await asyncio.to_thread(
                    _call_pycoder_execution_agent, conversation_history, prompt_text
                )
                if cancel_event.is_set():
                    notifications_state.show("Py-Coder run cancelled.", "info")
                    await bus.publish("notification")
                    return

                code_match = re.search(r"\[TOOL:PYTHON\](.*?)\[/TOOL\]", initial_response, re.DOTALL)
                if not code_match:
                    # No code needed for this prompt - a real completed run
                    # (never executed the REPL, never gated on approval),
                    # exactly mirroring legacy's own `finished.emit(result)`
                    # for this branch.
                    on_success(
                        "# No code was generated for this prompt.",
                        "[Not applicable]",
                        initial_response,
                        False,
                    )
                    await bus.publish("scene")
                    return

                current_code = code_match.group(1).strip()

                # -- human-approval gate --------------------------------------
                node.state.pycoder_code = current_code
                node.state.pycoder_approved_fingerprint = _fingerprint_changes({"code": current_code})
                node.state.pycoder_awaiting_approval = True
                await bus.publish("scene")
                approved = await approval_future
                node.state.pycoder_awaiting_approval = False

                if not approved:
                    on_failure("Py-Coder run cancelled: execution was not approved.")
                    await bus.publish("scene")
                    return

                repl = self.get_pycoder_repl(node_id, node.state.pycoder_repl_id)
                retry_count = 0
                max_retries = 4
                last_error = None

                while retry_count < max_retries:
                    if cancel_event.is_set():
                        notifications_state.show("Py-Coder execution cancelled.", "info")
                        await bus.publish("notification")
                        return

                    # ADR-002 P0: defense-in-depth, not the primary fix (the
                    # repair re-gate below is) - the code about to execute
                    # must be EXACTLY what the most recently resolved
                    # approval gate covered. Always true today (nothing
                    # mutates current_code between a gate and its matching
                    # execute call); this exists to fail loudly rather than
                    # silently execute unapproved content if a future change
                    # ever breaks that invariant.
                    if _fingerprint_changes({"code": current_code}) != node.state.pycoder_approved_fingerprint:
                        on_failure(
                            "Py-Coder execution blocked: the approved code no longer matches what is about to run."
                        )
                        await bus.publish("scene")
                        return

                    try:
                        execution_output = await asyncio.wait_for(
                            asyncio.to_thread(repl.execute, current_code),
                            timeout=PYCODER_EXECUTE_TIMEOUT_SECONDS,
                        )
                        execution_failed = getattr(repl, "last_run_failed", False)
                    except asyncio.TimeoutError:
                        await self.dispose_pycoder_repl(node_id)
                        message = (
                            "Py-Coder execution stopped responding before the request "
                            "completed and was terminated. Please try again."
                        )
                        on_failure(message)
                        notifications_state.show(message, "error")
                        await bus.publish("notification")
                        return
                    except Exception as exc:
                        execution_output = f"\n--- EXECUTION FAILED ---\n{type(exc).__name__}: {exc}"
                        execution_failed = True

                    if not execution_failed:
                        output_text = execution_output if execution_output else "[No output produced]"
                        analysis = await asyncio.to_thread(
                            _call_pycoder_analysis_agent, prompt_text, current_code, execution_output
                        )
                        on_success(current_code, output_text, analysis, False)
                        await bus.publish("scene")
                        return

                    last_error = execution_output
                    retry_count += 1
                    if retry_count < max_retries:
                        is_final = retry_count == max_retries - 1
                        current_code = await asyncio.to_thread(
                            _call_pycoder_repair_agent, current_code, last_error, is_final
                        )
                        if cancel_event.is_set():
                            notifications_state.show("Py-Coder execution cancelled.", "info")
                            await bus.publish("notification")
                            return

                        # ADR-002 P0: the repair agent just produced code the
                        # user has never seen. The prior design let this run
                        # automatically under the FIRST Approve click - the
                        # confirmed gap the ADR-002 security review named
                        # explicitly ("approval is not bound to what was
                        # shown"; the old warning copy even disclosed this:
                        # "automatically repaired versions of this code may
                        # run under this same approval"). Every repaired
                        # variant now goes through its own fresh gate, with a
                        # NEW Future replacing the resolved one on this same
                        # handle so cancel_pycoder/approve_code_execution/
                        # deny_code_execution keep targeting whichever gate
                        # is actually still open (see _resolve_approval's own
                        # docstring - it always re-reads this field fresh,
                        # never caches the future). The liveness re-check
                        # (self._runs.get(request_id) is None) mirrors the
                        # pre-migration dict-membership check exactly - see
                        # start_web_research's own _guarded_progress for the
                        # same "was this released out from under me" pattern.
                        if self._runs.get(request_id) is None:
                            return
                        repair_future: asyncio.Future = asyncio.get_running_loop().create_future()
                        handle.approval_future = repair_future
                        node.state.pycoder_code = current_code
                        node.state.pycoder_approved_fingerprint = _fingerprint_changes({"code": current_code})
                        node.state.pycoder_awaiting_approval = True
                        await bus.publish("scene")
                        approved = await repair_future
                        node.state.pycoder_awaiting_approval = False
                        if not approved:
                            on_failure("Py-Coder run cancelled: repaired code was not approved.")
                            await bus.publish("scene")
                            return

                # Every retry exhausted - still a real completed run (never
                # on_failure), matching legacy's own `finished.emit(result)`
                # for the exhausted-repair-loop case, flagged via
                # last_run_failed=True and a "**PROCESS FAILED**" prefix.
                final_failure_analysis = await asyncio.to_thread(
                    _call_pycoder_analysis_agent,
                    prompt_text,
                    current_code,
                    f"The code failed to execute after {max_retries} attempts. The final error was:\n{last_error}",
                )
                combined_analysis = (
                    f"**PROCESS FAILED**\n\nAfter {max_retries} attempts, the code could not "
                    f"be successfully executed.\n\n{final_failure_analysis}"
                )
                on_success(current_code, last_error, combined_analysis, True)
                await bus.publish("scene")
            except Exception as exc:
                logger.exception("pycoder dispatch failed")
                on_failure(f"Py-Coder execution failed: {exc}")
                notifications_state.show(f"Py-Coder execution failed: {exc}", "error")
                await bus.publish("notification")
            finally:
                self._runs.release(request_id)
                if node.pending_request_id == request_id:
                    node.pending_request_id = None
                await bus.publish("scene")

        self._runs.attach_task(handle, asyncio.create_task(_run()))

    async def start_code_sandbox_run(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        node,
        node_id: str,
        sandbox_id: str,
        prompt: str,
        existing_code: str,
        requirements_manifest: str,
        conversation_history: list,
        on_success,  # on_success(code, output, analysis)
        on_failure,  # on_failure(message)
    ) -> None:
        """R5.4: Execution Sandbox's Run action - mirrors legacy's
        CodeSandboxExecutionWorker (generate-or-reuse -> human-approval pause
        -> prepare venv -> install requirements -> execute-with-repair-loop
        -> analyze), collapsed into one coroutine via the same
        asyncio.Future approval-pause mechanism as start_pycoder_run above
        (see that method's own docstring).

        UNLIKE Py-Coder, there is no persisted mode field - the real branch
        is resolved HERE, at call time: a non-blank prompt always means
        "generate" (regenerating ignores any existing code, mirrors
        legacy's own `existing_code = code if run_mode == "manual" else
        ""`); a blank prompt with existing code means "reuse the existing
        code as-is, skip generation entirely"; a blank prompt with no
        existing code is a guard-rail failure, exactly matching legacy's own
        CodeSandboxExecutionWorker.run() top-of-function check.

        A fresh VirtualEnvSandbox is constructed HERE, per run (never
        cached/reused on the dispatcher) - the only state that must survive
        between runs is the plain string sandbox_id (real SceneNode state,
        not a live object), exactly like _call_gitlink_agent constructing a
        fresh GitlinkAgent per call.

        Cancellation is MORE effective here than Py-Coder's own REPL-based
        cancel: VirtualEnvSandbox._run_subprocess polls `should_continue()`
        (wired to `not cancel_event.is_set()`) roughly every 100ms while its
        subprocess is running, and genuinely terminates it via self.stop()
        the instant that check fails - a real, near-immediate interrupt, not
        merely a "checked between stages" limitation. This mirrors legacy's
        own already-working stop() behavior; it is not a new capability
        introduced by this port. VirtualEnvSandbox.execute_code's own
        baked-in 240s timeout (unchanged - see graphlink_plugins/
        code_sandbox/domain.py) is what actually bounds a hung subprocess
        that never checks should_continue on its own; PYCODER_EXECUTE_
        TIMEOUT_SECONDS reuses that same number for Py-Coder's own,
        previously-missing equivalent.

        R5.4 post-review FIX 1: live output streaming. VirtualEnvSandbox's
        `ensure_base_environment`/`sync_requirements`/`execute_code` each
        already accept an `emit_line` callback (see graphlink_plugins/
        code_sandbox/domain.py's own `_run_subprocess`) - invoked once per
        line of subprocess stdout/stderr, on the WORKER THREAD inside
        asyncio.to_thread. `_thread_emit_line` below hands each line to the
        event loop the same load-bearing way `_dispatch`'s own
        `_thread_on_chunk` does (`loop.call_soon_threadsafe(...)` feeding an
        `asyncio.Queue` - the only safe way to cross that thread boundary;
        `bus`/the queue itself are never touched directly from the worker
        thread). UNLIKE `_dispatch`'s own `_pump`, there is deliberately NO
        batching/flush-interval machinery here - R5.1's web-research
        increment already made this exact call for its own low-frequency
        progress channel ("too sparse to justify it"), and this channel is
        the same shape: one `bus.publish_stream(...)` call per subprocess
        line, in order, not a 15-17Hz token stream. A final `done=True` frame
        is always sent last, from the shared `finally` below, so it fires on
        EVERY exit path (guard-rail failure, no-code-generated, denied
        approval, cancelled, timed-out, or a real success) - mirroring
        `_dispatch`'s own "unconditional final flush on every exit path"
        guarantee for its own stream. `topic="scene"` (not a
        Composer-specific topic): CodeSandboxNode state is scene state, same
        as every other plugin node kind's own dispatch surface.

        ADR-002 stage 2.4g: shares the same self._runs claim pattern as
        start_pycoder_run above - node.pending_request_id remains the sole
        real busy guard, this registry claim is pure task/cancel_event/
        approval_future bookkeeping."""
        if node.pending_request_id and node.pending_request_id != _CODE_EXEC_RUN_CLAIM_PLACEHOLDER:
            notifications_state.show("Virtual Environment Runner is already busy for this node.", "info")
            await bus.publish("notification")
            return

        cancel_event = threading.Event()
        approval_future: asyncio.Future = asyncio.get_running_loop().create_future()
        # ADR-005 stage 5.5 review-fix: see RunHandle.approval_snapshot_fn's
        # own doc for the race this closes - _resolve_approval calls this
        # synchronously, atomically with future.set_result(), instead of
        # this coroutine re-reading node.state after resuming.
        handle = self._runs.claim(
            "code_sandbox",
            node_id=node_id,
            cancel_event=cancel_event,
            approval_future=approval_future,
            approval_snapshot_fn=lambda: node.state.code_sandbox_approval_allow_source_builds,
        )
        request_id = handle.request_id
        node.pending_request_id = request_id
        await bus.publish("scene")

        def _should_continue() -> bool:
            return not cancel_event.is_set()

        async def _run():
            loop = asyncio.get_running_loop()
            line_queue: asyncio.Queue = asyncio.Queue()
            _STREAM_DONE = object()
            stream_seq = 0

            def _thread_emit_line(line: str) -> None:
                # Runs on the WORKER THREAD inside asyncio.to_thread - never
                # touch `line_queue`/`bus` directly here, only via
                # call_soon_threadsafe (see this method's own docstring).
                loop.call_soon_threadsafe(line_queue.put_nowait, line)

            async def _drain_stream() -> None:
                nonlocal stream_seq
                while True:
                    item = await line_queue.get()
                    if item is _STREAM_DONE:
                        break
                    await bus.publish_stream(
                        topic="scene", request_id=request_id, seq=stream_seq, delta=item, done=False,
                    )
                    stream_seq += 1
                # Guaranteed final frame, unconditional and always last - see
                # the `finally` below that always queues _STREAM_DONE before
                # awaiting this task, on EVERY exit path.
                await bus.publish_stream(
                    topic="scene", request_id=request_id, seq=stream_seq, delta="", done=True,
                )

            drain_task = asyncio.create_task(_drain_stream())
            try:
                prompt_text = (prompt or "").strip()
                manifest = _normalize_requirements(requirements_manifest or "")
                current_code = (existing_code or "").strip()

                if prompt_text:
                    initial_response = await asyncio.to_thread(
                        _call_sandbox_generation_agent, conversation_history, prompt_text, manifest
                    )
                    if cancel_event.is_set():
                        notifications_state.show("Sandbox execution cancelled.", "info")
                        await bus.publish("notification")
                        return
                    extracted = _extract_python_block(initial_response)
                    if not extracted:
                        on_success(
                            "# No Python code was generated for this request.",
                            "[Sandbox was not executed]",
                            initial_response,
                        )
                        await bus.publish("scene")
                        return
                    current_code = extracted
                elif not current_code:
                    on_failure("Provide a task prompt or Python code before running the sandbox.")
                    await bus.publish("scene")
                    return

                # -- human-approval gate --------------------------------------
                node.state.code_sandbox_code = current_code
                node.state.code_sandbox_awaiting_approval = True
                # ADR-005 stage 5.5: reset the source-build opt-in to its
                # safe default every time a gate opens - a stale True from a
                # previous run's approval must never silently carry forward
                # into one the user has not actually reviewed. See
                # CodeSandboxState.code_sandbox_approval_allow_source_builds's
                # own comment.
                node.state.code_sandbox_approval_allow_source_builds = False
                # ADR-005 stage 5.5 review-fix: this is the INITIAL gate, not
                # a repair re-gate - see the repair re-gate's own identical
                # write, further down this function, for what this flag is
                # for.
                node.state.code_sandbox_approval_is_repair = False
                # R5.4 CODESANDBOX FIX (closing the requirements-disclosure
                # staleness race): freeze the DISCLOSED manifest into its own
                # snapshot field at the exact same moment the approval gate
                # opens, using `manifest` - already computed above, at the
                # top of this function, before this function's own
                # generation-agent await. This introduces no new race: it
                # only exposes a value already correctly frozen, never
                # re-reading node.state.code_sandbox_requirements (the user's
                # still-live, still-editable draft for the NEXT run) at this
                # point. See CodeSandboxState.code_sandbox_approval_
                # requirements's own comment for the full race this closes.
                node.state.code_sandbox_approval_requirements = manifest
                # ADR-002 P0: fingerprints exactly what this gate is asking
                # about - see CodeSandboxState.code_sandbox_approved_
                # fingerprint's own comment. Frozen from the same
                # already-correct local `manifest`/`current_code`, at the
                # same moment, for the same staleness-avoidance reason as the
                # requirements snapshot right above.
                node.state.code_sandbox_approved_fingerprint = _fingerprint_changes(
                    {"code": current_code, "manifest": manifest}
                )
                await bus.publish("scene")
                approved = await approval_future
                # ADR-005 stage 5.5 review-fix: read the ALREADY-SNAPSHOTTED
                # value off the handle, never node.state here. An earlier
                # version of this line re-read node.state.code_sandbox_
                # approval_allow_source_builds right after the await, which
                # looked safe (no await in between) but wasn't: this
                # coroutine only resumes once the event loop gets around to
                # it after _resolve_approval's future.set_result() call
                # (backend/agents.py) merely SCHEDULES that resumption - a
                # second WS connection's setCodeSandboxAllowSourceBuilds
                # could fully land in that scheduling gap. handle.
                # approval_snapshot was instead captured by _resolve_approval
                # itself, atomically with future.set_result(), in a
                # synchronous stretch nothing else can interleave with - see
                # RunHandle.approval_snapshot_fn's own doc (backend/
                # run_lifecycle.py) for the full race this closes.
                allow_source_builds = bool(handle.approval_snapshot)
                node.state.code_sandbox_awaiting_approval = False
                # Cleared here too, immediately once the approval resolves -
                # mirrors code_sandbox_awaiting_approval's own clear on this
                # exact line (and canvas.py's complete_code_sandbox_run/
                # fail_code_sandbox_run clear it again downstream, redundant
                # but harmless, for every other path that lands there).
                node.state.code_sandbox_approval_requirements = ""
                node.state.code_sandbox_approval_allow_source_builds = False

                if not approved:
                    on_failure("Sandbox run cancelled: execution was not approved.")
                    await bus.publish("scene")
                    return

                sandbox = VirtualEnvSandbox(sandbox_id)
                try:
                    await asyncio.to_thread(
                        sandbox.ensure_base_environment, _should_continue, _thread_emit_line
                    )
                    await asyncio.to_thread(
                        sandbox.sync_requirements,
                        manifest,
                        _should_continue,
                        _thread_emit_line,
                        allow_source_builds,
                    )
                except InterruptedError:
                    notifications_state.show("Sandbox execution cancelled.", "info")
                    await bus.publish("notification")
                    return

                max_attempts = 3
                final_output = ""
                final_return_code = 0
                last_error = ""
                try:
                    for attempt_index in range(max_attempts):
                        # ADR-002 P0: defense-in-depth, not the primary fix
                        # (the repair re-gate below is) - see
                        # start_pycoder_run's identical check for the full
                        # reasoning.
                        if _fingerprint_changes(
                            {"code": current_code, "manifest": manifest}
                        ) != node.state.code_sandbox_approved_fingerprint:
                            on_failure(
                                "Sandbox execution blocked: the approved code no longer matches what is about to run."
                            )
                            await bus.publish("scene")
                            return
                        final_output, final_return_code = await asyncio.to_thread(
                            sandbox.execute_code, current_code, _should_continue, _thread_emit_line
                        )
                        if not _is_sandbox_error_output(final_output, final_return_code):
                            break
                        last_error = final_output or "The sandbox process exited with an error."
                        if attempt_index == max_attempts - 1:
                            break
                        current_code = await asyncio.to_thread(
                            _call_sandbox_repair_agent, current_code, last_error, manifest, prompt_text or None
                        )
                        if not _should_continue():
                            notifications_state.show("Sandbox execution cancelled.", "info")
                            await bus.publish("notification")
                            return

                        # ADR-002 P0: same reasoning as start_pycoder_run's
                        # identical repair re-gate - the repair agent just
                        # produced code the user has never seen, so it must
                        # not run under the approval that only ever covered
                        # the FIRST version. Re-disclose the (unchanged)
                        # manifest alongside it, since code_sandbox_approval_
                        # requirements was already cleared once the initial
                        # gate resolved above. Liveness re-check mirrors
                        # start_pycoder_run's own (self._runs.get(request_id)
                        # is None) - see that method's own comment.
                        if self._runs.get(request_id) is None:
                            return
                        repair_future: asyncio.Future = asyncio.get_running_loop().create_future()
                        handle.approval_future = repair_future
                        node.state.code_sandbox_code = current_code
                        node.state.code_sandbox_approval_requirements = manifest
                        node.state.code_sandbox_approved_fingerprint = _fingerprint_changes(
                            {"code": current_code, "manifest": manifest}
                        )
                        # ADR-005 stage 5.5 review-fix: an earlier version of
                        # this re-gate left code_sandbox_approval_allow_
                        # source_builds untouched here, reasoning it was
                        # "still False from the initial gate's own clear
                        # above" - that assumption was never actually
                        # enforced: setCodeSandboxAllowSourceBuilds is
                        # ungated and could set it True during the execute/
                        # repair window (nothing here was awaiting_approval,
                        # so nothing rejected the intent), leaving the repair
                        # panel's checkbox rendered CHECKED without the user
                        # having touched it this round - a real, adversarial-
                        # review-confirmed contradiction of this field's own
                        # documented "reset on every gate-open" invariant.
                        # Reset explicitly here, matching the initial gate's
                        # own reset, even though sync_requirements is never
                        # called again on a repair round (so this has no
                        # install-level effect) - the value must still be
                        # honest about being reset, not just impotent.
                        node.state.code_sandbox_approval_allow_source_builds = False
                        # ADR-005 stage 5.5 review-fix: distinguishes "this
                        # panel's checkbox reflects a decision that can still
                        # affect an install" (initial gate) from "the install
                        # already happened, checking this now does nothing"
                        # (any repair gate) - CodeExecutionApprovalPanel.tsx
                        # uses this to hide the otherwise-genuinely-inert
                        # checkbox on repair rounds rather than let a user
                        # take an action that silently has no effect.
                        node.state.code_sandbox_approval_is_repair = True
                        node.state.code_sandbox_awaiting_approval = True
                        await bus.publish("scene")
                        repair_approved = await repair_future
                        node.state.code_sandbox_awaiting_approval = False
                        node.state.code_sandbox_approval_requirements = ""
                        if not repair_approved:
                            on_failure("Sandbox run cancelled: repaired code was not approved.")
                            await bus.publish("scene")
                            return
                    else:
                        # Structurally unreachable (mirrors legacy's own
                        # identical dead `else` branch - every loop path
                        # above ends in an explicit `break`), kept for exact
                        # structural parity rather than optimized away.
                        final_output = final_output or last_error
                except InterruptedError:
                    notifications_state.show("Sandbox execution cancelled.", "info")
                    await bus.publish("notification")
                    return

                output_text = final_output if final_output else "[No output produced]"
                analysis = await asyncio.to_thread(
                    _call_pycoder_analysis_agent, prompt_text or None, current_code, output_text
                )
                on_success(current_code, output_text, analysis)
                await bus.publish("scene")
            except Exception as exc:
                logger.exception("code sandbox dispatch failed")
                on_failure(f"Sandbox execution failed: {exc}")
                notifications_state.show(f"Sandbox execution failed: {exc}", "error")
                await bus.publish("notification")
            finally:
                self._runs.release(request_id)
                if node.pending_request_id == request_id:
                    node.pending_request_id = None
                line_queue.put_nowait(_STREAM_DONE)
                await drain_task
                await bus.publish("scene")

        self._runs.attach_task(handle, asyncio.create_task(_run()))

    # -- R6.2: Chart node -----------------------------------------------------

    async def start_chart_generation(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        node_id: str,
        chart_type: str,
        source_text: str,
        on_success,
        on_failure,
    ) -> None:
        """R6.2: Chart generation - DIRECTLY AWAITED by its caller
        (backend/canvas.py's generateChart), NOT scheduled via
        asyncio.create_task the way start_image_reply/start_web_research/
        start_artifact_reply/start_gitlink_run above are. Those four are all
        fire-and-forget precisely because generation there fills in an
        ALREADY-EXISTING node while the WS connection's read loop moves on
        to keep reading further messages - but generateChart's own contract
        is a single combined create+generate action with no pre-existing
        node at all: the chart SceneNode is only ever created inside
        on_success below, so the caller genuinely needs the finished result
        (and the new node id it produces) back in the SAME round trip before
        it can return anything meaningful to the client. This is the exact
        same shape - and reasoning - as the Gitlink read-only helpers just
        above (fetch_gitlink_repositories/load_gitlink_repo_tree/etc.): "no
        natural intermediate UI state beyond loading for a one-shot action,
        and the caller needs the result back in the same round trip" (see
        that section's own comment). Legacy's own generate_chart likewise
        shows a blocking loading animation for the duration, not a
        fire-and-forget spinner elsewhere - the same UX this mirrors.

        Still guarded by self._runs's "chart" kind (ADR-002 stage 2.3 -
        see backend/run_lifecycle.py) - there is no background task to
        hold onto, only a "one generation in flight for this session"
        marker, so two overlapping generateChart calls (e.g. from two tabs
        open on the same session) cannot race each other.

        No cancel_event: ChartDataAgent has no cancellation checkpoint of
        its own, and its own legacy caller (ChartWorkerThread) has no
        stop() method either - same honestly-documented limitation as every
        other dispatch surface.

        Two distinct failure shapes, both routed through on_failure plus a
        notification, NEITHER of which creates a node (node creation only
        ever happens in on_success):
          1. `_call_chart_agent` returns a dict carrying a top-level "error"
             key - ChartDataAgent.get_response's own fully-degraded case
             (even its heuristic_chart_data fallback found nothing usable).
             Mirrors ChartWorkerThread.run()'s identical `if 'error' in
             parsed: raise ValueError(...)` check at the one legacy call
             site.
          2. A timeout or any other exception raised getting there.
        A dict with NO "error" key is still not guaranteed to be canonical -
        on_success (backend/canvas.py's own closure) is responsible for its
        own defensive canonicalize_chart_data/ChartDataError handling before
        calling document.add_chart_node, exactly as this feature's own
        contract requires; this method's job ends at handing back whatever
        ChartDataAgent produced.

        Reuses WATCHDOG_TIMEOUT_SECONDS (420s), not a new constant:
        ChartDataAgent.get_response makes at most TWO sequential blocking
        api_provider.chat() calls (the initial extraction call, plus one
        repair_chart_data round trip on a non-canonical first attempt) -
        double Artifact's own single-call shape, but nowhere near Web
        Research's ~10-call chain that justified ITS own 900s bump, and 420s
        already carries ample headroom for two calls at any realistic
        per-call latency.

        ADR-002 stage 2.3: the guard/timeout/exception/notify skeleton
        below now lives once, shared with start_note_generation, in
        backend/run_lifecycle.py's run_single_shot - see that function's
        own docstring. Every message string and every branch's exact
        behavior (including the asymmetry where a top-level "error" key
        hands on_failure the RAW error text but prefixes the toast
        notification with "Chart generation failed: ") is unchanged from
        this method's pre-2.3 body."""
        await run_single_shot(
            self._runs,
            kind="chart",
            bus=bus,
            notifications_state=notifications_state,
            node_id=node_id,
            timeout=WATCHDOG_TIMEOUT_SECONDS,
            call=lambda: _call_chart_agent(source_text, chart_type),
            validate=lambda result: (
                str(result["error"]) if isinstance(result, dict) and "error" in result else None
            ),
            on_success=on_success,
            on_failure=on_failure,
            busy_message="A chart is already being generated.",
            timeout_message=(
                "Chart generation stopped responding before the request completed. "
                "Please try again."
            ),
            exception_prefix="Chart generation failed",
            log_exception=lambda exc: logger.exception(
                "chart generation dispatch failed (parent node %s)", node_id
            ),
            validate_notify=lambda message: f"Chart generation failed: {message}",
        )

    async def start_note_generation(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        node_id: str,
        note_kind: str,
        source_text: str,
        on_success,
        on_failure,
    ) -> None:
        """R8a: Key Takeaway / Explainer Note generation.

        DIRECTLY AWAITED by its caller rather than scheduled via
        asyncio.create_task, the same shape as start_chart_generation above
        and for the same reason: the result is a brand new note node, so the
        caller needs it back in the same round trip and there is no
        pre-existing node to attach a spinner to.

        `note_kind` selects the agent ("takeaway" | "explainer"). One method
        rather than two near-identical ones because the two differ ONLY in
        which agent class runs and how failures are worded - the guard,
        timeout, callback and cleanup logic are identical, and duplicating
        them would be two places to fix every future bug in.

        ADR-002 stage 2.3: that shared skeleton now lives once, in
        backend/run_lifecycle.py's run_single_shot, shared with
        start_chart_generation too - see that function's own docstring.
        Every message string and every branch's exact behavior is
        unchanged from this method's pre-2.3 body.
        """
        label = NOTE_AGENT_LABELS.get(note_kind, "Note")

        await run_single_shot(
            self._runs,
            kind="note",
            bus=bus,
            notifications_state=notifications_state,
            node_id=node_id,
            timeout=WATCHDOG_TIMEOUT_SECONDS,
            call=lambda: _call_note_agent(note_kind, source_text),
            validate=lambda text: (
                # An agent that returns nothing usable must not silently
                # create an empty note - that reads as a broken feature.
                f"{label} generation returned an empty response. Please try again."
                if not str(text or "").strip() else None
            ),
            on_success=on_success,
            on_failure=on_failure,
            busy_message=f"A {label.lower()} is already being generated.",
            timeout_message=(
                f"{label} generation stopped responding before the request completed. "
                "Please try again."
            ),
            exception_prefix=f"{label} generation failed",
            log_exception=lambda exc: logger.exception(
                "%s generation dispatch failed (source node %s)", label, node_id
            ),
        )

    async def start_branch_comparison(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        source_text: str,
        on_success,
        on_failure,
    ) -> None:
        """ADR-002 Workstream 1 ("Compare Branches"): mirrors start_note_
        generation's own shape exactly (directly awaited - the result is a
        brand new note node and there is no pre-existing node to attach a
        spinner to; single-slot busy guard; WATCHDOG_TIMEOUT_SECONDS;
        on_success/on_failure callbacks) but with its own "branch_
        comparison" kind in self._runs rather than reusing "note" - see
        that field's own comment in __init__ for why. source_text is
        already the fully-formatted multi-branch block (backend/canvas.py's
        _format_branches_for_comparison) - this method itself is agnostic
        to how many branches went into it.

        ADR-002 stage 2.4: the guard/timeout/exception/notify skeleton
        below now lives once, in backend/run_lifecycle.py's
        run_single_shot - the same primitive start_chart_generation/
        start_note_generation already share. Every message string and
        every branch's exact behavior is unchanged from this method's
        pre-2.4 body."""
        await run_single_shot(
            self._runs,
            kind="branch_comparison",
            bus=bus,
            notifications_state=notifications_state,
            node_id=None,
            timeout=WATCHDOG_TIMEOUT_SECONDS,
            call=lambda: _call_branch_comparison_agent(source_text),
            validate=lambda text: (
                "Branch comparison returned an empty response. Please try again."
                if not str(text or "").strip() else None
            ),
            on_success=on_success,
            on_failure=on_failure,
            busy_message="A branch comparison is already being generated.",
            timeout_message=(
                "Branch comparison stopped responding before the request completed. "
                "Please try again."
            ),
            exception_prefix="Branch comparison failed",
            log_exception=lambda exc: logger.exception("branch comparison dispatch failed"),
        )

    async def start_branch_synthesis(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        source_text: str,
        instructions: str,
        on_success,
        on_failure,
    ) -> None:
        """ADR-002 Workstream 1 ("Synthesize Branches"): mirrors start_branch_
        comparison's own shape exactly (directly awaited - the result is a
        brand new chat node and there is no pre-existing node to attach a
        spinner to; single-slot busy guard; WATCHDOG_TIMEOUT_SECONDS;
        on_success/on_failure callbacks) but with its own "branch_synthesis"
        kind in self._runs rather than reusing "branch_comparison" - see
        that field's own comment in __init__ for why. source_text is
        already the fully-formatted multi-branch block (backend/canvas.py's
        _format_branches_for_comparison, reused verbatim here - a
        labeled-branches text block is equally valid input whether the
        agent on the other end compares or synthesizes); instructions is
        the user's own free text steering the synthesis.

        ADR-002 stage 2.4: shares run_single_shot with start_branch_
        comparison above - see that method's own docstring."""
        await run_single_shot(
            self._runs,
            kind="branch_synthesis",
            bus=bus,
            notifications_state=notifications_state,
            node_id=None,
            timeout=WATCHDOG_TIMEOUT_SECONDS,
            call=lambda: _call_branch_synthesis_agent(source_text, instructions),
            validate=lambda text: (
                "Branch synthesis returned an empty response. Please try again."
                if not str(text or "").strip() else None
            ),
            on_success=on_success,
            on_failure=on_failure,
            busy_message="A branch synthesis is already being generated.",
            timeout_message=(
                "Branch synthesis stopped responding before the request completed. "
                "Please try again."
            ),
            exception_prefix="Branch synthesis failed",
            log_exception=lambda exc: logger.exception("branch synthesis dispatch failed"),
        )


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


def _call_pycoder_execution_agent(conversation_history, user_prompt) -> str:
    """Runs inside asyncio.to_thread. Reuses PyCoderExecutionAgent.get_response
    verbatim - a fresh instance per call, same posture as _call_gitlink_agent/
    _call_artifact_agent constructing their own agent fresh each time."""
    return PyCoderExecutionAgent().get_response(conversation_history, user_prompt)


def _call_pycoder_repair_agent(code, error, is_final_attempt) -> str:
    """Runs inside asyncio.to_thread. Reuses PyCoderRepairAgent.get_response
    verbatim."""
    return PyCoderRepairAgent().get_response(code, error, is_final_attempt)


def _call_pycoder_analysis_agent(original_prompt, code, code_output) -> str:
    """Runs inside asyncio.to_thread. Reuses PyCoderAnalysisAgent.get_response
    verbatim - shared by both Py-Coder's and Execution Sandbox's own final
    analysis step, exactly like legacy's CodeSandboxExecutionWorker
    constructing its own PyCoderAnalysisAgent instance directly rather than
    duplicating that agent's logic."""
    return PyCoderAnalysisAgent().get_response(original_prompt, code, code_output)


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
                     persona_is_override=False, on_context_trimmed=None, on_usage=None) -> str:
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
    )


def _call_chat_agent_stream(conversation_history, persona_text, cancel_event, on_chunk, *, runtime=None,
                            persona_is_override=False, on_context_trimmed=None, on_usage=None) -> str:
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


def _call_chart_agent(source_text: str, chart_type: str) -> dict:
    """Runs inside asyncio.to_thread - the blocking driver for
    start_chart_generation above, mirroring _call_artifact_agent's own
    shape. ChartDataAgent.get_response returns a JSON STRING (its own
    unchanged public contract, preserved byte-for-byte by the R6.2
    extraction into graphlink_chart_agent.py - see that module's own
    docstring), so this parses it back into a dict the same way
    ChartWorkerThread.run() already does at the one legacy call site
    (`parsed = json.loads(data)`) before start_chart_generation inspects it
    for a top-level "error" key."""
    agent = ChartDataAgent()
    raw = agent.get_response(source_text, chart_type)
    return json.loads(raw)


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
    bus, composer_document, notifications_state, settings_manager, provider_runtime=None
) -> AgentDispatcher:
    # ADR-006 stage 6.5: provider_runtime is None for the default session
    # (module-global path, byte-identical behavior) - see
    # AgentDispatcher.__init__'s own comment.
    dispatcher = AgentDispatcher(settings_manager, provider_runtime=provider_runtime)
    # dispatcher.cancel is synchronous (just sets an Event and returns a
    # bool) - no publish/await needed here; the in-flight _run task's own
    # finally block handles the resulting state transition.
    bus.register_intent("app-composer", "cancelChatRequest", lambda request_id: dispatcher.cancel(request_id))
    return dispatcher
