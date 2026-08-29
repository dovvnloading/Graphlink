"""ADR-014 H3: graphlink_plugins/code_sandbox/domain.py had ZERO direct unit
tests before this file - only indirect coverage via backend/tests/
test_execution_guard.py (guard wiring only), test_scratch_dirs.py (scratch-dir
plumbing only) and test_code_sandbox_only_binary.py (the --only-binary
security fix only). None of those exercise this module's OWN logic: request
normalization/extraction, the two LLM-calling agents, VirtualEnvSandbox's
stop()/cleanup semantics, or sync_requirements' caching branch. This file
closes that gap directly, mirroring the real-subprocess-where-it-matters /
mock-only-what's-heavy-or-flaky discipline the three files above already
established for this exact module and its siblings (PythonREPL).

Layout:
  - Pure functions (_normalize_requirements, _extract_python_block,
    _subprocess_kwargs) - fast, no subprocess, no mocks needed.
  - The two LLM agents - api_provider.chat mocked (matches conftest.py's own
    autouse chat_stream fixture's established pattern: patch api_provider's
    module attribute, never the class in isolation).
  - VirtualEnvSandbox construction/python_executable - fast, no I/O.
  - VirtualEnvSandbox.stop() - MagicMock processes for the exception/fallback
    branches (mirrors test_execution_guard.py's _FakeGuard technique).
  - VirtualEnvSandbox._run_subprocess/execute_code - real subprocesses for
    the happy path, timeout, and interrupted-then-reused paths (a mock
    cannot prove a real timeout or a real InterruptedError recovery works).
  - VirtualEnvSandbox.sync_requirements - mock-based for the caching branch
    (fast, deterministic), one real end-to-end failure case for genuinely
    malformed manifest content (needs a real venv + real pip to prove the
    RuntimeError path, same tradeoff test_code_sandbox_only_binary.py
    already accepted for this same method).
  - One full real end-to-end happy path: real venv creation +
    real script execution, the "construct a sandbox, run simple Python, get
    real output" case the plugin's users actually depend on.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import api_provider
import graphlink_scratch_dirs as scratch_dirs
import graphlink_task_config as task_config
from graphlink_plugins.code_sandbox import domain as domain_module
from graphlink_plugins.code_sandbox.domain import (
    SandboxGenerationAgent,
    SandboxRepairAgent,
    SandboxStage,
    VirtualEnvSandbox,
    _extract_python_block,
    _normalize_requirements,
    _subprocess_kwargs,
)


# -- SandboxStage -------------------------------------------------------


class TestSandboxStage:
    def test_all_five_stages_exist_with_distinct_values(self):
        # Cheap sanity pin on the enum this module exports as part of its
        # public surface - nothing else in this file exercises it, and a
        # careless edit (duplicate value, renamed member) would otherwise
        # go completely unnoticed by every existing indirect caller.
        assert [s.name for s in SandboxStage] == [
            "GENERATE",
            "PREPARE",
            "INSTALL",
            "EXECUTE",
            "ANALYZE",
        ]
        assert len({s.value for s in SandboxStage}) == 5


# -- _normalize_requirements ---------------------------------------------


class TestNormalizeRequirements:
    def test_crlf_and_cr_line_endings_both_become_lf(self):
        assert _normalize_requirements("a\r\nb\rc\n") == "a\nb\nc"

    def test_trailing_whitespace_is_stripped_per_line(self):
        assert _normalize_requirements("foo   \nbar\t\n") == "foo\nbar"

    def test_leading_whitespace_on_an_interior_line_is_preserved(self):
        # str.strip() at the very end only trims the ENDS of the whole
        # string - leading whitespace on a line that isn't the first or
        # last line survives untouched. Worth pinning explicitly: it is
        # easy to assume this function fully re-indents every line.
        assert _normalize_requirements("a\n  b\nc") == "a\n  b\nc"

    def test_leading_whitespace_on_the_first_line_is_stripped_as_a_side_effect(self):
        # The mirror image of the test above: because this whitespace sits
        # at the very START of the whole string, the final overall
        # .strip() removes it - even though nothing in this function
        # specifically targets "the first line".
        assert _normalize_requirements("  foo\nbar") == "foo\nbar"

    def test_blank_or_whitespace_only_input_normalizes_to_empty_string(self):
        assert _normalize_requirements("") == ""
        assert _normalize_requirements("   \n  \n\t") == ""

    def test_blank_interior_lines_are_preserved_by_position(self):
        assert _normalize_requirements("a\n\nb") == "a\n\nb"


# -- _extract_python_block -------------------------------------------------


class TestExtractPythonBlock:
    def test_extracts_from_tool_python_tags_ignoring_surrounding_prose(self):
        text = "Sure, here you go:\n[TOOL:PYTHON]\nprint('hi')\n[/TOOL]\nHope that helps!"
        assert _extract_python_block(text) == "print('hi')"

    def test_extracts_from_a_fenced_python_block_when_no_tool_tag_present(self):
        text = "Here:\n```python\nprint('hi')\n```\n"
        assert _extract_python_block(text) == "print('hi')"

    def test_fenced_block_language_tag_is_case_insensitive(self):
        text = "```PYTHON\nprint(1)\n```"
        assert _extract_python_block(text) == "print(1)"

    def test_tool_tag_takes_priority_over_a_fenced_block_when_both_present(self):
        text = "[TOOL:PYTHON]\nprint('from tool')\n[/TOOL]\n```python\nprint('from fence')\n```"
        assert _extract_python_block(text) == "print('from tool')"

    def test_lowercase_tool_tag_is_not_recognized_case_sensitively(self):
        # The [TOOL:PYTHON] regex has no re.IGNORECASE (unlike the fenced
        # regex, which does) - a lowercase tag must fall through, not match.
        text = "[tool:python]\nprint('nope')\n[/tool]"
        assert _extract_python_block(text) is None

    def test_a_bare_fence_with_no_language_tag_is_not_recognized(self):
        text = "```\nprint('no language tag')\n```"
        assert _extract_python_block(text) is None

    def test_plain_prose_with_no_code_block_returns_none(self):
        assert _extract_python_block("The answer is 4, no code needed.") is None

    def test_extracted_code_is_stripped_of_surrounding_whitespace(self):
        text = "[TOOL:PYTHON]\n\n   print(1)   \n\n[/TOOL]"
        assert _extract_python_block(text) == "print(1)"


# -- _subprocess_kwargs ----------------------------------------------------


class TestSubprocessKwargs:
    def test_env_key_is_always_present(self):
        kwargs = _subprocess_kwargs()
        assert "env" in kwargs
        assert isinstance(kwargs["env"], dict)

    def test_windows_adds_create_no_window(self, monkeypatch):
        monkeypatch.setattr(domain_module.sys, "platform", "win32")
        kwargs = _subprocess_kwargs()
        assert kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW

    def test_non_windows_has_no_creationflags_key(self, monkeypatch):
        monkeypatch.setattr(domain_module.sys, "platform", "linux")
        kwargs = _subprocess_kwargs()
        assert "creationflags" not in kwargs


# -- SandboxGenerationAgent -------------------------------------------------


class TestSandboxGenerationAgent:
    def test_get_response_forwards_prompt_and_manifest_and_returns_the_reply(self, monkeypatch):
        captured = {}

        def fake_chat(task, messages, **kwargs):
            captured["task"] = task
            captured["messages"] = messages
            return {"message": {"content": "print('ok')"}}

        monkeypatch.setattr(api_provider, "chat", fake_chat)

        agent = SandboxGenerationAgent()
        result = agent.get_response(
            conversation_history=[{"role": "user", "content": "earlier turn"}],
            user_prompt="write a hello world script",
            requirements_manifest="requests==2.31.0",
        )

        assert result == "print('ok')"
        assert captured["task"] == task_config.TASK_CHAT
        assert captured["messages"][0]["role"] == "system"
        user_message = captured["messages"][1]["content"]
        assert captured["messages"][1]["role"] == "user"
        assert "earlier turn" in user_message
        assert "requests==2.31.0" in user_message
        assert "write a hello world script" in user_message

    def test_an_empty_requirements_manifest_renders_as_none_specified(self, monkeypatch):
        captured = {}

        def fake_chat(task, messages, **kwargs):
            captured["messages"] = messages
            return {"message": {"content": "..."}}

        monkeypatch.setattr(api_provider, "chat", fake_chat)

        SandboxGenerationAgent().get_response(
            conversation_history=[], user_prompt="hi", requirements_manifest=""
        )

        assert "[none specified]" in captured["messages"][1]["content"]


# -- SandboxRepairAgent ------------------------------------------------------


class TestSandboxRepairAgent:
    def test_get_response_returns_raw_reply_when_no_fenced_block_present(self, monkeypatch):
        monkeypatch.setattr(
            api_provider, "chat", lambda task, messages, **kwargs: {"message": {"content": "  fixed_code()  "}}
        )
        result = SandboxRepairAgent().get_response(
            code="broken_code()",
            error_output="NameError: broken_code not defined",
            requirements_manifest="",
        )
        assert result == "fixed_code()"

    def test_get_response_extracts_code_from_a_fenced_reply(self, monkeypatch):
        monkeypatch.setattr(
            api_provider,
            "chat",
            lambda task, messages, **kwargs: {"message": {"content": "```python\nfixed_code()\n```"}},
        )
        result = SandboxRepairAgent().get_response(
            code="broken_code()", error_output="boom", requirements_manifest=""
        )
        assert result == "fixed_code()"

    def test_missing_original_prompt_falls_back_to_manual_execution_label(self, monkeypatch):
        captured = {}

        def fake_chat(task, messages, **kwargs):
            captured["messages"] = messages
            return {"message": {"content": "fixed()"}}

        monkeypatch.setattr(api_provider, "chat", fake_chat)

        SandboxRepairAgent().get_response(
            code="x", error_output="y", requirements_manifest="", original_prompt=None
        )

        assert "[manual execution]" in captured["messages"][1]["content"]

    def test_forwards_the_broken_code_and_error_output_verbatim(self, monkeypatch):
        captured = {}

        def fake_chat(task, messages, **kwargs):
            captured["messages"] = messages
            return {"message": {"content": "fixed()"}}

        monkeypatch.setattr(api_provider, "chat", fake_chat)

        SandboxRepairAgent().get_response(
            code="def broken(:",
            error_output="SyntaxError: invalid syntax",
            requirements_manifest="numpy",
            original_prompt="build a broken thing",
        )

        user_message = captured["messages"][1]["content"]
        assert "def broken(:" in user_message
        assert "SyntaxError: invalid syntax" in user_message
        assert "numpy" in user_message
        assert "build a broken thing" in user_message


# -- VirtualEnvSandbox construction / python_executable ---------------------


class TestVirtualEnvSandboxConstruction:
    def test_paths_are_derived_from_the_sanitized_sandbox_id(self):
        raw_id = "weird id/../etc"
        sandbox = VirtualEnvSandbox(raw_id)

        assert sandbox.base_dir.name == scratch_dirs.safe_scratch_id(raw_id)
        assert sandbox.base_dir.parent == scratch_dirs.EXECUTION_SANDBOX_ROOT
        assert sandbox.venv_dir == sandbox.base_dir / "venv"
        assert sandbox.requirements_file == sandbox.base_dir / "requirements.txt"
        assert sandbox.requirements_hash_file == sandbox.base_dir / ".requirements.sha256"
        assert sandbox.script_path == sandbox.base_dir / "sandbox_entry.py"

    def test_starts_with_no_process_or_guard(self):
        sandbox = VirtualEnvSandbox("fresh-sandbox-test")
        assert sandbox.current_process is None
        assert sandbox.guard is None


class TestPythonExecutableProperty:
    def test_windows_points_at_scripts_python_exe(self, monkeypatch):
        monkeypatch.setattr(domain_module.os, "name", "nt")
        sandbox = VirtualEnvSandbox("python-exe-windows-test")
        assert sandbox.python_executable == sandbox.venv_dir / "Scripts" / "python.exe"

    def test_posix_points_at_bin_python(self, monkeypatch):
        monkeypatch.setattr(domain_module.os, "name", "posix")
        sandbox = VirtualEnvSandbox("python-exe-posix-test")
        assert sandbox.python_executable == sandbox.venv_dir / "bin" / "python"


# -- VirtualEnvSandbox.stop() -------------------------------------------


class TestStop:
    def test_stop_with_nothing_running_is_a_silent_no_op(self):
        sandbox = VirtualEnvSandbox("stop-noop-test")
        sandbox.stop()  # must not raise
        assert sandbox.current_process is None
        assert sandbox.guard is None

    def test_stop_closes_a_guard_even_with_no_process(self):
        sandbox = VirtualEnvSandbox("stop-guard-only-test")
        fake_guard = MagicMock()
        sandbox.guard = fake_guard
        sandbox.stop()
        fake_guard.close.assert_called_once()
        assert sandbox.guard is None

    def test_an_already_exited_process_is_not_terminated_again(self):
        sandbox = VirtualEnvSandbox("stop-already-exited-test")
        fake_process = MagicMock()
        fake_process.poll.return_value = 0  # already exited
        sandbox.current_process = fake_process
        sandbox.stop()
        fake_process.terminate.assert_not_called()
        assert sandbox.current_process is None

    def test_a_running_process_is_terminated_and_waited_on(self):
        sandbox = VirtualEnvSandbox("stop-terminate-test")
        fake_process = MagicMock()
        fake_process.poll.return_value = None
        sandbox.current_process = fake_process
        sandbox.stop()
        fake_process.terminate.assert_called_once()
        fake_process.wait.assert_called_once_with(timeout=3)
        fake_process.kill.assert_not_called()
        assert sandbox.current_process is None

    def test_terminate_raising_falls_back_to_kill(self):
        sandbox = VirtualEnvSandbox("stop-terminate-raises-test")
        fake_process = MagicMock()
        fake_process.poll.return_value = None
        fake_process.terminate.side_effect = OSError("access denied")
        sandbox.current_process = fake_process
        sandbox.stop()
        fake_process.kill.assert_called_once()
        assert sandbox.current_process is None

    def test_wait_timing_out_after_terminate_falls_back_to_kill(self):
        sandbox = VirtualEnvSandbox("stop-wait-timeout-test")
        fake_process = MagicMock()
        fake_process.poll.return_value = None
        fake_process.wait.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=3)
        sandbox.current_process = fake_process
        sandbox.stop()
        fake_process.terminate.assert_called_once()
        fake_process.kill.assert_called_once()
        assert sandbox.current_process is None

    def test_a_kill_failure_after_terminate_failure_does_not_raise(self):
        sandbox = VirtualEnvSandbox("stop-kill-also-fails-test")
        fake_process = MagicMock()
        fake_process.poll.return_value = None
        fake_process.terminate.side_effect = OSError("nope")
        fake_process.kill.side_effect = OSError("nope either")
        sandbox.current_process = fake_process
        sandbox.stop()  # must not raise
        assert sandbox.current_process is None

    def test_stop_closes_all_subprocess_streams(self):
        sandbox = VirtualEnvSandbox("stop-closes-streams-test")
        fake_process = MagicMock()
        fake_process.poll.return_value = None
        sandbox.current_process = fake_process

        sandbox.stop()

        fake_process.stdin.close.assert_called_once()
        fake_process.stdout.close.assert_called_once()
        fake_process.stderr.close.assert_called_once()

    def test_the_guard_is_closed_before_the_process_is_terminated(self):
        # Pins the ordering ADR-005 stage 5.2's own comment on stop()
        # requires: on Windows, closing the guard first is what actually
        # kills the whole process tree - closing it after an already-
        # terminated direct child would be too late to catch anything the
        # child itself spawned.
        order = []
        fake_guard = MagicMock()
        fake_guard.close.side_effect = lambda: order.append("guard_closed")
        fake_process = MagicMock()
        fake_process.poll.return_value = None
        fake_process.terminate.side_effect = lambda: order.append("terminated")

        sandbox = VirtualEnvSandbox("stop-order-test")
        sandbox.guard = fake_guard
        sandbox.current_process = fake_process
        sandbox.stop()

        assert order == ["guard_closed", "terminated"]


# -- VirtualEnvSandbox._run_subprocess (real subprocesses) ------------------


class TestRunSubprocessRealHappyPath:
    def test_captures_real_stdout_and_a_zero_return_code(self):
        sandbox = VirtualEnvSandbox("run-subprocess-happy-test")
        output, code = sandbox._run_subprocess(
            [sys.executable, "-c", "print('hello from sandbox')"],
            should_continue=lambda: True,
            timeout_seconds=30,
        )
        assert code == 0
        assert "hello from sandbox" in output
        assert sandbox.current_process is None
        assert sandbox.guard is None

    def test_emit_line_receives_each_line_of_real_output(self):
        sandbox = VirtualEnvSandbox("run-subprocess-emit-test")
        emitted = []
        sandbox._run_subprocess(
            [sys.executable, "-c", "print('line1'); print('line2')"],
            should_continue=lambda: True,
            emit_line=emitted.append,
            timeout_seconds=30,
        )
        joined = "".join(emitted)
        assert "line1" in joined
        assert "line2" in joined

    def test_a_nonzero_exit_is_reported_without_raising(self):
        sandbox = VirtualEnvSandbox("run-subprocess-nonzero-test")
        output, code = sandbox._run_subprocess(
            [sys.executable, "-c", "import sys; print('failing'); sys.exit(3)"],
            should_continue=lambda: True,
            timeout_seconds=30,
        )
        assert code == 3
        assert "failing" in output


class TestRunSubprocessTimeout:
    def test_a_real_timeout_raises_runtimeerror_and_cleans_up(self):
        sandbox = VirtualEnvSandbox("run-subprocess-timeout-test")
        with pytest.raises(RuntimeError, match="timed out after"):
            sandbox._run_subprocess(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                should_continue=lambda: True,
                timeout_seconds=1,
            )
        assert sandbox.current_process is None
        assert sandbox.guard is None


class TestRunSubprocessInterruptedThenReused:
    def test_should_continue_false_raises_interrupted_error_and_cleans_up(self):
        sandbox = VirtualEnvSandbox("run-subprocess-interrupt-test")
        with pytest.raises(InterruptedError, match="stopped"):
            sandbox._run_subprocess(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                should_continue=lambda: False,
                timeout_seconds=30,
            )
        assert sandbox.current_process is None
        assert sandbox.guard is None

    def test_the_same_sandbox_instance_runs_a_fresh_subprocess_cleanly_afterward(self):
        # Cleanup-on-error coverage: an interrupted/failed run must not
        # leave the instance in a state that poisons the NEXT call on the
        # same object - current_process/guard must be genuinely reset, not
        # just appear reset.
        sandbox = VirtualEnvSandbox("run-subprocess-reuse-after-interrupt-test")
        with pytest.raises(InterruptedError):
            sandbox._run_subprocess(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                should_continue=lambda: False,
                timeout_seconds=30,
            )

        output, code = sandbox._run_subprocess(
            [sys.executable, "-c", "print('still works')"],
            should_continue=lambda: True,
            timeout_seconds=30,
        )
        assert code == 0
        assert "still works" in output


class TestReentrancyGapOnASingleSandboxInstance:
    """Documents a genuine gap in the domain code (flagged in the task
    report, not fixed - see this file's PR/task notes): VirtualEnvSandbox
    has no lock guarding self.guard/self.current_process against a second,
    overlapping _run_subprocess call on the SAME instance. Each call
    unconditionally overwrites both attributes at its own start, so an
    earlier in-flight call's own tracking is silently orphaned - its
    eventual `finally` block closes/nulls whatever the SECOND call put
    there, never its own guard/process. In production this is masked by
    VirtualEnvSandbox being constructed fresh per run (see backend/
    agents.py's own AgentDispatcher.__init__ docstring: "Execution Sandbox
    needs NO equivalent manager...constructed fresh per run"), so two
    overlapping runs never share one instance today - but nothing in
    domain.py itself enforces that; it is a caller convention, not a
    guarantee this module provides."""

    def test_a_second_run_subprocess_call_orphans_the_firsts_tracking(self):
        sandbox = VirtualEnvSandbox("reentrancy-gap-test")
        first_guard = MagicMock()
        first_process = MagicMock()
        first_process.poll.return_value = None
        sandbox.guard = first_guard
        sandbox.current_process = first_process

        output, code = sandbox._run_subprocess(
            [sys.executable, "-c", "print('second call')"],
            should_continue=lambda: True,
            timeout_seconds=30,
        )

        assert code == 0
        # Nothing about the second, successful call ever closed/terminated
        # the first call's own guard/process - they were simply overwritten.
        first_guard.close.assert_not_called()
        first_process.terminate.assert_not_called()


# -- VirtualEnvSandbox.execute_code ------------------------------------------


@pytest.fixture
def sandbox_using_system_python(monkeypatch, tmp_path):
    """execute_code() always runs against self.python_executable (the
    venv's own interpreter) - building a real venv for every execute_code
    test would make this whole class as slow as the one genuine end-to-end
    test below. Redirecting the read-only python_executable property to the
    real system interpreter keeps execute_code's OWN logic (script writing,
    stdout/stderr merging, non-raising nonzero-exit contract) under real
    subprocess test coverage without paying for venv creation on every case."""
    monkeypatch.setattr(VirtualEnvSandbox, "python_executable", property(lambda self: Path(sys.executable)))
    sandbox = VirtualEnvSandbox("execute-code-fast-test")
    sandbox.base_dir = tmp_path / "sandbox"
    sandbox.script_path = sandbox.base_dir / "sandbox_entry.py"
    yield sandbox


class TestExecuteCode:
    def test_writes_the_exact_code_to_script_path(self, sandbox_using_system_python):
        sandbox = sandbox_using_system_python
        sandbox.execute_code("print('written and run')", should_continue=lambda: True)
        assert sandbox.script_path.read_text(encoding="utf-8") == "print('written and run')"

    def test_returns_stripped_output_and_the_real_return_code(self, sandbox_using_system_python):
        sandbox = sandbox_using_system_python
        output, code = sandbox.execute_code("print('  padded by print, not by us  ')", should_continue=lambda: True)
        assert code == 0
        assert output == "padded by print, not by us"  # print() adds no extra padding; edges are trimmed by execute_code

    def test_stdout_and_stderr_are_merged_into_one_stream(self, sandbox_using_system_python):
        sandbox = sandbox_using_system_python
        code_str = "import sys; print('to stdout'); print('to stderr', file=sys.stderr)"
        output, return_code = sandbox.execute_code(code_str, should_continue=lambda: True)
        assert return_code == 0
        assert "to stdout" in output
        assert "to stderr" in output

    def test_a_script_that_raises_returns_the_traceback_and_nonzero_code_without_raising(self, sandbox_using_system_python):
        sandbox = sandbox_using_system_python
        output, code = sandbox.execute_code("raise ValueError('boom')", should_continue=lambda: True)
        assert code != 0
        assert "ValueError" in output
        assert "boom" in output

    def test_emit_line_is_invoked_with_the_sandbox_running_banner_and_output(self, sandbox_using_system_python):
        sandbox = sandbox_using_system_python
        emitted = []
        sandbox.execute_code("print('payload')", should_continue=lambda: True, emit_line=emitted.append)
        joined = "".join(emitted)
        assert "Running" in joined
        assert "payload" in joined


# -- VirtualEnvSandbox.ensure_base_environment (mock-based) -----------------


class TestEnsureBaseEnvironment:
    def test_skips_venv_creation_when_the_interpreter_already_exists(self, tmp_path):
        sandbox = VirtualEnvSandbox("ensure-base-skip-test")
        sandbox.base_dir = tmp_path / "sandbox"
        sandbox.venv_dir = sandbox.base_dir / "venv"
        sandbox.python_executable.parent.mkdir(parents=True, exist_ok=True)
        sandbox.python_executable.write_text("", encoding="utf-8")

        with patch.object(sandbox, "_run_subprocess") as fake_run:
            sandbox.ensure_base_environment(should_continue=lambda: True)
            fake_run.assert_not_called()

    def test_a_nonzero_venv_creation_return_code_raises_with_the_captured_output(self, tmp_path):
        sandbox = VirtualEnvSandbox("ensure-base-fail-test")
        sandbox.base_dir = tmp_path / "sandbox"
        sandbox.venv_dir = sandbox.base_dir / "venv"

        with patch.object(sandbox, "_run_subprocess", return_value=("boom: disk full", 1)):
            with pytest.raises(RuntimeError, match="Failed to create sandbox environment"):
                sandbox.ensure_base_environment(should_continue=lambda: True)


# -- VirtualEnvSandbox.sync_requirements (mock-based caching logic) ---------


def _bare_sandbox(tmp_path, sandbox_id):
    sandbox = VirtualEnvSandbox(sandbox_id)
    sandbox.base_dir = tmp_path / "sandbox"
    sandbox.base_dir.mkdir(parents=True, exist_ok=True)
    sandbox.venv_dir = sandbox.base_dir / "venv"
    sandbox.requirements_file = sandbox.base_dir / "requirements.txt"
    sandbox.requirements_hash_file = sandbox.base_dir / ".requirements.sha256"
    return sandbox


class TestSyncRequirementsCaching:
    def test_an_identical_manifest_on_a_second_call_skips_pip_entirely(self, tmp_path):
        sandbox = _bare_sandbox(tmp_path, "sync-cache-hit-test")
        calls = []
        with patch.object(sandbox, "_run_subprocess", side_effect=lambda *a, **k: (calls.append(1), ("", 0))[1]):
            sandbox.sync_requirements("requests==2.31.0", should_continue=lambda: True)
            assert len(calls) == 1

            emitted = []
            sandbox.sync_requirements(
                "requests==2.31.0", should_continue=lambda: True, emit_line=emitted.append
            )
            assert len(calls) == 1, "pip must not run again for an unchanged manifest"
            assert any("Reusing cached environment" in line for line in emitted)

    def test_manifests_that_normalize_identically_are_treated_as_a_cache_hit(self, tmp_path):
        # CRLF line endings + trailing whitespace differ from the original
        # byte-for-byte, but _normalize_requirements makes them equivalent -
        # the hash (and therefore the cache decision) must follow the
        # NORMALIZED text, not the raw manifest string.
        sandbox = _bare_sandbox(tmp_path, "sync-cache-normalize-test")
        calls = []
        with patch.object(sandbox, "_run_subprocess", side_effect=lambda *a, **k: (calls.append(1), ("", 0))[1]):
            sandbox.sync_requirements("requests==2.31.0\n", should_continue=lambda: True)
            assert len(calls) == 1

            sandbox.sync_requirements("requests==2.31.0  \r\n", should_continue=lambda: True)
            assert len(calls) == 1, "trailing-whitespace/CRLF-only differences must still hit the cache"

    def test_a_changed_manifest_invalidates_the_cache_and_runs_pip_again(self, tmp_path):
        sandbox = _bare_sandbox(tmp_path, "sync-cache-invalidate-test")
        calls = []
        with patch.object(sandbox, "_run_subprocess", side_effect=lambda *a, **k: (calls.append(1), ("", 0))[1]):
            sandbox.sync_requirements("requests==2.31.0", should_continue=lambda: True)
            assert len(calls) == 1

            sandbox.sync_requirements("requests==2.32.0", should_continue=lambda: True)
            assert len(calls) == 2, "a genuinely different manifest must re-invoke pip"

    def test_an_empty_manifest_writes_the_hash_without_ever_calling_pip(self, tmp_path):
        sandbox = _bare_sandbox(tmp_path, "sync-cache-empty-test")
        emitted = []
        with patch.object(sandbox, "_run_subprocess") as fake_run:
            sandbox.sync_requirements("   \n  ", should_continue=lambda: True, emit_line=emitted.append)
            fake_run.assert_not_called()
        assert sandbox.requirements_hash_file.exists()
        assert any("No extra dependencies requested" in line for line in emitted)

    def test_a_failed_pip_install_raises_and_does_not_write_the_hash_file(self, tmp_path):
        sandbox = _bare_sandbox(tmp_path, "sync-cache-fail-test")
        with patch.object(sandbox, "_run_subprocess", return_value=("ERROR: no matching distribution", 1)):
            with pytest.raises(RuntimeError, match="Dependency installation failed"):
                sandbox.sync_requirements("nonexistent-package==0.0.0", should_continue=lambda: True)
        assert not sandbox.requirements_hash_file.exists(), (
            "a failed install must not be cached as if it had succeeded - "
            "otherwise the NEXT call with the same manifest would silently "
            "skip pip and report success"
        )


# -- VirtualEnvSandbox real end-to-end coverage ------------------------------


def _make_sandbox_with_real_venv(tmp_path, sandbox_id):
    """Real venv, mirroring test_code_sandbox_only_binary.py's own
    identically-named helper - duplicated here rather than shared, matching
    that file's own precedent of each test module being self-contained."""
    sandbox = VirtualEnvSandbox(sandbox_id)
    sandbox.base_dir = tmp_path / "sandbox"
    sandbox.venv_dir = sandbox.base_dir / "venv"
    sandbox.requirements_file = sandbox.base_dir / "requirements.txt"
    sandbox.requirements_hash_file = sandbox.base_dir / ".requirements.sha256"
    sandbox.script_path = sandbox.base_dir / "sandbox_entry.py"
    sandbox.base_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "venv", str(sandbox.venv_dir)],
        check=True, capture_output=True, text=True, timeout=180,
    )
    return sandbox


