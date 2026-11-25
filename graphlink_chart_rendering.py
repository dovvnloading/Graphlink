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
backend/, which tests/test_no_qt_anywhere.py holds to zero tolerance for
backend/.
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
from matplotlib.colors import Colormap, LinearSegmentedColormap, Normalize, TwoSlopeNorm
from matplotlib.patches import FancyBboxPatch, PathPatch
from matplotlib.path import Path as MplPath

from graphlink_chart_data import (SUPPORTED_CHART_TYPES, canonicalize_chart_data, chart_regression_fits,
                                  raincloud_density, raincloud_jitter, heatmap_cluster_plan)

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

# Okabe-Ito colors are distinguishable for common color-vision deficiencies.
# Grayscale relies on hatches, markers and dashes as well as luminance.
PUBLICATION_COLORS = (
    "#0072B2", "#E69F00", "#009E73", "#CC79A7",
    "#D55E00", "#56B4E9", "#F0E442", "#000000",
)
# Standard composite figures share the same eight distinct colors as the live SVG.
COMPOSITE_COLORS = (
    "#3f8f5c", "#3f7dc9", "#8a5fd1", "#d98a3d",
    "#cf5354", "#d9b23d", "#168b91", "#bc568e",
)
MONOCHROME_COLORS = ("#202020", "#5A5A5A", "#878787", "#B6B6B6")
PUBLICATION_HATCHES = ("/", "\\\\", "xx", "--", "++", "oo", "..", "**")


def _publication_theme(*, colorblind_safe: bool, grayscale_safe: bool) -> dict[str, Any]:
    theme = _build_theme()
    theme.update({
        "surface": "#FFFFFF",
        "panel": "#FFFFFF",
        "border": "#555555",
        "text": "#151515",
        "muted": "#303030",
        "grid": "#787878",
        "primary": "#202020" if grayscale_safe else "#0072B2",
        "secondary": "#626262" if grayscale_safe else "#D55E00",
        "publication": True,
        "colorblind": colorblind_safe,
        "grayscale": grayscale_safe,
    })
    if grayscale_safe:
        theme["cycle"] = list(MONOCHROME_COLORS)
    elif colorblind_safe:
        theme["cycle"] = list(PUBLICATION_COLORS)
    else:
        # Preserve Graphlink's original series colors when no alternative
        # is requested, but on a white journal page.
        theme["cycle"] = list(_build_theme()["cycle"])
    return theme

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
    if theme.get("grayscale"):
        for index, bar in enumerate(bars):
            bar.set_hatch(PUBLICATION_HATCHES[index % len(PUBLICATION_HATCHES)])
            bar.set_edgecolor(theme["text"])
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



