"""Independent published oracles for the Fitting and Inference scientific pack."""

import copy
import json
import math
from pathlib import Path

import pytest

from graphlink_plugins.scientific.catalog import PACKS, operations
from graphlink_plugins.scientific.core import VERSION, readable_result, run_operation
from graphlink_plugins.scientific.packs import fitting

FIXTURE = json.loads(Path(__file__).with_name("test_scientific_fitting_nist.json").read_text(encoding="utf-8"))


def _run(operation_id, inputs):
    operation = next(o for o in operations("fitting") if o.id == operation_id)
    return run_operation("fitting", operation, inputs)


def _certified(actual, reference, digits):
    """Compare all *published* significant digits, not an arbitrary tolerance."""
    assert format(actual, f".{digits - 1}e") == format(reference, f".{digits - 1}e"), (actual, reference)


def test_manifest_and_all_examples_have_verifiable_receipts():
    import tomllib

    assert PACKS["fitting"][0] == "Fitting and Inference"
    manifest_path = Path(__file__).resolve().parents[2] / "plugins" / "science_fitting" / "plugin.toml"
    with manifest_path.open("rb") as f:
        manifest = tomllib.load(f)
    assert manifest["plugin"]["id"] == "science_fitting"
    assert manifest["plugin"]["version"] == VERSION
    assert manifest["scopes"]["grants"] == ["graph.read", "graph.mutate"]
    assert manifest["persistence"]["passive_node_kinds"] == ["workbench"]
    assert {op.id for op in operations("fitting")} == {
        "weighted_linear_least_squares", "nonlinear_least_squares", "welch_t_test", "chi_square_gof"
    }
    for operation in operations("fitting"):
        result = _run(operation.id, operation.example)
        assert result["provenance"]["packId"] == "fitting"
        assert result["provenance"]["packVersion"] == VERSION
        assert readable_result({"version": 1, "latest": result}, "fitting")
        corrupted = copy.deepcopy(result)
        key = next(iter(corrupted["values"]))
        corrupted["values"][key] = "corrupted"
        assert not readable_result({"version": 1, "latest": corrupted}, "fitting")


def test_longley_all_15_certified_digits_from_the_raw_nist_rows():
    item = FIXTURE["longley"]
    rows = item["data_y_x1_x2_x3_x4_x5_x6"]
    values = _run("weighted_linear_least_squares", {
        "predictors": [row[1:] for row in rows], "response": [row[0] for row in rows],
        "weights": [1] * len(rows),
    })
    result = values["values"]
    for i, expected in enumerate(item["coefficients"]):
        _certified(result[f"coefficient_{i}"], expected, item["digits"])
    for i, expected in enumerate(item["standard_errors"]):
        _certified(result[f"standard_error_{i}"], expected, item["digits"])
    _certified(result["residual_standard_deviation"], item["residual_standard_deviation"], item["digits"])
    _certified(result["r_squared"], item["r_squared"], item["digits"])
    assert result["degrees_of_freedom"] == 9
    assert len(values["table"]["rows"]) == 7  # 7 x 7 coefficient covariance
    for i in range(7):
        for j in range(7):
            _certified(values["table"]["rows"][i][j + 1],
                       values["table"]["rows"][j][i + 1], 14)
        assert math.sqrt(values["table"]["rows"][i][i + 1]) == pytest.approx(result[f"standard_error_{i}"], rel=1e-14)
    assert len(values["plot"]["y"]) == 16
    assert abs(math.fsum(values["plot"]["y"])) < 1e-7
    assert result["weighted_residual_sum_squares"] == pytest.approx(836424.055505915, rel=2e-15)


