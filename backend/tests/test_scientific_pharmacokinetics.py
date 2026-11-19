"""Numerical oracles, invalid profiles and saved receipts for the Biology PK pack."""

import math
import tomllib
from pathlib import Path

import pytest

from graphlink_plugins.scientific.catalog import PACKS, PACK_FAMILIES, operations
from graphlink_plugins.scientific.core import VERSION, readable_result, run_operation


PACK = "pharmacokinetics"


def _run(operation_id, **data):
    op = next(op for op in operations(PACK) if op.id == operation_id)
    return run_operation(PACK, op, data)


def _profile(times, a, alpha, b=0, beta=0):
    return [a * math.exp(-alpha * t) + b * math.exp(-beta * t) for t in times]


def _inputs(times, concentrations, dose=100):
    return {"time": times, "concentration": concentrations, "dose": dose,
            "time_unit": "h", "response_unit": "mg/L"}


def test_manifest_pack_discovery_examples_and_receipts():
    root = Path(__file__).resolve().parents[2]
    with (root / "plugins/science_pharmacokinetics/plugin.toml").open("rb") as stream:
        manifest = tomllib.load(stream)
    assert manifest["plugin"]["id"] == "science_pharmacokinetics"
    assert manifest["plugin"]["version"] == VERSION
    assert manifest["scopes"]["grants"] == ["graph.read", "graph.mutate"]
    assert manifest["persistence"]["passive_node_kinds"] == ["workbench"]
    assert manifest["scientific"]["family"] == "biology"
    assert PACKS[PACK][0] == "Pharmacokinetics"
    assert PACK_FAMILIES[PACK] == "biology"
    assert [op.id for op in operations(PACK)] == [
        "noncompartmental_analysis", "one_compartment_fit", "two_compartment_fit",
    ]
    for op in operations(PACK):
        assert all(field.unit for field in op.fields)
        assert op.methods and op.citations
        result = run_operation(PACK, op, op.example)
        assert result["summary"] and result["values"] and result["table"] and result["plot"]
        assert readable_result({"version": 1, "latest": result}, PACK)
        assert set(result["inputs"]) == set(op.example)
        assert result["provenance"]["packVersion"] == VERSION
    assert (root / "docs/biology/pharmacokinetics.md").is_file()


def test_nca_exponential_exact_area_moment_half_life_and_volume():
    times = [0, 1, 2, 4, 6, 8, 12, 16, 20, 24]
    rate = math.log(2) / 4
    data = {**_inputs(times, _profile(times, 12, rate), 120),
            "route": "iv_bolus", "terminal_points": 4}
    result = _run("noncompartmental_analysis", **data)
    v = result["values"]
    assert v["cmax"] == 12
    assert v["tmax"] == 0
    assert v["lambda_z"] == pytest.approx(rate, rel=1e-12)
    assert v["terminal_half_life"] == pytest.approx(4)
    assert v["terminal_r_squared"] == pytest.approx(1)
    assert v["auc_inf"] == pytest.approx(12 / rate, rel=1e-12)
    assert v["aumc_inf"] == pytest.approx(12 / rate**2, rel=1e-12)
    assert v["mrt"] == pytest.approx(1 / rate)
    assert v["clearance"] == pytest.approx(120 * rate / 12)
    assert v["volume_z"] == pytest.approx(10)
    assert v["volume_ss"] == pytest.approx(10)
    assert v["auc_extrapolated_percent"] < 20
    assert len(result["table"]["rows"]) == len(times)


def test_nca_extravascular_peak_earliest_and_apparent_parameters_only():
    data = {**_inputs([0, 1, 2, 3, 4, 5, 6, 8, 10],
                      [0, 2, 5, 5, 4, 3, 2, 1, 0.5]),
            "route": "extravascular", "terminal_points": 4}
    v = _run("noncompartmental_analysis", **data)["values"]
    assert (v["cmax"], v["tmax"]) == (5, 2)
    assert v["auc_last"] > 1 + 3.5  # first two rising intervals use linear AUC
    assert v["auc_inf"] > v["auc_last"]
    assert v["clearance_over_f"] == pytest.approx(100 / v["auc_inf"])
    assert v["volume_z_over_f"] == pytest.approx(v["clearance_over_f"] / v["lambda_z"])
    assert "clearance" not in v and "volume_ss" not in v
    assert "Unknown bioavailability" in v["warnings"]


def test_converted_mass_and_time_units_match_base_units():
    times = list(range(9))
    concentrations = _profile(times, 10, 0.25)
    base = {**_inputs(times, concentrations), "route": "iv_bolus", "terminal_points": 4}
    scaled = {**base,
              "time": [f"{60*t} min" for t in times],
              "concentration": [f"{1000*c} ng/mL" for c in concentrations],
              "dose": "100000 ug"}
    a = _run("noncompartmental_analysis", **base)["values"]
    b = _run("noncompartmental_analysis", **scaled)["values"]
    assert b["auc_inf"] == pytest.approx(a["auc_inf"], rel=1e-12)
    assert b["volume_z"] == pytest.approx(a["volume_z"], rel=1e-12)
    assert b["clearance"] == pytest.approx(a["clearance"], rel=1e-12)


