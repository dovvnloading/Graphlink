"""EPA, FAO-56 and hydrology reference cases with workbench artifact checks."""

import ast
import math
from pathlib import Path

import pytest

from graphlink_plugins.scientific.core import check_artifact, run_operation
from graphlink_plugins.scientific.packs import environment as env


def _run(name, **changes):
    operation = next(o for o in env.OPERATIONS if o.id == name)
    return operation.calculate({**operation.example, **changes})


def _aqi(**concentrations):
    inputs = {name: -1 for name in env._AQI_BREAKPOINTS}
    return _run("air_quality_index", **{**inputs, **concentrations})


def test_eight_new_operations_three_originals_and_legacy_results_are_unchanged():
    assert [o.id for o in env.OPERATIONS[:3]] == [
        "flow_weighted_concentration", "rainfall_runoff", "degree_days"
    ]
    assert len(env.OPERATIONS) == 11
    assert _run("flow_weighted_concentration")["values"] == {
        "mean_concentration_mg_l": 5, "total_water_m3": 1800, "total_constituent_kg": 9
    }
    assert _run("rainfall_runoff")["values"] == {
        "rainfall_volume_m3": 30000, "runoff_depth_mm": 6, "runoff_coefficient": .3
    }
    assert _run("degree_days")["plot"]["y"] == [0, 4, 10]
    assert all(f.unit for o in env.OPERATIONS for f in o.fields)
    for operation in env.OPERATIONS:
        check_artifact(operation.calculate(operation.example))
        result = run_operation("environment", operation, operation.example)
        assert result["provenance"]["operationId"] == operation.id
        assert result["provenance"]["packId"] == "environment"
        assert result["provenance"]["resultHash"]


def test_no_new_nonstdlib_imports():
    source = Path(env.__file__).read_text(encoding="utf8")
    imports = [node.module.split(".")[0] for node in ast.walk(ast.parse(source))
               if isinstance(node, ast.ImportFrom) and node.module]
    imports += [alias.name.split(".")[0] for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.Import) for alias in node.names]
    assert set(imports) <= {"__future__", "math", "collections", "decimal", "statistics",
                            "fractions", "itertools", "graphlink_plugins"}


def test_epa_aqi_tad_2024_published_ozone_and_multipollutant_examples():
    # EPA-454/B-24-002 p.15: 0.07853333 ppm O3 truncates to 0.078 -> AQI 126.
    solo = _aqi(o3_8h_ppm=.07853333)
    assert solo["values"]["aqi"] == 126
    assert solo["table"]["rows"][0][2] == .078
    multi = _aqi(o3_8h_ppm=.07853333, pm25_24h_ug_m3=35.9, co_8h_ppm=8.4)
    assert [r[4] for r in multi["table"]["rows"]] == [126, 102, 90]
    assert multi["values"]["main_pollutant"] == "O₃ 8-h"
    # EPA prints 148 for 0.162 ppm 1-h ozone, but its *published equation*
    # and breakpoint endpoints give 147.487... -> 147; use the equation.
    assert _aqi(o3_8h_ppm=.078, o3_1h_ppm=.162)["values"]["aqi"] == 147


@pytest.mark.parametrize("field,low,high,index_low,index_high", [
    ("pm25_24h_ug_m3", 9.0, 9.1, 50, 51),
    ("pm25_24h_ug_m3", 35.4, 35.5, 100, 101),
    ("pm10_24h_ug_m3", 54, 55, 50, 51),
    ("co_8h_ppm", 4.4, 4.5, 50, 51),
    ("no2_1h_ppb", 53, 54, 50, 51),
    ("so2_1h_ppb", 35, 36, 50, 51),
    ("o3_8h_ppm", .054, .055, 50, 51),
    ("o3_1h_ppm", .164, .165, 150, 151),
])
def test_epa_official_breakpoint_transitions(field, low, high, index_low, index_high):
    assert _aqi(**{field: low})["values"]["aqi"] == index_low
    assert _aqi(**{field: high})["values"]["aqi"] == index_high


def test_epa_upper_limits_aqi_500_and_special_so2_period_rules():
    assert _aqi(pm25_24h_ug_m3=325.4)["values"]["aqi"] == 500
    assert _aqi(pm25_24h_ug_m3=326.0)["values"]["aqi"] > 500
    assert _aqi(no2_1h_ppb=2049)["values"]["aqi"] == 500
    assert _aqi(so2_24h_ppb=305)["values"]["aqi"] == 201
    assert _aqi(so2_24h_ppb=604)["values"]["aqi"] == 300
    assert _aqi(so2_1h_ppb=400, so2_24h_ppb=200)["values"]["aqi"] == 200
    assert _aqi(so2_1h_ppb=400, so2_24h_ppb=800)["values"]["aqi"] > 300
    with pytest.raises(ValueError):
        _aqi(o3_1h_ppm=.104)
    with pytest.raises(ValueError):
        _aqi(o3_8h_ppm=.250)
    with pytest.raises(ValueError):
        _aqi(pm25_24h_ug_m3=-.5)
    with pytest.raises(ValueError):
        _aqi()


