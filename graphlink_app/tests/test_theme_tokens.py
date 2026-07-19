"""Regression coverage for graphlink_styles.THEME_TOKENS and the functions that
became lookups against it: get_semantic_color, get_neutral_button_colors,
get_graph_node_colors, ColorPalette (via DARK_PALETTE/MONO_PALETTE/MUTED_PALETTE),
and ComposerBridge._theme().

Every expected value below was captured from the actual running app BEFORE the
token-table refactor landed (a golden baseline, not retyped from the old
per-theme branching logic) and cross-checked programmatically against the
refactored code - not verified by eye. These tests pin that baseline going
forward so a future edit to THEME_TOKENS (or the functions reading it) that
silently drifts a color value fails immediately instead of shipping a visual
regression.

The three hand-written QSS stylesheet strings in graphlink_styles.py are NOT
yet generated from THEME_TOKENS - that is deliberately a separate, later step
(see the master plan). This file only guards the token table and the four
functions/class this increment actually touched.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PySide6.QtCore import QObject

import graphlink_config as gc
import graphlink_styles as gs
from graphlink_composer import ComposerController
from graphlink_composer_bridge import ComposerBridge
from graphlink_island_bridge import IslandBridge

# Golden values, captured from the running app before THEME_TOKENS existed.
GOLDEN_PALETTE = {
    "dark": {"userNode": "#838383", "aiNode": "#828282", "selection": "#858585", "navHighlight": "#949494"},
    "mono": {"userNode": "#999999", "aiNode": "#bbbbbb", "selection": "#ffffff", "navHighlight": "#dddddd"},
    "muted": {"userNode": "#757575", "aiNode": "#707070", "selection": "#848484", "navHighlight": "#8c8c8c"},
}

GOLDEN_SEMANTIC = {
    "dark": {
        "search_highlight": "#949494", "status_info": "#828282", "status_success": "#838383",
        "status_error": "#848484", "status_warning": "#919191", "artifact": "#828282",
        "conversation_user_bubble": "#696969", "conversation_ai_bubble": "#323232",
        "some_unknown_role": "#858585",  # falls through to the theme's "default"
    },
    "mono": {
        "search_highlight": "#dddddd", "status_info": "#bbbbbb", "status_success": "#999999",
        "status_error": "#9a9a9a", "status_warning": "#b0b0b0", "artifact": "#8f8f8f",
        "conversation_user_bubble": "#595959", "conversation_ai_bubble": "#323232",
        "some_unknown_role": "#ffffff",
    },
    "muted": {
        "search_highlight": "#8c8c8c", "status_info": "#707070", "status_success": "#757575",
        "status_error": "#8a8a8a", "status_warning": "#8d8d8d", "artifact": "#707070",
        "conversation_user_bubble": "#5e5e5e", "conversation_ai_bubble": "#323232",
        "some_unknown_role": "#848484",
    },
}

GOLDEN_NEUTRAL_BUTTON = {
    "dark": {"background": "#393939", "hover": "#484848", "pressed": "#343434", "border": "#585858", "icon": "#f0f0f0", "muted_icon": "#bdbdbd"},
    "mono": {"background": "#555555", "hover": "#666666", "pressed": "#4a4a4a", "border": "#666666", "icon": "#ffffff", "muted_icon": "#d5d5d5"},
    "muted": {"background": "#3a3a3a", "hover": "#484848", "pressed": "#363636", "border": "#5e5e5e", "icon": "#dbdbdb", "muted_icon": "#bababa"},
}

GOLDEN_GRAPH_NODE = {
    "dark": {"border": "#585858", "header": "#bdbdbd", "dot": "#585858", "hover_dot": "#484848", "hover_outline": "#515151", "selected_outline": "#595959", "body_start": "#303030", "body_end": "#292929", "header_start": "#3c3c3c", "header_end": "#333333", "badge_fill": "#484848", "panel_fill": "#202020", "panel_border": "#585858"},
    "mono": {"border": "#666666", "header": "#d5d5d5", "dot": "#666666", "hover_dot": "#666666", "hover_outline": "#727272", "selected_outline": "#7e7e7e", "body_start": "#303030", "body_end": "#292929", "header_start": "#3c3c3c", "header_end": "#333333", "badge_fill": "#484848", "panel_fill": "#202020", "panel_border": "#666666"},
    "muted": {"border": "#5e5e5e", "header": "#bababa", "dot": "#5e5e5e", "hover_dot": "#484848", "hover_outline": "#515151", "selected_outline": "#595959", "body_start": "#303030", "body_end": "#282828", "header_start": "#3d3d3d", "header_end": "#333333", "badge_fill": "#4a4a4a", "panel_fill": "#1c1c1c", "panel_border": "#5e5e5e"},
}


@pytest.fixture(autouse=True)
def _restore_current_theme():
    original = gc.CURRENT_THEME
    yield
    gc.CURRENT_THEME = original


class TestThemeTokensStructure:
    def test_every_theme_has_all_expected_token_groups(self):
        # "qss"/"qss_alpha" were added by the QSS-generation increment
        # (see tests/test_qss_generation.py for their coverage); this was
        # "all four token groups" before that change landed.
        for name in ("dark", "mono", "muted"):
            tokens = gs.THEME_TOKENS[name]
            assert set(tokens.keys()) == {
                "palette", "semantic", "neutral_button", "graph_node", "qss", "qss_alpha",
            }

    def test_themes_dict_exposes_the_matching_token_table(self):
        for name in gs.THEMES:
            assert gs.THEMES[name]["tokens"] is gs.THEME_TOKENS[name]


class TestColorPaletteBecameALookup:
    @pytest.mark.parametrize("theme_name,palette", [
        ("dark", gs.DARK_PALETTE), ("mono", gs.MONO_PALETTE), ("muted", gs.MUTED_PALETTE),
    ])
    def test_palette_matches_golden(self, theme_name, palette):
        expected = GOLDEN_PALETTE[theme_name]
        assert palette.USER_NODE.name() == expected["userNode"]
        assert palette.AI_NODE.name() == expected["aiNode"]
        assert palette.SELECTION.name() == expected["selection"]
        assert palette.NAV_HIGHLIGHT.name() == expected["navHighlight"]

    def test_palette_frame_colors_match_the_per_theme_preset_dict(self):
        assert gs.DARK_PALETTE.FRAME_COLORS is gs.DARK_FRAME_COLORS
        assert gs.MONO_PALETTE.FRAME_COLORS is gs.MONO_FRAME_COLORS
        assert gs.MUTED_PALETTE.FRAME_COLORS is gs.MUTED_FRAME_COLORS


class TestGetSemanticColorBecameALookup:
    @pytest.mark.parametrize("theme_name", ["dark", "mono", "muted"])
    def test_every_role_matches_golden(self, theme_name):
        gc.CURRENT_THEME = theme_name
        for role, expected_hex in GOLDEN_SEMANTIC[theme_name].items():
            actual = gc.get_semantic_color(role).name()
            assert actual == expected_hex, f"{theme_name}.{role}: got {actual}, expected {expected_hex}"

    def test_unknown_role_falls_through_to_default_not_a_crash(self):
        gc.CURRENT_THEME = "dark"
        color = gc.get_semantic_color("totally_made_up_role_xyz")
        assert color.name() == GOLDEN_SEMANTIC["dark"]["some_unknown_role"]


class TestGetNeutralButtonColorsBecameALookup:
    @pytest.mark.parametrize("theme_name", ["dark", "mono", "muted"])
    def test_matches_golden(self, theme_name):
        gc.CURRENT_THEME = theme_name
        colors = gc.get_neutral_button_colors()
        expected = GOLDEN_NEUTRAL_BUTTON[theme_name]
        assert set(colors.keys()) == set(expected.keys())
        for key, expected_hex in expected.items():
            assert colors[key].name() == expected_hex, f"{theme_name}.{key}"


class TestGetGraphNodeColorsBecameALookup:
    @pytest.mark.parametrize("theme_name", ["dark", "mono", "muted"])
    def test_matches_golden(self, theme_name):
        gc.CURRENT_THEME = theme_name
        colors = gc.get_graph_node_colors()
        expected = GOLDEN_GRAPH_NODE[theme_name]
        assert set(colors.keys()) == set(expected.keys())
        for key, expected_hex in expected.items():
            assert colors[key].name() == expected_hex, f"{theme_name}.{key}"


class _Window:
    settings_manager = None
    current_node = None
    pending_attachments = []


class TestBridgeSerializesTheFullTable:
    @pytest.mark.parametrize("theme_name", ["dark", "mono", "muted"])
    def test_theme_payload_matches_golden_for_every_group(self, theme_name):
        gc.CURRENT_THEME = theme_name
        bridge = ComposerBridge(_Window(), ComposerController())

        theme = bridge._theme()

        assert theme["mode"] == "dark"
        assert theme["name"] == theme_name
        assert theme["palette"] == GOLDEN_PALETTE[theme_name]

        expected_semantic = {
            "searchHighlight": GOLDEN_SEMANTIC[theme_name]["search_highlight"],
            "statusInfo": GOLDEN_SEMANTIC[theme_name]["status_info"],
            "statusSuccess": GOLDEN_SEMANTIC[theme_name]["status_success"],
            "statusError": GOLDEN_SEMANTIC[theme_name]["status_error"],
            "statusWarning": GOLDEN_SEMANTIC[theme_name]["status_warning"],
            "artifact": GOLDEN_SEMANTIC[theme_name]["artifact"],
            "conversationUserBubble": GOLDEN_SEMANTIC[theme_name]["conversation_user_bubble"],
            "conversationAiBubble": GOLDEN_SEMANTIC[theme_name]["conversation_ai_bubble"],
            "default": GOLDEN_SEMANTIC[theme_name]["some_unknown_role"],
        }
        assert theme["semantic"] == expected_semantic

        expected_neutral = {
            "background": GOLDEN_NEUTRAL_BUTTON[theme_name]["background"],
            "hover": GOLDEN_NEUTRAL_BUTTON[theme_name]["hover"],
            "pressed": GOLDEN_NEUTRAL_BUTTON[theme_name]["pressed"],
            "border": GOLDEN_NEUTRAL_BUTTON[theme_name]["border"],
            "icon": GOLDEN_NEUTRAL_BUTTON[theme_name]["icon"],
            "mutedIcon": GOLDEN_NEUTRAL_BUTTON[theme_name]["muted_icon"],
        }
        assert theme["neutralButton"] == expected_neutral

        gn = GOLDEN_GRAPH_NODE[theme_name]
        expected_graph_node = {
            "border": gn["border"], "header": gn["header"], "dot": gn["dot"],
            "hoverDot": gn["hover_dot"], "hoverOutline": gn["hover_outline"],
            "selectedOutline": gn["selected_outline"], "bodyStart": gn["body_start"],
            "bodyEnd": gn["body_end"], "headerStart": gn["header_start"],
            "headerEnd": gn["header_end"], "badgeFill": gn["badge_fill"],
            "panelFill": gn["panel_fill"], "panelBorder": gn["panel_border"],
        }
        assert theme["graphNode"] == expected_graph_node

    def test_theme_payload_is_included_in_the_full_state_and_json_serializable(self):
        import json

        gc.CURRENT_THEME = "dark"
        bridge = ComposerBridge(_Window(), ComposerController())
        states = []
        bridge.stateChanged.connect(states.append)

        bridge.ready()

        payload = json.loads(states[-1])
        assert payload["theme"]["name"] == "dark"
        assert payload["theme"]["palette"]["selection"] == GOLDEN_PALETTE["dark"]["selection"]

    def test_trusts_current_theme_the_same_way_get_current_palette_always_has(self):
        # _theme() deliberately has no fallback for an unrecognized CURRENT_THEME,
        # matching get_current_palette()'s long-standing (and unchanged) contract -
        # apply_theme() is the one place that guarantees a valid theme name, and
        # nothing else in the app (40+ callers of get_current_palette()) has ever
        # needed defensiveness beyond that. An earlier draft of _theme() added a
        # speculative fallback THEME_TOKENS.get(..., THEME_TOKENS["dark"]) that
        # this codebase's own conventions didn't call for; this test pins the
        # correction, not the fallback.
        gc.CURRENT_THEME = "some_theme_that_does_not_exist"
        bridge = ComposerBridge(_Window(), ComposerController())

        with pytest.raises(KeyError):
            bridge._theme()


class TestDeadThemeChangedSignalWasRemoved:
    def test_theme_changed_no_longer_exists_on_composer_bridge(self):
        bridge = ComposerBridge(_Window(), ComposerController())
        assert not hasattr(bridge, "themeChanged")

    def test_after_publish_hook_is_the_island_bridge_no_op_default(self):
        # ComposerBridge no longer overrides _after_publish at all - it should
        # be exactly IslandBridge's inherited no-op, not a leftover override.
        assert ComposerBridge._after_publish is IslandBridge._after_publish


class TestThemeFlagsAndStylesheetsUnaffectedByThisIncrement:
    """This increment deliberately does not touch the three QSS stylesheet
    strings - only the token table and the four functions built on it. These
    checks guard that boundary, not full byte-identical QSS golden coverage
    (that is the deferred follow-up step)."""

    @pytest.mark.parametrize("theme_name,is_mono,is_muted", [
        ("dark", False, False), ("mono", True, False), ("muted", False, True),
    ])
    def test_theme_flags_unchanged(self, theme_name, is_mono, is_muted):
        gc.CURRENT_THEME = theme_name
        assert gc.is_monochrome_theme() is is_mono
        assert gc.is_muted_theme() is is_muted

    def test_stylesheets_still_present_and_non_empty_for_every_theme(self):
        for name in ("dark", "mono", "muted"):
            stylesheet = gs.THEMES[name]["stylesheet"]
            assert isinstance(stylesheet, str)
            assert len(stylesheet) > 1000
