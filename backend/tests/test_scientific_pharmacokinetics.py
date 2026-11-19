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
        "multiple_dose_prediction", "human_equivalent_dose", "drug_combination_analysis",
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



def _repeated_inputs():
    return {"dose": 100, "central_volume": 10, "elimination_rate": math.log(2),
            "dosing_interval": 1, "number_of_doses": 5}


def _combination_inputs():
    return {"concentration_unit": "mg/L", "dose_a": 1, "median_effect_dose_a": 2,
            "slope_a": 1, "dose_b": 2, "median_effect_dose_b": 4, "slope_b": 1,
            "observed_combination_effect": 0.75, "interaction_model": "exclusive"}


def test_multiple_dose_accumulation_and_steady_state_analytic_oracles():
    result = _run("multiple_dose_prediction", **_repeated_inputs())
    v = result["values"]
    assert v["terminal_half_life"] == pytest.approx(1)
    assert v["accumulation_ratio_after_n_doses"] == pytest.approx(1.9375)
    assert v["steady_state_accumulation_ratio"] == pytest.approx(2)
    assert v["peak_after_last_dose"] == pytest.approx(19.375)
    assert v["trough_after_last_interval"] == pytest.approx(9.6875)
    assert v["steady_state_peak"] == pytest.approx(20)
    assert v["steady_state_trough"] == pytest.approx(10)
    assert v["steady_state_average"] == pytest.approx(10 / math.log(2))
    assert v["fraction_of_steady_state_peak_after_n_doses"] == pytest.approx(31/32)
    assert v["time_to_95_percent_envelope"] == pytest.approx(math.log(20) / math.log(2))
    assert result["table"]["rows"][0] == pytest.approx([1, 0, 0, 10, 5, 1])
    assert result["table"]["rows"][-1][1] == 4
    assert result["table"]["rows"][-1][3:] == pytest.approx([19.375, 9.6875, 1.9375])
    # At each administration, time is duplicated to model a discontinuous IV bolus.
    assert result["plot"]["x"][:3] == [0, 0, 1]
    assert result["plot"]["y"][:3] == pytest.approx([0, 10, 5])
    assert len(result["plot"]["x"]) == 15

    one = _run("multiple_dose_prediction", **{**_repeated_inputs(), "number_of_doses": 1})
    assert one["values"]["accumulation_ratio_after_n_doses"] == pytest.approx(1)
    assert one["values"]["peak_after_last_dose"] == pytest.approx(10)
    assert one["values"]["trough_after_last_interval"] == pytest.approx(5)
    many = _run("multiple_dose_prediction", **{**_repeated_inputs(), "number_of_doses": 200})
    assert len(many["table"]["rows"]) == 200
    assert len(many["plot"]["x"]) == 600
    assert many["values"]["peak_after_last_dose"] == pytest.approx(20)


def test_multiple_dose_field_units_convert_and_short_intervals_are_stable():
    base = _run("multiple_dose_prediction", **{
        "dose": 100, "central_volume": 10, "elimination_rate": 12,
        "dosing_interval": 1, "number_of_doses": 3,
    })["values"]
    converted = _run("multiple_dose_prediction", **{
        "dose": "100000 ug", "central_volume": "10000 mL",
        "elimination_rate": "0.2 1/min", "dosing_interval": "60 min",
        "number_of_doses": "3",
    })["values"]
    for field in ("peak_after_last_dose", "steady_state_peak", "steady_state_average"):
        assert converted[field] == pytest.approx(base[field])
    short = _run("multiple_dose_prediction", **{
        **_repeated_inputs(), "elimination_rate": 1e-8,
    })["values"]
    assert short["accumulation_ratio_after_n_doses"] == pytest.approx(5, rel=1e-7)
    assert short["steady_state_accumulation_ratio"] == pytest.approx(1e8, rel=1e-7)


@pytest.mark.parametrize("change,pattern", [
    ({"dose": 0}, "must be positive"),
    ({"central_volume": -2}, "must be positive"),
    ({"elimination_rate": 0}, "must be positive"),
    ({"dosing_interval": 0}, "must be positive"),
    ({"number_of_doses": 0}, "from 1 to 200"),
    ({"number_of_doses": 201}, "from 1 to 200"),
    ({"dosing_interval": 1e-320}, "numeric range"),
])
def test_multiple_dose_invalid_inputs(change, pattern):
    with pytest.raises(ValueError, match=pattern):
        _run("multiple_dose_prediction", **{**_repeated_inputs(), **change})


@pytest.mark.parametrize("species,km", [
    ("mouse", 3), ("hamster", 5), ("rat", 6), ("guinea_pig", 8),
    ("rabbit", 12), ("dog", 20), ("monkey", 12), ("mini_pig", 35),
])
def test_human_equivalent_dose_uses_fda_reference_km(species, km):
    v = _run("human_equivalent_dose", species=species, animal_dose=100, human_weight=60)["values"]
    assert v["animal_km"] == km
    assert v["human_km"] == 37
    assert v["bsa_conversion_factor"] == pytest.approx(km/37)
    assert v["human_equivalent_dose"] == pytest.approx(100*km/37)
    assert v["human_total_equivalent"] == pytest.approx(6000*km/37)
    assert "not a human starting dose" in v["warnings"]


