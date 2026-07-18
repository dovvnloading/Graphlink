"""Tests for graphlink_crash - crash capture, redaction, and the clean-shutdown sentinel.

Regression coverage for silent crashes: a windowed app with no console previously made
every unhandled exception and native fault invisible. This pins down two properties that
matter most:

1. Redaction is structural: build_crash_report() only ever reads sys.exc_info()-shaped
   arguments plus an explicit `context` dict the caller passes - it never reaches into app
   state itself, so a report cannot contain chat content/prompts that exist elsewhere in
   the process but were never explicitly handed to it. A test proves this directly: a
   "known chat string" sits in a variable the report-building call never receives, and the
   resulting report (and its JSON serialization) never contains it.
2. Path scrubbing: the user's home directory (which embeds the Windows username) is
   collapsed to "~" in the traceback text, defense-in-depth on top of (1).

Module-global state (sys.excepthook, threading.excepthook, graphlink_crash._installed) is
reset in teardown, mirroring the existing pattern in test_logging_setup.py for
graphlink_logging._configured.
"""

import json
import sys
import threading
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graphlink_crash


def _raise_and_capture():
    """Return (exc_type, exc_value, exc_tb) from a real raised-and-caught exception, so
    the traceback has real frames rather than being None."""
    try:
        raise ValueError("boom")
    except ValueError:
        return sys.exc_info()


class TestBuildCrashReport:
    def test_report_contains_the_core_fields(self):
        exc_type, exc_value, exc_tb = _raise_and_capture()

        report = graphlink_crash.build_crash_report(exc_type, exc_value, exc_tb, version="v1.2.3")

        assert report["app_version"] == "v1.2.3"
        assert report["exception_type"] == "ValueError"
        assert report["exception_message"] == "boom"
        assert "ValueError: boom" in report["traceback"]
        assert report["os"]
        assert report["python_version"]
        assert report["timestamp"]

    def test_context_defaults_to_empty_dict(self):
        exc_type, exc_value, exc_tb = _raise_and_capture()

        report = graphlink_crash.build_crash_report(exc_type, exc_value, exc_tb, version="v1")

        assert report["context"] == {}

    def test_explicit_context_is_included_verbatim(self):
        exc_type, exc_value, exc_tb = _raise_and_capture()

        report = graphlink_crash.build_crash_report(
            exc_type, exc_value, exc_tb, version="v1", context={"node_count": 3, "provider_mode": "ollama"},
        )

        assert report["context"] == {"node_count": 3, "provider_mode": "ollama"}

    def test_report_is_json_serializable(self):
        exc_type, exc_value, exc_tb = _raise_and_capture()
        report = graphlink_crash.build_crash_report(exc_type, exc_value, exc_tb, version="v1")

        json.dumps(report)  # must not raise


