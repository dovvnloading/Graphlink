"""JCGM 100:2008 Annex H oracles and adversarial validation for the GUM pack."""

import copy
import json
import math
import statistics
import tomllib
from pathlib import Path

import pytest

from graphlink_plugins.scientific.catalog import PACKS, operations
from graphlink_plugins.scientific.core import VERSION, readable_result, run_operation
from graphlink_plugins.scientific.packs.fitting import _student_t_ppf

FIXTURE = json.loads(Path(__file__).with_name("test_scientific_uncertainty_gum.json").read_text(encoding="utf-8"))
OPERATION = next(op for op in operations("uncertainty") if op.id == "propagate_uncertainty")


def run(expression, inputs, correlations=None, coverage=0.95):
    return run_operation("uncertainty", OPERATION, {
        "expression": expression,
        "measurements": json.dumps(inputs),
        "correlations": json.dumps(correlations if correlations is not None else []),
        "coverage_probability": coverage,
    })


def test_manifest_example_receipt_and_registration():
    assert PACKS["uncertainty"][0] == "Measurement Uncertainty"
    manifest = Path(__file__).resolve().parents[2] / "plugins" / "science_uncertainty" / "plugin.toml"
    with manifest.open("rb") as file:
        data = tomllib.load(file)
    assert data["plugin"]["id"] == "science_uncertainty"
    assert data["plugin"]["version"] == VERSION
    assert data["scopes"]["grants"] == ["graph.read", "graph.mutate"]
    assert data["persistence"]["passive_node_kinds"] == ["workbench"]
    assert len(operations("uncertainty")) == 1
    result = run_operation("uncertainty", OPERATION, OPERATION.example)
    assert readable_result({"version": 1, "latest": result}, "uncertainty")
    modified = copy.deepcopy(result)
    modified["values"]["estimate"] += 1
    assert not readable_result({"version": 1, "latest": modified}, "uncertainty")


def test_gum_h1_end_gauge_first_order_results_at_published_digits():
    # H.1.2 / H.1.3 / H.1.4 / H.1.5. Values in mm, degrees C and 1/C.
    # H.1.7 explicitly adds second-order effects to obtain 34 nm, outside this pack.
    measurements = {
        "lS": {"value": 50.000623, "uncertainty": 25e-6, "degrees_of_freedom": 18},
        "d": {"value": 0.000215, "uncertainty": 9.7e-6, "degrees_of_freedom": 25.6},
        "alphaS": {"value": 11.5e-6, "uncertainty": 1.2e-6},
        "theta": {"value": -0.1, "uncertainty": 0.41},
        "deltaAlpha": {"value": 0, "uncertainty": 1e-6 / math.sqrt(3), "degrees_of_freedom": 50},
        "deltaTheta": {"value": 0, "uncertainty": 0.05 / math.sqrt(3), "degrees_of_freedom": 2},
    }
    result = run("lS + d - lS*(deltaAlpha*theta + alphaS*deltaTheta)", measurements)
    values = result["values"]
    oracle = FIXTURE["H1"]
    assert values["estimate"] == pytest.approx(oracle["length_mm"], abs=1e-10)
    assert round(values["combined_standard_uncertainty"] * 1e6) == oracle["standard_uncertainty_nm"]
    assert math.floor(values["effective_degrees_of_freedom"]) == oracle["effective_degrees_of_freedom_truncated"]
    assert values["coverage_factor"] == pytest.approx(
        _student_t_ppf(0.975, values["effective_degrees_of_freedom"]), rel=1e-12)
    assert values["expanded_uncertainty"] == pytest.approx(
        values["coverage_factor"] * values["combined_standard_uncertainty"], rel=1e-14)
    assert values["sensitivity_alphaS"] == pytest.approx(0, abs=1e-8)
    assert values["sensitivity_theta"] == pytest.approx(0, abs=1e-8)
    for name, expected in oracle["sensitivity_contributions_nm"].items():
        assert abs(values[f"contribution_{name}"]) * 1e6 == pytest.approx(expected, abs=0.04)