def _render_scientific_cartesian(ax, data: dict[str, Any], theme: dict[str, Any],
                                  layout_width: float, chart_type: str) -> bool:
    """Render exactly the validated numeric coordinates and error intervals.

    This intentionally does not replace _render_line_chart: a saved v1
    categorical line must keep its historical fill, ticks and labels.
    Both PNG and SVG call this same function via _render_chart_content.
    """
    x_values = data.get("xValues")
    rows = data.get("series")
    if rows is None:
        rows = [{"values": data["values"], "xError": data.get("xError"),
                 "yError": data.get("yError")}]
    if x_values is None:
        x_values = list(range(len(rows[0]["values"])))

    def error_bounds(errors):
        if errors is None:
            return None
        return ([pair[0] for pair in errors], [pair[1] for pair in errors])

    def axis_label(axis):
        label = data[axis + "Axis"]
        unit = data.get(axis + "Unit")
        return f"{label} ({unit})" if unit else label

    _prepare_standard_axes(
        ax, theme, axis_label("x"), axis_label("y"), y_grid=True,
    )
    if data.get("xScale") == "log10":
        ax.set_xscale("log", base=10)
    if data.get("yScale") == "log10":
        ax.set_yscale("log", base=10)

    # Markers and line styles distinguish series even if two dark-theme
    # palette entries have very similar RGB values.
    markers = ("o", "s", "^", "D", "v", "P", "X", "*")
    styles = ("-", "--", "-.", ":")
    density = data.get("analysis", {}).get("density") == "hexbin"
    if density:
        # A saved locked viewport is the only user viewport persisted into
        # publication export. Match the interactive hexbin's X/Y inclusion
        # filter exactly; offscreen observations must not contribute counts
        # or expand the export's tessellation.
        analysis = data.get("analysis", {})
        xrange = analysis.get("xRange") if analysis.get("lockX") else None
        yrange = analysis.get("yRange") if analysis.get("lockY") else None
        visible = [
            (x, y) for x, y in zip(x_values, rows[0]["values"])
            if (xrange is None or xrange[0] <= x <= xrange[1])
            and (yrange is None or yrange[0] <= y <= yrange[1])
        ]
        if visible:
            hx, hy = zip(*visible)
            cmap = LinearSegmentedColormap.from_list(
                "graphlink-hexbin", [theme["surface"], theme["cycle"][0]]
            )
            cloud = ax.hexbin(hx, hy, gridsize=26, mincnt=1,
                              cmap=cmap, linewidths=0.25,
                              edgecolors=theme["surface"], zorder=2)
            colorbar = ax.figure.colorbar(cloud, ax=ax, fraction=0.046, pad=0.04)
            colorbar.ax.tick_params(colors=theme["muted"], labelsize=8)
            colorbar.set_label("Observations per bin", color=theme["muted"], fontsize=8)
        else:
            ax.text(0.5, 0.5, "No observations in selected range",
                    transform=ax.transAxes, ha="center", va="center",
                    color=theme["muted"], fontsize=9)
        ax.set_title("Hexbin density (count per visible cell)", fontsize=8,
                     color=theme["text"], loc="left")
    fits = chart_regression_fits(data) if data.get("analysis", {}).get("regression") == "ols95" else None
    for index, row in enumerate(rows):
        color = theme["cycle"][index % len(theme["cycle"])]
        label = row.get("name")
        if fits:
            fit = fits[index]
            fx, fy, lower, upper = zip(*fit["points"])
            summary = f"R²={fit['r2']:.3f}" if fit["r2"] is not None else "R² undefined"
            fit_label = f"{label + ': ' if label else ''}OLS ({summary}, n={fit['n']})"
            ax.fill_between(fx, lower, upper, color=color, alpha=0.18, zorder=1,
                            label="95% mean CI" if index == 0 else "_nolegend_")
            ax.plot(fx, fy, color=color, linestyle="--", linewidth=1.5, zorder=3, label=fit_label)
        if density:
            continue
        ax.errorbar(
            x_values,
            row["values"],
            xerr=error_bounds(row.get("xError")),
            yerr=error_bounds(row.get("yError")),
            fmt=markers[index % len(markers)],
            linestyle=styles[index % len(styles)] if chart_type == "line" else "none",
            linewidth=2.0,
            markersize=6,
            color=color,
            markerfacecolor=theme["surface"],
            markeredgecolor=color,
            markeredgewidth=1.5,
            ecolor=color,
            elinewidth=1.35,
            capsize=3.5,
            capthick=1.2,
            label=label,
            zorder=4 + index,
        )
    if (data.get("series") and not density) or fits:
        legend = ax.legend(frameon=False, loc="best", fontsize=8)
        for text in legend.get_texts():
            text.set_color(theme["text"])

    if data.get("xValues") is None:
        _set_categorical_ticks(ax, list(x_values), data["labels"], layout_width)
    # Keep all interval endpoints visible, on both linear and log axes.
    # matplotlib's dataLim accounts for errorbar cap endpoints; no silent
    # clipping, filtering or arbitrary domain transforms are introduced.
    ax.margins(x=0.08, y=0.16)
    return True


def _boxplot_statistics(values):
    """Tukey median-of-halves quartiles, 1.5-IQR whiskers and explicit outliers.

    Unlike pyplot.boxplot, calculating the stats here makes the PNG/SVG
    exporter and the interactive SVG use the same definition for odd samples.
    """
    ordered = sorted(values)
    mid = len(ordered) // 2

    def stable_median(sorted_values):
        """Avoid statistics.median's (a+b)/2 overflow on finite samples."""
        center = len(sorted_values) // 2
        if len(sorted_values) % 2:
            return sorted_values[center]
        low, high = sorted_values[center - 1], sorted_values[center]
        # Use a midpoint that does not overflow near the float ceiling or
        # underflow when both observations are tiny and equal.
        difference = high - low
        return (low + difference / 2 if math.isfinite(difference)
                else low / 2 + high / 2)

    q1 = stable_median(ordered[:mid])
    q3 = stable_median(ordered[mid + (len(ordered) % 2):])
    med = stable_median(ordered)
    fence = 1.5 * (q3 - q1)
    lower, upper = q1 - fence, q3 + fence
    inliers = [value for value in ordered if lower <= value <= upper]
    return {
        "med": med, "q1": q1, "q3": q3,
        "whislo": inliers[0], "whishi": inliers[-1],
        "fliers": [value for value in ordered if value < lower or value > upper],
    }


