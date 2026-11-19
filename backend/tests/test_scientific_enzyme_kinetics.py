"""Enzyme Kinetics pack: independent rate-law and unit-conversion regression oracles."""

import copy
import math
import tomllib
from pathlib import Path

import pytest

from graphlink_plugins.scientific.catalog import PACK_FAMILIES, operations
from graphlink_plugins.scientific.core import VERSION, readable_result, run_operation
from graphlink_plugins.scientific.packs import enzyme_kinetics


PACK = "enzyme_kinetics"


def _run(operation_id, **inputs):
    operation = next(op for op in operations(PACK) if op.id == operation_id)
    return run_operation(PACK, operation, inputs)


def _rates(mechanism, *, noise=False):
    # Deliberately independent expression, not the implementation's prediction helper.
    doses = (0, 1.5, 4.5, 8)
    levels = (.25, .5, 1, 2, 4, 8, 16)
    perturbation = (.10, -.12, .04, -.07, .09, -.02, .06, -.11, .03, -.05, .08)
    substrate, inhibitor, rate = [], [], []
    for dose in doses:
        for s in levels:
            alpha = 1 + dose / 1.5 if mechanism in ("competitive", "noncompetitive", "mixed") else 1
            alpha_prime = (1 + dose / 4 if mechanism == "mixed" else
                           1 + dose / 1.5 if mechanism in ("uncompetitive", "noncompetitive") else 1)
            value = 100 * s / (alpha * 2 + alpha_prime * s)
            substrate.append(s)
            inhibitor.append(dose)
            rate.append(value + perturbation[len(rate) % len(perturbation)] if noise else value)
    return {"substrate": substrate, "inhibitor": inhibitor, "initial_rate": rate,
            "substrate_unit": "mM", "concentration_unit": "uM", "rate_unit": "uM/s"}


def test_manifest_discovery_metadata_and_example_receipts():
    root = Path(__file__).resolve().parents[2]
    with (root / "plugins/science_enzyme_kinetics/plugin.toml").open("rb") as file:
        manifest = tomllib.load(file)
    assert manifest["plugin"]["id"] == "science_enzyme_kinetics"
    assert manifest["plugin"]["version"] == VERSION
    assert manifest["persistence"]["passive_node_kinds"] == ["workbench"]
    assert manifest["scopes"]["grants"] == ["graph.read", "graph.mutate"]
    assert PACK_FAMILIES[PACK] == "biology"
    assert [op.id for op in operations(PACK)] == [
        "inhibition_model_fit", "inhibition_model_compare", "catalytic_constants",
        "hill_fit", "cheng_prusoff_ki",
        "binding_isotherm_fit", "morrison_tight_binding_fit", "schild_analysis", "thermal_shift_tm",
    ]
    for operation in operations(PACK):
        assert operation.methods and operation.citations
        assert all(field.unit for field in operation.fields)
        assert operation.describe()["methods"] == operation.methods
        result = _run(operation.id, **operation.example)
        assert result["operationId"] == operation.id
        assert result["provenance"]["packId"] == PACK
        assert result["provenance"]["packVersion"] == VERSION
        assert readable_result({"version": 1, "latest": result}, PACK)
        broken = copy.deepcopy(result)
        broken["values"][next(iter(broken["values"]))] = "modified"
        assert not readable_result({"version": 1, "latest": broken}, PACK)
        if "table" in result:
            assert result["plot"]["xLabel"] and result["plot"]["yLabel"]
            assert len(result["table"]["rows"]) > 0


