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
        declarations = re.findall(r"--([a-z0-9-]+):\s*var\((--gl-[a-z0-9-]+)\);", content)
        assert len(declarations) > 0
        # UI-refactor P0: structure tokens route to Tailwind v4's proper
        # non-color namespaces (spacing/radius/text/font-weight/shadow/
        # duration/ease); everything else stays "color-gl-..." except the
        # original "font-gl" special case. Mirrors tailwind_theme_css()'s
        # own routing table.
        structure_routes = {
            "--gl-space-": "spacing-gl-",
            "--gl-radius-": "radius-gl-",
            "--gl-text-": "text-gl-",
            "--gl-weight-": "font-weight-gl-",
            "--gl-shadow-": "shadow-gl-",
        }
        for tailwind_name, gl_var in declarations:
            if tailwind_name == "font-gl":
                assert gl_var == "--gl-font-family"
                continue
            if gl_var == "--gl-motion-ease":
                assert tailwind_name == "ease-gl"
                continue
            if gl_var.startswith("--gl-motion-"):
                assert tailwind_name == f"duration-gl-{gl_var[len('--gl-motion-'):]}"
                continue
            for gl_prefix, tw_prefix in structure_routes.items():
                if gl_var.startswith(gl_prefix):
                    assert tailwind_name == f"{tw_prefix}{gl_var[len(gl_prefix):]}", (
                        f"--{tailwind_name} does not mirror {gl_var} as expected"
                    )
                    break
            else:
                assert tailwind_name == f"color-{gl_var[len('--'):]}", (
                    f"--{tailwind_name} does not mirror {gl_var} as expected"
                )

    def test_every_css_custom_property_name_is_covered_exactly_once(self):
        # Island-scoped tokens (see graphlink_styles._ISLAND_GROUPS) are
        # deliberately NOT registered as Tailwind design tokens - exporting
        # them as utilities would publish one island's private chrome palette
        # as a workspace-wide surface. The carve-out is subtracted from the
        # expected set explicitly, so a token dropping out of the @theme block
        # for any OTHER reason still fails here.
        content = _read_generated_file()
        referenced = set(re.findall(r"var\((--gl-[a-z0-9-]+)\)", content))
        expected = set(gs.css_custom_properties("dark")) - gs.island_property_names("dark")
        assert referenced == expected

    def test_island_scoped_tokens_are_absent_from_the_theme_block(self):
        content = _read_generated_file()
        for name in gs.island_property_names("dark"):
            assert name not in content, (
                f"{name} is island-scoped and must not appear in the Tailwind "
                "@theme block"
            )
