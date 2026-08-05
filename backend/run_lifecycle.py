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
checkpoint. cancel_all() below preserves this exactly: it walks every
claimed handle and fires whichever of the two mechanisms is present,
silently no-oping on kinds (like chart/note/branch_comparison/branch_
synthesis) that have neither - the same honestly-documented "this kind
cannot be cancelled" limitation those already had before their own
migration, just visible in one place now instead of being invisible to
cancel_all() entirely.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from backend.events import SessionBus  # type hint only


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
    it is Py-Coder/Execution Sandbox's "waiting for a human to approve or
    deny" mechanism (see AgentDispatcher.start_pycoder_run's own
    docstring). Deliberately NOT read or resolved by RunRegistry.cancel()/
    cancel_all() below - those two kinds' own cancel_pycoder/
    cancel_code_sandbox methods resolve it directly (an immediate,
    definite unblock, not the cooperative-flag semantics cancel_event
    represents), and only a full-session-disconnect auto-denies every
    still-pending approval, via cancel_all_pending_approvals below - a
    THIRD, deliberately separate cleanup mechanism from cancel()/
    cancel_all(), mirroring the one it replaces (AgentDispatcher's own
    pre-migration cancel_all_pending_approvals). Mutated in place after
    claim() on both kinds (a fresh Future replaces the old one on every
    repair-loop iteration) - callers must always read
    handle.approval_future fresh, never cache a reference to it.

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


class RunRegistry:
    """One in-flight-run registry per AgentDispatcher (session-scoped,
    like the dicts it replaces - never a module-level singleton)."""

    def __init__(self) -> None:
        self._handles: dict[str, RunHandle] = {}

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
    ) -> RunHandle:
        """Synchronous by design - see this module's own docstring for
        why callers must call this in the same synchronous stretch as
        their own is_busy() pre-check."""
        handle = RunHandle(
            kind=kind,
            request_id=uuid.uuid4().hex,
            node_id=node_id,
            cancel_event=cancel_event,
            on_cancel=on_cancel,
            approval_future=approval_future,
            approval_snapshot_fn=approval_snapshot_fn,
        )
        self._handles[handle.request_id] = handle
        return handle

    def attach_task(self, handle: RunHandle, task: asyncio.Task) -> None:
        handle.task = task

    def release(self, request_id: str) -> None:
        self._handles.pop(request_id, None)

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
        return fired

    def cancel_all(self) -> None:
        """See this module's own docstring for why kinds with neither
        cancel_event nor on_cancel (chart, note, branch_comparison,
        branch_synthesis) are silently skipped rather than treated as an
        error."""
        for handle in self._handles.values():
            if handle.cancel_event is not None:
                handle.cancel_event.set()
            if handle.on_cancel is not None:
                handle.on_cancel()

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
    call: Callable[[], Any],
    validate: Callable[[Any], str | None],
    on_success,
    on_failure,
    busy_message: str,
    timeout_message: str,
    exception_prefix: str,
    log_exception: Callable[[BaseException], None],
    validate_notify: Callable[[str], str] | None = None,
) -> None:
    """Shared skeleton for a "directly-awaited, single combined
    create+generate action" dispatch surface - start_chart_generation and
    start_note_generation today; see this module's own docstring for why
    chat/conversation cannot share this same function.

    `call`: zero-arg callable, run inside asyncio.to_thread. `validate`:
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

    handle = registry.claim(kind, node_id=node_id)
    try:
        result = await asyncio.wait_for(asyncio.to_thread(call), timeout=timeout)
        message = validate(result)
        if message is not None:
            await _invoke(on_failure, message)
            notify_message = validate_notify(message) if validate_notify else message
            notifications_state.show(notify_message, "error")
            await bus.publish("notification")
        else:
            await _invoke(on_success, result)
    except asyncio.TimeoutError:
        await _invoke(on_failure, timeout_message)
        notifications_state.show(timeout_message, "error")
        await bus.publish("notification")
    except Exception as exc:
        log_exception(exc)
        message = f"{exception_prefix}: {exc}"
        await _invoke(on_failure, message)
        notifications_state.show(message, "error")
        await bus.publish("notification")
    finally:
        registry.release(handle.request_id)
