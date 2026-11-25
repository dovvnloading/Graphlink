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

from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch, PathPatch

from graphlink_chart_rendering import _build_theme, _render_sankey_chart, render_chart_png

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


# Match the live SVG regression cases in sankeyLayout.test.ts and inspect
# the actual Matplotlib patch geometry, not merely whether a PNG was returned.
@pytest.mark.parametrize("flows", [
    [{"source": f"A{i}", "target": f"B{i}", "value": 1.0} for i in range(count)]
    for count in (30, 150, 300)
] + [
    [
        {"source": "A", "target": "B", "value": 1000.0},
        {"source": "A", "target": "C", "value": 1.0},
        {"source": "B", "target": "D", "value": 1000.0},
        {"source": "C", "target": "D", "value": 1.0},
        {"source": "X", "target": "Y", "value": 0.1},
    ],
    [
        {"source": "A", "target": "B", "value": 0.01},
        {"source": "A", "target": "C", "value": 0.03},
    ],
    [
        {"source": "A", "target": "B", "value": 1e308},
        {"source": "A", "target": "C", "value": 1e308},
    ],
], ids=["dense-30", "dense-150", "dense-300", "skewed", "fractional", "overflow"])
def test_sankey_nodes_and_ribbons_fit_and_preserve_flow_proportions(flows):
    figure = Figure()
    ax = figure.add_subplot()
    _render_sankey_chart(figure, ax, {"flows": flows}, _build_theme())

    nodes = [patch for patch in ax.patches if isinstance(patch, FancyBboxPatch)]
    ribbons = [patch for patch in ax.patches if isinstance(patch, PathPatch)]
    by_name = {label.get_text(): node for label, node in zip(ax.texts, nodes)}
    assert len(nodes) == len({flow[key] for flow in flows for key in ("source", "target")})
    assert len(ribbons) == len(flows)

    # The renderer orders ribbons by source column, source y, and target y.
    ordered = sorted(flows, key=lambda flow: (
        by_name[flow["source"]].get_x(),
        by_name[flow["source"]].get_y(),
        by_name[flow["target"]].get_y(),
    ))
    max_value = max(flow["value"] for flow in flows)
    scale = max(path.get_path().vertices[7, 1] - path.get_path().vertices[0, 1]
                for path in ribbons)
    assert scale > 0
    epsilon = 1e-10
    ports = {name: {"in": [], "out": []} for name in by_name}
    for flow, ribbon in zip(ordered, ribbons):
        vertices = ribbon.get_path().vertices
        source, target = by_name[flow["source"]], by_name[flow["target"]]
        start, end = (vertices[0, 1], vertices[7, 1]), (vertices[3, 1], vertices[4, 1])
        assert vertices[0, 0] == pytest.approx(source.get_x() + source.get_width(), abs=epsilon)
        assert vertices[3, 0] == pytest.approx(target.get_x(), abs=epsilon)
        expected = flow["value"] / max_value * scale
        assert start[1] - start[0] == pytest.approx(expected, abs=epsilon)
        assert end[1] - end[0] == pytest.approx(expected, abs=epsilon)
        ports[flow["source"]]["out"].append(start)
        ports[flow["target"]]["in"].append(end)

    for name, node in by_name.items():
        bounds = node.get_path().get_extents()
        assert bounds.xmin >= 0
        assert bounds.xmax <= 1
        assert bounds.ymin >= 0.09 - epsilon
        assert bounds.ymax <= 0.91 + epsilon
        weight = max(
            sum(flow["value"] / max_value for flow in flows if flow["source"] == name),
            sum(flow["value"] / max_value for flow in flows if flow["target"] == name),
        )
        assert node.get_height() > 0
        assert node.get_height() == pytest.approx(weight * scale, abs=epsilon)
        for side in ports[name].values():
            cursor = node.get_y()
            for start, end in sorted(side):
                assert start == pytest.approx(cursor, abs=epsilon)
                assert end > start
                assert end <= node.get_y() + node.get_height() + epsilon
                cursor = end

    columns = {node.get_x() for node in nodes}
    for x in columns:
        column = sorted((node for node in nodes if node.get_x() == x), key=lambda node: node.get_y())
        for previous, current in zip(column, column[1:]):
            assert current.get_y() >= previous.get_y() + previous.get_height() - epsilon
