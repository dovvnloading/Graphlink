"""Independent analytical and published-SI checks for the extended physics pack.

No SciPy, NumPy or networking: every oracle below is closed form, a finite
Fourier-series solution or a published NIST/CODATA reference value.
"""

import math

import pytest

from graphlink_plugins.scientific.core import readable_result, run_operation
from graphlink_plugins.scientific.packs import physics


def run(name, **changes):
    op = next(operation for operation in physics.OPERATIONS if operation.id == name)
    return run_operation("physics", op, {**op.example, **changes})


def test_legacy_operations_and_saved_receipts_still_work():
    assert [op.id for op in physics.OPERATIONS[:3]] == [
        "spring_oscillator", "thermal_layers", "sensible_heat",
    ]
    assert run("spring_oscillator")["values"]["period_s"] == pytest.approx(math.pi)
    assert run("thermal_layers")["values"]["heat_rate_w"] == pytest.approx(40.0)
    assert run("sensible_heat")["values"]["heat_j"] == pytest.approx(20900.0)
    for op in physics.OPERATIONS:
        result = run_operation("physics", op, op.example)
        assert result["provenance"]["receiptHash"]
        assert readable_result({"version": 1, "latest": result}, "physics")


def test_each_field_and_output_has_explicit_units():
    for op in physics.OPERATIONS:
        assert all(field.unit for field in op.fields), op.id
        result = run_operation("physics", op, op.example)
        if op.id in {"spring_oscillator", "thermal_layers", "sensible_heat"}:
            continue  # preserved legacy output schema
        assert result["values"]
        assert all(k.endswith(("_1", "_m", "_m2", "_s", "_hz", "_k", "_j", "_rad", "_deg", "_a", "_v", "_w_m2", "_kg_m_s", "_kg_m2", "_m_s")) for k in result["values"]), op.id
        if "plot" in result:
            assert "(" in result["plot"]["xLabel"] and "(" in result["plot"]["yLabel"]
        if "table" in result:
            assert all("(" in col and ")" in col for col in result["table"]["columns"])


def test_undriven_oscillator_has_closed_form_and_initial_velocity():
    times = [0, math.pi/4, math.pi/2]
    out = run("damped_driven_oscillator", mass_kg=1, stiffness_n_m=4,
              damping_n_s_m=0, drive_force_n=0, initial_displacement_m=0.3,
              initial_velocity_m_s=0.2, time_s=times)
    for t, row in zip(times, out["table"]["rows"]):
        assert row[1] == pytest.approx(0.3*math.cos(2*t)+0.1*math.sin(2*t), abs=1e-13)
        assert row[2] == pytest.approx(-0.6*math.sin(2*t)+0.2*math.cos(2*t), abs=1e-13)
        assert row[3] == pytest.approx(-4*row[1], abs=1e-13)
    assert "infinite" in out["values"]["quality_factor_1"]


def test_forced_oscillator_resonance_and_critical_and_overdamping():
    w0 = 2.0
    t = math.pi/4
    result = run("damped_driven_oscillator", mass_kg=1, stiffness_n_m=4,
                 damping_n_s_m=0, drive_force_n=2, drive_frequency_hz=w0/math.tau,
                 initial_displacement_m=0, initial_velocity_m_s=0, time_s=[0,t])
    assert result["table"]["rows"][-1][1] == pytest.approx(t/2, rel=1e-13)
    assert "unbounded" in result["values"]["drive_amplitude_m"]
    critical = run("damped_driven_oscillator", mass_kg=1, stiffness_n_m=4,
                   damping_n_s_m=4, drive_force_n=0, initial_displacement_m=1,
                   initial_velocity_m_s=0, time_s=[0,0.5])
    assert critical["table"]["rows"][-1][1] == pytest.approx(2/math.e, rel=1e-13)
    over = run("damped_driven_oscillator", mass_kg=1, stiffness_n_m=4,
               damping_n_s_m=6, drive_force_n=0, initial_displacement_m=1,
               initial_velocity_m_s=0, time_s=[0,0.5])
    r1,r2 = -3+math.sqrt(5),-3-math.sqrt(5)
    expected = (-r2*math.exp(r1*0.5)+r1*math.exp(r2*0.5))/(r1-r2)
    assert over["table"]["rows"][-1][1] == pytest.approx(expected, rel=1e-13)


