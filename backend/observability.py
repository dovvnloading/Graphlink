"""ADR-016 stage 16.1: structured JSON-lines logging for the run lifecycle.

JsonLogFormatter renders one JSON object per log line - timestamp, level,
logger name, message, thread, plus whatever of `run_id`/`session_id`/`kind`/
`node_id` the call site attached via `extra={...}`. Wired onto the ROTATING
FILE handler only (backend/crash_recovery.py's configure_logging) - the
stderr StreamHandler stays plain-text, because that channel exists purely so
`python graphlink_desktop.py` run from a terminal is readable at a glance
(see that module's own docstring on why stderr was restored after the audit
fix that had silently dropped it). The file is what tooling/diagnostics
bundles (ADR-016 stage 16.4) and a human `grep`/`jq` actually parse, so that
is the one that needs a machine-readable shape.

Extra fields are passed explicitly via `extra={"run_id": ...}` at each call
site rather than threaded through a contextvars.ContextVar. A ContextVar set
inside RunRegistry.claim() would leak into unrelated log lines processed
later by the SAME long-lived coroutine (the WS read loop handles many
requests over its lifetime in one coroutine - see run_lifecycle.py's own
docstring on why claim() must stay a plain synchronous call with no `await`
in between); explicit `extra=` at each call site has no such cross-call
leak risk and matches how every existing `logger.exception(...)` call in
this codebase already looks.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

_VALID_LEVEL_NAMES = ("DEBUG", "INFO", "WARNING", "ERROR")

# The record attributes this formatter promotes into the JSON payload when a
# call site supplies them via extra=. Never required - a plain
# `logger.info("message")` with no extra still produces valid JSON, just
# without these keys.
_EXTRA_FIELDS = ("run_id", "session_id", "kind", "node_id")


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line. Not a subclass of any stdlib JSON logging
    helper - stdlib ships none; this is a small, dependency-free formatter
    matching the codebase's established "hand-roll a narrow, purpose-fit
    implementation instead of adding a dependency" precedent (structured_output.py,
    mcp_client.py)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "thread": record.threadName,
        }
        for field in _EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # ensure_ascii=False: this is a local log file, not a network
        # payload - keep non-ASCII text (user content in exception messages)
        # readable rather than \uXXXX-escaped.
        return json.dumps(payload, ensure_ascii=False)


def apply_log_level(level_name: str) -> None:
    """Sets the ROOT logger's level at runtime - safe to call any time after
    configure_logging() has attached its handlers (backend/crash_recovery.py),
    including from a live settings-change intent, unlike configure_logging()
    itself which is a one-shot, idempotent, process-wide setup call. An
    unrecognized name is a silent no-op (matches every other SettingsManager
    setter's closed-vocabulary posture - see set_log_level's own validation)
    rather than raising, since a stale/corrupted settings value must never
    crash startup over something as low-stakes as verbosity."""
    if level_name not in _VALID_LEVEL_NAMES:
        return
    logging.getLogger().setLevel(getattr(logging, level_name))