@pytest.mark.parametrize("case", ["misra1a", "thurber", "mgh09"])
@pytest.mark.parametrize("start_number", [0, 1])
def test_nist_nonlinear_all_11_certified_digits_from_both_starts(case, start_number):
    item = FIXTURE[case]
    rows = item["data_y_x"]
    result = _run("nonlinear_least_squares", {
        "model": item["model"], "x": [row[1] for row in rows], "y": [row[0] for row in rows],
        "weights": [1] * len(rows), "initial_parameters": item["starts"][start_number],
    })
    values = result["values"]
    assert values["converged"] == "true"
    assert 1 <= values["iterations"] < 1201
    assert values["accepted_steps"] > 0
    assert values["degrees_of_freedom"] == len(rows) - len(item["parameters"])
    for i, expected in enumerate(item["parameters"]):
        _certified(values[f"parameter_{i}"], expected, item["digits"])
    for i, expected in enumerate(item["standard_errors"]):
        _certified(values[f"standard_error_{i}"], expected, item["digits"])
    _certified(values["weighted_residual_sum_squares"], item["residual_sum_squares"], item["digits"])
    assert values["reduced_chi_square"] == pytest.approx(
        values["weighted_residual_sum_squares"] / values["degrees_of_freedom"], rel=1e-14
    )
    assert len(result["plot"]["y"]) == len(rows)
    for j in range(len(item["parameters"])):
        estimate = values[f"parameter_{j}"]
        assert values[f"ci95_lower_{j}"] < estimate < values[f"ci95_upper_{j}"]
        assert values[f"ci95_upper_{j}"] - estimate == pytest.approx(
            fitting._student_t_ppf(0.975, values["degrees_of_freedom"]) * values[f"standard_error_{j}"],
            rel=1e-12, abs=1e-14,
        )


@pytest.mark.parametrize("model,parameters,x", [
    ("exponential", [2.0, -0.3, 4.0], list(range(1, 12))),
    ("power_law", [3.0, 1.25], list(range(1, 12))),
    ("gaussian", [2.0, 10.0, 0.5, 1.25], [i / 2 for i in range(-10, 13)]),
    ("polynomial", [2.0, -1.0, 0.5, 0.125], list(range(-5, 7))),
])
def test_other_nonlinear_families_recover_known_parameters(model, parameters, x):
    # Generated from the declared equation and analytic Jacobian, without
    # calling the fitter; add small deterministic noise for nonzero SEs.
    y = [float(fitting._model(model, fitting._d(xx), list(map(fitting._d, parameters)))[0])
         + (i % 3 - 1) * 0.0001 for i, xx in enumerate(x)]
    initial = [v * 1.02 if v else 0.02 for v in parameters]
    if model == "gaussian":
        initial[2] += 0.1
    result = _run("nonlinear_least_squares", {
        "model": model, "x": x, "y": y, "weights": [1] * len(x), "initial_parameters": initial,
    })
    for i, expected in enumerate(parameters):
        assert result["values"][f"parameter_{i}"] == pytest.approx(expected, rel=0.003, abs=0.002)
        assert result["values"][f"standard_error_{i}"] > 0
    assert result["values"]["weighted_residual_sum_squares"] < 1e-6


def test_analytic_model_jacobians_agree_with_centered_finite_differences():
    examples = [
        ("exponential", 2, [2.0, -0.3, 4.0]), ("saturating_exponential", 10, [25, 0.01]),
        ("power_law", 2, [3, 1.5]), ("gaussian", 0.4, [1, 3, 0.5, 1.3]),
        ("polynomial", 2, [1, -2, 3]), ("thurber", 0.4, [1000, 1000, 400, 40, 0.7, 0.3, 0.03]),
        ("mgh09", 0.5, [0.2, 0.2, 0.1, 0.15]),
    ]
    for name, xx, params in examples:
        dparams = list(map(fitting._d, params))
        _, derivatives = fitting._model(name, fitting._d(xx), dparams)
        for j, derivative in enumerate(derivatives):
            step = fitting._d(1e-6 * max(abs(params[j]), 1))
            higher, lower = dparams.copy(), dparams.copy()
            higher[j] += step
            lower[j] -= step
            high = fitting._model(name, fitting._d(xx), higher)[0]
            low = fitting._model(name, fitting._d(xx), lower)[0]
            numerical = (high - low) / (2 * step)
            assert float(derivative) == pytest.approx(float(numerical), rel=1e-9, abs=1e-9), (name, j)


