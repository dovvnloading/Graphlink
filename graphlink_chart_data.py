"""Canonical chart-data validation shared by generation, rendering, and storage."""

from __future__ import annotations

import math
import re
from decimal import Decimal, localcontext


SUPPORTED_CHART_TYPES = frozenset({"bar", "line", "pie", "histogram", "sankey", "scatter", "boxplot", "heatmap", "composite"})
MAX_SERIES_POINTS = 300
# Scientific line/scatter arrays retain all finite observations, while the
# SVG renderer uses bounded, viewport-aware samples. Other chart types keep
# their original 300-item bounds to protect memory and publication output.
MAX_SCIENTIFIC_OBSERVATIONS = 10_000
MAX_NAMED_SERIES = 12
MAX_SANKEY_FLOWS = 300
MAX_LABEL_LENGTH = 160
MAX_TITLE_LENGTH = 200

# ADR-013 stage 13.1: the canonical shape's own schema version - every spec
# canonicalize_chart_data produces today is version 1. Bumped only when this
# function's OUTPUT shape changes incompatibly (a renamed/removed key, a new
# required field) - not on every chart-type addition, which stays additive.
CHART_SPEC_VERSION = 1


# Publication export settings are per-download, not persisted ChartSpec fields.
# Validate before allocating a potentially very large raster/PDF.
PUBLICATION_WIDTH_MM = {"single": 85.0, "double": 180.0}
PUBLICATION_FORMATS = frozenset({"svg", "pdf", "png"})
PUBLICATION_DPI = frozenset({300, 600})
MAX_PUBLICATION_PIXELS = 30_000_000


def validate_publication_export(options, *, aspect_ratio: float = 0.65) -> dict:
    """Strict, bounded, side-effect-free publication export options.

    Journal presets select a physical *width*; height follows the chart's
    persisted aspect ratio. Custom size specifies both dimensions in mm.
    The wire never accepts arbitrary DPI, booleans disguised as numbers,
    unknown keys, or unbounded raster allocations.
    """
    if not isinstance(options, dict):
        raise ChartDataError("Publication export options must be an object")
    allowed = {"format", "size", "widthMm", "heightMm", "dpi",
               "colorblindSafe", "grayscaleSafe"}
    unknown = set(options) - allowed
    if unknown:
        raise ChartDataError("Unknown publication export options: " +
                             ", ".join(sorted(map(str, unknown))))

    fmt = options.get("format", "png")
    if not isinstance(fmt, str) or fmt not in PUBLICATION_FORMATS:
        raise ChartDataError("Export format must be svg, pdf or png")
    dpi = options.get("dpi", 300)
    if type(dpi) is not int or dpi not in PUBLICATION_DPI:
        raise ChartDataError("Export DPI must be 300 or 600")
    for field in ("colorblindSafe", "grayscaleSafe"):
        if type(options.get(field, False)) is not bool:
            raise ChartDataError(field + " must be a boolean")

    size = options.get("size", "single")
    if size not in ("single", "double", "custom") or not isinstance(size, str):
        raise ChartDataError("Export size must be single, double or custom")
    if type(aspect_ratio) not in (int, float) or not math.isfinite(aspect_ratio) or aspect_ratio <= 0:
        raise ChartDataError("Invalid chart aspect ratio")
    if size in PUBLICATION_WIDTH_MM:
        if "widthMm" in options or "heightMm" in options:
            raise ChartDataError("Preset sizes derive dimensions from chart aspect ratio")
        width_mm = PUBLICATION_WIDTH_MM[size]
        height_mm = width_mm * aspect_ratio
    else:
        if "widthMm" not in options or "heightMm" not in options:
            raise ChartDataError("Custom export size requires widthMm and heightMm")
        def dimension(field):
            value = options[field]
            if type(value) not in (float, int) or not math.isfinite(value):
                raise ChartDataError(field + " must be a finite number of millimeters")
            return float(value)
        width_mm = dimension("widthMm")
        height_mm = dimension("heightMm")

    if not (35 <= width_mm <= 300 and 30 <= height_mm <= 300):
        raise ChartDataError("Publication dimensions must be 35-300 mm wide and 30-300 mm high")
    pixels = (width_mm / 25.4 * dpi) * (height_mm / 25.4 * dpi)
    if pixels > MAX_PUBLICATION_PIXELS:
        raise ChartDataError("Export dimensions exceed the 30 megapixel safety limit")
    return {
        "format": fmt, "size": size, "widthMm": width_mm,
        "heightMm": height_mm, "dpi": dpi,
        "colorblindSafe": options.get("colorblindSafe", False),
        "grayscaleSafe": options.get("grayscaleSafe", False),
    }


class ChartDataError(ValueError):
    """Raised when a chart payload cannot be converted to the canonical schema."""


# -- ADR-013 stage 13.3: chart generation via respond_json --------------------
#
# The JSON Schema respond_json (backend/structured_output.py) constrains a
# model's chart-generation output to, and the system/user messages that ask
# for it - shared by backend/agents.py's _call_chart_agent (the real
# generation path) and backend/evals/runner.py's chart eval fixture (so the
# eval drives the SAME shape actually shipping, not a stand-in). Kept here
# rather than in backend/ since chart-shape knowledge already lives in this
# module (SUPPORTED_CHART_TYPES/canonicalize_chart_data) and both callers
# need it Qt-free. These schemas are deliberately looser than
# canonicalize_chart_data's own rules (no length caps, no cross-field checks
# like "pie values must be positive") - canonicalize_chart_data remains the
# single source of truth for what's actually ACCEPTED; this schema only
# exists to steer generation and give the provider's own native constraint
# something concrete to enforce, catching the most common shape mistakes
# (wrong container type, missing key) before canonicalize_chart_data's own,
# stricter pass.
# Optional scientific fields do not change any of the five legacy shapes.
_ERROR_ITEM_SCHEMA = {
    "anyOf": [
        {"type": "number"},
        {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
    ]
}
_ERROR_SCHEMA = {"type": "array", "items": _ERROR_ITEM_SCHEMA}
_SCIENTIFIC_PROPERTIES = {
    "xValues": {"type": "array", "items": {"type": "number"}},
    "xScale": {"type": "string", "enum": ["linear", "log10"]},
    "yScale": {"type": "string", "enum": ["linear", "log10"]},
    "xUnit": {"type": "string"},
    "yUnit": {"type": "string"},
    "xError": _ERROR_SCHEMA,
    "yError": _ERROR_SCHEMA,
    "series": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "values": {"type": "array", "items": {"type": "number"}},
                "xError": _ERROR_SCHEMA,
                "yError": _ERROR_SCHEMA,
            },
            "required": ["name", "values"],
        },
    },
}
_SERIES_PROPERTIES = {
    "title": {"type": "string"},
    "labels": {"type": "array", "items": {"type": "string"}},
    "values": {"type": "array", "items": {"type": "number"}},
    "xAxis": {"type": "string"},
    "yAxis": {"type": "string"},
}