def test_one_compartment_fit_recovers_iv_exponential():
    times = [0, 0.5, 1, 2, 3, 4, 6, 8, 10, 12]
    v = _run("one_compartment_fit", **_inputs(times, _profile(times, 10, 0.2)))["values"]
    assert v["initial_concentration"] == pytest.approx(10, rel=1e-5)
    assert v["elimination_rate"] == pytest.approx(0.2, rel=1e-5)
    assert v["auc_inf"] == pytest.approx(50, rel=1e-5)
    assert v["clearance"] == pytest.approx(2, rel=1e-5)
    assert v["central_volume"] == pytest.approx(10, rel=1e-5)
    assert v["volume_z"] == pytest.approx(10, rel=1e-5)
    assert v["mrt"] == pytest.approx(5, rel=1e-5)
    assert v["terminal_half_life"] == pytest.approx(math.log(2) / 0.2, rel=1e-5)
    assert v["r_squared"] > 0.999999


def test_two_compartment_recovers_microconstants_and_distinct_phases():
    times = [0, .25, .5, 1, 2, 3, 4, 6, 8, 10, 14, 18]
    data = _inputs(times, _profile(times, 8, 1, 2, 0.12))
    result = _run("two_compartment_fit", **data)
    v = result["values"]
    assert v["fast_amplitude"] == pytest.approx(8, rel=1e-4)
    assert v["slow_amplitude"] == pytest.approx(2, rel=1e-4)
    assert v["alpha"] == pytest.approx(1, rel=1e-4)
    assert v["beta"] == pytest.approx(0.12, rel=1e-4)
    assert v["k21"] == pytest.approx(0.296, rel=1e-4)
    assert v["k10"] == pytest.approx(0.12 / 0.296, rel=1e-4)
    assert v["k12"] == pytest.approx(1.12 - 0.296 - 0.12 / 0.296, rel=1e-4)
    assert v["central_volume"] == pytest.approx(10, rel=1e-4)
    assert v["auc_inf"] == pytest.approx(8 + 2 / 0.12, rel=1e-4)
    assert v["clearance"] == pytest.approx(100 / (8 + 2 / 0.12), rel=1e-4)
    assert v["volume_z"] == pytest.approx(v["clearance"] / 0.12, rel=1e-4)
    assert v["volume_ss"] == pytest.approx(v["clearance"] * v["mrt"])
    assert v["terminal_half_life"] == pytest.approx(math.log(2) / 0.12, rel=1e-4)
    assert v["r_squared"] > 0.999999
    one = _run("one_compartment_fit", **data)["values"]
    assert v["residual_sum_squares"] < one["residual_sum_squares"] * 1e-6
    assert all(row[1] - row[2] == pytest.approx(row[3]) for row in result["table"]["rows"])


@pytest.mark.parametrize("change,pattern", [
    ({"time": [1, 2, 3, 4, 5, 6, 7, 8, 9]}, "start at zero"),
    ({"time": [0, 1, 2, 2, 4, 5, 6, 7, 8]}, "strictly increasing"),
    ({"concentration": [10, 8, 7, -1, 5, 4, 3, 2, 1]}, "nonnegative"),
    ({"dose": 0}, "dose must be positive"),
    ({"response_unit": "uM"}, "mass/volume"),
    ({"time_unit": "kg"}, "time unit"),
    ({"terminal_points": 2}, "3 or more"),
    ({"terminal_points": 9}, "leaving an earlier"),
    ({"concentration": [10, 9, 8, 7, 5, 4, 0, 0, 0]}, "strictly positive"),
    ({"concentration": [10, 9, 8, 7, 6, 5, 6, 7, 8]}, "negative log-linear slope"),
])
def test_nca_rejects_invalid_designs(change, pattern):
    base = {**_inputs(list(range(9)), _profile(range(9), 10, 0.25)),
            "route": "iv_bolus", "terminal_points": 3}
    with pytest.raises(ValueError, match=pattern):
        _run("noncompartmental_analysis", **{**base, **change})


@pytest.mark.parametrize("name,data,pattern", [
    ("one_compartment_fit", {"concentration": [10, 8, 0, 5, 4, 3, 2, 1, 0.5]}, "strictly positive"),
    ("two_compartment_fit", {"time": list(range(7)), "concentration": [10, 9, 8, 7, 6, 5, 4]}, "At least 8"),
    ("two_compartment_fit", {"concentration": [10, 11, 12, 13, 14, 15, 16, 17, 18]}, "declining"),
])
def test_compartment_fits_reject_invalid_designs(name, data, pattern):
    base = _inputs(list(range(9)), _profile(range(9), 10, 0.25))
    with pytest.raises(ValueError, match=pattern):
        _run(name, **{**base, **data})
