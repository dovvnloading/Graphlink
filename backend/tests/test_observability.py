"""ADR-016 stage 16.1: backend/observability.py - JsonLogFormatter and
apply_log_level."""

from __future__ import annotations

import json
import logging

import pytest

from backend.observability import JsonLogFormatter, apply_log_level


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
    yield
    root_logger.setLevel(level_before)


def test_apply_log_level_sets_the_root_logger_level(isolated_root_level):
    apply_log_level("DEBUG")
    assert logging.getLogger().level == logging.DEBUG

    apply_log_level("ERROR")
    assert logging.getLogger().level == logging.ERROR


def test_apply_log_level_is_a_silent_noop_for_an_unrecognized_name(isolated_root_level):
    logging.getLogger().setLevel(logging.WARNING)
    apply_log_level("NOT_A_REAL_LEVEL")  # must not raise
    assert logging.getLogger().level == logging.WARNING
