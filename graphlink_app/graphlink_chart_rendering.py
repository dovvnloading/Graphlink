"""Qt-free chart rendering (Qt-removal plan R6.2).

Ports graphlink_canvas/graphlink_canvas_chart_item.py's Matplotlib rendering
(`_render_bar_chart`/`_render_line_chart`/`_render_pie_chart`/
`_render_histogram`/`_render_sankey_chart`, plus `_build_theme`/`_mpl_rgba`
and the figure-to-image step) into a standalone function that returns raw PNG
bytes instead of a QImage - the ONLY Qt touch point the legacy item had; the
chart DATA VISUALIZATION itself was already Qt-free (matplotlib.use("Agg") +
FigureCanvasAgg). The card "chrome" the legacy item painted on top with
QPainter (rounded rect, header, badge, resize handle) is intentionally NOT
reproduced here - that becomes plain React/CSS around the `<img>` this PNG
feeds, per the R6.2 design decision.

Theme: `_build_theme`/`_mpl_rgba`/`_with_alpha`/`_blend_colors` used
QColor/get_surface_color/get_current_palette (Qt + graphlink_config, both
off-limits here). Rewritten below as plain hex-string constants copied
verbatim from graphlink_config.py's "dark" theme tokens (the app's actual
default theme - CURRENT_THEME = "dark") - not arbitrary picks, the SAME
values the legacy chart rendered with in practice. One deliberate
improvement over a literal port: every legacy `_with_alpha(color, n).name()`
call site was a no-op (QColor.name()'s default HexRgb format drops alpha),
so the "translucent" edges/text those calls asked for always rendered fully
opaque. Matplotlib color args accept real (r, g, b, a) tuples, so this port
makes that alpha genuinely apply via `_mpl_rgba` instead of reproducing a
Qt-only accident.

This file must stay Qt-free forever - it exists to be importable from
backend/, which graphlink_app/tests/test_no_qt_anywhere.py holds to zero
tolerance for backend/.
"""

from __future__ import annotations

import colorsys
import math
import textwrap
from collections import defaultdict, deque
from io import BytesIO
from statistics import mean, median
from typing import Any

import matplotlib
matplotlib.use("Agg")
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch, PathPatch
from matplotlib.path import Path as MplPath

from graphlink_chart_data import SUPPORTED_CHART_TYPES, canonicalize_chart_data

# -- dark-theme hex palette --------------------------------------------------
# Copied verbatim from graphlink_config.py's THEME_TOKENS["dark"] (the app's
# CURRENT_THEME default) - "surface" tokens window/inset_deep/divider/
# text_bright/text_label, and "palette" tokens ai_node/user_node/selection.
SURFACE = "#1E1E1E"       # surface.window
PANEL = "#121212"         # surface.inset_deep
BORDER = "#424242"        # surface.divider
TEXT = "#FFFFFF"          # surface.text_bright
MUTED = "#A4A4A4"         # surface.text_label
GRID = "#A4A4A4"          # surface.text_label (legacy reuses text_label for grid too)
PRIMARY = "#828282"       # palette.ai_node
SECONDARY = "#838383"     # palette.user_node
SELECTION = "#858585"     # palette.selection
SLATE = "#868686"         # legacy's own hardcoded QColor("#868686") cycle entry

FONT_FAMILY = "Segoe UI"  # graphlink_styles.FONT_FAMILY_NAME

# -- figure sizing ------------------------------------------------------------
# Legacy rendered at a device-pixel-ratio-scaled resolution above the CSS box
# size for crispness (ChartItem._device_pixel_ratio defaulted to 1.5 with no
# live QScreen attached, the same "no live view" situation this headless
# backend is always in) - BASE_DPI reproduces that same ~1.5x density at
# dpi_scale=1.0. dpi_scale then multiplies on top for the 3x export render.
CSS_PX_PER_INCH = 96.0
BASE_DPI = 144.0  # 96 * 1.5
MIN_FIGURE_WIDTH_PX = 200.0
MIN_FIGURE_HEIGHT_PX = 140.0


# -- hex color helpers (Qt-free QColor replacements) -------------------------


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    text = hex_color.lstrip("#")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    clamped = [max(0, min(255, int(round(c)))) for c in rgb]
    return "#{:02X}{:02X}{:02X}".format(*clamped)


