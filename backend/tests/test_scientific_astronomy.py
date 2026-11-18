"""Published ephemeris/cosmology benchmarks and Astronomy pack regression tests."""

import math
from typing import get_type_hints

import pytest

from graphlink_plugins.scientific.core import Operation, run_operation
from graphlink_plugins.scientific.packs import astronomy


def _run(name, **overrides):
    op = next(op for op in astronomy.OPERATIONS if op.id == name)
    return run_operation("astronomy", op, {**op.example, **overrides})["values"]


def test_catalogue_and_original_results_remain_compatible():
    operations = astronomy.OPERATIONS
    assert get_type_hints(astronomy)["OPERATIONS"] == tuple[Operation, ...]
    assert [op.id for op in operations[:3]] == ["parallax_distance", "distance_modulus", "blackbody_peak"]
    assert len({op.id for op in operations}) == len(operations)
    assert _run("parallax_distance", parallax_mas=100) == pytest.approx({
        "distance_pc": 10,
        "distance_ly": 10 * astronomy._LIGHT_YEARS_PER_PARSEC,
    })
    assert _run("distance_modulus") == pytest.approx({"true_distance_modulus_mag": 5, "distance_pc": 100})
    assert _run("blackbody_peak", temperature_k=5772) == pytest.approx({
        "peak_wavelength_nm": 2.897771955e6 / 5772,
    })


def test_all_examples_validate_and_give_finite_results_with_labeled_units():
    assert len(astronomy.OPERATIONS) == 12
    for op in astronomy.OPERATIONS:
        assert all(field.unit for field in op.fields), op.id
        assert set(op.example) == {field.key for field in op.fields}
        result = run_operation("astronomy", op, op.example)
        assert result["operationId"] == op.id
        assert result["provenance"]["packId"] == "astronomy"
        assert result["provenance"]["resultHash"]
        assert result["values"]
        for key, value in result["values"].items():
            assert key.endswith(("_jd", "_deg", "_hours", "_days", "_mpc", "_gyr", "_au", "_au_per_day",
                                 "_jy", "_mag", "_ppm", "_fraction", "_earth", "_star", "_ratio", "airmass", "_pc", "_ly", "_nm")), (op.id, key)
            assert isinstance(value, (int, float)) and math.isfinite(value), (op.id, key)


def test_meeus_julian_day_sputnik_example_7a():
    # Meeus, Astronomical Algorithms (2nd ed.), Example 7.a: 1957-10-04.81.
    result = _run("julian_sidereal_time", utc_iso="1957-10-04T19:26:24Z")
    assert result["julian_date_jd"] == pytest.approx(2436116.31, abs=2e-8)


@pytest.mark.parametrize("instant, expected_jd, expected_gmst_hours", [
    ("1987-04-10T00:00:00Z", 2446895.5, 13 + 10/60 + 46.3668/3600),
    ("1987-04-10T19:21:00Z", 2446896.30625, 8 + 34/60 + 57.0896/3600),
    ("2000-01-01T12:00:00+00:00", 2451545.0, 280.46061837 / 15),
])
def test_meeus_sidereal_examples_11a_11b(instant, expected_jd, expected_gmst_hours):
    # Meeus Chapter 11: GMST in sexagesimal hours, to 0.0001 sidereal second.
    got = _run("julian_sidereal_time", utc_iso=instant, observer_longitude_deg_east=0)
    assert got["julian_date_jd"] == pytest.approx(expected_jd, abs=2e-8)
    assert got["greenwich_mean_sidereal_hours"] == pytest.approx(expected_gmst_hours, abs=0.00005/3600)


def test_sidereal_local_longitude_and_utc_validation():
    z = _run("julian_sidereal_time", observer_longitude_deg_east=0)
    west = _run("julian_sidereal_time", observer_longitude_deg_east=-75)
    assert west["local_mean_sidereal_hours"] == pytest.approx((z["greenwich_mean_sidereal_hours"] - 5) % 24)
    for invalid in ("1987-04-10T19:21:00", "not a date", "2023-02-30T00:00:00Z", "1987-04-10T19:21:00+01:00"):
        with pytest.raises(ValueError):
            _run("julian_sidereal_time", utc_iso=invalid)


def test_meeus_kepler_example_22a_newton_iteration():
    # Meeus, Astronomical Algorithms Example 22.a: e=0.1, M=5 degrees.
    got = _run("keplerian_orbit", eccentricity=0.1, mean_anomaly_epoch_deg=5,
               epoch_jd=2451545, target_jd=2451545)
    assert got["eccentric_anomaly_deg"] == pytest.approx(5.554589, abs=5e-7)
    assert got["true_anomaly_deg"] == pytest.approx(6.139762, abs=5e-7)


