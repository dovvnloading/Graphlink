"""Published-value, analytical, unit and numerical-domain tests for Materials Science.

The scientific core validates all input kinds and result artifacts; these tests
exercise the public Operation interface rather than bypassing its validator.
"""

import math

import pytest

from graphlink_plugins.scientific.core import Operation, run_operation
from graphlink_plugins.scientific.packs import materials


OPS = {operation.id: operation for operation in materials.OPERATIONS}


def calculate(name, **changes):
    operation = OPS[name]
    return run_operation("materials", operation, {**operation.example, **changes})


def values(name, **changes):
    return calculate(name, **changes)["values"]


def test_all_operation_examples_serialize_and_use_expected_shapes():
    assert len(OPS) == len(materials.OPERATIONS) == 15
    assert all(isinstance(op, Operation) for op in materials.OPERATIONS)
    assert materials.__annotations__["OPERATIONS"] == tuple[Operation, ...]
    for op in materials.OPERATIONS:
        assert set(op.example) == {field.key for field in op.fields}, op.id
        assert all(field.unit for field in op.fields), op.id
        artifact = calculate(op.id)
        assert isinstance(artifact["summary"], str) and artifact["values"], op.id
        assert all(math.isfinite(v) for v in artifact["values"].values() if not isinstance(v, str)), op.id
        if "plot" in artifact:
            assert len(artifact["plot"]["x"]) == len(artifact["plot"]["y"])
            assert len(artifact["plot"]["x"]) > 0


def test_existing_operations_are_unchanged():
    assert [op.id for op in materials.OPERATIONS[:3]] == [
        "bulk_density", "tensile_response", "apparent_porosity",
    ]
    assert OPS["bulk_density"].example == {"dry_mass_g": 37.5, "bulk_volume_cm3": 15}
    assert OPS["tensile_response"].example == {
        "force_n": [0, 100, 180, 160], "extension_mm": [0, 0.25, 0.6, 1],
        "original_area_mm2": 5, "gauge_length_mm": 50,
    }
    assert OPS["apparent_porosity"].example == {
        "dry_mass_g": 80, "saturated_mass_g": 86, "suspended_mass_g": 56,
    }
    assert values("bulk_density") == {"bulk_density_g_cm3": 2.5, "bulk_density_kg_m3": 2500}
    assert values("tensile_response") == {
        "peak_measured_stress_mpa": 36, "engineering_strain_at_first_peak": 0.012,
    }
    assert values("apparent_porosity") == {"apparent_open_porosity_percent": 20,
                                             "absorbed_liquid_g": 6}


def test_new_scalar_outputs_express_units_in_their_keys():
    suffixes = ("_mpa", "_gpa", "_m", "_mm", "_mm_mm", "_m2_s", "_mj_m3",
                "_percent", "_wt_percent", "_cycles", "_dimensionless", "_mpa_sqrt_m",
                "_hrc", "_hrb", "_hv", "_hbw", "_hbs")
    for op in materials.OPERATIONS[3:]:
        for key in calculate(op.id)["values"]:
            assert key.endswith(suffixes), (op.id, key)


def test_curve_fit_yield_uts_break_and_integrated_energies():
    result = calculate("stress_strain_analysis")
    v = result["values"]
    assert v["young_modulus_mpa"] == pytest.approx(200000, rel=1e-13)
    assert v["elastic_intercept_mpa"] == pytest.approx(0, abs=1e-10)
    assert v["elastic_fit_r_squared_dimensionless"] == pytest.approx(1)
    # Intersection of (0.004,455) -> (0.006,475) with σ=E(ε-0.002).
    assert v["offset_yield_strain_mm_mm"] == pytest.approx(0.004289473684210526)
    assert v["offset_yield_strength_mpa"] == pytest.approx(457.89473684210526)
    assert v["ultimate_tensile_strength_mpa"] == 500
    assert v["elongation_at_break_percent"] == pytest.approx(14)
    assert v["resilience_to_elastic_limit_mj_m3"] == pytest.approx(0.4)
    rows = OPS["stress_strain_analysis"].example["engineering_curve"]
    expected_area = sum((b[0]-a[0]) * (a[1]+b[1])/2 for a,b in zip(rows,rows[1:]))
    assert v["toughness_mj_m3"] == pytest.approx(expected_area)
    assert len(result["table"]["rows"]) == len(rows)