CHART_JSON_SCHEMAS: dict[str, dict] = {
    "bar": {
        "type": "object",
        "properties": {"type": {"type": "string", "enum": ["bar"]}, **_SERIES_PROPERTIES},
        "required": ["type", "labels", "values"],
    },
    "line": {
        "type": "object",
        "properties": {"type": {"type": "string", "enum": ["line"]},
                       **_SERIES_PROPERTIES, **_SCIENTIFIC_PROPERTIES},
        "required": ["type"],
        # Legacy top-level data OR additive named series; otherwise a
        # provider enforcing this schema makes multiseries lines impossible
        # (the canonicalizer correctly rejects simultaneous top-level
        # values and named series).
        "anyOf": [{"required": ["labels", "values"]}, {"required": ["series"]}],
    },
    "scatter": {
        "type": "object",
        "properties": {"type": {"type": "string", "enum": ["scatter"]},
                       **_SERIES_PROPERTIES, **_SCIENTIFIC_PROPERTIES},
        "required": ["type", "xValues"],
        "anyOf": [{"required": ["values"]}, {"required": ["series"]}],
    },
    "pie": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["pie"]},
            "title": {"type": "string"},
            "labels": {"type": "array", "items": {"type": "string"}},
            "values": {"type": "array", "items": {"type": "number"}},
        },
        "required": ["type", "labels", "values"],
    },
    "histogram": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["histogram"]},
            "title": {"type": "string"},
            "values": {"type": "array", "items": {"type": "number"}},
            "bins": {"type": "integer"},
            "xAxis": {"type": "string"},
            "yAxis": {"type": "string"},
        },
        "required": ["type", "values"],
    },
    "boxplot": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["boxplot"]},
            "title": {"type": "string"},
            "xAxis": {"type": "string"},
            "yAxis": {"type": "string"},
            "groups": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "values": {"type": "array", "items": {"type": "number"}},
                    },
                    "required": ["label", "values"],
                },
            },
        },
        "required": ["type", "groups"],
    },
    "heatmap": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["heatmap"]},
            "title": {"type": "string"},
            "xAxis": {"type": "string"},
            "yAxis": {"type": "string"},
            "valueUnit": {"type": "string"},
            "rowLabels": {"type": "array", "items": {"type": "string"}},
            "columnLabels": {"type": "array", "items": {"type": "string"}},
            "matrix": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
        },
        "required": ["type", "rowLabels", "columnLabels", "matrix"],
    },
    "composite": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["composite"]},
            "version": {"type": "integer", "enum": [2]},
            "title": {"type": "string"},
            "xAxis": {"type": "string"}, "yAxis": {"type": "string"},
            "xScale": {"type": "string", "enum": ["linear", "log10"]},
            "yScale": {"type": "string", "enum": ["linear", "log10"]},
            "xUnit": {"type": "string"}, "yUnit": {"type": "string"},
            "composite": {
                "type": "object",
                "properties": {
                    "numbers": {"type": "object", "additionalProperties": {
                        "type": "array", "items": {"type": "number"},
                    }},
                    "categories": {"type": "object", "additionalProperties": {
                        "type": "array", "items": {"type": "string"},
                    }},
                    "facetBy": {"type": "string"},
                    "layers": {"type": "array", "items": {"type": "object", "properties": {
                        "mark": {"type": "string", "enum": ["point", "line", "band", "area"]},
                        "x": {"type": "string"}, "y": {"type": "string"},
                        "y2": {"type": "string"}, "colorBy": {"type": "string"},
                        "label": {"type": "string"},
                    }, "required": ["mark", "x", "y"]}},
                },
                "required": ["numbers", "layers"],
            },
        },
        "required": ["type", "composite"],
    },
    "sankey": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["sankey"]},
            "title": {"type": "string"},
            "flows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "target": {"type": "string"},
                        "value": {"type": "number"},
                    },
                    "required": ["source", "target", "value"],
                },
            },
        },
        "required": ["type", "flows"],
    },
}


# Registered in graphlink_prompts.py's prompt registry ("chart-generation-
# system") as its own STABLE constant rather than embedded inline in
# chart_generation_messages below - that registry's own convention is
# "only stable template text is registered, per-request interpolation
# (chart type, source text) stays at the call site" (see
# BASE_SYSTEM_PROMPT/CONTEXT_SUMMARY_SYSTEM_PROMPT's identical shape). The
# chart TYPE lives in the user message instead of being f-string-baked into
# this text, specifically so this string never varies per call.
CHART_GENERATION_SYSTEM_PROMPT = (
    "You are Graphlink's chart data extractor. Read the source text and "
    "produce a chart payload as a single JSON object matching the required "
    "schema exactly for the requested chart type. Use only data present in "
    "the source text - never invent numbers. Keep the title concise."
)


def chart_generation_messages(source_text: str, chart_type: str) -> list[dict]:
    """The system+user pair respond_json sends to extract `chart_type` chart
    data from `source_text`. A plain data function (no api_provider/
    respond_json import here - see this section's own module-level comment
    on why this stays in graphlink_chart_data.py) so both real callers build
    the identical request rather than maintaining their own copies."""
    return [
        {"role": "system", "content": CHART_GENERATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Chart type: {chart_type}\n\n"
                + ("For composite figures: use numeric column arrays in composite.numbers; "
                   "optional grouping columns in composite.categories; connect each "
                   "layer to existing numeric columns by name. Layer marks are point, "
                   "line, band (with y2 upper-bound column), or area. Optional facetBy "
                   "names a categorical column. Never fabricate observations or "
                   "uncertainty intervals.\n\n" if chart_type == "composite" else "")
                + f"--- SOURCE TEXT ---\n{source_text}"
            ),
        },
    ]