def test_oscillator_quality_and_resonance_scan():
    out = run("damped_driven_oscillator", mass_kg=1, stiffness_n_m=100,
              damping_n_s_m=1, drive_force_n=1, drive_frequency_hz=1)
    assert out["values"]["quality_factor_1"] == 10
    assert out["values"]["damping_ratio_1"] == 0.05
    assert out["values"]["resonance_peak_frequency_hz"] == pytest.approx(math.sqrt(99.5)/math.tau)
    with pytest.raises(ValueError, match="resonance"):
        run("damped_driven_oscillator", mass_kg=1, stiffness_n_m=4,
            damping_n_s_m=0, scan_frequency_hz=[1/math.pi], drive_force_n=1)


def test_vacuum_projectile_matches_exact_parabola_using_standard_gravity():
    # Standard gravity is 9.80665 m/s², per the conventional SI reference.
    out = run("projectile_motion", initial_speed_m_s=20, launch_angle_deg=45,
              release_height_m=0, drag_coefficient_1=0, gravity_m_s2=9.80665)
    v,g = 20,9.80665
    assert out["values"]["flight_time_s"] == pytest.approx(math.sqrt(2)*v/g, rel=1e-12)
    assert out["values"]["horizontal_range_m"] == pytest.approx(v*v/g, rel=1e-12)
    assert out["values"]["maximum_height_m"] == pytest.approx(v*v/(4*g), rel=1e-12)
    assert out["plot"]["y"][-1] == 0


def test_projectile_quadratic_drag_matches_analytic_vertical_rise():
    # For upward flight, dv/dt = -g - b*v² gives v=vt*tan(atan(v0/vt)-gt/vt).
    # Integrating to apex gives height gain ln(1+b*v0²/g)/(2*b).
    mass, rho, cd, area, v0, g = 1.0, 1.0, 1.0, 0.2, 20.0, 9.80665
    b = rho*cd*area/(2*mass)
    out = run("projectile_motion", initial_speed_m_s=v0, launch_angle_deg=90,
              release_height_m=10, mass_kg=mass, air_density_kg_m3=rho,
              drag_coefficient_1=cd, cross_section_m2=area, gravity_m_s2=g,
              time_step_s=0.001)
    exact_apex = 10 + math.log1p(b*v0*v0/g)/(2*b)
    assert out["values"]["maximum_height_m"] == pytest.approx(exact_apex, rel=5e-8)
    vt = math.sqrt(g/b)
    at = min(out["table"]["rows"], key=lambda row: abs(row[0]-0.5))
    exact_v = vt*math.tan(math.atan(v0/vt)-g*at[0]/vt)
    assert at[4] == pytest.approx(exact_v, rel=5e-8)
    assert out["values"]["horizontal_range_m"] == pytest.approx(0, abs=1e-12)


def test_drag_reduces_range_and_refinement_converges():
    args = dict(initial_speed_m_s=30, launch_angle_deg=45, release_height_m=1)
    vacuum = run("projectile_motion", **args, drag_coefficient_1=0)
    drag = run("projectile_motion", **args, time_step_s=0.02)
    fine = run("projectile_motion", **args, time_step_s=0.01)
    assert 0 < fine["values"]["horizontal_range_m"] < vacuum["values"]["horizontal_range_m"]
    assert drag["values"]["horizontal_range_m"] == pytest.approx(fine["values"]["horizontal_range_m"], rel=1e-5)
    no_flight = run("projectile_motion", release_height_m=0, launch_angle_deg=-90)
    assert no_flight["values"]["flight_time_s"] == 0


