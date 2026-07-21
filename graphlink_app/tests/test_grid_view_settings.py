"""GridViewSettings is a plain, Qt-free model (Phase 6 increment 4) -
extracted from the widget-as-model anti-pattern where ChatView.
drawBackground()/ChatScene's snap math/ChatCanvasChartItem's resize-to-grid
math all read grid_size/grid_opacity/grid_style/grid_color directly off the
live GridControl QWidget. Defaults must match GridControl's own former
constructor defaults exactly - a silent drift here would change the grid's
real rendered appearance on every fresh app launch.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphlink_grid_view_settings import (
    DEFAULT_GRID_COLOR,
    DEFAULT_GRID_OPACITY,
    DEFAULT_GRID_SIZE,
    DEFAULT_GRID_STYLE,
    GRID_SIZE_PRESETS,
    GRID_STYLE_PRESETS,
    GridViewSettings,
)


def test_defaults_match_the_legacy_widgets_own_construction_time_defaults():
    settings = GridViewSettings()

    assert settings.grid_size == 10 == DEFAULT_GRID_SIZE
    assert settings.grid_opacity == 0.3 == DEFAULT_GRID_OPACITY
    assert settings.grid_style == "Dots" == DEFAULT_GRID_STYLE
    assert settings.grid_color == "#555555" == DEFAULT_GRID_COLOR


def test_fields_are_plain_mutable_attributes():
    settings = GridViewSettings()

    settings.grid_size = 50
    settings.grid_opacity = 0.75
    settings.grid_style = "Lines"
    settings.grid_color = "#FF0000"

    assert (settings.grid_size, settings.grid_opacity, settings.grid_style, settings.grid_color) == (
        50, 0.75, "Lines", "#FF0000",
    )


def test_two_instances_do_not_share_state():
    a = GridViewSettings()
    b = GridViewSettings()

    a.grid_size = 999

    assert b.grid_size == DEFAULT_GRID_SIZE


def test_presets_match_the_legacy_widgets_own_hardcoded_buttons():
    assert GRID_SIZE_PRESETS == (10, 20, 50, 100)
    assert GRID_STYLE_PRESETS == ("Dots", "Lines", "Cross")
