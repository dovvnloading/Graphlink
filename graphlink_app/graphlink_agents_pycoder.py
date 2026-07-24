"""Py-Coder's QThread worker classes - the Qt-coupled half of the legacy
Py-Coder plugin.

Qt-removal plan R5.4: the Qt-free domain pieces this module used to define
inline (PyCoderStage, PyCoderStatus, PythonREPL, PyCoderReplManager,
PyCoderExecutionAgent, PyCoderRepairAgent, PyCoderAnalysisAgent) moved
VERBATIM to graphlink_plugins/pycoder/domain.py, so backend/agents.py can
import them without ever pulling PySide6 into the FastAPI process (this
module's own `from PySide6.QtCore import QThread, Signal` is unconditional
and module-top-level, so importing anything from here at all used to pull
the whole Qt stack in regardless of whether the imported symbol itself
touched Qt).

Only the three QThread subclasses stay here - CodeExecutionWorker,
PyCoderExecutionWorker, PyCoderAgentWorker - now thin wrappers around the
imported domain classes. Their public class names, constructors, signals, and
behavior are unchanged; the legacy Qt app's own call sites
(graphlink_window_actions.py) keep working unmodified.
"""

from PySide6.QtCore import QThread, Signal

import re
import threading

from graphlink_plugins.pycoder.domain import (
    PyCoderAnalysisAgent,
    PyCoderExecutionAgent,
    PyCoderRepairAgent,
    PyCoderReplManager,
    PyCoderStage,
    PyCoderStatus,
    PythonREPL,
)

# Re-exported for backward compatibility with any existing call site that
# imports these names from this module (e.g.
# `from graphlink_agents_pycoder import PyCoderAnalysisAgent, PyCoderStatus`
# in the legacy graphlink_agents_code_sandbox.py, updated below to import
# from the new domain module directly, and any other lingering import).
__all__ = [
    "PyCoderStage",
    "PyCoderStatus",
    "PythonREPL",
    "PyCoderReplManager",
    "PyCoderExecutionAgent",
    "PyCoderRepairAgent",
    "PyCoderAnalysisAgent",
    "CodeExecutionWorker",
    "PyCoderExecutionWorker",
    "PyCoderAgentWorker",
]


class CodeExecutionWorker(QThread):
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, code, repl):
        super().__init__()
        self.code = code
        self.repl = repl
        self._is_running = True

    def stop(self):
        self._is_running = False
        if self.repl:
            self.repl.stop()

    def run(self):
        try:
            if not self._is_running: return
            output = self.repl.execute(self.code)
            if not self._is_running: return
            self.finished.emit(output if output else "[No output produced]")
        except Exception as e:
            if self._is_running:
                self.error.emit(f"Execution Error: {str(e)}")


