"""Numerical oracles and boundaries for the grant-gated Biology physiology pack."""

import json
import math
import tomllib
from pathlib import Path

import pytest

from graphlink_plugins.scientific.catalog import PACKS, PACK_FAMILIES, operations
from graphlink_plugins.scientific.core import VERSION, readable_result, run_operation
from graphlink_plugins.scientific.packs.physiology import _hh_rates


PACK = "physiology"
OP_IDS = (
    "nernst_potential", "ghk_potential", "membrane_properties",
    "hodgkin_huxley", "spike_detection", "fick_diffusion",
)


def run(operation_id, **changes):
    operation = next(op for op in operations(PACK) if op.id == operation_id)
    return run_operation(PACK, operation, {**operation.example, **changes})


def test_manifest_discovery_all_examples_and_receipts():
    root = Path(__file__).resolve().parents[2]
    with (root / "plugins/science_physiology/plugin.toml").open("rb") as stream:
        manifest = tomllib.load(stream)
    assert manifest["plugin"]["id"] == "science_physiology"
    assert manifest["plugin"]["version"] == VERSION
    assert manifest["scientific"]["family"] == "biology"
    assert manifest["scopes"]["grants"] == ["graph.read", "graph.mutate"]
    assert manifest["persistence"]["passive_node_kinds"] == ["workbench"]
    assert PACKS[PACK][0] == "Physiology"
    assert PACK_FAMILIES[PACK] == "biology"
    assert tuple(op.id for op in operations(PACK)) == OP_IDS
    reference = (root / "docs/biology/physiology.md").read_text(encoding="utf-8")
    assert "science_physiology" in reference
    for op in operations(PACK):
        assert all(field.unit for field in op.fields)
        assert op.methods and op.citations
        assert op.id in reference
        before = json.dumps(op.example)
        first = run_operation(PACK, op, op.example)
        second = run_operation(PACK, op, op.example)
        assert before == json.dumps(op.example)
        assert first["values"] == second["values"]
        assert first["summary"] and first["values"]
        assert first["provenance"]["packVersion"] == VERSION
        assert set(first["inputs"]) == set(op.example)
        assert readable_result({"version": 1, "latest": first}, PACK)
        json.dumps(first, allow_nan=False)


def test_nernst_potassium_reference_and_charge_direction():
    # Standard Nernst magnitude: ~-89.06 mV at 37 C, [K]i=140 mM, [K]o=5 mM.
    result = run("nernst_potential")
    expected = 1000 * 8.31446261815324 * 310.15 / 96485.33212 * math.log(5 / 140)
    assert result["values"]["equilibrium_potential_mv"] == pytest.approx(expected, rel=1e-13)
    assert result["values"]["equilibrium_potential_mv"] == pytest.approx(-89.05869404, abs=1e-7)
    assert run("nernst_potential", valence=-1)["values"]["equilibrium_potential_mv"] == pytest.approx(-expected)
    assert run("nernst_potential", inside_mm=10, outside_mm=10)["values"]["equilibrium_potential_mv"] == 0
    assert run("nernst_potential", valence=2)["values"]["equilibrium_potential_mv"] == pytest.approx(expected / 2)


def test_nernst_unit_conversion_and_temperature():
    base = run("nernst_potential")
    converted = run("nernst_potential", inside_mm="0.14 M", outside_mm="5000 uM", temperature_k="37 °C")
    assert converted["values"]["equilibrium_potential_mv"] == pytest.approx(
        base["values"]["equilibrium_potential_mv"], rel=1e-13
    )
    cold = run("nernst_potential", temperature_k=300)["values"]["equilibrium_potential_mv"]
    assert cold / base["values"]["equilibrium_potential_mv"] == pytest.approx(300 / 310.15)


def test_ghk_reference_chloride_reversal_and_single_species_limits():
    result = run("ghk_potential")
    numerator = 5 + 0.04 * 145 + 0.45 * 10
    denominator = 140 + 0.04 * 12 + 0.45 * 110
    expected = 1000 * 8.31446261815324 * 310.15 / 96485.33212 * math.log(
        numerator / denominator
    )
    assert result["values"]["membrane_potential_mv"] == pytest.approx(expected, rel=1e-13)
    assert expected == pytest.approx(-67.3262176, abs=1e-7)
    assert result["table"]["rows"][2] == [ "Cl-", 0.45, 10, 110, 4.5, 49.5 ]
    k_only = run("ghk_potential", p_na=0, p_cl=0)["values"]["membrane_potential_mv"]
    nernst_k = run("nernst_potential")["values"]["equilibrium_potential_mv"]
    assert k_only == pytest.approx(nernst_k, rel=1e-13)
    cl_only = run("ghk_potential", p_k=0, p_na=0)["values"]["membrane_potential_mv"]
    nernst_cl = run("nernst_potential", inside_mm=10, outside_mm=110, valence=-1)
    assert cl_only == pytest.approx(nernst_cl["values"]["equilibrium_potential_mv"], rel=1e-13)
    balanced = run("ghk_potential", k_outside_mm=140, na_outside_mm=12, cl_outside_mm=10)
    assert balanced["values"]["membrane_potential_mv"] == pytest.approx(0, abs=1e-13)


