"""ADR-002 stage 2.3: the RunLifecycle primitive.

AgentDispatcher (backend/agents.py) grew 12 independent in-flight-request
dicts across its 13 dispatch surfaces (one per R4.4/R5.x/R6.x/R8a/ADR-002
increment), each hand-copying the same "single slot per kind, uuid4
request_id, pop-in-finally" shape with small, undocumented variations -
audit findings S2 (13x duplicated skeleton) and C3 (cancel_all() walks
only ONE of the twelve dicts, so a client that disconnects mid-flight on
any of the other eleven leaves that call running server-side, untethered,
until its own watchdog timeout). This module replaces that pattern with
ONE registry every dispatch surface claims into and releases from, so
cancel_all() can walk every in-flight run regardless of kind.

Stage 2.3 migrated the 3 pilot surfaces named in ADR-002 (chat/
conversation, chart, note); stage 2.4's own first slice added branch
comparison/branch synthesis (structurally identical to chart/note, no
primitive changes needed). Stage 2.4b (this revision) extends RunHandle/
RunRegistry with `on_cancel` and `approval_future` - see RunHandle's own
docstring below - clearing the way for the remaining 7 fire-and-forget
surfaces (image, web research, artifact, gitlink x2, pycoder, code
sandbox), each migrated in its own slice rather than all at once.

Two access shapes coexist because the pilots themselves have two
genuinely different dispatch shapes:

- chat/conversation (AgentDispatcher._dispatch) is fire-and-forget: it
  claims a slot SYNCHRONOUSLY in the calling coroutine, then schedules a
  background asyncio.Task and returns immediately without awaiting it -
  the WS read loop must keep reading further messages (including a
  cancelChatRequest intent) while that task runs. Claim and release
  happen in DIFFERENT coroutines (the scheduling coroutine claims; the
  scheduled task releases in its own `finally`), so this surface uses
  RunRegistry.claim()/.release() directly.
- chart/note are directly awaited by their own caller (there is no
  pre-existing node to attach a spinner to - the caller needs the
  result, including a brand new node id, back in the same round trip).
  Claim and release happen in the SAME coroutine, so these two share
  run_single_shot() below, one function replacing two near-identical
  ~50-line try/except/finally bodies.

Claiming is always synchronous (RunRegistry.claim() is a plain method,
never `async def`) - this is load-bearing, not a style choice. Every
pilot's busy pre-check (`if registry.is_busy(kind):`) and its own claim
must happen in the same synchronous stretch, with no `await` in between,
exactly as every pre-existing dict-based guard already did - an `await`
between the two would let a second coroutine observe "not busy" before
the first one's claim lands, admitting two concurrent runs of the same
kind. See AgentDispatcher._dispatch's own call site for the pattern this
preserves.

RunHandle.task, where present, is held ONLY to keep the asyncio.Task
alive (the event loop holds only a weak reference to scheduled tasks) -
never call .cancel() on it. None of the 12 pre-existing dicts' task
references were ever cancelled directly either; the only cancellation
mechanisms anywhere in this codebase are cooperative - RunHandle.
cancel_event (threading.Event, the majority shape) or RunHandle.
on_cancel (a generic callable, for kinds like web_research whose own
cancellation primitive is not a threading.Event) - which callers other
than the run itself trip and the run's own code observes at its next
checkpoint. cancel_all() below fires whichever of the two mechanisms is present on
each claimed handle. As of ADR-006 stage 6.2 every kind carries one (the
formerly-uncancellable chart/note/branch_comparison/branch_synthesis now
get a cancel_event from run_single_shot itself; image and gitlink_apply
from their own dispatch surfaces), cancel releases the slot IMMEDIATELY
instead of waiting for the worker thread to observe it (see
RunRegistry.cancel/release), and run_single_shot schedules its work off
the WS read loop instead of being awaited on it.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from backend.events import SessionBus  # type hint only

# ADR-016 stage 16.1: every one of the app's dispatch surfaces (chat, chart,
# note, image, web research, artifact, gitlink x2, pycoder, code sandbox - 12
# kinds and counting) claims into and releases from ONE RunRegistry per
# session (see this module's own docstring above). Logging at claim()/
# cancel()/_pop() instead of at each of those 12 call sites gives every run,
# past and future, a traced lifecycle for free - no per-surface duplication,
# and no surface can forget to log. Deliberately explicit extra={} fields
# rather than a contextvars-based run_id (see backend/observability.py's own
# docstring for why): claim() is a plain synchronous method called from many
# different call sites, so there is no single "task body" to scope a
# ContextVar.set()/reset() around here.
_logger = logging.getLogger("graphlink.run")


@dataclass
class RunHandle:
    """One claimed slot in a RunRegistry.

    `kind` is the busy-guard bucket (e.g. "chat", "chart", "note") - one
    in-flight run per kind per AgentDispatcher, mirroring exactly what
    each pre-existing independent dict already enforced. `node_id` is
    informational only (logging/introspection) - it is NEVER consulted
    by RunRegistry.is_busy/claim, because every pilot's busy check is
    session-wide, not per-node (a second chat send while one is already
    running is rejected regardless of which node either one targets).

    `cancel_event`/`on_cancel` (ADR-002 stage 2.4b) are two alternative
    cancellation mechanisms, never both set on the same handle: most
    cancellable kinds use a plain threading.Event (the majority shape,
    kept as its own field for the common case), but web_research's
    cancellation primitive is a CancellationToken (graphlink_plugins/
    web_research/domain.py) - a structurally different class with its own
    .cancel() method, not an Event. `on_cancel` is a generic escape hatch
    for exactly that: any zero-arg callable a RunRegistry.cancel()/
    cancel_all() caller can invoke without needing to know which concrete
    cancellation primitive is behind it.

    `approval_future` (ADR-002 stage 2.4b) is unrelated to cancellation -
    it is Execution Sandbox/Builder/Harness's "waiting for a human to
    approve or deny" mechanism (see AgentDispatcher.start_code_sandbox_run's
    own docstring). Deliberately NOT read or resolved by
    RunRegistry.cancel()/cancel_all() below - those kinds' own
    cancel_code_sandbox/cancel_builder/cancel_harness methods resolve it
    directly (an immediate, definite unblock, not the cooperative-flag
    semantics cancel_event represents), and only a full-session-disconnect
    auto-denies every still-pending approval, via
    cancel_all_pending_approvals below - a THIRD, deliberately separate
    cleanup mechanism from cancel()/cancel_all(), mirroring the one it
    replaces (AgentDispatcher's own pre-migration
    cancel_all_pending_approvals). Mutated in place after claim() on any of
    these kinds (a fresh Future replaces the old one on every repair-loop
    iteration) - callers must always read handle.approval_future fresh,
    never cache a reference to it.

    `approval_snapshot_fn`/`approval_snapshot` (ADR-005 stage 5.5): closes a
    real race a 4-lens adversarial review found in the source-build
    escalation checkbox. That checkbox's value lives in mutable node state
    (CodeSandboxState.code_sandbox_approval_allow_source_builds), set by its
    OWN ungated WS intent (setCodeSandboxAllowSourceBuilds) - unlike the
    code/manifest being approved, which start_code_sandbox_run freezes into
    a local variable BEFORE `await approval_future` even begins, this value
    is only known once the user's own Approve click is decided, so it can't
    be pre-frozen the same way. Naively re-reading node.state AFTER
    `await approval_future` resolves is NOT safe: `future.set_result()` runs
    on the resolving connection's own WS-message-handling coroutine and only
    SCHEDULES (via call_soon) the awaiting `_run()` task's resumption - it
    does not resume it inline - so a second WS connection on the same
    session can dispatch setCodeSandboxAllowSourceBuilds and have it fully
    processed by the event loop in that scheduling gap, silently changing
    what an already-decided approval installs. `_resolve_approval` (backend/
    agents.py) has no `await` anywhere in its own call chain, so it - and
    only it - can read the live checkbox value and call
    `approval_snapshot_fn()` into `approval_snapshot` in the SAME
    uninterruptible synchronous stretch as `future.set_result()`: no other
    coroutine can observe or mutate anything in between. `start_code_sandbox_
    run` passes `approval_snapshot_fn` at claim() time; callers that resolve
    an approval read `handle.approval_snapshot` afterward instead of
    re-reading live state. `None` for every other kind (nothing else has a
    live-mutable field that needs capturing exactly at approval time)."""

    kind: str
    request_id: str
    node_id: str | None = None
    cancel_event: threading.Event | None = None
    on_cancel: Callable[[], None] | None = None
    approval_future: asyncio.Future | None = None
    approval_snapshot_fn: Callable[[], Any] | None = None
    approval_snapshot: Any = None
    task: asyncio.Task | None = None
    # ADR-006 stage 6.2: the surface's user-visible end transition (its
    # on_end() + state-topic publish), as an async zero-arg closure. On a
    # NORMAL completion the surface's own `finally` runs it - gated on
    # release() returning True. On CANCEL, RunRegistry.cancel() pops the
    # handle immediately (freeing the slot) and schedules THIS instead, so
    # the UI returns to idle the moment the user cancels rather than when
    # the worker thread eventually dies - and the worker's late `finally`
    # sees release() -> False and skips the transition, which is what stops
    # a stale run's teardown from clobbering a NEWER run's "generating"
    # state (the slot is free, so a new claim may already exist).
    finalize: Callable[[], Any] | None = None
    # Captured at claim() time so cancel() can schedule `finalize` from any
    # thread (cancelChatRequest's sync handler runs via asyncio.to_thread).
    loop: asyncio.AbstractEventLoop | None = None


class RunRegistry:
    """One in-flight-run registry per AgentDispatcher (session-scoped,
    like the dicts it replaces - never a module-level singleton)."""

    def __init__(
        self,
        *,
        on_claim: Callable[[str, str, str | None], None] | None = None,
        on_end: Callable[[str, str], None] | None = None,
    ) -> None:
        # ADR-016 stage 16.3: optional diagnostics hooks - on_claim(run_id,
        # kind, node_id) fires alongside the "run claimed" log line, on_end
        # (run_id, outcome) alongside "run cancelled"/"run released".
        # Explicit callbacks rather than a shared logging.Handler on
        # "graphlink.run" (see backend/diagnostics.py's own docstring for
        # why that would leak a handler per session). None by default so
        # every existing RunRegistry() call site (dozens across the test
        # suite) is unaffected.
        self._on_claim = on_claim
        self._on_end = on_end
        self._handles: dict[str, RunHandle] = {}
        # ADR-006 stage 6.2: tasks whose handle was popped by cancel() while
        # the task still runs. Held ONLY as an anti-GC reference (the event
        # loop keeps just a weak ref to scheduled tasks - the same reason
        # RunHandle.task exists) and to keep has-in-flight semantics honest:
        # a cancelled-but-still-unwinding worker is still real work against
        # this session's objects, so session eviction must keep waiting for
        # it (see values_or_orphans / backend/agents.py has_in_flight_runs).
        self._orphaned_tasks: set[asyncio.Task] = set()

    def is_busy(self, kind: str) -> bool:
        return any(handle.kind == kind for handle in self._handles.values())

    def claim(
        self,
        kind: str,
        *,
        node_id: str | None = None,
        cancel_event: threading.Event | None = None,
        on_cancel: Callable[[], None] | None = None,
        approval_future: asyncio.Future | None = None,
        approval_snapshot_fn: Callable[[], Any] | None = None,
        finalize: Callable[[], Any] | None = None,
    ) -> RunHandle:
        """Synchronous by design - see this module's own docstring for
        why callers must call this in the same synchronous stretch as
        their own is_busy() pre-check."""
        try:
            loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            # Direct registry use outside a loop (unit tests) - cancel()
            # then simply cannot schedule finalize, which such tests never
            # pass anyway.
            loop = None
        handle = RunHandle(
            kind=kind,
            request_id=uuid.uuid4().hex,
            node_id=node_id,
            cancel_event=cancel_event,
            on_cancel=on_cancel,
            approval_future=approval_future,
            approval_snapshot_fn=approval_snapshot_fn,
            finalize=finalize,
            loop=loop,
        )
        self._handles[handle.request_id] = handle
        _logger.info(
            "run claimed",
            extra={"run_id": handle.request_id, "kind": kind, "node_id": node_id},
        )
        if self._on_claim is not None:
            self._on_claim(handle.request_id, kind, node_id)
        return handle

    def attach_task(self, handle: RunHandle, task: asyncio.Task) -> None:
        handle.task = task

    def _pop(self, request_id: str) -> RunHandle | None:
        handle = self._handles.pop(request_id, None)
        if handle is not None and handle.task is not None and not handle.task.done():
            self._orphaned_tasks.add(handle.task)
            handle.task.add_done_callback(self._orphaned_tasks.discard)
        if handle is not None:
            # A popped handle is unreachable to cancel_all_pending_approvals
            # (it walks _handles), so an unresolved approval future MUST be
            # auto-denied here or the run's parked `await approval_future`
            # waits forever - the disconnect path calls cancel_all() BEFORE
            # cancel_all_pending_approvals() (backend/app.py), and before
            # release-on-cancel that ordering was harmless because cancel
            # never popped. Denial (False), never approval: popping means the
            # run was cancelled or torn down, and cancel-means-deny is the
            # semantic cancel_code_sandbox/cancel_builder/cancel_harness
            # already have.
            future = handle.approval_future
            if future is not None and not future.done():
                future.set_result(False)
            _logger.info(
                "run released",
                extra={"run_id": request_id, "kind": handle.kind, "node_id": handle.node_id},
            )
        return handle

    def release(self, request_id: str) -> bool:
        """Returns True if this call actually popped the handle - False when
        cancel() already released it. ADR-006 stage 6.2: every surface's
        `finally` gates its on_end/state-publish tail on this bool, so a
        cancelled run's late teardown never re-runs the end transition that
        cancel() already performed (and never clobbers a newer run's state -
        see RunHandle.finalize)."""
        handle = self._pop(request_id)
        # ADR-016 stage 16.3: "completed" specifically - NOT fired from _pop
        # itself, since _pop is also the cancel path's own pop (via
        # _finish_cancel below), which already records "cancelled" from
        # cancel()/cancel_all() before ever reaching here. Firing it here
        # unconditionally would overwrite a cancelled run's outcome back to
        # "completed" the instant its worker's own late `finally` calls
        # release() - see run_single_shot's docstring on why that always
        # happens regardless of which path actually freed the slot.
        if handle is not None and self._on_end is not None:
            self._on_end(request_id, "completed")
        return handle is not None

    def _schedule_finalize(self, handle: RunHandle) -> None:
        if handle.finalize is None or handle.loop is None or handle.loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(handle.finalize(), handle.loop)

    def get(self, request_id: str) -> RunHandle | None:
        return self._handles.get(request_id)

    def values(self):
        return self._handles.values()

    def cancel(self, request_id: str, *, kind: str | None = None) -> bool:
        """`kind`, when given, rejects a request_id that resolves to a
        DIFFERENT kind than expected - load-bearing once more than one
        cancellable kind shares a registry: AgentDispatcher.cancel()
        (backing the cancelChatRequest WS intent) passes kind="chat" so a
        stale or mismatched request_id can never trip an unrelated
        in-flight run of a different kind instead of being safely
        rejected. Returns True if either cancellation mechanism present
        on the handle actually fired - False for a kind with neither
        (e.g. chart/note today), matching every pre-existing dict-based
        cancel_* method's own "kind that cannot be cancelled" contract."""
        handle = self._handles.get(request_id)
        if handle is None or (kind is not None and handle.kind != kind):
            return False
        fired = False
        if handle.cancel_event is not None:
            handle.cancel_event.set()
            fired = True
        if handle.on_cancel is not None:
            handle.on_cancel()
            fired = True
        if fired:
            _logger.info(
                "run cancelled",
                extra={"run_id": request_id, "kind": handle.kind, "node_id": handle.node_id},
            )
            if self._on_end is not None:
                self._on_end(request_id, "cancelled")
            # ADR-006 stage 6.2: cancel frees the slot IMMEDIATELY. Before
            # this, release() lived only in the run's `finally`, downstream
            # of an uninterruptible asyncio.to_thread - so is_busy stayed
            # True (and the UI stayed "generating") for however long the
            # worker took to actually observe the cancel, up to the full
            # watchdog timeout for surfaces that only check post-return.
            # Popping here + scheduling the handle's finalize makes cancel
            # latency independent of worker-thread latency; the worker's own
            # late finally sees release() -> False and skips its tail.
            self._finish_cancel(request_id, handle)
        return fired

    def _finish_cancel(self, request_id: str, handle: RunHandle) -> None:
        """Pop + finalize, ALWAYS executed on the registry's event loop.

        6.2 review fix (thread affinity): cancelChatRequest's sync handler
        runs via asyncio.to_thread, so cancel() can arrive on a worker
        thread - but _pop mutates the handles dict (raced by the loop
        thread's is_busy/values iteration), calls Task.add_done_callback
        (not thread-safe on a running task), and may resolve an approval
        future (loop-affine). The cancel EVENT was already set inline above
        (threading.Event.set is thread-safe, so the worker observes the
        cancel with zero added latency); only the bookkeeping is marshalled
        here, landing microseconds later - still far inside the 2 s budget."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if handle.loop is not None and running is not handle.loop:
            if not handle.loop.is_closed():
                handle.loop.call_soon_threadsafe(self._finish_cancel, request_id, handle)
            return
        # Gated on the pop actually succeeding: in the marshalled case the
        # worker may have completed NORMALLY in the gap before this callback
        # ran - its finally already popped the handle and ran the end
        # transition, and running finalize again here would be the exact
        # double-transition this design exists to prevent.
        if self._pop(request_id) is not None:
            self._schedule_finalize(handle)

    def cancel_all(self) -> None:
        """Fires every handle's cancel mechanism, releasing each fired
        handle immediately (same semantics as cancel() above). As of
        ADR-006 stage 6.2 every kind carries a mechanism, so nothing is
        skipped any more - the pre-6.2 "chart/note/branch_* are silently
        skipped" limitation is closed."""
        for handle in list(self._handles.values()):
            fired = False
            if handle.cancel_event is not None:
                handle.cancel_event.set()
                fired = True
            if handle.on_cancel is not None:
                handle.on_cancel()
                fired = True
            if fired:
                _logger.info(
                    "run cancelled",
                    extra={"run_id": handle.request_id, "kind": handle.kind, "node_id": handle.node_id},
                )
                if self._on_end is not None:
                    self._on_end(handle.request_id, "cancelled")
                self._finish_cancel(handle.request_id, handle)

    def has_any_live_work(self) -> bool:
        """Claimed handles OR cancelled-but-still-unwinding orphan tasks -
        the honest "is anything still running against this session"
        predicate session eviction consults (ADR-004 stage 4.3 veto).
        Release-on-cancel empties _handles instantly, but the worker
        threads those runs hold are still alive until they observe the
        cancel; evicting the session out from under them would orphan real
        in-flight work mid-write."""
        return bool(self._handles) or bool(self._orphaned_tasks)

    def cancel_all_pending_approvals(self, kinds: tuple[str, ...]) -> None:
        """Auto-denies every still-undone approval_future among handles
        whose kind is in `kinds` - the third, deliberately separate
        cleanup mechanism from cancel()/cancel_all() described in
        RunHandle's own docstring. `future.done()` is checked first so an
        already-resolved future (e.g. a human approved it a moment before
        the last tab closed) is never clobbered - same guard every
        pre-existing approval-resolving method in this codebase already
        applies."""
        for handle in list(self._handles.values()):
            if handle.kind not in kinds:
                continue
            future = handle.approval_future
            if future is not None and not future.done():
                future.set_result(False)


