"""Shipped code must not enforce an invariant with `assert`.

`python -O` and `PYTHONOPTIMIZE=1` strip assert statements entirely. Anything
an assert was guarding is then simply not guarded - silently, with no error
and no log. That is fine for a test, where the whole process runs under
pytest, and wrong for shipped code.

The seven that existed when this gate was written were not stylistic. Two of
them were the event bus's duplicate-registration guards:

    assert name not in self._topics,  "topic registered twice"
    assert key  not in self._intents, "intent registered twice"

backend/app.py's _configure_session registers 12 topics and roughly 90
intents in an order its own comments describe as load-bearing. With those
asserts stripped, a duplicate registration silently REPLACES the previous
handler - the exact failure the checks exist to make loud, made silent by an
interpreter flag. Two more guarded a subprocess pipe against None, where the
alternative to the assert is an AttributeError raised deep inside a reader
thread with no hint that the process was never connected.

Nothing in the repo runs under -O today. That is the point: this is cheap to
keep true and expensive to discover is not.

AST-based rather than grep, same as tests/test_domain_purity.py and
test_node_state_migration.py - a grep for "assert " also matches the word
inside docstrings and comments, and backend/events.py has one of those.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# The four shipped packages (pyproject's [tool.setuptools.packages.find])
# plus the loose root modules that ship with them.
SHIPPED_TREES = ("backend", "graphlink_plugins", "provider_runtime", "settings_store")


def _shipped_modules() -> list[Path]:
    modules: list[Path] = []
    for tree in SHIPPED_TREES:
        for path in (REPO_ROOT / tree).rglob("*.py"):
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            modules.append(path)
    modules.extend(
        path for path in REPO_ROOT.glob("*.py")
        if not path.name.startswith("test_")
    )
    return modules


def _asserts_in(path: Path) -> list[int]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover - a broken module fails elsewhere
        return []
    return [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Assert)]


def test_no_shipped_module_enforces_an_invariant_with_assert():
    offenders = [
        f"{path.relative_to(REPO_ROOT).as_posix()}:{line}"
        for path in _shipped_modules()
        for line in _asserts_in(path)
    ]
    assert not offenders, (
        "assert statements in shipped code - `python -O` strips these, taking "
        "the invariant with them. Raise instead:\n  " + "\n  ".join(sorted(offenders))
    )


def test_the_scan_actually_reaches_the_shipped_code():
    """Guards the guard: a wrong root or a changed layout would make the check
    above pass over an empty file list."""
    modules = _shipped_modules()
    assert len(modules) > 100, len(modules)
    names = {path.name for path in modules}
    for expected in ("events.py", "mcp_client.py", "plugin_sdk.py", "asset_store.py"):
        assert expected in names, expected
