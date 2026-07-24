"""Tests for graphlink_plugins/code_sandbox/domain.py, the Qt-free half of
the Execution Sandbox plugin split out of graphlink_agents_code_sandbox.py
(Qt-removal plan R5.4).

Mirrors graphlink_app/tests/test_gitlink_agent.py's own shape: a direct
source-scan assertion that the module has zero Qt dependencies, plus a fresh-
subprocess import check.
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphlink_plugins.code_sandbox.domain import (
    SandboxGenerationAgent,
    SandboxRepairAgent,
    SandboxStage,
    VirtualEnvSandbox,
    _extract_python_block,
    _normalize_requirements,
    _subprocess_kwargs,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_module_has_no_qt_dependency():
    import graphlink_plugins.code_sandbox.domain as domain_module

    source = Path(domain_module.__file__).read_text(encoding="utf-8")
    for banned in ("PySide6", "qtawesome", "QGraphics", "QWidget", "QApplication"):
        assert banned not in source, f"{banned} leaked into the supposedly Qt-free code_sandbox domain module"


def test_module_imports_qt_free_in_a_fresh_process_and_resolves_the_qt_free_config():
    code = (
        "import sys\n"
        "import graphlink_plugins.code_sandbox.domain as domain_module\n"
        "qt = [m for m in sys.modules if m.startswith('PySide6')]\n"
        "assert not qt, f'importing graphlink_plugins.code_sandbox.domain pulled Qt: {qt}'\n"
        "assert domain_module.config.__name__ == 'graphlink_task_config', (\n"
        "    f'domain.py config reference resolved to {domain_module.config.__name__!r}, '\n"
        "    'expected graphlink_task_config'\n"
        ")\n"
        "print('graphlink_plugins.code_sandbox.domain imported qt-free')\n"
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
        "importing graphlink_plugins.code_sandbox.domain in a fresh process failed or pulled Qt:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


class TestNormalizeRequirements:
    def test_normalizes_crlf_and_strips_trailing_whitespace(self):
        assert _normalize_requirements("numpy \r\nrequests \r\n") == "numpy\nrequests"

    def test_empty_input_stays_empty(self):
        assert _normalize_requirements("") == ""


class TestExtractPythonBlock:
    def test_extracts_tool_tagged_block(self):
        assert _extract_python_block("[TOOL:PYTHON]\nprint(1)\n[/TOOL]") == "print(1)"

    def test_extracts_fenced_block_when_no_tool_tags(self):
        assert _extract_python_block("```python\nprint(1)\n```") == "print(1)"

    def test_returns_none_when_neither_shape_present(self):
        assert _extract_python_block("just a direct answer, no code") is None


class TestSubprocessKwargs:
    def test_returns_a_dict(self):
        assert isinstance(_subprocess_kwargs(), dict)


class TestSandboxGenerationAgent:
    def test_get_response_calls_api_provider_chat(self):
        fake_response = {"message": {"content": "[TOOL:PYTHON]\nprint(1)\n[/TOOL]"}}
        with patch("graphlink_plugins.code_sandbox.domain.api_provider.chat", return_value=fake_response) as mock_chat:
            result = SandboxGenerationAgent().get_response([], "print 1", "")
        assert result == fake_response["message"]["content"]
        assert mock_chat.called


class TestSandboxRepairAgent:
    def test_get_response_extracts_fenced_python_block(self):
        fake_response = {"message": {"content": "```python\nprint('fixed')\n```"}}
        with patch("graphlink_plugins.code_sandbox.domain.api_provider.chat", return_value=fake_response):
            result = SandboxRepairAgent().get_response("print(", "SyntaxError", "", original_prompt="do x")
        assert result == "print('fixed')"

    def test_get_response_falls_back_to_raw_text_when_unfenced(self):
        fake_response = {"message": {"content": "print('fixed')"}}
        with patch("graphlink_plugins.code_sandbox.domain.api_provider.chat", return_value=fake_response):
            result = SandboxRepairAgent().get_response("print(", "SyntaxError", "")
        assert result == "print('fixed')"


class TestVirtualEnvSandbox:
    def test_sandbox_id_is_sanitized_into_the_base_dir_name(self, tmp_path, monkeypatch):
        import tempfile

        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        sandbox = VirtualEnvSandbox("weird id/with*chars")
        assert sandbox.base_dir.name == "weird_id_with_chars"

    def test_blank_sandbox_id_falls_back_to_default(self, tmp_path, monkeypatch):
        import tempfile

        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        sandbox = VirtualEnvSandbox("")
        assert sandbox.base_dir.name == "default"

    def test_execute_code_runs_the_given_script_in_the_venv_python(self, tmp_path, monkeypatch):
        # Uses the REAL host Python as a stand-in "venv" python (avoids the
        # slow real venv-creation path) - proves execute_code writes the
        # script and runs it end to end.
        sandbox = VirtualEnvSandbox("test-sandbox")
        sandbox.base_dir = tmp_path
        sandbox.script_path = tmp_path / "sandbox_entry.py"
        monkeypatch.setattr(
            type(sandbox), "python_executable", property(lambda self: Path(sys.executable))
        )
        output, return_code = sandbox.execute_code("print('sandboxed output')", lambda: True)
        assert return_code == 0
        assert "sandboxed output" in output

    def test_stop_is_safe_when_no_process_is_running(self):
        sandbox = VirtualEnvSandbox("idle-sandbox")
        sandbox.stop()  # must not raise


def test_sandbox_stage_enum_has_the_expected_members():
    assert {member.name for member in SandboxStage} == {
        "GENERATE", "PREPARE", "INSTALL", "EXECUTE", "ANALYZE",
    }