@pytest.mark.parametrize("mechanism", ("competitive", "uncompetitive", "noncompetitive", "mixed"))
def test_joint_inhibition_fits_known_rate_surfaces(mechanism):
    result = _run("inhibition_model_fit", **_rates(mechanism, noise=True), model=mechanism)
    values = result["values"]
    assert values["model"] == mechanism
    assert values["vmax"] == pytest.approx(100, rel=0.015)
    assert values["km"] == pytest.approx(2, rel=0.02)
    assert values["vmax_unit"] == "uM/s" and values["km_unit"] == "mM"
    for key in ("ki", "ki_prime"):
        if key in values:
            assert values[key] == pytest.approx(4 if mechanism == "mixed" and key == "ki_prime" else 1.5,
                                                rel=0.04)
            assert values[f"{key}_unit"] == "uM"
            assert values[f"{key}_standard_error"] >= 0
    assert values["residual_sum_squares"] < 1
    assert values["r_squared"] > .999
    assert values["converged"] == "true"
    assert isinstance(values["aicc"], float)
    assert len(result["table"]["rows"]) == len(_rates(mechanism)["substrate"])
    assert result["plot"]["xLabel"].startswith("Observed initial rate")
    assert all(row[4] == pytest.approx(row[2] - row[3]) for row in result["table"]["rows"])


@pytest.mark.parametrize("mechanism", ("competitive", "uncompetitive", "noncompetitive", "mixed"))
def test_model_comparison_refits_and_selects_true_mechanism(mechanism):
    result = _run("inhibition_model_compare", **_rates(mechanism, noise=True))
    values = result["values"]
    assert values["best_model"] == mechanism
    assert values["models_compared"] == 4
    assert values["models_rejected"] == 0
    assert len(result["table"]["rows"]) == 4
    weights = [values[f"akaike_weight_{model}"] for model in enzyme_kinetics._MODELS]
    assert sum(weights) == pytest.approx(1, abs=1e-12)
    assert all(0 <= weight <= 1 for weight in weights)
    assert result["table"]["rows"][0][1] == mechanism
    assert result["table"]["rows"][0][5] == 0
    assert result["table"]["rows"][0][6] == max(weights)
    assert [row[3] for row in result["table"]["rows"]] == sorted(row[3] for row in result["table"]["rows"])


def test_inhibition_separate_units_replicates_and_zero_substrate_controls():
    data = _rates("competitive", noise=True)
    # A blank is modeled at zero even when inhibitor is nonzero; no intercept is invented.
    data["substrate"] += [0, 0]
    data["inhibitor"] += [0, 8]
    data["initial_rate"] += [0, 0]
    result = _run("inhibition_model_fit", **data, model="competitive")
    assert result["values"]["ki"] == pytest.approx(1.5, rel=.04)
    assert result["table"]["rows"][-2][-2:] == [0, 0]
    assert result["table"]["rows"][-1][-2:] == [0, 0]


def test_catalytic_constants_and_si_efficiency_conversion():
    example = next(op for op in operations(PACK) if op.id == "catalytic_constants").example
    values = _run("catalytic_constants", **example)["values"]
    assert values["kcat"] == pytest.approx(1200)
    assert values["kcat_per_second"] == pytest.approx(20)
    assert values["kcat_over_km"] == pytest.approx(60)
    assert values["kcat_over_km_si"] == pytest.approx(1e6)
    assert values["kcat_over_km_si_unit"] == "1/(M*s)"
    # Use explicit unit-suffixed strings to exercise shared core conversion.
    converted = _run("catalytic_constants", **{**example,
        "vmax": "0.12 mM/min", "km": "0.02 mM", "enzyme_concentration": "100 nM",
    })["values"]
    assert converted["kcat_per_second"] == pytest.approx(20)
    assert converted["kcat_over_km_si"] == pytest.approx(1e6)


def test_hill_fit_from_independent_saturation_curve():
    xs = [.15, .3, .6, 1.2, 2.5, 5, 10, 20, 40, 80]
    noise = [.06, -.05, .03, -.09, .02, .07, -.08, .1, -.04, .03]
    ys = [90 * (s / 4) ** 1.7 / (1 + (s / 4) ** 1.7) + delta
          for s, delta in zip(xs, noise)]
    result = _run("hill_fit", substrate=xs, initial_rate=ys,
                  substrate_unit="uM", rate_unit="uM/s")
    values = result["values"]
    assert values["vmax"] == pytest.approx(90, rel=.02)
    assert values["k_half"] == pytest.approx(4, rel=.02)
    assert values["hill_coefficient"] == pytest.approx(1.7, rel=.02)
    assert values["hill_coefficient_unit"] == "1 (dimensionless)"
    assert values["k_half_standard_error"] > 0
    assert values["hill_coefficient_standard_error"] > 0
    assert values["r_squared"] > .999
    assert len(result["table"]["rows"]) == len(xs)