def _h2_inputs():
    oracle = FIXTURE["H2"]
    groups = {"V": oracle["voltage_V"], "I": oracle["current_mA"], "phi": oracle["phase_rad"]}
    # Independent sample moments from the five simultaneous observation sets,
    # not rounded summary standard deviations/correlations in Table H.2.
    measurements = {name: {
        "value": statistics.mean(row), "uncertainty": statistics.stdev(row) / math.sqrt(len(row)),
        "degrees_of_freedom": len(row) - 1,
    } for name, row in groups.items()}
    correlations = []
    for a, b in [("V", "I"), ("V", "phi"), ("I", "phi")]:
        covariance = statistics.covariance(groups[a], groups[b])
        coefficient = covariance / (statistics.stdev(groups[a]) * statistics.stdev(groups[b]))
        correlations.append({"a": a, "b": b, "coefficient": coefficient})
    return measurements, correlations


@pytest.mark.parametrize("quantity,expression", [
    ("R", "1000*V/I*cos(phi)"), ("X", "1000*V/I*sin(phi)"), ("Z", "1000*V/I"),
])
def test_gum_h2_correlated_resistance_reactance_impedance(quantity, expression):
    measurements, correlations = _h2_inputs()
    if quantity == "Z":
        # The operation requires every declared input to occur in its model.
        measurements.pop("phi")
        correlations = [item for item in correlations if "phi" not in (item["a"], item["b"])]
    values = run(expression, measurements, correlations)["values"]
    published = FIXTURE["H2"]["published_results_ohm"][quantity]
    assert round(values["estimate"], 3) == published["estimate"]
    # Annex H notes that the displayed results retain hidden extra digits:
    # propagation from the *published five input rows* differs by at most
    # one unit in the last shown decimal (X: 0.29558 vs 0.295).
    assert values["combined_standard_uncertainty"] == pytest.approx(
        published["standard_uncertainty"], abs=0.001)
    assert values["effective_degrees_of_freedom"] == pytest.approx(4, rel=1e-12)
    assert values["coverage_factor"] == pytest.approx(_student_t_ppf(0.975, 4), rel=1e-12)
    uncorrelated = run(expression, measurements)["values"]
    assert round(uncorrelated["combined_standard_uncertainty"], 3) == (
        FIXTURE["H2"]["published_uncorrelated_standard_uncertainty_ohm"][quantity])


def test_gum_h2_approach2_published_figures_from_the_five_observation_sets():
    # Annex H.2.4/Table H.4: direct measured-output standard deviations retain
    # the displayed digits even where rounded first-order inputs differ slightly.
    rows = FIXTURE["H2"]
    measured = {
        "R": [1000 * v / i * math.cos(phi) for v, i, phi in zip(
            rows["voltage_V"], rows["current_mA"], rows["phase_rad"])],
        "X": [1000 * v / i * math.sin(phi) for v, i, phi in zip(
            rows["voltage_V"], rows["current_mA"], rows["phase_rad"])],
        "Z": [1000 * v / i for v, i in zip(rows["voltage_V"], rows["current_mA"])],
    }
    for name, samples in measured.items():
        reference = rows["published_results_ohm"][name]
        assert round(statistics.mean(samples), 3) == reference["estimate"]
        assert round(statistics.stdev(samples) / math.sqrt(5), 3) == reference["standard_uncertainty"]


@pytest.mark.parametrize("expression,expected", [
    ("x*0+2^3^2", 512), ("x*0-2^2", -4), ("x*0+2^-2", 0.25),
    ("sqrt(x^2)+exp(0)+ln(1)+log10(100)+sin(0)+cos(0)", 6),
    ("(x+1)/(x-1)", 3),
])
def test_allowed_expression_grammar(expression, expected):
    assert run(expression, {"x": {"value": 2, "uncertainty": 0.01}})["values"]["estimate"] == pytest.approx(expected)


@pytest.mark.parametrize("expression", [
    "__import__('os').system('id')", "x.__class__", "x[0]", "x or 1", "x; 2",
    "x ** 2", "lambda x: x", "f(x)", "sqrt(x, 2)", "sin x", "x(2)",
    "x+", "x/", "(x", "x)", "x..2", "2x", "2 3", "x^", "()", "",
    "x + (" * 55 + "x" + ")" * 55, "x+" * 260 + "x", "1e309*x",
])
def test_reject_unsafe_and_malformed_expressions(expression):
    with pytest.raises(ValueError):
        run(expression, {"x": {"value": 2, "uncertainty": 0.1}})


