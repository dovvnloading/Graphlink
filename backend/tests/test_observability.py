"""ADR-016 stage 16.1: backend/observability.py - JsonLogFormatter and
apply_log_level."""

from __future__ import annotations

import json
import logging

import pytest

from backend.observability import JsonLogFormatter, apply_log_level, resolve_log_level


def _make_record(msg="hello", level=logging.INFO, extra=None):
    record = logging.LogRecord(
        name="graphlink.test", level=level, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=None,
    )
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return record


def test_format_produces_valid_json():
    formatted = JsonLogFormatter().format(_make_record())
    payload = json.loads(formatted)  # must not raise
    assert payload["msg"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "graphlink.test"
    assert "ts" in payload
    assert "thread" in payload


def test_format_omits_extra_fields_when_not_supplied():
    payload = json.loads(JsonLogFormatter().format(_make_record()))
    for field in ("run_id", "session_id", "kind", "node_id"):
        assert field not in payload


def test_format_includes_extra_fields_when_supplied():
    record = _make_record(extra={"run_id": "r-1", "kind": "chat", "node_id": "n-1"})
    payload = json.loads(JsonLogFormatter().format(record))
    assert payload["run_id"] == "r-1"
    assert payload["kind"] == "chat"
    assert payload["node_id"] == "n-1"
    assert "session_id" not in payload


def test_format_includes_formatted_exception_when_present():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        exc_info = sys.exc_info()
    record = logging.LogRecord(
        name="graphlink.test", level=logging.ERROR, pathname=__file__, lineno=1,
        msg="failed", args=(), exc_info=exc_info,
    )
    payload = json.loads(JsonLogFormatter().format(record))
    assert "ValueError" in payload["exc"]
    assert "boom" in payload["exc"]


def test_format_preserves_non_ascii_text_unescaped():
    payload_text = JsonLogFormatter().format(_make_record(msg="café"))
    assert "café" in payload_text  # not \u-escaped


@pytest.fixture
def isolated_root_level():
    root_logger = logging.getLogger()
    level_before = root_logger.level
    third_party_levels_before = {
        name: logging.getLogger(name).level for name in ("openai", "anthropic")
    }
    yield
    root_logger.setLevel(level_before)
    for name, level in third_party_levels_before.items():
        logging.getLogger(name).setLevel(level)


def test_apply_log_level_sets_the_root_logger_level(isolated_root_level):
    apply_log_level("DEBUG")
    assert logging.getLogger().level == logging.DEBUG

    apply_log_level("ERROR")
    assert logging.getLogger().level == logging.ERROR


def test_apply_log_level_is_a_silent_noop_for_an_unrecognized_name(isolated_root_level):
    logging.getLogger().setLevel(logging.WARNING)
    apply_log_level("NOT_A_REAL_LEVEL")  # must not raise
    assert logging.getLogger().level == logging.WARNING


# -- SECURITY-FIX: resolve_log_level must never raise on a hostile/corrupted
# -- persisted log_level - graphlink_desktop.py's boot path has no handler or
# -- exception-handler installed yet, so a raise here crashed every launch. --


def test_resolve_log_level_maps_every_real_level_name():
    assert resolve_log_level("DEBUG") == logging.DEBUG
    assert resolve_log_level("INFO") == logging.INFO
    assert resolve_log_level("WARNING") == logging.WARNING
    assert resolve_log_level("ERROR") == logging.ERROR


def test_resolve_log_level_falls_back_to_default_for_an_unrecognized_name():
    # Naming a real logging-module attribute that isn't a level (the actual
    # reported crash: getattr(logging, "shutdown", INFO) returns the
    # shutdown FUNCTION, which setLevel then rejects with TypeError).
    assert resolve_log_level("shutdown") == logging.INFO
    assert resolve_log_level("Formatter") == logging.INFO
    assert resolve_log_level("not_a_real_level") == logging.INFO


def test_resolve_log_level_falls_back_to_default_for_a_non_string_value():
    # A JSON list/int/null read straight off a hand-edited session.dat -
    # getattr(logging, value, INFO) raises TypeError for these outright,
    # since the attribute-name argument must be a string.
    assert resolve_log_level(["DEBUG"]) == logging.INFO
    assert resolve_log_level(42) == logging.INFO
    assert resolve_log_level(None) == logging.INFO


def test_resolve_log_level_honors_a_custom_default():
    assert resolve_log_level("nonsense", default=logging.ERROR) == logging.ERROR


def test_apply_log_level_debug_caps_third_party_sdk_loggers_to_info(isolated_root_level):
    # OBS-1: DEBUG on the root must not cascade into openai/anthropic's own
    # internal loggers - their _base_client dumps the full request body
    # (every chat message) at DEBUG, and that would otherwise reach the
    # rotating file handler via plain logging propagation.
    apply_log_level("DEBUG")
    assert logging.getLogger().level == logging.DEBUG
    assert logging.getLogger("openai").level == logging.INFO
    assert logging.getLogger("anthropic").level == logging.INFO


def test_apply_log_level_debug_still_reaches_the_app_own_loggers(isolated_root_level):
    # The cap is specific to the third-party SDK logger names - it must not
    # dampen the app's own debug logging, which has no explicit level of its
    # own and so inherits the root's effective level like any other logger.
    apply_log_level("DEBUG")
    app_logger = logging.getLogger("graphlink.test.some_module")
    assert app_logger.getEffectiveLevel() == logging.DEBUG


def test_apply_log_level_deescalates_third_party_sdk_loggers_after_a_prior_debug_call(
    isolated_root_level,
):
    apply_log_level("DEBUG")
    assert logging.getLogger("openai").level == logging.INFO
    assert logging.getLogger("anthropic").level == logging.INFO

    apply_log_level("WARNING")
    assert logging.getLogger().level == logging.WARNING
    # Must track the newly requested level, not stay stuck at the INFO cap
    # left behind by the earlier DEBUG call.
    assert logging.getLogger("openai").level == logging.WARNING
    assert logging.getLogger("anthropic").level == logging.WARNING
