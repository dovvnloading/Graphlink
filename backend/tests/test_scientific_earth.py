"""Published geodetic, atmospheric and solar benchmarks plus physical/model guards.

Reference URLs, numerical precision, conventions and caveats:
docs/SCIENTIFIC_EARTH_REFERENCE.md. No network access or optional dependencies.
"""

import math

import pytest

from graphlink_plugins.scientific.core import readable_result, run_operation
from graphlink_plugins.scientific.packs import earth


def run(name, **updates):
    operation = next(op for op in earth.OPERATIONS if op.id == name)
    return run_operation("earth", operation, {**operation.example, **updates})


def test_original_three_operations_and_saved_receipts_remain_readable():
    assert [op.id for op in earth.OPERATIONS[:3]] == [
        "great_circle_distance", "geothermal_gradient", "seismic_distance"
    ]
    assert run("great_circle_distance")["values"] == pytest.approx({
        "distance_km": 6371.0088 * math.radians(2),
        "central_angle_deg": 2, "sphere_radius_km": 6371.0088,
    })
    assert run("geothermal_gradient")["values"] == {
        "gradient_c_per_km": 25, "depth_interval_m": 800, "temperature_change_c": 20,
    }
    assert run("seismic_distance")["values"] == {
        "path_length_km": 84, "p_travel_s": 14, "s_travel_s": 24,
    }
    for operation in earth.OPERATIONS[:3]:
        receipt = run(operation.id)
        assert readable_result({"version": 1, "latest": receipt}, "earth")


def test_new_operation_examples_and_units_are_usable_through_workbench():
    assert len({op.id for op in earth.OPERATIONS}) == len(earth.OPERATIONS)
    for op in earth.OPERATIONS[3:]:
        assert op.fields and all(field.unit for field in op.fields), op.id
        artifact = run(op.id)
        assert artifact["values"] and artifact["summary"]
        assert all(key.endswith(("_m", "_deg", "_km", "_s", "_ma", "_k", "_c", "_pa", "_kg_m3",
                                  "_m2", "_m_s", "_m3_s", "_nm", "_j", "_1", "_mw", "_min",
                                  "_per_mw")) or key in ("solution_method", "hemisphere")
                   for key in artifact["values"]), op.id
        assert readable_result({"version": 1, "latest": artifact}, "earth")
        if "table" in artifact:
            assert all("(" in label for label in artifact["table"]["columns"])


def test_vincenty_icsm_published_millimetre_and_hundredth_arcsecond():
    # ICSM GDA2020 Technical Manual v1.8, Table 5.1:
    # Flinders Peak -> Buninyong: 54972.271 m; 306°52'05.37"; 127°10'25.07".
    result = run("geodesic_inverse")["values"]
    assert result["distance_m"] == pytest.approx(54972.271, abs=0.0005)
    assert result["initial_bearing_deg"] == pytest.approx(306 + 52 / 60 + 5.37 / 3600,
                                                         abs=0.005 / 3600)
    assert result["reverse_bearing_deg"] == pytest.approx(127 + 10 / 60 + 25.07 / 3600,
                                                         abs=0.005 / 3600)
    assert result["solution_method"].startswith("Vincenty inverse")


def test_geodesic_near_antipodes_uses_ellipsoid_not_sphere():
    antipode = run("geodesic_inverse", latitude_a_deg=0, longitude_a_deg=0,
                   latitude_b_deg=0, longitude_b_deg=180)["values"]
    # WGS84 meridian pole-to-pole is 20,003,931.459 m (half meridian).
    assert antipode["distance_m"] == pytest.approx(20003931.459, abs=0.01)
    assert "fallback" in antipode["solution_method"]
    nearly = run("geodesic_inverse", latitude_a_deg=0, longitude_a_deg=0,
                 latitude_b_deg=0.01, longitude_b_deg=179.999)["values"]
    assert math.isfinite(nearly["distance_m"])
    assert nearly["distance_m"] < antipode["distance_m"]
    assert "fallback" in nearly["solution_method"]
    coincident = run("geodesic_inverse", latitude_b_deg=earth.OPERATIONS[3].example["latitude_a_deg"],
                     longitude_b_deg=earth.OPERATIONS[3].example["longitude_a_deg"])["values"]
    assert coincident["distance_m"] == 0
    with pytest.raises(ValueError, match="Latitude"):
        run("geodesic_inverse", latitude_a_deg=91)


