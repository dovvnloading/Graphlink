"""Ratchet: pyproject.toml's [tool.setuptools] py-modules list must stay in
exact sync with the loose top-level *.py modules at the repo root.

WHY THIS EXISTS. setuptools' auto-discovery only finds real packages
(backend/, graphlink_plugins/ - handled by packages.find); every loose
top-level module has to be named explicitly in py-modules or it is silently
omitted from the built wheel. Nothing in the ordinary local verification
loop catches an omission: `pytest -q` and `npm run check` both import from
the repo checkout, where every top-level file is importable regardless of
whether it is packaged. The break only surfaces when the wheel is built and
imported from somewhere the checkout is not on sys.path.

This has now bitten this project twice. The first time (pre-ADR-015)
graphlink_note_agent and graphlink_process_env were both missing, the
latter silently disabling the subprocess secret-scrubbing env allowlist in
the shipped wheel - which is why .github/workflows/ci.yml grew its
`build-check` job. The second time, ADR-005 stage 5.2 added
graphlink_execution_guard.py without listing it, and the wheel shipped
without the module that both code-execution surfaces import at module
scope; build-check caught it, but only after a push.

The build-check job stays (it validates far more than this list - real wheel
metadata, a real clean-venv install, a real import). This test just moves
THIS specific, entirely-static failure mode left, so it fails in the same
`pytest -q` run that a developer already runs before pushing rather than
minutes later in CI.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _declared_py_modules() -> set[str]:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)
    return set(config["tool"]["setuptools"]["py-modules"])


def _actual_top_level_modules() -> set[str]:
    # Every loose *.py at the repo root is application code that ships.
    # There is deliberately no allowlist of exceptions here: if a genuinely
    # non-shipping top-level module is ever added (a one-off script, say),
    # the right move is to put it in a directory rather than to weaken this
    # invariant - a silently-unpackaged module is exactly the failure this
    # test exists to prevent, and an exception list is where that protection
    # would quietly erode.
    return {path.stem for path in REPO_ROOT.glob("*.py")}


def test_every_top_level_module_is_declared_in_py_modules():
    missing = _actual_top_level_modules() - _declared_py_modules()

    assert not missing, (
        "top-level module(s) exist at the repo root but are NOT listed in "
        f"pyproject.toml's py-modules: {sorted(missing)}. They would be "
        "silently omitted from the built wheel - anything importing them "
        "raises ModuleNotFoundError once installed. Add them to the "
        "py-modules list."
    )


def test_py_modules_lists_no_module_that_no_longer_exists():
    stale = _declared_py_modules() - _actual_top_level_modules()

    assert not stale, (
        "pyproject.toml's py-modules lists module(s) with no matching file "
        f"at the repo root: {sorted(stale)}. A renamed or deleted module "
        "left in this list makes the wheel build fail (or silently ship "
        "nothing for that entry). Remove them."
    )
