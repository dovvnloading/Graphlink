"""Independent numerical and validation regressions for molecular plate assays."""

import math
from pathlib import Path

import pytest

from graphlink_plugins.scientific.catalog import operations
from graphlink_plugins.scientific.core import readable_result, run_operation


def _run(operation_id, **changes):
    operation = next(op for op in operations("plate_assays") if op.id == operation_id)
    return run_operation("plate_assays", operation, {**operation.example, **changes})


def test_new_operations_have_methods_sources_and_valid_receipts():
    for name in ("qpcr_standard_curve", "qpcr_relative_expression", "digital_pcr",
                 "a260_quantification"):
        operation = next(op for op in operations("plate_assays") if op.id == name)
        assert operation.methods and operation.citations
        result = run_operation("plate_assays", operation, operation.example)
        assert readable_result({"version": 1, "latest": result}, "plate_assays")
        assert result["operationId"] == name
    docs = (Path(__file__).resolve().parents[2] / "docs/biology/plate_assays.md").read_text()
    assert all(name in docs for name in ("qpcr_standard_curve", "qpcr_relative_expression",
                                        "digital_pcr", "a260_quantification"))


def test_qpcr_standard_curve_efficiency_one_hundred_percent_and_residuals():
    curve = _run("qpcr_standard_curve")
    values = curve["values"]
    assert values["ct_slope_per_log10_quantity"] == pytest.approx(-1 / math.log10(2), rel=1e-10)
    assert values["ct_intercept"] == pytest.approx(30)
    assert values["amplification_factor"] == pytest.approx(2)
    assert values["amplification_efficiency_percent"] == pytest.approx(100)
    assert values["r_squared"] == pytest.approx(1)
    assert all(abs(row[-1]) < 1e-9 for row in curve["table"]["rows"])
    assert curve["plot"]["x"][0] == pytest.approx(0)
    assert curve["plot"]["x"][-1] == pytest.approx(3)


def test_qpcr_standard_curve_uses_log_quantity_not_raw_quantity():
    amounts = [1, 10, 100, 1000]
    cts = [33.0, 29.7, 26.1, 23.1]
    values = _run("qpcr_standard_curve", standard_quantities=amounts, standard_cts=cts)["values"]
    mean_ct = sum(cts) / len(cts)
    expected_slope = sum((i - 1.5) * (ct - mean_ct)
                         for i, ct in enumerate(cts)) / 5
    assert values["ct_slope_per_log10_quantity"] == pytest.approx(expected_slope)
    assert values["amplification_factor"] == pytest.approx(10**(-1 / expected_slope))
    assert 0 < values["r_squared"] < 1


@pytest.mark.parametrize("changes", [
    {"standard_quantities": [1, 10]},
    {"standard_quantities": [1, 10, 100]},
    {"standard_quantities": [1, 1, 1, 10]},
    {"standard_quantities": [0, 10, 100, 1000]},
    {"standard_quantities": [-1, 10, 100, 1000]},
    {"standard_cts": [20, 23, 26, 29]},  # positive slope
    {"standard_cts": [25, 25, 25, 25]},
    {"standard_cts": [-1, 26, 23, 20]},
    {"standard_cts": [101, 26, 23, 20]},
    {"quantity_unit": "\t"},
])
def test_qpcr_curve_rejects_nonphysical_or_unpaired_data(changes):
    with pytest.raises(ValueError):
        _run("qpcr_standard_curve", **changes)


def test_qpcr_ddct_and_pfaffl_use_geometric_mean_of_reference_ratios():
    values = _run(
        "qpcr_relative_expression", target_ct_sample=23, target_ct_calibrator=25,
        reference_cts_sample=[18, 20], reference_cts_calibrator=[20, 19],
        target_efficiency=1.9, reference_efficiencies=[2.0, 1.8],
    )["values"]
    # ΔCt(sample) = 23-19=4; ΔCt(cal) = 25-19.5=5.5.
    assert values["delta_delta_ct"] == pytest.approx(-1.5)
    assert values["ddct_fold_change"] == pytest.approx(2**1.5)
    # Reference fold ratios are 2^(20-18)=4 and 1.8^(19-20)=1/1.8.
    normalizer = math.sqrt(4 / 1.8)
    assert values["reference_geometric_mean_sample_to_calibrator_ratio"] == pytest.approx(normalizer)
    assert values["target_sample_to_calibrator_ratio"] == pytest.approx(1.9**2)
    assert values["pfaffl_fold_change"] == pytest.approx(1.9**2 / normalizer)
    assert values["reference_genes"] == 2


def test_qpcr_single_reference_and_all_efficiency_two_match_ddct():
    first = _run("qpcr_relative_expression", reference_cts_sample=[19],
                 reference_cts_calibrator=[20], reference_efficiencies=[1.85],
                 target_efficiency=1.95)["values"]
    assert first["pfaffl_fold_change"] == pytest.approx(1.95**3 / 1.85)
    equal = _run("qpcr_relative_expression", target_efficiency=2,
                 reference_efficiencies=[2, 2])["values"]
    assert equal["ddct_fold_change"] == pytest.approx(equal["pfaffl_fold_change"])


