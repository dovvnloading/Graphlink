"""Independent equation oracles and adversarial cases for five Biology Workbench packs.

Checks use analytic identities rather than reusing pack example outputs.  The
near-equality tests probe representable IEEE-754 measurements; they do not
claim that such differences are measurable in a wet laboratory.
"""

import math

import pytest

from graphlink_plugins.scientific.catalog import operations
from graphlink_plugins.scientific.core import run_operation
from graphlink_plugins.scientific.packs.enzyme_advanced import _morrison_prediction
from graphlink_plugins.scientific.packs.pharmacokinetics import _interval_integrals
from graphlink_plugins.scientific.packs.plate_assays_molecular import qpcr_relative_expression


def run(pack, operation_id, **updates):
    operation = next(item for item in operations(pack) if item.id == operation_id)
    return run_operation(pack, operation, {**operation.example, **updates})["values"]


@pytest.mark.parametrize("ki", [1e-16, 1e-32])
def test_enzyme_morrison_ultratight_equimolar_quadratic(ki):
    # E=I=1. The naive discriminant (2+Ki)^2-4 rounds to zero;
    # subtracting bound enzyme from one also loses the small free fraction.
    response, derivatives = _morrison_prediction(1.0, (0.0, 0.0, math.log(ki)))
    # At E=I=1, free enzyme is (sqrt(Ki^2+4Ki)-Ki)/2.
    expected = (math.sqrt(ki * (ki + 4)) - ki) / 2
    assert response == pytest.approx(expected, rel=2e-7, abs=0)
    assert all(math.isfinite(value) for value in derivatives)
    assert derivatives[2] > 0


@pytest.mark.parametrize("model,asymmetry", [("4pl", 1.0), ("5pl", 0.7)])
def test_plate_logistic_half_response_is_a_half_of_asymptotes(model, asymmetry):
    # Independent manufactured asymmetrical calibration, not the pack fixture.
    a, d, scale, slope = 0.15, 2.1, 1.7, 1.3
    x = [0.03, 0.06, 0.12, 0.25, 0.5, 1, 2, 4, 8, 16, 32, 64]

    def observed(concentration):
        return d + (a - d) / (1 + (concentration / scale) ** slope) ** asymmetry

    result = run("plate_assays", "standard_curve",
                 model=model, standard_concentrations=x,
                 standard_signals=[observed(v) for v in x],
                 unknown_signals=[observed(2.6)], dilution_factors=[5],
                 concentration_unit="ng/mL", signal_unit="RFU")
    target = scale * math.expm1(math.log(2) / asymmetry) ** (1 / slope)
    half = result["half_response_concentration"]
    assert result["c"] == pytest.approx(scale, rel=2e-2)
    assert half == pytest.approx(target, rel=2e-2)
    assert observed(half) == pytest.approx((a + d) / 2, rel=2e-2)
    assert result["half_response_concentration_unit"] == "ng/mL"
    assert result["half_response_status"] == "within measured concentration interval"
    assert half == pytest.approx(result["c"], rel=2e-2) if model == "4pl" else abs(half - result["c"]) > 0.3


def test_qpcr_expression_requires_at_least_one_reference_gene_when_called_directly():
    operation = next(item for item in operations("plate_assays")
                     if item.id == "qpcr_relative_expression")
    with pytest.raises(ValueError, match="reference gene"):
        qpcr_relative_expression({
            **operation.example, "reference_cts_sample": [],
            "reference_cts_calibrator": [], "reference_efficiencies": [],
        })


def test_cell_culture_doubling_time_preserves_small_positive_log_growth():
    before = 1e200
    after = math.nextafter(before, math.inf)
    relative_increment = (after - before) / before
    assert math.log(after) == math.log(before)  # Old formula loses the signal.
    result = run("cell_culture", "doubling_time", initial_abundance=before,
                 final_abundance=after, elapsed_time=1, time_unit="h",
                 abundance_unit="cells/mL")
    expected = math.log1p(relative_increment)
    assert result["specific_growth_rate"] == pytest.approx(expected, rel=1e-12, abs=0)
    assert result["doubling_time"] == pytest.approx(math.log(2) / expected, rel=1e-12)