def _blend_colors(color_a: str, color_b: str, ratio: float) -> str:
    """Linear per-channel blend in 0-255 RGB space - port of ChartItem.
    _blend_colors, which operated on QColor's plain int red()/green()/blue()
    (no gamma correction), reproduced exactly here on hex-decoded ints."""
    ratio = max(0.0, min(1.0, ratio))
    inv = 1.0 - ratio
    ra, ga, ba = _hex_to_rgb(color_a)
    rb, gb, bb = _hex_to_rgb(color_b)
    return _rgb_to_hex((ra * inv + rb * ratio, ga * inv + gb * ratio, ba * inv + bb * ratio))


def _lighten(hex_color: str, factor: int = 130) -> str:
    """Port of QColor.lighter(factor): convert to HSV, scale V by
    factor/100, convert back - matches Qt's own documented algorithm."""
    r, g, b = (channel / 255.0 for channel in _hex_to_rgb(hex_color))
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    v = min(1.0, v * (factor / 100.0))
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return _rgb_to_hex((r * 255.0, g * 255.0, b * 255.0))


def _mpl_rgba(hex_color: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    """Port of ChartItem._mpl_rgba - a hex color plus a real, honored alpha
    (0-1), as a Matplotlib-native RGBA tuple."""
    r, g, b = (channel / 255.0 for channel in _hex_to_rgb(hex_color))
    return (r, g, b, alpha)


def _build_theme() -> dict[str, Any]:
    """Port of ChartItem._build_theme - identical derivation (accent/
    tertiary/cycle blends), operating on hex strings instead of QColor."""
    accent = _blend_colors(PRIMARY, SECONDARY, 0.55)
    tertiary = _blend_colors(PRIMARY, SELECTION, 0.50)
    cycle = [
        PRIMARY,
        SECONDARY,
        accent,
        tertiary,
        _blend_colors(PRIMARY, SECONDARY, 0.35),
        _blend_colors(SECONDARY, SELECTION, 0.40),
        _lighten(PRIMARY, 130),
        SLATE,
    ]
    return {
        "surface": SURFACE,
        "panel": PANEL,
        "border": BORDER,
        "text": TEXT,
        "muted": MUTED,
        "grid": GRID,
        "primary": PRIMARY,
        "secondary": SECONDARY,
        "accent": accent,
        "selection": SELECTION,
        "cycle": cycle,
    }


# -- formatting helpers (verbatim ports) --------------------------------------


def _format_value(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value - round(value)) < 0.001:
        return f"{value:,.0f}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _wrap_label(text: Any, width: int = 14, max_lines: int = 2) -> str:
    clean_text = " ".join(str(text).split())
    if not clean_text:
        return ""
    lines = textwrap.wrap(clean_text, width=width, break_long_words=False, break_on_hyphens=False)
    if not lines:
        return clean_text
    if len(lines) > max_lines:
        kept = lines[: max_lines - 1]
        remainder = " ".join(lines[max_lines - 1 :])
        kept.append(textwrap.shorten(remainder, width=width, placeholder="..."))
        lines = kept
    return "\n".join(lines)


def _prepare_standard_axes(
    ax, theme: dict[str, Any], x_label: str = "", y_label: str = "", x_grid: bool = False, y_grid: bool = True
) -> None:
    ax.set_facecolor(theme["surface"])
    ax.tick_params(colors=theme["muted"], labelsize=8)
    for side in ax.spines.values():
        side.set_color(theme["border"])
        side.set_linewidth(1.0)
    if y_grid:
        ax.grid(axis="y", color=_mpl_rgba(theme["grid"], 0.18), linewidth=0.8, linestyle="--")
    if x_grid:
        ax.grid(axis="x", color=_mpl_rgba(theme["grid"], 0.18), linewidth=0.8, linestyle="--")
    ax.set_axisbelow(True)
    if x_label:
        ax.set_xlabel(x_label, fontsize=9, color=theme["muted"], labelpad=10)
    if y_label:
        ax.set_ylabel(y_label, fontsize=9, color=theme["muted"], labelpad=8)


def _set_categorical_ticks(ax, positions: list[int], labels: list[str], layout_width: float) -> None:
    """Show a readable subset of labels while retaining every data point.
    Port of ChartItem._set_categorical_ticks - `layout_width` stands in for
    the legacy `self._chart_rect().width()` (there is no QGraphicsItem/scene
    geometry here); the caller passes the requested chart width in CSS px,
    the same order of magnitude the legacy panel width was."""
    if not positions:
        return
    max_ticks = max(4, min(12, int(max(1.0, layout_width) / 72)))
    stride = max(1, math.ceil(len(positions) / max_ticks))
    tick_indexes = list(range(0, len(positions), stride))
    if tick_indexes[-1] != len(positions) - 1:
        tick_indexes.append(len(positions) - 1)
    tick_positions = [positions[index] for index in tick_indexes]
    tick_labels = [_wrap_label(labels[index], 14, 2) for index in tick_indexes]
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(
        tick_labels,
        rotation=25 if len(tick_indexes) > 5 else 0,
        ha="right" if len(tick_indexes) > 5 else "center",
    )


# -- per-type renderers (verbatim ports of the 5 ChartItem._render_* methods) -


def _render_bar_chart(ax, chart_data: dict[str, Any], theme: dict[str, Any], layout_width: float) -> bool:
    values = chart_data["values"]
    labels = chart_data["labels"]
    positions = list(range(len(values)))
    colors = [theme["cycle"][index % len(theme["cycle"])] for index in range(len(values))]

    bars = ax.bar(
        positions,
        values,
        width=0.62,
        color=colors,
        edgecolor=_mpl_rgba(theme["text"], 40 / 255),
        linewidth=0.8,
        zorder=3,
    )
    _prepare_standard_axes(ax, theme, chart_data["xAxis"], chart_data["yAxis"], y_grid=True)
    _set_categorical_ticks(ax, positions, labels, layout_width)
    ax.margins(x=0.04)

    min_value = min(values)
    max_value = max(values)
    if min_value >= 0:
        ax.set_ylim(0, max_value * 1.18 if max_value else 1)
    else:
        buffer = (max_value - min_value) * 0.12 or 1
        ax.set_ylim(min_value - buffer, max_value + buffer)
        ax.axhline(0, color=_mpl_rgba(theme["text"], 90 / 255), linewidth=1.0)

    if len(values) <= 10:
        for bar, value in zip(bars, values):
            va = "bottom" if value >= 0 else "top"
            offset = 4 if value >= 0 else -4
            ax.annotate(
                _format_value(value),
                (bar.get_x() + (bar.get_width() / 2), value),
                textcoords="offset points",
                xytext=(0, offset),
                ha="center",
                va=va,
                color=theme["text"],
                fontsize=8,
            )
    return True


def _render_line_chart(ax, chart_data: dict[str, Any], theme: dict[str, Any], layout_width: float) -> bool:
    values = chart_data["values"]
    labels = chart_data["labels"]
    positions = list(range(len(values)))
    baseline = min(0, min(values))

    _prepare_standard_axes(ax, theme, chart_data["xAxis"], chart_data["yAxis"], y_grid=True)
    ax.plot(
        positions,
        values,
        color=theme["primary"],
        linewidth=2.8,
        marker="o",
        markersize=6,
        markerfacecolor=theme["surface"],
        markeredgewidth=1.6,
        markeredgecolor=theme["text"],
        zorder=4,
    )
    ax.fill_between(
        positions,
        values,
        baseline,
        color=_mpl_rgba(theme["primary"], 0.15),
        zorder=2,
    )
    _set_categorical_ticks(ax, positions, labels, layout_width)
    ax.margins(x=0.04)

    value_span = max(values) - min(values)
    buffer = (value_span * 0.18) or max(abs(max(values)), 1) * 0.18 or 1
    ax.set_ylim(min(values) - buffer, max(values) + buffer)
    if len(values) <= 12:
        for x_pos, value in zip(positions, values):
            ax.annotate(
                _format_value(value),
                (x_pos, value),
                textcoords="offset points",
                xytext=(0, 8),
                ha="center",
                color=theme["text"],
                fontsize=8,
            )
    return True


def _render_pie_chart(figure, ax, chart_data: dict[str, Any], theme: dict[str, Any]) -> bool:
    values = chart_data["values"]
    labels = chart_data["labels"]
    if len(values) > 10:
        ranked = sorted(zip(values, labels), reverse=True)
        top = ranked[:9]
        remainder = sum(value for value, _ in ranked[9:])
        values = [value for value, _ in top] + [remainder]
        labels = [label for _, label in top] + ["Other"]
    colors = [theme["cycle"][index % len(theme["cycle"])] for index in range(len(values))]
    total = sum(values)

    figure.subplots_adjust(left=0.06, right=0.66, top=0.92, bottom=0.08)
    wedges, _ = ax.pie(
        values,
        startangle=90,
        counterclock=False,
        labels=None,
        colors=colors,
        wedgeprops={"width": 0.38, "edgecolor": theme["surface"], "linewidth": 2},
    )

    ax.text(
        0,
        0.05,
        _format_value(total),
        ha="center",
        va="center",
        color=theme["text"],
        fontsize=16,
        fontweight="bold",
    )
    ax.text(
        0,
        -0.16,
        "Total",
        ha="center",
        va="center",
        color=theme["muted"],
        fontsize=9,
    )

    legend_labels = []
    for label, value in zip(labels, values):
        percentage = (value / total) * 100 if total else 0
        wrapped = textwrap.shorten(" ".join(str(label).split()), width=24, placeholder="...")
        legend_labels.append(f"{wrapped}  {percentage:.1f}%")

    legend = ax.legend(
        wedges,
        legend_labels,
        loc="center left",
        bbox_to_anchor=(1.01, 0.5),
        frameon=False,
        fontsize=8,
        handlelength=1.2,
        handletextpad=0.6,
    )
    for text in legend.get_texts():
        text.set_color(theme["text"])

    ax.set_aspect("equal")
    ax.set_facecolor(theme["surface"])
    return False


def _render_histogram(ax, chart_data: dict[str, Any], theme: dict[str, Any]) -> bool:
    values = chart_data["values"]
    bins = chart_data["bins"]

    counts, _, patches = ax.hist(
        values,
        bins=bins,
        linewidth=1.0,
        edgecolor=_mpl_rgba(theme["text"], 60 / 255),
        zorder=3,
    )
    for index, patch in enumerate(patches):
        color = theme["cycle"][index % len(theme["cycle"])]
        patch.set_facecolor(color)
        patch.set_alpha(0.82)

    avg = mean(values)
    med = median(values)
    _prepare_standard_axes(ax, theme, chart_data["xAxis"], chart_data["yAxis"], y_grid=True)
    ax.axvline(
        avg,
        color=theme["primary"],
        linewidth=1.6,
        linestyle="--",
        label=f"Mean {_format_value(avg)}",
        zorder=4,
    )
    ax.axvline(
        med,
        color=theme["secondary"],
        linewidth=1.6,
        linestyle="-.",
        label=f"Median {_format_value(med)}",
        zorder=4,
    )
    legend = ax.legend(frameon=False, fontsize=8, loc="upper right")
    for text in legend.get_texts():
        text.set_color(theme["muted"])
    ax.set_ylim(0, (max(counts) * 1.18) if len(counts) and max(counts) else 1)
    return True


def _render_sankey_chart(figure, ax, chart_data: dict[str, Any], theme: dict[str, Any]) -> bool:
    flows = chart_data["flows"]
    incoming = defaultdict(list)
    outgoing = defaultdict(list)
    indegree = defaultdict(int)
    nodes = set()

    for flow in flows:
        source = flow["source"]
        target = flow["target"]
        nodes.add(source)
        nodes.add(target)
        outgoing[source].append(flow)
        incoming[target].append(flow)
        indegree[target] += 1
        indegree.setdefault(source, 0)

    levels: dict[str, int] = {}
    queue = deque(sorted(node for node in nodes if indegree[node] == 0))
    for node in queue:
        levels[node] = 0

    while queue:
        node = queue.popleft()
        for flow in outgoing[node]:
            target = flow["target"]
            levels[target] = max(levels.get(target, 0), levels[node] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)

    for node in nodes:
        if node not in levels:
            levels[node] = max((levels.get(flow["source"], 0) + 1 for flow in incoming[node]), default=0)

    column_map = defaultdict(list)
    node_weights = {}
    for node in nodes:
        node_weights[node] = max(
            sum(flow["value"] for flow in outgoing[node]),
            sum(flow["value"] for flow in incoming[node]),
            1.0,
        )
        column_map[levels[node]].append(node)

    column_gap = 0.03
    chart_height = 0.82
    available_columns = max(column_map.keys()) + 1 if column_map else 1
    global_scale = None
    for column_nodes in column_map.values():
        total_weight = sum(node_weights[node] for node in column_nodes)
        available_height = chart_height - (column_gap * max(0, len(column_nodes) - 1))
        if total_weight <= 0 or available_height <= 0:
            continue
        column_scale = available_height / total_weight
        global_scale = column_scale if global_scale is None else min(global_scale, column_scale)
    scale = global_scale or 0.04

    x_start = 0.08
    x_end = 0.92
    step = (x_end - x_start) / max(1, available_columns - 1)
    node_width = min(0.05, step * 0.28)
    node_layout = {}

    for level in sorted(column_map.keys()):
        column_nodes = sorted(column_map[level], key=lambda node: (-node_weights[node], node.lower()))
        total_height = sum(node_weights[node] * scale for node in column_nodes)
        total_height += column_gap * max(0, len(column_nodes) - 1)
        current_y = 0.5 - (total_height / 2)
        for node in column_nodes:
            height = node_weights[node] * scale
            node_layout[node] = {
                "x": x_start + (level * step),
                "y": current_y,
                "width": node_width,
                "height": height,
                "level": level,
            }
            current_y += height + column_gap

    outgoing_cursor = {node: layout["y"] for node, layout in node_layout.items()}
    incoming_cursor = {node: layout["y"] for node, layout in node_layout.items()}
    max_level = max((layout["level"] for layout in node_layout.values()), default=0)

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.set_facecolor(theme["surface"])
    figure.subplots_adjust(left=0.02, right=0.98, top=0.94, bottom=0.05)

    sorted_flows = sorted(
        flows,
        key=lambda flow: (
            node_layout[flow["source"]]["level"],
            node_layout[flow["source"]]["y"],
            node_layout[flow["target"]]["y"],
        ),
    )

    for flow in sorted_flows:
        source_layout = node_layout[flow["source"]]
        target_layout = node_layout[flow["target"]]
        thickness = max(flow["value"] * scale, 0.008)

        start_y0 = outgoing_cursor[flow["source"]]
        start_y1 = start_y0 + thickness
        outgoing_cursor[flow["source"]] += thickness

        end_y0 = incoming_cursor[flow["target"]]
        end_y1 = end_y0 + thickness
        incoming_cursor[flow["target"]] += thickness

        x0 = source_layout["x"] + source_layout["width"]
        x1 = target_layout["x"]
        control = max(0.05, (x1 - x0) * 0.45)
        source_color = theme["cycle"][source_layout["level"] % len(theme["cycle"])]
        target_color = theme["cycle"][target_layout["level"] % len(theme["cycle"])]
        flow_color = _blend_colors(source_color, target_color, 0.5)

        path = MplPath(
            [
                (x0, start_y0),
                (x0 + control, start_y0),
                (x1 - control, end_y0),
                (x1, end_y0),
                (x1, end_y1),
                (x1 - control, end_y1),
                (x0 + control, start_y1),
                (x0, start_y1),
                (x0, start_y0),
            ],
            [
                MplPath.MOVETO,
                MplPath.CURVE4,
                MplPath.CURVE4,
                MplPath.CURVE4,
                MplPath.LINETO,
                MplPath.CURVE4,
                MplPath.CURVE4,
                MplPath.CURVE4,
                MplPath.CLOSEPOLY,
            ],
        )
        ax.add_patch(
            PathPatch(
                path,
                facecolor=_mpl_rgba(flow_color, 0.50),
                edgecolor=_mpl_rgba(flow_color, 0.12),
                linewidth=0.6,
                zorder=1,
            )
        )

    for node, layout in node_layout.items():
        node_color = theme["cycle"][layout["level"] % len(theme["cycle"])]
        ax.add_patch(
            FancyBboxPatch(
                (layout["x"], layout["y"]),
                layout["width"],
                layout["height"],
                boxstyle="round,pad=0.003,rounding_size=0.01",
                facecolor=node_color,
                edgecolor=_mpl_rgba(theme["text"], 70 / 255),
                linewidth=1.0,
                zorder=3,
            )
        )

        is_last_column = layout["level"] == max_level
        label_x = layout["x"] - 0.012 if is_last_column else layout["x"] + layout["width"] + 0.012
        alignment = "right" if is_last_column else "left"
        ax.text(
            label_x,
            layout["y"] + (layout["height"] / 2),
            _wrap_label(node, 16, 3),
            ha=alignment,
            va="center",
            color=theme["text"],
            fontsize=8,
            zorder=4,
        )
    return False


def _render_error_image(figure, theme: dict[str, Any], message: str) -> None:
    """Port of ChartItem._render_error_image - the never-blank placeholder
    for malformed/unrenderable chart_data (test_chart_render_is_bounded_for_
    dense_labels_and_invalid_data_gets_placeholder's contract)."""
    figure.clear()
    figure.patch.set_facecolor(theme["surface"])
    ax = figure.add_subplot(111)
    ax.set_facecolor(theme["surface"])
    ax.axis("off")
    ax.text(
        0.5,
        0.58,
        "Chart rendering issue",
        ha="center",
        va="center",
        color=theme["text"],
        fontsize=13,
        fontweight="bold",
    )
    ax.text(
        0.5,
        0.42,
        message,
        ha="center",
        va="center",
        color=_mpl_rgba(theme["text"], 180 / 255),
        fontsize=9,
        wrap=True,
    )
    figure.subplots_adjust(left=0.06, right=0.94, top=0.92, bottom=0.10)


def _render_chart_content(figure, chart_type: str, chart_data: dict[str, Any], theme: dict[str, Any], layout_width: float) -> None:
    figure.clear()
    figure.patch.set_facecolor(theme["surface"])
    ax = figure.add_subplot(111)
    canonical = canonicalize_chart_data(chart_data, chart_type)

    if chart_type == "bar":
        use_tight_layout = _render_bar_chart(ax, canonical, theme, layout_width)
    elif chart_type == "line":
        use_tight_layout = _render_line_chart(ax, canonical, theme, layout_width)
    elif chart_type == "pie":
        use_tight_layout = _render_pie_chart(figure, ax, canonical, theme)
    elif chart_type == "histogram":
        use_tight_layout = _render_histogram(ax, canonical, theme)
    elif chart_type == "sankey":
        use_tight_layout = _render_sankey_chart(figure, ax, canonical, theme)
    else:
        # Unreachable in practice - render_chart_png already validates
        # chart_type against SUPPORTED_CHART_TYPES before ever calling this.
        # Kept as a defensive mirror of legacy's own dead `else: raise` branch.
        raise ValueError(f"Unsupported chart type: {chart_type}")

    if use_tight_layout:
        figure.tight_layout(pad=1.25)


def render_chart_png(chart_type: str, chart_data: dict[str, Any], width: float, height: float, dpi_scale: float = 1.0) -> bytes:
    """Render one chart to PNG bytes. Port of ChartItem.generate_chart +
    _render_chart_to_image + _figure_to_image, minus the render cache (no
    long-lived widget here to cache against) and minus the card chrome (see
    module docstring).

    `width`/`height` are the desired on-screen box size in CSS px (the
    node's chart_width/chart_height); `dpi_scale` multiplies the base render
    density (1.0 for normal display/resize, 3.0 for the export endpoint -
    mirrors legacy's own EXPORT_SCALE).

    Never raises for a malformed `chart_data` under a KNOWN chart_type -
    canonicalize_chart_data's ChartDataError (or any other exception a
    renderer hits) is caught and swapped for a placeholder "Chart rendering
    issue" image instead, exactly as ChartItem.generate_chart never crashes
    on bad data. DOES raise ValueError for a `chart_type` outside
    SUPPORTED_CHART_TYPES - there is no such thing as a placeholder chart
    TYPE, only placeholder chart DATA."""
    normalized_type = str(chart_type or "").strip().lower()
    if normalized_type not in SUPPORTED_CHART_TYPES:
        raise ValueError(f"Unsupported chart type: {normalized_type or 'unknown'}")

    dpi = BASE_DPI * max(1.0, float(dpi_scale))
    fig_width_in = max(MIN_FIGURE_WIDTH_PX, float(width)) / CSS_PX_PER_INCH
    fig_height_in = max(MIN_FIGURE_HEIGHT_PX, float(height)) / CSS_PX_PER_INCH

    figure = Figure(figsize=(fig_width_in, fig_height_in), dpi=dpi)
    canvas = FigureCanvasAgg(figure)
    theme = _build_theme()

    try:
        with matplotlib.rc_context({"font.family": [FONT_FAMILY]}):
            _render_chart_content(figure, normalized_type, chart_data, theme, float(width))
    except Exception as exc:  # noqa: BLE001 - intentional catch-all, see docstring
        _render_error_image(figure, theme, str(exc))

    canvas.draw()
    buffer = BytesIO()
    figure.savefig(buffer, format="png", dpi=dpi, facecolor=theme["surface"])
    return buffer.getvalue()