def test_utm_published_zone_13_and_roundtrips_in_both_hemispheres():
    # WGS84 at 40°N, 105°W: UTM 13N E=500000 m N=4427757.219 m.
    result = run("utm_forward")["values"]
    assert result["zone_1"] == 13
    assert result["hemisphere"] == "N"
    assert result["easting_m"] == pytest.approx(500000, abs=0.001)
    assert result["northing_m"] == pytest.approx(4427757.219, abs=0.001)
    for latitude, longitude in ((40, -105), (-33.8688, 151.2093), (0, 3),
                                (60, 6), (75, 19), (-79.9, -45), (83.5, 25)):
        utm = run("utm_forward", latitude_deg=latitude, longitude_deg=longitude)["values"]
        geo = run("utm_inverse", zone_1=utm["zone_1"], hemisphere=utm["hemisphere"],
                  easting_m=utm["easting_m"], northing_m=utm["northing_m"])["values"]
        assert geo["latitude_deg"] == pytest.approx(latitude, abs=2e-6)
        assert geo["longitude_deg"] == pytest.approx(longitude, abs=2e-6)
    assert run("utm_forward", latitude_deg=60, longitude_deg=6)["values"]["zone_1"] == 32
    assert run("utm_forward", latitude_deg=75, longitude_deg=19)["values"]["zone_1"] == 33
    with pytest.raises(ValueError, match="UTM covers"):
        run("utm_forward", latitude_deg=85)
    with pytest.raises(ValueError, match="zone"):
        run("utm_inverse", zone_1=0)
    with pytest.raises(ValueError, match="hemisphere"):
        run("utm_inverse", hemisphere="n")


def test_radiometric_ages_and_half_life_standard_uncertainty():
    result = run("radiometric_age")["values"]
    assert result["age_ma"] == 1250
    assert result["age_standard_uncertainty_ma"] == 5
    assert run("radiometric_age", radiogenic_daughter_ratio_1=0)["values"]["age_ma"] == 0
    assert run("radiometric_age", parent_ratio_1=2, radiogenic_daughter_ratio_1=6,
               half_life_ma=10, half_life_standard_uncertainty_ma=1)["values"]["age_ma"] == 20
    with pytest.raises(ValueError, match="positive parent"):
        run("radiometric_age", parent_ratio_1=0)


def test_signed_darcy_discharge_and_pore_velocity():
    result = run("darcy_flow")["values"]
    assert result == pytest.approx({"hydraulic_gradient_1": -0.01, "darcy_flux_m_s": 1e-6,
                                    "discharge_m3_s": 2e-5, "pore_velocity_m_s": 4e-6})
    negative = run("darcy_flow", head_upstream_m=9, head_downstream_m=10)["values"]
    assert negative["discharge_m3_s"] == pytest.approx(-2e-5)
    with pytest.raises(ValueError, match="porosity"):
        run("darcy_flow", effective_porosity_1=0)


@pytest.mark.parametrize(("u", "expected", "digits"), [
    (0.1, 1.823, 0.0005), (0.5, 0.5598, 0.00005),
    (1, 0.2194, 0.00005), (5, 0.001148, 0.0000005),
])
def test_theis_published_well_function_values(u, expected, digits):
    # PE-Calc published W(u)=E1(u) table, displayed digits.
    values = run("theis_drawdown", pumping_rate_m3_s=0.02, transmissivity_m2_s=1,
                 storativity_1=u / 10, radial_distance_m=2,
                 time_since_pumping_s=0.1)["values"]
    assert values["theis_u_1"] == pytest.approx(u)
    assert values["well_function_e1_1"] == pytest.approx(expected, abs=digits)
    assert values["drawdown_m"] == pytest.approx(0.02 / (4 * math.pi) * values["well_function_e1_1"])


def test_theis_domain_and_late_time_asymptotic():
    assert run("theis_drawdown", pumping_rate_m3_s=0)["values"]["drawdown_m"] == 0
    with pytest.raises(ValueError, match="storativity"):
        run("theis_drawdown", storativity_1=0)
    assert earth._expint_e1(1e-8) == pytest.approx(17.843465089050834, rel=1e-12)


def test_moment_magnitude_inverse_and_energy_estimate():
    result = run("moment_magnitude", seismic_moment_nm=10**18.1)["values"]
    assert result["moment_magnitude_mw"] == pytest.approx(6, abs=1e-13)
    assert result["estimated_radiated_energy_j"] == pytest.approx(10**13.8)
    opposite = run("seismic_moment", moment_magnitude_mw=6)["values"]
    assert opposite["seismic_moment_nm"] == pytest.approx(10**18.1)
    with pytest.raises(ValueError, match="positive"):
        run("moment_magnitude", seismic_moment_nm=0)


