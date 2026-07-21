"""Audit findings B2 + B4: PyCoder execution hardening.

B2 - error detection was `_is_error()`, a substring scan of stdout for
English keywords ("error:", "failed", ...): a correct program that merely
printed one of those words was marked as a failure and handed to the repair
agent, which could discard working code across up to 4 "fix" iterations. The
REPL wrapper now reports success/failure structurally on its boundary line
(PythonREPL.last_run_failed), and PyCoderExecutionWorker consumes that flag.

B4 - the execution boundary was matched by SUBSTRING ("marker in line"): a
program printing a line containing the marker truncated its own output and
left the real boundary unread in the pipe, desyncing every subsequent
execute() for the life of the REPL. The boundary now carries a per-session
random nonce and is matched as an exact full line.

The PythonREPL tests here run a REAL subprocess (that is the thing under
test); each stops its REPL so no orphaned process survives the test run.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from graphlink_agents_pycoder import PythonREPL, PyCoderExecutionWorker


@pytest.fixture
def repl():
    instance = PythonREPL()
    yield instance
    instance.stop()


class TestStructuralStatus:
    def test_successful_run_reports_ok(self, repl):
        output = repl.execute("print('hello')")

        assert output == "hello"
        assert repl.last_run_failed is False

    def test_raising_code_reports_error_structurally(self, repl):
        output = repl.execute("raise ValueError('boom')")

        assert repl.last_run_failed is True
        assert "ValueError" in output

    def test_printing_error_keywords_is_not_a_failure(self, repl):
        # The exact B2 misclassification: correct output that merely contains
        # the old keyword list ("failed", "error:", ...).
        output = repl.execute("print('0 tests failed'); print('Error: none')")

        assert repl.last_run_failed is False
        assert "0 tests failed" in output
        assert "Error: none" in output

    def test_repl_death_reports_failure_and_next_execute_recovers(self, repl):
        repl.execute("import sys; sys.exit(0)")
        assert repl.last_run_failed is True

        # The wrapper process died; execute() must transparently restart it.
        output = repl.execute("print('back alive')")
        assert output == "back alive"
        assert repl.last_run_failed is False


class TestBoundaryDesyncResistance:
    def test_output_containing_the_marker_text_is_captured_not_truncated(self, repl):
        # The exact B4 scenario: program output echoing the boundary framing.
        # The old substring match would truncate here and desync the NEXT call.
        output = repl.execute(
            "print('---GRAPHLINK_EXEC_BOUNDARY---')\n"
            "print('---GRAPHLINK_EXEC_BOUNDARY:deadbeef:OK---')\n"
            "print('after the fakes')"
        )

        assert "---GRAPHLINK_EXEC_BOUNDARY---" in output
        assert "after the fakes" in output
        assert repl.last_run_failed is False

        # And the stream is still in sync: the next call returns its own
        # output, not stale leftovers from the previous run.
        assert repl.execute("print('second call')") == "second call"

    def test_boundary_nonce_differs_per_session(self):
        a, b = PythonREPL(), PythonREPL()
        try:
            a.start()
            b.start()
            assert a._boundary_prefix != b._boundary_prefix
        finally:
            a.stop()
            b.stop()


class _FakeRepl:
    def __init__(self, output, last_run_failed):
        self._output = output
        self.last_run_failed = last_run_failed

    def execute(self, code):
        return self._output

    def stop(self):
        pass


def _run_worker(fake_repl):
    worker = PyCoderExecutionWorker("do a thing", [], fake_repl)
    worker.execution_agent = MagicMock()
    worker.execution_agent.get_response.return_value = "[TOOL:PYTHON]print('x')[/TOOL]"
    worker.repair_agent = MagicMock()
    worker.repair_agent.get_response.return_value = "print('repaired')"
    worker.analysis_agent = MagicMock()
    worker.analysis_agent.get_response.return_value = "analysis text"
    results = []
    worker.finished.connect(results.append)
    worker.approve()  # pre-approve so run() doesn't park on the gate
    worker.run()
    return worker, results


class TestWorkerUsesTheStructuralFlag:
    def test_benign_output_with_error_words_is_treated_as_success(self):
        worker, results = _run_worker(_FakeRepl("0 tests failed", last_run_failed=False))

        worker.repair_agent.get_response.assert_not_called()
        assert results and results[0]["output"] == "0 tests failed"

    def test_a_real_failure_with_benign_looking_output_triggers_repair(self):
        # Inverse of B2: the flag says failed even though no keyword appears -
        # the old keyword scan would have called this a success.
        worker, results = _run_worker(_FakeRepl("exit status 1", last_run_failed=True))

        worker.repair_agent.get_response.assert_called()