def test_hed_input_unit_conversion_and_reference_weight_warning():
    original = _run("human_equivalent_dose", species="mouse", animal_dose=100, human_weight=60)
    converted = _run("human_equivalent_dose", species="mouse",
                     animal_dose="100000 ug/kg", human_weight="60000 g")
    assert converted["values"]["human_equivalent_dose"] == pytest.approx(
        original["values"]["human_equivalent_dose"])
    assert len(original["table"]["rows"]) == 5
    assert original["plot"]["x"][-1] == 100
    different_weight = _run("human_equivalent_dose", species="mouse",
                            animal_dose=100, human_weight=70)["values"]
    assert different_weight["human_equivalent_dose"] == pytest.approx(
        original["values"]["human_equivalent_dose"])
    assert "not individualized BSA" in different_weight["warnings"]


@pytest.mark.parametrize("change,pattern", [
    ({"animal_dose": 0}, "must be positive"),
    ({"human_weight": -1}, "must be positive"),
    ({"species": "elephant"}, "must be one of"),
    ({"animal_dose": "5 mg/L"}, "Incompatible units"),
])
def test_hed_invalid_inputs(change, pattern):
    base = {"species": "rat", "animal_dose": 25, "human_weight": 60}
    with pytest.raises(ValueError, match=pattern):
        _run("human_equivalent_dose", **{**base, **change})


def test_bliss_and_chou_talalay_oracles_and_model_switch():
    result = _run("drug_combination_analysis", **_combination_inputs())
    v = result["values"]
    assert v["single_agent_effect_a"] == pytest.approx(1/3)
    assert v["single_agent_effect_b"] == pytest.approx(1/3)
    assert v["bliss_expected_effect"] == pytest.approx(5/9)
    assert v["bliss_excess_effect"] == pytest.approx(0.75 - 5/9)
    assert v["equieffective_single_agent_dose_a"] == pytest.approx(6)
    assert v["equieffective_single_agent_dose_b"] == pytest.approx(12)
    assert v["dose_fraction_a"] == pytest.approx(1/6)
    assert v["dose_fraction_b"] == pytest.approx(1/6)
    assert v["ci_exclusive"] == pytest.approx(1/3)
    assert v["ci_nonexclusive"] == pytest.approx(13/36)
    assert v["combination_index"] == pytest.approx(1/3)
    assert v["bliss_direction"] == "above"
    assert v["ci_direction"] == "below 1"
    assert result["plot"]["y"][0] == 0
    assert result["plot"]["y"][-1] == pytest.approx(5/9)

    nonexclusive = _run("drug_combination_analysis", **{
        **_combination_inputs(), "interaction_model": "nonexclusive",
    })["values"]
    assert nonexclusive["combination_index"] == pytest.approx(13/36)
    # A CI of one in the exclusive model need not imply a zero Bliss excess:
    # the null models have distinct mathematical assumptions.
    additive_ci = _run("drug_combination_analysis", **{
        **_combination_inputs(), "observed_combination_effect": 0.5,
    })["values"]
    assert additive_ci["combination_index"] == pytest.approx(1)
    assert additive_ci["bliss_excess_effect"] == pytest.approx(0.5 - 5/9)


def test_combination_supports_mass_and_molar_concentration_units():
    base = _run("drug_combination_analysis", **_combination_inputs())["values"]
    converted = _run("drug_combination_analysis", **{
        **_combination_inputs(), "dose_a": "1000 ug/L",
        "median_effect_dose_a": "2000 ug/L",
        "dose_b": "2 ug/mL", "median_effect_dose_b": "4 ug/mL",
    })["values"]
    molar = _run("drug_combination_analysis", **{
        **_combination_inputs(), "concentration_unit": "uM",
    })["values"]
    for field in ("bliss_expected_effect", "bliss_excess_effect", "combination_index"):
        assert converted[field] == pytest.approx(base[field])
        assert molar[field] == pytest.approx(base[field])
    assert molar["equieffective_dose_unit"] == "uM"


@pytest.mark.parametrize("change,pattern", [
    ({"dose_a": 0}, "must be positive"),
    ({"median_effect_dose_b": 0}, "must be positive"),
    ({"slope_a": -0.5}, "must be positive"),
    ({"observed_combination_effect": 0}, "strictly between zero and one"),
    ({"observed_combination_effect": 1}, "strictly between zero and one"),
    ({"concentration_unit": "s"}, "mass/volume or amount/volume"),
    ({"interaction_model": "both"}, "must be one of"),
])
def test_combination_rejects_undefined_or_incompatible_inputs(change, pattern):
    with pytest.raises(ValueError, match=pattern):
        _run("drug_combination_analysis", **{**_combination_inputs(), **change})
