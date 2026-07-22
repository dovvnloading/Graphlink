"""UI-refactor P0 acceptance gate (doc/UI_QA_AUDIT.md section 7, P0).

The audit's P0 acceptance criterion, codified so it can never silently
regress: outside the token module (graphlink_styles.py), no UI source file
may contain a hardcoded 6-digit hex color literal or a string-pasted
"Segoe UI". Colors come from get_surface_color()/get_semantic_color()/
get_current_palette()/THEME_TOKENS lookups; the font family comes from
FONT_FAMILY (QSS stacks) / FONT_FAMILY_NAME (QFont).

ALLOWLIST POLICY: entries in _HEX_ALLOWLIST are (relative path, reason)
pairs for files whose remaining literals were adjudicated as domain DATA
rather than UI chrome (e.g. syntax-highlight palettes pending their own
token group). Every entry must carry a reason; an empty allowlist is the
goal state. Adding an entry to dodge the gate defeats P0 - route new
colors through THEME_TOKENS instead.

Structure-token coverage lives here too: the P0 scales exist, are complete,
and respect their own rules (4px spacing grid, no type size under 12px,
three elevation levels, 150/200ms motion).
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graphlink_styles as gs

APP_DIR = Path(__file__).resolve().parents[1]

# The one module allowed to define color literals and the font family.
_TOKEN_MODULES = {"graphlink_styles.py"}

# Adjudicated exceptions: (path relative to graphlink_app/, reason). Each
# entry was individually adjudicated during the P0 sweep (2026-07-22) as
# domain DATA rather than UI chrome. Shrink this list, never grow it
# casually - route new colors through THEME_TOKENS instead.
_HEX_ALLOWLIST: dict[str, str] = {
    "graphlink_context_menu.py": (
        "per-theme QMenu state table carrying literals for NON-current themes "
        "(get_surface_color resolves only the current theme); the menu system "
        "is rebuilt wholesale in refactor phase P8, which retires this file's "
        "styling entirely"
    ),
    "graphlink_exporter.py": (
        "light-styled CSS baked into exported standalone HTML/PDF documents - "
        "deliberately NOT themed to the running app; theming exports to the "
        "app's dark theme would corrupt the user's output artifacts"
    ),
    "graphlink_font_control_bridge.py": (
        "FONT_COLOR_PRESETS: user-pickable canvas font color swatches - "
        "stable data choices offered to the user, not app chrome"
    ),
    "graphlink_grid_control_bridge.py": (
        "grid-color preset swatches - user-pickable data choices, stable "
        "across themes by design"
    ),
    "graphlink_grid_view_settings.py": (
        "DEFAULT_GRID_COLOR in the deliberately Qt-free plain-data settings "
        "model - a persisted user-preference default, not chrome"
    ),
    "graphlink_plugins/common/combo.py": (
        "single #656565 default-parameter accent in a reusable combo widget's "
        "public API signature - callers pass real accents; changing the "
        "default's identity is an API change deferred past P0"
    ),
    "graphlink_canvas/graphlink_canvas_chart_item.py": (
        "single #868686 'slate' entry in the chart data-series color cycle - "
        "series colors are data, not chrome (rule 2 of the sweep)"
    ),
    "graphlink_canvas/graphlink_canvas_container.py": (
        "DEFAULT_CONTAINER_COLOR: the container's persisted user-facing body "
        "color default (saved into scene JSON) - scene data must keep its "
        "color across theme switches, like DEFAULT_GRID_COLOR"
    ),
}

# Files allowed to carry the literal string "Segoe UI" outside the token
# module, same adjudication bar.
_FONT_ALLOWLIST: dict[str, str] = {
    "graphlink_font_control_bridge.py": (
        "FONT_FAMILIES: the user-pickable font list offers 'Segoe UI' and "
        "'Segoe UI Variable' as CHOICES - data presented to the user, not a "
        "hardcoded app default (defaults go through FONT_FAMILY_NAME)"
    ),
}

_HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}\b")


def _ui_source_files():
    for path in sorted(APP_DIR.rglob("*.py")):
        rel = path.relative_to(APP_DIR).as_posix()
        if rel.startswith("tests/"):
            continue
        if path.name in _TOKEN_MODULES:
            continue
        yield rel, path


class TestNoHardcodedHexOutsideTokenModules:
    def test_no_ui_file_contains_a_hex_literal(self):
        offenders = {}
        for rel, path in _ui_source_files():
            if rel in _HEX_ALLOWLIST:
                continue
            text = path.read_text(encoding="utf-8")
            hits = _HEX_RE.findall(text)
            if hits:
                offenders[rel] = sorted(set(hits))
        assert not offenders, (
            "Hardcoded hex color literal(s) outside the token module - route "
            f"them through THEME_TOKENS/get_surface_color instead:\n{offenders}"
        )

    def test_allowlist_entries_all_carry_reasons_and_still_exist(self):
        for rel, reason in _HEX_ALLOWLIST.items():
            assert reason.strip(), f"allowlist entry {rel} has no reason"
            assert (APP_DIR / rel).is_file(), f"allowlist entry {rel} no longer exists - prune it"


class TestNoHardcodedFontFamilyOutsideTokenModules:
    def test_no_ui_file_string_pastes_segoe_ui(self):
        offenders = []
        for rel, path in _ui_source_files():
            if rel in _FONT_ALLOWLIST:
                continue
            text = path.read_text(encoding="utf-8")
            if "Segoe UI" in text:
                offenders.append(rel)
        assert not offenders, (
            "String-pasted 'Segoe UI' outside the token module - use "
            f"FONT_FAMILY / FONT_FAMILY_NAME from graphlink_styles:\n{offenders}"
        )

    def test_font_allowlist_entries_all_carry_reasons_and_still_exist(self):
        for rel, reason in _FONT_ALLOWLIST.items():
            assert reason.strip(), f"font allowlist entry {rel} has no reason"
            assert (APP_DIR / rel).is_file(), f"font allowlist entry {rel} no longer exists - prune it"


class TestStructureTokenScales:
    def test_all_expected_groups_exist(self):
        assert set(gs.STRUCTURE_TOKENS) == {
            "space", "radius", "text", "weight", "shadow", "motion",
        }

    def test_spacing_is_a_4px_grid(self):
        for key, px in gs.SPACE_PX.items():
            assert px % 4 == 0, f"space-{key}={px}px breaks the 4px grid"
        assert gs.SPACE_PX[1] == 4

    def test_no_type_size_under_12px(self):
        # Audit finding D2: 9-10px microtext. The ramp's floor is 12px.
        for key, px in gs.TEXT_PX.items():
            assert px >= 12, f"text-{key}={px}px is below the 12px floor"

    def test_three_radius_steps(self):
        assert set(gs.RADIUS_PX) == {"sm", "md", "lg"}
        assert gs.RADIUS_PX["sm"] < gs.RADIUS_PX["md"] < gs.RADIUS_PX["lg"]

    def test_three_elevation_levels_in_both_representations(self):
        assert set(gs.STRUCTURE_TOKENS["shadow"]) == {"1", "2", "3"}
        assert set(gs.ELEVATION_PARAMS) == {1, 2, 3}

    def test_motion_scale(self):
        motion = gs.STRUCTURE_TOKENS["motion"]
        assert motion["fast"] == "150ms"
        assert motion["base"] == "200ms"
        assert motion["ease"].startswith("cubic-bezier(")

    def test_structure_tokens_are_exported_to_every_theme_identically(self):
        for theme in ("dark", "mono", "muted"):
            props = gs.css_custom_properties(theme)
            for group, entries in gs.STRUCTURE_TOKENS.items():
                for key, value in entries.items():
                    assert props[f"--gl-{group}-{key}"] == value

    def test_surface_group_exports_for_every_theme(self):
        for theme in ("dark", "mono", "muted"):
            props = gs.css_custom_properties(theme)
            for key in gs.THEME_TOKENS[theme]["surface"]:
                assert f"--gl-surface-{key.replace('_', '-')}" in props
