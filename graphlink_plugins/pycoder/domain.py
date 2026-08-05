"""Py-Coder's Qt-free domain pieces, split out of graphlink_agents_pycoder.py
(Qt-removal plan R5.4) so backend/agents.py can import a persistent Python
REPL and the three LLM-calling agents without ever pulling the Qt stack into
the FastAPI process.

Moved here VERBATIM (same code, same behavior) from graphlink_agents_pycoder.py:
PyCoderStage, PyCoderStatus, PythonREPL, PyCoderExecutionAgent,
PyCoderRepairAgent, PyCoderAnalysisAgent - all of these were already
pure/Qt-free in the legacy file (confirmed by reading it directly before
this split: zero Qt references anywhere in this block). The ONLY change
from the legacy source is the config import: `graphlink_config` (which
transitively imports Qt's GUI/widget modules at module scope) becomes
`graphlink_task_config` (the R4.1 Qt-free split), mirroring the exact same
swap graphlink_plugins/gitlink/agent.py already made for the same reason.

What did NOT move here (stays in graphlink_agents_pycoder.py, unchanged):
CodeExecutionWorker, PyCoderExecutionWorker, PyCoderAgentWorker - the three
Qt worker-thread subclasses. They still import these classes below (via this
module) directly rather than defining them inline.

backend/agents.py's own new AgentDispatcher pipeline (R5.4) does not use
PyCoderReplManager - it does explicit, string-keyed REPL lifecycle
management on AgentDispatcher itself instead (see that module's
_pycoder_repls/get_pycoder_repl/dispose_pycoder_repl). ADR-002 stage 2.1:
PyCoderReplManager itself is deleted as confirmed-dead code - its only
remaining caller was the legacy Qt app's graphlink_window_actions.py,
which no longer exists anywhere in the repo (deleted at the R7.6b
Qt-removal cutover).
"""

import base64
import json
import re
import subprocess
import sys
import threading
import uuid
from enum import Enum

import api_provider
import graphlink_task_config as config
from graphlink_execution_guard import create_execution_guard
from graphlink_process_env import safe_subprocess_env
from graphlink_scratch_dirs import (
    PYCODER_REPL_ROOT,
    prepare_scratch_dir,
    safe_scratch_id,
    touch_scratch_dir_usage,
)


class PyCoderStage(Enum):
    ANALYZE = 1
    GENERATE = 2
    EXECUTE = 3
    REPAIR = 4
    ANALYZE_RESULT = 5


