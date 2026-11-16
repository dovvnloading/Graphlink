"""Reproducible measurements compared to constructed models and independent numerical oracles."""

import math
from decimal import Decimal, localcontext
from random import Random

import numpy as np
import pytest

from graphlink_plugins.scientific.catalog import operations
from graphlink_plugins.scientific.core import run_operation

TRIALS = 24


def run(pack, operation, **inputs):
    op = next(item for item in operations(pack) if item.id == operation)
    return run_operation(pack, op, inputs)


def test_optical_round_trips_and_isotope_mixture():
    rng = Random(291)
    for _ in range(TRIALS):
        coefficient, path, concentration = 10 ** rng.uniform(1, 4), rng.uniform(0.1, 10), 10 ** rng.uniform(-9, -5)
        result = run(
            "chemistry",
            "beer_lambert",
            absorbance=coefficient * path * concentration,
            molar_absorptivity=coefficient,
            path_length_cm=path,
        )
        assert result["values"]["concentration_mol_l"] == pytest.approx(concentration, rel=1e-14, abs=0)
        absorbances = np.array([rng.random() * 3 for _ in range(9)])
        result = run(
            "chemistry",
            "transmission_spectrum",
            wavelength_nm=list(range(500, 509)),
            transmission_percent=(100 * 10 ** (-absorbances)).tolist(),
        )
        assert result["plot"]["y"] == pytest.approx(absorbances, abs=1e-14)
        assert result["values"]["peak_wavelength_nm"] == 500 + np.argmax(absorbances)
        fraction = rng.random()
        masses = [rng.uniform(50, 100), rng.uniform(100, 150)]
        result = run(
            "chemistry",
            "isotope_average",
            isotope_mass_u=masses,
            abundance_percent=[fraction * 100, (1 - fraction) * 100],
        )
        assert result["values"]["mean_atomic_mass_u"] == pytest.approx(masses[1] + (masses[0] - masses[1]) * fraction)


def test_ecological_counts_against_histograms_sets_and_pooled_areas():
    rng = Random(887)
    for _ in range(TRIALS):
        site_a = [rng.randrange(12) for _ in range(rng.randint(4, 16))]
        site_b = [rng.randrange(12) for _ in range(rng.randint(4, 16))]
        a, b = [[site.count(taxon) for taxon in range(12)] for site in (site_a, site_b)]
        result = run("biology", "community_diversity", counts=a)["values"]
        # Counting ordered unequal draws directly is independent of the diversity formula.
        different = sum(i != j for i in site_a for j in site_a)
        entropy = np.mean([math.log(len(site_a) / site_a.count(taxon)) for taxon in site_a])
        assert result == pytest.approx(
            {
                "individuals": len(site_a),
                "richness": len(set(site_a)),
                "shannon_nats": entropy,
                "gini_simpson": different / len(site_a) ** 2,
            }
        )
        overlap = run("biology", "community_overlap", site_a_counts=a, site_b_counts=b)["values"]
        assert overlap["jaccard_similarity"] == pytest.approx(
            len(set(site_a) & set(site_b)) / len(set(site_a) | set(site_b))
        )
        assert overlap["sorensen_similarity"] == pytest.approx(
            2 * len(set(site_a) & set(site_b)) / (len(set(site_a)) + len(set(site_b)))
        )
        areas = [rng.uniform(0.1, 2) for _ in a]
        density = run("biology", "quadrat_density", counts=a, area_m2=areas)
        assert density["values"]["pooled_density_per_m2"] == pytest.approx(
            np.average(np.array(a) / areas, weights=areas)
        )
        assert [row[-1] for row in density["table"]["rows"]] == pytest.approx(np.array(a) / areas)


