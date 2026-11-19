"""Numerical reference cases and model boundaries for five quantitative packs."""

import json
import math

import pytest

from graphlink_plugins.scientific.packs import astronomy, engineering, mathematics, physics, statistics

PACKS = (mathematics, statistics, physics, astronomy, engineering)
OPERATIONS = {op.id: op for pack in PACKS for op in pack.OPERATIONS}


def calculate(operation_id, **changes):
    operation = OPERATIONS[operation_id]
    return operation.calculate({**operation.example, **changes})


@pytest.mark.parametrize("operation_id", OPERATIONS)
def test_example_repeats_without_mutating_input_and_stays_json_safe(operation_id):
    operation = OPERATIONS[operation_id]
    if any(field.kind == "table" for field in operation.fields):
        # These operations deliberately have no standalone sample: they
        # require an imported table asset and a connected source receipt.
        pytest.skip("Whole-table operations require a connected imported dataset.")
    before = json.dumps(operation.example)
    result = calculate(operation_id)
    assert result == calculate(operation_id)
    assert before == json.dumps(operation.example)
    assert result["summary"] and result["values"]
    json.dumps(result, allow_nan=False)


def test_polynomial_and_trapezoid():
    result = calculate("polynomial", coefficients=[2, -1, 3], points=[2, -1, 0])
    assert result["table"]["rows"] == [[2, 12], [-1, 6], [0, 2]]
    assert result["plot"]["x"] == [-1, 0, 2]
    assert result["plot"]["y"] == [6, 2, 12]
    assert calculate("trapezoid", x=[0, 1, 3], y=[-2, 0, 4])["values"]["integral"] == 3


def test_trapezoid_preserves_subnormal_height_and_exact_cancellation():
    tiny = math.ulp(0.0)
    assert calculate("trapezoid", x=[0, 1e308], y=[tiny, tiny])["values"]["integral"] == tiny * 1e308
    assert calculate("trapezoid", x=[-1e308, 1e308], y=[-1, 1])["values"]["integral"] == 0


def test_solve_with_row_interchange_scaling_and_residual():
    result = calculate("linear_system", matrix=[[0, 2e-100], [1e100, 1e100]], rhs=[8e-100, 7e100])
    assert [v for _, v in result["table"]["rows"]] == pytest.approx([3, 4])
    assert result["values"]["max_absolute_residual"] / 7e100 < 1e-15


def test_describe_and_weighted_population_statistics():
    values = calculate("describe", observations=[1, 2, 3])["values"]
    assert values == {"count": 3, "mean": 2, "median": 2, "minimum": 1, "maximum": 3, "sample_sd": 1}
    values = calculate("weighted_summary", observations=[3, 5, -999], weights=[1e308, 1e308, 0])["values"]
    assert values["positive_weights"] == 2
    assert values["weighted_mean"] == 4
    assert values["population_sd"] == pytest.approx(1)


def test_regression_small_variation_around_large_center_and_constant_response():
    offset = 1e16
    result = calculate("linear_regression", x=[offset, offset + 2, offset + 4], y=[11, 7, 3])
    assert result["values"]["slope"] == pytest.approx(-2)
    assert result["values"]["pearson_r"] == pytest.approx(-1)
    assert result["values"]["r_squared"] == pytest.approx(1)
    assert result["plot"]["y"] == pytest.approx([11, 3])
    flat = calculate("linear_regression", x=[2, 3, 4], y=[5, 5, 5])
    assert flat["values"]["slope"] == 0 and flat["values"]["intercept"] == 5
    assert "pearson_r" not in flat["values"] and "undefined" in flat["summary"]


def test_oscillator_reference_and_sampling():
    result = calculate("spring_oscillator", mass_kg=1, stiffness_n_m=4, amplitude_m=0.1)
    assert result["values"]["period_s"] == pytest.approx(math.pi)
    assert result["values"]["frequency_hz"] == pytest.approx(1 / math.pi)
    assert result["plot"]["x"][32] == pytest.approx(math.pi / 2)
    assert result["plot"]["y"][32] == pytest.approx(-0.1)
    assert result["plot"]["y"][-1] == pytest.approx(0.1)