def test_hill_exponent_one_recovers_michaelis_menten_shape():
    xs = [.2, .4, .8, 1.5, 3, 5, 10, 20, 40]
    ys = [60 * x / (3 + x) + (0.01 if i % 2 else -0.01) for i, x in enumerate(xs)]
    v = _run("hill_fit", substrate=xs, initial_rate=ys,
             substrate_unit="mM", rate_unit="mM/s")["values"]
    assert v["hill_coefficient"] == pytest.approx(1, abs=.01)
    assert v["k_half"] == pytest.approx(3, rel=.01)


def test_cheng_prusoff_identity_zero_substrate_and_typed_conversion():
    kwargs = {"ic50": 15, "substrate_concentration": 8, "km": 4,
              "concentration_unit": "uM"}
    v = _run("cheng_prusoff_ki", **kwargs)["values"]
    assert v["ki"] == pytest.approx(5)
    assert v["ki_unit"] == "uM"
    assert v["cheng_prusoff_factor"] == 3
    zero = _run("cheng_prusoff_ki", **{**kwargs, "substrate_concentration": 0})["values"]
    assert zero["ki"] == pytest.approx(15)
    unit_suffix = _run("cheng_prusoff_ki", **{**kwargs, "substrate_concentration": "0.008 mM",
                                                "ic50": "15000 nM"})["values"]
    assert unit_suffix["ki"] == pytest.approx(5)


@pytest.mark.parametrize("name,changes,match", [
    ("inhibition_model_fit", {"inhibitor": [0] * 18}, "inhibitor levels"),
    ("inhibition_model_fit", {"initial_rate": [1, 2, 3]}, "aligned"),
    ("inhibition_model_fit", {"initial_rate": [-1] * 18}, "nonnegative"),
    ("inhibition_model_fit", {"substrate": [0] * 18}, "substrate levels"),
    ("inhibition_model_fit", {"substrate": [0] + [1] * 17}, "substrate levels"),
    ("inhibition_model_fit", {"model": "allosteric"}, "must be one of"),
    ("catalytic_constants", {"enzyme_concentration": 0}, "positive"),
    ("catalytic_constants", {"km": 0}, "positive"),
    ("catalytic_constants", {"rate_unit": "uM"}, "concentration/time"),
    ("hill_fit", {"substrate": [0, 1, 2, 3, 4, 5, 6, 7, 8]}, "positive substrate"),
    ("hill_fit", {"initial_rate": [-1] * 9}, "nonnegative"),
    ("cheng_prusoff_ki", {"ic50": 0}, "positive"),
    ("cheng_prusoff_ki", {"km": 0}, "positive"),
    ("cheng_prusoff_ki", {"substrate_concentration": -1}, "nonnegative"),
])
def test_invalid_inputs_rejected(name, changes, match):
    example = next(op.example for op in operations(PACK) if op.id == name)
    with pytest.raises(ValueError, match=match):
        _run(name, **{**example, **changes})


def test_unidentifiable_inhibition_design_is_rejected():
    data = _rates("competitive", noise=True)
    # All treated measurements at one substrate remove mechanism identifiability.
    data["substrate"] = [s if i == 0 else 2 for s, i in zip(data["substrate"], data["inhibitor"])]
    with pytest.raises(ValueError, match="substrate levels"):
        _run("inhibition_model_compare", **data)


def test_hill_exact_response_is_finite_and_well_labeled():
    s = [.1, .25, .5, 1, 2, 4, 8, 16]
    v = [50 * x ** 2 / (4 + x ** 2) for x in s]
    r = _run("hill_fit", substrate=s, initial_rate=v, substrate_unit="uM", rate_unit="uM/s")
    assert math.isfinite(r["values"]["vmax"])
    assert r["values"]["hill_coefficient"] == pytest.approx(2, abs=.01)