def test_meeus_high_eccentricity_kepler_example():
    # Meeus, Example 22.b: e=0.99, M=2 degrees, E=32.361007 degrees.
    got = _run("keplerian_orbit", eccentricity=0.99, mean_anomaly_epoch_deg=2,
               epoch_jd=2451545, target_jd=2451545)
    assert got["eccentric_anomaly_deg"] == pytest.approx(32.361007, abs=5e-7)
    assert got["true_anomaly_deg"] == pytest.approx(152.542134, abs=5e-7)


@pytest.mark.parametrize("e, angle", [(0, 0), (0.9, 179), (0.999999, 0.00001), (0.999999, 359.99999),
                                       (0.999999, 180), (0.01, 359.99)])
def test_kepler_residual_and_angular_periodicity(e, angle):
    data = _run("keplerian_orbit", eccentricity=e, mean_anomaly_epoch_deg=angle,
                epoch_jd=2451545, target_jd=2451545)
    anomaly = math.radians(data["eccentric_anomaly_deg"])
    residual = (anomaly - e * math.sin(anomaly) - math.radians(angle) + math.pi) % math.tau - math.pi
    assert abs(residual) < 1e-11
    assert 0 <= data["true_anomaly_deg"] < 360


def test_circular_orbit_propagation_velocity_geometry():
    # 1 AU circular orbit in the reference plane: x=1 at epoch, v_y=n.
    start = _run("keplerian_orbit", eccentricity=0, inclination_deg=0, ascending_node_deg=0,
                 argument_periapsis_deg=0, mean_anomaly_epoch_deg=0,
                 epoch_jd=2451545, target_jd=2451545)
    assert [start[k] for k in ("x_au", "y_au", "z_au")] == pytest.approx([1, 0, 0], abs=1e-13)
    assert start["vx_au_per_day"] == pytest.approx(0, abs=1e-13)
    assert start["vy_au_per_day"] == pytest.approx(math.tau / start["orbital_period_days"])
    quarter = _run("keplerian_orbit", eccentricity=0, inclination_deg=0, ascending_node_deg=0,
                   argument_periapsis_deg=0, mean_anomaly_epoch_deg=0,
                   epoch_jd=2451545, target_jd=2451545 + start["orbital_period_days"] / 4)
    assert quarter["x_au"] == pytest.approx(0, abs=2e-9)
    assert quarter["y_au"] == pytest.approx(1, abs=2e-9)
    assert quarter["vx_au_per_day"] == pytest.approx(-start["vy_au_per_day"])
    assert quarter["vy_au_per_day"] == pytest.approx(0, abs=2e-11)
    assert quarter["orbital_period_days"] == pytest.approx(365.2569, abs=0.0002)


def test_kepler_rotation_preserves_speed_and_radius():
    face = _run("keplerian_orbit", inclination_deg=0, ascending_node_deg=0, argument_periapsis_deg=0)
    tilt = _run("keplerian_orbit", inclination_deg=101, ascending_node_deg=97, argument_periapsis_deg=72)
    assert face["radius_au"] == pytest.approx(tilt["radius_au"])
    assert face["speed_au_per_day"] == pytest.approx(tilt["speed_au_per_day"])
    assert math.hypot(tilt["x_au"], tilt["y_au"], tilt["z_au"]) == pytest.approx(tilt["radius_au"])
    assert math.hypot(tilt["vx_au_per_day"], tilt["vy_au_per_day"], tilt["vz_au_per_day"]) == pytest.approx(tilt["speed_au_per_day"])


def test_iau_galactic_center_and_north_pole_at_j2000():
    # IAU/J2000 Galactic coordinate landmarks (ICRS alignment <0.001 deg).
    center = _run("coordinate_frames", source_frame="equatorial", target_frame="galactic",
                  utc_iso="2000-01-01T12:00:00Z", source_longitude_deg=266.4049948,
                  source_latitude_deg=-28.93617396)
    assert min(center["target_longitude_deg"], 360 - center["target_longitude_deg"]) < 2e-6
    assert center["target_latitude_deg"] == pytest.approx(0, abs=2e-6)
    pole = _run("coordinate_frames", source_frame="equatorial", target_frame="galactic",
                utc_iso="2000-01-01T12:00:00Z", source_longitude_deg=192.85948,
                source_latitude_deg=27.12825)
    assert pole["target_latitude_deg"] == pytest.approx(90, abs=2e-5)


