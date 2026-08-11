"""ADR-014 H3: graphlink_plugins/pycoder/domain.py had zero direct unit
tests - only indirect coverage via backend/tests/test_execution_guard.py
(the Windows Job Object guard's wiring into PythonREPL.start()/stop(), plus
its own crash-restart-leak and concurrent-stop-race regression pins) and
backend/tests/test_process_env_allowlist.py (safe_subprocess_env()'s pure
allowlist logic, unwired). Neither exercises PythonREPL's actual REPL
behavior (real stdout, state persistence, error classification, EOF/crash
handling) or any of the three LLM-calling Agent classes at all.

This file deliberately does NOT re-test what test_execution_guard.py already
covers (guard assign/close on start/stop, the crash-restart leak fix, the
concurrent-stop lock, prepare_scratch_dir/touch_scratch_dir_usage wiring) -
see that file for those. It covers:

  - PythonREPL's own REPL semantics against REAL subprocesses (this repo's
    established precedent for this class - see test_execution_guard.py's
    own module docstring): real stdout, cross-call state persistence,
    last_run_failed classification, the EOF-mid-read crash path (distinct
    from test_execution_guard's kill-between-calls restart path), the
    stdin-write-failure path, stop()/restart idempotency, and one true
    end-to-end proof that the executed code's actual cwd is the scratch
    directory (existing coverage only proves prepare_scratch_dir(cwd) was
    *called*, never that Popen's cwd= really lands the child there) plus one
    proving env=safe_subprocess_env() is really wired into the real Popen
    call (existing coverage only proves the allowlist's own pure logic).

  - PyCoderExecutionAgent / PyCoderRepairAgent / PyCoderAnalysisAgent: mocks
    the api_provider.chat boundary (mirroring backend/tests/test_agents.py's
    own `monkeypatch.setattr(api_provider, "chat", ...)` convention) and
    exercises the real prompt-construction and response-parsing logic around
    it - conversation-history JSON embedding, the is_final_attempt repair-
    prompt branch, the ```python fenced-code extraction regex (and its
    fallback to the raw response), and the original_prompt-present/absent
    analysis-prompt branch.
"""

from __future__ import annotations

import json
import re
from unittest.mock import MagicMock

import api_provider
import graphlink_task_config as config
from graphlink_plugins.pycoder.domain import (
    PyCoderAnalysisAgent,
    PyCoderExecutionAgent,
    PyCoderRepairAgent,
    PythonREPL,
)


# ============================================================================
# PythonREPL - happy path, against real subprocesses
# ============================================================================


