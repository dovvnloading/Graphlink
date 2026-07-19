"""Coverage for graphlink_styles.css_custom_properties()/css_root_block()
(Phase 1 checklist: Tailwind var(--gl-*) preset - Python-side token export,
split out as its own first slice; see the master plan for the split
rationale).

This is purely additive - it reads THEME_TOKENS and the frame-color preset
dicts without modifying either, and no existing function's behavior changes.
There is no "golden baseline captured before this landed" the way earlier
token-table increments needed one; the checks here are direct structural and
round-trip correctness against the live source data instead.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import graphlink_styles as gs

THEMES = ("dark", "mono", "muted")
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
RGBA_RE = re.compile(r"^rgba\(\d{1,3}, \d{1,3}, \d{1,3}, [01](\.\d+)?\)$")
CSS_VAR_NAME_RE = re.compile(r"^--gl-[a-z0-9]+(-[a-z0-9]+)*$")


class TestCssCustomPropertiesStructure:
    @pytest.mark.parametrize("theme_name", THEMES)
    def test_every_key_is_a_well_formed_css_custom_property_name(self, theme_name):
        props = gs.css_custom_properties(theme_name)
        assert len(props) > 0
        for key in props:
            assert CSS_VAR_NAME_RE.match(key), f"{key!r} is not a valid --gl-* custom property name"

    @pytest.mark.parametrize("theme_name", THEMES)
    def test_key_set_is_identical_across_every_theme(self, theme_name):
        # Different themes must expose the same *shape* (same token names),
        # only differing in value - a Tailwind preset built against one
        # theme's key set must work unmodified against every other.
        assert set(gs.css_custom_properties(theme_name)) == set(gs.css_custom_properties("dark"))

    @pytest.mark.parametrize("theme_name", THEMES)
    def test_every_value_is_a_flat_hex_color_except_font_family_and_alpha_groups(
        self, theme_name
    ):
        # The carve-out is enumerated from the composer_alpha group's real key
        # set rather than widened to "anything that looks like rgba()", so a
        # NEW non-hex value appearing anywhere else still fails loudly.
        props = gs.css_custom_properties(theme_name)
        alpha_keys = {
            f"--gl-composer-{key.replace('_', '-')}"
            for key in gs.THEME_TOKENS[theme_name]["composer_alpha"]
        }
        for key, value in props.items():
            if key == "--gl-font-family":
                continue
            if key in alpha_keys:
                assert RGBA_RE.match(value), f"{key} = {value!r} is not an rgba() literal"
                continue
            assert HEX_RE.match(value), f"{key} = {value!r} is not a flat #RRGGBB hex color"

    def test_qss_and_qss_alpha_groups_are_excluded(self):
        # Those are QSS-only literals for the hand-written Qt stylesheets,
        # not colors any web island's own UI is expected to reuse.
        props = gs.css_custom_properties("dark")
        assert not any(key.startswith("--gl-qss-") for key in props)


class TestCssCustomPropertiesRoundTripAgainstThemeTokens:
    @pytest.mark.parametrize("theme_name", THEMES)
    @pytest.mark.parametrize("group", ["palette", "semantic", "neutral_button", "graph_node"])
    def test_every_theme_tokens_value_is_reachable_unmodified(self, theme_name, group):
        props = gs.css_custom_properties(theme_name)
        tokens = gs.THEME_TOKENS[theme_name][group]
        group_slug = group.replace("_", "-")
        for key, value in tokens.items():
            css_key = f"--gl-{group_slug}-{key.replace('_', '-')}"
            assert props.get(css_key) == value, f"{theme_name}.{group}.{key} did not round-trip via {css_key}"

    @pytest.mark.parametrize("theme_name", THEMES)
    def test_frame_colors_are_deduplicated_by_base_name_not_by_full_or_header_type(self, theme_name):
        # "X" and "X Header" share the same color in every theme (verified
        # directly against the source dicts here, not assumed) - the CSS
        # export should expose one token per base name, not two identical ones.
        frame_colors = gs._FRAME_COLORS_BY_THEME[theme_name]
        for name, entry in frame_colors.items():
            if not name.endswith(" Header"):
                continue
            base_entry = frame_colors[name[: -len(" Header")]]
            assert entry["color"] == base_entry["color"], (
                f"{theme_name}: {name!r} and its base differ in color - "
                "the CSS export's Header-dedup assumption doesn't hold here"
            )

        props = gs.css_custom_properties(theme_name)
        frame_keys = {k for k in props if k.startswith("--gl-frame-")}
        expected_base_names = {name.removesuffix(" Header") for name in frame_colors}
        assert len(frame_keys) == len(expected_base_names)

    @pytest.mark.parametrize("theme_name", THEMES)
    def test_every_frame_color_value_matches_its_source_dict(self, theme_name):
        props = gs.css_custom_properties(theme_name)
        for name, entry in gs._FRAME_COLORS_BY_THEME[theme_name].items():
            base = name.removesuffix(" Header")
            slug = base.lower().replace(" ", "-")
            assert props[f"--gl-frame-{slug}"] == entry["color"]

    def test_font_family_matches_the_shared_constant(self):
        for theme_name in THEMES:
            assert gs.css_custom_properties(theme_name)["--gl-font-family"] == gs.FONT_FAMILY

    @pytest.mark.parametrize("theme_name", THEMES)
    def test_total_property_count_has_no_missing_or_extra_keys(self, theme_name):
        tokens = gs.THEME_TOKENS[theme_name]
        expected_group_count = sum(len(tokens[g]) for g in ("palette", "semantic", "neutral_button", "graph_node"))
        expected_island_count = sum(len(tokens[g]) for g in gs._ISLAND_GROUPS)
        expected_frame_count = len({name.removesuffix(" Header") for name in gs._FRAME_COLORS_BY_THEME[theme_name]})
        expected_total = (
            expected_group_count + expected_island_count + expected_frame_count + 1
        )  # +1 for --gl-font-family
        assert len(gs.css_custom_properties(theme_name)) == expected_total


class TestCssRootBlock:
    @pytest.mark.parametrize("theme_name", THEMES)
    def test_is_syntactically_a_single_root_block(self, theme_name):
        block = gs.css_root_block(theme_name)
        assert block.startswith(":root {\n")
        assert block.rstrip().endswith("}")
        assert block.count(":root") == 1
        assert block.count("{") == 1
        assert block.count("}") == 1

    @pytest.mark.parametrize("theme_name", THEMES)
    def test_every_property_appears_exactly_once_with_a_terminating_semicolon(self, theme_name):
        props = gs.css_custom_properties(theme_name)
        block = gs.css_root_block(theme_name)
        assert block.count(";") == len(props)
        for key, value in props.items():
            assert f"{key}: {value};" in block

    @pytest.mark.parametrize("theme_name", THEMES)
    def test_is_deterministic_and_sorted(self, theme_name):
        first = gs.css_root_block(theme_name)
        second = gs.css_root_block(theme_name)
        assert first == second

        declared_names = re.findall(r"^\s*(--gl-[a-z0-9-]+):", first, re.MULTILINE)
        assert declared_names == sorted(declared_names)

    def test_different_themes_produce_different_blocks(self):
        blocks = {theme_name: gs.css_root_block(theme_name) for theme_name in THEMES}
        assert blocks["dark"] != blocks["mono"] != blocks["muted"] != blocks["dark"]


class TestSafetyGuards:
    """Regression coverage for three gaps an adversarial review found and
    fixed in the same change: the frame-color dedup silently trusting dict
    iteration order instead of asserting the assumption it depends on, no
    validation against a CSS-breaking character in any exported value, and
    no self-check that THEME_TOKENS's group set is fully accounted for
    (included or deliberately excluded)."""

    def test_frame_color_header_variant_diverging_from_base_raises(self, monkeypatch):
        diverged = {
            key: dict(value) for key, value in gs.DARK_FRAME_COLORS.items()
        }
        diverged["Green Header"] = {"color": "#ff00ff", "type": "header"}
        monkeypatch.setitem(gs._FRAME_COLORS_BY_THEME, "dark", diverged)

        with pytest.raises(AssertionError, match="Green Header"):
            gs.css_custom_properties("dark")

    def test_frame_color_dedup_is_independent_of_dict_iteration_order(self, monkeypatch):
        # Same data, header entries listed first - must resolve to the exact
        # same output as base-first order, not whichever is encountered first.
        reordered = dict(reversed(list(gs.DARK_FRAME_COLORS.items())))
        monkeypatch.setitem(gs._FRAME_COLORS_BY_THEME, "dark", reordered)
        assert gs.css_custom_properties("dark") == gs.css_custom_properties("dark")
        # And matches the un-reordered result's frame values exactly.
        reordered_result = {
            k: v for k, v in gs.css_custom_properties("dark").items() if k.startswith("--gl-frame-")
        }
        monkeypatch.setitem(gs._FRAME_COLORS_BY_THEME, "dark", gs.DARK_FRAME_COLORS)
        original_result = {
            k: v for k, v in gs.css_custom_properties("dark").items() if k.startswith("--gl-frame-")
        }
        assert reordered_result == original_result

    @pytest.mark.parametrize("bad_char", [";", "{", "}", "\n", "\r", "<", ">"])
    def test_a_css_breaking_character_in_a_token_value_raises(self, monkeypatch, bad_char):
        tampered = dict(gs.THEME_TOKENS["dark"])
        tampered_palette = dict(tampered["palette"])
        tampered_palette["user_node"] = f"#838383{bad_char}injected"
        tampered["palette"] = tampered_palette
        monkeypatch.setitem(gs.THEME_TOKENS, "dark", tampered)

        with pytest.raises(ValueError, match="injected"):
            gs.css_custom_properties("dark")

    def test_font_family_containing_a_css_breaking_character_raises(self, monkeypatch):
        monkeypatch.setattr(gs, "FONT_FAMILY", "Segoe UI; } body { color: red")
        with pytest.raises(ValueError):
            gs.css_custom_properties("dark")

    def test_an_unaccounted_theme_tokens_group_raises(self, monkeypatch):
        tampered = dict(gs.THEME_TOKENS["dark"])
        tampered["accent"] = {"primary": "#123456"}
        monkeypatch.setitem(gs.THEME_TOKENS, "dark", tampered)

        with pytest.raises(AssertionError, match="accent"):
            gs.css_custom_properties("dark")
