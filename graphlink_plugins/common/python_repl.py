"""Shared code-execution substrate, split out of the retired Py-Coder
plugin (PLAN-2026-08-24 H5) so its still-live consumers keep working
without pulling in anything else Py-Coder-specific.

`PythonREPL` is a persistent, guarded Python subprocess - Execution
Sandbox's own test suite (backend/tests/test_execution_guard.py) uses it
as a real vehicle for exercising graphlink_execution_guard.py's job-
object/process-group mechanics, and it remains available for any future
caller that wants a stateful REPL over a one-shot subprocess. `CodeAnalysisAgent`
(renamed from Py-Coder's own PyCoderAnalysisAgent - same behavior, same
prompt) is Execution Sandbox's shared final-analysis step
(backend/agents.py's start_code_sandbox_run) - it was never pycoder-
specific in practice, just pycoder-named.

Both moved here VERBATIM (same code, same behavior) from
graphlink_plugins/pycoder/domain.py; nothing about their own logic
changed, only their home and (for the analysis agent) their name.
"""

import base64
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import api_provider
import graphlink_task_config as config
from graphlink_execution_guard import create_execution_guard
from graphlink_process_env import safe_subprocess_env
from graphlink_scratch_dirs import (
    PYTHON_REPL_ROOT,
    prepare_scratch_dir,
    safe_scratch_id,
    touch_scratch_dir_usage,
)


