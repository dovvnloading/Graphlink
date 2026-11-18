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
    # The nested type is optional in ChartDataRow. The authoritative outer
    # ChartState.chart_type is "scatter", preserving the existing generated
    # wire enum until a future (separately scoped) schema change.
    assert "type" not in spec
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
    ({"series": [{"name": "Same", "values": [1, 2, 3]},
                 {"name": "Same", "values": [2, 3, 4]}]}, "Duplicate series name"),
])
def test_scientific_rejects_bad_log_bounds_and_series_lengths(data, match):
    base = {"type": "scatter", "xValues": [1, 10, 100], "values": [1, 2, 3]}
    if "series" in data:
        base.pop("values")
    base.update(data)
    with pytest.raises(ChartDataError, match=match):
        canonicalize_chart_data(base, "scatter")


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
                      if path.name != "scatter_calibration_error_bars.json")
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