class PyCoderStatus(Enum):
    PENDING = 1
    RUNNING = 2
    SUCCESS = 3
    FAILURE = 4


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

    ADR-005 stage 5.1: the subprocess runs with cwd set to a per-node scratch
    directory, never the app's own working directory. Without this, LLM-
    generated code with a relative path (open("config.json", "w")) could
    clobber real application files, and python -c's sys.path[0] == '' would
    make every app module (including graphlink_secrets) directly importable
    by executed code. Mirrors VirtualEnvSandbox's own base_dir pattern in
    graphlink_plugins/code_sandbox/domain.py exactly (same safe-id
    sanitization, same tempdir root convention, sibling directory name).

    ADR-005 stage 5.3 (review-fix): that scratch directory is keyed by
    repl_id, NOT by this node's own id - node.id is reassigned fresh,
    purely by array position, every time a session is (re)loaded
    (backend/domain/graph.py's register_restored_node), so keying by it
    let a reload silently swap which on-disk directory a node's REPL
    resolved to (one node losing its own accumulated files, or inheriting
    a different node's leftovers, any time an earlier node in save order
    was deleted before the next load). repl_id is
    node.state.pycoder_repl_id - minted once at node creation and
    round-tripped through session save/load independent of node.id
    churn, mirroring CodeSandboxState.code_sandbox_sandbox_id exactly
    (see PycoderState's own docstring for the full mechanism).
    """
    def __init__(self, repl_id=None):
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
        # reproduced scenario, not theoretical - backend/agents.py wraps
        # execute() in asyncio.wait_for(asyncio.to_thread(...)), and a
        # timed-out wait_for does NOT stop the underlying worker thread; on
        # timeout, agents.py's own cleanup calls stop() from a NEW thread
        # while the ORIGINAL execute() call's thread may still be blocked
        # reading the (now-killed) process's stdout, whose own EOF-handling
        # path also calls stop() - two concurrent, unsynchronized stop()
        # calls on one instance previously crashed with
        # "AttributeError: 'NoneType' object has no attribute 'kill'" (one
        # thread nulling self.process between the other's `if self.process`
        # check and its later self.process.kill() call) and could
        # double-close the same real OS job handle.
        self._lock = threading.RLock()
        self.cwd = PYCODER_REPL_ROOT / safe_scratch_id(repl_id)

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
                self.process.kill()
                self.process.wait()
                self.process = None


class PyCoderExecutionAgent:
    def __init__(self):
        self.system_prompt = """
You are an expert programmer and a helpful assistant. Your goal is to answer user prompts, using a Python code tool when necessary.
You will be given the previous conversation history for context, followed by the user's final prompt.

1.  First, analyze the user's final prompt in the context of the conversation history.
2.  If the prompt can be answered without computation, provide a direct, helpful answer.
3.  If the prompt requires computation or information from the history, you MUST generate Python code to solve it.
4.  When you generate code, you MUST wrap it in [TOOL:PYTHON] and [/TOOL] tags.
5.  The code should be self-contained and print its result. Do not assume any external libraries unless they are standard.
6.  Do not include any other text or explanation outside the tool tags if you decide to use the tool.

Example (with context):
Conversation History:
[
  {"role": "user", "content": "I have a list of numbers: 15, 8, 22, 5, 19."},
  {"role": "assistant", "content": "Okay, I see that list of numbers."}
]
Final User Prompt: "Please sort them in descending order."
Your response:
[TOOL:PYTHON]
numbers = [15, 8, 22, 5, 19]
numbers.sort(reverse=True)
print(numbers)
[/TOOL]
"""
    def get_response(self, conversation_history, user_prompt):
        history_str = json.dumps(conversation_history, indent=2)

        full_prompt = f"""
Conversation History:
{history_str}

Final User Prompt: "{user_prompt}"
"""
        messages = [
            {'role': 'system', 'content': self.system_prompt},
            {'role': 'user', 'content': full_prompt}
        ]
        response = api_provider.chat(task=config.TASK_CHAT, messages=messages)
        return response['message']['content']


class PyCoderRepairAgent:
    def __init__(self):
        self.system_prompt = """You are an expert Python code debugging assistant. You will be given a block of Python code and the error that occurred when it was executed.
Your task is to analyze the error and fix the code.
You MUST return ONLY the complete, corrected, and runnable Python code block.
Do not add explanations, apologies, or any text outside the code.
"""
        self.retry_prompt = """The previous attempts to fix the code have failed. The fundamental approach might be wrong.
Re-evaluate the original problem and the previous error. Provide a new, different block of Python code to solve it.
Return ONLY the complete, runnable Python code. Do not include any other text.
"""

    def get_response(self, code, error, is_final_attempt=False):
        if is_final_attempt:
            user_message = f"""
Original Problem: Find a new way to solve the task that previously resulted in an error.
Previous Code:
```python
{code}
```
Resulting Error:
```
{error}
```
{self.retry_prompt}
"""
        else:
            user_message = f"""
The following Python code produced an error. Please fix it.

--- Code with Bug ---
```python
{code}
```

--- Error Message ---
```
{error}
```

Return only the corrected code.
"""
        messages = [
            {'role': 'system', 'content': self.system_prompt},
            {'role': 'user', 'content': user_message}
        ]
        response = api_provider.chat(task=config.TASK_CHAT, messages=messages)
        cleaned_response = response['message']['content']
        code_match = re.search(r'```python\n(.*?)\n```', cleaned_response, re.DOTALL)
        if code_match:
            return code_match.group(1).strip()
        return cleaned_response.strip()


class PyCoderAnalysisAgent:
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
