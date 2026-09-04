"""The coverage floor has to measure everything that ships.

[tool.coverage.run].source used to be `["backend", "graphlink_plugins"]`, while
[tool.setuptools] ships FOUR packages and 23 loose root modules. Two shipped
packages - provider_runtime and settings_store - and every root module,
including api_provider.py (the code that talks to every model endpoint), sat
outside the 85% floor entirely. A file nothing measures can regress to zero
and the gate stays green.

Both lists are hand-maintained in the same file, which is the shape this
codebase keeps getting bitten by: a set asserted by hand, the other set
growing, and nothing failing. So they are compared here instead.

Deliberately compares against the SHIPPING manifest rather than against
"every .py in the repo": tools/, mutation_tests/ and the test suites are not
shipped and have no business inside a production coverage floor.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"

# Measured but not shipped: contracts/ is build-time codegen (it generates the
# TS types the SPA imports), and it has real tests, so it belongs in the floor
# even though no wheel carries it.
_MEASURED_BUT_NOT_SHIPPED = {"contracts"}


def _config() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_every_shipped_package_is_inside_the_coverage_floor():
    config = _config()
    source = set(config["tool"]["coverage"]["run"]["source"])
    # "backend*" -> "backend"
    shipped = {
        entry.rstrip("*")
        for entry in config["tool"]["setuptools"]["packages"]["find"]["include"]
    }
    missing = sorted(shipped - source)
    assert not missing, (
        f"shipped packages outside [tool.coverage.run].source: {missing}. "
        "A package nothing measures can regress to zero with the gate still green."
    )


def test_every_shipped_root_module_is_inside_the_coverage_floor():
    config = _config()
    source = set(config["tool"]["coverage"]["run"]["source"])
    shipped = set(config["tool"]["setuptools"]["py-modules"])
    missing = sorted(shipped - source)
    assert not missing, (
        f"shipped root modules outside [tool.coverage.run].source: {missing}"
    )


def test_the_coverage_source_lists_nothing_that_does_not_exist():
    """The other direction: a renamed or deleted module left in the list
    silently measures nothing, which reads as coverage it does not have.

    Accepts both forms because coverage's `source` names a package/directory
    OR a top-level module: `backend` is a directory, `api_provider` resolves
    to api_provider.py."""
    config = _config()
    stale = [
        entry for entry in config["tool"]["coverage"]["run"]["source"]
        if not (REPO_ROOT / entry).exists() and not (REPO_ROOT / f"{entry}.py").exists()
    ]
    assert not stale, f"[tool.coverage.run].source names things that do not exist: {stale}"


def test_the_source_list_adds_nothing_beyond_what_ships():
    """Guards against quietly padding the floor with well-covered code that
    is not part of the product."""
    config = _config()
    source = set(config["tool"]["coverage"]["run"]["source"])
    shipped = {
        entry.rstrip("*")
        for entry in config["tool"]["setuptools"]["packages"]["find"]["include"]
    }
    shipped |= set(config["tool"]["setuptools"]["py-modules"])
    shipped |= _MEASURED_BUT_NOT_SHIPPED
    unexpected = sorted(source - shipped)
    assert not unexpected, (
        f"[tool.coverage.run].source measures things that do not ship: {unexpected}. "
        "If that is deliberate, add it to _MEASURED_BUT_NOT_SHIPPED with a reason."
    )