def test_offset_intersection_uses_fitted_elastic_intercept():
    rows = [list(row) for row in OPS["stress_strain_analysis"].example["engineering_curve"]]
    # A nonzero extensometer/fit intercept should translate the proof line,
    # not silently force that proof line through (0.002, 0).
    for row in rows[1:5]:
        row[1] += 8
    v = values("stress_strain_analysis", engineering_curve=rows)
    crossing = v["offset_yield_strain_mm_mm"]
    yield_stress = v["offset_yield_strength_mpa"]
    assert yield_stress == pytest.approx(
        v["young_modulus_mpa"] * (crossing - 0.002) + v["elastic_intercept_mpa"],
        abs=1e-10)


def test_elastic_resilience_partial_sample_interpolation():
    v = values("stress_strain_analysis", elastic_limit_strain_mm_mm=0.00125)
    assert v["resilience_to_elastic_limit_mj_m3"] == pytest.approx(0.15625)
    assert v["young_modulus_mpa"] == pytest.approx(200000)


@pytest.mark.parametrize("changes", [
    {"engineering_curve": [[0, 0], [0.001, 100]]},
    {"engineering_curve": [[0, 0, 1]] * 5},
    {"engineering_curve": [[0, 0], [0.001, 100], [0.001, 100], [0.004, 200], [0.1, 40]]},
    {"engineering_curve": [[0, 1], [0.001, 100], [0.002, 200], [0.01, 300], [0.1, 40]]},
    {"engineering_curve": [[0, 0], [0.001, 100], [0.002, 200], [0.003, 300], [0.004, 400]]},
    {"elastic_limit_strain_mm_mm": 0},
    {"elastic_limit_strain_mm_mm": 10},
])
def test_curve_rejects_invalid_tables_or_no_offset_crossing(changes):
    with pytest.raises(ValueError):
        calculate("stress_strain_analysis", **changes)


def test_pre_necking_true_conversion_and_inverse_roundtrip():
    forward = calculate("true_stress_strain")
    assert forward["values"]["last_true_strain_mm_mm"] == pytest.approx(math.log(1.1))
    assert forward["values"]["last_true_stress_mpa"] == pytest.approx(352)
    original = OPS["true_stress_strain"].example["uniform_curve"]
    reversed_rows = [[row[2],row[3]] for row in forward["table"]["rows"]]
    backward = calculate("true_stress_strain", uniform_curve=reversed_rows, direction="true_to_engineering")
    for recovered, expected in zip(backward["table"]["rows"], original):
        assert recovered[:2] == pytest.approx(expected, abs=1e-12)


@pytest.mark.parametrize("direction,rows", [
    ("unknown", [[0, 0], [0.1, 100]]),
    ("engineering_to_true", [[0, 0], [0, 100]]),
    ("true_to_engineering", [[0.1, 20], [0.0, 50]]),
    ("engineering_to_true", [[0, 0], [0.2, -1]]),
])
def test_true_conversion_rejects_invalid_direction_or_curve(direction, rows):
    with pytest.raises(ValueError):
        calculate("true_stress_strain", direction=direction, uniform_curve=rows)


def test_hollomon_exact_power_law_recovers_parameters():
    rows = [[p, 820 * p ** .32] for p in (0.005, .01, .02, .04, .08)]
    v = values("hollomon_fit", true_plastic_curve=rows)
    assert v["strength_coefficient_k_mpa"] == pytest.approx(820, rel=1e-13)
    assert v["strain_hardening_exponent_n_dimensionless"] == pytest.approx(.32, abs=1e-13)
    assert v["log_log_r_squared_dimensionless"] == pytest.approx(1)


