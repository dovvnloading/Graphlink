"""Execution Sandbox's Qt-free domain pieces, split out of
graphlink_agents_code_sandbox.py (Qt-removal plan R5.4) so backend/agents.py
can import the virtualenv sandbox and its two LLM-calling agents without ever
pulling the Qt stack into the FastAPI process.

Moved here VERBATIM (same code, same behavior) from
graphlink_agents_code_sandbox.py: SandboxStage, _subprocess_kwargs,
_normalize_requirements, _extract_python_block, SandboxGenerationAgent,
SandboxRepairAgent, VirtualEnvSandbox - all of these were already pure/Qt-free
in the legacy file (confirmed by reading it directly before this split: zero
Qt references anywhere in this block). The venv-creation/pip-install/
script-execution timeout numbers inside VirtualEnvSandbox (180s / 600s / 240s)
are carried forward completely unchanged - see backend/agents.py's own
PYCODER_EXECUTE_TIMEOUT_SECONDS comment for why 240 is reused there rather
than reinvented.

The ONLY change from the legacy source is the config import:
`graphlink_config` (which transitively imports Qt's GUI/widget modules at
module scope) becomes `graphlink_task_config`, mirroring the exact same swap
graphlink_plugins/gitlink/agent.py and graphlink_plugins/pycoder/domain.py
already made for the same reason.

What did NOT move here (stays in graphlink_agents_code_sandbox.py, unchanged):
CodeSandboxExecutionWorker (the Qt worker-thread subclass) and its own
_is_error_output helper (a worker instance method, never called by anything
that moved here) - backend/agents.py carries its own equivalent copy of that
same keyword-based heuristic (see _is_sandbox_error_output there) rather than
reaching back into the legacy Qt-coupled file for it.

backend/agents.py's own new AgentDispatcher pipeline (R5.4) constructs a
fresh VirtualEnvSandbox per run, exactly like _call_gitlink_agent constructs a
fresh GitlinkAgent per call - the only state that must survive between runs is
the plain string code_sandbox_sandbox_id (real SceneNode state), not a live
VirtualEnvSandbox object.
"""

import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from enum import Enum
from pathlib import Path

import api_provider
import graphlink_task_config as config


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
        self.base_dir = Path(tempfile.gettempdir()) / "graphlink_execution_sandboxes" / safe_id
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
        output_queue = queue.Queue()
        done_signal = object()

        def _reader():
            if not process.stdout:
                output_queue.put(done_signal)
                return
            try:
                for line in iter(process.stdout.readline, ""):
                    output_queue.put(line)
            finally:
                output_queue.put(done_signal)

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()

        try:
            while True:
                if not should_continue():
                    self.stop()
                    raise InterruptedError("Sandbox execution was stopped.")

                if timeout_seconds and (time.monotonic() - start_time) > timeout_seconds:
                    self.stop()
                    raise RuntimeError(f"Sandbox process timed out after {timeout_seconds} seconds.")

                try:
                    line = output_queue.get(timeout=0.1)
                except queue.Empty:
                    if process.poll() is None:
                        continue
                    # The process has exited. The reader thread owns
                    # process.stdout exclusively (audit finding B3: a direct
                    # stdout.read() here used to race the reader's readline on
                    # the same pipe, garbling/duplicating captured output) -
                    # let it drain to EOF and post done_signal instead.
                    reader_thread.join(timeout=5)
                    if not reader_thread.is_alive() and output_queue.empty():
                        break
                    continue

                if line is done_signal:
                    break

                output_chunks.append(line)
                if emit_line:
                    emit_line(line)

            # done_signal received (or the reader finished with an empty
            # queue): stdout has been consumed to EOF by the reader, so no
            # direct read is needed - or safe - here. Reap the process so
            # returncode is real rather than a still-None poll() snapshot.
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Pathological: the child closed stdout but kept running.
                process.kill()
                process.wait()

            return "".join(output_chunks), process.returncode
        except Exception:
            if process.poll() is None:
                self.stop()
            raise
        finally:
            if reader_thread.is_alive():
                reader_thread.join(timeout=0.5)
            self.current_process = None

    def ensure_base_environment(self, should_continue, emit_line=None):
        self.base_dir.mkdir(parents=True, exist_ok=True)
        if self.python_executable.exists():
            return

        if emit_line:
            emit_line("[Sandbox] Creating virtual environment...\n")
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
            emit_line(f"[Sandbox] Running {self.script_path.name} in the virtualenv...\n")

        output, return_code = self._run_subprocess(
            [str(self.python_executable), str(self.script_path)],
            should_continue=should_continue,
            emit_line=emit_line,
            cwd=self.base_dir,
            timeout_seconds=240,
        )
        return output.strip(), return_code
