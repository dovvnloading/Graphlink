"""ADR-016 stage 16.3: in-app diagnostics."""

from __future__ import annotations

import pytest

from backend.diagnostics import (
    DiagnosticsState,
    provider_errors,
    record_provider_error,
    reset_provider_errors,
)


@pytest.fixture(autouse=True)
def _isolated_provider_errors():
    """The provider-errors deque is module-global by design (see the
    module docstring) - reset around every test so this file's own
    assertions never see another test's leftovers, and vice versa."""
    reset_provider_errors()
    yield
    reset_provider_errors()


# -- provider errors (process-global) ---------------------------------------


def test_provider_errors_starts_empty():
    assert provider_errors() == []


def test_record_provider_error_appends_provider_and_message():
    # "connection refused" is redacted to its fixed category label - see
    # the redaction tests below for why the raw text is never stored.
    record_provider_error("ollama", "connection refused")
    errors = provider_errors()
    assert len(errors) == 1
    assert errors[0]["provider"] == "ollama"
    assert errors[0]["message"] == "connection failed"
    assert isinstance(errors[0]["at"], float)


def test_provider_errors_is_bounded():
    # Distinguish entries by `provider` (not `message`, which is now
    # collapsed to a redacted category label - see the redaction tests
    # below) to keep proving FIFO eviction of the bounded deque.
    for i in range(15):
        record_provider_error(f"provider-{i}", "some transient failure")
    errors = provider_errors()
    assert len(errors) == 10  # _MAX_PROVIDER_ERRORS
    assert errors[0]["provider"] == "provider-5"  # oldest 5 evicted
    assert errors[-1]["provider"] == "provider-14"


# -- DiagnosticsState: run history --------------------------------------


def test_recent_runs_starts_empty():
    diagnostics = DiagnosticsState()
    assert diagnostics.payload()["recentRuns"] == []


def test_record_run_claimed_shows_up_as_running():
    diagnostics = DiagnosticsState()
    diagnostics.record_run_claimed("r-1", "chat", "n-1")

    runs = diagnostics.payload()["recentRuns"]
    assert len(runs) == 1
    assert runs[0]["runId"] == "r-1"
    assert runs[0]["kind"] == "chat"
    assert runs[0]["nodeId"] == "n-1"
    assert runs[0]["outcome"] == "running"
    assert runs[0]["durationSeconds"] is None


def test_record_run_ended_sets_outcome_and_duration():
    diagnostics = DiagnosticsState()
    diagnostics.record_run_claimed("r-1", "chat", "n-1")
    diagnostics.record_run_ended("r-1", "completed")

    runs = diagnostics.payload()["recentRuns"]
    assert runs[0]["outcome"] == "completed"
    assert runs[0]["durationSeconds"] is not None
    assert runs[0]["durationSeconds"] >= 0


def test_record_run_ended_for_an_unknown_run_id_is_a_safe_noop():
    diagnostics = DiagnosticsState()
    diagnostics.record_run_ended("never-claimed", "completed")  # must not raise
    assert diagnostics.payload()["recentRuns"] == []


def test_recent_runs_lists_newest_first():
    diagnostics = DiagnosticsState()
    diagnostics.record_run_claimed("r-1", "chat", None)
    diagnostics.record_run_claimed("r-2", "chart", None)

    runs = diagnostics.payload()["recentRuns"]
    assert [r["runId"] for r in runs] == ["r-2", "r-1"]


def test_recent_runs_is_bounded():
    diagnostics = DiagnosticsState()
    for i in range(35):
        diagnostics.record_run_claimed(f"r-{i}", "chat", None)

    runs = diagnostics.payload()["recentRuns"]
    assert len(runs) == 30  # _MAX_RECENT_RUNS
    assert runs[0]["runId"] == "r-34"  # newest first
    assert runs[-1]["runId"] == "r-5"  # oldest 5 evicted


# -- DiagnosticsState: publish size/rate ---------------------------------


