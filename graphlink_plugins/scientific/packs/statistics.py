"""Descriptive and bivariate statistics for finite observations."""

import statistics
from decimal import Decimal, localcontext
from fractions import Fraction

from graphlink_plugins.scientific.core import Field, Operation


def _describe(data):
    observations = data["observations"]
    if len(observations) < 2:
        raise ValueError("Sample statistics require at least two observations.")
    ordered = sorted(observations)
    middle_pair = ordered[(len(ordered) - 1) // 2 : len(ordered) // 2 + 1]
    return {
        "summary": "Summary of the supplied sample. Standard deviation uses n − 1 degrees of freedom.",
        "values": {
            "count": len(observations),
            "mean": statistics.mean(observations),
            "median": statistics.mean(middle_pair),
            "sample_sd": statistics.stdev(observations),
            "minimum": min(observations),
            "maximum": max(observations),
        },
    }


def _sqrt_fraction(value):
    # Variance can exceed the float range even when its square root fits.
    with localcontext() as ctx:
        ctx.prec = 40
        return float((Decimal(value.numerator) / Decimal(value.denominator)).sqrt())


def _regression(data):
    x, y = data["x"], data["y"]
    if len(x) != len(y) or len(x) < 2:
        raise ValueError("Supply at least two paired x and y observations.")
    # Keep centered sums exact for binary input. This avoids catastrophic
    # cancellation when coordinates share a large offset or differ in scale.
    mean_x = statistics.mean(map(Fraction, x))
    mean_y = statistics.mean(map(Fraction, y))
    dx = [Fraction(v) - mean_x for v in x]
    dy = [Fraction(v) - mean_y for v in y]
    xx = sum(v * v for v in dx)
    if not xx:
        raise ValueError("A regression line requires distinct x coordinates.")
    xy = sum(a * b for a, b in zip(dx, dy))
    slope = xy / xx
    intercept = mean_y - slope * mean_x
    values = {"count": len(x), "slope": float(slope), "intercept": float(intercept)}
    yy = sum(v * v for v in dy)
    summary = "Least-squares line with an intercept, fitted to paired observations."
    if yy:
        r_squared = xy * xy / (xx * yy)
        r = _sqrt_fraction(r_squared)
        values.update({"pearson_r": -r if xy < 0 else r, "r_squared": float(r_squared)})
    else:
        summary += " The response is constant, so correlation and R² are undefined."
    limits = [min(x), max(x)]
    fitted = [float(slope * Fraction(v) + intercept) for v in limits]
    return {
        "summary": summary,
        "values": values,
        "plot": {"title": "Fitted regression line", "xLabel": "x", "yLabel": "Predicted y", "x": limits, "y": fitted},
    }


def _weighted(data):
    observations, weights = data["observations"], data["weights"]
    if len(observations) != len(weights) or not observations:
        raise ValueError("Supply one weight per observation.")
    if min(weights) < 0 or max(weights) == 0:
        raise ValueError("Weights must be nonnegative and at least one must be positive.")
    positive = [(Fraction(v), Fraction(w)) for v, w in zip(observations, weights) if w > 0]
    total = sum(w for _, w in positive)
    mean = sum(v * w for v, w in positive) / total
    variance = sum(w * (v - mean) ** 2 for v, w in positive) / total
    return {
        "summary": "Weighted mean and population standard deviation using the supplied relative weights.",
        "values": {
            "positive_weights": len(positive),
            "weighted_mean": float(mean),
            "population_sd": _sqrt_fraction(variance),
        },
    }



def _grouped_table(data):
    from graphlink_plugins.scientific.data_table import read_table

    table = read_table(data["table"])
    value_column = data["value_column"]
    group_column = data["group_column"]
    groups: dict[str, list[float]] = {}
    excluded = 0
    for row in table["rows"]:
        value, group = row[value_column], row[group_column]
        if value is None or group is None:
            excluded += 1
            continue
        label = ("true" if group else "false") if type(group) is bool else str(group)
        groups.setdefault(label, []).append(value)
        if len(groups) > 64:
            raise ValueError("Group summaries support at most 64 distinct groups.")
    if not groups:
        raise ValueError("No complete group/measurement rows remain.")
    rows = [[label, len(values), statistics.mean(values)]
            for label, values in sorted(groups.items())]
    used = sum(row[1] for row in rows)
    unit = table["columns"][value_column]["unit"]
    return {
        "summary": f"Grouped {used} complete rows into {len(groups)} groups; excluded {excluded} rows with missing data.",
        "values": {"row_count": len(table["rows"]), "rows_used": used,
                   "rows_excluded": excluded, "group_count": len(groups)},
        "table": {"columns": [table["columns"][group_column]["label"], "Count",
                              f"Mean ({unit})" if unit else "Mean"], "rows": rows},
    }

OPERATIONS = (
    Operation(
        "describe",
        "Describe a sample",
        "Compute count, arithmetic mean, median, range endpoints and sample standard deviation.",
        (Field("observations", "Observations", "vector", description="At least two finite values."),),
        {"observations": [2, 4, 4, 5, 7, 8]},
        _describe,
        headline="mean",
    ),
    Operation(
        "linear_regression",
        "Fit a regression line",
        "Fit y = intercept + slope × x and calculate Pearson correlation and R² for a varying response.",
        (Field("x", "Predictor", "vector"), Field("y", "Response", "vector")),
        {"x": [1, 2, 3, 4, 5], "y": [2, 4, 5, 8, 11]},
        _regression,
        headline="slope",
        paired_fields=(("x", "y"),),
    ),
    Operation(
        "weighted_summary",
        "Summarize weighted observations",
        "Compute the mean and population standard deviation with nonnegative relative weights.",
        (Field("observations", "Observations", "vector"), Field("weights", "Relative weights", "vector")),
        {"observations": [2, 4, 10], "weights": [1, 2, 1]},
        _weighted,
        headline="weighted_mean",
        paired_fields=(("observations", "weights"),),
    ),
    Operation(
        "grouped_table_summary",
        "Summarize a typed table by group",
        "Use every row of a connected typed table to calculate group means. Missing group/value pairs are excluded; at most 64 groups.",
        (Field("table", "Typed table", "table", description="Choose a connected imported table; its complete asset stays outside the graph."),
         Field("value_column", "Numeric measurement column", "column", table_field="table",
               column_types=("numeric",)),
         Field("group_column", "Group column", "column", table_field="table")),
        {"table": None, "value_column": None, "group_column": None},
        _grouped_table,
        headline="group_count",
    ),
)
