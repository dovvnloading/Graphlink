import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QThread, Signal

import api_provider
import graphite_config as config
from graphite_agents_pycoder import PyCoderAnalysisAgent, PyCoderStatus


class SandboxStage(Enum):
    GENERATE = 1
    PREPARE = 2
    INSTALL = 3
    EXECUTE = 4
    ANALYZE = 5


def _subprocess_kwargs():
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs


def _normalize_requirements(requirements_text):
    normalized = requirements_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    return "\n".join(lines).strip()


def _extract_python_block(response_text):
    tool_match = re.search(r"\[TOOL:PYTHON\](.*?)\[/TOOL\]", response_text, re.DOTALL)
    if tool_match:
        return tool_match.group(1).strip()

    fenced_match = re.search(r"```python\s*(.*?)```", response_text, re.DOTALL | re.IGNORECASE)
    if fenced_match:
        return fenced_match.group(1).strip()

    return None


class SandboxGenerationAgent:
    def __init__(self):
        self.system_prompt = """
You are Graphlink's Execution Sandbox coding agent.
You will receive prior branch history, the user's prompt, and a requirements manifest.

Rules:
1. If code execution is needed, return ONLY Python code wrapped in [TOOL:PYTHON] and [/TOOL].
2. The code may use Python standard library plus the libraries explicitly listed in Available Dependencies.
3. Do not import or reference packages that are not in Available Dependencies.
4. The code must be runnable as a standalone script and should print meaningful output.
5. If code execution is not actually needed, provide a concise direct answer with no tool tags.
6. Never output markdown fences when you use the tool tags.
"""

    def get_response(self, conversation_history, user_prompt, requirements_manifest):
        history_str = json.dumps(conversation_history, indent=2)
        user_message = f"""
Conversation History:
{history_str}

Available Dependencies:
{requirements_manifest if requirements_manifest else "[none specified]"}

Final User Prompt:
{user_prompt}
"""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message},
        ]
        response = api_provider.chat(task=config.TASK_CHAT, messages=messages)
        return response["message"]["content"]


class SandboxRepairAgent:
    def __init__(self):
        self.system_prompt = """
You are Graphlink's sandbox repair agent.
You will be given Python code, the runtime error, and the sandbox requirements manifest.

Rules:
1. Return ONLY the complete corrected Python code.
2. The code may use only Python standard library plus dependencies explicitly listed in the requirements manifest.
3. Do not include explanations, markdown fences, or extra commentary.
4. Prefer small repairs before rewriting the whole solution.
"""

    def get_response(self, code, error_output, requirements_manifest, original_prompt=None):
        user_message = f"""
Original Prompt:
{original_prompt or "[manual execution]"}

Available Dependencies:
{requirements_manifest if requirements_manifest else "[none specified]"}

Broken Code:
{code}

Error Output:
{error_output}
"""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message},
        ]
        response = api_provider.chat(task=config.TASK_CHAT, messages=messages)
        repaired = response["message"]["content"].strip()
        fenced_match = re.search(r"```python\s*(.*?)```", repaired, re.DOTALL | re.IGNORECASE)
        if fenced_match:
            return fenced_match.group(1).strip()
        return repaired