def test_mean_obliquity_j2000_and_ecliptic_transform():
    # Meeus mean obliquity (2000.0): 23°26′21.448″.
    eps = 23 + 26/60 + 21.448/3600
    result = _run("coordinate_frames", source_frame="ecliptic", target_frame="equatorial",
                  utc_iso="2000-01-01T12:00:00Z", source_longitude_deg=90,
                  source_latitude_deg=0)
    assert result["target_longitude_deg"] == pytest.approx(90, abs=1e-10)
    assert result["target_latitude_deg"] == pytest.approx(eps, abs=1e-10)


@pytest.mark.parametrize("source,target", [("equatorial", "horizontal"), ("galactic", "equatorial"),
                                         ("ecliptic", "galactic"), ("horizontal", "ecliptic")])
def test_coordinate_round_trips_all_frame_pairs(source, target):
    values = {"utc_iso": "2026-03-04T01:25:00Z", "observer_latitude_deg": 35.2,
              "observer_longitude_deg_east": -105.3}
    out = _run("coordinate_frames", source_frame=source, target_frame=target,
               source_longitude_deg=129, source_latitude_deg=30, **values)
    back = _run("coordinate_frames", source_frame=target, target_frame=source,
                source_longitude_deg=out["target_longitude_deg"],
                source_latitude_deg=out["target_latitude_deg"], **values)
    assert back["target_longitude_deg"] == pytest.approx(129, abs=2e-7)
    assert back["target_latitude_deg"] == pytest.approx(30, abs=2e-7)


def test_horizontal_zenith_meridian_geometry():
    base = _run("julian_sidereal_time", utc_iso="2000-01-01T12:00:00Z", observer_longitude_deg_east=0)
    transit_ra = base["local_mean_sidereal_hours"] * 15
    got = _run("coordinate_frames", source_frame="equatorial", target_frame="horizontal",
               utc_iso="2000-01-01T12:00:00Z", observer_latitude_deg=30, observer_longitude_deg_east=0,
               source_longitude_deg=transit_ra, source_latitude_deg=0)
    assert got["target_latitude_deg"] == pytest.approx(60, abs=1e-8)
    assert got["target_longitude_deg"] == pytest.approx(180, abs=1e-8)


def test_kasten_young_airmass_and_extinction():
    # Kasten & Young 1989 Appl. Opt. 28, 4735, for apparent altitude.
    for altitude in (0, 10, 30, 45, 90):
        got = _run("airmass_extinction", apparent_altitude_deg=altitude, observed_mag=10,
                   extinction_coefficient_mag_per_airmass=0.2)
        expected = 1 / (math.sin(math.radians(altitude)) + 0.50572 * (altitude + 6.07995) ** -1.6364)
        assert got["relative_airmass"] == pytest.approx(expected, rel=1e-14)
        assert got["extinction_mag"] == pytest.approx(0.2 * expected)
        assert got["transmission_fraction"] == pytest.approx(10 ** (-0.4 * 0.2 * expected))
        assert got["above_atmosphere_mag"] == pytest.approx(10 - 0.2 * expected)
    assert _run("airmass_extinction", apparent_altitude_deg=0)["relative_airmass"] == pytest.approx(37.92, abs=0.01)


def test_flat_lcdm_z1_published_cosmology_calculator_values():
    # H0=70, Ωm=0.3, ΩΛ=0.7, z=1, cf. Wright (2006) / iCosmos
    # https://icosmos.co.uk/compare/ (printed to 2 decimal Mpc, 5 decimal Gyr).
    got = _run("flat_lcdm_distances", redshift=1, h0_km_s_mpc=70, omega_matter=0.3)
    assert got["comoving_distance_mpc"] == pytest.approx(3303.83, abs=0.005)
    assert got["angular_diameter_distance_mpc"] == pytest.approx(1651.91, abs=0.005)
    assert got["luminosity_distance_mpc"] == pytest.approx(6607.66, abs=0.005)
    assert got["lookback_time_gyr"] == pytest.approx(7.71534, abs=0.000005)


