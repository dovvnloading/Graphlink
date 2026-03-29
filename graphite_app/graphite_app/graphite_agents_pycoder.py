import subprocess
import tempfile
import sys
import os
import re
import json
import base64
from enum import Enum
from PySide6.QtCore import QThread, Signal
import graphite_config as config
import api_provider


class PyCoderStage(Enum):
    ANALYZE = 1
    GENERATE = 2
    EXECUTE = 3
    REPAIR = 3
    ANALYZE_RESULT = 4


class PyCoderStatus(Enum):
    PENDING = 1
    RUNNING = 2
    SUCCESS = 3
    FAILURE = 4


class PythonREPL:
    """
    A persistent Python subprocess that acts as a stateful REPL.
    Variables and imports survive between executions.
    Communicates securely via base64 encoded strings over stdin/stdout.
    """
    def __init__(self):
        self.process = None

    def start(self):
        script = """
import sys, traceback, base64
env = {}
while True:
    line = sys.stdin.readline()
    if not line: break
    try:
        code = base64.b64decode(line.strip()).decode('utf-8')
        exec(code, env)
    except Exception:
        traceback.print_exc()
    print("\\n---GRAPHITE_EXEC_BOUNDARY---", flush=True)
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
            return f"Failed to send code to REPL: {e}"

        output = []
        while True:
            line = self.process.stdout.readline()
            if not line or "---GRAPHITE_EXEC_BOUNDARY---" in line:
                break
            output.append(line)
        
        return "".join(output).strip()

    def stop(self):
        if self.process:
            self.process.kill()
            self.process.wait()
            self.process = None


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


class PyCoderExecutionWorker(QThread):
    log_update = Signal(object, object)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, user_prompt, conversation_history, repl):
        super().__init__()
        self.user_prompt = user_prompt
        self.conversation_history = conversation_history
        self.repl = repl
        self.execution_agent = PyCoderExecutionAgent()
        self.repair_agent = PyCoderRepairAgent()
        self.analysis_agent = PyCoderAnalysisAgent()
        self._is_running = True
        
    def stop(self):
        self._is_running = False
        if self.repl:
            self.repl.stop()

    def _is_error(self, output):
        error_keywords = ["traceback (most recent call last)", "error:", "exception:", "failed"]
        return any(keyword in output.lower() for keyword in error_keywords)

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

            while retry_count < max_retries:
                if not self._is_running: return
                self.log_update.emit(PyCoderStage.EXECUTE, PyCoderStatus.RUNNING)
                
                execution_output = ""
                try:
                    execution_output = self.repl.execute(current_code)
                except Exception as e:
                    execution_output = f"\n--- EXECUTION FAILED ---\n{type(e).__name__}: {e}"

                if not self._is_running: return
                if not self._is_error(execution_output):
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