"""Scientific chart validation and all historical eval fixture regressions."""

import json
from pathlib import Path

import pytest

from graphlink_chart_data import ChartDataError, canonicalize_chart_data
from graphlink_chart_rendering import render_chart_png, render_chart_svg


def _scatter(**overrides):
    spec = {
        "type": "scatter",
        "title": "Calibration",
        "xValues": [1, 10, 100],
        "series": [
            {"name": "Control", "values": [0.9, 1.8, 3.2],
             "xError": [0.05, [1, 2], 10],
             "yError": [0.1, [0.1, 0.3], 0.2]},
            {"name": "Treatment", "values": [1.1, 2.1, 3.5]},
        ],
        "xScale": "log10", "yScale": "linear",
        "xAxis": "Concentration", "yAxis": "Response",
        "xUnit": "mg/L", "yUnit": "mV",
    }
    spec.update(overrides)
    return spec


def test_scatter_canonicalizes_symmetric_and_asymmetric_errors_and_named_series():
    spec = canonicalize_chart_data(_scatter(), "scatter")
    assert spec["version"] == 1
    assert spec["type"] == "scatter"
    assert spec["xValues"] == [1.0, 10.0, 100.0]
    assert spec["series"][0]["xError"] == [[0.05, 0.05], [1.0, 2.0], [10.0, 10.0]]
    assert spec["series"][0]["yError"] == [[0.1, 0.1], [0.1, 0.3], [0.2, 0.2]]
    assert spec["series"][1]["name"] == "Treatment"
    assert canonicalize_chart_data(spec, "scatter") == spec


@pytest.mark.parametrize("key,value,match", [
    ("xValues", [1.0, float("nan"), 3.0], "xValues at index 1.*finite"),
    ("xValues", [1.0, float("inf"), 3.0], "xValues at index 1.*finite"),
    ("values", [2, -float("inf"), 4], "Value at index 1.*finite"),
    ("xError", [0.1, float("nan"), 0.2], "xError at index 1.*finite"),
    ("yError", [[0, float("inf")], 0.1, 0.1], "upper magnitude.*finite"),
    ("xError", [0.1, -0.1, 0.1], "magnitudes must be nonnegative"),
    ("yError", [[-0.1, 0.2], 0.1, 0.1], "magnitudes must be nonnegative"),
    ("yError", [[0.1, 0.2, 0.3], 0.1, 0.1], "exactly two"),
    ("xError", [0.1, 0.2], "length must match"),
    ("xValues", [1.0, 2.0], "length must match"),
    ("xScale", "ln", "must be linear or log10"),
])
def test_scatter_strictly_rejects_invalid_numeric_inputs(key, value, match):
    spec = {"type": "scatter", "xValues": [1, 2, 3], "values": [2, 3, 4]}
    spec[key] = value
    with pytest.raises(ChartDataError, match=match):
        canonicalize_chart_data(spec, "scatter")


@pytest.mark.parametrize("data,match", [
    ({"xScale": "log10", "xValues": [0, 10, 100]}, "log10 axis"),
    ({"xScale": "log10", "xValues": [-1, 10, 100]}, "log10 axis"),
    ({"xScale": "log10", "xValues": [1, 10, 100], "xError": [1, 0, 0]}, "lower error endpoint"),
    ({"yScale": "log10", "values": [0, 2, 3]}, "log10 axis"),
    ({"yScale": "log10", "values": [1, 2, 3], "yError": [1, 0, 0]}, "lower error endpoint"),
    ({"values": [1e308, 1e308, 1e308], "yError": [1e308, 0, 0]},
     "non-finite error endpoint"),
    ({"series": [{"name": "One", "values": [1, 2, 3]},
                 {"name": "Two", "values": [1, 2]}]}, "length must match"),
    ({"series": [{"name": "Full", "values": [1] * 300},
                 {"name": "Short", "values": [1]}]}, "length must match"),
    ({"series": [{"name": "Same", "values": [1, 2, 3]},
                 {"name": "Same", "values": [2, 3, 4]}]}, "Duplicate series name"),
    ({"series": [
        {"name": "Lower", "values": [0, 0, 0],
         "yError": [[1.5e308, 0], [1.5e308, 0], [1.5e308, 0]]},
        {"name": "Upper", "values": [0, 0, 0],
         "yError": [[0, 1.5e308], [0, 1.5e308], [0, 1.5e308]]},
    ]}, "range including all series errors"),
])
def test_scientific_rejects_bad_log_bounds_and_series_lengths(data, match):
    base = {"type": "scatter", "xValues": [1, 10, 100], "values": [1, 2, 3]}
    if "series" in data:
        base.pop("values")
    base.update(data)
    with pytest.raises(ChartDataError, match=match):
        canonicalize_chart_data(base, "scatter")


def test_uncertainty_span_overflow_is_rejected_before_reaching_both_renderers():
    # All three coordinates and each lower/upper error endpoint are finite.
    # Collectively the interval is too wide for a finite Cartesian domain.
    with pytest.raises(ChartDataError, match="range including error endpoints must be finite"):
        canonicalize_chart_data({
            "type": "scatter", "xValues": [1, 2, 3], "values": [0, 0, 0],
            "yError": [1e308, 1e308, 1e308],
        }, "scatter")