def test_publish_stats_start_at_zero():
    diagnostics = DiagnosticsState()
    payload = diagnostics.payload()
    assert payload["publishCount"] == 0
    assert payload["publishBytesTotal"] == 0
    assert payload["lastPublishBytes"] is None
    assert payload["lastPublishTopic"] is None
    assert payload["publishBytesPerSecond"] == 0.0


def test_record_publish_updates_count_total_and_last():
    diagnostics = DiagnosticsState()
    diagnostics.record_publish("scene", 100)
    diagnostics.record_publish("scene", 250)

    payload = diagnostics.payload()
    assert payload["publishCount"] == 2
    assert payload["publishBytesTotal"] == 350
    assert payload["lastPublishBytes"] == 250
    assert payload["lastPublishTopic"] == "scene"


def test_publish_bytes_per_second_reflects_recent_samples():
    diagnostics = DiagnosticsState()
    diagnostics.record_publish("scene", 1000)
    assert diagnostics.payload()["publishBytesPerSecond"] > 0.0


# -- DiagnosticsState: session count + provider errors in the payload ---


def test_session_count_is_none_without_a_session_count_fn():
    diagnostics = DiagnosticsState()
    assert diagnostics.payload()["sessionCount"] is None


def test_session_count_reads_the_live_accessor():
    diagnostics = DiagnosticsState(session_count_fn=lambda: 3)
    assert diagnostics.payload()["sessionCount"] == 3


def test_payload_includes_provider_errors_from_the_shared_process_global_view():
    record_provider_error("Anthropic Claude", "the request timed out")
    diagnostics = DiagnosticsState()
    errors = diagnostics.payload()["providerErrors"]
    assert len(errors) == 1
    assert errors[0]["provider"] == "Anthropic Claude"
    assert errors[0]["message"] == "request timed out"


# -- provider error message redaction (see backend/diagnostics.py's own ----
# -- module docstring: this is the one field in the whole diagnostics ------
# -- allowlist that isn't inherently content-free by construction) ---------


CANARY = "CANARY-SECRET-CHAT-CONTENT-DO-NOT-LEAK-12345"


def test_record_provider_error_never_stores_arbitrary_message_text():
    """The empirical proof: literal content passed as `message` (standing
    in for a provider SDK's raw exception text, which can embed anything -
    an absolute filesystem path, or even echoed chat content) must never
    survive into provider_errors() verbatim."""
    record_provider_error("openai", CANARY)
    errors = provider_errors()
    assert len(errors) == 1
    assert CANARY not in errors[0]["message"]
    assert errors[0]["message"] == "provider error (see application log for detail)"


def test_record_provider_error_redacts_an_absolute_windows_path_in_an_os_error():
    """The concretely-demonstrated real-world trigger: api_provider.py's
    _read_attachment_bytes interpolates a raw OSError, whose __str__ for a
    failed open() embeds the FULL absolute path - including the OS
    username and any private folder/file names."""
    leaky_message = (
        r"Failed to read attached image file 'secret_photo.png': "
        r"[Errno 2] No such file or directory: "
        r"'C:\\Users\\Someone\\Documents\\Private\\secret_photo.png'"
    )
    record_provider_error("openai", leaky_message)
    errors = provider_errors()
    assert "Someone" not in errors[0]["message"]
    assert "Private" not in errors[0]["message"]
    assert "secret_photo" not in errors[0]["message"]
    assert errors[0]["message"] == "attachment file unavailable"


@pytest.mark.parametrize(
    "message,expected_category",
    [
        ("Request timed out after 30s", "request timed out"),
        ("HTTP 429: rate limit exceeded", "rate limited or quota exceeded"),
        ("google.api_core.exceptions.ResourceExhausted: quota exceeded", "rate limited or quota exceeded"),
        ("401 Unauthorized: invalid API key provided", "authentication failed"),
        ("Connection refused by remote host", "connection failed"),
        ("HTTP 404: model not found", "resource not found"),
        ("Attached audio file is no longer available: clip.wav", "attachment file unavailable"),
    ],
)
def test_record_provider_error_classifies_known_shapes_without_echoing_them(message, expected_category):
    record_provider_error("openai", message)
    errors = provider_errors()
    assert errors[0]["message"] == expected_category
