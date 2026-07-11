"""Shared filesystem path helpers.

Keeps asset lookups independent of where the repo happens to be checked out -
several call sites previously hardcoded an absolute path to one developer's machine.
"""

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
ASSETS_DIR = REPO_ROOT / "assets"


def asset_path(filename: str) -> Path:
    """Return the absolute Path to a file inside the repo's assets/ directory."""
    return ASSETS_DIR / filename


def asset_url(filename: str) -> str:
    """Return a forward-slash path to an asset, suitable for a Qt stylesheet url()."""
    return asset_path(filename).as_posix()
