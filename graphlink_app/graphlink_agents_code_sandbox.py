"""Execution Sandbox's QThread worker class - the Qt-coupled half of the
legacy Code Sandbox plugin.

Qt-removal plan R5.4: the Qt-free domain pieces this module used to define
inline (SandboxStage, _subprocess_kwargs, _normalize_requirements,
_extract_python_block, SandboxGenerationAgent, SandboxRepairAgent,
VirtualEnvSandbox) moved VERBATIM to graphlink_plugins/code_sandbox/domain.py,
so backend/agents.py can import them without ever pulling PySide6 into the
FastAPI process (this module's own `from PySide6.QtCore import QThread,
Signal` is unconditional and module-top-level, so importing anything from
here at all used to pull the whole Qt stack in regardless of whether the
imported symbol itself touched Qt).

Only CodeSandboxExecutionWorker (the QThread subclass) stays here, now a thin
wrapper around the imported domain classes, plus its own _is_error_output
helper (never moved - it is a worker-instance method, not a free function any
domain piece calls). Its public class name, constructor, signals, and
behavior are unchanged; the legacy Qt app's own call sites
(graphlink_window_actions.py) keep working unmodified.

The cross-module dependency on Py-Coder's own analysis agent moves with this
extraction: `from graphlink_agents_pycoder import PyCoderAnalysisAgent,
PyCoderStatus` becomes `from graphlink_plugins.pycoder.domain import
PyCoderAnalysisAgent, PyCoderStatus` - the real Qt-free home of those two
names as of R5.4.
"""

from PySide6.QtCore import QThread, Signal

import threading

from graphlink_plugins.code_sandbox.domain import (
    SandboxGenerationAgent,
    SandboxRepairAgent,
    SandboxStage,
    VirtualEnvSandbox,
    _extract_python_block,
    _normalize_requirements,
    _subprocess_kwargs,
)
from graphlink_plugins.pycoder.domain import PyCoderAnalysisAgent, PyCoderStatus

# Re-exported for backward compatibility with any existing call site that
# imports these names from this module.
__all__ = [
    "SandboxStage",
    "SandboxGenerationAgent",
    "SandboxRepairAgent",
    "VirtualEnvSandbox",
    "CodeSandboxExecutionWorker",
]


