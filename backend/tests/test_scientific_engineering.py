"""Engineering handbook oracles, analytic transients and workbench contracts."""

import json
import math

import pytest

from graphlink_plugins.scientific.core import readable_result, run_operation
from graphlink_plugins.scientific.packs.engineering import OPERATIONS


OP = {op.id: op for op in OPERATIONS}


def run(name, **overrides):
    op = OP[name]
    return run_operation("engineering", op, {**op.example, **overrides})


def test_legacy_three_unchanged_and_new_example_receipts():
    assert [op.id for op in OPERATIONS[:3]] == ["resistor_network", "rc_step", "linear_expansion"]
    assert run("resistor_network", resistance_ohm=[10, 30])["values"] == {"series_ohm": 40, "parallel_ohm": 7.5}
    assert run("rc_step")["values"]["time_constant_s"] == 1
    assert run("linear_expansion")["values"] == {
        "strain": 0.00092, "length_change_m": pytest.approx(0.00184), "final_length_m": pytest.approx(2.00184),
    }
    assert len(OPERATIONS) == 9
    for op in OPERATIONS:
        before = json.dumps(op.example)
        result = run_operation("engineering", op, op.example)
        assert result["operationId"] == op.id
        assert result["provenance"]["packVersion"] == "1.1.0"
        assert readable_result({"version": 1, "latest": result}, "engineering")
        assert json.dumps(op.example) == before
        json.dumps(result, allow_nan=False)
        assert result["summary"] and result["values"]
        for field in op.fields:
            if op not in OPERATIONS[:3]:
                assert field.unit, (op.id, field.key)
        if op not in OPERATIONS[:3]:
            for key, value in result["values"].items():
                if isinstance(value, (int, float)):
                    assert math.isfinite(value), (op.id, key)
            if "table" in result:
                assert all("(" in column and ")" in column for column in result["table"]["columns"])
            if "plot" in result:
                assert "(" in result["plot"]["xLabel"] and "(" in result["plot"]["yLabel"]


# NCEES FE Reference Handbook 10.5, pages 139–140, beam tables.
# https://ncees.org/exams/exam-prep/ (FE handbook), numerical identities
# in Mechanics of Materials, based on Hibbeler's tables.
@pytest.mark.parametrize("support,point,where,uniform,expected_y,expected_theta,expected_moment", [
    ("simply_supported", 1200, 2, 0, 1200*4**3/(48*2e6), 1200*4**2/(16*2e6), 1200*4/4),
    ("simply_supported", 0, 2, 400, 5*400*4**4/(384*2e6), 400*4**3/(24*2e6), 400*4**2/8),
    ("cantilever", 1200, 4, 0, 1200*4**3/(3*2e6), 1200*4**2/(2*2e6), 1200*4),
    ("cantilever", 0, 2, 400, 400*4**4/(8*2e6), 400*4**3/(6*2e6), 400*4**2/2),
])
def test_ncees_beam_reference_cases(support, point, where, uniform, expected_y, expected_theta, expected_moment):
    result = run("beam_response", support=support, length_m=4,
                 young_modulus_pa=2e11, second_moment_m4=1e-5,
                 point_load_n=point, point_location_m=where, uniform_load_n_m=uniform,
                 position_m=[0, 1, 2, 3, 4])
    v = result["values"]
    assert v["max_deflection_m"] == pytest.approx(expected_y, rel=1e-12)
    assert v["max_abs_slope_rad"] == pytest.approx(expected_theta, rel=1e-12)
    assert v["max_abs_bending_moment_nm"] == pytest.approx(expected_moment, rel=1e-12)
    assert v["max_deflection_at_m"] == pytest.approx(2 if support == "simply_supported" else 4)
    rows = result["table"]["rows"]
    assert rows[0][1] == 0
    if support == "simply_supported":
        assert rows[-1][1] == 0
        assert rows[-1][4] == 0
    else:
        assert rows[0][2] == 0
        assert rows[-1][3] == pytest.approx(0)