class TestPythonReplHappyPath:
    def test_start_spawns_a_real_running_process(self):
        repl = PythonREPL(repl_id="happy-start-test")
        try:
            repl.start()
            assert repl.process is not None
            assert repl.process.poll() is None, "the child should still be alive right after start()"
        finally:
            repl.stop()

    def test_execute_returns_real_stdout(self):
        repl = PythonREPL(repl_id="happy-stdout-test")
        try:
            result = repl.execute("print('hello from repl')")
            assert result == "hello from repl"
            assert repl.last_run_failed is False
        finally:
            repl.stop()

    def test_execute_implicitly_starts_the_repl(self):
        repl = PythonREPL(repl_id="happy-implicit-start-test")
        assert repl.process is None
        try:
            repl.execute("1 + 1")
            assert repl.process is not None
        finally:
            repl.stop()

    def test_state_persists_across_execute_calls(self):
        # The whole point of PythonREPL over subprocess.run: it's a
        # persistent interpreter, not a fresh one per call.
        repl = PythonREPL(repl_id="happy-state-test")
        try:
            repl.execute("x = 41")
            result = repl.execute("print(x + 1)")
            assert result == "42"
        finally:
            repl.stop()

    def test_imports_persist_across_execute_calls(self):
        repl = PythonREPL(repl_id="happy-import-persist-test")
        try:
            repl.execute("import math")
            result = repl.execute("print(math.floor(3.7))")
            assert result == "3"
        finally:
            repl.stop()

    def test_last_run_failed_is_false_on_success(self):
        repl = PythonREPL(repl_id="happy-success-flag-test")
        try:
            repl.execute("print('ok')")
            assert repl.last_run_failed is False
        finally:
            repl.stop()

    def test_last_run_failed_is_true_on_a_raised_exception(self):
        repl = PythonREPL(repl_id="happy-error-flag-test")
        try:
            result = repl.execute("raise ValueError('boom')")
            assert repl.last_run_failed is True
            assert "ValueError" in result
            assert "boom" in result
        finally:
            repl.stop()

    def test_repl_survives_and_keeps_working_after_a_caught_exception(self):
        # The wrapper script's exec() failing must not kill the REPL loop -
        # only an uncaught process-level crash should.
        repl = PythonREPL(repl_id="happy-recovery-test")
        try:
            repl.execute("raise KeyError('nope')")
            assert repl.last_run_failed is True
            result = repl.execute("print('still alive')")
            assert result == "still alive"
            assert repl.last_run_failed is False
        finally:
            repl.stop()

    def test_output_containing_the_word_error_is_not_misclassified(self):
        # Regression precedent named directly in PythonREPL's own docstring
        # (audit finding B2): classification is structural (the boundary
        # line), not a scan for English error keywords in stdout.
        repl = PythonREPL(repl_id="happy-error-keyword-test")
        try:
            result = repl.execute("print('the operation failed gracefully')")
            assert repl.last_run_failed is False
            assert result == "the operation failed gracefully"
        finally:
            repl.stop()

    def test_multiline_code_round_trips_through_base64(self):
        repl = PythonREPL(repl_id="happy-multiline-test")
        code = "\n".join([
            "total = 0",
            "for i in range(5):",
            "    total += i",
            "print(total)",
        ])
        try:
            result = repl.execute(code)
            assert result == "10"
        finally:
            repl.stop()

    def test_unicode_code_and_output_round_trip_through_base64(self):
        # Restricted to Latin-1-supplement characters (codepage-safe on
        # Windows' default console codepage, e.g. cp1252) rather than
        # broader Unicode (e.g. CJK or symbol characters) - see this
        # module's own test run notes: the wrapper script's child process
        # gets no explicit stdout encoding from PythonREPL.start() (no
        # `encoding="utf-8"` on Popen, and safe_subprocess_env() allowlists
        # LANG/LC_ALL for POSIX but nothing that forces UTF-8 I/O on
        # Windows), so print()-ing a character outside the console's default
        # codepage genuinely raises UnicodeEncodeError inside the child on
        # this platform - a real, unfixed gap flagged separately, not a
        # test-authoring mistake. This test still proves the base64 framing
        # itself round-trips real non-ASCII bytes correctly.
        repl = PythonREPL(repl_id="happy-unicode-test")
        try:
            result = repl.execute("print('café')")
            assert result == "café"
        finally:
            repl.stop()

    def test_stop_clears_process_and_guard(self):
        repl = PythonREPL(repl_id="happy-stop-test")
        repl.execute("1 + 1")
        real_process = repl.process
        repl.stop()
        assert repl.process is None
        assert repl.guard is None
        # And the OS-level process is actually gone, not just detached.
        assert real_process.poll() is not None, "the real subprocess must actually be dead after stop()"


class TestPythonReplStopAndRestartIdempotency:
    def test_stop_without_ever_starting_does_not_raise(self):
        repl = PythonREPL(repl_id="idempotent-never-started-test")
        repl.stop()  # must be a no-op, not an AttributeError
        assert repl.process is None
        assert repl.guard is None

    def test_calling_stop_twice_in_a_row_does_not_raise(self):
        repl = PythonREPL(repl_id="idempotent-double-stop-test")
        repl.execute("1 + 1")
        repl.stop()
        repl.stop()  # second call: self.process is already None, must be a no-op
        assert repl.process is None
        assert repl.guard is None

    def test_execute_after_stop_transparently_restarts(self):
        repl = PythonREPL(repl_id="idempotent-restart-test")
        try:
            repl.execute("x = 1")
            repl.stop()
            assert repl.process is None
            # A fresh process - and thus fresh state, since it's a new
            # interpreter - not an error.
            result = repl.execute("print('restarted')")
            assert result == "restarted"
            assert repl.process is not None
        finally:
            repl.stop()

    def test_state_does_not_survive_a_stop_restart_cycle(self):
        # Sibling of test_state_persists_across_execute_calls: proves state
        # persistence is a property of the live process, not something
        # PythonREPL fakes up some other way across a real restart.
        repl = PythonREPL(repl_id="idempotent-state-reset-test")
        try:
            repl.execute("x = 99")
            repl.stop()
            result = repl.execute("print(x)")
            assert repl.last_run_failed is True
            assert "NameError" in result
        finally:
            repl.stop()


