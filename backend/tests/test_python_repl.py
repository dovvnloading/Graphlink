"""PLAN-2026-08-24 H5: lean core coverage for the shared PythonREPL class
(graphlink_plugins/common/python_repl.py), reused from the retired Py-Coder
plugin's own now-deleted test_pycoder_domain.py.

Against a REAL subprocess, not mocks - the whole point of this class is
persistent interpreter state and process lifecycle, neither of which a mock
can prove. Deliberately lean (per this repo's own test-suite discipline):
just the load-bearing behaviors - state persists across calls, a failed run
is reported without killing the REPL, cwd is genuinely isolated (never the
app's own working directory), and a dead process transparently restarts on
the next execute() - not one test per internal code path (guard wiring and
scratch-dir wiring already have their own dedicated coverage in
test_execution_guard.py).
"""

from __future__ import annotations

from graphlink_plugins.common.python_repl import PythonREPL


def test_state_persists_across_calls_and_reports_success():
    repl = PythonREPL(repl_id="core-state-test")
    try:
        repl.execute("x = 21")
        result = repl.execute("print(x * 2)")

        assert result == "42"
        assert repl.last_run_failed is False
    finally:
        repl.stop()


def test_a_failed_execution_is_reported_without_killing_the_repl():
    repl = PythonREPL(repl_id="core-failure-test")
    try:
        repl.execute("y = 5")
        failure_output = repl.execute("1 / 0")

        assert repl.last_run_failed is True
        assert "ZeroDivisionError" in failure_output

        # The REPL itself must still be alive with its prior state intact -
        # a failed user script is not a REPL crash.
        recovery = repl.execute("print(y)")
        assert recovery == "5"
        assert repl.last_run_failed is False
    finally:
        repl.stop()


def test_cwd_is_a_private_scratch_dir_not_the_apps_own_working_directory():
    repl = PythonREPL(repl_id="core-cwd-test")
    try:
        repl.execute("open('marker.txt', 'w').write('hi')")

        assert (repl.cwd / "marker.txt").is_file(), (
            "a relative-path write must land in the REPL's own scratch dir"
        )
    finally:
        repl.stop()


def test_two_repl_ids_get_different_isolated_cwds():
    a = PythonREPL(repl_id="core-isolation-a")
    b = PythonREPL(repl_id="core-isolation-b")
    assert a.cwd != b.cwd


def test_a_dead_process_is_transparently_restarted_on_the_next_execute():
    repl = PythonREPL(repl_id="core-restart-test")
    try:
        repl.execute("z = 99")
        dead_pid = repl.process.pid

        # Simulate a hard crash (e.g. the executed code called sys.exit() or
        # segfaulted) - stdout EOF with no boundary line, exactly what
        # execute() itself detects and reacts to.
        repl.stop()
        assert repl.process is None

        result = repl.execute("print(1 + 1)")

        assert result == "2"
        assert repl.last_run_failed is False
        assert repl.process is not None and repl.process.pid != dead_pid
    finally:
        repl.stop()