def test_eccentric_point_beam_superposition_and_shear_jump():
    # NCEES eccentric point-load formula δ(a) = P a² b² / (3 E I L).
    p, a, length, ei = 600, 2, 5, 2e6
    result = run("beam_response", length_m=length, young_modulus_pa=2e11, second_moment_m4=1e-5,
                 point_load_n=p, point_location_m=a, uniform_load_n_m=0,
                 position_m=[0, a-1e-8, a, a+1e-8, length])
    assert result["table"]["rows"][2][1] == pytest.approx(p*a*a*(length-a)**2/(3*ei*length))
    assert result["table"]["rows"][1][3] - result["table"]["rows"][2][3] == pytest.approx(p)
    mixed = run("beam_response", point_load_n=600, point_location_m=1,
                uniform_load_n_m=50, position_m=[0, 0.5, 1, 2, 4])
    only_point = run("beam_response", point_load_n=600, point_location_m=1,
                     uniform_load_n_m=0, position_m=[0, 0.5, 1, 2, 4])
    only_udl = run("beam_response", point_load_n=0, point_location_m=1,
                   uniform_load_n_m=50, position_m=[0, 0.5, 1, 2, 4])
    for i in range(5):
        for col in (1, 2, 3, 4):
            assert mixed["table"]["rows"][i][col] == pytest.approx(
                only_point["table"]["rows"][i][col] + only_udl["table"]["rows"][i][col], abs=1e-12)
    assert mixed["values"]["reaction_left_n"] + mixed["values"]["reaction_right_n"] == 800
    cantilever = run("beam_response", support="cantilever", point_load_n=900,
                     point_location_m=1.5, uniform_load_n_m=0, position_m=[0, 1.5, 4])
    assert cantilever["values"]["max_deflection_m"] == pytest.approx(
        900*1.5**2*(3*4-1.5)/(6*2e11*8e-6))


def test_combined_stress_yield_and_reversed_bending():
    result = run("combined_stress", axial_force_n=30000, bending_moment_nm=600,
                 area_m2=0.003, section_modulus_m3=3e-5, shear_force_n=6000,
                 shear_area_m2=0.002, yield_strength_pa=100e6, safety_factor_1=2)
    v = result["values"]
    assert v["axial_stress_pa"] == pytest.approx(10e6)
    assert v["bending_stress_pa"] == pytest.approx(20e6)
    assert v["normal_stress_face_a_pa"] == pytest.approx(30e6)
    assert v["normal_stress_face_b_pa"] == pytest.approx(-10e6)
    assert v["max_von_mises_pa"] == pytest.approx(math.sqrt((30e6)**2 + 3*(3e6)**2))
    assert v["allowable_von_mises_pa"] == 50e6
    assert v["yield_check"] == "PASS"
    fails = run("combined_stress", axial_force_n=30000, bending_moment_nm=-600,
                area_m2=0.003, section_modulus_m3=3e-5, shear_force_n=6000,
                shear_area_m2=0.002, yield_strength_pa=30e6, safety_factor_1=2)
    assert fails["values"]["max_von_mises_pa"] == pytest.approx(v["max_von_mises_pa"])
    assert fails["values"]["utilization_ratio_1"] > 1
    assert fails["values"]["yield_check"] == "FAIL"


# Moody chart reference table (6 digits), Re=100000, epsilon/D=0.0002:
# https://www.inflectionpointengineering.com/library/references/moody-friction-factor
# Original correlation: Colebrook–White; Darcy–Weisbach in USDA NEH 634-04.
def test_pipe_moody_reference_to_stated_six_digits_and_head_loss():
    diameter, re = 0.1, 100000
    speed = 1.0
    q = speed*math.pi*diameter**2/4
    v = run("pipe_flow", diameter_m=diameter, length_m=50, roughness_m=2e-5,
            volume_flow_m3_s=q, density_kg_m3=1000, viscosity_pa_s=0.001,
            gravity_m_s2=9.80665)["values"]
    assert v["reynolds_number_1"] == pytest.approx(re)
    assert f"{v['darcy_friction_factor_1']:.6f}" == "0.019005"
    assert v["colebrook_iterations_count"] > 1
    assert v["colebrook_residual_1"] < 1e-11
    expected_head = v["darcy_friction_factor_1"] * 50/diameter * speed**2/(2*9.80665)
    assert v["head_loss_m"] == pytest.approx(expected_head)
    assert v["pressure_drop_pa"] == pytest.approx(1000*9.80665*expected_head)
    assert v["flow_regime"] == "turbulent"


def test_pipe_laminar_stagnant_transition_and_roughness_rejection():
    # Hagen–Poiseuille/Darcy f=64/Re for fully developed laminar flow.
    case = OP["pipe_flow"].example
    q = math.pi*0.1**2/4 * 0.01
    lam = run("pipe_flow", volume_flow_m3_s=q)["values"]
    assert lam["reynolds_number_1"] == pytest.approx(1000)
    assert lam["darcy_friction_factor_1"] == pytest.approx(.064)
    assert lam["colebrook_iterations_count"] == 0
    still = run("pipe_flow", volume_flow_m3_s=0)["values"]
    assert still["head_loss_m"] == still["pressure_drop_pa"] == 0
    assert still["flow_regime"] == "stagnant"
    with pytest.raises(ValueError, match="transitional"):
        run("pipe_flow", volume_flow_m3_s=math.pi*0.1**2/4*0.03)
    with pytest.raises(ValueError, match="roughness"):
        run("pipe_flow", roughness_m=0.1)


