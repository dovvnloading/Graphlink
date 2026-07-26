"""Crash recovery tests (Qt-removal plan R6.7).

configure_logging/install_exception_handlers mutate PROCESS-WIDE global state
(the root logger's handlers, sys.excepthook, threading.excepthook,
faulthandler) by design - see backend/crash_recovery.py's own docstring on
why they're idempotent and never called from create_app(). Every test that
exercises them resets that global state in a finally block so nothing leaks
into unrelated tests elsewhere in the suite.
"""

import json
import logging
import logging.handlers
import os
import sys
import threading
from pathlib import Path

import pytest

from backend.crash_recovery import (
    CRASH_NOTICE_MESSAGE,
    configure_logging,
    crash_dir,
    install_exception_handlers,
    log_path,
    maybe_show_crash_notice,
    mark_clean_exit,
    mark_running,
    previous_run_crashed,
    sentinel_path,
)
import backend.crash_recovery as crash_recovery_module
from backend.notifications import NotificationState


# -- path construction --------------------------------------------------


def test_paths_default_to_the_established_graphlink_data_dir():
    home_graphlink = Path.home() / ".graphlink"
    assert log_path() == home_graphlink / "graphlink.log"
    assert sentinel_path() == home_graphlink / "running.lock"
    assert crash_dir() == home_graphlink / "crash"


def test_paths_honor_a_base_dir_override(tmp_path):
    assert log_path(tmp_path) == tmp_path / "graphlink.log"
    assert sentinel_path(tmp_path) == tmp_path / "running.lock"
    assert crash_dir(tmp_path) == tmp_path / "crash"


# -- sentinel lifecycle ---------------------------------------------------


def test_previous_run_crashed_is_false_with_no_sentinel_present(tmp_path):
    assert previous_run_crashed(tmp_path) is False


def test_mark_running_writes_a_valid_json_sentinel_with_pid(tmp_path):
    mark_running(tmp_path)

    path = sentinel_path(tmp_path)
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["pid"] > 0
    assert "started_at" in payload


def test_mark_running_creates_the_parent_directory(tmp_path):
    nested = tmp_path / "does" / "not" / "exist" / "yet"
    mark_running(nested)
    assert sentinel_path(nested).is_file()


def test_previous_run_crashed_is_true_once_marked_running(tmp_path):
    mark_running(tmp_path)
    assert previous_run_crashed(tmp_path) is True


def test_mark_clean_exit_removes_the_sentinel(tmp_path):
    mark_running(tmp_path)
    mark_clean_exit(tmp_path)
    assert previous_run_crashed(tmp_path) is False


def test_mark_clean_exit_is_a_no_op_when_no_sentinel_exists(tmp_path):
    # Must never raise - called on every controlled early-return path in
    # graphlink_desktop.py's main(), including ones before mark_running
    # could plausibly have failed to run.
    mark_clean_exit(tmp_path)
    assert previous_run_crashed(tmp_path) is False


def test_full_lifecycle_clean_shutdown_leaves_no_crash_evidence(tmp_path):
    assert previous_run_crashed(tmp_path) is False  # first-ever launch
    mark_running(tmp_path)
    mark_clean_exit(tmp_path)  # this run shut down cleanly
    assert previous_run_crashed(tmp_path) is False  # next launch sees no crash


def test_full_lifecycle_unclean_shutdown_is_detected_next_launch(tmp_path):
    mark_running(tmp_path)
    # ... process dies here without ever reaching mark_clean_exit ...
    assert previous_run_crashed(tmp_path) is True  # next launch sees the crash
    mark_clean_exit(tmp_path)
    assert previous_run_crashed(tmp_path) is False  # and clears it going forward


# -- in-app notice ----------------------------------------------------------


def test_maybe_show_crash_notice_shows_the_warning_when_crashed():
    notifications = NotificationState()
    maybe_show_crash_notice(notifications, True)

    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert notifications.message == CRASH_NOTICE_MESSAGE


def test_maybe_show_crash_notice_is_a_no_op_when_not_crashed():
    notifications = NotificationState()
    maybe_show_crash_notice(notifications, False)

    assert notifications.visible is False
    assert notifications.message == ""


# -- logging + exception-handler configuration (idempotent, global state) --


@pytest.fixture
def isolated_logging_state(monkeypatch):
    """configure_logging's idempotency flag is module-global by design (see
    the module docstring) - flip it back to unconfigured for this test only,
    and strip whatever handler(s) got attached to the REAL root logger so
    they don't outlive the test (which would otherwise leave a handler
    pointed at this test's about-to-be-deleted tmp_path, breaking any later
    test in the suite that logs anything)."""
    root_logger = logging.getLogger()
    handlers_before = list(root_logger.handlers)
    level_before = root_logger.level
    monkeypatch.setattr(crash_recovery_module, "_logging_configured", False)
    yield
    for handler in list(root_logger.handlers):
        if handler not in handlers_before:
            root_logger.removeHandler(handler)
            handler.close()
    root_logger.setLevel(level_before)


@pytest.fixture
def isolated_exception_handler_state(monkeypatch):
    """Same idempotency concern as configure_logging, for
    install_exception_handlers - restores sys.excepthook/threading.excepthook
    to their pre-test values and closes the faulthandler file this test
    caused to be opened."""
    excepthook_before = sys.excepthook
    threading_excepthook_before = threading.excepthook
    monkeypatch.setattr(crash_recovery_module, "_handlers_installed", False)
    yield
    sys.excepthook = excepthook_before
    threading.excepthook = threading_excepthook_before
    if crash_recovery_module._faulthandler_file is not None:
        try:
            crash_recovery_module._faulthandler_file.close()
        except (OSError, ValueError):
            pass
        crash_recovery_module._faulthandler_file = None