class TestRealEndToEndHappyPath:
    def test_construct_a_sandbox_run_simple_python_and_get_real_output(self, tmp_path):
        # The scenario the plugin's users actually depend on: a fresh
        # sandbox, a real (already-provisioned, per ensure_base_environment's
        # own early-return contract) virtualenv, and a real script producing
        # real, observable output through the real interpreter at
        # sandbox.python_executable - not sys.executable, not a mock.
        sandbox = _make_sandbox_with_real_venv(tmp_path, "e2e-happy-path-test")

        sandbox.ensure_base_environment(should_continue=lambda: True)  # early-returns: venv already built above

        output, return_code = sandbox.execute_code(
            "print('the real venv interpreter ran this')",
            should_continue=lambda: True,
        )

        assert return_code == 0
        assert output == "the real venv interpreter ran this"
        assert sandbox.script_path.read_text(encoding="utf-8") == "print('the real venv interpreter ran this')"


class TestSyncRequirementsRealMalformedManifest:
    def test_a_malformed_requirement_line_fails_with_dependency_installation_failed(self, tmp_path):
        # --no-index as an embedded option line keeps this deterministic and
        # network-free (same technique test_code_sandbox_only_binary.py
        # uses) - pip's own requirement-file parser rejects the unmatched
        # bracket before any resolution/network step is ever reached.
        sandbox = _make_sandbox_with_real_venv(tmp_path, "e2e-bad-manifest-test")
        manifest = "--no-index\ntotally-not-a-package[unclosed-extra"

        with pytest.raises(RuntimeError, match="Dependency installation failed"):
            sandbox.sync_requirements(manifest, should_continue=lambda: True)

        # The malformed text is still written to disk verbatim BEFORE pip
        # ever runs (sync_requirements writes the file unconditionally,
        # ahead of the cache check) - worth pinning since it means a
        # subsequent read of requirements.txt reflects the attempted
        # manifest even when the install itself failed.
        assert "totally-not-a-package[unclosed-extra" in sandbox.requirements_file.read_text(encoding="utf-8")
