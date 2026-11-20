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

from io import StringIO
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from graphlink_plugins.common import python_repl as python_repl_module
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


def test_stop_closes_the_subprocess_pipes_instead_of_leaving_them_to_gc():
    repl = PythonREPL(repl_id="core-pipe-cleanup-test")
    repl.execute("print('ready')")
    process = repl.process
    assert process is not None

    repl.stop()

    assert process.stdin is not None and process.stdin.closed
    assert process.stdout is not None and process.stdout.closed
    assert process.stderr is None


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


def test_invalid_stdout_bytes_do_not_desynchronize_the_persistent_repl():
    repl = PythonREPL(repl_id="core-invalid-stdout-test")
    try:
        # 0x81 is undecodable under UTF-8 and the usual Windows cp1252 locale.
        output = repl.execute("import os; os.write(1, b'\\x81\\n')")
        assert "\ufffd" in output
        assert repl.last_run_failed is False
        # The status boundary must still have been consumed by the same read.
        assert repl.execute("print('synchronized')") == "synchronized"
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

def test_one_oversized_unterminated_stdout_line_is_bounded_and_repl_stays_synchronized():
    """A child may emit many bytes before any newline; never buffer the full line."""
    repl = PythonREPL(repl_id="core-oversized-line-test")
    try:
        output = repl.execute("print('a' * 50000, end='')")
        assert "output truncated" in output
        assert len(output) <= 20_000
        assert repl.last_run_failed is False
        # Must consume through the nonce marker despite truncating the output.
        assert repl.execute("print(42)") == "42"
    finally:
        repl.stop()


def test_many_short_stdout_lines_are_bounded_and_next_execution_works():
    """Capping readline() alone is insufficient: total captured output needs a cap."""
    repl = PythonREPL(repl_id="core-many-output-lines-test")
    try:
        output = repl.execute("for _ in range(11_000): print('x')")
        assert "output truncated" in output
        assert len(output) <= 20_000
        assert repl.last_run_failed is False
        assert repl.execute("print(7)") == "7"
    finally:
        repl.stop()


def test_exactly_sized_reader_chunk_preserves_repl_framing():
    """A real output line split by the bounded reader must not hide the sentinel."""
    repl = PythonREPL(repl_id="core-reader-boundary-test")
    try:
        output = repl.execute("print('z' * 4096)")
        assert output == "z" * 4096
        assert repl.execute("print('next')") == "next"
    finally:
        repl.stop()



def test_live_callback_continues_after_model_output_is_truncated():
    repl = PythonREPL(repl_id="core-live-tail-test")
    pieces: list[str] = []
    try:
        result = repl.execute(
            "print('x' * 25000); print('late marker')",
            on_output=pieces.append,
        )
        assert "output truncated" in result
        assert "late marker" not in result
        assert "late marker" in "".join(pieces)
    finally:
        repl.stop()


def test_repl_emits_unbuffered_output_before_execution_finishes():
    """Output must reach the callback while the child is still sleeping."""
    repl = PythonREPL(repl_id="core-live-output-test")
    first_line = Event()
    pieces: list[str] = []
    finished: list[str] = []

    def on_output(text: str) -> None:
        pieces.append(text)
        if "first" in text:
            first_line.set()

    def run() -> None:
        finished.append(repl.execute(
            "import time; print('first'); time.sleep(1); print('second')",
            on_output=on_output,
        ))

    worker = Thread(target=run, daemon=True)
    try:
        worker.start()
        assert first_line.wait(5), "first line must stream before the sleep ends"
        assert worker.is_alive(), "the callback must fire before execute() returns"
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert "first" in "".join(pieces) and "second" in "".join(pieces)
        assert finished == ["first\nsecond"]
    finally:
        repl.stop()
        worker.join(timeout=5)


def test_flushed_unterminated_python_output_is_live_before_completion():
    """A flushed short fragment must not wait for the REPL's nonce newline."""
    repl = PythonREPL(repl_id="core-flushed-fragment-test")
    ready = Event()
    pieces: list[str] = []

    def on_output(fragment: str) -> None:
        pieces.append(fragment)
        if "READY" in "".join(pieces):
            ready.set()

    worker = Thread(
        target=lambda: repl.execute(
            "import sys, time; sys.stdout.write('READY'); "
            "sys.stdout.flush(); time.sleep(30)",
            on_output=on_output,
        ),
        daemon=True,
    )
    try:
        worker.start()
        assert ready.wait(7), "flushed text must stream without a newline"
        assert worker.is_alive(), "the child must still be running"
    finally:
        repl.stop()
        worker.join(timeout=8)
    assert not worker.is_alive()


@pytest.mark.parametrize("tail, next_prefix", [
    ("background-thread-output\n", ""),
    ("background-", "thread-output\n"),
])
def test_completion_marker_chunk_preserves_stdout_for_next_execution(
    monkeypatch, tmp_path, tail, next_prefix,
):
    """A read coalescing the nonce and later stdout must not eat its suffix."""
    repl = PythonREPL(cwd=tmp_path, manage_cwd=False)
    repl._boundary_prefix = "---GRAPHLINK_EXEC_BOUNDARY:fixture:"
    # The byte-reader fixture is deterministic; no child timing or pipe
    # scheduling can determine whether the marker and its suffix coalesce.
    repl.process = SimpleNamespace(
        stdin=StringIO(), stdout=StringIO(), stderr=None,
        poll=lambda: None, kill=lambda: None, wait=lambda: None,
    )
    fragments = iter((
        f"first\n{repl._boundary_prefix}OK---\n{tail}",
        f"{next_prefix}second\n{repl._boundary_prefix}OK---\n",
    ))
    monkeypatch.setattr(
        python_repl_module, "_stdout_fragments",
        lambda _stream: iter((next(fragments),)),
    )
    live: list[str] = []
    try:
        assert repl.execute("pass") == "first"
        assert repl.execute("pass", on_output=live.append) == "background-thread-output\nsecond"
        assert "".join(live) == "background-thread-output\nsecond\n"
        assert repl._stdout_pending == ""
        # Stopping/restarting must not replay another interpreter's tail.
        repl._stdout_pending = "stale\n"
    finally:
        repl.stop()
    assert repl._stdout_pending == ""