def test_existing_histogram_and_sankey_canonical_shapes_stay_exactly_the_same():
    histogram = canonicalize_chart_data({"type": "histogram", "values": [1, 2, 3, 4]}, "histogram")
    assert histogram == {
        "version": 1, "type": "histogram", "title": "Histogram Chart",
        "values": [1.0, 2.0, 3.0, 4.0], "bins": 4,
        "xAxis": "Value", "yAxis": "Frequency",
    }
    sankey = canonicalize_chart_data({"type": "sankey", "flows": [
        {"source": "Start", "target": "End", "value": 1},
    ]}, "sankey")
    assert sankey == {
        "version": 1, "type": "sankey", "title": "Sankey Chart",
        "flows": [{"source": "Start", "target": "End", "value": 1.0}],
    }


def test_model_schema_accepts_named_series_without_forcing_legacy_top_level_values():
    from graphlink_chart_data import CHART_JSON_SCHEMAS

    for chart_type in ("line", "scatter"):
        schema = CHART_JSON_SCHEMAS[chart_type]
        assert "series" in schema["properties"]
        assert "values" not in schema["required"]
        assert {"required": ["series"]} in schema["anyOf"]


def test_scientific_line_can_use_categorical_labels_and_y_error_without_changing_legacy_line():
    old = {"type": "line", "labels": ["A", "B"], "values": [2, 4]}
    assert canonicalize_chart_data(old, "line") == {
        "version": 1, "type": "line", "title": "Line Chart",
        "labels": ["A", "B"], "values": [2.0, 4.0],
        "xAxis": "Sequence", "yAxis": "Value",
    }
    new = canonicalize_chart_data({**old, "yError": [0.2, [0.1, 0.3]]}, "line")
    assert new["yError"] == [[0.2, 0.2], [0.1, 0.3]]
    assert new["values"] == [2, 4]
    with pytest.raises(ChartDataError, match="xError requires numeric xValues"):
        canonicalize_chart_data({**old, "xError": [1, 1]}, "line")


def test_all_existing_chart_eval_fixtures_are_byte_shape_compatible():
    folder = Path(__file__).parents[1] / "evals" / "fixtures" / "chart"
    historic = sorted(path for path in folder.glob("*.json")
                      if path.name not in {"scatter_calibration_error_bars.json",
                                           "boxplot_grouped_measurements.json",
                                           "heatmap_assay_matrix.json"})
    assert {p.stem for p in historic} == {
        "bar_support_tickets", "line_daily_active_users", "pie_engineering_budget"
    }
    for path in historic:
        fixture = json.loads(path.read_text(encoding="utf-8"))
        result = canonicalize_chart_data(json.loads(fixture["scripted_response"]),
                                         fixture["input"]["chart_type"])
        assert result == fixture["expected"], path.name
        assert canonicalize_chart_data(result, result["type"]) == result


def test_scatter_fixture_validates_identically_to_scripted_eval_expected():
    folder = Path(__file__).parents[1] / "evals" / "fixtures" / "chart"
    fixture = json.loads((folder / "scatter_calibration_error_bars.json").read_text(encoding="utf-8"))
    actual = canonicalize_chart_data(json.loads(fixture["scripted_response"]), "scatter")
    assert fixture["expected"]["type"] == "scatter"
    assert actual == fixture["expected"]


def test_matplotlib_png_and_svg_both_render_scatter_errors_and_log_scales(monkeypatch):
    from matplotlib.axes import Axes

    seen = []
    original = Axes.errorbar

    def spy(self, xs, ys, **kwargs):
        seen.append((list(xs), list(ys), kwargs))
        return original(self, xs, ys, **kwargs)

    monkeypatch.setattr(Axes, "errorbar", spy)
    png = render_chart_png("scatter", _scatter(), 640, 480)
    svg = render_chart_svg("scatter", _scatter(), 640, 480)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"<svg " in svg
    assert len(seen) == 4  # two series for each format
    for xs, ys, kwargs in (seen[0], seen[2]):
        assert xs == [1.0, 10.0, 100.0]
        assert ys == [0.9, 1.8, 3.2]
        assert kwargs["xerr"] == ([0.05, 1.0, 10.0], [0.05, 2.0, 10.0])
        assert kwargs["yerr"] == ([0.1, 0.1, 0.2], [0.1, 0.3, 0.2])
        assert kwargs["linestyle"] == "none"
        assert kwargs["label"] == "Control"
    assert b"legend" in svg


def test_scientific_line_exports_connected_series_without_affecting_legacy():
    payload = {"type": "line", "labels": ["A", "B", "C"],
               "series": [{"name": "One", "values": [1, 2, 3]},
                          {"name": "Two", "values": [3, 2, 1]}]}
    svg = render_chart_svg("line", payload, 640, 480)
    assert b"<svg " in svg
    assert b"legend" in svg