def test_gutenberg_richter_mle_completeness_and_fit_table():
    result = run("gutenberg_richter", magnitudes_mw=[1.8, 2.0, 2.1, 2.2, 2.3],
                 completeness_magnitude_mw=2, bin_width_mw=0.1, target_magnitude_mw=2.2)
    expected_b = math.log10(math.e) / (0.15 + 0.05)
    assert result["values"]["b_value_per_mw"] == pytest.approx(expected_b)
    assert result["values"]["a_intercept_log10_count_1"] == pytest.approx(math.log10(4) + 2 * expected_b)
    assert result["values"]["events_below_completeness_1"] == 1
    assert result["values"]["b_approx_standard_error_per_mw"] == pytest.approx(expected_b / 2)
    assert result["values"]["observed_count_at_target_1"] == 2
    assert result["values"]["predicted_count_at_target_1"] == pytest.approx(4 * 10**(-0.2 * expected_b))
    assert result["table"]["rows"][0][1] == 4
    with pytest.raises(ValueError, match="two events"):
        run("gutenberg_richter", magnitudes_mw=[1, 2])


@pytest.mark.parametrize(("geopotential_m", "temperature_k", "pressure_pa", "precision_pa"), [
    (0, 288.15, 101325.0, 0.05),
    (11000, 216.65, 22632.06, 0.005),
    (20000, 216.65, 5474.889, 0.0005),
    (32000, 228.65, 868.0187, 0.00005),
    (47000, 270.65, 110.9063, 0.00005),
    (51000, 270.65, 66.93887, 0.000005),
    (71000, 214.65, 3.956420, 0.0000005),
])
def test_isa_1976_published_layer_table(geopotential_m, temperature_k, pressure_pa, precision_pa):
    # 1976 Standard Atmosphere Table 4 at geopotential altitudes; the field
    # accepts *geometric* z, so convert by the published 6,356,766 m radius.
    radius = 6356766
    geometric = radius * geopotential_m / (radius - geopotential_m)
    values = run("isa_atmosphere", geometric_altitude_m=geometric)["values"]
    assert values["geopotential_altitude_m"] == pytest.approx(geopotential_m, abs=1e-7)
    assert values["temperature_k"] == pytest.approx(temperature_k, abs=1e-8)
    assert values["pressure_pa"] == pytest.approx(pressure_pa, abs=precision_pa)
    assert values["density_kg_m3"] == pytest.approx(values["pressure_pa"] / (8314.32 / 28.9644 * temperature_k))


def test_isa_sea_level_density_and_coverage():
    assert run("isa_atmosphere", geometric_altitude_m=0)["values"]["density_kg_m3"] == pytest.approx(
        1.225, abs=0.0005)
    with pytest.raises(ValueError, match="86000"):
        run("isa_atmosphere", geometric_altitude_m=87000)


def test_solar_position_noaa_approximation_and_published_solar_example():
    # Golden CO, 2003-10-17 12:30:30 MST = 19:30:30 UTC.
    # NREL SPA publishes azimuth 194.34024° and apparent elevation 39.88838°.
    # NOAA Meeus approximation (standard refraction, not NREL site pressure)
    # reproduces these values at *hundredth-degree* resolution, NOT the SPA's
    # five decimals; geometric elevation matches the published 39.872°.
    result = run("solar_position", timestamp_iso8601="2003-10-17T19:30:30Z",
                 latitude_deg=39.742476, longitude_deg=-105.1786)["values"]
    assert result["azimuth_deg"] == pytest.approx(194.34024, abs=0.005)
    assert result["elevation_deg"] == pytest.approx(39.88838, abs=0.005)
    assert result["geometric_elevation_deg"] == pytest.approx(39.872, abs=0.0005)
    same_instant = run("solar_position", timestamp_iso8601="2003-10-17T12:30:30-07:00",
                       latitude_deg=39.742476, longitude_deg=-105.1786)["values"]
    assert same_instant == pytest.approx(result)
    with pytest.raises(ValueError, match="timezone-aware"):
        run("solar_position", timestamp_iso8601="2003-10-17T12:30:30")
    with pytest.raises(ValueError, match="1901"):
        run("solar_position", timestamp_iso8601="2100-03-20T12:00:00Z")


