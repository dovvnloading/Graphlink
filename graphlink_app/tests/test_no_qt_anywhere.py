"""THE Qt-removal gate (doc/QT_REMOVAL_PLAN.md sections 0 and 3). Permanent.

Burn-down mode (pin > 0): the count of Python files importing PySide6/PyQt -
source AND tests, whole repo - must EXACTLY match qt_burndown.json. Adding a
Qt import anywhere fails this test; removing Qt files fails it too until the
pin is lowered in the same commit, which is what makes the pin the project's
single truthful progress number.

Zero mode (pin == 0): additionally asserts PySide6 is not installed and not
declared in pyproject.toml. This test never gets deleted - it is what makes
"the Qt removal is complete" a machine-checked fact instead of a claim.

The new architecture is held to zero from day one: nothing under backend/,
web_ui/, or the desktop entry point may ever import Qt, pin or no pin.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PIN_PATH = REPO_ROOT / "qt_burndown.json"

_EXCLUDED_DIRS = {".git", "node_modules", "dist", "__pycache__", ".venv", "venv"}
_QT_IMPORT = re.compile(r"^\s*(?:from|import)\s+(?:PySide6|PyQt\d?)\b", re.MULTILINE)

# Files that must be Qt-free FOREVER, regardless of the pin: the replacement
# architecture itself.
_ZERO_TOLERANCE_ROOTS = ("backend", "web_ui")
_ZERO_TOLERANCE_FILES = ("graphlink_desktop.py",)


def _qt_importing_files() -> list[Path]:
    hits: list[Path] = []
    for path in REPO_ROOT.rglob("*.py"):
        if any(part in _EXCLUDED_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _QT_IMPORT.search(text):
            hits.append(path.relative_to(REPO_ROOT))
    return sorted(hits)


def _split(hits: list[Path]) -> tuple[list[Path], list[Path]]:
    tests = [h for h in hits if "tests" in h.parts]
    source = [h for h in hits if "tests" not in h.parts]
    return source, tests


def test_new_architecture_is_qt_free_forever():
    offenders = [
        h
        for h in _qt_importing_files()
        if h.parts[0] in _ZERO_TOLERANCE_ROOTS or str(h) in _ZERO_TOLERANCE_FILES
    ]
    assert offenders == [], (
        f"Qt imports in the NEW architecture (never allowed): {[str(o) for o in offenders]}"
    )


def test_qt_burndown_matches_pin():
    pin = json.loads(PIN_PATH.read_text(encoding="utf-8"))
    hits = _qt_importing_files()
    source, tests = _split(hits)

    assert len(hits) <= pin["total"], (
        f"Qt-file count ROSE: {len(hits)} files import PySide6/PyQt but the pin is "
        f"{pin['total']}. New Qt imports are forbidden - the project goal is ZERO "
        f"(doc/QT_REMOVAL_PLAN.md section 0). New since pin: rerun with -rA and compare."
    )
    assert len(hits) == pin["total"], (
        f"Qt-file count dropped to {len(hits)} but qt_burndown.json still pins "
        f"{pin['total']}. Good progress - now lower the pin in the SAME commit "
        f"(source_files={len(source)}, test_files={len(tests)}, total={len(hits)}) "
        f"and record the drop in the plan ledger."
    )
    assert (len(source), len(tests)) == (pin["source_files"], pin["test_files"]), (
        f"Pin split drifted: actual source={len(source)}/tests={len(tests)}, "
        f"pin says {pin['source_files']}/{pin['test_files']} - update qt_burndown.json."
    )


def test_zero_mode_gate_when_pin_reaches_zero():
    pin = json.loads(PIN_PATH.read_text(encoding="utf-8"))
    if pin["total"] != 0:
        return  # burn-down mode; the assertions above carry the load

    # THE GATE (plan section 0): no import anywhere, package gone, not declared.
    assert _qt_importing_files() == []
    assert importlib.util.find_spec("PySide6") is None, (
        "pin is 0 but PySide6 is still installed - `pip uninstall PySide6` is part "
        "of the R7 cutover"
    )
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "PySide6" not in pyproject and "qtawesome" not in pyproject, (
        "pin is 0 but pyproject.toml still declares Qt dependencies"
    )