async def _invoke(fn, *args) -> None:
    if inspect.iscoroutinefunction(fn):
        await fn(*args)
    else:
        fn(*args)


async def run_single_shot(
    registry: RunRegistry,
    *,
    kind: str,
    bus: SessionBus,
    notifications_state,
    node_id: str | None,
    timeout: float,
    call: Callable[[threading.Event], Any],
    validate: Callable[[Any], str | None],
    on_success,
    on_failure,
    busy_message: str,
    timeout_message: str,
    exception_prefix: str,
    log_exception: Callable[[BaseException], None],
    validate_notify: Callable[[str], str] | None = None,
) -> None:
    """Shared skeleton for a "single combined create+generate action"
    dispatch surface - chart/note/branch-comparison/branch-synthesis.
    Fire-and-forget as of ADR-006 stage 6.2 (claim synchronously, schedule
    the generation, return immediately) - see the inline comment at the
    claim below for why the old directly-awaited shape had to go.

    `call`: callable run inside asyncio.to_thread, receiving THIS run's own
    cancel_event (below) as its one argument - ADR-013 stage 13.3: previously
    a zero-arg callable, the cancel_event was created here but never reached
    `call` itself, so a cancelled run's blocking work (an in-flight
    api_provider.chat()) always ran to completion regardless; only the
    result was discarded (see the cancel_event comment just below). A
    surface that wants a REAL interruption threads it through to something
    that checks it (respond_json's own cancellation_event kwarg, ultimately
    api_provider.RequestCancelledError) - chart's own `_call_chart_agent` is
    the first to do so; note/branch-comparison/branch-synthesis accept and
    ignore it today (no change in their own behavior), a call signature
    change rather than a behavior one for those three. `validate`:
    inspects a SUCCESSFUL `call` result and returns a human-readable
    failure message when it is well-formed but not usable (e.g. chart's
    top-level "error" key, note's empty-string check) - this is a
    success-path check, not an exception. `validate_notify`: separately
    formats that same message for the toast notification only, for a
    surface whose notification text differs from the message it hands to
    on_failure (chart prefixes its toast with "Chart generation failed: ";
    every other branch - including note's own validate failure - uses
    the identical string for both, the default identity mapping).
    `log_exception`: called with the caught exception, from inside the
    `except Exception` handler, so a `logger.exception(...)` callback
    still resolves the right traceback (Python's active-exception state
    stays valid for the whole dynamic extent of the except block, across
    nested calls) even though it fires through this shared frame."""
    if registry.is_busy(kind):
        notifications_state.show(busy_message, "info")
        await bus.publish("notification")
        return

    # ADR-006 stage 6.2: claimed synchronously (unchanged), but the actual
    # generation now runs in a SCHEDULED task instead of being awaited here.
    # These four surfaces used to be the app's worst read-loop blockers: the
    # WS receive loop (backend/app.py) awaits each intent handler inline, so
    # a chart/note/comparison/synthesis generation held the ENTIRE socket -
    # no further frame, including a cancel, was even read - for up to the
    # 420 s watchdog. The old justification was a return-value contract
    # ("the caller needs the new node id back in the same round trip"), but
    # every frontend call site fires these intents fire-and-forget and
    # discards the result (sceneStore.ts fireIntent sites) - the node
    # arrives via the scene publish inside on_success, exactly like every
    # other fire-and-forget surface. So this returns immediately; the
    # cancel_event (new - these kinds were previously uncancellable and
    # silently skipped by cancel_all) suppresses the result callbacks when
    # a cancel or session disconnect lands mid-generation.
    #
    # DELIBERATE BEHAVIOR CHANGE, decided rather than drifted into (6.2
    # adversarial review surfaced it): pre-6.2, a last-connection drop left
    # these four generations running because cancel_all skipped them, so a
    # reconnecting client found the finished node. Now a disconnect cancels
    # them like every OTHER kind (chat has always discarded on disconnect),
    # trading that accidental resilience for the uniform "disconnect cancels
    # everything" contract the eviction/cancel machinery is built on. If
    # blip-survival ever becomes a requirement, it belongs to a
    # reconnect-grace design for ALL kinds, not a silent carve-out for four.
    cancel_event = threading.Event()
    handle = registry.claim(kind, node_id=node_id, cancel_event=cancel_event)

    async def _task() -> None:
        try:
            result = await asyncio.wait_for(asyncio.to_thread(call, cancel_event), timeout=timeout)
            if cancel_event.is_set():
                return
            message = validate(result)
            if message is not None:
                await _invoke(on_failure, message)
                notify_message = validate_notify(message) if validate_notify else message
                notifications_state.show(notify_message, "error")
                await bus.publish("notification")
            else:
                await _invoke(on_success, result)
        except asyncio.TimeoutError:
            if cancel_event.is_set():
                return
            await _invoke(on_failure, timeout_message)
            notifications_state.show(timeout_message, "error")
            await bus.publish("notification")
        except Exception as exc:
            if cancel_event.is_set():
                return
            log_exception(exc)
            message = f"{exception_prefix}: {exc}"
            await _invoke(on_failure, message)
            notifications_state.show(message, "error")
            await bus.publish("notification")
        finally:
            registry.release(handle.request_id)

    registry.attach_task(handle, asyncio.create_task(_task()))
