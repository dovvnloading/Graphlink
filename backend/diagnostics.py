"""ADR-016 stage 16.3: in-app diagnostics.

Local-only, in-memory, bounded - this is field visibility for the
maintainer (and a curious user), not a metrics pipeline. Nothing here is
persisted or leaves the machine.

DiagnosticsState is constructed fresh per session (backend/app.py's
_configure_session, same posture as TokenCounterState/ComposerDocument) and
fed by explicit callbacks rather than a shared logging.Handler: RunRegistry
(backend/run_lifecycle.py) already logs "run claimed"/"run cancelled"/"run
released" to the "graphlink.run" logger for stage 16.1, but attaching a
Handler per session to that ONE shared, process-lifetime logger would leak
a handler every time a session is constructed (this codebase's own test
suite builds hundreds of SessionBus/AgentDispatcher instances per run) -
explicit on_claim/on_end callbacks threaded through RunRegistry's
constructor carry the same data with no such leak, and are trivially
testable without touching global logging state.

Provider errors are the one genuinely PROCESS-GLOBAL piece: api_provider.py
is module-level state serving the default session (ADR-006 stage 6.5's own
"None means the default session" contract), so _translate_chat_exception -
the single choke point BOTH chat() and chat_stream() funnel every non-cancel
failure through - records into a module-level bounded deque here, and every
session's DiagnosticsState.payload() reads the same shared view. This
matches every session already seeing the same module-global provider
config today for the default session.

record_provider_error's own `message` argument is raw str(exc) from
whatever exception fired - there is no way to structurally guarantee an
arbitrary exception's text contains neither a filesystem path (an OSError's
__str__ embeds the FULL absolute path, including the OS username and any
private folder/file names - see api_provider.py's _read_attachment_bytes,
whose own error text interpolates a raw OSError) nor literal chat content
(some provider SDKs' client-side request validation echoes the offending
field's literal value). So the raw text is NEVER stored here: see
_redact_provider_error_message below, which classifies it into one of a
small, fixed vocabulary of category labels instead - this is the ONE place
in the whole diagnostics allowlist that isn't inherently content-free by
construction, so it gets its own explicit redaction step rather than
inheriting "never chat content" for free the way every other field does.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

# -- provider errors (process-global - see module docstring) ---------------

_MAX_PROVIDER_ERRORS = 10
_provider_errors: deque[dict[str, Any]] = deque(maxlen=_MAX_PROVIDER_ERRORS)

# Keyword (lowercase substring) -> fixed category label, checked in order -
# first match wins. Deliberately never echoes back any substring of the
# original message: only ever one of the fixed strings on the right below,
# or the generic fallback (see _redact_provider_error_message's own
# docstring for why this can't be a best-effort filter instead).
_PROVIDER_ERROR_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("no longer available", "attachment file unavailable"),
    ("failed to read attached", "attachment file unavailable"),
    ("timed out", "request timed out"),
    ("timeout", "request timed out"),
    ("429", "rate limited or quota exceeded"),
    ("quota", "rate limited or quota exceeded"),
    ("resourceexhausted", "rate limited or quota exceeded"),
    ("rate limit", "rate limited or quota exceeded"),
    ("401", "authentication failed"),
    ("403", "authentication failed"),
    ("unauthorized", "authentication failed"),
    ("forbidden", "authentication failed"),
    ("api key", "authentication failed"),
    ("apikey", "authentication failed"),
    ("404", "resource not found"),
    ("not found", "resource not found"),
    ("connection", "connection failed"),
    ("connect", "connection failed"),
    ("network", "connection failed"),
    ("refused", "connection failed"),
)
_GENERIC_PROVIDER_ERROR_CATEGORY = "provider error (see application log for detail)"


def _redact_provider_error_message(message: str) -> str:
    """Classifies `message` (raw str(exc)) into one of the fixed category
    labels in _PROVIDER_ERROR_CATEGORIES above, falling back to a generic
    label when nothing matches. NEVER returns any substring of `message`
    itself - a regex that only strips out "known-dangerous-looking"
    patterns (e.g. absolute paths) would still leak whatever it doesn't
    recognize (arbitrary chat content echoed by an SDK's own validation
    error, say), which is exactly the failure this function exists to
    close off structurally rather than best-effort."""
    lowered = message.lower()
    for needle, category in _PROVIDER_ERROR_CATEGORIES:
        if needle in lowered:
            return category
    return _GENERIC_PROVIDER_ERROR_CATEGORY


def record_provider_error(provider: str, message: str) -> None:
    _provider_errors.append({
        "provider": provider,
        "message": _redact_provider_error_message(message),
        "at": time.time(),
    })


def provider_errors() -> list[dict[str, Any]]:
    return list(_provider_errors)


def reset_provider_errors() -> None:
    """Test-only: process-global state must not leak between tests."""
    _provider_errors.clear()


# -- per-session diagnostics -------------------------------------------------

_MAX_RECENT_RUNS = 30
_MAX_PUBLISH_SAMPLES = 50
# Publish rate is a trailing window, not a cumulative average - a burst 10
# minutes ago should not still be inflating "now".
_PUBLISH_RATE_WINDOW_SECONDS = 10.0


@dataclass
class _RunRecord:
    run_id: str
    kind: str
    node_id: str | None
    started_at: float
    ended_at: float | None = None
    outcome: str | None = None  # "completed" | "cancelled", None while in flight


@dataclass
class DiagnosticsState:
    session_count_fn: Callable[[], int] | None = None
    _runs: dict[str, _RunRecord] = field(default_factory=dict, repr=False)
    _recent_run_ids: deque[str] = field(default_factory=lambda: deque(maxlen=_MAX_RECENT_RUNS), repr=False)
    _publish_samples: deque[tuple[float, str, int]] = field(
        default_factory=lambda: deque(maxlen=_MAX_PUBLISH_SAMPLES), repr=False
    )
    _publish_count: int = 0
    _publish_bytes_total: int = 0

    def record_run_claimed(self, run_id: str, kind: str, node_id: str | None) -> None:
        self._runs[run_id] = _RunRecord(run_id=run_id, kind=kind, node_id=node_id, started_at=time.time())
        self._recent_run_ids.append(run_id)
        # A recycled dict never grows unbounded: any run pushed out of the
        # bounded _recent_run_ids deque is also dropped here, keyed by the
        # exact ids the deque still references.
        live_ids = set(self._recent_run_ids)
        for stale_id in [rid for rid in self._runs if rid not in live_ids]:
            del self._runs[stale_id]

    def record_run_ended(self, run_id: str, outcome: str) -> None:
        run = self._runs.get(run_id)
        if run is None:
            return  # already evicted by the bounded-history trim above
        run.ended_at = time.time()
        run.outcome = outcome

    def record_publish(self, topic: str, byte_size: int) -> None:
        now = time.time()
        self._publish_samples.append((now, topic, byte_size))
        self._publish_count += 1
        self._publish_bytes_total += byte_size

    def _publish_rate_bytes_per_second(self) -> float:
        if not self._publish_samples:
            return 0.0
        now = time.time()
        window_start = now - _PUBLISH_RATE_WINDOW_SECONDS
        windowed = [size for (at, _topic, size) in self._publish_samples if at >= window_start]
        if not windowed:
            return 0.0
        return round(sum(windowed) / _PUBLISH_RATE_WINDOW_SECONDS, 1)

    def payload(self) -> dict[str, Any]:
        recent_runs = []
        for run_id in reversed(self._recent_run_ids):
            run = self._runs.get(run_id)
            if run is None:
                continue
            duration = (run.ended_at - run.started_at) if run.ended_at is not None else None
            recent_runs.append({
                "runId": run.run_id,
                "kind": run.kind,
                "nodeId": run.node_id,
                "outcome": run.outcome or "running",
                "durationSeconds": round(duration, 3) if duration is not None else None,
            })
        last_publish = self._publish_samples[-1] if self._publish_samples else None
        return {
            "recentRuns": recent_runs,
            "publishCount": self._publish_count,
            "publishBytesTotal": self._publish_bytes_total,
            "lastPublishBytes": last_publish[2] if last_publish is not None else None,
            "lastPublishTopic": last_publish[1] if last_publish is not None else None,
            "publishBytesPerSecond": self._publish_rate_bytes_per_second(),
            "sessionCount": self.session_count_fn() if self.session_count_fn is not None else None,
            "providerErrors": [
                {"provider": e["provider"], "message": e["message"], "at": e["at"]}
                for e in provider_errors()
            ],
        }