def test_geoscience_against_unit_vector_geodesics_and_forward_models():
    rng = Random(177)
    for _ in range(TRIALS):
        latitudes = [rng.uniform(-90, 90) for _ in range(2)]
        longitudes = [rng.uniform(-180, 180) for _ in range(2)]
        phi, lam = np.radians(latitudes), np.radians(longitudes)
        a, b = np.column_stack([np.cos(phi) * np.cos(lam), np.cos(phi) * np.sin(lam), np.sin(phi)])
        angle = np.arctan2(np.linalg.norm(np.cross(a, b)), np.dot(a, b))
        distance = run(
            "earth",
            "great_circle_distance",
            latitude_a=latitudes[0],
            latitude_b=latitudes[1],
            longitude_a=longitudes[0],
            longitude_b=longitudes[1],
        )["values"]
        assert distance["distance_km"] == pytest.approx(angle * 6371.0088, rel=2e-14)
        gradient, baseline, depth = rng.uniform(-30, 80), rng.uniform(-10, 15), rng.uniform(100, 800)
        temperature = lambda z: baseline + z * gradient / 1000
        result = run(
            "earth",
            "geothermal_gradient",
            shallow_depth_m=depth,
            deep_depth_m=3 * depth,
            shallow_temperature_c=temperature(depth),
            deep_temperature_c=temperature(3 * depth),
        )
        assert result["values"]["gradient_c_per_km"] == pytest.approx(gradient)
        p, s, distance = rng.uniform(6, 8), rng.uniform(3, 4), rng.uniform(20, 200)
        result = run(
            "earth", "seismic_distance", s_minus_p_s=distance / s - distance / p, p_speed_km_s=p, s_speed_km_s=s
        )
        assert result["values"] == pytest.approx(
            {"path_length_km": distance, "p_travel_s": distance / p, "s_travel_s": distance / s}
        )


def test_integrated_water_and_temperature_observations():
    rng = Random(782)
    for _ in range(TRIALS):
        durations = np.array([rng.uniform(1, 500) for _ in range(8)])
        volumes = np.array([rng.uniform(0, 2000) for _ in durations])
        concentrations = np.array([rng.uniform(0, 100) for _ in durations])
        result = run(
            "environment",
            "flow_weighted_concentration",
            duration_s=durations.tolist(),
            flow_m3_s=(volumes / durations).tolist(),
            concentration_mg_l=concentrations.tolist(),
        )
        assert result["values"] == pytest.approx(
            {
                "total_water_m3": volumes.sum(),
                "total_constituent_kg": volumes @ concentrations / 1000,
                "mean_concentration_mg_l": np.average(concentrations, weights=volumes),
            }
        )
        rain, area, coefficient = rng.uniform(0.1, 150), rng.uniform(0.01, 100), rng.uniform(0, 1.2)
        rainfall = (rain / 1000) * (area * 1000000)
        runoff = run(
            "environment", "rainfall_runoff", rainfall_mm=rain, area_km2=area, runoff_volume_m3=rainfall * coefficient
        )
        assert runoff["values"] == pytest.approx(
            {"runoff_depth_mm": rain * coefficient, "rainfall_volume_m3": rainfall, "runoff_coefficient": coefficient}
        )
        midpoints = np.array([rng.uniform(-15, 30) for _ in range(12)])
        half_ranges = np.array([rng.uniform(0, 6) for _ in midpoints])
        base = rng.uniform(-5, 15)
        result = run(
            "environment",
            "degree_days",
            daily_min_c=(midpoints - half_ranges).tolist(),
            daily_max_c=(midpoints + half_ranges).tolist(),
            base_c=base,
        )
        cumulative = np.cumsum(np.maximum(midpoints - base, 0))
        assert result["plot"]["y"] == pytest.approx(cumulative)
        assert result["values"]["growing_degree_days_c"] == pytest.approx(cumulative[-1])


def test_material_response_against_constructed_specimens():
    rng = Random(615)
    for _ in range(TRIALS):
        volume, density = rng.uniform(1, 50), rng.uniform(0.1, 5)
        mass = volume * density
        result = run("materials", "bulk_density", dry_mass_g=mass, bulk_volume_cm3=volume)
        assert result["values"] == pytest.approx({"bulk_density_g_cm3": density, "bulk_density_kg_m3": density * 1000})
        liquid, porosity = rng.uniform(0.7, 1.3), rng.uniform(0, 0.85)
        wet_mass = mass + volume * porosity * liquid
        result = run(
            "materials",
            "apparent_porosity",
            dry_mass_g=mass,
            saturated_mass_g=wet_mass,
            suspended_mass_g=wet_mass - volume * liquid,
        )
        assert result["values"] == pytest.approx(
            {"apparent_open_porosity_percent": 100 * porosity, "absorbed_liquid_g": volume * porosity * liquid}
        )
        length, area, modulus = rng.uniform(2, 15), rng.uniform(1, 8), rng.uniform(1000, 20000)
        strain = np.array([rng.uniform(0, 0.02) for _ in range(5)])
        result = run(
            "materials",
            "tensile_response",
            original_area_mm2=area,
            gauge_length_mm=length,
            extension_mm=(strain * length).tolist(),
            force_n=(area * modulus * strain).tolist(),
        )
        assert result["plot"]["x"] == pytest.approx(strain)
        assert result["plot"]["y"] == pytest.approx(modulus * strain)
        assert result["values"]["engineering_strain_at_first_peak"] == pytest.approx(max(strain))