def test_ghk_permeability_scale_invariance_and_unit_conversion():
    first = run("ghk_potential")["values"]["membrane_potential_mv"]
    scaled = run("ghk_potential", p_k=100, p_na=4, p_cl=45)
    assert scaled["values"]["membrane_potential_mv"] == pytest.approx(first)
    converted = run("ghk_potential", k_inside_mm="0.14 M", temperature_k="37 °C")
    assert converted["values"]["membrane_potential_mv"] == pytest.approx(first, rel=1e-13)


def test_passive_membrane_rc_reference_step_and_current_polarity():
    result = run("membrane_properties")
    v = result["values"]
    assert v["input_resistance_mohm"] == pytest.approx(1)
    assert v["capacitance_nf"] == pytest.approx(10)
    assert v["time_constant_ms"] == pytest.approx(10)
    assert v["steady_state_delta_mv"] == pytest.approx(0.1)
    assert result["table"]["rows"][0] == [0, -65, 0]
    assert result["table"]["rows"][2][1] == pytest.approx(-65 + 0.1 * (1 - 1 / math.e))
    assert result["values"]["final_voltage_mv"] == pytest.approx(-64.9018315639)
    negative = run("membrane_properties", current_na=-0.1)
    assert negative["values"]["steady_state_delta_mv"] == pytest.approx(-0.1)
    assert negative["plot"]["y"][2] < -65


def test_passive_membrane_area_scaling_and_small_time():
    # Doubling area doubles C but halves R, preserving the specific RC tau.
    double = run("membrane_properties", area_cm2=0.02)
    assert double["values"]["capacitance_nf"] == pytest.approx(20)
    assert double["values"]["input_resistance_mohm"] == pytest.approx(0.5)
    assert double["values"]["time_constant_ms"] == pytest.approx(10)
    tiny = run("membrane_properties", time_ms=[0, 1e-9])
    assert tiny["table"]["rows"][1][2] == pytest.approx(0.1e-10, rel=1e-12)


def test_hh_removable_rate_singularities_are_finite():
    am, _, _, _, _, _ = _hh_rates(-40)
    *_, an, _ = _hh_rates(-55)
    assert am == pytest.approx(1)
    assert an == pytest.approx(0.1)
    for v in (-100, -65, -55, -40, 0, 80):
        assert all(math.isfinite(rate) and rate > 0 for rate in _hh_rates(v))


def test_hh_known_classical_spike_and_deterministic_bounds():
    result = run("hodgkin_huxley")
    v = result["values"]
    assert v["integration_steps"] == 5000
    assert v["integration_step_ms"] == pytest.approx(0.01)
    assert v["peak_voltage_mv"] == pytest.approx(40.26307, abs=0.03)
    assert v["peak_time_ms"] == pytest.approx(7.14, abs=0.05)
    assert v["zero_mv_upcrossings"] == 2
    assert v["reference_temperature_c"] == 6.3
    table = result["table"]
    assert len(table["columns"]) == 8
    assert 1 < len(table["rows"]) <= 500
    assert len(table["rows"]) * 8 <= 4096
    assert table["rows"][0][0] == 0
    assert table["rows"][-1][0] == pytest.approx(50)
    assert all(a[0] < b[0] for a, b in zip(table["rows"], table["rows"][1:]))
    assert all(0 <= row[j] <= 1 for row in table["rows"] for j in (2, 3, 4))
    assert result["plot"]["x"] == [row[0] for row in table["rows"]]
    assert result["plot"]["y"] == [row[1] for row in table["rows"]]


def test_hh_no_stimulus_stays_near_rest_and_step_refinement():
    no_pulse = run("hodgkin_huxley", current_ua_cm2=0, duration_ms=20,
                   stimulus_start_ms=2, stimulus_stop_ms=10)
    assert no_pulse["values"]["zero_mv_upcrossings"] == 0
    assert no_pulse["values"]["final_voltage_mv"] == pytest.approx(-65, abs=0.02)
    fine = run("hodgkin_huxley", time_step_ms=0.005, duration_ms=40)
    coarse = run("hodgkin_huxley", time_step_ms=0.02, duration_ms=40)
    assert fine["values"]["peak_voltage_mv"] == pytest.approx(
        coarse["values"]["peak_voltage_mv"], abs=0.05
    )


def test_spike_detector_crossings_interpolated_peaks_refractory_and_no_spikes():
    result = run("spike_detection")
    rows = result["table"]["rows"]
    assert result["values"]["spike_count"] == 2
    assert rows[0] == pytest.approx([1, 1.5, 2, 30])
    assert rows[1] == pytest.approx([2, 5 + 25 / 60, 6, 35])
    assert result["values"]["mean_interspike_interval_ms"] == pytest.approx(5 + 25 / 60 - 1.5)
    assert result["values"]["window_firing_rate_hz"] == pytest.approx(200)
    filtered = run("spike_detection", refractory_ms=4)
    assert filtered["values"]["spike_count"] == 1
    assert len(filtered["table"]["rows"]) == 1
    empty = run("spike_detection", voltage_mv=[-70] * 11)
    assert empty["values"]["spike_count"] == 0
    assert empty["values"]["window_firing_rate_hz"] == 0
    assert "undefined" in empty["values"]["mean_interspike_interval_ms"]
    assert empty["table"]["rows"] == []


