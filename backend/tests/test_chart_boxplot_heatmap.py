"""Distribution and matrix chart contracts: validation, fixtures and exports."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from graphlink_chart_data import CHART_JSON_SCHEMAS, ChartDataError, canonicalize_chart_data
from graphlink_chart_rendering import _boxplot_statistics, render_chart_png, render_chart_svg


BOXPLOT = {
    "type": "boxplot", "title": "Measurements",
    "groups": [
        {"label": "Control", "values": [10, 12, 12, 14, 40]},
        {"label": "Treatment", "values": [8, 9, 10, 11, 12]},
    ],
}
HEATMAP = {
    "type": "heatmap", "title": "Response",
    "rowLabels": ["Control", "Treatment"],
    "columnLabels": ["0h", "24h", "48h"],
    "matrix": [[-2, 0, 1], [1.5, 3, 5]],
    "valueUnit": "AU",
}


@pytest.mark.parametrize("chart_type,payload", [
    ("boxplot", BOXPLOT), ("heatmap", HEATMAP),
])
def test_new_chart_canonicalization_is_idempotent_does_not_mutate_input_and_fits_v1_wire(chart_type, payload):
    original = deepcopy(payload)
    result = canonicalize_chart_data(payload, chart_type)
    assert payload == original
    assert result["version"] == 1
    assert result["type"] == chart_type
    assert canonicalize_chart_data(result, chart_type) == result
    assert result["title"] == payload["title"]
    assert CHART_JSON_SCHEMAS[chart_type]["required"]


def test_boxplot_preserves_samples_labels_and_defaults():
    result = canonicalize_chart_data({"type": "boxplot", "groups": [
        {"label": "A", "values": [1, 2]},
        {"label": "B", "values": ["3", 4]},
    ]}, "boxplot")
    assert result == {
        "version": 1, "type": "boxplot", "title": "Boxplot Chart",
        "groups": [
            {"label": "A", "values": [1.0, 2.0]},
            {"label": "B", "values": [3.0, 4.0]},
        ],
        "xAxis": "Group", "yAxis": "Value",
    }


def test_tukey_whiskers_and_outliers_are_not_just_min_max():
    stats = _boxplot_statistics([10, 12, 12, 14, 40])
    assert stats == {
        "med": 12, "q1": 11, "q3": 27,
        "whislo": 10, "whishi": 40, "fliers": [],
    }
    # Median-of-halves: with seven points, 100 is a true 1.5 IQR outlier.
    outliers = _boxplot_statistics([1, 2, 2, 3, 3, 4, 100])
    assert outliers == {
        "med": 3, "q1": 2, "q3": 4,
        "whislo": 1, "whishi": 4, "fliers": [100],
    }


def test_boxplot_medians_stay_finite_for_valid_extreme_samples():
    # statistics.median would overflow the two equally large operands.
    huge = _boxplot_statistics([1e308, 1e308])
    assert huge == {
        "med": 1e308, "q1": 1e308, "q3": 1e308,
        "whislo": 1e308, "whishi": 1e308, "fliers": [],
    }
    tiny = _boxplot_statistics([5e-324, 5e-324])
    assert tiny["med"] == tiny["q1"] == tiny["q3"] == 5e-324


@pytest.mark.parametrize("payload,match", [
    ({"groups": []}, "nonempty groups"),
    ({"groups": [{}]}, "label.*non-empty"),
    ({"groups": [{"label": "A", "values": [1]}]}, "at least two observations"),
    ({"groups": [{"label": "A", "values": [1, float("nan")]}]}, "finite number"),
    ({"groups": [{"label": "A", "values": [1, True]}]}, "finite number"),
    ({"groups": [{"label": "A", "values": [1, 2]}, {"label": "A", "values": [2, 3]}]}, "Duplicate"),
    ({"groups": [{"label": str(i), "values": [i, i + 1]} for i in range(13)]}, "at most 12"),
    ({"groups": [{"label": "Large", "values": [1] * 301}]}, "at most 300"),
    ({"groups": [{"label": "Overflow", "values": [-1e308, 1e308]}]}, "range must be finite"),
])
def test_boxplot_rejects_malformed_or_unbounded_payloads(payload, match):
    with pytest.raises(ChartDataError, match=match):
        canonicalize_chart_data({"type": "boxplot", **payload}, "boxplot")


@pytest.mark.parametrize("payload,match", [
    ({"rowLabels": [], "columnLabels": ["A"], "matrix": []}, "rowLabels"),
    ({"rowLabels": ["A"], "columnLabels": [], "matrix": [[]]}, "columnLabels"),
    ({"rowLabels": ["A", "B"], "columnLabels": ["X"], "matrix": [[1]]}, "row count"),
    ({"rowLabels": ["A"], "columnLabels": ["X", "Y"], "matrix": [[1]]}, "row 0 length"),
    ({"rowLabels": ["A"], "columnLabels": ["X"], "matrix": [[None]]}, "finite number"),
    ({"rowLabels": ["A"], "columnLabels": ["X"], "matrix": [[float("inf")]]}, "finite number"),
    ({"rowLabels": ["A"], "columnLabels": ["X"], "matrix": [[True]]}, "finite number"),
    ({"rowLabels": ["A"], "columnLabels": ["X", "Y"], "matrix": [[-1e308, 1e308]]}, "range must be finite"),
    ({"rowLabels": ["A"] * 20, "columnLabels": ["B"] * 16, "matrix": [[1] * 16] * 20}, "at most 300"),
    ({"rowLabels": [""], "columnLabels": ["A"], "matrix": [[1]]}, "non-empty"),
    ({"rowLabels": ["A"], "columnLabels": ["X"], "matrix": [[1]], "valueUnit": ""}, "non-empty"),
])
def test_heatmap_rejects_ragged_nonfinite_and_oversized_matrices(payload, match):
    with pytest.raises(ChartDataError, match=match):
        canonicalize_chart_data({"type": "heatmap", **payload}, "heatmap")


@pytest.mark.parametrize("chart_type,fixture_name", [
    ("boxplot", "boxplot_grouped_measurements.json"),
    ("heatmap", "heatmap_assay_matrix.json"),
])
def test_new_eval_fixtures_match_canonical_output(chart_type, fixture_name):
    folder = Path(__file__).parents[1] / "evals" / "fixtures" / "chart"
    fixture = json.loads((folder / fixture_name).read_text(encoding="utf-8"))
    assert fixture["input"]["chart_type"] == chart_type
    assert canonicalize_chart_data(
        json.loads(fixture["scripted_response"]), chart_type,
    ) == fixture["expected"]


def test_boxplot_exports_png_svg_with_exact_tukey_statistics(monkeypatch):
    from matplotlib.axes import Axes

    calls = []
    original = Axes.bxp

    def spy(self, stats, **kwargs):
        calls.append((stats, kwargs))
        return original(self, stats, **kwargs)

    monkeypatch.setattr(Axes, "bxp", spy)
    payload = {"type": "boxplot", "groups": [
        {"label": "Outlier", "values": [1, 2, 2, 3, 3, 4, 100]},
    ]}
    assert render_chart_png("boxplot", payload, 640, 480).startswith(b"\x89PNG\r\n\x1a\n")
    svg = render_chart_svg("boxplot", payload, 640, 480)
    assert b"<svg " in svg and b"Chart rendering issue" not in svg
    assert len(calls) == 2
    assert all(call[0][0]["fliers"] == [100.0] for call in calls)


def test_heatmap_exports_png_svg_with_correct_matrix_and_norm(monkeypatch):
    from matplotlib.axes import Axes

    calls = []
    original = Axes.imshow

    def spy(self, array, **kwargs):
        calls.append((array, kwargs))
        return original(self, array, **kwargs)

    monkeypatch.setattr(Axes, "imshow", spy)
    assert render_chart_png("heatmap", HEATMAP, 640, 480).startswith(b"\x89PNG\r\n\x1a\n")
    svg = render_chart_svg("heatmap", HEATMAP, 640, 480)
    assert b"<svg " in svg and b"Chart rendering issue" not in svg
    assert len(calls) == 2
    for matrix, kwargs in calls:
        assert matrix == [[-2.0, 0.0, 1.0], [1.5, 3.0, 5.0]]
        assert kwargs["norm"].vcenter == 0


def test_existing_chart_types_still_require_their_original_shapes():
    assert canonicalize_chart_data({"type": "bar", "labels": ["A"], "values": [1]}, "bar")["type"] == "bar"
    assert canonicalize_chart_data({"type": "scatter", "xValues": [1], "values": [2]}, "scatter")["xValues"] == [1.0]



@pytest.mark.parametrize("chart_type,payload", [
    ("boxplot", BOXPLOT), ("heatmap", HEATMAP),
])
def test_pr590_saved_chart_without_nested_type_recovers_samples_and_matrix(chart_type, payload):
    canonical = canonicalize_chart_data(payload, chart_type)
    saved_by_pr590 = {key: value for key, value in canonical.items() if key != "type"}
    assert canonicalize_chart_data(saved_by_pr590, chart_type) == canonical