def test_briggs_epa_dispersion_reference_and_reflection():
    # EPA / Briggs 1973 open-country class D at 1000 m: σy 76.3 m, σz 37.9 m.
    values = _run("gaussian_plume")["values"]
    assert values["sigma_y_m"] == pytest.approx(76.3, abs=.05)
    assert values["sigma_z_m"] == pytest.approx(37.9, abs=.05)
    # H=0, z=0: reflected term doubles the unreflected 1/(2πuσyσz).
    zero_height = _run("gaussian_plume", effective_stack_height_m=0)["values"]
    formula = 10 * 1e6 / (math.pi * 3 * values["sigma_y_m"] * values["sigma_z_m"])
    assert zero_height["receptor_concentration_ug_m3"] == pytest.approx(formula)
    assert _run("gaussian_plume", crosswind_m=200)["values"]["receptor_concentration_ug_m3"] < values["receptor_concentration_ug_m3"]
    assert _run("gaussian_plume", terrain="urban", stability_class="A")["values"]["sigma_z_m"] == pytest.approx(240 * math.sqrt(2))
    assert _run("gaussian_plume", terrain="urban", stability_class="F")["values"]["sigma_z_m"] == pytest.approx(80 / math.sqrt(1.15))
    for stability in "ABCDEF":
        assert _run("gaussian_plume", stability_class=stability)["values"]["sigma_z_m"] > 0
    for changes in ({"wind_speed_m_s": 0}, {"downwind_m": 10}, {"terrain": "ocean"},
                    {"stability_class": "G"}, {"receptor_height_m": -1}):
        with pytest.raises(ValueError):
            _run("gaussian_plume", **changes)


def test_streeter_phelps_analytical_minimum_and_equal_rate_limit():
    params = {"initial_do_mg_l": 8, "saturation_do_mg_l": 9,
              "ultimate_bod_mg_l": 15, "deoxygenation_per_day": .2,
              "reaeration_per_day": .4, "velocity_m_s": .35, "reach_length_km": 180}
    result = _run("dissolved_oxygen_sag", **params)
    tcrit = math.log(.4 * (.2 * 15 - .2 * 1) / (.2**2 * 15)) / (.4 - .2)
    crit_km = .35 * 86.4 * tcrit
    deficit = .2 * 15 / (.4 - .2) * (math.exp(-.2 * tcrit) - math.exp(-.4 * tcrit)) + math.exp(-.4 * tcrit)
    assert result["values"]["minimum_do_distance_km"] == pytest.approx(crit_km)
    assert result["values"]["minimum_do_mg_l"] == pytest.approx(9 - deficit)
    assert result["values"]["minimum_do_mg_l"] <= result["values"]["outlet_do_mg_l"]
    assert _run("dissolved_oxygen_sag", **{**params, "deoxygenation_per_day": .4})["values"]["outlet_do_mg_l"] == pytest.approx(
        9 - (1 + .4 * 15 * (180 / (.35 * 86.4))) * math.exp(-.4 * 180 / (.35 * 86.4))
    )
    with pytest.raises(ValueError):
        _run("dissolved_oxygen_sag", initial_do_mg_l=10)


def test_first_order_decay_fit_rate_half_life_and_bad_data():
    times = [0, 1, 2, 4, 5, 8]
    concentrations = [12 * math.exp(-.23 * t) for t in times]
    result = _run("first_order_decay_fit", time_day=times, concentration_mg_l=concentrations,
                  prediction_time_day=10)
    values = result["values"]
    assert values["decay_constant_per_day"] == pytest.approx(.23, rel=1e-12)
    assert values["half_life_day"] == pytest.approx(math.log(2) / .23, rel=1e-12)
    assert values["initial_concentration_mg_l"] == pytest.approx(12)
    assert values["predicted_concentration_mg_l"] == pytest.approx(12 * math.exp(-2.3))
    assert values["rate_standard_error_per_day"] < 1e-12
    for changes in ({"concentration_mg_l": [1, 2, 3]}, {"time_day": [0, 0, 2]},
                    {"concentration_mg_l": [1, 0, 1]}, {"time_day": [0, 1]}):
        with pytest.raises(ValueError):
            _run("first_order_decay_fit", **changes)