class CodeSandboxExecutionWorker(QThread):
    log_update = Signal(object, object)
    terminal_chunk = Signal(str)
    finished = Signal(dict)
    error = Signal(str)
    # Emitted with (code, requirements_manifest) once code is ready but before the
    # sandbox installs anything or executes it. The receiver (main thread) must call
    # approve() or deny() to unblock run(), which is parked on _approval_event.wait().
    approval_requested = Signal(str, str)

    def __init__(self, sandbox_id, user_prompt, conversation_history, requirements_manifest, existing_code=""):
        super().__init__()
        self.sandbox = VirtualEnvSandbox(sandbox_id)
        self.user_prompt = user_prompt or ""
        self.conversation_history = conversation_history or []
        self.requirements_manifest = requirements_manifest or ""
        self.existing_code = existing_code or ""
        self.generation_agent = SandboxGenerationAgent()
        self.repair_agent = SandboxRepairAgent()
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
        self._approval_event.set()
        self.sandbox.stop()

    def _emit_terminal(self, text):
        if text:
            self.terminal_chunk.emit(text)

    def _should_continue(self):
        return self._is_running

    def _is_error_output(self, output_text, return_code):
        if return_code != 0:
            return True
        lowered = (output_text or "").lower()
        error_keywords = [
            "traceback (most recent call last)",
            "modulenotfounderror",
            "importerror",
            "nameerror:",
            "syntaxerror:",
            "typeerror:",
            "valueerror:",
            "exception:",
        ]
        return any(keyword in lowered for keyword in error_keywords)

    def run(self):
        try:
            prompt = self.user_prompt.strip()
            requirements_manifest = _normalize_requirements(self.requirements_manifest)
            current_code = self.existing_code.strip()

            if not current_code and not prompt:
                self.error.emit("Provide a task prompt or Python code before running the sandbox.")
                return

            if current_code:
                self.log_update.emit(SandboxStage.GENERATE, PyCoderStatus.SUCCESS)
            else:
                self.log_update.emit(SandboxStage.GENERATE, PyCoderStatus.RUNNING)
                initial_response = self.generation_agent.get_response(
                    self.conversation_history,
                    prompt,
                    requirements_manifest,
                )
                self.log_update.emit(SandboxStage.GENERATE, PyCoderStatus.SUCCESS)

                current_code = _extract_python_block(initial_response)
                if not current_code:
                    result = {
                        "code": "# No Python code was generated for this request.",
                        "output": "[Sandbox was not executed]",
                        "analysis": initial_response,
                        "requirements": requirements_manifest,
                    }
                    if self._is_running:
                        self.finished.emit(result)
                    return

            if not self._is_running:
                return

            self._emit_terminal("[Sandbox] Waiting for approval to install dependencies and execute this code...\n")
            self.approval_requested.emit(current_code, requirements_manifest)
            self._approval_event.wait()

            if not self._is_running:
                return

            if not self._approved:
                self.error.emit("Sandbox run cancelled: execution was not approved.")
                return

            self.log_update.emit(SandboxStage.PREPARE, PyCoderStatus.RUNNING)
            self.sandbox.ensure_base_environment(self._should_continue, emit_line=self._emit_terminal)
            self.log_update.emit(SandboxStage.PREPARE, PyCoderStatus.SUCCESS)

            if not self._is_running:
                return

            self.log_update.emit(SandboxStage.INSTALL, PyCoderStatus.RUNNING)
            self.sandbox.sync_requirements(requirements_manifest, self._should_continue, emit_line=self._emit_terminal)
            self.log_update.emit(SandboxStage.INSTALL, PyCoderStatus.SUCCESS)

            if not self._is_running:
                return

            max_attempts = 3
            final_output = ""
            last_error = ""
            final_return_code = 0

            for attempt_index in range(max_attempts):
                if not self._is_running:
                    return

                self.log_update.emit(SandboxStage.EXECUTE, PyCoderStatus.RUNNING)
                self._emit_terminal(f"[Sandbox] Execution attempt {attempt_index + 1} of {max_attempts}.\n")
                final_output, final_return_code = self.sandbox.execute_code(
                    current_code,
                    self._should_continue,
                    emit_line=self._emit_terminal,
                )

                if not self._is_error_output(final_output, final_return_code):
                    self.log_update.emit(SandboxStage.EXECUTE, PyCoderStatus.SUCCESS)
                    break

                last_error = final_output or "The sandbox process exited with an error."
                self.log_update.emit(SandboxStage.EXECUTE, PyCoderStatus.FAILURE)

                if attempt_index == max_attempts - 1:
                    break

                self._emit_terminal("[Sandbox] Repairing the script for another attempt...\n")
                current_code = self.repair_agent.get_response(
                    current_code,
                    last_error,
                    requirements_manifest,
                    original_prompt=prompt,
                )
            else:
                final_output = final_output or last_error

            if not self._is_running:
                return

            self.log_update.emit(SandboxStage.ANALYZE, PyCoderStatus.RUNNING)
            analysis_text = self.analysis_agent.get_response(
                original_prompt=prompt or None,
                code=current_code,
                code_output=final_output if final_output else "[No output produced]",
            )

            result = {
                "code": current_code,
                "output": final_output if final_output else "[No output produced]",
                "analysis": analysis_text,
                "requirements": requirements_manifest,
            }

            if self._is_running:
                self.finished.emit(result)
                if self._is_error_output(final_output, final_return_code):
                    self.log_update.emit(SandboxStage.ANALYZE, PyCoderStatus.FAILURE)
                else:
                    self.log_update.emit(SandboxStage.ANALYZE, PyCoderStatus.SUCCESS)

        except InterruptedError:
            return
        except Exception as exc:
            if self._is_running:
                self.error.emit(f"Sandbox execution failed: {exc}")