class PyCoderExecutionWorker(QThread):
    log_update = Signal(object, object)
    finished = Signal(dict)
    error = Signal(str)
    # Emitted with the generated code once it is ready but before anything executes in
    # the REPL. The receiver (main thread) must call approve() or deny() to unblock
    # run(), which is parked on _approval_event.wait(). Same contract as
    # CodeSandboxExecutionWorker.approval_requested: this path also runs model-generated
    # code with full user privileges, so it needs the same gate.
    approval_requested = Signal(str)

    def __init__(self, user_prompt, conversation_history, repl):
        super().__init__()
        self.user_prompt = user_prompt
        self.conversation_history = conversation_history
        self.repl = repl
        self.execution_agent = PyCoderExecutionAgent()
        self.repair_agent = PyCoderRepairAgent()
        self.analysis_agent = PyCoderAnalysisAgent()
        self._is_running = True
        self._approval_event = threading.Event()
        self._approved = False

    def approve(self):
        self._approved = True
        self._approval_event.set()

    def deny(self):
        self._approved = False
        self._approval_event.set()

    def stop(self):
        self._is_running = False
        # Unblock a worker parked on the approval wait so stop() can't hang it.
        self._approval_event.set()
        if self.repl:
            self.repl.stop()

    def run(self):
        try:
            retry_count = 0
            max_retries = 4
            current_code = None
            last_error = None

            if not self._is_running: return
            self.log_update.emit(PyCoderStage.ANALYZE, PyCoderStatus.RUNNING)
            initial_response = self.execution_agent.get_response(self.conversation_history, self.user_prompt)
            self.log_update.emit(PyCoderStage.ANALYZE, PyCoderStatus.SUCCESS)

            if not self._is_running: return
            code_match = re.search(r'\[TOOL:PYTHON\](.*?)\[/TOOL\]', initial_response, re.DOTALL)
            if not code_match:
                self.log_update.emit(PyCoderStage.GENERATE, PyCoderStatus.SUCCESS)
                self.log_update.emit(PyCoderStage.EXECUTE, PyCoderStatus.SUCCESS)
                self.log_update.emit(PyCoderStage.ANALYZE_RESULT, PyCoderStatus.RUNNING)
                result = {
                    "code": "# No code was generated for this prompt.",
                    "output": "[Not applicable]",
                    "analysis": initial_response
                }
                if self._is_running:
                    self.finished.emit(result)
                    self.log_update.emit(PyCoderStage.ANALYZE_RESULT, PyCoderStatus.SUCCESS)
                return

            current_code = code_match.group(1).strip()
            self.log_update.emit(PyCoderStage.GENERATE, PyCoderStatus.SUCCESS)

            # Approval gate: AI-generated code runs with full user privileges (the
            # REPL subprocess is completely unsandboxed), so pause here until the main
            # thread approves - the same contract Code Sandbox has had all along.
            # Repair-loop iterations below run under this same single approval,
            # matching the sandbox's behavior for its own repair attempts.
            self.approval_requested.emit(current_code)
            self._approval_event.wait()

            if not self._is_running:
                return

            if not self._approved:
                self.error.emit("Py-Coder run cancelled: execution was not approved.")
                return

            while retry_count < max_retries:
                if not self._is_running: return
                self.log_update.emit(PyCoderStage.EXECUTE, PyCoderStatus.RUNNING)

                # Failure is reported structurally by the REPL wrapper
                # (repl.last_run_failed) instead of keyword-scanning stdout,
                # which used to mark correct programs that merely printed
                # words like "failed" as errors and "repair" working code
                # (audit finding B2). getattr keeps duck-typed test fakes
                # without the attribute working.
                execution_output = ""
                try:
                    execution_output = self.repl.execute(current_code)
                    execution_failed = getattr(self.repl, "last_run_failed", False)
                except Exception as e:
                    execution_output = f"\n--- EXECUTION FAILED ---\n{type(e).__name__}: {e}"
                    execution_failed = True

                if not self._is_running: return
                if not execution_failed:
                    self.log_update.emit(PyCoderStage.EXECUTE, PyCoderStatus.SUCCESS)
                    self.log_update.emit(PyCoderStage.ANALYZE_RESULT, PyCoderStatus.RUNNING)
                    final_analysis = self.analysis_agent.get_response(
                        self.user_prompt, current_code, execution_output
                    )
                    result = {
                        "code": current_code,
                        "output": execution_output if execution_output else "[No output produced]",
                        "analysis": final_analysis
                    }
                    if self._is_running:
                        self.finished.emit(result)
                        self.log_update.emit(PyCoderStage.ANALYZE_RESULT, PyCoderStatus.SUCCESS)
                    return

                last_error = execution_output
                self.log_update.emit(PyCoderStage.EXECUTE, PyCoderStatus.FAILURE)
                retry_count += 1

                if retry_count < max_retries:
                    is_final = (retry_count == max_retries - 1)
                    current_code = self.repair_agent.get_response(current_code, last_error, is_final)

            if not self._is_running: return
            self.log_update.emit(PyCoderStage.ANALYZE_RESULT, PyCoderStatus.RUNNING)
            final_failure_analysis = self.analysis_agent.get_response(
                self.user_prompt,
                current_code,
                f"The code failed to execute after {max_retries} attempts. The final error was:\n{last_error}"
            )
            result = {
                "code": current_code,
                "output": last_error,
                "analysis": f"**PROCESS FAILED**\n\nAfter {max_retries} attempts, the code could not be successfully executed.\n\n{final_failure_analysis}"
            }
            if self._is_running:
                self.finished.emit(result)
                self.log_update.emit(PyCoderStage.ANALYZE_RESULT, PyCoderStatus.FAILURE)

        except Exception as e:
            if self._is_running:
                self.error.emit(f"An unexpected error occurred in the PyCoder workflow: {str(e)}")


class PyCoderAgentWorker(QThread):
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, code, code_output):
        super().__init__()
        self.code = code
        self.code_output = code_output
        self.analysis_agent = PyCoderAnalysisAgent()

    def run(self):
        try:
            ai_analysis = self.analysis_agent.get_response(
                original_prompt=None,
                code=self.code,
                code_output=self.code_output
            )
            self.finished.emit(ai_analysis)
        except Exception as e:
            self.error.emit(f"Failed to get AI analysis: {str(e)}")