def test_signal_transforms_against_numpy_and_conservation_of_energy():
    rng = Random(8762)
    for _ in range(TRIALS):
        n = rng.randrange(5, 66)
        rate = rng.uniform(1, 1000)
        samples = np.array([rng.uniform(-10, 10) for _ in range(n)])
        width = rng.choice(range(1, n + 1, 2))
        filtered = run("signals", "moving_average", samples=samples.tolist(), window=width)
        assert filtered["plot"]["y"] == pytest.approx(
            np.convolve(samples, np.ones(width) / width, mode="valid"), abs=1e-14
        )
        spectrum = run("signals", "amplitude_spectrum", samples=samples.tolist(), sample_rate_hz=rate)
        weights = np.full(n // 2 + 1, 2.0)
        weights[0] = 1
        if n % 2 == 0:
            weights[-1] = 1
        amplitudes = np.array(spectrum["plot"]["y"])
        assert amplitudes == pytest.approx(weights * np.abs(np.fft.rfft(samples)) / n, abs=1e-13)
        assert spectrum["plot"]["x"] == pytest.approx(np.fft.rfftfreq(n, 1 / rate))
        assert np.sum(amplitudes**2 / weights) == pytest.approx(np.mean(samples**2), rel=1e-14)
        times = np.cumsum([rng.uniform(0.1, 10) for _ in range(n)])
        query = [rng.uniform(float(times[0]), float(times[-1])) for _ in range(7)]
        interpolated = run(
            "signals", "linear_resample", coordinates=times.tolist(), samples=samples.tolist(), targets=query
        )
        assert [row[1] for row in interpolated["table"]["rows"]] == pytest.approx(
            np.interp(query, times, samples), abs=1e-13
        )


def test_spectroscopy_resolves_the_last_transmission_below_the_blank():
    almost_transparent = math.nextafter(100.0, 0)
    result = run(
        "chemistry", "transmission_spectrum", wavelength_nm=[520, 521], transmission_percent=[100, almost_transparent]
    )
    with localcontext() as ctx:
        ctx.prec = 60
        ref = float(-(Decimal(almost_transparent) / 100).log10())
    assert result["values"]["peak_wavelength_nm"] == 521
    assert result["values"]["peak_absorbance"] == pytest.approx(ref, abs=0, rel=2e-15)


def test_compound_measures_with_intermediate_values_outside_float_range():
    tiny = math.ulp(0.0)
    beer = run("chemistry", "beer_lambert", absorbance=1e-280, molar_absorptivity=1e120, path_length_cm=1e-100)
    assert beer["values"]["concentration_mol_l"] == pytest.approx(1e-300, rel=2e-15, abs=0)
    isotopes = run("chemistry", "isotope_average", isotope_mass_u=[1e300, 1e-300], abundance_percent=[tiny, 100])
    assert isotopes["values"]["mean_atomic_mass_u"] == pytest.approx(tiny * 1e300 / 100, rel=2e-15, abs=0)
    gradient = run(
        "earth",
        "geothermal_gradient",
        shallow_depth_m=0,
        deep_depth_m=1e120,
        shallow_temperature_c=0,
        deep_temperature_c=1e-205,
    )
    assert gradient["values"]["gradient_c_per_km"] == 1e-322
    lag = run("earth", "seismic_distance", s_minus_p_s=0.05, p_speed_km_s=1e308, s_speed_km_s=9e307)
    assert lag["values"]["path_length_km"] == pytest.approx(4.5e307)
    assert lag["values"]["s_travel_s"] - lag["values"]["p_travel_s"] == pytest.approx(0.05)
    stream = run(
        "environment",
        "flow_weighted_concentration",
        flow_m3_s=[1e-200, 1e140],
        duration_s=[1, 1],
        concentration_mg_l=[1e300, 0],
    )
    assert stream["values"]["mean_concentration_mg_l"] == pytest.approx(1e-40, rel=2e-15, abs=0)
    runoff = run("environment", "rainfall_runoff", rainfall_mm=1e-190, area_km2=1e-135, runoff_volume_m3=1e-320)
    assert runoff["values"]["rainfall_volume_m3"] == 1e-322
    assert runoff["values"]["runoff_coefficient"] == pytest.approx(99.9988867182683)
    density = run("materials", "bulk_density", dry_mass_g=1e-200, bulk_volume_cm3=1e125)
    assert density["values"]["bulk_density_kg_m3"] == 1e-322
    pores = run("materials", "apparent_porosity", dry_mass_g=1e307, saturated_mass_g=1e308, suspended_mass_g=-1e308)
    assert pores["values"]["apparent_open_porosity_percent"] == pytest.approx(45)


def test_close_geodesics_and_dateline_are_nonzero_and_symmetric():
    nearby = math.nextafter(65.0, 90)
    distance = run("earth", "great_circle_distance", latitude_a=65, latitude_b=nearby, longitude_a=0, longitude_b=0)
    assert distance["values"]["central_angle_deg"] == pytest.approx(nearby - 65, abs=0, rel=1e-15)
    near_dateline = math.nextafter(180.0, 0)
    for a, b in [(near_dateline, -180), (-180, near_dateline)]:
        distance = run("earth", "great_circle_distance", latitude_a=0, latitude_b=0, longitude_a=a, longitude_b=b)
        assert distance["values"]["central_angle_deg"] == pytest.approx(180 - near_dateline, abs=0, rel=1e-15)


def test_subnormal_samples_and_dc_offset_invariance():
    tiny = math.ulp(0.0)
    assert run("signals", "moving_average", samples=[tiny] * 5, window=3)["plot"]["y"] == [tiny] * 3
    assert run("signals", "amplitude_spectrum", samples=[tiny] * 5, sample_rate_hz=1)["plot"]["y"] == [tiny, 0, 0]
    assert run("signals", "amplitude_spectrum", samples=[1e100] * 7, sample_rate_hz=1)["plot"]["y"] == [1e100, 0, 0, 0]
    interpolation = run("signals", "linear_resample", coordinates=[-1e300, 1], samples=[1e300, 0], targets=[0])
    assert interpolation["plot"]["y"] == [1]
    days = run("environment", "degree_days", daily_min_c=[1e16, 1, 1, 1], daily_max_c=[1e16, 1, 1, 1], base_c=0)
    assert days["plot"]["y"][-1] == days["values"]["growing_degree_days_c"] == 1e16 + 4


def test_exact_observations_and_physical_ranges():
    with pytest.raises(ValueError, match="Total individuals"):
        run("biology", "community_diversity", counts=[2**53 - 1, 2])
    with pytest.raises(ValueError, match="Total individuals"):
        run("biology", "quadrat_density", counts=[2**53 - 1, 2], area_m2=[1, 2])
    for pack, name, overrides in [
        ("earth", "geothermal_gradient", {"shallow_temperature_c": -273.2}),
        ("earth", "great_circle_distance", {"longitude_b": 180.1}),
        ("earth", "seismic_distance", {"s_minus_p_s": -0.01}),
        ("chemistry", "transmission_spectrum", {"transmission_percent": [10, 50, 85, 100.00001]}),
        ("materials", "tensile_response", {"force_n": [0, -1, 5, 8]}),
        ("materials", "apparent_porosity", {"suspended_mass_g": 81}),
        ("environment", "degree_days", {"base_c": -274}),
        ("environment", "flow_weighted_concentration", {"concentration_mg_l": [3, -1]}),
        ("signals", "amplitude_spectrum", {"sample_rate_hz": math.ulp(0.0)}),
    ]:
        operation = next(op for op in operations(pack) if op.id == name)
        with pytest.raises(ValueError):
            run_operation(pack, operation, {**operation.example, **overrides})
