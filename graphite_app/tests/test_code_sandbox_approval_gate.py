"""Tests for the Code Sandbox approval gate.

Code Sandbox executes AI/user-generated Python with full host-user privileges and
installs whatever packages are declared in requirements.txt (see
doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md section 2.1). These tests cover the approval gate
added to CodeSandboxExecutionWorker.run(): the worker must pause after code is ready
and before it touches the venv/pip/subprocess, and must only proceed if the main
thread calls approve() - a call to deny() (or stop()) must short-circuit the run
without ever installing dependencies or executing code.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

from graphite_agents_code_sandbox import CodeSandboxExecutionWorker

# A full QApplication (not QCoreApplication) is required process-wide: other test
# modules in this suite construct real QWidget-based plugin nodes, and Qt can only
# have one application instance per process (see tests/conftest.py).
_APP = QApplication.instance() or QApplication([])


def _make_worker(existing_code="print('hello')", requirements_manifest=""):
    worker = CodeSandboxExecutionWorker(
        sandbox_id="test-sandbox",
        user_prompt="",
        conversation_history=[],
        requirements_manifest=requirements_manifest,
        existing_code=existing_code,
    )
    # Manual mode (existing_code set) skips the LLM generation call entirely, so no
    # network/API stubbing is needed to reach the approval gate.
    worker.analysis_agent.get_response = lambda **kwargs: "stub analysis"
    return worker


def _stub_sandbox(worker, *, execute_return_code=0):
    calls = []
    worker.sandbox.ensure_base_environment = lambda *a, **k: calls.append("prepare")
    worker.sandbox.sync_requirements = lambda *a, **k: calls.append("install")

    def _execute_code(code, should_continue, emit_line=None):
        calls.append("execute")
        return "ok\n", execute_return_code

    worker.sandbox.execute_code = _execute_code
    return calls


class TestApprovalGateDenied:
    def test_deny_before_run_prevents_any_sandbox_activity(self):
        worker = _make_worker()
        calls = _stub_sandbox(worker)

        errors = []
        worker.error.connect(errors.append)
        worker.approval_requested.connect(lambda code, reqs: worker.deny())

        worker.run()

        assert calls == []
        assert len(errors) == 1
        assert "not approved" in errors[0].lower()

    def test_stop_while_awaiting_approval_prevents_sandbox_activity(self):
        # stop() must unblock a worker that is parked on the approval wait, not hang it.
        worker = _make_worker()
        calls = _stub_sandbox(worker)

        worker.approval_requested.connect(lambda code, reqs: worker.stop())

        worker.run()

        assert calls == []


class TestApprovalGateApproved:
    def test_approve_allows_prepare_install_execute_in_order(self):
        worker = _make_worker(requirements_manifest="requests==2.31.0")
        calls = _stub_sandbox(worker)

        finished_results = []
        worker.finished.connect(finished_results.append)
        worker.approval_requested.connect(lambda code, reqs: worker.approve())

        worker.run()

        assert calls == ["prepare", "install", "execute"]
        assert len(finished_results) == 1

    def test_approval_request_carries_the_exact_code_and_requirements(self):
        worker = _make_worker(existing_code="print(1)", requirements_manifest="numpy")
        _stub_sandbox(worker)

        seen = []

        def _capture(code, reqs):
            seen.append((code, reqs))
            worker.approve()

        worker.approval_requested.connect(_capture)
        worker.run()

        assert seen == [("print(1)", "numpy")]
