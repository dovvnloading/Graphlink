"""Tests for graphlink_plugins/pycoder/domain.py, the Qt-free half of the
Py-Coder plugin split out of graphlink_agents_pycoder.py (Qt-removal plan
R5.4).

Mirrors graphlink_app/tests/test_gitlink_agent.py's own shape: a direct
source-scan assertion that the module has zero Qt dependencies, plus a fresh-
subprocess import check (a same-process check is unreliable once anything
else in the same pytest run has already imported Qt).
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphlink_plugins.pycoder.domain import (
    PyCoderAnalysisAgent,
    PyCoderExecutionAgent,
    PyCoderRepairAgent,
    PyCoderReplManager,
    PyCoderStage,
    PyCoderStatus,
    PythonREPL,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_module_has_no_qt_dependency():
    import graphlink_plugins.pycoder.domain as domain_module

    source = Path(domain_module.__file__).read_text(encoding="utf-8")
    for banned in ("PySide6", "qtawesome", "QGraphics", "QWidget", "QApplication"):
        assert banned not in source, f"{banned} leaked into the supposedly Qt-free pycoder domain module"


def test_module_imports_qt_free_in_a_fresh_process_and_resolves_the_qt_free_config():
    # Mirrors backend/tests/test_agent_layer_qt_free.py's own
    # _assert_import_is_qt_free mechanism - the real precedent for "does this
    # import chain stay Qt-free" in this codebase - plus confirms the
    # `graphlink_config` -> `graphlink_task_config` swap this split made.
    code = (
        "import sys\n"
        "import graphlink_plugins.pycoder.domain as domain_module\n"
        "qt = [m for m in sys.modules if m.startswith('PySide6')]\n"
        "assert not qt, f'importing graphlink_plugins.pycoder.domain pulled Qt: {qt}'\n"
        "assert domain_module.config.__name__ == 'graphlink_task_config', (\n"
        "    f'domain.py config reference resolved to {domain_module.config.__name__!r}, '\n"
        "    'expected graphlink_task_config'\n"
        ")\n"
        "print('graphlink_plugins.pycoder.domain imported qt-free')\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "graphlink_app") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert result.returncode == 0, (
        "importing graphlink_plugins.pycoder.domain in a fresh process failed or pulled Qt:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


class TestPythonREPL:
    def test_execute_runs_code_and_reports_success(self):
        repl = PythonREPL()
        try:
            output = repl.execute("print('hello from repl')")
            assert "hello from repl" in output
            assert repl.last_run_failed is False
        finally:
            repl.stop()

    def test_execute_reports_failure_on_a_raised_exception(self):
        repl = PythonREPL()
        try:
            output = repl.execute("raise ValueError('boom')")
            assert repl.last_run_failed is True
            assert "ValueError" in output
        finally:
            repl.stop()

    def test_state_persists_between_execute_calls(self):
        repl = PythonREPL()
        try:
            repl.execute("x = 41")
            output = repl.execute("print(x + 1)")
            assert "42" in output
            assert repl.last_run_failed is False
        finally:
            repl.stop()

    def test_stop_is_idempotent_and_safe_before_start(self):
        repl = PythonREPL()
        repl.stop()  # never started - must not raise
        repl.stop()  # already stopped - must not raise


class _FakeNode:
    """A minimal weakly-referenceable stand-in for a live QGraphicsObject
    node - plain `object()` instances cannot be weakly referenced, but
    PyCoderReplManager's WeakKeyDictionary keying requires it, same as a
    real node would support."""


class TestPyCoderReplManager:
    def test_get_repl_returns_the_same_instance_for_the_same_node(self):
        manager = PyCoderReplManager()
        node = _FakeNode()
        try:
            first = manager.get_repl(node)
            second = manager.get_repl(node)
            assert first is second
        finally:
            manager.stop(node)

    def test_get_repl_returns_different_instances_for_different_nodes(self):
        manager = PyCoderReplManager()
        node_a, node_b = _FakeNode(), _FakeNode()
        try:
            assert manager.get_repl(node_a) is not manager.get_repl(node_b)
        finally:
            manager.stop(node_a)
            manager.stop(node_b)

    def test_stop_removes_the_repl_so_a_later_get_repl_creates_a_fresh_one(self):
        manager = PyCoderReplManager()
        node = _FakeNode()
        first = manager.get_repl(node)
        manager.stop(node)
        second = manager.get_repl(node)
        try:
            assert first is not second
        finally:
            manager.stop(node)


class TestPyCoderExecutionAgent:
    def test_get_response_calls_api_provider_chat_and_returns_content(self):
        fake_response = {"message": {"content": "[TOOL:PYTHON]\nprint(1)\n[/TOOL]"}}
        with patch("graphlink_plugins.pycoder.domain.api_provider.chat", return_value=fake_response) as mock_chat:
            result = PyCoderExecutionAgent().get_response([], "add 1")
        assert result == fake_response["message"]["content"]
        assert mock_chat.called


class TestPyCoderRepairAgent:
    def test_get_response_extracts_fenced_python_block(self):
        fake_response = {"message": {"content": "```python\nprint('fixed')\n```"}}
        with patch("graphlink_plugins.pycoder.domain.api_provider.chat", return_value=fake_response):
            result = PyCoderRepairAgent().get_response("print(", "SyntaxError")
        assert result == "print('fixed')"

    def test_get_response_falls_back_to_raw_text_when_unfenced(self):
        fake_response = {"message": {"content": "print('fixed')"}}
        with patch("graphlink_plugins.pycoder.domain.api_provider.chat", return_value=fake_response):
            result = PyCoderRepairAgent().get_response("print(", "SyntaxError")
        assert result == "print('fixed')"


class TestPyCoderAnalysisAgent:
    def test_get_response_with_original_prompt_calls_api_provider_chat(self):
        fake_response = {"message": {"content": "Analysis text"}}
        with patch("graphlink_plugins.pycoder.domain.api_provider.chat", return_value=fake_response) as mock_chat:
            result = PyCoderAnalysisAgent().get_response("what is 1+1", "print(2)", "2")
        assert result == "Analysis text"
        assert mock_chat.called

    def test_get_response_without_original_prompt_still_calls_api_provider_chat(self):
        fake_response = {"message": {"content": "Analysis text"}}
        with patch("graphlink_plugins.pycoder.domain.api_provider.chat", return_value=fake_response):
            result = PyCoderAnalysisAgent().get_response(None, "print(2)", "2")
        assert result == "Analysis text"


def test_pycoder_stage_and_status_enums_have_the_expected_members():
    assert {member.name for member in PyCoderStage} == {
        "ANALYZE", "GENERATE", "EXECUTE", "REPAIR", "ANALYZE_RESULT",
    }
    assert {member.name for member in PyCoderStatus} == {
        "PENDING", "RUNNING", "SUCCESS", "FAILURE",
    }