def test_ramberg_osgood_exact_log_fit_recovers_parameters():
    e_mod = 210000
    rows = [[sigma / e_mod + (sigma / 950) ** (1 / .3), sigma]
            for sigma in (300, 350, 400, 450, 500)]
    v = values("ramberg_osgood_fit", total_strain_stress_curve=rows, young_modulus_mpa=e_mod)
    assert v["strength_coefficient_k_mpa"] == pytest.approx(950, rel=1e-12)
    assert v["ramberg_osgood_n_dimensionless"] == pytest.approx(.3, abs=1e-12)
    assert v["log_log_r_squared_dimensionless"] == pytest.approx(1)


@pytest.mark.parametrize("name,changes", [
    ("hollomon_fit", {"true_plastic_curve": [[0,100],[.01,200],[.1,300]]}),
    ("hollomon_fit", {"true_plastic_curve": [[.01,300],[.02,200],[.04,100]]}),
    ("ramberg_osgood_fit", {"young_modulus_mpa": 0}),
    ("ramberg_osgood_fit", {"total_strain_stress_curve": [[.001,200],[.002,300],[.003,400]]}),
    ("ramberg_osgood_fit", {"total_strain_stress_curve": [[.01,300],[.01,350],[.02,400]]}),
])
def test_hardening_rejects_out_of_domain_data(name, changes):
    with pytest.raises(ValueError):
        calculate(name, **changes)


def test_basquin_and_coffin_manson_reproduce_analytically_generated_life():
    # Fully reversed strain-life equation in 2N reversals (Dowling convention).
    target_n = 100000
    reversals = 2 * target_n
    sigma_f, elastic_modulus, b, epsilon_f, c = 1000, 200000, -.1, .5, -.5
    stress_amp = sigma_f * reversals ** b
    strain_amp = sigma_f / elastic_modulus * reversals ** b + epsilon_f * reversals ** c
    v = values("fatigue_life", stress_amplitude_mpa=stress_amp,
               total_strain_amplitude_mm_mm=strain_amp, mean_stress_mpa=0,
               mean_stress_correction="none")
    assert v["basquin_fatigue_life_cycles"] == pytest.approx(target_n, rel=1e-12)
    assert v["basquin_coffin_manson_fatigue_life_cycles"] == pytest.approx(target_n, rel=1e-12)


@pytest.mark.parametrize("method,expected_factor", [
    ("none", 1), ("goodman", .9), ("gerber", .99),
    ("soderberg", 1-100/700),
])
def test_goodman_gerber_soderberg_mean_stress(method, expected_factor):
    v = values("fatigue_life", mean_stress_mpa=100, mean_stress_correction=method)
    assert v["mean_stress_factor_dimensionless"] == pytest.approx(expected_factor)
    assert v["corrected_alternating_stress_mpa"] == pytest.approx(400 / expected_factor)
    if method != "none":
        baseline = values("fatigue_life", mean_stress_correction="none")
        assert v["basquin_fatigue_life_cycles"] < baseline["basquin_fatigue_life_cycles"]
        assert v["basquin_coffin_manson_fatigue_life_cycles"] < baseline["basquin_coffin_manson_fatigue_life_cycles"]


def test_negative_mean_stress_is_not_given_an_unvalidated_life_credit():
    v = values("fatigue_life", mean_stress_mpa=-200, mean_stress_correction="goodman")
    assert v["mean_stress_factor_dimensionless"] == 1


@pytest.mark.parametrize("changes", [
    {"mean_stress_correction":"swt"}, {"mean_stress_correction":"goodman", "mean_stress_mpa":1001},
    {"basquin_exponent_b":0}, {"coffin_manson_exponent_c":.5},
    {"stress_amplitude_mpa":0}, {"total_strain_amplitude_mm_mm":0},
    {"fatigue_strength_coefficient_mpa":-1},
])
def test_fatigue_refuses_invalid_models(changes):
    with pytest.raises(ValueError):
        calculate("fatigue_life", **changes)