class TestPythonReplCrashMidRead:
    """The EOF-with-no-boundary-line branch (execute()'s `if not line: ...
    self.stop(); break`) - distinct from test_execution_guard.py's
    TestPythonReplRestartDoesNotLeakTheOldGuard, which kills the process
    BETWEEN execute() calls (poll()-based restart at the top of execute()).
    Here the process dies DURING the readline() loop of the call that
    triggered it."""

    def test_sys_exit_inside_executed_code_is_detected_as_a_crash(self):
        # sys.exit() raises SystemExit, which is a BaseException, not an
        # Exception - the wrapper script's `except Exception:` does not
        # catch it, so it propagates and kills the whole wrapper process.
        repl = PythonREPL(repl_id="crash-sysexit-test")
        try:
            repl.execute("import sys; sys.exit(1)")
            assert repl.last_run_failed is True
            # execute()'s EOF branch calls self.stop() itself.
            assert repl.process is None
        finally:
            repl.stop()

    def test_the_repl_auto_restarts_and_keeps_working_after_a_crash(self):
        repl = PythonREPL(repl_id="crash-recovery-test")
        try:
            repl.execute("import os; os._exit(1)")
            assert repl.last_run_failed is True
            assert repl.process is None

            result = repl.execute("print('back up')")
            assert result == "back up"
            assert repl.last_run_failed is False
            assert repl.process is not None
        finally:
            repl.stop()


class TestPythonReplStdinWriteFailure:
    """The `except Exception as e: return f"Failed to send code to REPL:
    {e}"` branch - mocks only the stdin file object on an otherwise real,
    running process, to force a deterministic write failure without racing
    a real pipe-closed timing window."""

    def test_a_stdin_write_failure_is_reported_without_crashing(self):
        repl = PythonREPL(repl_id="stdin-failure-test")
        repl.start()
        real_stdin = repl.process.stdin
        try:
            broken_stdin = MagicMock()
            broken_stdin.write.side_effect = OSError("simulated broken pipe")
            repl.process.stdin = broken_stdin

            result = repl.execute("1 + 1")

            assert "Failed to send code to REPL" in result
            assert "simulated broken pipe" in result
            assert repl.last_run_failed is True
        finally:
            repl.process.stdin = real_stdin
            repl.stop()


