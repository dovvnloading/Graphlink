"""Coverage for ComposerBridge._theme()'s cssVariables field - the runtime
half of composer's theme wiring (the build-time half is
graphlink_web_island_host.py's _inline_bundle(), covered in
test_composer_bridge.py).

Both halves must supply IDENTICAL values for the same theme, since they are
the same DOM property set seen at two different moments (first paint vs. a
live theme change) - that invariant, not just "the field exists," is what
these tests are really guarding.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graphlink_config as config
from graphlink_composer import ComposerController
from graphlink_composer_bridge import ComposerBridge
from graphlink_styles import THEME_TOKENS, THEMES, css_custom_properties, css_root_block


class _Window:
    settings_manager = None
    current_node = None
    pending_attachments = []
    composer_controller = None


def _snapshot() -> dict:
    bridge = ComposerBridge(_Window(), ComposerController())
    states = []
    bridge.stateChanged.connect(states.append)
    bridge.ready()
    return json.loads(states[-1])


class TestCssVariablesMatchTheExportedTable:
    def test_matches_css_custom_properties_exactly_for_the_current_theme(self):
        state = _snapshot()

        assert state["theme"]["cssVariables"] == css_custom_properties(config.CURRENT_THEME)

    def test_matches_for_every_theme_not_just_the_default(self, monkeypatch):
        for theme_name in THEMES:
            monkeypatch.setattr(config, "CURRENT_THEME", theme_name)
            state = _snapshot()

            assert state["theme"]["cssVariables"] == css_custom_properties(theme_name), (
                f"cssVariables mismatch for theme {theme_name!r}"
            )

    def test_composer_alpha_values_survive_verbatim_not_rounded_by_qcolor(self, monkeypatch):
        # The whole reason cssVariables is sourced from css_custom_properties()
        # rather than QColor.name(): QColor drops alpha entirely. Prove the
        # rgba() strings actually reach the payload with their real precision.
        state = _snapshot()
        css_vars = state["theme"]["cssVariables"]

        alpha_keys = [
            f"--gl-composer-{key.replace('_', '-')}"
            for key in THEME_TOKENS[config.CURRENT_THEME]["composer_alpha"]
        ]
        assert alpha_keys, "expected at least one composer_alpha token"
        for key in alpha_keys:
            assert css_vars[key].startswith("rgba("), f"{key} = {css_vars[key]!r} is not an rgba() string"


class TestCssVariablesAreLive:
    def test_reflects_a_theme_change_between_two_publishes(self, monkeypatch):
        bridge = ComposerBridge(_Window(), ComposerController())
        states = []
        bridge.stateChanged.connect(states.append)

        monkeypatch.setattr(config, "CURRENT_THEME", "dark")
        bridge.ready()
        dark_vars = json.loads(states[-1])["theme"]["cssVariables"]

        monkeypatch.setattr(config, "CURRENT_THEME", "mono")
        bridge.publish()
        mono_vars = json.loads(states[-1])["theme"]["cssVariables"]

        assert dark_vars != mono_vars
        assert dark_vars["--gl-composer-shell-background"] == mono_vars["--gl-composer-shell-background"], (
            "composer's own values are theme-invariant today (see "
            "test_composer_token_retrofit.py::test_every_theme_resolves_identically_today) "
            "- this asserts that invariant holds through the bridge too, not "
            "just in graphlink_styles.py directly"
        )
        assert dark_vars["--gl-palette-user-node"] != mono_vars["--gl-palette-user-node"], (
            "a genuinely per-theme value (palette.user_node) should differ "
            "between dark and mono - if this ever fails, cssVariables stopped "
            "being live"
        )


class TestRuntimeAndBuildTimeAgree:
    """The two halves of composer's theme wiring must never disagree."""

    def test_runtime_css_variables_match_the_build_time_root_block(self, monkeypatch):
        for theme_name in THEMES:
            monkeypatch.setattr(config, "CURRENT_THEME", theme_name)
            state = _snapshot()
            runtime_vars = state["theme"]["cssVariables"]

            root_block = css_root_block(theme_name)
            for name, value in runtime_vars.items():
                assert f"{name}: {value};" in root_block, (
                    f"{theme_name}: runtime cssVariables has {name}={value!r}, which "
                    "does not appear in the build-time css_root_block() output - "
                    "first paint and a live theme switch would disagree"
                )