@pytest.mark.parametrize("shape,factor", [
    ("solid_sphere", 2/5), ("thin_spherical_shell", 2/3),
    ("solid_disk",1/2), ("solid_cylinder",1/2), ("thin_hoop",1),
])
def test_radius_based_inertia_and_steiner_theorem(shape, factor):
    result = run("rigid_body_inertia", shape=shape, mass_kg=3,
                 radius_m=2, parallel_axis_offset_m=4)["values"]
    assert result["centroidal_inertia_kg_m2"] == pytest.approx(12*factor)
    assert result["parallel_axis_inertia_kg_m2"] == pytest.approx(12*factor+48)


def test_rod_plate_moments_and_parallel_axis():
    rod = run("rigid_body_inertia", shape="slender_rod", mass_kg=12,
              length_m=2, parallel_axis_offset_m=1)["values"]
    assert rod["centroidal_inertia_kg_m2"] == pytest.approx(4)
    assert rod["parallel_axis_inertia_kg_m2"] == pytest.approx(16)
    plate = run("rigid_body_inertia", shape="rectangular_plate",mass_kg=12,
                length_m=2,width_m=3,parallel_axis_offset_m=0)["values"]
    assert plate["centroidal_inertia_kg_m2"] == pytest.approx(13)


def test_explicit_implicit_three_node_exact_discrete_updates():
    opts = dict(length_m=1,diffusivity_m2_s=0.1,initial_temperature_k=100,
                left_boundary_k=0,right_boundary_k=0,nodes=3,
                time_step_s=0.5,duration_s=0.5)
    exp = run("transient_heat_1d",scheme="explicit",**opts)
    imp = run("transient_heat_1d",scheme="implicit",**opts)
    assert exp["values"]["fourier_number_1"] == pytest.approx(0.2)
    assert exp["values"]["midpoint_temperature_k"] == pytest.approx(60)
    assert imp["values"]["midpoint_temperature_k"] == pytest.approx(100/1.4)
    with pytest.raises(ValueError,match="Unstable explicit"):
        run("transient_heat_1d",scheme="explicit",**{**opts,"time_step_s":1.5,"duration_s":1.5})
    assert run("transient_heat_1d",scheme="implicit",**{**opts,"time_step_s":1.5,"duration_s":1.5})["values"]["midpoint_temperature_k"] > 0


def test_heat_equation_sine_series_analytic_benchmark():
    # Uniform hot interior, zero-temperature Dirichlet boundaries:
    # T(x,t) = (4 T0/π) Σ_{n odd} sin(nπx/L)/n exp(-n²π²αt/L²).
    center_exact = (400/math.pi)*sum(
        math.sin(n*math.pi/2)/n*math.exp(-n*n*math.pi**2*0.1)
        for n in range(1,51,2)
    )
    cfg = dict(length_m=1,diffusivity_m2_s=0.1,initial_temperature_k=100,
               left_boundary_k=0,right_boundary_k=0,nodes=51,duration_s=1)
    exp = run("transient_heat_1d",scheme="explicit",time_step_s=0.0016,**cfg)
    imp = run("transient_heat_1d",scheme="implicit",time_step_s=0.0016,**cfg)
    assert exp["values"]["midpoint_temperature_k"] == pytest.approx(center_exact, rel=0.002)
    assert imp["values"]["midpoint_temperature_k"] == pytest.approx(center_exact, rel=0.002)
    for output in (exp,imp):
        assert min(output["plot"]["y"]) >= 0
        assert max(output["plot"]["y"]) <= 100


def test_classical_and_relativistic_doppler_shift():
    sound = run("doppler_shift",wave_type="sound",emitted_frequency_hz=440,
                medium_wave_speed_m_s=343,source_toward_receiver_m_s=20,
                receiver_toward_source_m_s=0)["values"]
    assert sound["received_frequency_hz"] == pytest.approx(440*343/323)
    assert sound["wavelength_m"] == pytest.approx(323/440)
    light = run("doppler_shift",wave_type="light",emitted_frequency_hz=1e14,
                source_toward_receiver_m_s=0.6*299792458,
                receiver_toward_source_m_s=0)["values"]
    assert light["received_frequency_hz"] == pytest.approx(2e14)
    assert light["wavelength_m"] == pytest.approx(299792458/2e14)
    with pytest.raises(ValueError,match="below"):
        run("doppler_shift",wave_type="sound",source_toward_receiver_m_s=343)


