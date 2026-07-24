"""R4.1's load-bearing guarantee: the agent layer's imports are Qt-free.

The whole point of the graphlink_task_config split is that backend/ can
import api_provider (and through it the task/provider/model config) without
PySide6 ever loading. A same-process assertion would be unreliable - some
earlier test may already have imported Qt - so each check runs a fresh
python subprocess and asserts PySide6 never entered sys.modules.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _assert_import_is_qt_free(module_name: str) -> None:
    code = (
        "import sys\n"
        f"import {module_name}\n"
        "qt = [m for m in sys.modules if m.startswith('PySide6')]\n"
        "assert not qt, f'importing {module_name} pulled Qt: {{qt}}'\n"
        f"print('{module_name} imported qt-free')\n"
    )
    # The legacy modules live in graphlink_app/ and are importable as
    # top-level names (pyproject py-modules); a fresh subprocess needs that
    # directory on its path the same way conftest arranges it in-process.
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
        f"importing {module_name} in a fresh process failed or pulled Qt:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_task_config_imports_qt_free():
    _assert_import_is_qt_free("graphlink_task_config")


def test_api_provider_imports_qt_free():
    # THE R4 unblock: before the split, api_provider's
    # `import graphlink_config` chained to PySide6.QtGui/QtWidgets, making
    # any backend chat dispatch a Qt process. This is the machine-checked
    # fact that that chain is severed.
    _assert_import_is_qt_free("api_provider")


def test_chat_agent_imports_qt_free():
    # R4.2 prerequisite: graphlink_agents_core.py (home of ChatWorker/
    # ChatAgent/resolve_branch_system_prompt before this split) has its own
    # unconditional `from PySide6.QtCore import ...` at module level, needed
    # only by its *WorkerThread classes - importing anything from it,
    # including these three Qt-free symbols, pulled Qt in regardless. This
    # is the machine-checked fact that the real chat-agent path backend/
    # needs no longer does.
    _assert_import_is_qt_free("graphlink_chat_agent")


def test_artifact_agent_imports_qt_free():
    # R5.2 prerequisite: graphlink_agents_artifact.py (home of ArtifactAgent
    # before this split) has its own unconditional `from PySide6.QtCore
    # import QThread, Signal` at module level, needed only by its
    # ArtifactWorkerThread class - importing anything from it, including the
    # Qt-free ArtifactAgent, pulled Qt in regardless. This is the
    # machine-checked fact that the real artifact-agent path backend/ needs
    # no longer does.
    _assert_import_is_qt_free("graphlink_artifact_agent")


def test_pycoder_domain_imports_qt_free():
    # R5.4 prerequisite: graphlink_agents_pycoder.py (home of PythonREPL/
    # PyCoderReplManager/PyCoderExecutionAgent/PyCoderRepairAgent/
    # PyCoderAnalysisAgent before this split) has its own unconditional
    # `from PySide6.QtCore import QThread, Signal` at module level, needed
    # only by its CodeExecutionWorker/PyCoderExecutionWorker/
    # PyCoderAgentWorker classes - importing anything from it, including
    # these Qt-free symbols, pulled Qt in regardless. This is the
    # machine-checked fact that the real Py-Coder dispatch path backend/
    # needs (backend/agents.py's start_pycoder_run) no longer does.
    _assert_import_is_qt_free("graphlink_plugins.pycoder.domain")


def test_code_sandbox_domain_imports_qt_free():
    # R5.4 prerequisite: graphlink_agents_code_sandbox.py (home of
    # SandboxGenerationAgent/SandboxRepairAgent/VirtualEnvSandbox before this
    # split) has its own unconditional `from PySide6.QtCore import QThread,
    # Signal` at module level, needed only by its CodeSandboxExecutionWorker
    # class - importing anything from it, including these Qt-free symbols,
    # pulled Qt in regardless. This is the machine-checked fact that the
    # real Execution Sandbox dispatch path backend/ needs (backend/agents.py's
    # start_code_sandbox_run) no longer does.
    _assert_import_is_qt_free("graphlink_plugins.code_sandbox.domain")