# Lienhard & Lienhard, A Heat Transfer Textbook (6th edition), section 3.3,
# parallel-flow worked example: Cmin=10000 W/K, Cr=0.5, U=500, NTU=1.5,
# epsilon=0.596 (3 dp), Q=655.6 kW after book rounding.
def test_heat_exchanger_published_parallel_flow_worked_example():
    v = run("heat_exchanger_sizing", flow_arrangement="parallel", target_heat_w=655600)["values"]
    assert v["capacity_rate_ratio_1"] == pytest.approx(0.5)
    assert round(v["effectiveness_1"], 3) == 0.596
    assert v["hot_outlet_c"] == pytest.approx(84.44, abs=1e-10)
    assert v["cold_outlet_c"] == pytest.approx(72.78, abs=1e-10)
    # The handbook's 30 m² is rounded from ε≈0.596, so area is compared
    # to the last meaningful printed digit (whole m²), not 6 figures.
    assert round(v["required_area_lmtd_m2"]) == 30
    assert v["required_area_ntu_m2"] == pytest.approx(v["required_area_lmtd_m2"], rel=1e-12)


def test_counterflow_balanced_capacity_equal_terminal_differences_and_pinch():
    balanced = run("heat_exchanger_sizing", flow_arrangement="counterflow",
                   hot_mass_flow_kg_s=2.5, cold_mass_flow_kg_s=2.5,
                   hot_cp_j_kg_k=4000, cold_cp_j_kg_k=4000,
                   hot_inlet_c=90, cold_inlet_c=20, target_heat_w=350000)["values"]
    assert balanced["capacity_rate_ratio_1"] == 1
    assert balanced["lmtd_k"] == 35
    assert balanced["effectiveness_1"] == .5
    assert balanced["ntu_1"] == 1
    assert balanced["required_area_lmtd_m2"] == pytest.approx(balanced["required_area_ntu_m2"])
    unequal = run("heat_exchanger_sizing", flow_arrangement="counterflow")["values"]
    assert unequal["required_area_ntu_m2"] == pytest.approx(unequal["required_area_lmtd_m2"], rel=1e-12)
    with pytest.raises(ValueError, match="limit|pinch"):
        run("heat_exchanger_sizing", flow_arrangement="parallel", target_heat_w=800000)
    with pytest.raises(ValueError, match="between zero"):
        run("heat_exchanger_sizing", target_heat_w=1200000)


# Illinois ECE486 handbook, lec06: first-order rise time τ ln9 and
# undamped/underdamped second-order peak/overshoot formulas.
# https://courses.grainger.illinois.edu/ece486/sp2025/documentation/handbook/lec06.html
def test_first_order_exact_steps_bode_and_phase_margin():
    v = run("transfer_function_response", order=1, dc_gain_1=2,
            time_constant_s=2, step_amplitude_1=3, time_s=[0, 2, 4, 8],
            angular_frequency_rad_s=[0, math.sqrt(3)/2, 1])["values"]
    assert v["final_step_output_1"] == 6
    assert v["rise_time_10_90_s"] == pytest.approx(2*math.log(9))
    assert v["settling_time_2_percent_s"] == pytest.approx(2*math.log(50))
    assert v["peak_overshoot_percent"] == 0
    assert v["gain_crossover_rad_s"] == pytest.approx(math.sqrt(3)/2)
    assert v["phase_margin_deg"] == pytest.approx(120)
    assert v["gain_margin_db"].startswith("∞")
    out = run("transfer_function_response", order=1, dc_gain_1=2,
              time_constant_s=2, step_amplitude_1=3, time_s=[0, 2, 4, 8],
              angular_frequency_rad_s=[0, math.sqrt(3)/2, 1])
    assert out["plot"]["y"][1] == pytest.approx(6*(1-math.exp(-1)))
    assert out["table"]["rows"][0] == pytest.approx([0, 2, 20*math.log10(2), 0])
    no_unity = run("transfer_function_response", order=1, dc_gain_1=0.5)["values"]
    assert no_unity["unity_gain_crossings_count"] == 0
    assert "undefined" in no_unity["phase_margin_deg"]


