"""R4.1's load-bearing guarantee: the agent layer's imports are Qt-free.

The whole point of the graphlink_task_config split is that backend/ can
import api_provider (and through it the task/provider/model config) without
PySide6 ever loading. A same-process assertion would be unreliable - some
earlier test may already have imported Qt - so each check runs a fresh
python subprocess and asserts PySide6 never entered sys.modules.
"""

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
    # R7.2: these modules now sit at REPO_ROOT itself (a sibling of
    # backend/), not inside graphlink_app/ - `python -c` sets sys.path[0] to
    # cwd, and the subprocess already runs with cwd=REPO_ROOT below, so no
    # PYTHONPATH injection is needed any more.
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
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


def test_chart_agent_imports_qt_free():
    # R6.2 prerequisite: graphlink_agents_tools.py (home of ChartDataAgent
    # before this split) has its own unconditional `from PySide6.QtCore
    # import QThread, Signal` at module level, needed only by its
    # ChartWorkerThread/ImageGenerationWorkerThread/ModelPullWorkerThread
    # classes - importing anything from it, including the Qt-free
    # ChartDataAgent, pulled Qt in regardless. This is the machine-checked
    # fact that the real chart-generation path backend/ needs (backend/
    # agents.py's generateChart dispatch) no longer does.
    _assert_import_is_qt_free("graphlink_chart_agent")


def test_chart_rendering_imports_qt_free():
    # R6.2: graphlink_chart_rendering.py ports the legacy ChartItem's
    # Matplotlib rendering (already Qt-free upstream: matplotlib.use("Agg")
    # + FigureCanvasAgg) into a standalone module that returns raw PNG bytes
    # instead of wrapping them in a QImage - the one Qt touch point the
    # legacy item had. This is the machine-checked fact that swap is real.
    _assert_import_is_qt_free("graphlink_chart_rendering")


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
