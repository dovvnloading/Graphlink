"""Coverage for _inline_bundle()'s single-chunk-build assumption.

_inline_bundle() regex-replaces exactly one <link>/<script src> tag pair with
the real file's content, and its own comment always said so ("Vite emits one
stylesheet and one module") without anything actually checking it. If Vite's
build for an island ever code-splits - a second explicit chunk referenced by
its own tag, or a dynamically-imported chunk with no tag at all - the old
behavior was to silently proceed: either flatten a real import()/export
relationship between two files into two disconnected inline <script> blocks
(breaking at runtime the moment one references the other), or leave an
unreferenced chunk on disk that a dynamic import() would try to fetch from a
page that has no server to fetch it from. Both failure modes are runtime
errors in a shipped build with nothing pointing back at "the build shape
changed" - this file proves the fail-loud replacement instead.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from graphlink_web_island_host import (
    MultiChunkBuildError,
    _assert_single_chunk_build,
    _inline_bundle,
)

_REAL_COMPOSER_ASSETS = Path(__file__).resolve().parents[2] / "assets" / "composer"


def _write_index_html(root: Path, *, css_names: list[str], js_names: list[str]) -> None:
    css_links = "".join(f'<link rel="stylesheet" href="./assets/{n}">' for n in css_names)
    scripts = "".join(f'<script type="module" src="./assets/{n}"></script>' for n in js_names)
    root.joinpath("index.html").write_text(
        f"<!doctype html><html><head>{css_links}</head><body>{scripts}</body></html>",
        encoding="utf-8",
    )


def _write_asset(root: Path, name: str, content: str = "/* stub */") -> None:
    assets_dir = root / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.joinpath(name).write_text(content, encoding="utf-8")


class TestRealCurrentBuildIsSingleChunk:
    """Ground truth: today's real composer build actually satisfies the
    assumption _inline_bundle() has always silently relied on. If this fails,
    Vite's output shape changed - a real, actionable signal, not a fixture
    problem."""

    def test_assets_dir_exists(self):
        assert (_REAL_COMPOSER_ASSETS / "assets").is_dir(), (
            f"{_REAL_COMPOSER_ASSETS} has no built assets/ dir - run "
            "`npm run build` in web_ui/ (with GRAPHLINK_ISLAND=composer) first"
        )

    def test_exactly_one_css_and_one_js_chunk(self):
        assets_dir = _REAL_COMPOSER_ASSETS / "assets"
        css_files = list(assets_dir.glob("*.css"))
        js_files = list(assets_dir.glob("*.js"))

        assert len(css_files) == 1, f"expected exactly 1 CSS chunk, found {css_files}"
        assert len(js_files) == 1, f"expected exactly 1 JS chunk, found {js_files}"

    def test_the_guard_itself_passes_against_the_real_build(self):
        _assert_single_chunk_build(_REAL_COMPOSER_ASSETS)  # must not raise

    def test_inline_bundle_still_produces_real_output(self):
        document = _inline_bundle(_REAL_COMPOSER_ASSETS)

        assert "<style>" in document
        assert '<script type="module">' in document


class TestGuardFiresOnAMultiChunkBuild:
    """The actual regression guard: constructed asset directories proving
    _assert_single_chunk_build() (and therefore _inline_bundle()) fails
    loudly rather than silently inlining or silently dropping extra chunks."""

    def test_passes_for_a_genuinely_single_chunk_build(self, tmp_path):
        _write_index_html(tmp_path, css_names=["a.css"], js_names=["a.js"])
        _write_asset(tmp_path, "a.css")
        _write_asset(tmp_path, "a.js")

        _assert_single_chunk_build(tmp_path)  # must not raise

    def test_raises_on_two_css_chunks(self, tmp_path):
        _write_index_html(tmp_path, css_names=["a.css", "vendor.css"], js_names=["a.js"])
        _write_asset(tmp_path, "a.css")
        _write_asset(tmp_path, "vendor.css")
        _write_asset(tmp_path, "a.js")

        with pytest.raises(MultiChunkBuildError, match="2 CSS file"):
            _assert_single_chunk_build(tmp_path)

    def test_raises_on_two_js_chunks(self, tmp_path):
        _write_index_html(tmp_path, css_names=["a.css"], js_names=["a.js"])
        _write_asset(tmp_path, "a.css")
        _write_asset(tmp_path, "a.js")
        _write_asset(tmp_path, "chunk-DYNAMIC.js")  # e.g. a dynamic import() target

        with pytest.raises(MultiChunkBuildError, match="2 JS file"):
            _assert_single_chunk_build(tmp_path)

    def test_raises_on_an_unreferenced_dynamically_imported_chunk(self, tmp_path):
        # The dangerous silent case this guard exists for: index.html's own
        # <script src> tags are perfectly single-chunk (a regex-only check
        # would see nothing wrong), but a second .js file sits on disk,
        # reachable only via a dynamic import() inside the entry chunk's own
        # code - invisible to anything that only reads index.html's tags.
        _write_index_html(tmp_path, css_names=["a.css"], js_names=["a.js"])
        _write_asset(tmp_path, "a.css")
        _write_asset(tmp_path, "a.js", content='import("./lazy-CHUNK123.js");')
        _write_asset(tmp_path, "lazy-CHUNK123.js")

        with pytest.raises(MultiChunkBuildError, match="2 JS file"):
            _assert_single_chunk_build(tmp_path)

    def test_inline_bundle_itself_raises_before_touching_any_tag(self, tmp_path):
        _write_index_html(tmp_path, css_names=["a.css"], js_names=["a.js", "b.js"])
        _write_asset(tmp_path, "a.css")
        _write_asset(tmp_path, "a.js")
        _write_asset(tmp_path, "b.js")

        with pytest.raises(MultiChunkBuildError):
            _inline_bundle(tmp_path)

    def test_error_message_names_the_real_offending_files(self, tmp_path):
        _write_index_html(tmp_path, css_names=["a.css"], js_names=["a.js"])
        _write_asset(tmp_path, "a.css")
        _write_asset(tmp_path, "a.js")
        _write_asset(tmp_path, "b.js")

        with pytest.raises(MultiChunkBuildError) as excinfo:
            _assert_single_chunk_build(tmp_path)

        assert "a.js" in str(excinfo.value)
        assert "b.js" in str(excinfo.value)


class TestGuardIsANoOpWhenAssetsAreEntirelyMissing:
    """_inline_bundle() already has its own, separate, pre-existing fallback
    for a missing index.html ("Island assets are not installed."). The guard
    must not fight that path - a missing assets/ dir is not a multi-chunk
    build, it is a not-yet-built one."""

    def test_no_assets_dir_at_all_does_not_raise(self, tmp_path):
        _assert_single_chunk_build(tmp_path)  # must not raise

    def test_inline_bundle_still_returns_its_own_not_installed_fallback(self, tmp_path):
        document = _inline_bundle(tmp_path)

        assert "Island assets are not installed" in document
