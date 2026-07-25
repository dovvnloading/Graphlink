"""Direct unit tests for graphlink_chart_rendering.render_chart_png (Qt-removal
plan R6.2) - the Qt-free port of legacy ChartItem's Matplotlib rendering.

These exercise render_chart_png directly (not through SceneDocument.
add_chart_node/resize_chart, which are covered separately in
backend/tests/test_canvas.py) to pin down the "never raise for bad chart
data, only for an unknown chart_type" contract at its source, matching
graphlink_app/tests/test_chart_nodes.py's
test_chart_render_is_bounded_for_dense_labels_and_invalid_data_gets_placeholder.
"""

import pytest

from graphlink_chart_rendering import render_chart_png

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

_BAR_DATA = {"type": "bar", "title": "Widgets Sold", "labels": ["Q1", "Q2", "Q3"], "values": [10.0, 20.0, 15.0]}
_SANKEY_DATA = {
    "type": "sankey",
    "title": "Traffic Flow",
    "flows": [
        {"source": "Visitors", "target": "Signups", "value": 100.0},
        {"source": "Signups", "target": "Paid", "value": 20.0},
    ],
}


@pytest.mark.parametrize("chart_type,data", [("bar", _BAR_DATA), ("sankey", _SANKEY_DATA)])
def test_render_chart_png_produces_valid_png_bytes_for_well_formed_data(chart_type, data):
    png_bytes = render_chart_png(chart_type, data, 680.0, 500.0)

    assert isinstance(png_bytes, bytes)
    assert png_bytes.startswith(_PNG_MAGIC)
    assert len(png_bytes) > 100


@pytest.mark.parametrize("chart_type,data", [
    ("bar", {"type": "bar", "title": "Broken", "labels": ["a"], "values": "not-a-list"}),
    ("sankey", {"type": "sankey", "title": "Broken", "flows": "not-a-list"}),
])
def test_render_chart_png_never_raises_for_malformed_data_renders_placeholder_instead(chart_type, data):
    # test_chart_nodes.py's own contract: invalid data must still produce a
    # non-null rendered image - the caller (add_chart_node/resize_chart) is
    # responsible for surfacing chart_error, this function itself must not
    # propagate an exception for merely-bad DATA.
    png_bytes = render_chart_png(chart_type, data, 680.0, 500.0)

    assert isinstance(png_bytes, bytes)
    assert png_bytes.startswith(_PNG_MAGIC)


def test_render_chart_png_raises_value_error_for_an_unsupported_chart_type():
    # Unlike bad DATA, an unknown chart TYPE has no placeholder concept at
    # all - there is no such thing as "a chart of no particular kind".
    with pytest.raises(ValueError):
        render_chart_png("not-a-real-chart-type", _BAR_DATA, 680.0, 500.0)


def test_render_chart_png_at_export_dpi_scale_produces_larger_output_than_display_scale():
    display_bytes = render_chart_png("bar", _BAR_DATA, 680.0, 500.0, dpi_scale=1.0)
    export_bytes = render_chart_png("bar", _BAR_DATA, 680.0, 500.0, dpi_scale=3.0)

    assert len(export_bytes) > len(display_bytes)


@pytest.mark.parametrize("chart_type", ["line", "histogram", "pie"])
def test_render_chart_png_covers_the_remaining_three_chart_types(chart_type):
    data = {
        "line": {"type": "line", "title": "t", "labels": ["a", "b"], "values": [1.0, 2.0]},
        "histogram": {"type": "histogram", "title": "t", "values": [1.0, 2.0, 3.0, 4.0]},
        "pie": {"type": "pie", "title": "t", "labels": ["a", "b"], "values": [1.0, 2.0]},
    }[chart_type]

    png_bytes = render_chart_png(chart_type, data, 680.0, 500.0)

    assert png_bytes.startswith(_PNG_MAGIC)