class PythonREPL:
    """
    A persistent Python subprocess that acts as a stateful REPL.
    Variables and imports survive between executions.
    Communicates via base64-encoded strings over stdin/stdout (encoding for safe IPC
    framing, not a security mechanism).

    After every execute() call, last_run_failed reports whether the executed
    code raised - the wrapper reports it structurally on the boundary line, so
    callers no longer scan stdout for English error keywords (which
    misclassified correct programs that merely printed words like "failed";
    audit finding B2). The boundary line carries a per-session nonce and is
    matched as an exact full line, so program output that happens to contain
    the marker text can no longer truncate the result or desync every
    subsequent call (audit finding B4).

    ADR-005 stage 5.1: the subprocess runs with cwd set to a per-instance
    scratch directory, never the app's own working directory. Without this,
    LLM-generated code with a relative path (open("config.json", "w")) could
    clobber real application files, and python -c's sys.path[0] == '' would
    make every app module (including graphlink_secrets) directly importable
    by executed code. Mirrors VirtualEnvSandbox's own base_dir pattern in
    graphlink_plugins/code_sandbox/domain.py exactly (same safe-id
    sanitization, same tempdir root convention, sibling directory name).

    ADR-005 stage 5.3 (review-fix): that scratch directory is keyed by the
    caller-supplied repl_id, NOT by any node id a caller might otherwise be
    tempted to use - a node id can be reassigned fresh, purely by array
    position, every time a session is (re)loaded, which would let a reload
    silently swap which on-disk directory a REPL resolved to. A caller that
    wants stable, reload-independent scratch space should mint its own id
    once and round-trip it through whatever persistence it owns.

    PLAN-2026-08-24 §3.2.6 (`python.exec`): `cwd`/`manage_cwd` let the
    harness point a REPL at its own bound workspace instead of a
    repl_id-derived scratch dir. `manage_cwd=False` is what makes that safe
    for a USER's project folder: prepare_scratch_dir's 0700 chmod and
    touch_scratch_dir_usage's mtime bump are correct for a directory this
    app owns and age-sweeps, and wrong for one the person owns - we neither
    created it nor may we re-permission it. The caller that supplies a cwd
    is asserting it already exists and is already governed.
    """
    def __init__(self, repl_id=None, cwd=None, manage_cwd=True):
        self.process = None
        self.last_run_failed = False
        self._boundary_prefix = ""
        # ADR-005 stage 5.2: the resource guard for whichever process
        # `start()` currently owns - see stop()'s own comment for why
        # closing this, not just process.kill(), is what makes "Stop"
        # actually kill the whole tree.
        self.guard = None
        # Adversarial-review fix: serializes start()/stop() against
        # concurrent calls on the SAME PythonREPL instance. This is a real,
        # reproduced scenario, not theoretical - a caller that wraps
        # execute() in asyncio.wait_for(asyncio.to_thread(...)) can have a
        # timed-out wait_for NOT stop the underlying worker thread; on
        # timeout, cleanup calling stop() from a NEW thread while the
        # ORIGINAL execute() call's thread may still be blocked reading the
        # (now-killed) process's stdout, whose own EOF-handling path also
        # calls stop() - two concurrent, unsynchronized stop() calls on one
        # instance previously crashed with "AttributeError: 'NoneType'
        # object has no attribute 'kill'" (one thread nulling self.process
        # between the other's `if self.process` check and its later
        # self.process.kill() call) and could double-close the same real OS
        # job handle.
        self._lock = threading.RLock()
        self._manage_cwd = bool(manage_cwd) and cwd is None
        self.cwd = Path(cwd) if cwd is not None else PYTHON_REPL_ROOT / safe_scratch_id(repl_id)

    def start(self):
        with self._lock:
            # Adversarial-review fix: execute() calls start() again to
            # restart a dead REPL process (poll() is not None) WITHOUT
            # going through stop() first - stop() is what normally closes
            # self.guard, so that restart path used to silently overwrite
            # self.guard with a fresh one, orphaning the old Job Object
            # handle for the life of the backend process. Reproduced: 20
            # forced crash-restarts (killing the process directly,
            # bypassing stop()) leaked 20 unclosed job handles and grew
            # this process's real OS handle count by +16. Closing any
            # pre-existing guard here, mirroring stop()'s own
            # close-then-null pattern, makes EVERY path that (re)assigns
            # self.guard symmetric with cleanup, not just the one stop()
            # already covered.
            if self.guard:
                self.guard.close()
                self.guard = None
            nonce = uuid.uuid4().hex
            self._boundary_prefix = f"---GRAPHLINK_EXEC_BOUNDARY:{nonce}:"
            script = f"""
import sys, traceback, base64
env = {{}}
while True:
    line = sys.stdin.readline()
    if not line: break
    failed = False
    try:
        code = base64.b64decode(line.strip()).decode('utf-8')
        exec(code, env)
    except Exception:
        failed = True
        traceback.print_exc()
    status = "ERROR" if failed else "OK"
    print("\\n---GRAPHLINK_EXEC_BOUNDARY:{nonce}:" + status + "---", flush=True)
"""
            # ADR-002 P0: env= is explicit-allowlist, not inherited - see
            # graphlink_process_env's own module doc. Without this, the REPL
            # subprocess would inherit the backend's full os.environ,
            # including any provider API key configured as an environment
            # variable.
            kwargs = {'env': safe_subprocess_env()}
            # Hide the console window on Windows
            if sys.platform == 'win32':
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW

            # ADR-005 stage 5.1: cwd= a scratch dir, never the app's own cwd
            # (see this class's own docstring). Created here, not in
            # __init__, so a REPL that is constructed but never started
            # never touches the filesystem. ADR-005 stage 5.3: chmod 0700
            # on POSIX - see graphlink_scratch_dirs.prepare_scratch_dir's
            # own docstring for why.
            if self._manage_cwd:
                prepare_scratch_dir(self.cwd)

            # ADR-005 stage 5.2/5.3: the guard is created BEFORE Popen so
            # its popen_kwargs() can reach the spawn itself. On Windows
            # that dict is empty (a job object is applied to an already-
            # running process by assign() below); on POSIX it carries the
            # process-group request and the rlimit preexec hook, which have
            # to be in place between fork and exec - see
            # graphlink_execution_guard's own docstring.
            self.guard = create_execution_guard()

            self.process = subprocess.Popen(
                [sys.executable, '-c', script],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(self.cwd),
                **self.guard.popen_kwargs(),
                **kwargs
            )
            # Windows applies its cap here, after the child exists. An
            # accepted tradeoff over CREATE_SUSPENDED: the child hasn't run
            # any of its own code yet, so the window for it to spawn an
            # unassigned grandchild first is negligible. On POSIX this
            # records the process-group id that close() will kill.
            self.guard.assign(self.process.pid)

    def execute(self, code):
        if not self.process or self.process.poll() is not None:
            self.start()

        # ADR-005 stage 5.3 (review-fix): mark this cwd as actively used -
        # see touch_scratch_dir_usage's own docstring for why the age
        # sweep would otherwise treat a REPL run daily for months exactly
        # like one abandoned the day after creation.
        if self._manage_cwd:
            touch_scratch_dir_usage(self.cwd)

        encoded_code = base64.b64encode(code.encode('utf-8')).decode('utf-8')
        try:
            self.process.stdin.write(encoded_code + "\n")
            self.process.stdin.flush()
        except Exception as e:
            self.last_run_failed = True
            return f"Failed to send code to REPL: {e}"

        output = []
        while True:
            line = self.process.stdout.readline()
            if not line:
                # EOF with no boundary line: the REPL process died mid-run
                # (e.g. the executed code called sys.exit() or hard-crashed
                # the interpreter). Reap it now - stdout EOF can arrive
                # before poll() reports the exit, and without this the next
                # execute() could write into the dying process's stdin
                # (EINVAL) instead of restarting.
                self.last_run_failed = True
                self.stop()
                break
            stripped = line.strip()
            if stripped == self._boundary_prefix + "OK---":
                self.last_run_failed = False
                break
            if stripped == self._boundary_prefix + "ERROR---":
                self.last_run_failed = True
                break
            output.append(line)

        return "".join(output).strip()

    def stop(self):
        # Adversarial-review fix: serialized against a concurrent stop()
        # (or start()) on this same instance via self._lock - see
        # __init__'s own comment for the real, reproduced crash this
        # closes ("AttributeError: 'NoneType' object has no attribute
        # 'kill'" from two threads both inside this method at once, one
        # nulling self.process out from under the other's later use of it).
        with self._lock:
            if self.process:
                process = self.process
                # ADR-005 stage 5.2: close the resource guard FIRST - on
                # Windows this terminates the whole job (the REPL process
                # AND anything it has itself spawned), closing the
                # pre-existing gap where process.kill() alone only ever
                # killed the one directly-tracked process, never its own
                # children. Still followed by the existing
                # process.kill()/wait() unconditionally: a safe no-op if
                # the guard already killed it, and the only thing that
                # actually stops the direct child on non-Windows in this
                # stage (the POSIX process-group tier is ADR-005 stage 5.3).
                if self.guard:
                    self.guard.close()
                    self.guard = None
                try:
                    process.kill()
                    process.wait()
                finally:
                    # Popen owns real pipe objects for stdin/stdout even
                    # after the child has been reaped. Close them here
                    # rather than relying on Popen's finalizer, which
                    # otherwise emits ResourceWarning and leaves cleanup
                    # dependent on garbage collection.
                    for stream in (process.stdin, process.stdout, process.stderr):
                        if stream is not None:
                            stream.close()
                    self.process = None


class CodeAnalysisAgent:
    def __init__(self):
        self.system_prompt = """
You are a code analysis AI. Your task is to provide a final, user-facing answer based on the available information.

- If an "Original Prompt" is provided, synthesize all information to answer it directly.
- If no "Original Prompt" is provided, simply analyze the given code and its output.
- Explain what the code does and how the output relates to it.
- If the output contains an error, explain the error and suggest a fix.
- Format your response clearly using markdown.
"""

    def get_response(self, original_prompt, code, code_output):
        if original_prompt:
            user_message = f"""
Original Prompt: "{original_prompt}"

--- Generated Python Code ---
{code}

--- Code Execution Output ---
{code_output}

Based on all the above, please provide a comprehensive and helpful final answer to my original prompt.
"""
        else:
            user_message = f"""
Please analyze the following Python code and its execution output.

--- Python Code ---
{code}

--- Execution Output ---
{code_output}
"""
        messages = [
            {'role': 'system', 'content': self.system_prompt},
            {'role': 'user', 'content': user_message}
        ]
        response = api_provider.chat(task=config.TASK_CHAT, messages=messages)
        return response['message']['content']
