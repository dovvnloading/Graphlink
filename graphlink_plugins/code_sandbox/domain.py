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
are carried forward completely unchanged.

The ONLY change from the legacy source is the config import:
`graphlink_config` (which transitively imports Qt's GUI/widget modules at
module scope) becomes `graphlink_task_config`, mirroring the exact same swap
graphlink_plugins/gitlink/agent.py and graphlink_plugins/common/python_repl.py
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
import threading
import time
from enum import Enum
from pathlib import Path

import api_provider
import graphlink_task_config as config
from graphlink_execution_guard import create_execution_guard
from graphlink_process_env import safe_subprocess_env
from graphlink_scratch_dirs import (
    EXECUTION_SANDBOX_ROOT,
    prepare_scratch_dir,
    safe_scratch_id,
    touch_scratch_dir_usage,
)


class SandboxStage(Enum):
    GENERATE = 1
    PREPARE = 2
    INSTALL = 3
    EXECUTE = 4
    ANALYZE = 5


def _subprocess_kwargs():
    # ADR-002 P0: env= is explicit-allowlist, not inherited - see
    # graphlink_process_env's own module doc for why. Every venv-create/
    # pip-install/script-execute call in this file goes through
    # _run_subprocess below, which passes these kwargs, so this is the one
    # place that needs to set it.
    kwargs = {"env": safe_subprocess_env()}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs


def _normalize_requirements(requirements_text):
    normalized = requirements_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    return "\n".join(lines).strip()


# ADR-005 stage 5.5 round-3 review-fix: requirements-file option lines that
# are NOT package specifiers - they legitimately carry their own paths/URLs
# (--find-links, --index-url, ...) and must never be run through the
# direct-reference check below, or every real end-to-end test in
# test_code_sandbox_only_binary.py that uses `--find-links <path>` would
# start failing alongside the actually-dangerous lines.
_PIP_OPTION_LINE_PREFIXES = (
    "-i", "--index-url",
    "--extra-index-url",
    "--no-index",
    "-c", "--constraint",
    "-r", "--requirement",
    "-f", "--find-links",
    "--no-binary",
    "--only-binary",
    "--prefer-binary",
    "--require-hashes",
    "--pre",
    "--trusted-host",
    "--use-feature",
    "--global-option",
    "--install-option",
    "--config-settings",
    "--hash",
    "--no-deps",
    "--no-build-isolation",
)

# SECURITY-FIX: option lines that DEFEAT or ROUTE AROUND the --only-binary
# :all: guard, so they are unsafe whenever source builds are off even though
# they are not package specifiers. Checked before the generic option skip
# above, since every one of these is also in _PIP_OPTION_LINE_PREFIXES:
#
# - `--no-binary` in a requirements file is applied by pip AFTER the CLI's
#   --only-binary :all: (both feed one FormatControl; the later write wins,
#   and `:all:` on either side clears the other), so a single
#   `--no-binary :all:` line in the manifest silently re-enables sdist
#   builds for every package while the approval panel still shows source
#   builds as off.
# - `-r`/`--requirement` and `-c`/`--constraint` make pip read ANOTHER file
#   (a local path, or a URL pip fetches itself) whose lines never pass
#   through _direct_reference_requirement_lines at all - a one-line
#   `-r https://attacker/reqs.txt` smuggles in any `pkg @ file://` or
#   `-e` line this whole check exists to stop.
# - `--global-option`/`--install-option`/`--config-settings`/
#   `--no-build-isolation` only do anything during a source build, which
#   is exactly what is supposed to be impossible here; there is no
#   legitimate reason for a manifest to carry them while builds are off,
#   and refusing them closes the "build happens anyway via one of the
#   holes above, and now also runs with attacker-chosen build flags"
#   combination.
#
# Everything else on the skip list (index/find-links/trusted-host/hash/
# no-deps/pre/prefer-binary/...) selects WHERE a wheel comes from, not
# whether code runs before the user's approved script does, and stays
# allowed - the existing end-to-end tests' `--find-links <dir>` /
# `--no-index` manifests depend on that.
_GUARD_DEFEATING_OPTION_PREFIXES = (
    "--no-binary",
    "-r", "--requirement",
    "-c", "--constraint",
    "--global-option",
    "--install-option",
    "--config-settings",
    "--no-build-isolation",
)