def test_stress_intensity_for_infinite_center_edge_and_finite_geometries():
    v = values("fracture_stress_intensity", applied_tensile_stress_mpa=300,
               crack_length_a_m=.002, geometry="infinite_center")
    k = 300 * math.sqrt(math.pi * .002)
    assert v["geometry_factor_y_dimensionless"] == 1
    assert v["mode_i_stress_intensity_mpa_sqrt_m"] == pytest.approx(k)
    edge = values("fracture_stress_intensity", applied_tensile_stress_mpa=300,
                  crack_length_a_m=.002, geometry="semi_infinite_edge")
    assert edge["mode_i_stress_intensity_mpa_sqrt_m"] == pytest.approx(1.12 * k)
    finite = values("fracture_stress_intensity", applied_tensile_stress_mpa=300,
                    crack_length_a_m=.002, plate_width_m=.04, geometry="finite_center")
    assert finite["geometry_factor_y_dimensionless"] == pytest.approx(
        1/math.sqrt(math.cos(math.pi * .002 / .04)))
    assert values("fracture_stress_intensity", geometry="finite_edge")[
        "geometry_factor_y_dimensionless"] > 1.12


def test_callister_glass_critical_flaw_example_8_1():
    # Callister Example 8.1: E=69 GPa, surface energy 0.3 J/m², σ=40 MPa,
    # a_critical=2 E γ/(π σ²) = 8.2 μm (two significant digits).
    fracture_toughness = math.sqrt(2 * 69e9 * 0.3) / 1e6
    v = values("fracture_critical_length", fracture_toughness_mpa_sqrt_m=fracture_toughness,
               applied_tensile_stress_mpa=40, constant_geometry_factor_y=1)
    assert v["critical_crack_length_a_m"] == pytest.approx(8.2e-6, abs=0.1e-6)
    assert v["full_center_crack_length_2a_m"] == pytest.approx(
        2 * v["critical_crack_length_a_m"])


@pytest.mark.parametrize("name,changes", [
    ("fracture_stress_intensity", {"geometry":"unknown"}),
    ("fracture_stress_intensity", {"geometry":"finite_center", "crack_length_a_m":.05,
                                  "plate_width_m":.1}),
    ("fracture_stress_intensity", {"geometry":"finite_edge", "crack_length_a_m":.07,
                                  "plate_width_m":.1}),
    ("fracture_critical_length", {"constant_geometry_factor_y":0}),
])
def test_fracture_geometry_and_material_bounds(name, changes):
    with pytest.raises(ValueError):
        calculate(name, **changes)


def test_paris_crack_growth_analytic_integral_m3_and_m2():
    # Y=1, Δσ=100 MPa, a0=.001 m, af=.005 m, C=1e-12,
    # m=3: N=(2/s)[a0^-1/2-af^-1/2] with s=C(Δσ√π)^3.
    v = values("paris_crack_growth")
    expected = 2 * (.001 ** (-.5) - .005 ** (-.5)) / (1e-12 * (100 * math.sqrt(math.pi)) ** 3)
    assert v["paris_growth_cycles"] == pytest.approx(expected, rel=1e-12)
    c2 = values("paris_crack_growth", paris_exponent_m=2)
    expected2 = math.log(.005 / .001) / (1e-12 * (100 * math.sqrt(math.pi)) ** 2)
    assert c2["paris_growth_cycles"] == pytest.approx(expected2, rel=1e-12)
    nearby = values("paris_crack_growth", paris_exponent_m=2+1e-10)
    assert nearby["paris_growth_cycles"] == pytest.approx(expected2, rel=1e-9)


@pytest.mark.parametrize("changes", [
    {"final_crack_a_m":.001}, {"initial_crack_a_m":0},
    {"paris_c_m_cycle_inv_mpa_sqrt_m_neg_m":0},
    {"paris_exponent_m":0}, {"load_ratio_r":1},
    {"load_ratio_r":-.2}, {"fracture_toughness_mpa_sqrt_m":1},
])
def test_paris_rejects_bad_crack_parameters(changes):
    with pytest.raises(ValueError):
        calculate("paris_crack_growth", **changes)