def test_distribution_functions_against_nist_published_tables():
    # https://www.itl.nist.gov/div898/handbook/eda/section3/eda3672.htm
    assert fitting._student_t_cdf(2.228, 10) == pytest.approx(0.975, abs=2e-5)
    assert fitting._student_t_cdf(-2.228, 10) == pytest.approx(0.025, abs=2e-5)
    assert fitting._student_t_ppf(.975, 10) == pytest.approx(2.228, abs=0.0005)
    assert fitting._student_t_ppf(.025, 10) == pytest.approx(-2.228, abs=0.0005)
    # https://www.itl.nist.gov/div898/handbook/eda/section3/eda3674.htm
    assert fitting._chi_square_cdf(18.307, 10) == pytest.approx(0.95, abs=2e-5)
    assert fitting._chi_square_cdf(3.841, 1) == pytest.approx(0.95, abs=2e-5)
    assert fitting._normal_cdf(1.959963984540054) == pytest.approx(.975, abs=2e-14)
    assert fitting._normal_cdf(0) == .5
    assert fitting._normal_cdf(-20) == pytest.approx(0, abs=3e-89)
    assert fitting._gamma_q(1, 5) == pytest.approx(math.exp(-5), rel=2e-13)
    assert fitting._gamma_q(1, 0) == 1
    assert fitting._beta_i(.5, .5, .1) == pytest.approx(2 / math.pi * math.asin(math.sqrt(.1)), rel=2e-14)
    assert fitting._beta_i(.5, .5, .9) == pytest.approx(2 / math.pi * math.asin(math.sqrt(.9)), rel=2e-14)
    assert fitting._beta_i(1, 1, 0) == 0
    assert fitting._beta_i(1, 1, 1) == 1
    assert fitting._student_t_cdf(0, 8) == .5
    # Independent central Taylor coefficient: t-density at the origin.
    # Using I(df/(df+t²)) here would round to 1 and return exactly 0.5.
    df, tiny_t = 1000, 1e-8
    density_zero = math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2)) / math.sqrt(df * math.pi)
    expected_cdf = 0.5 + density_zero * tiny_t
    assert fitting._student_t_cdf(tiny_t, df) == pytest.approx(expected_cdf, abs=5e-15)
    assert fitting._student_t_cdf(-tiny_t, df) == pytest.approx(1 - expected_cdf, abs=5e-15)
    assert fitting._student_t_cdf(1e200, 10) == 1


def test_welch_two_sided_p_and_chi_square_p():
    welch = _run("welch_t_test", {"sample_a": [1, 3, 5, 7], "sample_b": [2, 4, 6, 8]})["values"]
    # Equal sample variance in this case: t = -sqrt(0.3), df = 6.
    assert welch["t_statistic"] == pytest.approx(-math.sqrt(.3), rel=1e-14)
    assert welch["degrees_of_freedom"] == pytest.approx(6, rel=1e-14)
    assert welch["p_value"] == pytest.approx(
        fitting._beta_i(3, .5, 6 / (6 + .3)), rel=1e-14
    )
    assert 0 < welch["p_value"] < 1
    chi = _run("chi_square_gof", {"observed": [24, 36, 40], "expected": [30, 30, 40]})
    assert chi["values"]["chi_square"] == pytest.approx(2.4, rel=1e-14)
    assert chi["values"]["degrees_of_freedom"] == 2
    assert chi["values"]["p_value"] == pytest.approx(math.exp(-1.2), rel=1e-13)
    assert sum(row[-1] for row in chi["table"]["rows"]) == pytest.approx(2.4)