def test_flat_lcdm_endpoint_analytic_solutions_and_scaling():
    # Flat Ωm=0 de Sitter and Ωm=1 Einstein-de Sitter exact integrals.
    for om in (0, 1):
        z, h0 = 2.0, 70.0
        got = _run("flat_lcdm_distances", omega_matter=om, redshift=z, h0_km_s_mpc=h0)
        dc = 299792.458 / h0 * (z if om == 0 else 2 * (1 - 1 / math.sqrt(1 + z)))
        t_hubble = astronomy._MPC_KM / (h0 * astronomy._JULIAN_YEAR_S * 1e9)
        lookback = t_hubble * (math.log1p(z) if om == 0 else 2 / 3 * (1 - (1+z)**-1.5))
        assert got["comoving_distance_mpc"] == pytest.approx(dc, rel=3e-10)
        assert got["lookback_time_gyr"] == pytest.approx(lookback, rel=3e-10)
        assert got["luminosity_distance_mpc"] == pytest.approx(dc * 3)
        assert got["angular_diameter_distance_mpc"] == pytest.approx(dc / 3)
    zero = _run("flat_lcdm_distances", redshift=0)
    assert set(zero.values()) == {0}


def test_ab_flux_magnitude_and_colour():
    assert _run("ab_flux_to_magnitude", flux_density_jy=3631)["ab_magnitude_mag"] == pytest.approx(0, abs=1e-14)
    assert _run("ab_flux_to_magnitude", flux_density_jy=36.31e-6)["ab_magnitude_mag"] == pytest.approx(20)
    assert _run("ab_magnitude_to_flux", ab_magnitude_mag=20)["flux_density_jy"] == pytest.approx(36.31e-6)
    assert _run("ab_colour_index", first_ab_mag=21, second_ab_mag=20) == pytest.approx({
        "colour_index_mag": 1, "first_to_second_flux_ratio": 10 ** -0.4,
    })
    for magnitude in (-10, 0, 10, 25, 35):
        flux = _run("ab_magnitude_to_flux", ab_magnitude_mag=magnitude)["flux_density_jy"]
        assert _run("ab_flux_to_magnitude", flux_density_jy=flux)["ab_magnitude_mag"] == pytest.approx(magnitude)


def test_exoplanet_transit_geometry_and_radius():
    # 1% uniform disk depth => Rp/Rstar=0.1; a central, circular transit.
    got = _run("exoplanet_transit", out_of_transit_flux_relative=1, in_transit_flux_relative=0.99,
               stellar_radius_solar=1, impact_parameter_stellar_radii=0, semimajor_axis_au=0.05,
               orbital_period_days=4)
    assert got["transit_depth_fraction"] == pytest.approx(0.01)
    assert got["transit_depth_ppm"] == pytest.approx(10000)
    assert got["radius_ratio_planet_star"] == pytest.approx(0.1)
    assert got["planet_radius_earth"] == pytest.approx(0.1 * 6.957e8 / 6.3781e6)
    a_over_r = 0.05 * astronomy._AU_M / astronomy._R_SUN_M
    t14 = 4 * 24 / math.pi * math.asin(1.1 / a_over_r)
    t23 = 4 * 24 / math.pi * math.asin(0.9 / a_over_r)
    assert got["total_duration_hours"] == pytest.approx(t14)
    assert got["full_duration_hours"] == pytest.approx(t23)
    assert got["ingress_duration_hours"] == pytest.approx((t14 - t23) / 2)
    grazing = _run("exoplanet_transit", impact_parameter_stellar_radii=0.95)
    assert grazing["full_duration_hours"] == 0
    assert grazing["total_duration_hours"] > 0


@pytest.mark.parametrize("name,overrides", [
    ("keplerian_orbit", {"semimajor_axis_au": 0}),
    ("keplerian_orbit", {"eccentricity": -0.1}),
    ("keplerian_orbit", {"eccentricity": 1}),
    ("keplerian_orbit", {"central_mass_solar": 0}),
    ("keplerian_orbit", {"inclination_deg": 181}),
    ("coordinate_frames", {"source_frame": "invalid"}),
    ("coordinate_frames", {"source_latitude_deg": 91}),
    ("coordinate_frames", {"utc_iso": "1500-01-01T12:00:00Z"}),
    ("airmass_extinction", {"apparent_altitude_deg": -0.01}),
    ("airmass_extinction", {"extinction_coefficient_mag_per_airmass": -1}),
    ("flat_lcdm_distances", {"redshift": -0.1}),
    ("flat_lcdm_distances", {"h0_km_s_mpc": 0}),
    ("flat_lcdm_distances", {"omega_matter": 1.1}),
    ("ab_flux_to_magnitude", {"flux_density_jy": 0}),
    ("exoplanet_transit", {"in_transit_flux_relative": 1}),
    ("exoplanet_transit", {"stellar_radius_solar": -1}),
    ("exoplanet_transit", {"semimajor_axis_au": 0.001}),
    ("exoplanet_transit", {"impact_parameter_stellar_radii": 2}),
])
def test_domain_errors_are_actionable(name, overrides):
    with pytest.raises(ValueError):
        _run(name, **overrides)
