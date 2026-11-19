"""Regression tests for the grant-gated Biology Workbench plate-assay pack."""

import copy
import json
import statistics

import pytest

from graphlink_plugins.scientific.catalog import PACK_FAMILIES, operations
from graphlink_plugins.scientific.core import readable_result, run_operation


def _run(operation_id, **inputs):
    operation = next(operation for operation in operations("plate_assays")
                     if operation.id == operation_id)
    return run_operation("plate_assays", operation, {**operation.example, **inputs})


def test_registered_in_biology_family_with_runnable_examples():
    assert PACK_FAMILIES["plate_assays"] == "biology"
    assert {operation.id for operation in operations("plate_assays")} == {
        "plate_layout", "standard_curve", "assay_quality",
        "qpcr_standard_curve", "qpcr_relative_expression", "digital_pcr",
        "a260_quantification",
    }
    for operation in operations("plate_assays"):
        output = run_operation("plate_assays", operation, operation.example)
        assert readable_result({"version": 1, "latest": output}, "plate_assays")
        assert output["provenance"]["packId"] == "plate_assays"


@pytest.mark.parametrize("size,last_well", [("96", "H12"), ("384", "P24")])
def test_plate_map_is_typed_and_preserves_zero_missing_and_category(size, last_well):
    output = _run("plate_layout", plate_format=size, reading_label="RFU",
                  wells_json=json.dumps({"A1": "blank", "A2": 0, "B1": None,
                                          last_well: 1.25}))
    view, = output["views"]
    assert view["type"] == "plate_map"
    assert view["format"] == int(size)
    assert view["wells"][1] == {"well": "A2", "value": 0}
    assert next(cell for cell in view["wells"] if cell["well"] == "B1")["value"] is None
    assert output["values"]["recorded_wells"] == 3
    assert output["values"]["empty_wells"] == int(size) - 3
    assert output["table"]["rows"][1] == ["A2", 0]
    assert readable_result({"version": 1, "latest": output}, "plate_assays")
    tampered = copy.deepcopy(output)
    tampered["views"][0]["wells"][1]["value"] = 999
    assert not readable_result({"version": 1, "latest": tampered}, "plate_assays")


@pytest.mark.parametrize("wells", [
    '{"A1":1,"A1":2}', '{"A13":1}', '{"P1":2}', '{"A1":NaN}',
    '{"A1":true}', '{"A1":[]}', '{"A1":1e999}',
])
def test_bad_plate_assay_wells_fail_closed(wells):
    with pytest.raises(ValueError):
        _run("plate_layout", wells_json=wells)


def test_linear_recovery_and_reciprocal_dilution():
    result = _run("standard_curve")
    values = result["values"]
    assert values["r_squared"] == pytest.approx(1.0, abs=1e-12)
    assert values["coefficient_0"] == pytest.approx(0.1)
    assert values["coefficient_1"] == pytest.approx(2.0)
    assert values["unknowns_quantified"] == 2
    unknowns = [row for row in result["table"]["rows"] if row[0] == "Unknown"]
    assert unknowns[0][5] == pytest.approx(1.5)
    assert unknowns[0][7] == pytest.approx(3.0)  # 1.5 measured × 2 dilution
    assert unknowns[1][7] == pytest.approx(25.0)  # 2.5 measured × 10 dilution
    assert all(row[-1] == "within range" for row in unknowns)
    assert readable_result({"version": 1, "latest": result}, "plate_assays")


def test_quadratic_inverse_uses_monotone_calibration_branch():
    xs = [0, 1, 2, 3, 4, 5]
    ys = [2 + 3 * x + 0.5 * x * x for x in xs]
    sample = 1.5
    result = _run("standard_curve", model="quadratic",
                  standard_concentrations=xs, standard_signals=ys,
                  unknown_signals=[2 + 3 * sample + 0.5 * sample**2],
                  dilution_factors=[4])
    assert result["values"]["r_squared"] == pytest.approx(1, abs=1e-12)
    assert result["table"]["rows"][-1][7] == pytest.approx(6, rel=1e-10)