class VirtualEnvSandbox:
    def __init__(self, sandbox_id):
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", sandbox_id or "default")
        self.base_dir = Path(tempfile.gettempdir()) / "graphite_execution_sandboxes" / safe_id
        self.venv_dir = self.base_dir / "venv"
        self.requirements_file = self.base_dir / "requirements.txt"
        self.requirements_hash_file = self.base_dir / ".requirements.sha256"
        self.script_path = self.base_dir / "sandbox_entry.py"
        self.current_process = None

    @property
    def python_executable(self):
        if os.name == "nt":
            return self.venv_dir / "Scripts" / "python.exe"
        return self.venv_dir / "bin" / "python"

    def stop(self):
        if self.current_process and self.current_process.poll() is None:
            try:
                self.current_process.terminate()
                self.current_process.wait(timeout=3)
            except Exception:
                try:
                    self.current_process.kill()
                except Exception:
                    pass
        self.current_process = None

    def _run_subprocess(self, args, should_continue, emit_line=None, cwd=None, timeout_seconds=None):
        output_chunks = []
        start_time = time.monotonic()
        process = subprocess.Popen(
            args,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            **_subprocess_kwargs(),
        )
        self.current_process = process
        try:
            while True:
                if not should_continue():
                    self.stop()
                    raise InterruptedError("Sandbox execution was stopped.")

                if timeout_seconds and (time.monotonic() - start_time) > timeout_seconds:
                    self.stop()
                    raise RuntimeError(f"Sandbox process timed out after {timeout_seconds} seconds.")

                line = process.stdout.readline() if process.stdout else ""
                if line:
                    output_chunks.append(line)
                    if emit_line:
                        emit_line(line)
                    continue

                if process.poll() is not None:
                    break

                time.sleep(0.02)

            remainder = process.stdout.read() if process.stdout else ""
            if remainder:
                output_chunks.append(remainder)
                if emit_line:
                    emit_line(remainder)

            return "".join(output_chunks), process.returncode
        finally:
            self.current_process = None

    def ensure_base_environment(self, should_continue, emit_line=None):
        self.base_dir.mkdir(parents=True, exist_ok=True)
        if self.python_executable.exists():
            return

        if emit_line:
            emit_line("[Sandbox] Creating isolated virtual environment...\n")
        output, return_code = self._run_subprocess(
            [sys.executable, "-m", "venv", str(self.venv_dir)],
            should_continue=should_continue,
            emit_line=emit_line,
            cwd=self.base_dir,
            timeout_seconds=180,
        )
        if return_code != 0:
            raise RuntimeError(f"Failed to create sandbox environment.\n{output.strip()}")

    def sync_requirements(self, requirements_manifest, should_continue, emit_line=None):
        normalized = _normalize_requirements(requirements_manifest)
        manifest_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        previous_hash = self.requirements_hash_file.read_text(encoding="utf-8").strip() if self.requirements_hash_file.exists() else ""

        self.requirements_file.write_text(normalized + ("\n" if normalized else ""), encoding="utf-8")

        if manifest_hash == previous_hash:
            if emit_line:
                emit_line("[Sandbox] Requirements unchanged. Reusing cached environment.\n")
            return

        if not normalized:
            if emit_line:
                emit_line("[Sandbox] No extra dependencies requested.\n")
            self.requirements_hash_file.write_text(manifest_hash, encoding="utf-8")
            return

        if emit_line:
            emit_line("[Sandbox] Installing sandbox dependencies...\n")
        output, return_code = self._run_subprocess(
            [
                str(self.python_executable),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "-r",
                str(self.requirements_file),
            ],
            should_continue=should_continue,
            emit_line=emit_line,
            cwd=self.base_dir,
            timeout_seconds=600,
        )
        if return_code != 0:
            raise RuntimeError(f"Dependency installation failed.\n{output.strip()}")

        self.requirements_hash_file.write_text(manifest_hash, encoding="utf-8")

    def execute_code(self, code, should_continue, emit_line=None):
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.script_path.write_text(code, encoding="utf-8")
        if emit_line:
            emit_line(f"[Sandbox] Running {self.script_path.name} inside the isolated environment...\n")

        output, return_code = self._run_subprocess(
            [str(self.python_executable), str(self.script_path)],
            should_continue=should_continue,
            emit_line=emit_line,
            cwd=self.base_dir,
            timeout_seconds=240,
        )
        return output.strip(), return_code


class CodeSandboxExecutionWorker(QThread):
    log_update = Signal(object, object)
    terminal_chunk = Signal(str)
    finished = Signal(dict)
    error = Signal(str)

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

    def stop(self):
        self._is_running = False
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
