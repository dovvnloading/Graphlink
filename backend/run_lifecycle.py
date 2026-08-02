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

Stage 2.3 migrates the 3 pilot surfaces named in ADR-002 (chat/
conversation, chart, note) - see that document for why the remaining 9
slots (image, web research, artifact, gitlink x2, pycoder, code sandbox,
branch comparison, branch synthesis) are deliberately deferred to stage
2.4 rather than folded in here: several of those have real per-kind
shape differences (approval futures, cancellation tokens instead of
threading.Event, no-cancel-at-all) that deserve their own migration and
verification pass rather than being rushed alongside this one.

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
mechanism anywhere in this codebase is cooperative, via
RunHandle.cancel_event, which callers other than the run itself trip and
the run's own code observes at its next checkpoint. cancel_all() below
preserves this exactly: it walks every claimed handle and sets
cancel_event on the ones that have one, silently no-oping on kinds (like
chart/note) that have none - the same honestly-documented "this kind
cannot be cancelled" limitation those two already had before this
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
    running is rejected regardless of which node either one targets)."""

    kind: str
    request_id: str
    node_id: str | None = None
    cancel_event: threading.Event | None = None
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
    ) -> RunHandle:
        """Synchronous by design - see this module's own docstring for
        why callers must call this in the same synchronous stretch as
        their own is_busy() pre-check."""
        handle = RunHandle(
            kind=kind,
            request_id=uuid.uuid4().hex,
            node_id=node_id,
            cancel_event=cancel_event,
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

    def cancel(self, request_id: str) -> bool:
        handle = self._handles.get(request_id)
        if handle is None or handle.cancel_event is None:
            return False
        handle.cancel_event.set()
        return True

    def cancel_all(self) -> None:
        """See this module's own docstring for why kinds with no
        cancel_event (chart, note) are silently skipped rather than
        treated as an error."""
        for handle in self._handles.values():
            if handle.cancel_event is not None:
                handle.cancel_event.set()


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