def _finite_number(value, field_name):
    if isinstance(value, bool):
        raise ChartDataError(f"{field_name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        # json.loads can produce arbitrarily large Python integers.
        # float(10**1000) raises OverflowError, not ValueError; a chart
        # request must report a validation failure rather than a 500.
        raise ChartDataError(f"{field_name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ChartDataError(f"{field_name} must be a finite number")
    return number


def _clean_text(value, field_name, *, default="", max_length=MAX_LABEL_LENGTH):
    text = " ".join(str(value if value is not None else "").split()).strip()
    if not text:
        if default:
            return default
        raise ChartDataError(f"{field_name} must be non-empty")
    if len(text) > max_length:
        raise ChartDataError(f"{field_name} is too long (maximum {max_length} characters)")
    return text


def _legacy_sankey_flows(payload):
    nested = payload.get("data")
    if not isinstance(nested, dict):
        return None
    nodes = nested.get("nodes", [])
    links = nested.get("links", [])
    if not isinstance(nodes, list) or not isinstance(links, list):
        return None

    # REVIEW-FIX: bound BEFORE either loop below, not after. The direct
    # `{"flows": [...]}` shape is rejected by canonicalize_chart_data's own
    # `len(raw_flows) > MAX_SANKEY_FLOWS` check in O(1) - a plain len() on
    # a list never touches an element. This legacy nested shape used to do
    # two full O(n) passes (building `names` from every node, `flows` from
    # every link) BEFORE that same cap was ever consulted, so an oversized
    # legacy-format payload did unbounded synchronous work first and only
    # got rejected afterward. Measured directly: an equal-size legacy
    # payload took roughly 60,000x longer to reject than the direct-list
    # shape. This runs on the FastAPI event loop (backend/session_load.py's
    # chat restore is not offloaded to a worker thread), so that stall
    # blocks every other session on the process, not just the one loading
    # this chat - a genuinely reachable exposure for any chat saved in the
    # pre-"flows"-key era, not a contrived input.
    if len(links) > MAX_SANKEY_FLOWS:
        raise ChartDataError(f"Sankey charts support at most {MAX_SANKEY_FLOWS} flows")
    # A legitimate sankey graph never needs more distinct nodes than it has
    # flows, plus one - bounding this separately closes the case where
    # `links` alone is small but `nodes` is huge (the names-building loop
    # would otherwise still be unbounded on its own).
    if len(nodes) > MAX_SANKEY_FLOWS + 1:
        raise ChartDataError(f"Sankey charts support at most {MAX_SANKEY_FLOWS + 1} nodes")

    names = []
    for index, node in enumerate(nodes):
        if isinstance(node, dict):
            node_name = node.get("name", f"Node {index}")
        else:
            node_name = node
        names.append(str(node_name).strip())

    flows = []
    for link in links:
        if not isinstance(link, dict):
            raise ChartDataError("Sankey links must be objects")
        source = link.get("source")
        target = link.get("target")
        if isinstance(source, int) and 0 <= source < len(names):
            source = names[source]
        if isinstance(target, int) and 0 <= target < len(names):
            target = names[target]
        flows.append({"source": source, "target": target, "value": link.get("value")})
    return flows


def _assert_acyclic(flows):
    adjacency = {}
    for flow in flows:
        adjacency.setdefault(flow["source"], set()).add(flow["target"])
        adjacency.setdefault(flow["target"], set())

    visiting = set()
    visited = set()

    def visit(node):
        if node in visiting:
            raise ChartDataError("Sankey charts cannot contain cycles")
        if node in visited:
            return
        visiting.add(node)
        for target in sorted(adjacency.get(node, ())):
            visit(target)
        visiting.remove(node)
        visited.add(node)

    for node in sorted(adjacency):
        visit(node)



def _scientific_errors(raw, field, count):
    """Normalize symmetric and asymmetric errors into [minus, plus] pairs."""
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ChartDataError(f"{field} must be a list")
    if len(raw) != count:
        raise ChartDataError(f"{field} length must match the series length ({count})")
    normalized = []
    for index, entry in enumerate(raw):
        name = f"{field} at index {index}"
        if isinstance(entry, list):
            if len(entry) != 2:
                raise ChartDataError(f"{name} must have exactly two magnitudes")
            minus = _finite_number(entry[0], f"{name} lower magnitude")
            plus = _finite_number(entry[1], f"{name} upper magnitude")
        else:
            minus = plus = _finite_number(entry, name)
        if minus < 0 or plus < 0:
            raise ChartDataError(f"{name} magnitudes must be nonnegative")
        normalized.append([minus, plus])
    return normalized


def _check_scientific_axis(values, errors, scale, field):
    """Check all coordinates AND error endpoints (including overflow)."""
    smallest, largest = float("inf"), float("-inf")
    for index, value in enumerate(values):
        minus, plus = errors[index] if errors is not None else (0.0, 0.0)
        lower, upper = value - minus, value + plus
        if not math.isfinite(lower) or not math.isfinite(upper):
            raise ChartDataError(f"{field} at index {index} has a non-finite error endpoint")
        if scale == "log10" and lower <= 0:
            raise ChartDataError(
                f"{field} at index {index} and its lower error endpoint must be "
                "greater than zero on a log10 axis"
            )
        smallest, largest = min(smallest, lower), max(largest, upper)
    if not math.isfinite(largest - smallest):
        raise ChartDataError(f"{field} range including error endpoints must be finite")


def _scientific_chart(data, chart_type, result):
    """Additive v1 scientific path. Plain legacy line data never enters here."""
    raw_x = data.get("xValues")
    if chart_type == "scatter" and raw_x is None:
        raise ChartDataError("Scatter charts require numeric xValues")
    if raw_x is not None:
        if not isinstance(raw_x, list):
            raise ChartDataError("xValues must be a list")
        if not raw_x:
            raise ChartDataError("xValues require at least one data point")
        if len(raw_x) > MAX_SCIENTIFIC_OBSERVATIONS:
            raise ChartDataError(f"Charts support at most {MAX_SCIENTIFIC_OBSERVATIONS} data points")
        x_values = [_finite_number(value, f"xValues at index {i}") for i, value in enumerate(raw_x)]
        result["xValues"] = x_values
    else:
        x_values = None

    for axis in ("xScale", "yScale"):
        scale = data.get(axis, "linear")
        if scale not in ("linear", "log10"):
            raise ChartDataError(f"{axis} must be linear or log10")
        result[axis] = scale
    if result["xScale"] == "log10" and x_values is None:
        raise ChartDataError("A log10 x-axis requires numeric xValues")

    for field in ("xUnit", "yUnit"):
        if field in data and data[field] is not None:
            result[field] = _clean_text(data[field], field)

    labels = data.get("labels")
    if labels is not None:
        if not isinstance(labels, list):
            raise ChartDataError("Labels must be a list")
        if len(labels) > MAX_SCIENTIFIC_OBSERVATIONS:
            raise ChartDataError(f"Charts support at most {MAX_SCIENTIFIC_OBSERVATIONS} data points")
        result["labels"] = [_clean_text(label, f"Label at index {i}") for i, label in enumerate(labels)]

    raw_series = data.get("series")
    if raw_series is not None:
        if not isinstance(raw_series, list) or not raw_series:
            raise ChartDataError("series must be a nonempty list")
        if len(raw_series) > MAX_NAMED_SERIES:
            raise ChartDataError(f"Charts support at most {MAX_NAMED_SERIES} named series")
        if "values" in data or "xError" in data or "yError" in data:
            raise ChartDataError("Named series cannot be combined with top-level values or errors")
        series = []
        seen_names = set()
        for index, raw in enumerate(raw_series):
            if not isinstance(raw, dict):
                raise ChartDataError(f"series at index {index} must be an object")
            name = _clean_text(raw.get("name"), f"series at index {index} name")
            if name in seen_names:
                raise ChartDataError(f"Duplicate series name: {name}")
            seen_names.add(name)
            values = raw.get("values")
            if not isinstance(values, list):
                raise ChartDataError(f"series {name} values must be a list")
            if not values:
                raise ChartDataError(f"series {name} requires at least one data point")
            if len(values) > MAX_SCIENTIFIC_OBSERVATIONS:
                raise ChartDataError(f"Charts support at most {MAX_SCIENTIFIC_OBSERVATIONS} data points")
            row = {
                "name": name,
                "values": [_finite_number(value, f"series {name} value at index {i}") for i, value in enumerate(values)],
            }
            for axis in ("xError", "yError"):
                if axis in raw:
                    row[axis] = _scientific_errors(raw[axis], f"series {name} {axis}", len(values))
            series.append(row)
        result["series"] = series
    else:
        raw_values = data.get("values")
        if not isinstance(raw_values, list) or not raw_values:
            raise ChartDataError(f"{chart_type.title()} charts require at least one data point")
        if len(raw_values) > MAX_SCIENTIFIC_OBSERVATIONS:
            raise ChartDataError(f"Charts support at most {MAX_SCIENTIFIC_OBSERVATIONS} data points")
        series = [{
            "values": [_finite_number(value, f"Value at index {i}") for i, value in enumerate(raw_values)],
        }]
        result["values"] = series[0]["values"]
        for axis in ("xError", "yError"):
            if axis in data:
                errors = _scientific_errors(data[axis], axis, len(raw_values))
                result[axis] = errors
                series[0][axis] = errors

    count = len(series[0]["values"])
    for row in series:
        if len(row["values"]) != count:
            raise ChartDataError(f"series {row.get('name', 'values')} length must match the other series ({count})")
    if chart_type == "line" and count < 2:
        raise ChartDataError("Line charts require at least two data points")
    if count > MAX_SCIENTIFIC_OBSERVATIONS or len(series) * count > MAX_SCIENTIFIC_OBSERVATIONS:
        raise ChartDataError(f"Charts support at most {MAX_SCIENTIFIC_OBSERVATIONS} total series points")
    if x_values is not None and len(x_values) != count:
        raise ChartDataError(f"xValues length must match the series length ({count})")
    if "labels" in result and len(result["labels"]) != count:
        raise ChartDataError(f"labels length must match the series length ({count})")
    if chart_type == "line" and x_values is None and "labels" not in result:
        raise ChartDataError("Line charts require labels or numeric xValues")

    # Categorical line x positions are zero-based indexes, as before. Only
    # numeric x-values can have an error interval or logarithmic scale.
    if x_values is None and any(row.get("xError") is not None for row in series):
        raise ChartDataError("xError requires numeric xValues")
    if x_values is not None:
        for row in series:
            _check_scientific_axis(x_values, row.get("xError"), result["xScale"], "xValues")
    for row in series:
        _check_scientific_axis(row["values"], row.get("yError"), result["yScale"],
                               f"series {row.get('name', 'values')} values")

    # Each row is locally finite, but multiple series can still have
    # disjoint asymmetric intervals whose UNION has an infinite span. Use
    # the same all-series extrema the React renderer uses for its domains.
    for field, coordinates, error_field in (
        ("xValues", x_values, "xError"),
        ("values", None, "yError"),
    ):
        if field == "xValues" and coordinates is None:
            continue
        smallest, largest = float("inf"), float("-inf")
        for row in series:
            values = coordinates if coordinates is not None else row["values"]
            errors = row.get(error_field)
            for index, value in enumerate(values):
                minus, plus = errors[index] if errors is not None else (0.0, 0.0)
                smallest = min(smallest, value - minus)
                largest = max(largest, value + plus)
        if not math.isfinite(largest - smallest):
            raise ChartDataError(f"{field} range including all series errors must be finite")

    result["xAxis"] = _clean_text(
        data.get("xAxis"), "X-axis label",
        default="X" if chart_type == "scatter" else "Sequence",
    )
    result["yAxis"] = _clean_text(
        data.get("yAxis"), "Y-axis label", default="Y" if chart_type == "scatter" else "Value",
    )
    return result


def _boxplot_chart(data, result):
    """Preserve observed samples; both renderers calculate Tukey statistics."""
    raw_groups = data.get("groups")
    if not isinstance(raw_groups, list) or not raw_groups:
        raise ChartDataError("Boxplot charts require a nonempty groups list")
    if len(raw_groups) > MAX_NAMED_SERIES:
        raise ChartDataError(f"Boxplots support at most {MAX_NAMED_SERIES} groups")
    groups = []
    seen = set()
    total = 0
    overall_min, overall_max = float("inf"), float("-inf")
    for index, raw in enumerate(raw_groups):
        if not isinstance(raw, dict):
            raise ChartDataError(f"Boxplot group at index {index} must be an object")
        label = _clean_text(raw.get("label"), f"Boxplot group at index {index} label")
        if label in seen:
            raise ChartDataError(f"Duplicate boxplot group label: {label}")
        seen.add(label)
        values = raw.get("values")
        if not isinstance(values, list) or len(values) < 2:
            raise ChartDataError(f"Boxplot group {label} requires at least two observations")
        total += len(values)
        if total > MAX_SERIES_POINTS:
            raise ChartDataError(f"Boxplots support at most {MAX_SERIES_POINTS} observations total")
        cleaned = [_finite_number(value, f"Boxplot group {label} value at index {i}")
                   for i, value in enumerate(values)]
        overall_min = min(overall_min, min(cleaned))
        overall_max = max(overall_max, max(cleaned))
        groups.append({"label": label, "values": cleaned})
    if not math.isfinite(overall_max - overall_min):
        raise ChartDataError("Boxplot observation range must be finite")
    result["groups"] = groups
    result["xAxis"] = _clean_text(data.get("xAxis"), "X-axis label", default="Group")
    result["yAxis"] = _clean_text(data.get("yAxis"), "Y-axis label", default="Value")
    return result


def _heatmap_chart(data, result):
    """A dense, rectangular matrix; never pad, drop, or coerce cells."""
    rows = data.get("rowLabels")
    columns = data.get("columnLabels")
    matrix = data.get("matrix")
    if not isinstance(rows, list) or not rows:
        raise ChartDataError("Heatmap rowLabels must be a nonempty list")
    if not isinstance(columns, list) or not columns:
        raise ChartDataError("Heatmap columnLabels must be a nonempty list")
    if len(rows) * len(columns) > MAX_SERIES_POINTS:
        raise ChartDataError(f"Heatmaps support at most {MAX_SERIES_POINTS} cells")
    if not isinstance(matrix, list) or len(matrix) != len(rows):
        raise ChartDataError("Heatmap matrix row count must match rowLabels")
    cleaned_matrix = []
    smallest, largest = float("inf"), float("-inf")
    for index, row in enumerate(matrix):
        if not isinstance(row, list) or len(row) != len(columns):
            raise ChartDataError(f"Heatmap matrix row {index} length must match columnLabels")
        values = [_finite_number(value, f"Heatmap cell at row {index} column {j}")
                  for j, value in enumerate(row)]
        smallest = min(smallest, min(values))
        largest = max(largest, max(values))
        cleaned_matrix.append(values)
    if not math.isfinite(largest - smallest):
        raise ChartDataError("Heatmap value range must be finite")
    result["rowLabels"] = [_clean_text(label, f"Heatmap row label at index {i}")
                           for i, label in enumerate(rows)]
    result["columnLabels"] = [_clean_text(label, f"Heatmap column label at index {i}")
                              for i, label in enumerate(columns)]
    result["matrix"] = cleaned_matrix
    result["xAxis"] = _clean_text(data.get("xAxis"), "X-axis label", default="Columns")
    result["yAxis"] = _clean_text(data.get("yAxis"), "Y-axis label", default="Rows")
    if data.get("valueUnit") is not None:
        result["valueUnit"] = _clean_text(data["valueUnit"], "Heatmap valueUnit")
    return result


# V2 is deliberately a *data-bound* graphics grammar: every mark refers to
# a checked original column, never to model-supplied expressions or scripts.
MAX_COMPOSITE_ROWS = 2_000
MAX_COMPOSITE_LAYERS = 8


def _composite_chart(data, result):
    """Validate a small, composable, faceted grammar without changing V1 charts."""
    if data.get("version", 2) != 2:
        raise ChartDataError("Composite figures require ChartSpec version 2")
    allowed = {"version", "type", "title", "composite", "xAxis", "yAxis",
               "xScale", "yScale", "xUnit", "yUnit", "scienceReceipt"}
    if set(data) - allowed:
        raise ChartDataError("Unsupported composite figure field")
    raw = data.get("composite")
    if not isinstance(raw, dict) or set(raw) - {"numbers", "categories", "layers", "facetBy"}:
        raise ChartDataError("Composite data must contain only numbers, categories, layers, facetBy")
    numbers = raw.get("numbers")
    categories = raw.get("categories", {})
    layers = raw.get("layers")
    if not isinstance(numbers, dict) or not 2 <= len(numbers) <= 12:
        raise ChartDataError("Composite figures require 2-12 numeric columns")
    if not isinstance(categories, dict) or len(categories) > 4:
        raise ChartDataError("Composite figures support at most four categorical columns")
    if not isinstance(layers, list) or not 1 <= len(layers) <= MAX_COMPOSITE_LAYERS:
        raise ChartDataError("Composite figures require 1-8 mark layers")

    cleaned_numbers, cleaned_categories = {}, {}
    count = None
    for source, destination, field_limit in (
        (numbers, cleaned_numbers, "numeric"),
        (categories, cleaned_categories, "categorical"),
    ):
        for field, values in source.items():
            if not isinstance(field, str) or _clean_text(field, field_limit) != field:
                raise ChartDataError(f"Invalid {field_limit} column name")
            if (field in cleaned_numbers or field in cleaned_categories or len(field) > 80
                    or field in {"__proto__", "constructor", "prototype"}):
                raise ChartDataError(f"Duplicate or oversized composite column: {field}")
            if not isinstance(values, list):
                raise ChartDataError(f"Composite column {field} must contain an array")
            if count is None:
                count = len(values)
            if len(values) != count or not 2 <= count <= MAX_COMPOSITE_ROWS:
                raise ChartDataError("Composite columns must have 2-2000 aligned observations")
            if field_limit == "numeric":
                numbers_clean = [_finite_number(value, f"Composite {field}[{i}]")
                                 for i, value in enumerate(values)]
                if not math.isfinite(max(numbers_clean) - min(numbers_clean)):
                    raise ChartDataError(f"Composite {field} range must be finite")
                destination[field] = numbers_clean
            else:
                labels = [_clean_text(value, f"Composite {field}[{i}]")
                          for i, value in enumerate(values)]
                if len(set(labels)) > 30:
                    raise ChartDataError("Composite categorical columns support at most 30 categories")
                destination[field] = labels

    facet = raw.get("facetBy")
    if facet is not None:
        if not isinstance(facet, str) or facet not in cleaned_categories:
            raise ChartDataError("Composite facetBy must name a categorical column")
        if len(set(cleaned_categories[facet])) > 6:
            raise ChartDataError("Composite figures support at most six facets")

    xs, ys = [], []
    result_layers = []
    for index, layer in enumerate(layers):
        if not isinstance(layer, dict) or set(layer) - {"mark", "x", "y", "y2", "colorBy", "label"}:
            raise ChartDataError(f"Composite layer {index} has unknown fields")
        mark = layer.get("mark")
        if mark not in ("point", "line", "band", "area"):
            raise ChartDataError(f"Unsupported composite mark in layer {index}")
        x, y, y2 = layer.get("x"), layer.get("y"), layer.get("y2")
        if not isinstance(x, str) or x not in cleaned_numbers or not isinstance(y, str) or y not in cleaned_numbers:
            raise ChartDataError(f"Composite layer {index} must reference numeric x/y columns")
        if (mark == "band") != (y2 is not None):
            raise ChartDataError("Composite band requires y2; other marks forbid y2")
        if mark == "band":
            if not isinstance(y2, str) or y2 not in cleaned_numbers:
                raise ChartDataError("Composite y2 must reference a numeric column")
            if any(low > high for low, high in zip(cleaned_numbers[y], cleaned_numbers[y2])):
                raise ChartDataError("Composite band lower bounds must not exceed upper bounds")
        color = layer.get("colorBy")
        if color is not None:
            if not isinstance(color, str) or color not in cleaned_categories:
                raise ChartDataError("Composite colorBy must name a categorical column")
            if len(set(cleaned_categories[color])) > 8:
                raise ChartDataError("Composite colorBy supports at most eight groups")
        clean = {"mark": mark, "x": x, "y": y}
        if y2 is not None:
            clean["y2"] = y2
        if color is not None:
            clean["colorBy"] = color
        if layer.get("label") is not None:
            clean["label"] = _clean_text(layer["label"], f"Composite layer {index} label")
        result_layers.append(clean)
        xs.extend(cleaned_numbers[x])
        ys.extend(cleaned_numbers[y])
        if y2 is not None:
            ys.extend(cleaned_numbers[y2])
        if mark == "area":
            ys.append(0.0)

    for axis, values in (("x", xs), ("y", ys)):
        scale = data.get(axis + "Scale", "linear")
        if scale not in ("linear", "log10"):
            raise ChartDataError(f"Composite {axis}Scale must be linear or log10")
        if scale == "log10" and any(value <= 0 for value in values):
            raise ChartDataError(f"Composite {axis}Scale requires positive observations")
        if not math.isfinite(max(values) - min(values)):
            raise ChartDataError(f"Composite {axis} domain is not finite")
        result[axis + "Scale"] = scale
        result[axis + "Axis"] = _clean_text(data.get(axis + "Axis"), f"Composite {axis}Axis",
                                           default=axis.upper())
        if data.get(axis + "Unit") is not None:
            result[axis + "Unit"] = _clean_text(data[axis + "Unit"], f"Composite {axis}Unit")

    result["version"] = 2
    result["composite"] = {
        "numbers": cleaned_numbers, "categories": cleaned_categories,
        "layers": result_layers,
    }
    if facet is not None:
        result["composite"]["facetBy"] = facet
    return result


def _canonicalize_chart_data_base(data, chart_type=None):
    """Return a new, bounded chart payload or raise :class:`ChartDataError`.

    The function intentionally does not mutate ``data``. Callers that retain the
    legacy in-place API can copy the returned mapping back into their payload.
    """
    if not isinstance(data, dict):
        raise ChartDataError("Chart payload must be a JSON object")

    expected_type = str(chart_type or data.get("type") or "").strip().lower()
    if expected_type not in SUPPORTED_CHART_TYPES:
        raise ChartDataError(f"Unsupported chart type: {expected_type or 'unknown'}")

    actual_type = str(data.get("type") or expected_type).strip().lower()
    if actual_type != expected_type:
        raise ChartDataError(f"Chart payload type must be {expected_type}")

    result = {
        "version": CHART_SPEC_VERSION,
        "type": expected_type,
        "title": _clean_text(
            data.get("title"),
            "Chart title",
            default=f"{expected_type.title()} Chart",
            max_length=MAX_TITLE_LENGTH,
        ),
    }

    # An optional receipt travels with a science-sourced chart through ordinary
    # chart storage, session reload and wire forwarding. It is provenance, not
    # rendering data; existing chart marks ignore it.
    receipt = data.get("scienceReceipt")
    if receipt is not None:
        if (not isinstance(receipt, dict) or set(receipt) != {"sourceKey", "resultHash", "receiptHash"}
                or not isinstance(receipt["sourceKey"], str)
                or not re.fullmatch(r"[0-9a-f]{32}", receipt["sourceKey"])
                or any(not isinstance(receipt[field], str)
                       or not re.fullmatch(r"[0-9a-f]{64}", receipt[field])
                       for field in ("resultHash", "receiptHash"))):
            raise ChartDataError("Invalid scientific chart receipt")
        result["scienceReceipt"] = dict(receipt)

    if expected_type == "composite":
        return _composite_chart(data, result)

    if expected_type in {"boxplot", "heatmap"}:
        return (_boxplot_chart(data, result) if expected_type == "boxplot"
                else _heatmap_chart(data, result))

    if expected_type == "scatter" or (
        expected_type == "line" and any(key in data for key in
            ("xValues", "series", "xError", "yError", "xScale", "yScale", "xUnit", "yUnit"))
    ):
        return _scientific_chart(data, expected_type, result)

    if expected_type in {"bar", "line", "pie"}:
        labels = data.get("labels")
        values = data.get("values")
        if not isinstance(labels, list) or not isinstance(values, list):
            raise ChartDataError("Labels and values must both be lists")
        if not labels or not values:
            raise ChartDataError(f"{expected_type.title()} charts require at least one data point")
        if len(labels) != len(values):
            raise ChartDataError("Labels and values must have the same length")
        if len(labels) > MAX_SERIES_POINTS:
            raise ChartDataError(f"Charts support at most {MAX_SERIES_POINTS} data points")
        if expected_type == "line" and len(labels) < 2:
            raise ChartDataError("Line charts require at least two data points")

        result["labels"] = [_clean_text(label, f"Label at index {index}") for index, label in enumerate(labels)]
        result["values"] = [
            _finite_number(value, f"Value at index {index}") for index, value in enumerate(values)
        ]
        if expected_type == "pie" and any(value <= 0 for value in result["values"]):
            raise ChartDataError("Pie chart values must be greater than zero")
        result["xAxis"] = _clean_text(
            data.get("xAxis"),
            "X-axis label",
            default="Category" if expected_type == "bar" else "Sequence",
            max_length=MAX_LABEL_LENGTH,
        )
        result["yAxis"] = _clean_text(data.get("yAxis"), "Y-axis label", default="Value", max_length=MAX_LABEL_LENGTH)
        return result

    if expected_type == "histogram":
        values = data.get("values")
        if not isinstance(values, list) or len(values) < 2:
            raise ChartDataError("Histogram charts require at least two values")
        if len(values) > MAX_SERIES_POINTS:
            raise ChartDataError(f"Histograms support at most {MAX_SERIES_POINTS} values")
        bins = _finite_number(data.get("bins", 10), "Histogram bins")
        if bins < 2:
            raise ChartDataError("Histogram bins must be at least 2")
        result["values"] = [_finite_number(value, f"Value at index {index}") for index, value in enumerate(values)]
        result["bins"] = max(2, min(int(bins), 24, len(values)))
        result["xAxis"] = _clean_text(data.get("xAxis"), "X-axis label", default="Value", max_length=MAX_LABEL_LENGTH)
        result["yAxis"] = _clean_text(data.get("yAxis"), "Y-axis label", default="Frequency", max_length=MAX_LABEL_LENGTH)
        return result

    raw_flows = data.get("flows")
    if raw_flows is None:
        raw_flows = _legacy_sankey_flows(data)
    if not isinstance(raw_flows, list) or not raw_flows:
        raise ChartDataError("Sankey charts require at least one flow")
    if len(raw_flows) > MAX_SANKEY_FLOWS:
        raise ChartDataError(f"Sankey charts support at most {MAX_SANKEY_FLOWS} flows")

    aggregated = {}
    for index, flow in enumerate(raw_flows):
        if not isinstance(flow, dict):
            raise ChartDataError(f"Sankey flow at index {index} must be an object")
        source = _clean_text(flow.get("source"), f"Sankey source at index {index}")
        target = _clean_text(flow.get("target"), f"Sankey target at index {index}")
        value = _finite_number(flow.get("value"), f"Sankey value at index {index}")
        if value <= 0:
            raise ChartDataError(f"Sankey value at index {index} must be greater than zero")
        key = (source, target)
        combined = aggregated.get(key, 0.0) + value
        if not math.isfinite(combined):
            raise ChartDataError("Sankey flow totals must be finite")
        aggregated[key] = combined

    # Different edges can still overflow a node's total throughput, even
    # when every individual edge and duplicate-pair sum is finite.
    outgoing_totals = {}
    incoming_totals = {}
    for (source, target), value in aggregated.items():
        outgoing_totals[source] = outgoing_totals.get(source, 0.0) + value
        incoming_totals[target] = incoming_totals.get(target, 0.0) + value
        if not math.isfinite(outgoing_totals[source]) or not math.isfinite(incoming_totals[target]):
            raise ChartDataError("Sankey node totals must be finite")

    result["flows"] = [
        {"source": source, "target": target, "value": value}
        for (source, target), value in sorted(aggregated.items())
    ]
    _assert_acyclic(result["flows"])
    return result



# Chart analysis is an additive, explicitly user-authored part of ChartSpec.
# Omit it entirely for unedited charts: generating or opening an existing chart
# must not alter its measurements, its visible domain, or the legacy defaults.
CHART_ANALYSIS_TYPES = frozenset({"line", "scatter", "histogram", "boxplot", "heatmap"})
MAX_CHART_ANNOTATIONS = 60


# Pointwise 95% Student-t critical values for df 1..30; the asymptotic
# expansion below is accurate to < 2e-6 for df > 30 at this quantile.
_T95 = (
    12.706204736, 4.302652730, 3.182446305, 2.776445105, 2.570581836, 2.446911851,
    2.364624252, 2.306004135, 2.262157163, 2.228138852, 2.200985160, 2.178812830,
    2.160368656, 2.144786688, 2.131449546, 2.119905299, 2.109815578, 2.100922040,
    2.093024054, 2.085963447, 2.079613845, 2.073873068, 2.068657610, 2.063898562,
    2.059538553, 2.055529439, 2.051830516, 2.048407142, 2.045229642, 2.042272456,
)


def _t95(df):
    if df <= len(_T95):
        return _T95[df - 1]
    z = 1.959963984540054
    return (z + (z**3 + z) / (4 * df)
            + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * df**2)
            + (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / (384 * df**3))


def chart_regression_fits(data):
    """Unweighted OLS and pointwise mean-response CI from canonical samples.

    Normalize both axes before computing moments to avoid squaring huge or
    tiny physical coordinates. Never modify observations, extrapolate, or
    treat measurement-error bars as sampling confidence intervals. Return
    None for unsupported/degenerate data or nonrepresentable output.
    """
    xs = data.get("xValues")
    if (not xs or len(xs) < 3 or data.get("xScale", "linear") != "linear"
            or data.get("yScale", "linear") != "linear"):
        return None
    xmin, xmax = min(xs), max(xs)
    span = xmax - xmin
    if not math.isfinite(span) or span <= 0:
        return None
    n = len(xs)
    u = [(x - xmin) / span for x in xs]
    umean = sum(u) / n
    suu = sum((x - umean)**2 for x in u)
    if suu <= 0:
        return None
    rows = data.get("series") or [{"name": "", "values": data.get("values", []),
                                    "yError": data.get("yError")}]
    fits = []
    all_y = []
    for row in rows:
        ys = row["values"]
        if len(ys) != n:
            return None
        ymin, ymax = min(ys), max(ys)
        yspan = ymax - ymin
        if not math.isfinite(yspan):
            return None
        yscale = yspan or 1.0
        v = [(y - ymin) / yscale for y in ys]
        vmean = sum(v) / n
        beta = sum((x - umean) * (y - vmean) for x, y in zip(u, v)) / suu
        sse = sum((y - (vmean + beta * (x - umean)))**2 for x, y in zip(u, v))
        sst = sum((y - vmean)**2 for y in v)
        r2 = max(0.0, min(1.0, 1.0 - sse / sst)) if sst else None
        sigma = math.sqrt(sse / (n - 2))
        samples = []
        # The line and confidence band are sampled over the observed X span.
        # The same fixed 65 positions are used by the SVG renderer.
        for i in range(65):
            p = i / 64
            x = xmin + p * span
            mean = vmean + beta * (p - umean)
            half = _t95(n - 2) * sigma * math.sqrt(1 / n + (p - umean)**2 / suu)
            point = (x, ymin + mean * yscale,
                     ymin + (mean - half) * yscale, ymin + (mean + half) * yscale)
            if not all(math.isfinite(value) for value in point):
                return None
            samples.append(point)
            all_y.extend(point[2:])
        for i, value in enumerate(ys):
            minus, plus = row["yError"][i] if row.get("yError") else (0, 0)
            all_y.extend((value - minus, value + plus))
        fits.append({"n": n, "r2": r2, "points": samples})
    if not math.isfinite(max(all_y) - min(all_y)):
        return None
    return fits


# Agglomerative average-linkage heatmap clustering. This operates solely on
# original finite matrix observations. Returned order/merge trees are
# presentation metadata and never mutate the stored chart or numeric values.
def heatmap_cluster_axis(vectors):
    from heapq import heappop, heappush

    n = len(vectors)
    if n == 0:
        return {"order": [], "tree": None}
    active = {
        i: {"id": i, "minLeaf": i, "leaf": i, "height": 0.0, "depth": 0, "size": 1}
        for i in range(n)
    }
    if n == 1:
        return {"order": [0], "tree": active[0]}

    scale = max(abs(value) for row in vectors for value in row) or 1.0
    normalized = [[value / scale for value in row] for row in vectors]
    distances = {}
    heap = []

    def key(a, b):
        return (min(a, b), max(a, b))

    for i in range(n):
        for j in range(i + 1, n):
            distance = math.sqrt(sum((a - b)**2 for a, b in zip(normalized[i], normalized[j])))
            distances[(i, j)] = distance
            heappush(heap, (distance, i, j, i, j))

    next_id = n
    while len(active) > 1:
        while True:
            distance, _, _, aid, bid = heappop(heap)
            if aid in active and bid in active:
                break
        a, b = active.pop(aid), active.pop(bid)
        left, right = (a, b) if a["minLeaf"] < b["minLeaf"] else (b, a)
        merged = {
            "id": next_id, "minLeaf": min(a["minLeaf"], b["minLeaf"]),
            "size": a["size"] + b["size"], "height": distance,
            "depth": max(a["depth"], b["depth"]) + 1,
            "left": left, "right": right,
        }
        for other in active.values():
            d = ((a["size"] * distances[key(aid, other["id"])]
                  + b["size"] * distances[key(bid, other["id"])]) / merged["size"])
            distances[key(next_id, other["id"])] = d
            first, second = sorted((merged["minLeaf"], other["minLeaf"]))
            heappush(heap, (d, first, second, next_id, other["id"]))
        active[next_id] = merged
        next_id += 1

    tree = next(iter(active.values()))

    def order(node):
        if "leaf" in node:
            return [node["leaf"]]
        return order(node["left"]) + order(node["right"])

    return {"order": order(tree), "tree": tree}


def heatmap_cluster_plan(matrix, mode=None):
    rows = len(matrix)
    columns = len(matrix[0]) if rows else 0
    row = heatmap_cluster_axis(matrix) if mode in ("rows", "both") else None
    col = (heatmap_cluster_axis([[row[j] for row in matrix] for j in range(columns)])
           if mode in ("columns", "both") else None)
    return {
        "rowOrder": row["order"] if row else list(range(rows)),
        "columnOrder": col["order"] if col else list(range(columns)),
        "rowTree": row["tree"] if row else None,
        "columnTree": col["tree"] if col else None,
    }


def raincloud_density(values):
    """Scott/Silverman normal-reference Gaussian KDE on the observed span.

    Compute in normalized coordinates, return (original value, relative
    density) points. Each group's peak is scaled to 1 for visual shape
    comparison, not absolute probability-density comparison. Constant
    samples have no estimable bandwidth; return no cloud for that group.
    """
    if len(values) < 2:
        return []
    lo, hi = min(values), max(values)
    span = hi - lo
    if not math.isfinite(span) or span <= 0:
        return []
    u = [(value - lo) / span for value in values]
    avg = sum(u) / len(u)
    sd = math.sqrt(sum((value - avg)**2 for value in u) / (len(u) - 1))
    bandwidth = 1.06 * sd * len(u)**(-0.2)
    if bandwidth <= 0 or not math.isfinite(bandwidth):
        return []
    estimates = []
    for i in range(65):
        q = i / 64
        density = sum(math.exp(-0.5 * ((q - value) / bandwidth)**2) for value in u)
        estimates.append((lo + q * span, density))
    peak = max(density for _, density in estimates)
    return [(value, density / peak) for value, density in estimates] if peak else []


def raincloud_jitter(index):
    """Stable visual offset; never change or randomize a measurement."""
    return min(0.43, max(0.15, 0.15 + 0.28 * ((index * 137) % 307) / 306))


def canonicalize_chart_analysis(analysis, chart_type, *, x_scale="linear", y_scale="linear", chart_data=None):
    """Bounded document-state annotations and frozen axes; no data mutation."""
    if not isinstance(analysis, dict):
        raise ChartDataError("Chart analysis must be an object")
    allowed = {"lockX", "lockY", "xRange", "yRange", "annotations", "regression", "distribution", "clustering", "density"}
    if set(analysis) - allowed:
        raise ChartDataError("Unknown chart analysis field")
    if chart_type not in CHART_ANALYSIS_TYPES:
        raise ChartDataError("Chart analysis is only supported on cartesian analysis charts")
    result = {}
    for axis, scale in (("x", x_scale), ("y", y_scale)):
        field = axis + "Range"
        locked = "lock" + axis.upper()
        value = analysis.get(locked, False)
        if type(value) is not bool:
            raise ChartDataError(locked + " must be a boolean")
        result[locked] = value
        if field in analysis:
            bounds = analysis[field]
            if not isinstance(bounds, list) or len(bounds) != 2:
                raise ChartDataError(field + " must contain two bounds")
            lo, hi = (_finite_number(bounds[0], field + " minimum"),
                      _finite_number(bounds[1], field + " maximum"))
            if not lo < hi or not math.isfinite(hi - lo):
                raise ChartDataError(field + " must increase across a finite span")
            if scale == "log10" and lo <= 0:
                raise ChartDataError(field + " must be positive on a log10 axis")
            result[field] = [lo, hi]
        if value and field not in result:
            raise ChartDataError(locked + " requires " + field)
    if "regression" in analysis:
        if (analysis["regression"] != "ols95" or chart_type not in ("line", "scatter")
                or x_scale != "linear" or y_scale != "linear" or chart_data is None
                or chart_regression_fits(chart_data) is None):
            raise ChartDataError("Linear regression requires at least three numeric observations, "
                                 "varying X, linear axes and finite confidence bounds")
        result["regression"] = "ols95"
    if "distribution" in analysis:
        if chart_type != "boxplot" or analysis["distribution"] != "raincloud":
            raise ChartDataError("Raincloud distributions require a box plot")
        result["distribution"] = "raincloud"
    if "clustering" in analysis:
        if chart_type != "heatmap" or analysis["clustering"] not in ("rows", "columns", "both"):
            raise ChartDataError("Heatmap clustering requires a heatmap and rows, columns, or both")
        result["clustering"] = analysis["clustering"]
    if "density" in analysis:
        if (chart_type != "scatter" or analysis["density"] != "hexbin"
                or chart_data is None or len(chart_data.get("series") or [1]) != 1
                or x_scale != "linear" or y_scale != "linear"):
            raise ChartDataError("Hexbin density requires single-series scatter with linear axes")
        result["density"] = "hexbin"
    annotations = analysis.get("annotations", [])
    if not isinstance(annotations, list) or len(annotations) > MAX_CHART_ANNOTATIONS:
        raise ChartDataError("Chart annotations must be a list of at most 60 items")
    result["annotations"] = []
    for index, item in enumerate(annotations):
        where = f"Annotation {index}"
        if not isinstance(item, dict):
            raise ChartDataError(where + " must be an object")
        kind = item.get("kind")
        fields = {
            "label": {"kind", "x", "y", "text"},
            "arrow": {"kind", "x", "y", "x2", "y2"},
            "region": {"kind", "axis", "from", "to"},
        }
        if not isinstance(kind, str) or kind not in fields or set(item) != fields[kind]:
            raise ChartDataError(where + " has invalid fields")
        row = {"kind": kind}
        if kind in ("label", "arrow"):
            for field in (("x", "y", "x2", "y2") if kind == "arrow" else ("x", "y")):
                number = _finite_number(item[field], where + " " + field)
                if (x_scale if field.startswith("x") else y_scale) == "log10" and number <= 0:
                    raise ChartDataError(where + " coordinates must be positive on log10 axes")
                row[field] = number
            if kind == "label":
                row["text"] = _clean_text(item["text"], where + " text")
        else:
            if item["axis"] not in ("x", "y"):
                raise ChartDataError(where + " axis must be x or y")
            lo = _finite_number(item["from"], where + " start")
            hi = _finite_number(item["to"], where + " end")
            if not lo < hi or not math.isfinite(hi - lo):
                raise ChartDataError(where + " region must have increasing finite bounds")
            if (x_scale if item["axis"] == "x" else y_scale) == "log10" and lo <= 0:
                raise ChartDataError(where + " region must be positive on log10 axes")
            row.update({"axis": item["axis"], "from": lo, "to": hi})
        result["annotations"].append(row)
    if chart_type == "heatmap" and (result["lockX"] or result["lockY"]
                                   or "xRange" in result or "yRange" in result
                                   or result["annotations"]):
        raise ChartDataError("Heatmap analysis supports only clustering")
    return result


# Keep the historic per-type canonicalizers intact, including their early
# returns. Analysis is deliberately a post-pass and cannot influence whether
# a line uses the categorical or scientific rendering path.
def canonicalize_chart_data(data, chart_type=None):
    canonical = _canonicalize_chart_data_base(data, chart_type)
    if "analysis" in data and data["analysis"] is not None:
        canonical["analysis"] = canonicalize_chart_analysis(
            data["analysis"], canonical["type"],
            x_scale=canonical.get("xScale", "linear"),
            y_scale=canonical.get("yScale", "linear"),
            chart_data=canonical,
        )
    return canonical



# -- Deterministic Scientific Workbench -> existing ChartSpec adapter --------
#
# The workbench persists only numeric results, input measurements and their
# checksummed receipts. Never send those numbers to the chart-generating LLM:
# transform the *current* verified receipt into a normal ChartSpec instead.
# Limits, error endpoints and finite ranges remain canonicalize_chart_data's
# responsibility, exactly as for every other producer of these chart types.

def _science_axis(label):
    """Split 'Distance (m)' into a display label and a physical unit."""
    if not isinstance(label, str):
        raise ChartDataError("Scientific plot axes must be labeled")
    match = re.fullmatch(r"\s*(.*?)\s+\(([^()]+)\)\s*", label)
    if match and match.group(1).strip():
        return match.group(1).strip(), match.group(2).strip()
    return label.strip(), ""


def _science_numbers(value):
    return (isinstance(value, list) and bool(value)
            and all(type(item) in (int, float) and math.isfinite(item) for item in value))


def _science_table(result):
    table = result.get("table")
    if not isinstance(table, dict):
        raise ChartDataError("This scientific result has no table to chart")
    columns, rows = table.get("columns"), table.get("rows")
    if (not isinstance(columns, list) or not all(isinstance(c, str) for c in columns)
            or not isinstance(rows, list) or not rows
            or any(not isinstance(row, list) or len(row) != len(columns) for row in rows)):
        raise ChartDataError("The scientific result table is not rectangular")
    return columns, rows


def _science_xy(result):
    plot = result.get("plot")
    if isinstance(plot, dict):
        xs, ys = plot.get("x"), plot.get("y")
        if not _science_numbers(xs) or not _science_numbers(ys) or len(xs) != len(ys):
            raise ChartDataError("The scientific XY trace is not chartable")
        x_label, x_unit = _science_axis(plot.get("xLabel", "X"))
        y_label, y_unit = _science_axis(plot.get("yLabel", "Y"))
        payload = {"title": plot.get("title", "Scientific measurements"),
                   "xValues": xs, "values": ys, "xAxis": x_label or "X",
                   "yAxis": y_label or "Y"}
        if x_unit:
            payload["xUnit"] = x_unit
        if y_unit:
            payload["yUnit"] = y_unit
        # A coincident table can carry measured per-row uncertainty. Only
        # reuse it when BOTH plotted coordinates exactly match the table.
        table = result.get("table")
        if isinstance(table, dict):
            cols, rows = table.get("columns"), table.get("rows")
            if (isinstance(cols, list) and isinstance(rows, list)
                    and len(rows) == len(xs) and len(cols) >= 3
                    and all(isinstance(row, list) and len(row) == len(cols) for row in rows)):
                x_at = next((i for i, col in enumerate(cols) if col == plot.get("xLabel")), None)
                y_at = next((i for i, col in enumerate(cols) if col == plot.get("yLabel")), None)
                if (x_at is not None and y_at is not None
                        and [row[x_at] for row in rows] == xs
                        and [row[y_at] for row in rows] == ys):
                    for i, col in enumerate(cols):
                        if i in (x_at, y_at):
                            continue
                        if not re.search(r"\b(standard\s+)?(uncertainty|error)\b", col, re.I):
                            continue
                        errors = [row[i] for row in rows]
                        if _science_numbers(errors) and all(v >= 0 for v in errors):
                            payload["yError"] = errors
                            break
        return payload
    values = result.get("values", {})
    if (type(values.get("estimate")) in (int, float)
            and type(values.get("combined_standard_uncertainty")) in (int, float)):
        estimate = values["estimate"]
        error = values["combined_standard_uncertainty"]
        if not math.isfinite(estimate) or not math.isfinite(error) or error < 0:
            raise ChartDataError("The scientific uncertainty cannot be plotted")
        # A coverage interval, when available, is asymmetric in general.
        lower, upper = values.get("coverage_lower"), values.get("coverage_upper")
        uncertainty = ([estimate - lower, upper - estimate]
                       if type(lower) in (int, float) and type(upper) in (int, float)
                       else [error, error])
        return {"title": "Measurement with uncertainty", "xValues": [0],
                "values": [estimate], "xAxis": "Measurement", "yAxis": "Estimate",
                "yError": [uncertainty]}
    columns, rows = _science_table(result)
    numbers = [i for i in range(len(columns)) if _science_numbers([row[i] for row in rows])]
    if len(numbers) < 2:
        raise ChartDataError("A scatter chart needs two numeric columns")
    x_at, y_at = numbers[:2]
    x_label, x_unit = _science_axis(columns[x_at])
    y_label, y_unit = _science_axis(columns[y_at])
    payload = {"title": "Scientific measurements", "xValues": [row[x_at] for row in rows],
               "values": [row[y_at] for row in rows], "xAxis": x_label or "X", "yAxis": y_label or "Y"}
    if x_unit:
        payload["xUnit"] = x_unit
    if y_unit:
        payload["yUnit"] = y_unit
    for i, col in enumerate(columns):
        if i in (x_at, y_at) or not re.search(r"\b(standard\s+)?(uncertainty|error)\b", col, re.I):
            continue
        errs = [row[i] for row in rows]
        if _science_numbers(errs) and all(v >= 0 for v in errs):
            payload["yError"] = errs
            break
    return payload


def _science_boxplot(result):
    columns, rows = _science_table(result)
    if len(columns) == 2 and all(isinstance(row[0], str) for row in rows):
        groups = {}
        for label, value in rows:
            if not isinstance(label, str) or type(value) not in (int, float) or not math.isfinite(value):
                raise ChartDataError("A grouped box plot needs text labels and numeric observations")
            groups.setdefault(label, []).append(value)
        if groups and all(len(values) >= 2 for values in groups.values()):
            return {"title": "Scientific distributions", "xAxis": columns[0], "yAxis": columns[1],
                    "groups": [{"label": label, "values": values} for label, values in groups.items()]}
    groups = [{"label": name, "values": [row[i] for row in rows]}
              for i, name in enumerate(columns)
              if len(rows) >= 2 and _science_numbers([row[i] for row in rows])]
    if not groups:
        raise ChartDataError("A box plot needs at least two observations per numeric column")
    return {"title": "Scientific distributions", "groups": groups, "xAxis": "Column", "yAxis": "Value"}


def _science_heatmap(result):
    columns, rows = _science_table(result)
    has_labels = (len(columns) >= 2 and all(isinstance(row[0], str) for row in rows)
                  and all(row[0] for row in rows) and len({row[0] for row in rows}) == len(rows))
    numeric = columns[1:] if has_labels else columns
    matrix = [row[1:] if has_labels else row for row in rows]
    if not numeric or not all(_science_numbers(row) for row in matrix):
        raise ChartDataError("A heatmap needs a dense numeric result table")
    return {"title": "Scientific result heatmap", "xAxis": "Columns", "yAxis": "Rows",
            "columnLabels": numeric, "rowLabels": [str(row[0]) for row in rows] if has_labels
            else [str(i + 1) for i in range(len(rows))], "matrix": matrix}


def _science_fit(result):
    """Observed response, fitted prediction and pointwise 95% mean-response CI.

    The existing line renderer draws the fitted model over the measurements,
    with upper/lower confidence envelope curves and asymmetric y error bars
    on its fitted series. Do not treat residuals as measured responses or
    coefficient intervals as prediction intervals.
    """
    pack, op = result.get("provenance", {}).get("packId"), result.get("operationId")
    inputs, values = result.get("inputs", {}), result.get("values", {})
    if pack == "statistics" and op == "linear_regression":
        xs, ys = inputs.get("x"), inputs.get("y")
        if not _science_numbers(xs) or not _science_numbers(ys) or len(xs) != len(ys) or len(xs) < 3:
            raise ChartDataError("A fitted confidence band requires at least three paired observations")
        a, b = values["intercept"], values["slope"]
        from graphlink_plugins.scientific.packs.fitting import _student_t_ppf
        average = math.fsum(xs) / len(xs)
        sxx = math.fsum((x - average) ** 2 for x in xs)
        if sxx <= 0:
            raise ChartDataError("A fitted confidence band requires varying x coordinates")
        residuals = [y - (a + b * x) for x, y in zip(xs, ys)]
        variance = math.fsum(r * r for r in residuals) / (len(xs) - 2)
        multiplier = _student_t_ppf(0.975, len(xs) - 2)
        fitted = [a + b * x for x in xs]
        uncertainty = [multiplier * math.sqrt(variance * (1 / len(xs) + (x - average) ** 2 / sxx))
                       for x in xs]
        title = "Linear regression with 95% confidence envelope"
    elif pack == "fitting" and op == "weighted_linear_least_squares":
        predictors, ys = inputs.get("predictors"), inputs.get("response")
        if (not isinstance(predictors, list) or not predictors
                or not all(isinstance(row, list) and len(row) == 1 for row in predictors)
                or not _science_numbers(ys) or len(predictors) != len(ys)):
            raise ChartDataError("Only single-predictor weighted fits can be plotted in XY")
        xs = [row[0] for row in predictors]
        if not _science_numbers(xs):
            raise ChartDataError("The fitted predictor is not numeric")
        a, b = values["coefficient_0"], values["coefficient_1"]
        table = result.get("table", {})
        covariance = table.get("rows", []) if isinstance(table, dict) else []
        if len(covariance) != 2 or any(not isinstance(row, list) or len(row) != 3 for row in covariance):
            raise ChartDataError("A weighted fit needs its stored covariance table")
        c00, c01, c11 = covariance[0][1], covariance[0][2], covariance[1][2]
        from graphlink_plugins.scientific.packs.fitting import _student_t_ppf
        multiplier = _student_t_ppf(0.975, values["degrees_of_freedom"])
        fitted = [a + b * x for x in xs]
        uncertainty = [multiplier * math.sqrt(max(0, c00 + 2 * x * c01 + x * x * c11))
                       for x in xs]
        title = "Weighted linear fit with 95% confidence envelope"
    elif pack == "fitting" and op == "nonlinear_least_squares":
        xs, ys = inputs.get("x"), inputs.get("y")
        if not _science_numbers(xs) or not _science_numbers(ys) or len(xs) != len(ys):
            raise ChartDataError("The nonlinear fit requires paired measurements")
        from graphlink_plugins.scientific.packs.fitting import _d, _model, _normal_solve, _student_t_ppf
        parameters = [values[f"parameter_{i}"] for i in range(values["parameters"])]
        weights = inputs["weights"]
        with localcontext() as context:
            context.prec = 76
            beta = list(map(_d, parameters))
            evaluations = [_model(inputs["model"].strip().lower(), _d(x), beta) for x in xs]
            derivative = [jac for _, jac in evaluations]
            size = len(beta)
            gram = [[sum((_d(w) * row[i] * row[j] for w, row in zip(weights, derivative)), Decimal(0))
                     for j in range(size)] for i in range(size)]
            _, inverse = _normal_solve(gram, [Decimal(0)] * size, inverse=True)
            scale = _d(values["reduced_chi_square"])
            multiplier = _d(_student_t_ppf(0.975, values["degrees_of_freedom"]))
            fitted = [float(predicted) for predicted, _ in evaluations]
            uncertainty = [float(multiplier * max(Decimal(0),
                           sum((jac[i] * inverse[i][j] * jac[j]
                                for i in range(size) for j in range(size)), Decimal(0)) * scale).sqrt())
                           for jac in derivative]
        title = "Nonlinear fit with 95% confidence envelope"
    else:
        raise ChartDataError("This result has no fitted model with a supported confidence band")

    if len(xs) > MAX_SERIES_POINTS // 4:
        raise ChartDataError("Fitted charts support at most 75 observations and 4 series")
    points = sorted(zip(xs, ys, fitted, uncertainty), key=lambda row: row[0])
    xs, ys, fitted, uncertainty = map(list, zip(*points))
    if not (all(math.isfinite(v) and v >= 0 for v in uncertainty)
            and all(math.isfinite(v) for v in fitted)):
        raise ChartDataError("The fitted confidence band exceeds the chart numeric range")
    if not all(math.isfinite(y - delta) and math.isfinite(y + delta)
               for y, delta in zip(fitted, uncertainty)):
        raise ChartDataError("The fitted confidence band has unrepresentable endpoints")
    return {"title": title, "xAxis": "Predictor", "yAxis": "Response", "xValues": xs,
            "series": [{"name": "Observed", "values": ys},
                       {"name": "Fit", "values": fitted, "yError": uncertainty},
                       {"name": "95% CI lower", "values": [y - d for y, d in zip(fitted, uncertainty)]},
                       {"name": "95% CI upper", "values": [y + d for y, d in zip(fitted, uncertainty)]}]}


def scientific_result_chart(result: dict, chart_type: str) -> dict:
    """Convert one *verified* Scientific Workbench result to existing ChartSpec.

    This does not validate a receipt or authorize access to a scientific
    result: the chart intent must do both before calling this adapter.
    """
    if not isinstance(result, dict):
        raise ChartDataError("Run a scientific calculation before charting it")
    if chart_type == "scatter":
        payload = _science_xy(result)
    elif chart_type == "line":
        payload = _science_fit(result)
    elif chart_type == "boxplot":
        payload = _science_boxplot(result)
    elif chart_type == "heatmap":
        payload = _science_heatmap(result)
    else:
        raise ChartDataError("Scientific results support scatter, fitted line, box plot or heatmap")
    payload["type"] = chart_type
    return canonicalize_chart_data(payload, chart_type)