def test_thermal_direction_and_units():
    values = calculate("thermal_layers", left_k=275, right_k=295)["values"]
    assert values == pytest.approx({"resistance_k_w": 0.5, "heat_rate_w": -40, "heat_flux_w_m2": -20})
    values = calculate("sensible_heat", mass_kg=2, specific_heat_j_kgk=400, initial_k=100, final_k=90)["values"]
    assert values == {"temperature_change_k": -10, "heat_capacity_j_k": 800, "heat_j": -8000}


def test_distance_relations_and_wien_wavelength_peak():
    assert calculate("parallax_distance", parallax_mas=1000)["values"] == pytest.approx(
        {"distance_pc": 1, "distance_ly": 3.261563777}
    )
    assert calculate("distance_modulus", apparent_mag=8, absolute_mag=1, extinction_mag=2)["values"] == {
        "true_distance_modulus_mag": 5,
        "distance_pc": 100,
    }
    assert calculate("blackbody_peak", temperature_k=3000)["values"]["peak_wavelength_nm"] == pytest.approx(965.923985)


def test_series_parallel_short_and_small_resistance():
    assert calculate("resistor_network", resistance_ohm=[10, 30])["values"] == {"series_ohm": 40, "parallel_ohm": 7.5}
    assert calculate("resistor_network", resistance_ohm=[0, 10])["values"]["parallel_ohm"] == 0
    tiny = calculate("resistor_network", resistance_ohm=[1e-310, 1e-310])["values"]["parallel_ohm"]
    assert math.isclose(tiny, 5e-311, rel_tol=1e-12)


def test_rc_step_retains_small_time_and_negative_step():
    result = calculate("rc_step", resistance_ohm=1, capacitance_f=1, step_v=-2, time_s=[0, 1e-20, 1])
    assert result["values"]["time_constant_s"] == 1
    assert result["plot"]["y"][0] == 0
    assert math.isclose(result["plot"]["y"][1], -2e-20, rel_tol=1e-15)
    assert result["plot"]["y"][-1] == pytest.approx(-2 * (1 - math.exp(-1)))


def test_thermal_expansion_allows_negative_coefficients():
    result = calculate("linear_expansion", length_m=10, coefficient_per_k=-1e-5, temperature_change_k=20)
    assert result["values"] == pytest.approx({"strain": -0.0002, "length_change_m": -0.002, "final_length_m": 9.998})


@pytest.mark.parametrize(
    ("operation_id", "changes"),
    [
        ("polynomial", {"coefficients": []}),
        ("polynomial", {"points": list(range(257))}),
        ("trapezoid", {"x": [0, 0], "y": [1, 2]}),
        ("trapezoid", {"x": [0, 1], "y": [1]}),
        ("linear_system", {"matrix": [[1, 2], [2, 4]]}),
        ("linear_system", {"matrix": [[1], [2]]}),
        ("linear_system", {"matrix": [[0, 0], [1, 2]]}),
        ("describe", {"observations": [2]}),
        ("linear_regression", {"x": [1, 1], "y": [2, 3]}),
        ("linear_regression", {"y": [1]}),
        ("weighted_summary", {"weights": [0, 0, 0]}),
        ("weighted_summary", {"weights": [0, -1, 1]}),
        ("spring_oscillator", {"stiffness_n_m": 0}),
        ("spring_oscillator", {"amplitude_m": -1}),
        ("thermal_layers", {"conductivity_w_mk": [0]}),
        ("thermal_layers", {"thickness_m": [0.1, 0]}),
        ("thermal_layers", {"right_k": -1}),
        ("sensible_heat", {"specific_heat_j_kgk": 0}),
        ("sensible_heat", {"initial_k": -10}),
        ("parallax_distance", {"parallax_mas": 0}),
        ("distance_modulus", {"extinction_mag": -0.1}),
        ("blackbody_peak", {"temperature_k": 0}),
        ("resistor_network", {"resistance_ohm": [10, -10]}),
        ("rc_step", {"resistance_ohm": 0}),
        ("rc_step", {"time_s": [-1, 1]}),
        ("rc_step", {"time_s": [1, 1]}),
        ("linear_expansion", {"length_m": 0}),
        ("linear_expansion", {"coefficient_per_k": 0.1, "temperature_change_k": -10}),
    ],
)
def test_invalid_model_assumptions(operation_id, changes):
    with pytest.raises(ValueError):
        calculate(operation_id, **changes)
