"""Shared filesystem path helpers.

Keeps asset lookups independent of where the repo happens to be checked out -
several call sites previously hardcoded an absolute path to one developer's machine.

Also independent of whether the app is running from a source checkout or a PyInstaller
freeze: __file__-based resolution (assets/ as a sibling of graphlink_app/) only holds in a
checkout. A frozen onedir/onefile build extracts bundled `datas` under sys._MEIPASS
instead, so ASSETS_DIR branches on sys.frozen.
"""

import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent

if getattr(sys, "frozen", False):
    _FROZEN_BASE_DIR = Path(getattr(sys, "_MEIPASS", None) or Path(sys.executable).resolve().parent)
    ASSETS_DIR = _FROZEN_BASE_DIR / "assets"
else:
    ASSETS_DIR = REPO_ROOT / "assets"


def asset_path(filename: str) -> Path:
    """Return the absolute Path to a file inside the repo's assets/ directory."""
    return ASSETS_DIR / filename


def asset_url(filename: str) -> str:
    """Return a forward-slash path to an asset, suitable for a Qt stylesheet url()."""
    return asset_path(filename).as_posix()
