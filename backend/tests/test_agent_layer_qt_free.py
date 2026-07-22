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
