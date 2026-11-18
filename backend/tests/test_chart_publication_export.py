"""Publication export regression: dimensions, real text, canonical geometry.

These checks exercise the actual SVG/PDF/PNG renderer, not a mocked export
endpoint. The same per-type Matplotlib artists are used for all formats.
"""
import asyncio
import base64
import json
from pathlib import Path
from copy import deepcopy
import math
import re
import struct
from xml.etree import ElementTree as ET

import pytest
from matplotlib.axes import Axes

from graphlink_chart_data import ChartDataError, canonicalize_chart_data, validate_publication_export
from graphlink_chart_rendering import render_chart_publication, PUBLICATION_COLORS
from backend.api.intents_chart import register_chart_intents
from backend.domain.graph import SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState

# The UI test loads this exact JSON as well, so both renderers are held to
# the *same* raw data geometry and labels, not independent approximations.
SPECS = json.loads(
    (Path(__file__).parents[2]
     / "web_ui/src/app/canvas/charts/publicationParityFixtures.json").read_text(encoding="utf-8")
)


def export(chart_type, fmt="svg", *, dpi=300, width=85, height=70, **flags):
    return render_chart_publication(
        chart_type, SPECS[chart_type], width_mm=width, height_mm=height,
        fmt=fmt, dpi=dpi, **flags,
    )


def texts(svg):
    root = ET.fromstring(svg)
    return " ".join("".join(e.itertext()) for e in root.iter() if e.tag.endswith("}text"))


@pytest.mark.parametrize("kind", sorted(SPECS))
def test_every_interactive_chart_type_has_the_same_axes_labels_and_vector_artists(kind):
    original = deepcopy(SPECS[kind])
    svg = export(kind, "svg", colorblind_safe=True)
    pdf = export(kind, "pdf", colorblind_safe=True)
    png = export(kind, "png", colorblind_safe=True)
    assert SPECS[kind] == original, "Export must not modify persisted chart data"
    assert svg.lstrip().startswith(b"<?xml")
    assert b"<svg" in svg and b"Chart rendering issue" not in svg
    assert pdf.startswith(b"%PDF-")
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    # Matplotlib Type-42 font text is searchable; these files must not be
    # composed from a full-page bitmap or outlined SVG glyphs.
    assert b"/ToUnicode" in pdf and (b"/FontFile2" in pdf or b"/FontFile3" in pdf)
    assert "<text" in svg.decode("utf-8"), kind

    label_text = texts(svg)
    canonical = canonicalize_chart_data(original, kind)
    if kind in ("bar", "line", "histogram", "scatter", "boxplot", "heatmap"):
        assert canonical["xAxis"] in label_text
        assert canonical["yAxis"] in label_text
    if kind == "scatter":
        for label in ("Concentration (mg/L)", "Signal (mV)", "Control", "Treatment"):
            assert label in label_text
    if kind == "boxplot":
        assert "Control" in label_text
    if kind == "heatmap":
        for label in ("Control", "Treatment", "0h", "24h", "AU"):
            assert label in label_text
    if kind == "sankey":
        assert "Input" in label_text and "Output" in label_text
    if kind in ("bar", "line", "pie"):
        assert "A" in label_text and "B" in label_text
    if kind == "histogram":
        assert "Mean" in label_text and "Median" in label_text