@pytest.mark.parametrize("expression,inputs,correlations,coverage", [
    ("x", {"x": {"value": 1, "uncertainty": -1}}, [], .95),
    ("x", {"x": {"value": True, "uncertainty": 1}}, [], .95),
    ("x", {"x": {"value": float("nan"), "uncertainty": 1}}, [], .95),
    ("x", {"x": {"value": 1, "uncertainty": float("inf")}}, [], .95),
    ("x", {"x": {"value": 1, "uncertainty": 1, "degrees_of_freedom": 0}}, [], .95),
    ("x", {"x": {"value": 1, "uncertainty": 1, "degrees_of_freedom": -3}}, [], .95),
    ("x", {"x": {"value": 1, "uncertainty": 1, "extra": 3}}, [], .95),
    ("x", {"x": {"value": 1}}, [], .95),
    ("x", {"x": {"value": 1, "uncertainty": 1}, "y": {"value": 2, "uncertainty": 1}}, [], .95),
    ("x", {"sin": {"value": 1, "uncertainty": 1}}, [], .95),
    ("x", {"x": {"value": 1, "uncertainty": 1}}, [], 0),
    ("x", {"x": {"value": 1, "uncertainty": 1}}, [], 1),
    ("x", {"x": {"value": 1, "uncertainty": 1}}, [], -0.1),
    ("x", {"x": {"value": 1, "uncertainty": 1}}, [], 1.1),
    ("ln(x)", {"x": {"value": -1, "uncertainty": 1}}, [], .95),
    ("sqrt(x)", {"x": {"value": 0, "uncertainty": 1}}, [], .95),
    ("1/(x-1)", {"x": {"value": 1, "uncertainty": 0.01}}, [], .95),
    ("x^0.5", {"x": {"value": -1, "uncertainty": 0.01}}, [], .95),
])
def test_reject_invalid_measurements_or_domains(expression, inputs, correlations, coverage):
    with pytest.raises(ValueError):
        run(expression, inputs, correlations, coverage)


def test_reject_correlations_with_inconsistent_matrix_and_repeated_or_unknown_pairs():
    m = {name: {"value": 1, "uncertainty": 0.2} for name in ("x", "y", "z")}
    model = "x+y+z"
    bad = [
        [{"a": "x", "b": "y", "coefficient": 1.1}],
        [{"a": "x", "b": "x", "coefficient": 0.5}],
        [{"a": "x", "b": "q", "coefficient": 0.5}],
        [{"a": "x", "b": "y", "coefficient": 0.5}, {"a": "y", "b": "x", "coefficient": 0.5}],
        [{"a": "x", "b": "y", "coefficient": 0.9},
         {"a": "x", "b": "z", "coefficient": 0.9},
         {"a": "y", "b": "z", "coefficient": -0.9}],
        [{"a": "x", "b": "y", "coefficient": True}],
        [{"a": "x", "b": "y", "coefficient": 0.5, "other": 0}],
    ]
    for correlation in bad:
        with pytest.raises(ValueError):
            run(model, m, correlation)


def test_reject_mismatched_joint_degrees_of_freedom_and_duplicate_json_keys():
    with pytest.raises(ValueError, match="common joint degrees"):
        run("x+y", {"x": {"value": 1, "uncertainty": 0.5, "degrees_of_freedom": 4},
                    "y": {"value": 2, "uncertainty": 0.5, "degrees_of_freedom": 6}},
            [{"a": "x", "b": "y", "coefficient": 0.3}])
    raw = dict(OPERATION.example)
    raw["measurements"] = '{"x":{"value":1,"value":2,"uncertainty":0.1}}'
    raw["expression"] = "x"
    with pytest.raises(ValueError, match="duplicate"):
        run_operation("uncertainty", OPERATION, raw)


def test_perfect_anticorrelation_zero_combined_uncertainty_and_normal_limit():
    r = run("x+y", {"x": {"value": 2, "uncertainty": 1},
                     "y": {"value": 3, "uncertainty": 1}},
            [{"a": "x", "b": "y", "coefficient": -1}])["values"]
    assert r["combined_standard_uncertainty"] == pytest.approx(0, abs=1e-11)
    assert r["effective_degrees_of_freedom"] == "infinite"
    assert r["coverage_factor"] == pytest.approx(1.959963984540054, rel=1e-12)