def test_thermal_expansion_and_fixed_end_compression():
    v = values("thermal_expansion_stress")
    assert v["free_length_change_mm"] == pytest.approx(.6)
    assert v["free_thermal_strain_mm_mm"] == pytest.approx(.0006)
    assert v["restrained_axial_stress_mpa"] == pytest.approx(-120)
    assert v["actual_length_change_mm"] == 0
    free = values("thermal_expansion_stress", axial_restraint_fraction=0)
    assert free["restrained_axial_stress_mpa"] == 0
    assert free["actual_length_change_mm"] == pytest.approx(.6)
    cooled = values("thermal_expansion_stress", temperature_change_k=-50)
    assert cooled["restrained_axial_stress_mpa"] == pytest.approx(120)
    negative_cte = values("thermal_expansion_stress", linear_expansion_per_k=-2.5e-6)
    assert negative_cte["restrained_axial_stress_mpa"] == pytest.approx(25)


@pytest.mark.parametrize("changes", [
    {"initial_length_mm":0}, {"young_modulus_mpa":0},
    {"axial_restraint_fraction":-0.1}, {"axial_restraint_fraction":1.1},
])
def test_thermal_rejects_bad_material_parameters(changes):
    with pytest.raises(ValueError):
        calculate("thermal_expansion_stress", **changes)


def test_rule_of_mixtures_and_halpin_tsai_published_example():
    # Carbon/epoxy E_f=230 GPa, E_m=3.5 GPa, V_f=.6, ξ=2; ~17.61 GPa
    # (Halpin–Tsai engineering worked example; equations in reference doc).
    v = values("composite_moduli", fiber_transverse_modulus_gpa=230)
    assert v["longitudinal_rule_mixtures_modulus_gpa"] == pytest.approx(139.4)
    assert v["transverse_inverse_rule_modulus_gpa"] == pytest.approx(8.55472901, rel=1e-8)
    assert v["halpin_tsai_modulus_gpa"] == pytest.approx(17.61, abs=.01)
    anisotropic = values("composite_moduli")
    assert anisotropic["longitudinal_rule_mixtures_modulus_gpa"] == pytest.approx(139.4)
    assert anisotropic["halpin_tsai_modulus_gpa"] < 17.61
    equal = values("composite_moduli", fiber_axial_modulus_gpa=3.5,
                   fiber_transverse_modulus_gpa=3.5)
    assert equal["longitudinal_rule_mixtures_modulus_gpa"] == pytest.approx(3.5)
    assert equal["transverse_inverse_rule_modulus_gpa"] == pytest.approx(3.5)
    assert equal["halpin_tsai_modulus_gpa"] == pytest.approx(3.5)


@pytest.mark.parametrize("changes", [
    {"fiber_volume_fraction":-0.1}, {"fiber_volume_fraction":1.1},
    {"fiber_axial_modulus_gpa":0}, {"fiber_transverse_modulus_gpa":0},
    {"matrix_modulus_gpa":0}, {"halpin_tsai_shape_xi":0},
])
def test_composite_rejects_bad_parameters(changes):
    with pytest.raises(ValueError):
        calculate("composite_moduli", **changes)


def test_astm_e140_table_1_reference_steel_values_to_tabulated_digits():
    assert values("hardness_e140", from_scale="HRC", to_scale="HV", hardness_reading=38)[
        "converted_hardness_hv"] == 372
    assert values("hardness_e140", from_scale="HRC", to_scale="HBW", hardness_reading=38)[
        "converted_hardness_hbw"] == 353
    assert values("hardness_e140", from_scale="HBW", to_scale="HRC", hardness_reading=353)[
        "converted_hardness_hrc"] == 38
    assert values("hardness_e140", from_scale="HV", to_scale="HRC", hardness_reading=302)[
        "converted_hardness_hrc"] == 30
    assert values("hardness_e140", from_scale="HRC", to_scale="HV", hardness_reading=68)[
        "converted_hardness_hv"] == 940
    interpolated = values("hardness_e140", from_scale="HRC", to_scale="HBW", hardness_reading=38.5)
    assert interpolated["converted_hardness_hbw"] == 358  # E140 reporting precision