@pytest.mark.parametrize("model,asymmetry", [("4pl", 1.0), ("5pl", 0.7)])
def test_logistic_standard_curves_back_calculate_unknowns(model, asymmetry):
    xs = [0.03, 0.06, 0.12, 0.25, 0.5, 1, 2, 4, 8, 16, 32, 64]
    def response(x):
        return 2.1 + (0.15 - 2.1) / (1 + (x / 1.7)**1.3)**asymmetry

    measured = 2.6
    result = _run("standard_curve", model=model, standard_concentrations=xs,
                  standard_signals=[response(x) for x in xs],
                  unknown_signals=[response(measured)], dilution_factors=[5])
    assert result["values"]["unknowns_quantified"] == 1
    assert result["values"]["r_squared"] > 0.999999
    assert result["table"]["rows"][-1][5] == pytest.approx(measured, rel=2e-3)
    assert result["table"]["rows"][-1][7] == pytest.approx(measured * 5, rel=2e-3)
    if model == "5pl":
        assert result["values"]["g"] == pytest.approx(asymmetry, rel=2e-2)
    assert readable_result({"version": 1, "latest": result}, "plate_assays")


def test_unknown_outside_standard_range_not_extrapolated():
    output = _run("standard_curve", unknown_signals=[100, -5],
                  dilution_factors=[2, 10])
    assert output["values"]["unknowns_quantified"] == 0
    assert all(row[-1] == "outside calibration" and row[5] == "" and row[7] == ""
               for row in output["table"]["rows"][-2:])


@pytest.mark.parametrize("changes", [
    {"standard_concentrations": [0, 1]},
    {"standard_concentrations": [0, 0, 0, 0, 0]},
    {"standard_signals": [5, 5, 5, 5, 5]},
    {"dilution_factors": [1]},
    {"dilution_factors": [0, 1]},
    {"concentration_unit": "\n"},
])
def test_invalid_calibrations_are_rejected(changes):
    with pytest.raises(ValueError):
        _run("standard_curve", **changes)


def test_quadratic_turning_point_is_not_inverted_ambiguously():
    with pytest.raises(ValueError, match="not monotonic"):
        _run("standard_curve", model="quadratic",
             standard_concentrations=[0, 1, 2, 3, 4, 5],
             standard_signals=[-(x - 2.5)**2 for x in range(6)],
             unknown_signals=[-1], dilution_factors=[1])


def test_quality_statistics_use_unbiased_sd_and_correct_formulas():
    positive, negative = [98, 100, 102, 100], [9, 10, 11, 10]
    result = _run("assay_quality", positive_controls=positive, negative_controls=negative)
    p_sd, n_sd = statistics.stdev(positive), statistics.stdev(negative)
    expected_z = 1 - 3 * (p_sd + n_sd) / 90
    values = result["values"]
    assert values["z_prime_factor"] == pytest.approx(expected_z)
    assert values["positive_cv_percent"] == pytest.approx(p_sd)
    assert values["negative_cv_percent"] == pytest.approx(10 * n_sd)
    assert values["absolute_signal_window"] == pytest.approx(90 - 3 * (p_sd + n_sd))
    assert values["signal_window"] == pytest.approx((90 - 3 * (p_sd + n_sd)) / p_sd)
    assert len(result["table"]["rows"]) == 8
    assert readable_result({"version": 1, "latest": result}, "plate_assays")


def test_signal_window_zero_sd_and_zero_mean_cv_are_explicitly_undefined():
    result = _run("assay_quality", positive_controls=[10, 10],
                  negative_controls=[-1, 1])
    assert result["values"]["z_prime_factor"] < 1
    assert result["values"]["signal_window"] == "undefined (zero high-control SD)"
    assert result["values"]["negative_cv_percent"] == "undefined (zero mean)"
    assert readable_result({"version": 1, "latest": result}, "plate_assays")


@pytest.mark.parametrize("positive,negative", [
    ([1], [0, 0]), ([1, 1], [1, 1]), ([1, 2], [1, 2]),
])
def test_quality_requires_replicates_and_separated_means(positive, negative):
    with pytest.raises(ValueError):
        _run("assay_quality", positive_controls=positive, negative_controls=negative)
