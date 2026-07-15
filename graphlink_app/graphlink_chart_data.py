"""Canonical chart-data validation shared by generation, rendering, and storage."""

from __future__ import annotations

import math


SUPPORTED_CHART_TYPES = frozenset({"bar", "line", "pie", "histogram", "sankey"})
MAX_SERIES_POINTS = 300
MAX_SANKEY_FLOWS = 300
MAX_LABEL_LENGTH = 160
MAX_TITLE_LENGTH = 200


class ChartDataError(ValueError):
    """Raised when a chart payload cannot be converted to the canonical schema."""


def _finite_number(value, field_name):
    if isinstance(value, bool):
        raise ChartDataError(f"{field_name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
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
        "type": expected_type,
        "title": _clean_text(
            data.get("title"),
            "Chart title",
            default=f"{expected_type.title()} Chart",
            max_length=MAX_TITLE_LENGTH,
        ),
    }

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
        aggregated[key] = aggregated.get(key, 0.0) + value

    result["flows"] = [
        {"source": source, "target": target, "value": value}
        for (source, target), value in sorted(aggregated.items())
    ]
    _assert_acyclic(result["flows"])
    return result