def test_astm_e140_table_2_rockwell_b_uses_hbs_not_hbw():
    other_table = "table2_non_austenitic_hrb"
    params = {"e140_table":other_table}
    assert values("hardness_e140", **params, from_scale="HRB", to_scale="HV",
                  hardness_reading=90)["converted_hardness_hv"] == 185
    assert values("hardness_e140", **params, from_scale="HRB", to_scale="HBS",
                  hardness_reading=100)["converted_hardness_hbs"] == 240
    assert values("hardness_e140", **params, from_scale="HBS", to_scale="HRB",
                  hardness_reading=150)["converted_hardness_hrb"] == 80


@pytest.mark.parametrize("changes", [
    {"e140_table":"other"},
    {"from_scale":"HRB", "to_scale":"HV"},
    {"from_scale":"HRC", "to_scale":"HRC"},
    {"from_scale":"HRC", "to_scale":"HBW", "hardness_reading":65},
    {"from_scale":"HRC", "to_scale":"HV", "hardness_reading":70},
    {"from_scale":"HV", "to_scale":"HRC", "hardness_reading":200},
    {"e140_table":"table2_non_austenitic_hrb", "from_scale":"HRB", "to_scale":"HBS",
     "hardness_reading":54},
    {"e140_table":"table2_non_austenitic_hrb", "from_scale":"HRB", "to_scale":"HBW"},
])
def test_astm_e140_refuses_wrong_material_table_scale_or_extrapolation(changes):
    with pytest.raises(ValueError):
        calculate("hardness_e140", **changes)


def test_callister_carburizing_example_0_8_wt_percent_at_0_5_mm():
    # Callister & Rethwisch, Ch. 5 (10e), Example: at 950 °C, D=1.6e-11
    # m²/s, C0=.25 wt%, Cs=1.20 wt%, t≈25,400 s gives C(.5 mm)=.80 wt%.
    temp = 1223.15
    d_target = 1.6e-11
    activation = 148  # kJ/mol; D0 is chosen to match the example's stated D.
    d0 = d_target * math.exp((activation * 1000)/(8.31446261815324*temp))
    result = calculate("arrhenius_diffusion_profile",
                       temperature_k=temp, diffusion_prefactor_m2_s=d0,
                       activation_energy_kj_mol=activation, elapsed_time_s=25400,
                       depth_mm=[0, .5, 1], initial_solute_wt_percent=.25,
                       surface_solute_wt_percent=1.2)
    assert result["values"]["diffusivity_m2_s"] == pytest.approx(d_target, rel=1e-12)
    assert result["table"]["rows"][0][1] == pytest.approx(1.2)
    assert result["table"]["rows"][1][1] == pytest.approx(.80, abs=.002)
    assert result["values"]["concentration_at_last_depth_wt_percent"] == pytest.approx(
        result["table"]["rows"][-1][1])


def test_diffusion_profile_matches_erfc_and_stable_far_field():
    v = calculate("arrhenius_diffusion_profile", depth_mm=[0, .25, 10000])
    assert v["table"]["rows"][0][1] == 1.2
    assert v["table"]["rows"][-1][1] == .25
    assert v["values"]["two_sqrt_dt_diffusion_length_mm"] > 0


@pytest.mark.parametrize("changes", [
    {"diffusion_prefactor_m2_s":0}, {"activation_energy_kj_mol":-1},
    {"temperature_k":0}, {"elapsed_time_s":0},
    {"initial_solute_wt_percent":-1}, {"surface_solute_wt_percent":101},
    {"depth_mm":[.5,.25]}, {"depth_mm":[.5,.5]}, {"depth_mm":[-.1]},
    {"temperature_k":1},
])
def test_diffusion_rejects_invalid_domain_or_underflow(changes):
    with pytest.raises(ValueError):
        calculate("arrhenius_diffusion_profile", **changes)


