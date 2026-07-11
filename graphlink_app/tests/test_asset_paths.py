"""Tests for graphlink_paths.py and the assets it resolves.

Regression coverage for hardcoded developer-machine absolute paths that used to be
scattered across graphlink_window.py, graphlink_ui_components.py, graphlink_styles.py,
graphlink_ui_dialogs/graphlink_settings_dialogs.py, and graphlink_widgets/controls.py -
see doc/ARCHITECTURE_REVIEW_FINDINGS.md #65. Also covers check.png/down_arrow.png,
which those hardcoded paths referenced but which never actually existed in the repo.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graphlink_paths


class TestAssetPaths:
    def test_assets_dir_is_a_sibling_of_the_package_directory(self):
        assert graphlink_paths.ASSETS_DIR == graphlink_paths.PACKAGE_DIR.parent / "assets"
        assert graphlink_paths.ASSETS_DIR.is_dir()

    def test_asset_path_returns_an_existing_file_for_known_assets(self):
        for filename in ("graphlink.ico", "check.png", "down_arrow.png", "File.png"):
            assert graphlink_paths.asset_path(filename).is_file(), filename

    def test_asset_url_uses_forward_slashes(self):
        url = graphlink_paths.asset_url("down_arrow.png")
        assert "\\" not in url
        assert url.endswith("assets/down_arrow.png")


class TestStylesheetAssetSubstitution:
    def test_dark_theme_stylesheet_has_no_leftover_sentinel(self):
        import graphlink_styles

        stylesheet = graphlink_styles.THEMES["dark"]["stylesheet"]
        assert "__ASSET_DOWN_ARROW__" not in stylesheet
        assert graphlink_paths.asset_url("down_arrow.png") in stylesheet

    def test_stylesheet_asset_reference_tracks_the_computed_assets_dir(self, monkeypatch, tmp_path):
        """The stylesheet must resolve assets through graphlink_paths, not a literal
        string - relocating ASSETS_DIR should change the embedded url() accordingly."""
        import importlib

        import graphlink_styles

        monkeypatch.setattr(graphlink_paths, "ASSETS_DIR", tmp_path)
        importlib.reload(graphlink_styles)
        try:
            stylesheet = graphlink_styles.THEMES["dark"]["stylesheet"]
            assert tmp_path.as_posix() in stylesheet
        finally:
            importlib.reload(graphlink_paths)
            importlib.reload(graphlink_styles)
