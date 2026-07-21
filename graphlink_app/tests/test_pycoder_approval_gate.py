"""Tests for the Py-Coder execution approval gate.

Regression coverage for ungated AI-generated code execution: Py-Coder's AI_DRIVEN
mode executed LLM-generated code in a completely unsandboxed REPL subprocess (full
user-account privileges) with no approval step, while the newer Code Sandbox plugin has
always had an explicit approval dialog for the same threat - an inconsistent trust model.

PyCoderExecutionWorker now has the same approval_requested/approve()/deny() contract as
CodeSandboxExecutionWorker (see tests/test_code_sandbox_approval_gate.py): run() parks
on the approval event after code is generated and before anything reaches the REPL, and
only proceeds if the main thread approves. deny() or stop() must short-circuit the run
without ever executing code. Repair-loop iterations run under the same single approval,
matching the sandbox's behavior for its own repair attempts.

MANUAL mode (CodeExecutionWorker) is deliberately ungated: there the user authored the
code themselves and clicking Run is the approval - covered by a test below so the
boundary of the gate is pinned down explicitly, not implied.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

from graphlink_agents_pycoder import CodeExecutionWorker, PyCoderExecutionWorker

_APP = QApplication.instance() or QApplication([])

GENERATED_RESPONSE = "[TOOL:PYTHON]print('hello')[/TOOL]"


class _FakeRepl:
    def __init__(self, output="ok", last_run_failed=False):
        self.executed = []
        self.output = output
        self.stopped = False
        # The worker reads failure structurally from this flag (audit B2) -
        # NOT by scanning the output text for error keywords.
        self.last_run_failed = last_run_failed

    def execute(self, code):
        self.executed.append(code)
        return self.output

    def stop(self):
        self.stopped = True


def _make_worker(repl=None, generated_response=GENERATED_RESPONSE):
    worker = PyCoderExecutionWorker("do a thing", [], repl or _FakeRepl())
    # Stub every LLM call so no network/API access is needed to reach the gate.
    worker.execution_agent.get_response = lambda history, prompt: generated_response
    worker.repair_agent.get_response = lambda code, error, is_final: code
    worker.analysis_agent.get_response = lambda *a, **k: "stub analysis"
    return worker


class TestApprovalGateDenied:
    def test_deny_prevents_any_repl_execution(self):
        repl = _FakeRepl()
        worker = _make_worker(repl)

        errors = []
        worker.error.connect(errors.append)
        worker.approval_requested.connect(lambda code: worker.deny())

        worker.run()

        assert repl.executed == []
        assert len(errors) == 1
        assert "not approved" in errors[0].lower()

    def test_stop_while_awaiting_approval_prevents_repl_execution(self):
        # stop() must unblock a worker parked on the approval wait, not hang it.
        repl = _FakeRepl()
        worker = _make_worker(repl)

        worker.approval_requested.connect(lambda code: worker.stop())

        worker.run()

        assert repl.executed == []


class TestApprovalGateApproved:
    def test_approve_allows_execution_and_finishes(self):
        repl = _FakeRepl()
        worker = _make_worker(repl)

        finished_results = []
        worker.finished.connect(finished_results.append)
        worker.approval_requested.connect(lambda code: worker.approve())

        worker.run()

        assert repl.executed == ["print('hello')"]
        assert len(finished_results) == 1
        assert finished_results[0]["code"] == "print('hello')"

    def test_approval_request_carries_the_exact_generated_code(self):
        worker = _make_worker()

        seen = []

        def _capture(code):
            seen.append(code)
            worker.approve()

        worker.approval_requested.connect(_capture)
        worker.run()

        assert seen == ["print('hello')"]

    def test_repair_iterations_run_under_the_same_single_approval(self):
        # First execution fails, repair produces new code, which then executes without
        # a second approval prompt - the documented (sandbox-matching) behavior.
        repl = _FakeRepl(output="Traceback (most recent call last): boom", last_run_failed=True)
        worker = _make_worker(repl)
        worker.repair_agent.get_response = lambda code, error, is_final: "print('repaired')"

        approval_requests = []

        def _approve_once(code):
            approval_requests.append(code)
            worker.approve()

        worker.approval_requested.connect(_approve_once)
        worker.run()

        assert len(approval_requests) == 1  # exactly one gate for the whole run
        assert len(repl.executed) > 1  # ...but repair attempts did execute after it


class TestNoCodeGeneratedSkipsTheGate:
    def test_a_text_only_response_never_requests_approval(self):
        # If the model answered in prose (no [TOOL:PYTHON] block), nothing will ever
        # execute, so no approval dialog should interrupt the user.
        repl = _FakeRepl()
        worker = _make_worker(repl, generated_response="Just a plain text answer.")

        requests = []
        worker.approval_requested.connect(requests.append)
        finished_results = []
        worker.finished.connect(finished_results.append)

        worker.run()

        assert requests == []
        assert repl.executed == []
        assert len(finished_results) == 1


class TestDisposeStopsWorkerSoDeletionDoesNotOrphanIt:
    """Adversarial-review finding: PyCoder workers are now discovered at app close only
    via scene.pycoder_nodes[*].worker_thread. Deleting a running node removes it from
    that list, so its worker must be stopped ON deletion or it becomes invisible to
    _iter_shutdown_threads and the app can tear down with a live QThread. PyCoderNode
    now has dispose() (which ChatScene.deleteSelectedItems calls) to close that gap -
    matching CodeSandboxNode/GitlinkNode."""

    def test_pycoder_node_exposes_dispose_so_the_delete_path_picks_it_up(self):
        from graphlink_pycoder import PyCoderNode

        # ChatScene.deleteSelectedItems is `if hasattr(item, "dispose"): item.dispose()`
        # - the gap was purely that PyCoderNode didn't define it.
        assert hasattr(PyCoderNode, "dispose")

    def test_dispose_stops_a_running_worker_and_clears_the_reference(self):
        from unittest.mock import MagicMock

        from graphlink_pycoder import PyCoderNode

        node = PyCoderNode(parent_node=None)
        worker = MagicMock()
        worker.isRunning.return_value = True
        node.worker_thread = worker

        node.dispose()

        worker.stop.assert_called_once()
        assert node.worker_thread is None
        assert node.is_disposed is True

    def test_dispose_is_idempotent(self):
        from unittest.mock import MagicMock

        from graphlink_pycoder import PyCoderNode

        node = PyCoderNode(parent_node=None)
        node.dispose()
        node.worker_thread = MagicMock()  # a second dispose must not touch this

        node.dispose()

        node.worker_thread.stop.assert_not_called()


class TestManualModeStaysUngated:
    def test_code_execution_worker_has_no_approval_gate(self):
        # MANUAL mode runs code the user typed themselves - clicking Run is the
        # approval. Pin that boundary down so a future refactor doesn't silently
        # extend the gate (or accidentally assume one exists here).
        assert not hasattr(CodeExecutionWorker, "approval_requested")

        repl = _FakeRepl()
        worker = CodeExecutionWorker("print('mine')", repl)
        outputs = []
        worker.finished.connect(outputs.append)

        worker.run()

        assert repl.executed == ["print('mine')"]
        assert outputs == ["ok"]
