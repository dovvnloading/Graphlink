"""Tests for graphite_paths.py and the assets it resolves.

Regression coverage for hardcoded developer-machine absolute paths that used to be
scattered across graphite_window.py, graphite_ui_components.py, graphite_styles.py,
graphite_ui_dialogs/graphite_settings_dialogs.py, and graphite_widgets/controls.py -
see doc/ARCHITECTURE_REVIEW_FINDINGS.md #65. Also covers check.png/down_arrow.png,
which those hardcoded paths referenced but which never actually existed in the repo.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graphite_paths


class TestAssetPaths:
    def test_assets_dir_is_a_sibling_of_the_package_directory(self):
        assert graphite_paths.ASSETS_DIR == graphite_paths.PACKAGE_DIR.parent / "assets"
        assert graphite_paths.ASSETS_DIR.is_dir()

    def test_asset_path_returns_an_existing_file_for_known_assets(self):
        for filename in ("graphite.ico", "check.png", "down_arrow.png", "File.png"):
            assert graphite_paths.asset_path(filename).is_file(), filename

    def test_asset_url_uses_forward_slashes(self):
        url = graphite_paths.asset_url("down_arrow.png")
        assert "\\" not in url
        assert url.endswith("assets/down_arrow.png")


class TestStylesheetAssetSubstitution:
    def test_dark_theme_stylesheet_has_no_leftover_sentinel(self):
        import graphite_styles

        stylesheet = graphite_styles.THEMES["dark"]["stylesheet"]
        assert "__ASSET_DOWN_ARROW__" not in stylesheet
        assert graphite_paths.asset_url("down_arrow.png") in stylesheet

    def test_stylesheet_asset_reference_tracks_the_computed_assets_dir(self, monkeypatch, tmp_path):
        """The stylesheet must resolve assets through graphite_paths, not a literal
        string - relocating ASSETS_DIR should change the embedded url() accordingly."""
        import importlib

        import graphite_styles

        monkeypatch.setattr(graphite_paths, "ASSETS_DIR", tmp_path)
        importlib.reload(graphite_styles)
        try:
            stylesheet = graphite_styles.THEMES["dark"]["stylesheet"]
            assert tmp_path.as_posix() in stylesheet
        finally:
            importlib.reload(graphite_paths)
            importlib.reload(graphite_styles)