def _render_boxplot(ax, chart_data: dict[str, Any], theme: dict[str, Any],
                    layout_width: float) -> bool:
    groups = chart_data["groups"]
    entries = [dict(label=group["label"], **_boxplot_statistics(group["values"]))
               for group in groups]
    raincloud = chart_data.get("analysis", {}).get("distribution") == "raincloud"
    if raincloud:
        for index, group in enumerate(groups):
            color = theme["cycle"][index % len(theme["cycle"])]
            density = raincloud_density(group["values"])
            if density:
                values, relative = zip(*density)
                ax.fill_betweenx(values, index - 0.12, [index - 0.12 - 0.32 * d for d in relative],
                                 color=color, alpha=0.35, linewidth=0.8, edgecolor=color)
            ax.scatter([index + raincloud_jitter(j) for j in range(len(group["values"]))],
                       group["values"], s=13, color=color, alpha=0.8, edgecolors=theme["text"],
                       linewidths=0.35, zorder=4)
    artists = ax.bxp(
        entries, positions=list(range(len(groups))), widths=0.14 if raincloud else 0.56,
        patch_artist=True, showfliers=not raincloud, manage_ticks=False,
        boxprops={"edgecolor": theme["text"], "linewidth": 1.1},
        medianprops={"color": theme["text"], "linewidth": 2.0},
        whiskerprops={"color": theme["muted"], "linewidth": 1.3},
        capprops={"color": theme["muted"], "linewidth": 1.3},
        flierprops={"marker": "o", "markersize": 4,
                    "markerfacecolor": "none", "markeredgecolor": theme["text"]},
    )
    for index, patch in enumerate(artists["boxes"]):
        patch.set_facecolor(theme["cycle"][index % len(theme["cycle"])])
        patch.set_alpha(0.75)
        if theme.get("grayscale"):
            patch.set_hatch(PUBLICATION_HATCHES[index % len(PUBLICATION_HATCHES)])
    _prepare_standard_axes(ax, theme, chart_data["xAxis"], chart_data["yAxis"])
    _set_categorical_ticks(ax, list(range(len(groups))), [group["label"] for group in groups], layout_width)
    if raincloud:
        ax.set_xlim(-0.5, len(groups) - 0.5)
        ax.set_title("Raincloud: density (relative peak), quartiles, raw observations",
                     fontsize=8, color=theme["text"], loc="left")
    ax.margins(x=0.07, y=0.16)
    return True