@pytest.mark.parametrize("changes", [
    {"reference_cts_sample": [19]},
    {"reference_cts_calibrator": [19, 20, 21]},
    {"reference_efficiencies": [2]},
    {"reference_efficiencies": [2, 0.9]},
    {"reference_efficiencies": [2, 100]},  # percentages are not amplification factors
    {"target_efficiency": 1},
    {"target_efficiency": 3.1},
    {"target_ct_sample": -0.1},
    {"reference_cts_sample": [19, 101]},
])
def test_qpcr_relative_rejects_bad_pairs_efficiencies_or_cts(changes):
    with pytest.raises(ValueError):
        _run("qpcr_relative_expression", **changes)


def test_dpcr_poisson_quantification_and_wilson_interval():
    result = _run("digital_pcr", positive_partitions=250, total_partitions=1000,
                  partition_volume_nl=0.5, dilution_factor=4)
    values = result["values"]
    expected_lam = -math.log(0.75)
    expected = expected_lam / 0.0005 * 4
    assert values["copies_per_partition"] == pytest.approx(expected_lam)
    assert values["copies_per_ul_measured"] == pytest.approx(expected_lam / 0.0005)
    assert values["copies_per_ul_original"] == pytest.approx(expected)
    assert values["ci95_lower_copies_per_ul_original"] < expected
    assert values["ci95_upper_copies_per_ul_original"] > expected
    assert result["table"]["rows"][0][1] == 750


def test_dpcr_zero_positives_is_zero_point_estimate_with_upper_bound():
    values = _run("digital_pcr", positive_partitions=0)["values"]
    assert values["copies_per_partition"] == 0
    assert values["copies_per_ul_original"] == 0
    assert values["ci95_lower_copies_per_ul_original"] == 0
    assert values["ci95_upper_copies_per_ul_original"] > 0


def test_dpcr_wilson_interval_stays_finite_near_saturation():
    # Even the largest exactly representable partition counts must not
    # round a non-saturated observation into an infinite occupancy estimate.
    for positives, total in ((999, 1000), (2**53 - 2, 2**53 - 1)):
        values = _run("digital_pcr", positive_partitions=positives,
                      total_partitions=total)["values"]
        assert (0 < values["ci95_lower_copies_per_ul_original"]
                < values["copies_per_ul_original"]
                < values["ci95_upper_copies_per_ul_original"])


@pytest.mark.parametrize("changes", [
    {"positive_partitions": -1}, {"positive_partitions": 1000},
    {"positive_partitions": 1001}, {"positive_partitions": 0.5},
    {"total_partitions": 0}, {"total_partitions": 99},
    {"partition_volume_nl": 0}, {"dilution_factor": 0.5},
])
def test_dpcr_rejects_invalid_or_saturated_partitions(changes):
    with pytest.raises(ValueError):
        _run("digital_pcr", **changes)


def test_a260_blank_path_length_dilution_and_purity():
    result = _run("a260_quantification")
    values = result["values"]
    assert values["a260_blank_corrected"] == pytest.approx(0.5)
    assert values["concentration_ng_per_ul_measured"] == pytest.approx(25)
    assert values["concentration_ng_per_ul_original"] == pytest.approx(250)
    assert values["a260_a280_ratio"] == pytest.approx(2)
    assert values["a260_a230_ratio"] == pytest.approx(2.5)
    half = _run("a260_quantification", path_length_cm=0.5)["values"]
    assert half["concentration_ng_per_ul_measured"] == pytest.approx(50)
    assert half["a260_a280_ratio"] == values["a260_a280_ratio"]
    assert len(result["table"]["rows"]) == 3


@pytest.mark.parametrize("kind,factor", [("dsDNA", 50), ("ssDNA", 33), ("RNA", 40)])
def test_a260_nucleic_acid_conversion_factors(kind, factor):
    values = _run("a260_quantification", nucleic_acid_type=kind,
                  a260=1, blank_a260=0, path_length_cm=1, dilution_factor=1)["values"]
    assert values["concentration_ng_per_ul_original"] == pytest.approx(factor)


def test_a260_zero_denominators_are_undefined_not_nonfinite():
    values = _run("a260_quantification", a280=0.05, a230=0.01)["values"]
    assert values["a260_a280_ratio"].startswith("undefined")
    assert values["a260_a230_ratio"].startswith("undefined")
    assert values["concentration_ng_per_ul_original"] == pytest.approx(250)


@pytest.mark.parametrize("changes", [
    {"a260": 0.01}, {"path_length_cm": 0},
    {"path_length_cm": -1}, {"dilution_factor": 0.1},
])
def test_a260_rejects_negative_net_260_or_invalid_geometry(changes):
    with pytest.raises(ValueError):
        _run("a260_quantification", **changes)