def test_shared_validation_rejects_nan_and_infinity():
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            calculate("thermal_expansion_stress", young_modulus_mpa=value)
        with pytest.raises(ValueError):
            calculate("arrhenius_diffusion_profile", depth_mm=[0, value])


# Independent audit regressions: previously accepted invalid fits or lost
# numerical/reporting precision at domain edges.


def test_elastic_fit_requires_three_points_to_establish_linearity():
    # Two points always produce R²=1, even if no elastic *region* exists.
    with pytest.raises(ValueError, match="at least three"):
        calculate("stress_strain_analysis", elastic_limit_strain_mm_mm=0.0005)


def test_elastic_fit_rejects_noisy_non_linear_selection_but_tolerates_small_scatter():
    rows = [list(row) for row in OPS["stress_strain_analysis"].example["engineering_curve"]]
    rows[2][1] = 20  # Huge stress reversal; an arbitrary positive OLS slope is not Young's modulus.
    with pytest.raises(ValueError, match="sufficiently linear"):
        calculate("stress_strain_analysis", engineering_curve=rows)

    close = [list(row) for row in OPS["stress_strain_analysis"].example["engineering_curve"]]
    for i, shift in ((1, 2), (2, -2), (3, 3), (4, 2)):
        close[i][1] += shift
    assert values("stress_strain_analysis", engineering_curve=close)["elastic_fit_r_squared_dimensionless"] > .98


def test_hollomon_rejects_data_without_a_power_law_trend():
    # Positive log-log slope is not sufficient: this scatter gave R²≈0.42.
    rows = [[p, s] for p, s in zip(
        (.01, .02, .04, .08, .16), (200, 240, 210, 290, 245),
    )]
    with pytest.raises(ValueError, match="power law"):
        calculate("hollomon_fit", true_plastic_curve=rows)


def test_paris_m2_integral_retains_precision_for_adjacent_crack_lengths():
    initial = .007
    final = math.nextafter(initial, math.inf)
    expected_log_span = math.log1p((final - initial) / initial)
    expected_cycles = expected_log_span / (1e-12 * (100 * math.sqrt(math.pi)) ** 2)
    actual = values("paris_crack_growth", initial_crack_a_m=initial,
                    final_crack_a_m=final, paris_exponent_m=2)["paris_growth_cycles"]
    assert actual == pytest.approx(expected_cycles, rel=1e-12)


def test_astm_e140_interpolated_results_do_not_claim_extra_precision():
    # The chosen E140 columns are integral; input readings remain unrounded.
    v = values("hardness_e140", from_scale="HRC", to_scale="HBW", hardness_reading=38.5)
    assert v["original_hardness_hrc"] == 38.5
    assert v["converted_hardness_hbw"] == 358
    inverse = values("hardness_e140", from_scale="HV", to_scale="HRC", hardness_reading=377)
    assert inverse["converted_hardness_hrc"] == 38  # 38.5 rounds to even.


def test_materials_thermal_composite_and_fracture_boundary_values():
    # Cooling reverses restrained-stress sign, while free restraint implies σ=0.
    assert values("thermal_expansion_stress", temperature_change_k=-50)[
        "restrained_axial_stress_mpa"] == pytest.approx(120)
    assert values("thermal_expansion_stress", axial_restraint_fraction=0)[
        "restrained_axial_stress_mpa"] == 0
    for vf, expected in ((0, 3.5), (1, 15)):
        result = values("composite_moduli", fiber_volume_fraction=vf)
        assert result["transverse_inverse_rule_modulus_gpa"] == pytest.approx(expected)
        assert result["halpin_tsai_modulus_gpa"] == pytest.approx(expected)
    for name, changed in (
        ("fracture_stress_intensity", {"applied_tensile_stress_mpa": 0}),
        ("fracture_critical_length", {"applied_tensile_stress_mpa": -2}),
        ("paris_crack_growth", {"stress_range_mpa": 0}),
    ):
        with pytest.raises(ValueError):
            calculate(name, **changed)
