"""Turn-level retry discipline (PLAN-2026-08-24 §2.2/§3.2.2).

The plan's requirement, stated there as a convergent pattern across every
surveyed production runtime: name the recovery paths explicitly in one
per-attempt state object rather than scattering ad-hoc flags through the
loop. Two pieces:

- `classify_fault(exc)` maps whatever the provider raised onto a small
  closed enum. Classification is by exception TYPE first (the only
  reliable signal) and message text second - our provider seam
  (api_provider) surfaces a single RuntimeError family rather than typed
  HTTP errors, so a status code or a rate-limit phrase in the message is
  genuinely the best available evidence. Matching is deliberately
  conservative: anything unrecognized is FATAL, so a new provider error
  fails loudly and visibly instead of being silently retried forever.

- `TurnRetryState` holds the ONE-SHOT guards. Each guard fires at most
  once per task, which is what makes the loop provably terminating: a
  rate-limit retry cannot itself trigger another rate-limit retry, a
  context-overflow compaction cannot loop against a model that keeps
  reporting overflow. `TRANSIENT_RETRY_LIMIT` is the single exception -
  a network blip genuinely can recur - and it is a hard counter, not a
  reset-on-success one.

Deliberately NOT here: credential rotation. The plan lists it because
runtimes with multiple credential sources rotate between them on auth
failure; Graphlink has exactly one configured credential per provider, so
an auth fault is terminal and is classified that way rather than
pretending at a recovery path that does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass

# -- fault taxonomy ---------------------------------------------------------
# Plain string constants, not an Enum: these cross the asyncio.to_thread and
# logging boundaries as values, and every other closed vocabulary in this
# codebase (tools.py's scopes, run kinds, node kinds) is string constants for
# the same reason.

FAULT_RATE_LIMIT = "rate_limit"
FAULT_CONTEXT_OVERFLOW = "context_overflow"
FAULT_TRANSIENT = "transient"
FAULT_AUTH = "auth"
FAULT_TIMEOUT = "timeout"
FAULT_FATAL = "fatal"

ACTION_RETRY = "retry"
ACTION_COMPACT_AND_RETRY = "compact_and_retry"
ACTION_FAIL = "fail"

# How many times a plain transient fault (5xx, connection reset) may be
# retried within ONE task. Small and hard-capped: past this the fault is not
# a blip, and a run that fails visibly is better than one that spins.
TRANSIENT_RETRY_LIMIT = 2

# Backoff before a retried attempt. Fixed per fault class rather than
# exponential-with-jitter: the ceiling is 2 transient attempts plus one
# rate-limit attempt, so total added latency is bounded at a handful of
# seconds and a scheduler is not worth the machinery.
RATE_LIMIT_BACKOFF_SECONDS = 8.0
TRANSIENT_BACKOFF_SECONDS = 2.0

_RATE_LIMIT_MARKERS = ("rate limit", "rate_limit", "ratelimit", "too many requests", "429", "quota")
_CONTEXT_MARKERS = (
    "context length", "context_length", "context window", "maximum context",
    "too many tokens", "prompt is too long", "input is too long", "reduce the length",
)
_AUTH_MARKERS = (
    "unauthorized", "forbidden", "invalid api key", "invalid_api_key",
    "authentication", "401", "403",
)
_TRANSIENT_MARKERS = (
    "500", "502", "503", "504", "overloaded", "temporarily unavailable",
    "connection reset", "connection aborted", "connection refused",
    "timed out", "timeout", "broken pipe", "server disconnected",
)


def classify_fault(exc: BaseException) -> str:
    """Map a provider exception onto the fault vocabulary above.

    Type-first: asyncio.TimeoutError is unambiguous regardless of message.
    Everything else falls to lowercased-message marker matching, ordered
    most-specific-first - a message can legitimately contain more than one
    marker ("429 too many requests: rate limit reached, retry after"), and
    rate-limit/context faults have specific recovery paths where transient
    only has "wait and try again", so the specific classes must win.
    """
    import asyncio

    if isinstance(exc, asyncio.TimeoutError):
        return FAULT_TIMEOUT

    text = f"{type(exc).__name__}: {exc}".lower()
    if any(marker in text for marker in _RATE_LIMIT_MARKERS):
        return FAULT_RATE_LIMIT
    if any(marker in text for marker in _CONTEXT_MARKERS):
        return FAULT_CONTEXT_OVERFLOW
    if any(marker in text for marker in _AUTH_MARKERS):
        return FAULT_AUTH
    if any(marker in text for marker in _TRANSIENT_MARKERS):
        return FAULT_TRANSIENT
    return FAULT_FATAL


@dataclass
class TurnRetryState:
    """One-shot recovery guards for a single task's model calls.

    Constructed once per task (never per turn): the guards bound the TASK's
    total recovery budget, so a task cannot burn an unbounded number of
    provider calls by alternating fault classes across turns.
    """

    rate_limit_retried: bool = False
    context_overflow_compacted: bool = False
    timeout_retried: bool = False
    transient_retries: int = 0

    def decide(self, fault: str) -> tuple[str, float, str]:
        """(action, backoff_seconds, human_reason) for one observed fault.

        The human_reason is surfaced in the node's status detail on the
        failing path and logged on the recovering one, so a user can always
        see WHY a run paused or died rather than inferring it from a
        stack-trace string.
        """
        if fault == FAULT_RATE_LIMIT:
            if self.rate_limit_retried:
                return ACTION_FAIL, 0.0, "Rate limited twice in one task - stopping."
            self.rate_limit_retried = True
            return (
                ACTION_RETRY,
                RATE_LIMIT_BACKOFF_SECONDS,
                f"Rate limited - retrying once in {RATE_LIMIT_BACKOFF_SECONDS:.0f}s.",
            )

        if fault == FAULT_CONTEXT_OVERFLOW:
            if self.context_overflow_compacted:
                return ACTION_FAIL, 0.0, "Context still too large after compaction - stopping."
            self.context_overflow_compacted = True
            return ACTION_COMPACT_AND_RETRY, 0.0, "Context overflowed - compacting and retrying."

        if fault == FAULT_TIMEOUT:
            if self.timeout_retried:
                return ACTION_FAIL, 0.0, "The model stopped responding twice - stopping."
            self.timeout_retried = True
            return ACTION_RETRY, 0.0, "The model stopped responding - retrying once."

        if fault == FAULT_TRANSIENT:
            if self.transient_retries >= TRANSIENT_RETRY_LIMIT:
                return ACTION_FAIL, 0.0, f"Provider failed {self.transient_retries + 1} times - stopping."
            self.transient_retries += 1
            return (
                ACTION_RETRY,
                TRANSIENT_BACKOFF_SECONDS,
                f"Provider error - retry {self.transient_retries} of {TRANSIENT_RETRY_LIMIT}.",
            )

        if fault == FAULT_AUTH:
            # Terminal by design - see this module's own docstring on why
            # there is no credential-rotation path to take here.
            return ACTION_FAIL, 0.0, "The provider rejected the credentials - check Settings."

        return ACTION_FAIL, 0.0, "Unrecoverable provider error."