class TestPythonReplScratchDirIsolation:
    """test_execution_guard.py's TestPycoderScratchDirWiring proves
    prepare_scratch_dir(repl.cwd) is CALLED. These prove the isolation it
    exists for actually holds end to end: the child's real working directory
    is that scratch dir, and the restricted environment really reaches the
    real Popen() call, not just safe_subprocess_env()'s own pure allowlist
    logic (already covered by test_process_env_allowlist.py in isolation)."""

    def test_executed_code_actually_runs_with_cwd_set_to_the_scratch_dir(self):
        repl = PythonREPL(repl_id="cwd-isolation-test")
        try:
            repl.execute("open('marker.txt', 'w').write('hello from repl')")
            marker = repl.cwd / "marker.txt"
            assert marker.exists(), (
                "a relative-path file write from executed code must land in "
                "the REPL's own scratch dir, not the app's cwd"
            )
            assert marker.read_text(encoding="utf-8") == "hello from repl"
        finally:
            repl.stop()
            marker_path = repl.cwd / "marker.txt"
            if marker_path.exists():
                marker_path.unlink()

    def test_the_real_subprocess_environment_excludes_a_secret_shaped_variable(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak-into-repl")
        repl = PythonREPL(repl_id="env-isolation-test")
        try:
            result = repl.execute("import os; print(os.environ.get('ANTHROPIC_API_KEY'))")
            assert result.strip() == "None", (
                "PythonREPL.start() must pass env=safe_subprocess_env() into "
                "the real Popen() call, not inherit the parent's full "
                "environment"
            )
        finally:
            repl.stop()


# ============================================================================
# PyCoderExecutionAgent
# ============================================================================


def _capture_chat(monkeypatch, response_content="FAKE_RESPONSE"):
    """Mirrors backend/tests/test_agents.py's own
    `monkeypatch.setattr(api_provider, "chat", ...)` convention. Returns the
    list of captured (task, messages, kwargs) calls."""
    calls = []

    def _fake_chat(task, messages, **kwargs):
        calls.append({"task": task, "messages": messages, "kwargs": kwargs})
        return {"message": {"content": response_content}}

    monkeypatch.setattr(api_provider, "chat", _fake_chat)
    return calls


class TestPyCoderExecutionAgent:
    def test_get_response_calls_chat_with_task_chat_and_system_plus_user_messages(self, monkeypatch):
        calls = _capture_chat(monkeypatch)
        agent = PyCoderExecutionAgent()

        agent.get_response(conversation_history=[], user_prompt="sort a list")

        assert len(calls) == 1
        call = calls[0]
        assert call["task"] == config.TASK_CHAT
        assert [m["role"] for m in call["messages"]] == ["system", "user"]
        assert "[TOOL:PYTHON]" in call["messages"][0]["content"]

    def test_get_response_embeds_the_conversation_history_as_json(self, monkeypatch):
        calls = _capture_chat(monkeypatch)
        agent = PyCoderExecutionAgent()
        history = [
            {"role": "user", "content": "I have a list: 3, 1, 2."},
            {"role": "assistant", "content": "Got it."},
        ]

        agent.get_response(conversation_history=history, user_prompt="sort it")

        user_message = calls[0]["messages"][1]["content"]
        assert json.dumps(history, indent=2) in user_message
        assert "sort it" in user_message

    def test_get_response_returns_the_chat_response_content(self, monkeypatch):
        _capture_chat(monkeypatch, response_content="[TOOL:PYTHON]\nprint(1)\n[/TOOL]")
        agent = PyCoderExecutionAgent()

        result = agent.get_response(conversation_history=[], user_prompt="anything")

        assert result == "[TOOL:PYTHON]\nprint(1)\n[/TOOL]"

    def test_user_prompt_is_embedded_verbatim_in_the_final_prompt(self, monkeypatch):
        calls = _capture_chat(monkeypatch)
        agent = PyCoderExecutionAgent()

        agent.get_response(conversation_history=[], user_prompt='what is 2 + 2?')

        user_message = calls[0]["messages"][1]["content"]
        assert 'Final User Prompt: "what is 2 + 2?"' in user_message


# ============================================================================
# PyCoderRepairAgent
# ============================================================================


class TestPyCoderRepairAgentPromptConstruction:
    def test_default_call_builds_the_bug_fix_prompt_not_the_retry_prompt(self, monkeypatch):
        calls = _capture_chat(monkeypatch)
        agent = PyCoderRepairAgent()

        agent.get_response(code="x = 1/0", error="ZeroDivisionError")

        user_message = calls[0]["messages"][1]["content"]
        assert "The following Python code produced an error" in user_message
        assert "x = 1/0" in user_message
        assert "ZeroDivisionError" in user_message
        assert "Re-evaluate the original problem" not in user_message

    def test_is_final_attempt_true_builds_the_retry_prompt_instead(self, monkeypatch):
        calls = _capture_chat(monkeypatch)
        agent = PyCoderRepairAgent()

        agent.get_response(code="x = 1/0", error="ZeroDivisionError", is_final_attempt=True)

        user_message = calls[0]["messages"][1]["content"]
        assert "Re-evaluate the original problem" in user_message
        assert "x = 1/0" in user_message
        assert "ZeroDivisionError" in user_message
        assert "The following Python code produced an error" not in user_message

    def test_calls_chat_with_task_chat(self, monkeypatch):
        calls = _capture_chat(monkeypatch)
        agent = PyCoderRepairAgent()

        agent.get_response(code="pass", error="none")

        assert calls[0]["task"] == config.TASK_CHAT
        assert [m["role"] for m in calls[0]["messages"]] == ["system", "user"]


class TestPyCoderRepairAgentResponseParsing:
    def test_extracts_code_from_a_single_fenced_python_block(self, monkeypatch):
        _capture_chat(
            monkeypatch,
            response_content="Here is the fix:\n```python\nprint('fixed')\n```\nLet me know if that works.",
        )
        agent = PyCoderRepairAgent()

        result = agent.get_response(code="broken", error="err")

        assert result == "print('fixed')"

    def test_falls_back_to_the_raw_stripped_response_when_unfenced(self, monkeypatch):
        _capture_chat(monkeypatch, response_content="  print('no fence here')  ")
        agent = PyCoderRepairAgent()

        result = agent.get_response(code="broken", error="err")

        assert result == "print('no fence here')"

    def test_extracts_only_the_first_fenced_block_when_multiple_are_present(self):
        # Direct regex-behavior test - pins the module's own extraction
        # pattern (non-greedy .*? under re.DOTALL) independent of any
        # provider mock.
        response = (
            "```python\nfirst_block()\n```\n"
            "some commentary\n"
            "```python\nsecond_block()\n```"
        )
        match = re.search(r"```python\n(.*?)\n```", response, re.DOTALL)
        assert match is not None
        assert match.group(1).strip() == "first_block()"

    def test_multiline_fenced_code_is_preserved_verbatim(self, monkeypatch):
        code_block = "def f(x):\n    return x + 1\n\nprint(f(41))"
        _capture_chat(monkeypatch, response_content=f"```python\n{code_block}\n```")
        agent = PyCoderRepairAgent()

        result = agent.get_response(code="broken", error="err")

        assert result == code_block


# ============================================================================
# PyCoderAnalysisAgent
# ============================================================================


class TestPyCoderAnalysisAgent:
    def test_with_an_original_prompt_includes_it_and_calls_chat_task_chat(self, monkeypatch):
        calls = _capture_chat(monkeypatch)
        agent = PyCoderAnalysisAgent()

        agent.get_response(original_prompt="what is the sum?", code="print(sum([1,2]))", code_output="3")

        assert calls[0]["task"] == config.TASK_CHAT
        user_message = calls[0]["messages"][1]["content"]
        assert 'Original Prompt: "what is the sum?"' in user_message
        assert "print(sum([1,2]))" in user_message
        assert "3" in user_message
        assert "Based on all the above" in user_message

    def test_without_an_original_prompt_omits_the_original_prompt_label(self, monkeypatch):
        calls = _capture_chat(monkeypatch)
        agent = PyCoderAnalysisAgent()

        agent.get_response(original_prompt=None, code="print('hi')", code_output="hi")

        user_message = calls[0]["messages"][1]["content"]
        assert "Original Prompt:" not in user_message
        assert "Please analyze the following Python code" in user_message
        assert "print('hi')" in user_message
        assert "hi" in user_message

    def test_an_empty_string_original_prompt_is_treated_as_absent(self, monkeypatch):
        # `if original_prompt:` - an empty string is falsy, same branch as
        # None, not the "Original Prompt:" branch.
        calls = _capture_chat(monkeypatch)
        agent = PyCoderAnalysisAgent()

        agent.get_response(original_prompt="", code="print(1)", code_output="1")

        user_message = calls[0]["messages"][1]["content"]
        assert "Original Prompt:" not in user_message
        assert "Please analyze the following Python code" in user_message

    def test_returns_the_chat_response_content(self, monkeypatch):
        _capture_chat(monkeypatch, response_content="This code prints hi.")
        agent = PyCoderAnalysisAgent()

        result = agent.get_response(original_prompt=None, code="print('hi')", code_output="hi")

        assert result == "This code prints hi."
