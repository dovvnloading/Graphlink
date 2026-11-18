"""Canonical chart-data validation shared by generation, rendering, and storage."""

from __future__ import annotations

import math


SUPPORTED_CHART_TYPES = frozenset({"bar", "line", "pie", "histogram", "sankey", "scatter", "boxplot", "heatmap"})
MAX_SERIES_POINTS = 300
MAX_NAMED_SERIES = 12
MAX_SANKEY_FLOWS = 300
MAX_LABEL_LENGTH = 160
MAX_TITLE_LENGTH = 200

# ADR-013 stage 13.1: the canonical shape's own schema version - every spec
# canonicalize_chart_data produces today is version 1. Bumped only when this
# function's OUTPUT shape changes incompatibly (a renamed/removed key, a new
# required field) - not on every chart-type addition, which stays additive.
CHART_SPEC_VERSION = 1


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
            "content": f"Chart type: {chart_type}\n\n--- SOURCE TEXT ---\n{source_text}",
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
        if len(raw_x) > MAX_SERIES_POINTS:
            raise ChartDataError(f"Charts support at most {MAX_SERIES_POINTS} data points")
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
        if len(labels) > MAX_SERIES_POINTS:
            raise ChartDataError(f"Charts support at most {MAX_SERIES_POINTS} data points")
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
            if len(values) > MAX_SERIES_POINTS:
                raise ChartDataError(f"Charts support at most {MAX_SERIES_POINTS} data points")
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
        if len(raw_values) > MAX_SERIES_POINTS:
            raise ChartDataError(f"Charts support at most {MAX_SERIES_POINTS} data points")
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
    if count > MAX_SERIES_POINTS or len(series) * count > MAX_SERIES_POINTS:
        raise ChartDataError(f"Charts support at most {MAX_SERIES_POINTS} total series points")
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


def canonicalize_chart_data(data, chart_type=None):
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

