"""Staleness coverage for web_ui/src/lib/tokens/gl-theme.css, generated from
graphlink_styles.py::tailwind_theme_css() (Phase 1 checklist: Tailwind
preset mapped to var(--gl-*)). Mirrors the QSS-golden-test pattern already
used for the generated QSS stylesheets: the checked-in file is compared
against what regenerating it right now would produce, so an edit to
THEME_TOKENS/frame colors/FONT_FAMILY that changes the token name set (not
just values - values aren't in this file at all) can't silently drift the
checked-in Tailwind preset out of sync with what Python actually exports.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graphlink_styles as gs

GENERATED_FILE = Path(__file__).resolve().parents[2] / "web_ui" / "src" / "lib" / "tokens" / "gl-theme.css"


def _read_generated_file():
    with open(GENERATED_FILE, "r", encoding="utf-8") as f:
        return f.read()


class TestGlThemeCssIsNotStale:
    def test_file_exists(self):
        assert GENERATED_FILE.is_file(), f"{GENERATED_FILE} is missing - regenerate it from tailwind_theme_css()"

    def test_checked_in_file_matches_regenerating_it_now(self):
        checked_in = _read_generated_file()
        # The checked-in file has a short generated-file header comment
        # before the @theme block itself; only the @theme block is compared
        # against fresh regeneration (the header is static, hand-written).
        theme_block_start = checked_in.index("@theme {")
        checked_in_block = checked_in[theme_block_start:]
        fresh_block = gs.tailwind_theme_css()
        assert checked_in_block == fresh_block, (
            f"{GENERATED_FILE} is stale - regenerate it from graphlink_styles.py::tailwind_theme_css() "
            "(THEME_TOKENS/frame-color/FONT_FAMILY token names changed since this file was last generated)"
        )


class TestGlThemeCssStructure:
    def test_declares_exactly_one_theme_block(self):
        content = _read_generated_file()
        assert content.count("@theme {") == 1
        assert content.count("}") == 1

    def test_every_declaration_references_the_matching_gl_custom_property(self):
        content = _read_generated_file()
        declarations = re.findall(r"--([a-z-]+):\s*var\((--gl-[a-z-]+)\);", content)
        assert len(declarations) > 0
        for tailwind_name, gl_var in declarations:
            # Every Tailwind theme key is either "color-gl-..." (mirroring
            # the referenced --gl-... name exactly) or the one special-cased
            # "font-gl" for --gl-font-family.
            if tailwind_name == "font-gl":
                assert gl_var == "--gl-font-family"
            else:
                assert tailwind_name == f"color-{gl_var[len('--'):]}", (
                    f"--{tailwind_name} does not mirror {gl_var} as expected"
                )

    def test_every_css_custom_property_name_is_covered_exactly_once(self):
        content = _read_generated_file()
        referenced = set(re.findall(r"var\((--gl-[a-z-]+)\)", content))
        expected = set(gs.css_custom_properties("dark"))
        assert referenced == expected