def test_configure_logging_attaches_a_rotating_file_handler(tmp_path, isolated_logging_state):
    configure_logging(tmp_path)

    assert log_path(tmp_path).parent.is_dir()
    root_logger = logging.getLogger()
    assert any(
        isinstance(h, logging.handlers.RotatingFileHandler) and h.baseFilename == str(log_path(tmp_path))
        for h in root_logger.handlers
    )


def test_configure_logging_is_idempotent(tmp_path, isolated_logging_state):
    configure_logging(tmp_path)
    handler_count_after_first = len(logging.getLogger().handlers)

    configure_logging(tmp_path)  # a second call must be a no-op
    assert len(logging.getLogger().handlers) == handler_count_after_first


def test_install_exception_handlers_replaces_both_hooks(tmp_path, isolated_exception_handler_state):
    original_excepthook = sys.excepthook
    original_threading_excepthook = threading.excepthook

    install_exception_handlers(tmp_path)

    assert sys.excepthook is not original_excepthook
    assert threading.excepthook is not original_threading_excepthook
    assert (crash_dir(tmp_path) / "faulthandler.log").is_file()


def test_install_exception_handlers_is_idempotent(tmp_path, isolated_exception_handler_state):
    install_exception_handlers(tmp_path)
    hook_after_first = sys.excepthook

    install_exception_handlers(tmp_path)  # a second call must be a no-op
    assert sys.excepthook is hook_after_first


def test_excepthook_logs_the_unhandled_exception_and_still_calls_the_default_hook(
    tmp_path, isolated_logging_state, isolated_exception_handler_state, monkeypatch,
):
    configure_logging(tmp_path)
    install_exception_handlers(tmp_path)

    default_hook_calls = []
    monkeypatch.setattr(sys, "__excepthook__", lambda *args: default_hook_calls.append(args))

    try:
        raise ValueError("boom")
    except ValueError:
        exc_type, exc_value, exc_tb = sys.exc_info()

    sys.excepthook(exc_type, exc_value, exc_tb)

    log_content = log_path(tmp_path).read_text(encoding="utf-8")
    assert "boom" in log_content
    assert "ValueError" in log_content
    assert len(default_hook_calls) == 1


# -- audit fixes --


def test_mark_clean_exit_leaves_another_instances_sentinel_alone(tmp_path):
    # Audit finding: mark_clean_exit unlinked unconditionally, so two
    # concurrent instances corrupted each other - B overwrites the lock with
    # its own pid, A then exits cleanly and deletes B's lock, and a later
    # crash of B goes completely unreported on the next launch.
    mark_running(tmp_path)
    payload = json.loads(sentinel_path(tmp_path).read_text(encoding="utf-8"))
    payload["pid"] = os.getpid() + 1  # as if a second instance had marked it
    sentinel_path(tmp_path).write_text(json.dumps(payload), encoding="utf-8")

    mark_clean_exit(tmp_path)

    assert previous_run_crashed(tmp_path), "a sibling instance's lock must survive"


def test_mark_clean_exit_still_removes_our_own_sentinel(tmp_path):
    mark_running(tmp_path)

    mark_clean_exit(tmp_path)

    assert not previous_run_crashed(tmp_path)


def test_mark_clean_exit_clears_a_malformed_sentinel_rather_than_stranding_it(tmp_path):
    # A lock we can't parse must not become permanent - that would report a
    # phantom crash on every launch, forever.
    sentinel_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    sentinel_path(tmp_path).write_text("not json", encoding="utf-8")

    mark_clean_exit(tmp_path)

    assert not previous_run_crashed(tmp_path)


def test_configure_logging_keeps_a_stderr_handler_so_terminal_runs_still_print(tmp_path, isolated_logging_state):
    # Audit finding: this replaced graphlink_desktop.py's logging.basicConfig,
    # which had provided the only stderr handler. Without one, root HAS a
    # handler (so logging.lastResort never fires) but nothing reaches the
    # console - `python graphlink_desktop.py` with a missing SPA build printed
    # nothing at all and exited 1, despite logging a perfectly good error.
    configure_logging(tmp_path)

    stream_handlers = [
        h for h in logging.getLogger().handlers
        if type(h) is logging.StreamHandler
    ]
    assert stream_handlers, "terminal runs must still see log output"


def test_install_exception_handlers_can_be_retried_after_a_failure(
    tmp_path, monkeypatch, isolated_exception_handler_state
):
    # Audit finding: the installed flag was set BEFORE the work, so a single
    # failure (unwritable ~/.graphlink/crash, full disk) latched it forever
    # and every retry became a silent no-op - leaving sys.excepthook
    # permanently uninstalled with no second chance.
    import builtins

    original_hook = sys.excepthook
    real_open = builtins.open

    def failing_open(*args, **kwargs):
        raise PermissionError("crash dir is not writable")

    monkeypatch.setattr(builtins, "open", failing_open)
    with pytest.raises(PermissionError):
        install_exception_handlers(tmp_path)
    assert sys.excepthook is original_hook

    monkeypatch.setattr(builtins, "open", real_open)
    install_exception_handlers(tmp_path)

    assert sys.excepthook is not original_hook, "a retry after a failure must actually install"