def test_rational_and_nrcs_runoff_units_abstraction_and_limits():
    assert _run("rational_runoff")["values"]["peak_runoff_m3_s"] == pytest.approx(5 / 3)
    with pytest.raises(ValueError):
        _run("rational_runoff", storm_duration_min=10)
    with pytest.raises(ValueError):
        _run("rational_runoff", runoff_coefficient=1.1)
    values = _run("scs_curve_number")["values"]
    assert values["potential_retention_mm"] == pytest.approx(25400 / 75 - 254)
    s = values["potential_retention_mm"]
    assert values["direct_runoff_depth_mm"] == pytest.approx((80 - .2 * s) ** 2 / (80 + .8 * s))
    assert values["direct_runoff_volume_m3"] == pytest.approx(values["direct_runoff_depth_mm"] * 2000)
    assert _run("scs_curve_number", rainfall_mm=10)["values"]["direct_runoff_depth_mm"] == 0
    assert _run("scs_curve_number", curve_number=100)["values"]["direct_runoff_depth_mm"] == 80
    # USDA NRCS TR-55 (1986), Example 2-1, worksheet 2: P=6.0 in,
    # area-weighted CN=70, runoff Q=2.81 in to the worksheet's 0.01 in.
    tr55 = _run("scs_curve_number", rainfall_mm=6 * 25.4, curve_number=70)["values"]
    assert round(tr55["direct_runoff_depth_mm"] / 25.4, 2) == 2.81
    for changes in ({"curve_number": 0}, {"initial_abstraction_ratio": 1.01}, {"rainfall_mm": -1}):
        with pytest.raises(ValueError):
            _run("scs_curve_number", **changes)


def test_fao56_examples_17_and_20_to_published_digits():
    # FAO-56, chapter 4, Example 17, Bangkok April: ET0 5.72 => 5.7 mm/day.
    bangkok = _run("fao56_evapotranspiration")["values"]
    assert bangkok["reference_et0_mm_day"] == pytest.approx(5.72, abs=.01)
    assert round(bangkok["reference_et0_mm_day"], 1) == 5.7
    # Example 20, Lyon July: ET0 4.56 => 4.6 mm/day. Intermediate
    # FAO values are rounded at multiple steps; full precision gives 4.565.
    lyon = _run("fao56_evapotranspiration", tmax_c=26.6, tmin_c=14.8,
                actual_vapour_pressure_kpa=1.68, wind_speed_2m_m_s=2,
                elevation_m=200, net_radiation_mj_m2_day=13.48,
                soil_heat_flux_mj_m2_day=0)["values"]
    assert lyon["reference_et0_mm_day"] == pytest.approx(4.56, abs=.01)
    assert round(lyon["reference_et0_mm_day"], 1) == 4.6
    assert bangkok["reference_et0_mm_day"] == pytest.approx(
        bangkok["radiation_term_mm_day"] + bangkok["aerodynamic_term_mm_day"])
    with pytest.raises(ValueError):
        _run("fao56_evapotranspiration", actual_vapour_pressure_kpa=9)
    with pytest.raises(ValueError):
        _run("fao56_evapotranspiration", wind_speed_2m_m_s=-1)


def test_mann_kendall_sen_irregular_time_ties_and_independent_samples():
    times = [2000, 2001, 2003, 2005, 2006, 2010, 2012, 2017]
    measured = [3 + .25 * (t - 2000) for t in times]
    values = _run("mann_kendall_sen", time_year=times, observations=measured,
                  observation_unit="°C")["values"]
    assert values["mann_kendall_s"] == 28
    assert values["kendall_tau_b"] == pytest.approx(1)
    assert values["sen_slope_per_year"] == pytest.approx(.25)
    assert values["sen_slope_unit"] == "°C/year"
    assert values["trend"] == "increasing"
    assert values["two_sided_p_value"] == pytest.approx(2 / math.factorial(8))
    assert values["p_value_method"] == "exact permutation"
    flat = _run("mann_kendall_sen", observations=[1] * 8)["values"]
    assert flat["variance_s"] == flat["kendall_tau_b"] == flat["sen_slope_per_year"] == 0
    assert flat["two_sided_p_value"] == 1
    assert "normal approximation" in flat["p_value_method"]
    for changes in ({"time_year": [1, 1, 2]}, {"observations": [1]},
                    {"observation_unit": "\n"}, {"significance_level": 1}):
        with pytest.raises(ValueError):
            _run("mann_kendall_sen", **changes)