def test_pk_logdown_auc_handles_adjacent_positive_floats():
    first = 1e200
    second = math.nextafter(first, 0.0)
    assert math.log(first) == math.log(second)  # Naive q=log(C0)-log(C1)=0.
    auc, aumc = _interval_integrals(0, 1, first, second)
    assert auc == pytest.approx(first, rel=1e-12)
    assert aumc == pytest.approx(first / 2, rel=1e-12)


def test_pk_terminal_log_slope_preserved_for_shallow_measurable_decay():
    rate = 1e-14
    times = list(range(8))
    concentrations = [1e100 * math.exp(-rate * t) for t in times]
    result = run("pharmacokinetics", "noncompartmental_analysis",
                 time=times, concentration=concentrations, dose=100,
                 route="iv_bolus", terminal_points=4, time_unit="h",
                 response_unit="mg/L")
    assert result["lambda_z"] == pytest.approx(rate, rel=0.02)
    assert result["auc_inf"] > result["auc_last"] > 0
    assert result["terminal_half_life"] == pytest.approx(math.log(2) / rate, rel=0.02)


def test_physiology_nernst_preserves_adjacent_concentrations():
    inside = 100.0
    outside = math.nextafter(inside, math.inf)
    assert math.log(outside) == math.log(inside)
    result = run("physiology", "nernst_potential", inside_mm=inside,
                 outside_mm=outside, temperature_k=310.15, valence=1)
    expected = (1000 * 8.31446261815324 * 310.15 / 96485.33212
                * math.log1p((outside - inside) / inside))
    assert result["equilibrium_potential_mv"] > 0
    assert result["equilibrium_potential_mv"] == pytest.approx(expected, rel=1e-12, abs=0)


def test_physiology_ghk_single_cation_equals_nernst_at_small_gradient():
    inside, outside = 100.0, math.nextafter(100.0, math.inf)
    actual = run("physiology", "ghk_potential", k_inside_mm=inside,
                 k_outside_mm=outside, p_k=1, p_na=0, p_cl=0,
                 temperature_k=310.15)
    nernst = run("physiology", "nernst_potential", inside_mm=inside,
                 outside_mm=outside, valence=1, temperature_k=310.15)
    assert actual["membrane_potential_mv"] > 0
    assert actual["membrane_potential_mv"] == pytest.approx(
        nernst["equilibrium_potential_mv"], rel=1e-12, abs=0)


def test_five_pack_independent_ordinary_formula_cross_checks():
    # Cheng-Prusoff competitive Ki, Z-prime, viability, IV accumulation, Fick.
    enzyme = run("enzyme_kinetics", "cheng_prusoff_ki",
                 ic50=18, substrate_concentration=8, km=4)
    assert enzyme["ki"] == pytest.approx(6)
    assay = run("plate_assays", "assay_quality",
                positive_controls=[100, 100], negative_controls=[10, 10])
    assert assay["z_prime_factor"] == pytest.approx(1)
    culture = run("cell_culture", "cell_count_viability",
                  live_counts=[8, 12], dead_counts=[2, 3],
                  dilution_factor=2, counted_volume_ul=0.1, sample_volume_ml=1)
    assert culture["viability_percent"] == pytest.approx(80)
    pk = run("pharmacokinetics", "multiple_dose_prediction",
             dose=100, central_volume=10, elimination_rate=0.2,
             dosing_interval=8, number_of_doses=5)
    assert pk["steady_state_average"] == pytest.approx(100 / (10 * 0.2 * 8))
    physiology = run("physiology", "fick_diffusion",
                     diffusivity_m2_s=1e-9, thickness_m=1e-3,
                     area_m2=2e-4, left_mol_m3=3, right_mol_m3=1,
                     duration_s=100)
    assert physiology["net_transfer_mol"] == pytest.approx(
        1e-9 * (3 - 1) / 1e-3 * 2e-4 * 100)
