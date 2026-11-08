"""Resolve bundled desktop resources without relying on the current cwd.

Source checkouts keep their established web_ui/dist/app and plugins/
directories. Built wheels place a copy under backend/_bundled/ through
the setuptools build_py hook.
"""

from __future__ import annotations

from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _BACKEND_DIR.parent
_BUNDLE_ROOT = _BACKEND_DIR / "_bundled"


def resolve_spa_dir() -> Path:
    packaged = _BUNDLE_ROOT / "spa"
    if (packaged / "index.html").is_file():
        return packaged
    return _PROJECT_ROOT / "web_ui" / "dist" / "app"


def resolve_plugins_dir() -> Path:
    packaged = _BUNDLE_ROOT / "plugins"
    if (packaged / "system_prompt" / "plugin.toml").is_file():
        return packaged
    return _PROJECT_ROOT / "plugins"


def resolve_app_icon() -> Path:
    packaged = _BUNDLE_ROOT / "assets" / "graphlink.ico"
    if packaged.is_file():
        return packaged
    return _PROJECT_ROOT / "assets" / "graphlink.ico"