@pytest.mark.parametrize("op,inputs", [
    ("weighted_linear_least_squares", {"predictors": [[1], [2]], "response": [2, 4], "weights": [1, 1]}),
    ("weighted_linear_least_squares", {"predictors": [[1], [1], [1]], "response": [2, 4, 6], "weights": [1, 1, 1]}),
    ("weighted_linear_least_squares", {"predictors": [[1], [2], [3]], "response": [2, 4, 6], "weights": [1, 0, 0]}),
    ("weighted_linear_least_squares", {"predictors": [[1], [2], [3]], "response": [2, 4, 6], "weights": [1, -1, 1]}),
    ("weighted_linear_least_squares", {"predictors": [[1], [2], [3]], "response": [2, 4], "weights": [1, 1, 1]}),
    ("nonlinear_least_squares", {"model": "bogus", "x": [1, 2, 3], "y": [2, 4, 6], "weights": [1, 1, 1], "initial_parameters": [1, 2]}),
    ("nonlinear_least_squares", {"model": "power_law", "x": [0, 1, 2], "y": [2, 3, 4], "weights": [1, 1, 1], "initial_parameters": [1, 2]}),
    ("nonlinear_least_squares", {"model": "gaussian", "x": [1, 2, 3, 4, 5], "y": [1, 2, 4, 2, 1], "weights": [1] * 5, "initial_parameters": [1, 3, 2, 0]}),
    ("nonlinear_least_squares", {"model": "mgh09", "x": [0, 1, 2, 3, 4], "y": [1, 2, 3, 4, 5], "weights": [1] * 5, "initial_parameters": [1, 2, 3]}),
    ("nonlinear_least_squares", {"model": "polynomial", "x": [1, 2, 3], "y": [1, 2, 3], "weights": [1] * 3, "initial_parameters": [1, 1, 1]}),
    ("welch_t_test", {"sample_a": [1], "sample_b": [1, 3]}),
    ("welch_t_test", {"sample_a": [1, 1], "sample_b": [2, 2]}),
    ("chi_square_gof", {"observed": [1, 2], "expected": [1, 1]}),
    ("chi_square_gof", {"observed": [1, -1], "expected": [1, 1]}),
    ("chi_square_gof", {"observed": [1.5, 2.5], "expected": [2, 2]}),
    ("chi_square_gof", {"observed": [2, 2], "expected": [0, 4]}),
    ("chi_square_gof", {"observed": [10], "expected": [10]}),
    ("weighted_linear_least_squares", {"predictors": [[0], [1], [2]], "response": [0, 1, float("nan")], "weights": [1, 1, 1]}),
])
def test_invalid_inputs_rejected_by_operation_and_shared_validator(op, inputs):
    with pytest.raises(ValueError):
        _run(op, inputs)


@pytest.mark.parametrize("function,args", [
    (fitting._gamma_q, (0, 1)), (fitting._gamma_q, (2, -1)),
    (fitting._beta_i, (-1, 1, .5)), (fitting._beta_i, (1, 2, 1.1)),
    (fitting._student_t_cdf, (1, 0)), (fitting._student_t_ppf, (1, 2)),
])
def test_invalid_distribution_domains_are_rejected(function, args):
    with pytest.raises(ValueError):
        function(*args)


def test_weighted_linear_matches_independent_hand_calculation():
    # Fit y = 1 + 2*x with a nonzero noisy endpoint and one ignored row.
    # All normal-equation sums below can be verified by hand.
    result = _run("weighted_linear_least_squares", {
        "predictors": [[-1], [0], [1], [2], [10]],
        "response": [-1, 1, 3, 6, 1e5], "weights": [1, 1, 1, 1, 0],
    })
    values = result["values"]
    assert values["positive_weight_observations"] == 4
    assert values["degrees_of_freedom"] == 2
    assert values["coefficient_0"] == pytest.approx(1.1)
    assert values["coefficient_1"] == pytest.approx(2.3)
    assert values["residual_4"] == pytest.approx(1e5 - 24.1)
    assert values["r_squared"] <= 1
    assert values["r_squared"] >= 0


def test_constant_response_r_squared_undefined_and_singular_nonlinear_rejected():
    linear = _run("weighted_linear_least_squares", {
        "predictors": [[0], [1], [2], [3]],
        "response": [4, 4, 4, 4], "weights": [1] * 4,
    })
    assert "r_squared" not in linear["values"]
    with pytest.raises(ValueError, match="rank deficient|unidentifiable"):
        _run("nonlinear_least_squares", {
            "model": "polynomial", "x": [1, 1, 1, 1],
            "y": [1, 2, 3, 4], "weights": [1] * 4,
            "initial_parameters": [0, 0],
        })


def test_float_underflow_is_rejected_not_silently_rounded_to_zero():
    from decimal import Decimal
    with pytest.raises(ValueError, match="numeric range"):
        fitting._as_float(Decimal("1e-500"), "Tiny covariance")
