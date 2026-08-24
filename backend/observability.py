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

# Third-party SDK loggers whose own internal DEBUG logging includes the full
# outbound request body: openai._base_client and anthropic._base_client both
# do `log.debug("Request options: %s", model_dump(options))` on every call,
# where `options` is the FinalRequestOptions carrying `json_data` - i.e.
# every chat message and system prompt sent to the provider. Both loggers
# are created via `logging.getLogger(__name__)` inside a `_base_client`
# submodule, so capping the PACKAGE-ROOT name here governs every descendant
# logger through Python's ancestor-lookup rule - no need to enumerate
# `openai._base_client`, `openai._legacy_response`, etc individually.
# (httpx/httpcore, the shared HTTP transport underneath both SDKs, were
# checked too and don't need a cap: httpx logs its request/response lines at
# INFO, not DEBUG, and httpcore's DEBUG trace logs repr() a Request object
# whose __repr__ is `<Request [b'POST']>` - method only, no body.)
_THIRD_PARTY_SDK_LOGGER_NAMES = ("openai", "anthropic")

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


def resolve_log_level(level_name: object, default: int = logging.INFO) -> int:
    """SECURITY-FIX: maps a persisted log-level NAME to its logging-module
    int constant, falling back to `default` for anything outside the closed
    vocabulary - including a non-string JSON value (a list/int/null read
    straight off session.dat) - instead of raising. graphlink_desktop.py's
    main() used to do this itself via a bare
    `getattr(logging, SettingsManager().get_log_level(), logging.INFO)`:
    getattr's default only covers a MISSING attribute, not a present-but-
    wrong-shaped one, so a persisted value naming any other module
    attribute ("shutdown", "Formatter", "handlers") returned that object
    instead of an int, and a non-string value raised TypeError outright -
    either way crashing every launch, before configure_logging ever attaches
    a handler or install_exception_handlers runs. Called before any handler
    exists, so unlike apply_log_level below this can't just skip silently -
    it must always return something configure_logging can use."""
    if not isinstance(level_name, str) or level_name not in _VALID_LEVEL_NAMES:
        return default
    return getattr(logging, level_name)


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
    level = getattr(logging, level_name)
    logging.getLogger().setLevel(level)
    # SECURITY-FIX (OBS-1-debug-level-logs-chat-content): the ROOT setLevel
    # above lets openai/anthropic's own internal loggers inherit DEBUG via
    # normal propagation, which would dump the full request body (see the
    # comment on _THIRD_PARTY_SDK_LOGGER_NAMES above) into graphlink.log -
    # the file this app's own bug-report/Diagnostics flow tells users to
    # attach. Cap those loggers to no more verbose than INFO explicitly, on
    # EVERY call (not just when DEBUG is requested), so a later call with a
    # less verbose level correctly de-escalates them too instead of leaving
    # them pinned at a stale INFO cap from a previous DEBUG call.
    third_party_level = max(level, logging.INFO)
    for logger_name in _THIRD_PARTY_SDK_LOGGER_NAMES:
        logging.getLogger(logger_name).setLevel(third_party_level)