def test_lorentz_energy_momentum_and_nist_codata_digits():
    c = 299792458  # exactly, from NIST's SI definition
    rest_mass = 9.1093837139e-31  # NIST 2022 CODATA electron mass, kg
    rest = run("relativistic_kinematics",rest_mass_kg=rest_mass,
               velocity_m_s=0,proper_interval_s=2)["values"]
    # 2022 CODATA electron rest-energy equivalent: 0.51099895069 MeV.
    assert rest["rest_energy_j"]/(1.602176634e-13) == pytest.approx(0.51099895069, abs=5e-12)
    assert rest["rest_energy_j"] == pytest.approx(8.187105788e-14, rel=5e-12)
    moving = run("relativistic_kinematics",rest_mass_kg=1,
                 velocity_m_s=0.6*c,proper_interval_s=8)["values"]
    assert moving["lorentz_factor_1"] == pytest.approx(1.25)
    assert moving["dilated_interval_s"] == pytest.approx(10)
    assert moving["relativistic_momentum_kg_m_s"] == pytest.approx(0.75*c)
    assert moving["total_energy_j"]**2-(moving["relativistic_momentum_kg_m_s"]*c)**2 == pytest.approx(c**4, rel=1e-12)
    with pytest.raises(ValueError,match="below c"):
        run("relativistic_kinematics",velocity_m_s=c)


def test_thin_lens_mirror_snell_tir_and_critical_angle():
    lens = run("geometric_optics",calculation="thin_lens",focal_length_m=0.1,
               object_distance_m=0.3)["values"]
    assert lens["image_distance_m"] == pytest.approx(0.15)
    assert lens["lateral_magnification_1"] == pytest.approx(-0.5)
    mirror = run("geometric_optics",calculation="spherical_mirror",focal_length_m=-0.1,
                 object_distance_m=0.3)["values"]
    assert mirror["image_distance_m"] == pytest.approx(-0.075)
    refraction = run("geometric_optics",calculation="snell",index_incident_1=1.0,
                     index_transmitted_1=1.5,incidence_angle_deg=30)["values"]
    assert refraction["refracted_angle_deg"] == pytest.approx(math.degrees(math.asin(1/3)))
    critical = math.degrees(math.asin(2/3))
    at_critical = run("geometric_optics",calculation="snell",index_incident_1=1.5,
                      index_transmitted_1=1.0,incidence_angle_deg=critical)["values"]
    assert at_critical["critical_angle_deg"] == pytest.approx(critical)
    assert at_critical["total_internal_reflection_1"] == "no (1)"
    tir = run("geometric_optics",calculation="snell",index_incident_1=1.5,
              index_transmitted_1=1.0,incidence_angle_deg=50)["values"]
    assert tir["total_internal_reflection_1"] == "yes (1)"


def test_single_and_double_slit_closed_form_minima_and_fringes():
    lam,a,d,L = 500e-9,10e-6,100e-6,2.0
    zero_single = L*math.tan(math.asin(lam/a))
    single = run("slit_diffraction",pattern="single",screen_position_m=[0,zero_single],
                 wavelength_m=lam,slit_width_m=a,slit_separation_m=d,screen_distance_m=L)
    assert single["plot"]["y"] == pytest.approx([1,0],abs=1e-24)
    zero_double = L*math.tan(math.asin(lam/(2*d)))
    double = run("slit_diffraction",pattern="double",screen_position_m=[0,zero_double],
                 wavelength_m=lam,slit_width_m=a,slit_separation_m=d,screen_distance_m=L)
    assert double["plot"]["y"] == pytest.approx([1,0],abs=1e-24)
    assert double["values"]["paraxial_fringe_spacing_m"] == pytest.approx(0.01)