class TestRedactionIsStructural:
    def test_a_chat_string_never_passed_to_the_function_cannot_appear_in_the_report(self):
        # This "chat content" exists in the test process's memory, exactly like a real
        # conversation would exist in app memory during a crash - but it is never passed
        # to build_crash_report(), so it structurally cannot leak into the report.
        user_chat_content = "the user's private prompt about their medical history"  # noqa: F841

        exc_type, exc_value, exc_tb = _raise_and_capture()
        report = graphlink_crash.build_crash_report(exc_type, exc_value, exc_tb, version="v1")

        serialized = json.dumps(report)
        assert user_chat_content not in serialized
        assert graphlink_crash.format_crash_report_text(report).find(user_chat_content) == -1

    def test_home_directory_path_is_scrubbed_from_the_traceback(self, monkeypatch, tmp_path):
        fake_home = tmp_path / "Users" / "SomePrivateUsername"
        fake_home.mkdir(parents=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        try:
            raise ValueError(f"failed to read {fake_home / 'session.dat'}")
        except ValueError:
            exc_type, exc_value, exc_tb = sys.exc_info()

        report = graphlink_crash.build_crash_report(exc_type, exc_value, exc_tb, version="v1")

        assert "SomePrivateUsername" not in report["exception_message"]
        assert str(fake_home) not in report["exception_message"]
        assert "~" in report["exception_message"]


class TestFormatCrashReportText:
    def test_produces_readable_text_with_the_key_fields(self):
        exc_type, exc_value, exc_tb = _raise_and_capture()
        report = graphlink_crash.build_crash_report(exc_type, exc_value, exc_tb, version="v9.9.9")

        text = graphlink_crash.format_crash_report_text(report)

        assert "v9.9.9" in text
        assert "ValueError" in text
        assert "boom" in text

    def test_includes_context_when_present(self):
        exc_type, exc_value, exc_tb = _raise_and_capture()
        report = graphlink_crash.build_crash_report(exc_type, exc_value, exc_tb, version="v1", context={"k": "v"})

        assert '"k": "v"' in graphlink_crash.format_crash_report_text(report)


class TestWriteCrashReport:
    def test_writes_valid_json_under_a_timestamped_filename(self, tmp_path):
        exc_type, exc_value, exc_tb = _raise_and_capture()
        report = graphlink_crash.build_crash_report(exc_type, exc_value, exc_tb, version="v1")

        path = graphlink_crash.write_crash_report(report, crash_dir=tmp_path)

        assert path.exists()
        assert path.name.startswith("crash-")
        assert path.suffix == ".json"
        assert json.loads(path.read_text(encoding="utf-8"))["exception_type"] == "ValueError"

    def test_creates_the_crash_directory_if_missing(self, tmp_path):
        nested = tmp_path / "does" / "not" / "exist"
        exc_type, exc_value, exc_tb = _raise_and_capture()
        report = graphlink_crash.build_crash_report(exc_type, exc_value, exc_tb, version="v1")

        graphlink_crash.write_crash_report(report, crash_dir=nested)

        assert nested.is_dir()


class TestBuildGithubIssueUrl:
    def test_url_targets_the_repo_and_embeds_the_report(self):
        exc_type, exc_value, exc_tb = _raise_and_capture()
        report = graphlink_crash.build_crash_report(exc_type, exc_value, exc_tb, version="v1")

        url = graphlink_crash.build_github_issue_url(report, repo_issue_url="https://github.com/x/y/issues/new")

        assert url.startswith("https://github.com/x/y/issues/new?")
        assert "title=" in url
        assert "body=" in url
        # Percent-encoded, but the exception type/message must be present somewhere in it.
        from urllib.parse import unquote
        assert "ValueError" in unquote(url)
        assert "boom" in unquote(url)


class TestInstallCrashHandlers:
    def teardown_method(self):
        graphlink_crash.uninstall_crash_handlers()

    def test_installs_a_non_default_excepthook(self, tmp_path):
        graphlink_crash.install_crash_handlers(version="v1", crash_dir=tmp_path)

        assert sys.excepthook is not sys.__excepthook__

    def test_is_idempotent(self, tmp_path):
        graphlink_crash.install_crash_handlers(version="v1", crash_dir=tmp_path)
        first_hook = sys.excepthook

        graphlink_crash.install_crash_handlers(version="v1", crash_dir=tmp_path)

        assert sys.excepthook is first_hook

    def test_uninstall_restores_process_global_handlers(self, tmp_path):
        graphlink_crash.install_crash_handlers(version="v1", crash_dir=tmp_path)

        graphlink_crash.uninstall_crash_handlers()

        assert sys.excepthook is sys.__excepthook__
        assert threading.excepthook is threading.__excepthook__
        assert graphlink_crash._installed is False
        assert graphlink_crash._faulthandler_file is None

    def test_the_installed_excepthook_writes_a_report(self, tmp_path, monkeypatch):
        monkeypatch.setattr(graphlink_crash, "_crash_dir", lambda base_dir=None: tmp_path)
        graphlink_crash.install_crash_handlers(version="v1", crash_dir=tmp_path)

        try:
            raise RuntimeError("simulated crash")
        except RuntimeError:
            exc_type, exc_value, exc_tb = sys.exc_info()

        sys.excepthook(exc_type, exc_value, exc_tb)

        reports = list(tmp_path.glob("crash-*.json"))
        assert len(reports) == 1
        assert json.loads(reports[0].read_text(encoding="utf-8"))["exception_type"] == "RuntimeError"

    def test_the_installed_threading_excepthook_writes_a_report(self, tmp_path, monkeypatch):
        monkeypatch.setattr(graphlink_crash, "_crash_dir", lambda base_dir=None: tmp_path)
        graphlink_crash.install_crash_handlers(version="v1", crash_dir=tmp_path)

        try:
            raise RuntimeError("simulated thread crash")
        except RuntimeError:
            exc_type, exc_value, exc_tb = sys.exc_info()

        args = threading.ExceptHookArgs((exc_type, exc_value, exc_tb, threading.current_thread()))
        threading.excepthook(args)

        reports = list(tmp_path.glob("crash-*.json"))
        assert len(reports) == 1

    def test_a_broken_report_writer_does_not_propagate(self, tmp_path, monkeypatch):
        # The crash handler itself must never become a second crash. Install normally
        # first (so its own setup succeeds), THEN break the write path the excepthook
        # calls into.
        graphlink_crash.install_crash_handlers(version="v1", crash_dir=tmp_path)

        def _broken_write(report, crash_dir=None):
            raise OSError("disk full")
        monkeypatch.setattr(graphlink_crash, "write_crash_report", _broken_write)

        try:
            raise RuntimeError("simulated crash")
        except RuntimeError:
            exc_type, exc_value, exc_tb = sys.exc_info()

        sys.excepthook(exc_type, exc_value, exc_tb)  # must not raise


class TestCrashSentinel:
    def test_previous_run_crashed_is_false_when_no_sentinel_exists(self, tmp_path):
        assert graphlink_crash.previous_run_crashed(sentinel_dir=tmp_path) is False

    def test_mark_running_creates_the_sentinel(self, tmp_path):
        graphlink_crash.mark_running(version="v1", sentinel_dir=tmp_path)

        assert graphlink_crash.previous_run_crashed(sentinel_dir=tmp_path) is True
        contents = json.loads((tmp_path / "running.lock").read_text(encoding="utf-8"))
        assert contents["version"] == "v1"
        assert "pid" in contents

    def test_mark_clean_exit_removes_the_sentinel(self, tmp_path):
        graphlink_crash.mark_running(version="v1", sentinel_dir=tmp_path)

        graphlink_crash.mark_clean_exit(sentinel_dir=tmp_path)

        assert graphlink_crash.previous_run_crashed(sentinel_dir=tmp_path) is False

    def test_mark_clean_exit_is_safe_when_nothing_to_remove(self, tmp_path):
        graphlink_crash.mark_clean_exit(sentinel_dir=tmp_path)  # must not raise

    def test_full_lifecycle_mirrors_a_clean_run_then_a_crashed_run(self, tmp_path):
        # Run 1: starts, exits cleanly.
        graphlink_crash.mark_running(version="v1", sentinel_dir=tmp_path)
        graphlink_crash.mark_clean_exit(sentinel_dir=tmp_path)
        assert graphlink_crash.previous_run_crashed(sentinel_dir=tmp_path) is False

        # Run 2: starts, never reaches a clean exit (simulated crash - no mark_clean_exit).
        graphlink_crash.mark_running(version="v1", sentinel_dir=tmp_path)
        assert graphlink_crash.previous_run_crashed(sentinel_dir=tmp_path) is True
