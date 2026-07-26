"""Py-Coder's Qt-free domain pieces, split out of graphlink_agents_pycoder.py
(Qt-removal plan R5.4) so backend/agents.py can import a persistent Python
REPL and the three LLM-calling agents without ever pulling the Qt stack into
the FastAPI process.

Moved here VERBATIM (same code, same behavior) from graphlink_agents_pycoder.py:
PyCoderStage, PyCoderStatus, PythonREPL, PyCoderReplManager,
PyCoderExecutionAgent, PyCoderRepairAgent, PyCoderAnalysisAgent - all of these
were already pure/Qt-free in the legacy file (confirmed by reading it directly
before this split: zero Qt references anywhere in this block). The ONLY
change from the legacy source is the config import: `graphlink_config` (which
transitively imports Qt's GUI/widget modules at module scope) becomes
`graphlink_task_config` (the R4.1 Qt-free split), mirroring the exact same
swap graphlink_plugins/gitlink/agent.py already made for the same reason.

What did NOT move here (stays in graphlink_agents_pycoder.py, unchanged):
CodeExecutionWorker, PyCoderExecutionWorker, PyCoderAgentWorker - the three
Qt worker-thread subclasses. They still import these classes below (via this
module) directly rather than defining them inline.

backend/agents.py's own new AgentDispatcher pipeline (R5.4) does NOT use
PyCoderReplManager - that class's weakref.WeakKeyDictionary keying strategy
is bound to a live scene-graph node object's own identity, which does not
survive the port (a backend SceneNode's node_id is a plain string, never
weakly referenceable, and no GC signal reaches the session layer when a
SceneNode dataclass instance is dropped). PyCoderReplManager is kept here, unmodified,
purely so the legacy Qt app's own graphlink_window_actions.py can keep using
it exactly as before. The new backend instead does explicit, string-keyed
REPL lifecycle management on AgentDispatcher itself (see that module's
_pycoder_repls/get_pycoder_repl/dispose_pycoder_repl).
"""

import base64
import json
import re
import subprocess
import sys
import uuid
import weakref
from enum import Enum

import api_provider
import graphlink_task_config as config


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
    """
    def __init__(self):
        self.process = None
        self.last_run_failed = False
        self._boundary_prefix = ""

    def start(self):
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
        kwargs = {}
        # Hide the console window on Windows
        if sys.platform == 'win32':
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW

        self.process = subprocess.Popen(
            [sys.executable, '-c', script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            **kwargs
        )

    def execute(self, code):
        if not self.process or self.process.poll() is not None:
            self.start()

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
        if self.process:
            self.process.kill()
            self.process.wait()
            self.process = None


class PyCoderReplManager:
    """Owns the PythonREPL subprocess for each PyCoderNode, keyed by node
    identity. PyCoderNode used to construct and stop its own REPL directly;
    ownership moves here so the node no longer manages a live subprocess.

    A weakref.finalize callback registered at REPL-creation time stops the
    subprocess once the owning node is garbage collected, regardless of
    whether stop()/dispose() was ever called first. This is load-bearing,
    not just a safety net: ChatScene.clear() (the "New Chat"/chat-switch
    path) never calls PyCoderNode.dispose() at all - only Python's own GC,
    via this finalizer, stops the REPL on that path. dispose() (the
    individual right-click-delete path) still calls stop() directly for
    immediate, deterministic cleanup rather than waiting on GC.

    A plain dict keyed by node would keep every node alive forever (the
    dict entry itself would be a strong reference), which would prevent the
    very GC this manager relies on - hence WeakKeyDictionary.

    R5.4: this class stays legacy-only (the Qt app's graphlink_window_actions.py
    is its only remaining caller) - see this module's own docstring for why
    the new backend's AgentDispatcher does not use it.
    """

    def __init__(self):
        self._repls = weakref.WeakKeyDictionary()
        self._finalizers = weakref.WeakKeyDictionary()

    def get_repl(self, node):
        repl = self._repls.get(node)
        if repl is None:
            repl = PythonREPL()
            self._repls[node] = repl
            self._finalizers[node] = weakref.finalize(node, repl.stop)
        return repl

    def stop(self, node):
        finalizer = self._finalizers.pop(node, None)
        if finalizer is not None:
            finalizer.detach()
        repl = self._repls.pop(node, None)
        if repl is not None:
            repl.stop()


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