def test_series_rlc_exact_lossless_and_critical_step_transients():
    lossless = run("series_rlc_response",resistance_ohm=0,inductance_h=1,
                   capacitance_f=1,step_voltage_v=0,
                   initial_capacitor_voltage_v=1,initial_current_a=0,
                   time_s=[0,0.5,1.0],scan_frequency_hz=[0,0.1,0.2])
    for row in lossless["table"]["rows"]:
        t,vc,current,energy = row
        assert vc == pytest.approx(math.cos(t),abs=1e-13)
        assert current == pytest.approx(-math.sin(t),abs=1e-13)
        assert energy == pytest.approx(0.5,abs=1e-13)
    critical = run("series_rlc_response",resistance_ohm=2,inductance_h=1,
                   capacitance_f=1,step_voltage_v=1,initial_capacitor_voltage_v=0,
                   initial_current_a=0,time_s=[0,1])
    assert critical["table"]["rows"][-1][1] == pytest.approx(1-2/math.e)
    assert critical["table"]["rows"][-1][2] == pytest.approx(1/math.e)


def test_rlc_frequency_response_quality_bandwidth_and_lossless_singularity():
    R,L,C,V = 10,0.1,0.001,1
    f0 = 1/(math.tau*math.sqrt(L*C))
    out = run("series_rlc_response",resistance_ohm=R,inductance_h=L,
              capacitance_f=C,drive_amplitude_v=V,
              scan_frequency_hz=[0,f0,2*f0])
    assert out["plot"]["y"][0] == 0
    assert out["plot"]["y"][1] == pytest.approx(V/R,rel=1e-12)
    assert out["values"]["quality_factor_1"] == pytest.approx(math.sqrt(L/C)/R)
    assert out["values"]["half_power_bandwidth_hz"] == pytest.approx(R/(math.tau*L))
    with pytest.raises(ValueError,match="resonant"):
        run("series_rlc_response",resistance_ohm=0,inductance_h=1,capacitance_f=1,
            drive_amplitude_v=1,scan_frequency_hz=[1/math.tau])


@pytest.mark.parametrize("name,changes", [
    ("damped_driven_oscillator",{"damping_n_s_m":-1}),
    ("damped_driven_oscillator",{"time_s":[1,0]}),
    ("projectile_motion",{"mass_kg":0}),
    ("projectile_motion",{"max_duration_s":0.001}),
    ("rigid_body_inertia",{"shape":"unsupported"}),
    ("transient_heat_1d",{"scheme":"unsupported"}),
    ("transient_heat_1d",{"nodes":2}),
    ("doppler_shift",{"wave_type":"unsupported"}),
    ("relativistic_kinematics",{"rest_mass_kg":0}),
    ("geometric_optics",{"calculation":"invalid"}),
    ("slit_diffraction",{"slit_width_m":0}),
    ("series_rlc_response",{"capacitance_f":0}),
])
def test_invalid_physical_inputs_rejected(name,changes):
    with pytest.raises(ValueError):
        run(name,**changes)


def test_rlc_phasor_bode_impedance_phase_and_amplitude():
    r,l,c,v = 10,0.1,0.001,1
    f0 = 1/(math.tau*math.sqrt(l*c))
    out = run("series_rlc_frequency", resistance_ohm=r,inductance_h=l,
              capacitance_f=c,drive_amplitude_v=v,frequency_hz=[0,f0/2,f0,2*f0])
    dc,below,at,above = out["table"]["rows"]
    assert "infinite" in dc[1]
    assert dc[3] == 0
    assert below[2] < 0  # capacitive below resonance
    assert above[2] > 0  # inductive above resonance
    assert at[1] == pytest.approx(r,rel=1e-14)
    assert at[2] == pytest.approx(0,abs=1e-13)
    assert at[3] == pytest.approx(v/r)
    assert out["values"]["quality_factor_1"] == pytest.approx(1)
    with pytest.raises(ValueError,match="unbounded"):
        run("series_rlc_frequency",resistance_ohm=0,inductance_h=1,capacitance_f=1,
            drive_amplitude_v=1,frequency_hz=[1/math.tau])