def _render_heatmap(figure, ax, chart_data: dict[str, Any],
                    theme: dict[str, Any], layout_width: float) -> bool:
    mode = chart_data.get("analysis", {}).get("clustering")
    plan = heatmap_cluster_plan(chart_data["matrix"], mode)
    rows = [chart_data["rowLabels"][i] for i in plan["rowOrder"]]
    columns = [chart_data["columnLabels"][i] for i in plan["columnOrder"]]
    matrix = [[chart_data["matrix"][i][j] for j in plan["columnOrder"]]
              for i in plan["rowOrder"]]
    smallest = min(min(row) for row in matrix)
    largest = max(max(row) for row in matrix)
    norm: Normalize
    cmap: Colormap
    if smallest < 0 < largest:
        colors = (["#0072B2", "#F7F7F7", "#D55E00"]
                  if theme.get("colorblind") else ["#2166AC", "#F7F7F7", "#B2182B"])
        cmap = LinearSegmentedColormap.from_list("graphlink-diverging", colors)
        norm = TwoSlopeNorm(vmin=smallest, vcenter=0, vmax=largest)
    else:
        cmap = LinearSegmentedColormap.from_list(
            "graphlink-sequential", ["#E5F5F9", "#0A6275"]
        )
        # A constant matrix should still produce a visible, uniform map.
        norm = Normalize(vmin=smallest, vmax=largest if largest != smallest else smallest + 1)
    if theme.get("grayscale"):
        cmap = matplotlib.colormaps["Greys"]
    elif theme.get("publication") and theme["cycle"][0] == PUBLICATION_COLORS[0]:
        cmap = matplotlib.colormaps["cividis"] if smallest >= 0 else cmap
    # pcolormesh keeps heatmap cells vector in SVG/PDF, including at 85 mm.
    # The cell centers align with imshow and the interactive SVG rectangles.
    if theme.get("publication"):
        image = ax.pcolormesh(
            [[value for value in row] for row in matrix], cmap=cmap, norm=norm,
            shading="nearest", rasterized=False, zorder=2,
        )
        ax.invert_yaxis()
    else:
        image = ax.imshow(matrix, aspect="auto", interpolation="nearest", origin="upper",
                          cmap=cmap, norm=norm, zorder=2)
    _prepare_standard_axes(ax, theme, chart_data["xAxis"], chart_data["yAxis"],
                           x_grid=False, y_grid=False)
    _set_categorical_ticks(ax, list(range(len(columns))), columns, layout_width)
    step = max(1, math.ceil(len(rows) / 12))
    shown = list(range(0, len(rows), step))
    if shown[-1] != len(rows) - 1:
        shown.append(len(rows) - 1)
    ax.set_yticks(shown, [_wrap_label(rows[i], 14, 2) for i in shown])
    # Draw deterministic UPGMA dendrograms outside the heatmap's cell
    # coordinates. Preserve identical ticks and physical unit values.
    def draw_tree(tree, order, axis):
        if not tree or "left" not in tree:
            return
        positions = {leaf: i for i, leaf in enumerate(order)}
        max_height = tree["height"]

        def visit(node):
            if "leaf" in node:
                return (-0.5, positions[node["leaf"]]) if axis == "rows" else (positions[node["leaf"]], -0.5)
            a, b = visit(node["left"]), visit(node["right"])
            ratio = (node["height"] / max_height if max_height > 0
                     else node["depth"] / tree["depth"])
            if axis == "rows":
                x = -0.6 - 1.8 * ratio
                ax.plot([a[0], x, x, b[0]], [a[1], a[1], b[1], b[1]],
                        color=theme["muted"], linewidth=0.75, zorder=5)
                return (x, (a[1] + b[1]) / 2)
            y = -0.6 - 1.8 * ratio
            ax.plot([a[0], a[0], b[0], b[0]], [a[1], y, y, b[1]],
                    color=theme["muted"], linewidth=0.75, zorder=5)
            return ((a[0] + b[0]) / 2, y)

        visit(tree)

    draw_tree(plan["rowTree"], plan["rowOrder"], "rows")
    draw_tree(plan["columnTree"], plan["columnOrder"], "columns")
    ax.set_xlim(-2.6 if plan["rowTree"] and plan["rowTree"].get("left") else -0.5,
                len(columns) - 0.5)
    ax.set_ylim(len(rows) - 0.5,
                -2.6 if plan["columnTree"] and plan["columnTree"].get("left") else -0.5)
    if mode:
        ax.set_title("Average-linkage clustered heatmap (Euclidean)", fontsize=8,
                     color=theme["text"], loc="left")
    colorbar = figure.colorbar(image, ax=ax, fraction=0.045, pad=0.04)
    colorbar.ax.tick_params(colors=theme["muted"], labelsize=8)
    colorbar.outline.set_edgecolor(theme["border"])
    if chart_data.get("valueUnit"):
        colorbar.set_label(chart_data["valueUnit"], color=theme["muted"], fontsize=9)
    return True


def _format_pie_value(value: float) -> str:
    if not math.isfinite(value):
        return ">1.79e308"
    if value >= 1e9 or 0 < value < 0.01:
        return f"{value:.2e}"
    return _format_value(value)


