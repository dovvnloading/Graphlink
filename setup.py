"""Build-time hook for a self-contained Python desktop wheel.

An installed wheel cannot load web_ui/dist/app or plugins/ directly from
a source checkout. The sdist includes the Vite sources, npm lockfile and
first-party plugin sources; the wheel build compiles and bundles all of them.
Installing a prebuilt wheel never invokes npm.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

ROOT = Path(__file__).resolve().parent
FRONTEND_ROOT = ROOT / "web_ui"
PLUGIN_ROOT = ROOT / "plugins"
ICON = ROOT / "assets" / "graphlink.ico"


class BuildDesktopPackage(build_py):
    """Copy built React SPA and trusted plugin sources into backend/_bundled."""

    def run(self) -> None:
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        if npm is None:
            raise RuntimeError(
                "Building a Graphlink wheel requires Node.js 24 and npm "
                "(web_ui/.nvmrc); install Node or use an existing wheel."
            )
        if not (FRONTEND_ROOT / "package-lock.json").is_file():
            raise RuntimeError("Missing web_ui/package-lock.json in source distribution.")
        subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=FRONTEND_ROOT, check=True)
        subprocess.run([npm, "run", "build"], cwd=FRONTEND_ROOT, check=True)

        spa = FRONTEND_ROOT / "dist" / "app"
        if not (spa / "index.html").is_file() or not (spa / "assets").is_dir():
            raise RuntimeError("Vite did not produce a complete web_ui/dist/app.")
        manifests = sorted(PLUGIN_ROOT.glob("*/plugin.toml"))
        if len(manifests) < 8:
            raise RuntimeError("Graphlink first-party plugin manifest sources are missing.")
        for manifest in manifests:
            if not (manifest.parent / "plugin.py").is_file():
                raise RuntimeError(f"Missing built-in plugin entry for {manifest.parent.name}.")
        if not ICON.is_file():
            raise RuntimeError("assets/graphlink.ico is missing from the source distribution.")

        super().run()
        output = Path(self.build_lib) / "backend" / "_bundled"
        shutil.copytree(spa, output / "spa", dirs_exist_ok=True)
        for manifest in manifests:
            plugin_dir = output / "plugins" / manifest.parent.name
            plugin_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(manifest, plugin_dir / "plugin.toml")
            shutil.copy2(manifest.parent / "plugin.py", plugin_dir / "plugin.py")
        icon_dir = output / "assets"
        icon_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ICON, icon_dir / "graphlink.ico")


setup(cmdclass={"build_py": BuildDesktopPackage})