def test_second_order_analytic_peak_exact_last_exit_settling_and_margin():
    zeta, omega = .5, 2
    r = run("transfer_function_response", order=2, dc_gain_1=2,
            natural_frequency_rad_s=omega, damping_ratio_1=zeta,
            step_amplitude_1=1, time_s=[0, math.pi/(omega*math.sqrt(.75)), 10],
            angular_frequency_rad_s=[0, 1, 2, 4, 8])
    v = r["values"]
    peak_time = math.pi/(omega*math.sqrt(1-zeta*zeta))
    overshoot = 100*math.exp(-math.pi*zeta/math.sqrt(1-zeta*zeta))
    assert v["first_peak_time_s"] == pytest.approx(peak_time)
    assert v["peak_overshoot_percent"] == pytest.approx(overshoot)
    assert r["plot"]["y"][1] == pytest.approx(2*(1+overshoot/100))
    assert v["phase_margin_deg"] > 0
    assert v["gain_margin_ratio_1"].startswith("∞")
    assert v["settling_time_2_percent_s"] > 0
    from graphlink_plugins.scientific.packs.engineering import _second_step_fraction
    settlement = v["settling_time_2_percent_s"]
    # The reported time is the final crossing of the ±2% tolerance band.
    assert abs(_second_step_fraction(zeta, omega*settlement)-1) == pytest.approx(.02, rel=1e-9)
    for t in (settlement+0.1, settlement+1, settlement+5):
        assert abs(_second_step_fraction(zeta, omega*t)-1) < .02


def test_second_order_critically_and_overdamped_and_two_gain_crossings():
    for zeta in (1, 2):
        v = run("transfer_function_response", order=2, damping_ratio_1=zeta)["values"]
        assert v["peak_overshoot_percent"] == 0
        assert v["settling_time_2_percent_s"] > v["rise_time_10_90_s"]
        assert "none" in v["first_peak_time_s"]
    # Resonant gain > 1 even though DC gain < 1, giving two unity crossings.
    v = run("transfer_function_response", order=2, dc_gain_1=.5,
            damping_ratio_1=.1)["values"]
    assert v["unity_gain_crossings_count"] == 2
    assert 0 < v["phase_margin_deg"] < 90


def test_pid_proportional_exact_plant_pole_and_pi_reference():
    p = run("pid_closed_loop", plant_gain_1=2, plant_tau_s=3, kp_1=1,
            ki_per_s=0, kd_s=0, setpoint_1=1, time_s=[0, 1, 2, 10])
    assert p["values"]["steady_state_output_1"] == pytest.approx(2/3)
    assert p["values"]["steady_state_error_1"] == pytest.approx(1/3)
    assert p["values"]["closed_loop_time_constant_s"] == pytest.approx(1)
    assert p["plot"]["y"][1] == pytest.approx(2/3*(1-math.exp(-1)))
    # PI tuned so T(s)=(s+1)/(s+1)^2 -> step y(t)=1-exp(-t).
    pi = run("pid_closed_loop", plant_gain_1=1, plant_tau_s=1, kp_1=1,
             ki_per_s=1, kd_s=0, setpoint_1=1, time_s=[0, .5, 1, 2, 8])
    assert pi["plot"]["y"] == pytest.approx([1-math.exp(-t) for t in [0, .5, 1, 2, 8]])
    assert pi["values"]["steady_state_error_1"] == 0
    # Derivative on measurement: output starts at zero even for setpoint step.
    pid = run("pid_closed_loop", plant_gain_1=1, plant_tau_s=2,
              kp_1=2, ki_per_s=1, kd_s=.2, setpoint_1=1,
              time_s=[0, .001, .1, 1, 100])
    assert pid["plot"]["y"][0] == pytest.approx(0, abs=1e-14)
    assert pid["plot"]["y"][-1] == pytest.approx(1)
    assert pid["values"]["steady_state_error_1"] == 0


@pytest.mark.parametrize("name,changes,part", [
    ("beam_response", {"support": "hanging"}, "Support"),
    ("beam_response", {"point_location_m": 8}, "location"),
    ("beam_response", {"uniform_load_n_m": -1}, "downward"),
    ("beam_response", {"position_m": [2, 1]}, "strictly increasing"),
    ("beam_response", {"position_m": [-1, 0]}, "nonnegative"),
    ("combined_stress", {"section_modulus_m3": 0}, "positive"),
    ("combined_stress", {"safety_factor_1": 0.9}, "at least 1"),
    ("pipe_flow", {"diameter_m": 0}, "positive"),
    ("pipe_flow", {"roughness_m": -1}, "roughness"),
    ("heat_exchanger_sizing", {"flow_arrangement": "crossflow"}, "arrangement"),
    ("heat_exchanger_sizing", {"cold_inlet_c": 180}, "temperatures"),
    ("transfer_function_response", {"order": 3}, "order"),
    ("transfer_function_response", {"damping_ratio_1": 0}, "requires"),
    ("transfer_function_response", {"angular_frequency_rad_s": [2, 1]}, "strictly increasing"),
    ("pid_closed_loop", {"plant_tau_s": 0}, "positive"),
    ("pid_closed_loop", {"kd_s": -0.1}, "nonnegative"),
    ("pid_closed_loop", {"time_s": [1, 1]}, "strictly increasing"),
])
def test_engineering_rejects_out_of_domain_input(name, changes, part):
    with pytest.raises(ValueError, match=part):
        run(name, **changes)