def _render_pie_chart(figure, ax, chart_data: dict[str, Any], theme: dict[str, Any]) -> bool:
    values = chart_data["values"]
    labels = chart_data["labels"]
    total = sum(values)
    # Matplotlib converts pie inputs to float32. Scale before summing or
    # collapsing Other, keeping its input finite even for extreme doubles.
    scale = max(values)
    weights = [value / scale for value in values]
    if len(values) > 10:
        # Stable value-only ordering matches the interactive renderer for ties.
        ranked = sorted(zip(weights, values, labels), key=lambda entry: entry[1], reverse=True)
        top = ranked[:9]
        weights = [weight for weight, _, _ in top] + [sum(weight for weight, _, _ in ranked[9:])]
        labels = [label for _, _, label in top] + ["Other"]
    colors = [theme["cycle"][index % len(theme["cycle"])] for index in range(len(weights))]
    weight_total = sum(weights)

    figure.subplots_adjust(left=0.06, right=0.66, top=0.92, bottom=0.08)
    wedges, _ = ax.pie(
        weights,
        startangle=90,
        counterclock=False,
        labels=None,
        colors=colors,
        wedgeprops={"width": 0.38, "edgecolor": theme["surface"], "linewidth": 2},
    )

    if theme.get("grayscale"):
        for index, wedge in enumerate(wedges):
            wedge.set_hatch(PUBLICATION_HATCHES[index % len(PUBLICATION_HATCHES)])
            wedge.set_edgecolor(theme["text"])

    ax.text(
        0,
        0.05,
        _format_pie_value(total),
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
    for label, weight in zip(labels, weights):
        share = weight / weight_total
        percentage = "<0.1%" if share < 0.001 else f"{share * 100:.1f}%"
        clean = " ".join(str(label).split())
        wrapped = (clean if theme.get("publication") else
                   textwrap.shorten(clean, width=24, placeholder="..."))
        legend_labels.append(f"{wrapped}  {percentage}")

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
        if theme.get("grayscale"):
            patch.set_hatch(PUBLICATION_HATCHES[index % len(PUBLICATION_HATCHES)])
            patch.set_edgecolor(theme["text"])

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
    # The `incoming`/`outgoing` defaultdicts above take their element type
    # from the .append() calls below; this one is only ever read through
    # augmented assignment, leaving the element type unclear.
    indegree: defaultdict[str, int] = defaultdict(int)
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

    # Normalize before summing to avoid overflowing finite flow values.
    # Keep these geometry rules in sync with sankeyLayout.ts.
    max_flow_value = max((flow["value"] for flow in flows), default=1.0) or 1.0
    column_map = defaultdict(list)
    node_weights = {}
    for node in nodes:
        node_weights[node] = max(
            sum(flow["value"] / max_flow_value for flow in outgoing[node]),
            sum(flow["value"] / max_flow_value for flow in incoming[node]),
        )
        column_map[levels[node]].append(node)

    chart_height = 0.82
    # Keep at least half the height available for nodes in the densest column.
    largest_column = max((len(column) for column in column_map.values()), default=1)
    column_gap = min(0.03, chart_height / (2 * max(1, largest_column - 1)))
    available_columns = max(column_map.keys()) + 1 if column_map else 1
    global_scale = None
    for column_nodes in column_map.values():
        total_weight = sum(node_weights[node] for node in column_nodes)
        available_height = chart_height - (column_gap * max(0, len(column_nodes) - 1))
        if total_weight <= 0 or available_height <= 0:
            continue
        column_scale = available_height / total_weight
        global_scale = column_scale if global_scale is None else min(global_scale, column_scale)
    scale = global_scale if global_scale is not None else 0.0

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
        # The stroke can aid visibility without inflating the filled area.
        thickness = (flow["value"] / max_flow_value) * scale

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
                boxstyle=f"round,pad=0,rounding_size={min(0.01, layout['width'] / 2, layout['height'] / 2)}",
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


def _render_chart_analysis(ax, analysis: dict[str, Any], theme: dict[str, Any]) -> None:
    """Apply persisted axis locks and vector annotations to publication artists.

    Bounds/coordinates have already been canonicalized (including log10
    positivity), and are in original data units, never CSS pixel space.
    """
    if analysis.get("lockX"):
        ax.set_xlim(*analysis["xRange"])
    if analysis.get("lockY"):
        ax.set_ylim(*analysis["yRange"])
    for item in analysis["annotations"]:
        kind = item["kind"]
        if kind == "region":
            if item["axis"] == "x":
                ax.axvspan(item["from"], item["to"], facecolor=theme["secondary"],
                           alpha=0.16, linewidth=0, zorder=0.5)
            else:
                ax.axhspan(item["from"], item["to"], facecolor=theme["secondary"],
                           alpha=0.16, linewidth=0, zorder=0.5)
        elif kind == "label":
            ax.text(item["x"], item["y"], item["text"], color=theme["text"],
                    fontsize=9, fontweight="semibold", zorder=12, clip_on=True,
                    bbox={"facecolor": theme["surface"], "alpha": 0.8,
                          "edgecolor": "none", "pad": 1.5})
        elif kind == "arrow":
            ax.annotate("", xy=(item["x2"], item["y2"]),
                        xytext=(item["x"], item["y"]),
                        arrowprops={"arrowstyle": "-|>", "color": theme["primary"],
                                    "lw": 1.7},
                        zorder=12, annotation_clip=True)


def _render_composite_chart(figure, data: dict[str, Any], theme: dict[str, Any]) -> None:
    """Render bounded V2 layers/facets from original named columns.

    No AI-generated expression or script executes here. Each mark is a
    reference to canonicalized arrays; every observation participates in
    the publication image. Panels share X/Y domains so comparisons cannot
    silently change scale across experimental groups.
    """
    spec = data["composite"]
    numbers = spec["numbers"]
    categories = spec["categories"]
    facet_field = spec.get("facetBy")
    facets = list(dict.fromkeys(categories[facet_field])) if facet_field else [None]
    columns = 1 if len(facets) == 1 else 2
    rows = math.ceil(len(facets) / columns)

    # Establish data-wide domains *before* faceting, including band upper
    # boundaries and the area baseline.
    x_values, y_values = [], []
    for layer in spec["layers"]:
        x_values.extend(numbers[layer["x"]])
        y_values.extend(numbers[layer["y"]])
        if layer.get("y2"):
            y_values.extend(numbers[layer["y2"]])
        if layer["mark"] == "area":
            y_values.append(0.0)

    # Match the live SVG's globally ordered field/value or ungrouped-layer
    # slots. Facets reuse the same identity even when a group is absent.
    color_slots = {}
    for layer_index, layer in enumerate(spec["layers"]):
        color_by = layer.get("colorBy")
        keys = [(color_by, value) for value in categories[color_by]] if color_by else [(layer_index,)]
        for key in keys:
            color_slots.setdefault(key, len(color_slots))
    palette = (MONOCHROME_COLORS if theme.get("grayscale") else
               PUBLICATION_COLORS if theme.get("colorblind") else COMPOSITE_COLORS)

    x_unit, y_unit = data.get("xUnit"), data.get("yUnit")
    xlabel = data["xAxis"] + (f" ({x_unit})" if x_unit else "")
    ylabel = data["yAxis"] + (f" ({y_unit})" if y_unit else "")
    figure.clear()
    axes = [
        figure.add_subplot(rows, columns, i + 1)
        for i in range(rows * columns)
    ]
    for i, facet in enumerate(facets):
        ax = axes[i]
        _prepare_standard_axes(ax, theme, xlabel, ylabel)
        if data.get("xScale") == "log10":
            ax.set_xscale("log", base=10)
        if data.get("yScale") == "log10":
            ax.set_yscale("log", base=10)
        present = [
            index for index in range(len(next(iter(numbers.values()))))
            if facet_field is None or categories[facet_field][index] == facet
        ]
        for layer_index, layer in enumerate(spec["layers"]):
            color_by = layer.get("colorBy")
            groups = (list(dict.fromkeys(categories[color_by][j] for j in present))
                      if color_by else [None])
            for group in groups:
                indices = [
                    j for j in present
                    if color_by is None or categories[color_by][j] == group
                ]
                indices.sort(key=lambda index: (numbers[layer["x"]][index], index))
                if not indices:
                    continue
                xx = [numbers[layer["x"]][j] for j in indices]
                yy = [numbers[layer["y"]][j] for j in indices]
                slot = color_slots[(color_by, group) if group is not None else (layer_index,)]
                color = palette[slot % len(palette)]
                label = layer.get("label") or layer["y"]
                if group is not None:
                    label = f"{label} · {color_by}: {group}"
                else:
                    label = f"Layer {layer_index + 1}: {label}"
                mark = layer["mark"]
                # In grayscale, reused luminance slots get different hatches,
                # line styles or marker shapes instead of relying on color alone.
                hatch = PUBLICATION_HATCHES[slot] if theme.get("grayscale") else None
                line_style = (("-", "--", "-.", ":")[(layer_index + slot // len(palette)) % 4]
                              if theme.get("grayscale") else ("-", "--", "-.", ":")[layer_index % 4])
                marker = (("o", "s", "^", "D", "v", "P", "X", "*")[slot]
                          if theme.get("grayscale") else ("o", "s", "^", "D")[layer_index % 4])
                if mark == "band":
                    upper = [numbers[layer["y2"]][j] for j in indices]
                    ax.fill_between(xx, yy, upper, facecolor=color, alpha=0.24,
                                    linewidth=0.8, edgecolor=color, hatch=hatch, label=label)
                elif mark == "area":
                    ax.fill_between(xx, 0, yy, facecolor=color, alpha=0.24,
                                    linewidth=0.8, edgecolor=color, hatch=hatch, label=label)
                elif mark == "line":
                    ax.plot(xx, yy, color=color, linestyle=line_style, linewidth=1.8, label=label)
                else:
                    ax.scatter(xx, yy, s=19, color=color, alpha=0.87,
                               marker=marker, label=label, zorder=4)
        if facet is not None:
            ax.set_title(f"{facet_field}: {facet}", fontsize=9, color=theme["text"])
        if data.get("xScale") == "log10":
            xmin, xmax = min(x_values), max(x_values)
            if xmin == xmax:
                xmin, xmax = xmin / 1.2, xmax * 1.2
        else:
            xmin, xmax = min(x_values), max(x_values)
            if xmin == xmax:
                delta = max(abs(xmin) * 0.05, 1)
                xmin, xmax = xmin - delta, xmax + delta
        if data.get("yScale") == "log10":
            ymin, ymax = min(y_values), max(y_values)
            if ymin == ymax:
                ymin, ymax = ymin / 1.2, ymax * 1.2
        else:
            ymin, ymax = min(y_values), max(y_values)
            if ymin == ymax:
                delta = max(abs(ymin) * 0.05, 1)
                ymin, ymax = ymin - delta, ymax + delta
        ax.set_xlim(xmin, xmax)
        ax.set_ylim(ymin, ymax)
        if len(spec["layers"]) > 1 or any(layer.get("colorBy") for layer in spec["layers"]):
            handles, labels = ax.get_legend_handles_labels()
            unique = dict(zip(labels, handles))
            if unique:
                ax.legend(unique.values(), unique.keys(), fontsize=7, frameon=False,
                          labelcolor=theme["text"], loc="best")
    for ax in axes[len(facets):]:
        ax.set_visible(False)
    figure.suptitle(data["title"], fontsize=10, color=theme["text"])
    # The common publication geometry is shared by SVG/PDF/PNG.
    figure.tight_layout(rect=[0, 0, 1, 0.95], pad=1.35)


def _render_chart_content(figure, chart_type: str, chart_data: dict[str, Any], theme: dict[str, Any], layout_width: float) -> None:
    figure.clear()
    figure.patch.set_facecolor(theme["surface"])
    canonical = canonicalize_chart_data(chart_data, chart_type)
    if chart_type == "composite":
        _render_composite_chart(figure, canonical, theme)
        return
    ax = figure.add_subplot(111)

    if chart_type == "bar":
        use_tight_layout = _render_bar_chart(ax, canonical, theme, layout_width)
    elif chart_type == "line":
        if any(key in canonical for key in
               ("xValues", "series", "xError", "yError", "xScale", "yScale", "xUnit", "yUnit")):
            use_tight_layout = _render_scientific_cartesian(ax, canonical, theme, layout_width, chart_type)
        else:
            use_tight_layout = _render_line_chart(ax, canonical, theme, layout_width)
    elif chart_type == "scatter":
        use_tight_layout = _render_scientific_cartesian(ax, canonical, theme, layout_width, chart_type)
    elif chart_type == "pie":
        use_tight_layout = _render_pie_chart(figure, ax, canonical, theme)
    elif chart_type == "histogram":
        use_tight_layout = _render_histogram(ax, canonical, theme)
    elif chart_type == "sankey":
        use_tight_layout = _render_sankey_chart(figure, ax, canonical, theme)
    elif chart_type == "boxplot":
        use_tight_layout = _render_boxplot(ax, canonical, theme, layout_width)
    elif chart_type == "heatmap":
        use_tight_layout = _render_heatmap(figure, ax, canonical, theme, layout_width)
    else:
        # Unreachable in practice - render_chart_png already validates
        # chart_type against SUPPORTED_CHART_TYPES before ever calling this.
        # Kept as a defensive mirror of legacy's own dead `else: raise` branch.
        raise ValueError(f"Unsupported chart type: {chart_type}")

    if canonical.get("analysis"):
        _render_chart_analysis(ax, canonical["analysis"], theme)

    if use_tight_layout:
        figure.tight_layout(pad=1.25)


def _build_chart_figure(chart_type: str, chart_data: dict[str, Any], width: float, height: float, dpi: float):
    """Shared figure-construction path for both render_chart_png and
    render_chart_svg below (ADR-013 stage 13.4) - everything through
    `canvas.draw()` is format-agnostic; only the final `figure.savefig(...)`
    call differs (raster vs vector). Returns `(figure, theme)` - the theme
    carries `surface`, the facecolor every caller's own savefig needs.

    Raises ValueError for a `chart_type` outside SUPPORTED_CHART_TYPES -
    there is no such thing as a placeholder chart TYPE, only placeholder
    chart DATA (a malformed `chart_data` under a KNOWN type never raises;
    canonicalize_chart_data's ChartDataError, or any other exception a
    renderer hits, is caught and swapped for a placeholder "Chart rendering
    issue" image instead, exactly as legacy ChartItem.generate_chart never
    crashed on bad data)."""
    normalized_type = str(chart_type or "").strip().lower()
    if normalized_type not in SUPPORTED_CHART_TYPES:
        raise ValueError(f"Unsupported chart type: {normalized_type or 'unknown'}")

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
    return figure, theme


def render_chart_png(chart_type: str, chart_data: dict[str, Any], width: float, height: float, dpi_scale: float = 1.0) -> bytes:
    """Render one chart to PNG bytes. Port of ChartItem.generate_chart +
    _render_chart_to_image + _figure_to_image, minus the render cache (no
    long-lived widget here to cache against) and minus the card chrome (see
    module docstring).

    `width`/`height` are the desired on-screen box size in CSS px; `dpi_scale`
    multiplies the base render density (3.0 for the export endpoint - mirrors
    legacy's own EXPORT_SCALE; ADR-013 stage 13.4 retired every OTHER caller,
    see this module's own docstring)."""
    dpi = BASE_DPI * max(1.0, float(dpi_scale))
    figure, theme = _build_chart_figure(chart_type, chart_data, width, height, dpi)
    buffer = BytesIO()
    figure.savefig(buffer, format="png", dpi=dpi, facecolor=theme["surface"])
    return buffer.getvalue()


def render_chart_svg(chart_type: str, chart_data: dict[str, Any], width: float, height: float) -> bytes:
    """Render one chart to SVG bytes (ADR-013 stage 13.4's new export
    option) - same figure/content pipeline as render_chart_png, but vector
    output, so there is no dpi_scale parameter: SVG is resolution-
    independent, unlike a PNG's fixed pixel grid. BASE_DPI still sets the
    figure's internal layout density (font/line sizing in figure-relative
    units), matching the PNG export's own proportions rather than picking
    an unrelated second constant."""
    figure, theme = _build_chart_figure(chart_type, chart_data, width, height, BASE_DPI)
    buffer = BytesIO()
    # Preserve selectable/searchable SVG text; Matplotlib's default turns
    # every glyph into a path, making the file unsuitable for journal edits.
    with matplotlib.rc_context({"svg.fonttype": "none", "font.family": ["DejaVu Sans"]}):
        figure.savefig(buffer, format="svg", facecolor=theme["surface"])
    return buffer.getvalue()


def render_chart_publication(
    chart_type: str,
    chart_data: dict[str, Any],
    *,
    width_mm: float,
    height_mm: float,
    fmt: str,
    dpi: int = 300,
    colorblind_safe: bool = False,
    grayscale_safe: bool = False,
) -> bytes:
    """Print-sized export from the SAME canonical data and per-type artists.

    PNG pixel dimensions are exactly the rounded mm-to-inch conversion at
    300/600 DPI; SVG/PDF use real physical dimensions in points. Both vector
    formats retain actual font glyphs as editable/selectable text.
    """
    from graphlink_chart_data import validate_publication_export

    options = validate_publication_export({
        "format": fmt, "size": "custom",
        "widthMm": width_mm, "heightMm": height_mm,
        "dpi": dpi, "colorblindSafe": colorblind_safe,
        "grayscaleSafe": grayscale_safe,
    })
    canonical = canonicalize_chart_data(chart_data, chart_type)
    width_in = options["widthMm"] / 25.4
    height_in = options["heightMm"] / 25.4
    # All matplotlib labels use physical point sizes, never pixel-scaled
    # font sizes. 8pt ticks/legend and 9pt axis labels remain readable at
    # journal single-column size irrespective of requested DPI.
    theme = _publication_theme(
        colorblind_safe=options["colorblindSafe"],
        grayscale_safe=options["grayscaleSafe"],
    )
    with matplotlib.rc_context({
        "font.family": ["DejaVu Sans"],
        "font.size": 9,
        "svg.fonttype": "none",
        "pdf.fonttype": 42,  # embedded TrueType, not Type 3 glyph outlines
        "pdf.use14corefonts": False,
        "savefig.transparent": False,
    }):
        figure = Figure(figsize=(width_in, height_in), dpi=dpi)
        FigureCanvasAgg(figure)
        _render_chart_content(figure, chart_type, canonical, theme, width_in * 96)
        figure.canvas.draw()
        out = BytesIO()
        figure.savefig(
            out, format=fmt, dpi=dpi,
            facecolor=theme["surface"], edgecolor="none",
            metadata={"Creator": "Graphlink"} if fmt == "pdf" else None,
        )
        return out.getvalue()