@pytest.mark.parametrize("dpi", [300, 600])
@pytest.mark.parametrize("size", [(85, 65), (180, 115), (123, 91)])
def test_png_pixel_count_and_vector_page_size_are_physical(dpi, size):
    width, height = size
    png = export("bar", "png", dpi=dpi, width=width, height=height)
    px_width, px_height = struct.unpack(">II", png[16:24])
    assert abs(px_width - width / 25.4 * dpi) <= 1
    assert abs(px_height - height / 25.4 * dpi) <= 1
    svg_root = ET.fromstring(export("bar", "svg", dpi=dpi, width=width, height=height))
    assert math.isclose(float(svg_root.attrib["width"].removesuffix("pt")), width / 25.4 * 72, abs_tol=0.01)
    assert math.isclose(float(svg_root.attrib["height"].removesuffix("pt")), height / 25.4 * 72, abs_tol=0.01)
    # PDF MediaBox is also measured in physical PostScript points.
    pdf = export("bar", "pdf", dpi=dpi, width=width, height=height)
    boxes = re.findall(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", pdf)
    assert boxes
    w_pt, h_pt = map(float, boxes[0])
    assert abs(w_pt - width / 25.4 * 72) < 0.01
    assert abs(h_pt - height / 25.4 * 72) < 0.01


@pytest.mark.parametrize("fmt", ["png", "svg", "pdf"])
@pytest.mark.parametrize("kind,method", [
    ("bar", "bar"), ("line", "plot"),
    ("pie", "pie"), ("histogram", "hist"),
])
def test_basic_chart_mark_geometry_is_the_same_as_interactive_fixture(fmt, kind, method, monkeypatch):
    observed = []
    original = getattr(Axes, method)

    def spy(ax, *args, **kwargs):
        observed.append((args, kwargs))
        return original(ax, *args, **kwargs)

    monkeypatch.setattr(Axes, method, spy)
    export(kind, fmt)
    assert observed, f"{kind} must create actual vector artists"
    args, opts = observed[0]
    if kind in ("bar", "line"):
        assert list(args[0]) == [0, 1]  # Category centers in interactive SVG
        assert list(args[1]) == [2, 4]  # Values, no normalization or rebinning
    elif kind == "pie":
        assert list(args[0]) == [2, 4]  # Same slice areas, total 6
        assert opts["startangle"] == 90 and opts["counterclock"] is False
    else:
        assert list(args[0]) == [1, 2, 3, 4]
        assert opts["bins"] == 4


@pytest.mark.parametrize("fmt", ["png", "svg", "pdf"])
def test_sankey_flow_ribbon_has_same_thickness_as_source_and_target_nodes(fmt, monkeypatch):
    from matplotlib.patches import FancyBboxPatch, PathPatch

    artists = []
    original = Axes.add_patch

    def spy(ax, patch):
        artists.append(patch)
        return original(ax, patch)

    monkeypatch.setattr(Axes, "add_patch", spy)
    export("sankey", fmt)
    ribbons = [patch for patch in artists if type(patch) is PathPatch]
    nodes = [patch for patch in artists if isinstance(patch, FancyBboxPatch)]
    assert len(ribbons) == 1 and len(nodes) == 2
    vertex = ribbons[0].get_path().vertices
    thickness_at_source = vertex[7][1] - vertex[0][1]
    thickness_at_target = vertex[4][1] - vertex[3][1]
    assert math.isclose(thickness_at_source, thickness_at_target, rel_tol=1e-9)
    assert all(math.isclose(thickness_at_source, node.get_height(), rel_tol=1e-9)
               for node in nodes)


@pytest.mark.parametrize("fmt", ["png", "svg", "pdf"])
def test_scatter_export_coordinates_errors_and_legend_are_unmodified(fmt, monkeypatch):
    observed = []
    original = Axes.errorbar

    def spy(ax, x, y, **kwargs):
        observed.append((list(x), list(y), kwargs))
        return original(ax, x, y, **kwargs)
    monkeypatch.setattr(Axes, "errorbar", spy)
    export("scatter", fmt, grayscale_safe=True)
    assert len(observed) == 2
    x, y, opts = observed[0]
    assert x == [1, 10, 100] and y == [2, 4, 8]
    assert opts["xerr"] == ([0.1, 1.0, 10.0], [0.1, 2.0, 10.0])
    assert opts["yerr"] == ([0.3, 0.2, 1.0], [0.3, 0.5, 1.0])
    assert opts["label"] == "Control"
    assert opts["fmt"] != observed[1][2]["fmt"]


@pytest.mark.parametrize("fmt", ["png", "svg", "pdf"])
def test_boxplot_and_heatmap_use_raw_samples_cells_and_original_order(fmt, monkeypatch):
    box_calls, heat_calls = [], []
    original_box, original_heat = Axes.bxp, Axes.pcolormesh

    def bxp(ax, stats, **kwargs):
        box_calls.append(stats)
        return original_box(ax, stats, **kwargs)
    def mesh(ax, *args, **kwargs):
        heat_calls.append(args)
        return original_heat(ax, *args, **kwargs)
    monkeypatch.setattr(Axes, "bxp", bxp)
    monkeypatch.setattr(Axes, "pcolormesh", mesh)
    export("boxplot", fmt)
    export("heatmap", fmt, grayscale_safe=True)
    assert box_calls[0][0]["q1"] == 2
    assert box_calls[0][0]["q3"] == 4
    assert box_calls[0][0]["med"] == 3
    assert box_calls[0][0]["fliers"] == [100]
    # The data axes are drawn before the colorbar, which may also invoke
    # pcolormesh for its own gradient. Check the first (data) mesh only.
    assert heat_calls
    assert list(map(list, heat_calls[0][0])) == [[-2, 0], [2, 4]]


def test_accessible_palettes_match_artists_instead_of_only_a_legend(monkeypatch):
    colors = []
    bars = []
    original_errorbar, original_bar = Axes.errorbar, Axes.bar

    def spy_errorbar(ax, x, y, **kwargs):
        colors.append(kwargs["color"])
        return original_errorbar(ax, x, y, **kwargs)

    def spy_bar(ax, *args, **kwargs):
        items = original_bar(ax, *args, **kwargs)
        bars.extend(items)
        return items

    monkeypatch.setattr(Axes, "errorbar", spy_errorbar)
    monkeypatch.setattr(Axes, "bar", spy_bar)
    export("scatter", "svg", colorblind_safe=True)
    assert colors == list(PUBLICATION_COLORS[:2])
    svg = export("bar", "svg", grayscale_safe=True)
    assert bars[0].get_hatch() != bars[1].get_hatch()
    assert b"<pattern" in svg


def test_export_option_validation_bounds_sizes_dpi_and_flags():
    single = validate_publication_export({"format": "pdf", "size": "single"}, aspect_ratio=0.75)
    assert single["widthMm"] == 85 and single["heightMm"] == 63.75
    double = validate_publication_export({"size": "double"}, aspect_ratio=0.5)
    assert double["widthMm"] == 180 and double["heightMm"] == 90
    custom = validate_publication_export({
        "size": "custom", "widthMm": 123, "heightMm": 67, "dpi": 600,
        "grayscaleSafe": True,
    })
    assert custom["dpi"] == 600 and custom["heightMm"] == 67

    for invalid in [
        {"format": "jpeg"}, {"dpi": 150}, {"dpi": True},
        {"colorblindSafe": "yes"}, {"size": "custom", "widthMm": 80},
        {"size": "single", "widthMm": 85},
        {"size": "custom", "widthMm": 1000, "heightMm": 80},
        {"size": "custom", "widthMm": 300, "heightMm": 300, "dpi": 600},
        {"unknown": 1},
    ]:
        with pytest.raises(ChartDataError):
            validate_publication_export(invalid)


def test_export_intent_returns_authenticated_file_payload_for_saved_chart():
    async def run():
        bus = SessionBus("publication-test")
        document = SceneDocument()
        notifications = NotificationState()
        bus.register_topic("scene", document.scene_payload)
        bus.register_topic("notification", notifications.payload)
        register_chart_intents(bus, document, notifications, object())
        root = document.add_chat_node(0, 0, "source", is_user=True)
        state = canonicalize_chart_data(SPECS["scatter"], "scatter")
        chart = document.add_chart_node(0, 0, root.id, "scatter", state)
        response = await bus.dispatch_intent("scene", "exportPublicationChart", [
            chart.id, {"format": "pdf", "size": "single", "dpi": 300},
        ])
        assert response["filename"].endswith(".pdf")
        assert response["mimeType"] == "application/pdf"
        assert response["widthMm"] == 85
        assert base64.b64decode(response["base64"]).startswith(b"%PDF-")
        with pytest.raises(ChartDataError):
            await bus.dispatch_intent("scene", "exportPublicationChart", [
                "bogus-id", {"format": "pdf", "size": "single"},
            ])
    asyncio.run(run())