def test_pr586_saved_scatter_without_nested_type_recovers_the_same_scientific_data():
    canonical = canonicalize_chart_data(_scatter(), "scatter")
    saved_by_pr586 = {key: value for key, value in canonical.items() if key != "type"}
    assert canonicalize_chart_data(saved_by_pr586, "scatter") == canonical
    assert saved_by_pr586["series"][0]["yError"] == canonical["series"][0]["yError"]



# Scientific Workbench conversion uses ordinary ChartSpec, not a new renderer.
def test_workbench_xy_preserves_units_error_bars_and_receipt():
    from graphlink_chart_data import scientific_result_chart

    result = {"plot": {"title": "Sensor response", "x": [0, 1, 2], "y": [2, 3, 5],
                       "xLabel": "Time (s)", "yLabel": "Voltage (mV)"},
              "table": {"columns": ["Time (s)", "Voltage (mV)", "standard uncertainty"],
                        "rows": [[0, 2, 0.1], [1, 3, 0.2], [2, 5, 0.3]]}}
    spec = scientific_result_chart(result, "scatter")
    assert spec["xValues"] == [0, 1, 2]
    assert spec["values"] == [2, 3, 5]
    assert (spec["xAxis"], spec["xUnit"], spec["yAxis"], spec["yUnit"]) == ("Time", "s", "Voltage", "mV")
    assert spec["yError"] == [[0.1, 0.1], [0.2, 0.2], [0.3, 0.3]]
    receipt = {"sourceKey": "a" * 32, "resultHash": "b" * 64, "receiptHash": "c" * 64}
    stored = canonicalize_chart_data({**spec, "scienceReceipt": receipt}, "scatter")
    assert stored["scienceReceipt"] == receipt
    assert canonicalize_chart_data(stored, "scatter") == stored
    for broken in [{**receipt, "receiptHash": "0"}, {"sourceKey": "a" * 32},
                   {**receipt, "surprise": "not checksummed"}]:
        with pytest.raises(ChartDataError, match="scientific chart receipt"):
            canonicalize_chart_data({**spec, "scienceReceipt": broken}, "scatter")


def test_workbench_uncertainties_and_tables_use_existing_shapes():
    from graphlink_chart_data import scientific_result_chart
    point = scientific_result_chart({"values": {"estimate": 20, "combined_standard_uncertainty": 2,
                                               "coverage_lower": 17, "coverage_upper": 25}}, "scatter")
    assert point["xValues"] == [0]
    assert point["yError"] == [[3, 5]]
    grouped = {"table": {"columns": ["Treatment", "Signal (V)"],
                         "rows": [["Control", 1], ["Treatment", 3], ["Control", 2], ["Treatment", 5]]}}
    boxes = scientific_result_chart(grouped, "boxplot")
    assert boxes["groups"] == [{"label": "Control", "values": [1, 2]},
                               {"label": "Treatment", "values": [3, 5]}]
    heat = scientific_result_chart({"table": {"columns": ["Specimen", "T1", "T2"],
                               "rows": [["B", 3, 5], ["A", 4, 6]]}}, "heatmap")
    assert heat["rowLabels"] == ["B", "A"]
    assert heat["matrix"] == [[3, 5], [4, 6]]
    with pytest.raises(ChartDataError, match="at most 300 cells"):
        scientific_result_chart({"table": {"columns": [f"v{i}" for i in range(32)],
                                 "rows": [[1] * 32 for _ in range(10)]}}, "heatmap")
    with pytest.raises(ChartDataError, match="at least two observations"):
        scientific_result_chart({"table": {"columns": ["only"], "rows": [[1]]}}, "boxplot")


@pytest.mark.parametrize("pack,operation_id", [
    ("statistics", "linear_regression"),
    ("fitting", "weighted_linear_least_squares"),
    ("fitting", "nonlinear_least_squares"),
])
def test_workbench_fitted_model_overlays_measured_values_and_95_percent_envelope(pack, operation_id):
    from graphlink_chart_data import scientific_result_chart
    from graphlink_plugins.scientific.catalog import operations
    from graphlink_plugins.scientific.core import run_operation

    op = next(item for item in operations(pack) if item.id == operation_id)
    result = run_operation(pack, op, op.example)
    chart = scientific_result_chart(result, "line")
    assert chart["type"] == "line"
    assert [series["name"] for series in chart["series"]] == [
        "Observed", "Fit", "95% CI lower", "95% CI upper",
    ]
    original = result["inputs"].get("y", result["inputs"].get("response"))
    assert sorted(original) == sorted(chart["series"][0]["values"])
    assert len(chart["xValues"]) == len(original)
    for fit, err, lo, hi in zip(
        chart["series"][1]["values"], chart["series"][1]["yError"],
        chart["series"][2]["values"], chart["series"][3]["values"],
    ):
        assert lo <= fit <= hi
        assert err[0] >= 0 and err[1] >= 0
        assert lo == pytest.approx(fit - err[0], abs=1e-10)
        assert hi == pytest.approx(fit + err[1], abs=1e-10)