def test_spike_detector_initial_above_threshold_and_supthreshold_peak():
    result = run("spike_detection", time_ms=[0, 1, 2, 3, 4, 5],
                 voltage_mv=[20, 30, -20, 5, 15, -25], threshold_mv=0,
                 refractory_ms=0)
    assert result["values"]["spike_count"] == 1
    assert result["table"]["rows"][0][1:] == pytest.approx([2.8, 4, 15])


def test_hh_voltage_plot_can_feed_independent_spike_detection():
    hh = run("hodgkin_huxley")
    spikes = run("spike_detection", time_ms=hh["plot"]["x"],
                 voltage_mv=hh["plot"]["y"], threshold_mv=0, refractory_ms=2)
    assert spikes["values"]["spike_count"] == hh["values"]["zero_mv_upcrossings"]


def test_fick_steady_flux_integrated_amount_direction_and_zero_gradient():
    result = run("fick_diffusion")
    v = result["values"]
    assert v["gradient_mol_m4"] == pytest.approx(-2000)
    assert v["flux_mol_m2_s"] == pytest.approx(2e-6)
    assert v["transfer_rate_mol_s"] == pytest.approx(4e-10)
    assert v["net_transfer_mol"] == pytest.approx(4e-8)
    assert v["direction"] == "left to right"
    assert result["table"]["rows"][4] == pytest.approx([0.0005, 2])
    reverse = run("fick_diffusion", left_mol_m3=1, right_mol_m3=3)
    assert reverse["values"]["net_transfer_mol"] == pytest.approx(-4e-8)
    assert reverse["values"]["direction"] == "right to left"
    zero = run("fick_diffusion", left_mol_m3=1, right_mol_m3=1)
    assert zero["values"]["net_transfer_mol"] == 0
    assert zero["values"]["direction"] == "no net flux"
    half_thickness = run("fick_diffusion", thickness_m=0.0005)
    assert half_thickness["values"]["flux_mol_m2_s"] == pytest.approx(4e-6)


@pytest.mark.parametrize(("operation_id", "changes", "error"), [
    ("nernst_potential", {"inside_mm": 0}, "greater than zero"),
    ("nernst_potential", {"outside_mm": 0}, "greater than zero"),
    ("nernst_potential", {"valence": 0}, "valence"),
    ("nernst_potential", {"valence": 4}, "valence"),
    ("nernst_potential", {"temperature_k": 0}, "greater than zero"),
    ("ghk_potential", {"p_na": -0.1}, "nonnegative"),
    ("ghk_potential", {"k_inside_mm": -1}, "nonnegative"),
    ("ghk_potential", {"p_k": 0, "p_na": 0, "p_cl": 0}, "permeability"),
    ("ghk_potential", {"k_outside_mm": 0, "na_outside_mm": 0,
                       "cl_inside_mm": 0}, "both be positive"),
    ("membrane_properties", {"area_cm2": 0}, "greater than zero"),
    ("membrane_properties", {"time_ms": [0, 10, 10]}, "strictly increasing"),
    ("membrane_properties", {"time_ms": [0]}, "two sample times"),
    ("hodgkin_huxley", {"duration_ms": 201}, "1–200"),
    ("hodgkin_huxley", {"time_step_ms": 0.001}, "0.005"),
    ("hodgkin_huxley", {"duration_ms": 200, "time_step_ms": 0.005}, "20,000"),
    ("hodgkin_huxley", {"stimulus_stop_ms": 60}, "Stimulus"),
    ("hodgkin_huxley", {"current_ua_cm2": 101}, "HH current"),
    ("hodgkin_huxley", {"initial_voltage_mv": -200}, "initial voltage"),
    ("spike_detection", {"voltage_mv": [1, 2]}, "aligned"),
    ("spike_detection", {"time_ms": [0, 2, 2]}, "aligned|strictly increasing"),
    ("spike_detection", {"refractory_ms": -1}, "nonnegative"),
    ("fick_diffusion", {"diffusivity_m2_s": 0}, "greater than zero"),
    ("fick_diffusion", {"thickness_m": 0}, "greater than zero"),
    ("fick_diffusion", {"left_mol_m3": -1}, "nonnegative"),
])
def test_model_invalid_inputs_are_rejected(operation_id, changes, error):
    with pytest.raises(ValueError, match=error):
        run(operation_id, **changes)


def test_nonfinite_numeric_inputs_are_rejected_at_workbench_boundary():
    with pytest.raises(ValueError, match="finite"):
        run("ghk_potential", p_na=float("nan"))
    with pytest.raises(ValueError, match="finite"):
        run("hodgkin_huxley", current_ua_cm2=float("inf"))
