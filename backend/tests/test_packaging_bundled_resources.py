"""A reused wheel build directory must only contain current frontend chunks and plugins."""

import runpy
from pathlib import Path
from unittest.mock import patch

from setuptools.command.build_py import build_py
from setuptools.dist import Distribution


def test_rebuild_removes_obsolete_spa_chunks_and_manifests(tmp_path, monkeypatch):
    # Load the project's real build command without invoking the packaging CLI.
    with patch("setuptools.setup"):
        build_command = runpy.run_path(str(Path(__file__).resolve().parents[2] / "setup.py"))["BuildDesktopPackage"]
    frontend, plugins = tmp_path / "web_ui", tmp_path / "plugins"
    assets = frontend / "dist" / "app" / "assets"
    assets.mkdir(parents=True)
    (assets.parent / "index.html").write_text("workbench", encoding="utf-8")
    (frontend / "package-lock.json").write_text("{}", encoding="utf-8")
    icon = tmp_path / "app.ico"
    icon.write_bytes(b"icon")
    for i in range(9):
        directory = plugins / f"pack_{i}"
        directory.mkdir(parents=True)
        (directory / "plugin.toml").write_text(f"id = 'pack_{i}'", encoding="utf-8")
        (directory / "plugin.py").write_text("def register(host): pass", encoding="utf-8")

    config = build_command.run.__globals__
    for name, value in {"FRONTEND_ROOT": frontend, "PLUGIN_ROOT": plugins, "ICON": icon}.items():
        monkeypatch.setitem(config, name, value)
    monkeypatch.setattr(config["shutil"], "which", lambda _: "npm")
    monkeypatch.setattr(config["subprocess"], "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(build_py, "run", lambda _self: None)
    output_dir = tmp_path / "build" / "lib"
    cmd = build_command(Distribution())
    cmd.build_lib = str(output_dir)
    (assets / "science-old.js").write_text("first", encoding="utf-8")
    cmd.run()
    installed = output_dir / "backend" / "_bundled"
    assert (installed / "plugins/pack_8/plugin.toml").is_file()
    assert (installed / "spa/assets/science-old.js").is_file()

    (assets / "science-old.js").unlink()
    (assets / "science-new.js").write_text("second", encoding="utf-8")
    (plugins / "pack_8/plugin.toml").unlink()
    cmd.run()

    assert set(p.name for p in (installed / "spa/assets").iterdir()) == {"science-new.js"}
    assert set(p.name for p in (installed / "plugins").iterdir()) == {f"pack_{i}" for i in range(8)}