def test_solar_position_cardinal_convention_and_polar_undefined_azimuth():
    equinox = run("solar_position", timestamp_iso8601="2024-03-20T12:00:00Z",
                  latitude_deg=0, longitude_deg=0)["values"]
    assert equinox["geometric_elevation_deg"] > 88
    assert 0 <= equinox["azimuth_deg"] < 360
    pole = run("solar_position", latitude_deg=90)["values"]
    assert pole["azimuth_deg"] == 0


def test_karney_antimeridian_inverse_and_undefined_polar_bearings():
    # Independent WGS84 GeographicLib/Karney inverse oracle; shortest dateline arc.
    crossing = run("geodesic_inverse", latitude_a_deg=10, longitude_a_deg=179.999,
                   latitude_b_deg=10, longitude_b_deg=-179.999)["values"]
    assert crossing["distance_m"] == pytest.approx(219.278728137, abs=.001)
    assert crossing["initial_bearing_deg"] == pytest.approx(89.9998263518, abs=.00001)
    antipodal_poles = run("geodesic_inverse", latitude_a_deg=-90, longitude_a_deg=0,
                          latitude_b_deg=90, longitude_b_deg=30)["values"]
    assert antipodal_poles["distance_m"] == pytest.approx(20003931.4586, abs=.005)
    assert antipodal_poles["initial_bearing_deg"] == 0
    assert antipodal_poles["reverse_bearing_deg"] == 0
    assert "undefined" in antipodal_poles["solution_method"]


def test_utm_nga_zone_boundaries_and_antimeridian():
    cases = [
        (0, -180, 1), (0, 180, 60), (60, 2.999999, 31),
        (60, 3, 32), (60, 11.999999, 32), (60, 12, 33),
        (75, 8.999999, 31), (75, 9, 33), (75, 20.999999, 33),
        (75, 21, 35), (75, 32.999999, 35), (75, 33, 37),
    ]
    for latitude, longitude, zone in cases:
        assert run("utm_forward", latitude_deg=latitude,
                   longitude_deg=longitude)["values"]["zone_1"] == zone
    with pytest.raises(ValueError, match="UTM covers"):
        run("utm_forward", latitude_deg=-80.0001)


def test_large_finite_radiometric_age_does_not_overflow_intermediate():
    age = run("radiometric_age", parent_ratio_1=1, radiogenic_daughter_ratio_1=1e-300,
              half_life_ma=1.5e308, half_life_standard_uncertainty_ma=1.5e308)["values"]
    expected = 1.5e308 * (math.log1p(1e-300) / math.log(2))
    assert age["age_ma"] == pytest.approx(expected, rel=1e-12)
    assert age["age_standard_uncertainty_ma"] == pytest.approx(expected, rel=1e-12)


def test_large_finite_theis_geometry_keeps_representable_well_argument():
    values = run("theis_drawdown", pumping_rate_m3_s=.02,
                 transmissivity_m2_s=1e200, storativity_1=1e-10,
                 radial_distance_m=1e200, time_since_pumping_s=1e200)["values"]
    assert values["theis_u_1"] == pytest.approx(2.5e-11, rel=1e-10)
    assert values["well_function_e1_1"] > 0
    assert values["drawdown_m"] > 0


def test_seismic_moment_uses_representable_binary64_limits():
    # Previous artificial 10^300 cutoff rejected finite, well-scaled values.
    values = run("seismic_moment", moment_magnitude_mw=197)["values"]
    assert values["seismic_moment_nm"] == pytest.approx(10**304.6, rel=1e-12)
    assert run("moment_magnitude", seismic_moment_nm=values["seismic_moment_nm"]
               )["values"]["moment_magnitude_mw"] == pytest.approx(197, abs=1e-12)
    # A positive scalar moment must not silently yield a zero energy estimate.
    with pytest.raises(ValueError, match="radiated energy"):
        run("moment_magnitude", seismic_moment_nm=1e-323)


def test_gutenberg_richter_decimal_bin_edges_and_completeness_guard():
    # 0.3/0.1 is 2.999999... in binary64; the last bin must survive.
    result = run("gutenberg_richter", magnitudes_mw=[0, .1, .2, .3],
                 completeness_magnitude_mw=0, bin_width_mw=.1,
                 target_magnitude_mw=.3)
    assert [row[0] for row in result["table"]["rows"]] == [0, .1, .2, .3]
    assert [row[1] for row in result["table"]["rows"]] == [4, 3, 2, 1]
    with pytest.raises(ValueError, match="Target magnitude.*completeness"):
        run("gutenberg_richter", target_magnitude_mw=1.9)