def _option_name(line):
    """The option token itself, split from its value whether written
    `--opt value`, `--opt=value`, or `-r value`, so `--no-binary` cannot be
    confused with a hypothetical `--no-binary-something`."""
    head = re.split(r"[\s=]", line, maxsplit=1)[0]
    return head


def _direct_reference_requirement_lines(normalized_requirements):
    """Returns the requirements-file lines that name a package by direct
    URL/VCS reference (`pkg @ <url>`, a bare `git+.../https://...` line),
    editable install (`-e`/`--editable`), or bare local path, rather than an
    ordinary index-resolved name.

    THIS is the category `--only-binary :all:` (sync_requirements, below)
    does NOT cover: none of these forms are ever resolved against an index,
    so pip's index-only "no wheel published -> refuse" restriction never
    even applies to them - pip downloads/reads the reference directly and
    still runs ITS OWN PEP 517 build backend to produce a wheel. Verified
    empirically against real pip (see test_code_sandbox_only_binary.py's
    TestDirectReferenceRequirementsBypassOnlyBinary, mirroring this file's
    own hostile-sdist proof for the plain-name case) that a `pkg @
    file:///...` line reaches and executes a hostile build_wheel() hook
    with --only-binary :all: on the command line, exactly as if the flag
    were absent.

    Deliberately a denylist of dangerous SHAPES, not an allowlist of a full
    PEP 508 grammar - ordinary requirement lines (name, extras, version
    specifiers, environment markers) never contain '@', '://', '/', or
    '\\\\', so this stays conservative without needing to parse PEP 508
    itself. A trailing '\\' line-continuation marker is stripped before the
    path-character check so a routine multi-line hashed requirement
    (`pkg==1.0 \\` / `    --hash=...`) is not mistaken for a Windows path.
    """
    unsafe_lines = []
    for raw_line in normalized_requirements.split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
        if not line:
            continue
        if line.endswith("\\"):
            line = line[:-1].rstrip()
        if not line:
            continue
        if line.startswith(("-e", "--editable")):
            unsafe_lines.append(raw_line.strip())
            continue
        if line.startswith("-") and _option_name(line) in _GUARD_DEFEATING_OPTION_PREFIXES:
            # See _GUARD_DEFEATING_OPTION_PREFIXES - must run before the
            # generic option skip just below, which would wave it through.
            unsafe_lines.append(raw_line.strip())
            continue
        if line.startswith(_PIP_OPTION_LINE_PREFIXES):
            continue
        if line.startswith("-"):
            # An option this function does not special-case either way -
            # not a package specifier, so it is not this check's concern.
            continue
        if "@" in line or "://" in line or "/" in line or "\\" in line:
            unsafe_lines.append(raw_line.strip())
    return unsafe_lines


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
        self.base_dir = EXECUTION_SANDBOX_ROOT / safe_scratch_id(sandbox_id)
        self.venv_dir = self.base_dir / "venv"
        self.requirements_file = self.base_dir / "requirements.txt"
        self.requirements_hash_file = self.base_dir / ".requirements.sha256"
        self.script_path = self.base_dir / "sandbox_entry.py"
        self.current_process = None
        # ADR-005 stage 5.2: the resource guard for whichever subprocess
        # _run_subprocess currently owns - see stop()'s own comment for why
        # closing this, not just terminating/killing current_process, is
        # what makes "Stop" actually kill the whole tree.
        self.guard = None

    @property
    def python_executable(self):
        if os.name == "nt":
            return self.venv_dir / "Scripts" / "python.exe"
        return self.venv_dir / "bin" / "python"

    def stop(self):
        # ADR-005 stage 5.2: close the resource guard FIRST - on Windows
        # this terminates the whole job (the tracked process AND anything
        # it has itself spawned), closing the pre-existing gap where
        # terminate()/kill() alone only ever stopped the one directly-
        # tracked process, never its own children. Still followed by the
        # existing terminate/kill logic unconditionally: a safe no-op if
        # the guard already killed it, and the only thing that actually
        # stops the direct child on non-Windows in this stage (the POSIX
        # process-group tier is ADR-005 stage 5.3).
        process = self.current_process
        if self.guard:
            self.guard.close()
            self.guard = None
        if process is not None:
            try:
                if process.poll() is None:
                    try:
                        process.terminate()
                        process.wait(timeout=3)
                    except Exception:
                        try:
                            process.kill()
                        except Exception:
                            pass
            finally:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
        self.current_process = None

    def _run_subprocess(self, args, should_continue, emit_line=None, cwd=None, timeout_seconds=None):
        output_chunks = []
        start_time = time.monotonic()
        # ADR-005 stage 5.2/5.3: the guard is created BEFORE Popen so its
        # popen_kwargs() can reach the spawn itself. Empty on Windows (the
        # job object is applied to an already-running process by assign()
        # below); on POSIX it carries the process-group request and the
        # rlimit preexec hook, which must be in place between fork and
        # exec - see graphlink_execution_guard's own docstring.
        self.guard = create_execution_guard()
        process = subprocess.Popen(
            args,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            **self.guard.popen_kwargs(),
            **_subprocess_kwargs(),
        )
        self.current_process = process
        # Windows applies its cap here, after the child exists (an accepted
        # tradeoff over CREATE_SUSPENDED - the child has not run its own
        # code yet). On POSIX this records the process-group id close()
        # will kill.
        self.guard.assign(process.pid)
        # ADR-005 stage 5.3 (review-fix): mark base_dir as actively used -
        # every real call site (venv creation, pip install, script
        # execution) routes through here, so this is the one choke point
        # that needs it. See touch_scratch_dir_usage's own docstring for
        # why an in-place file rewrite (sync_requirements/execute_code's
        # normal, repeated-use pattern) never bumps this on its own.
        if cwd:
            touch_scratch_dir_usage(Path(cwd))
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
            # Normal-completion path: stop() (called on the
            # should_continue()==False/timeout/exception paths above) has
            # already closed the guard by this point and cleared it, so
            # this is a no-op there - it only actually fires here when the
            # subprocess exited cleanly on its own.
            if self.guard:
                self.guard.close()
                self.guard = None
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            self.current_process = None

    def ensure_base_environment(self, should_continue, emit_line=None):
        # ADR-005 stage 5.3: chmod 0700 on POSIX - see
        # graphlink_scratch_dirs.prepare_scratch_dir's own docstring.
        prepare_scratch_dir(self.base_dir)
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

    def sync_requirements(self, requirements_manifest, should_continue, emit_line=None, allow_source_builds=False):
        normalized = _normalize_requirements(requirements_manifest)
        # ADR-005 stage 5.5 round-3 review-fix: checked unconditionally,
        # ahead of even the cache short-circuit below - --only-binary :all:
        # (further down this method) has NO effect on a direct-URL/editable/
        # local-path requirement line (see _direct_reference_requirement_
        # lines's own docstring for why), so those lines must never reach
        # pip at all while the human has left source builds off, regardless
        # of which branch of this method would otherwise run. Raising here,
        # before the manifest hash is even computed against the cache, means
        # a dangerous manifest can never become "the cached one" either.
        if not allow_source_builds:
            unsafe_lines = _direct_reference_requirement_lines(normalized)
            if unsafe_lines:
                offending = "\n".join(f"  {line}" for line in unsafe_lines)
                raise RuntimeError(
                    "Dependency installation blocked: the requirements list "
                    "contains a direct URL, editable (-e), or local-path "
                    "reference, or a pip option (--no-binary, -r/-c, "
                    "--global-option, --config-settings, ...) that would "
                    "let a package's own build code run regardless of "
                    "--only-binary :all:. This cannot be installed without "
                    "explicitly allowing source builds. Enable \"Allow "
                    "source builds\" to install it, or remove the line(s) "
                    "below:\n" + offending
                )
        manifest_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        previous_hash = self.requirements_hash_file.read_text(encoding="utf-8").strip() if self.requirements_hash_file.exists() else ""

        self.requirements_file.write_text(normalized + ("\n" if normalized else ""), encoding="utf-8")

        if manifest_hash == previous_hash:
            # ADR-005 stage 5.5 review-fix: this pre-existing cache
            # short-circuit is keyed ONLY on the manifest text - it runs
            # BEFORE allow_source_builds is ever consulted below, and pip is
            # not invoked at all on a hit. This means the escalation
            # checkbox's real guarantee is "governs the first install of
            # this exact manifest text", not literally "governs every run" -
            # a later run with an unchecked box can silently reuse an
            # environment an earlier, explicitly-approved source build
            # produced. This is NOT a new install-time risk on the cache-hit
            # path itself (no pip invocation means no build backend can
            # execute here either way) - it is a disclosure-accuracy point
            # an adversarial review flagged: the checkbox does not mean
            # "source builds are re-verified on every run of this node."
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
        pip_args = [
            str(self.python_executable),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
        ]
        if not allow_source_builds:
            # ADR-005 stage 5.5: without this, pip resolving a requirement to
            # a source distribution (no wheel published for this
            # platform/version, or a private/unindexed package) invokes that
            # sdist's own PEP 517 build backend - arbitrary Python, not a
            # data format - to produce a wheel, *during* what looks like an
            # ordinary dependency install. Verified empirically with a real
            # hostile sdist (a custom build backend whose build_wheel() hook
            # has an observable side effect) before this flag was added: a
            # plain `pip install -r requirements.txt` against it executes the
            # backend and its side effect fires; with --only-binary :all:,
            # pip refuses to consider the sdist at all and fails with "No
            # matching distribution found" - the backend is never invoked.
            # Also verified this does not regress the ordinary case: a
            # package that DOES publish a wheel installs exactly as before.
            #
            # allow_source_builds=True is ADR-005 stage 5.5's "source-build
            # escalation" - an explicit, UI-approved opt-in
            # (CodeExecutionApprovalPanel.tsx's checkbox, threaded through
            # AgentDispatcher.start_code_sandbox_run) for a genuinely
            # source-only package, in effect for the FIRST install of a
            # given manifest text (see the cache short-circuit above this
            # method's own comment for why a later run with an identical,
            # already-cached manifest never reaches this branch at all,
            # regardless of this run's own checkbox value). Nothing here
            # re-verifies that the caller actually got a real approval for
            # this - that gate lives one layer up, the same trust boundary
            # every other call site of this
            # method already relies on for `requirements_manifest` itself.
            pip_args += ["--only-binary", ":all:"]
        # --no-input stays unconditional either way - defense in depth
        # matching ADR-005's own decision text, this subprocess has no
        # attached TTY to prompt on regardless of the source-build setting.
        pip_args += ["--no-input", "-r", str(self.requirements_file)]
        output, return_code = self._run_subprocess(
            pip_args,
            should_continue=should_continue,
            emit_line=emit_line,
            cwd=self.base_dir,
            timeout_seconds=600,
        )
        if return_code != 0:
            raise RuntimeError(f"Dependency installation failed.\n{output.strip()}")

        self.requirements_hash_file.write_text(manifest_hash, encoding="utf-8")

    def execute_code(self, code, should_continue, emit_line=None):
        prepare_scratch_dir(self.base_dir)
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
