"""Reference calculations, invalid observations, and boundary cases for five domain packs."""

import math

import pytest

from graphlink_plugins.scientific.packs import biology, chemistry, earth, environment, materials


def test_blank_corrected_concentration_and_blank():
    inputs = {"molar_absorptivity": 2000, "path_length_cm": 0.5}
    assert chemistry.beer_lambert({**inputs, "absorbance": 0.15})["values"]["concentration_mol_l"] == pytest.approx(
        0.00015
    )
    assert chemistry.beer_lambert({**inputs, "absorbance": 0})["values"]["concentration_mol_l"] == 0


def test_transmission_spectrum_with_first_of_tied_peaks():
    result = chemistry.transmission_spectrum({"wavelength_nm": [500, 510, 520], "transmission_percent": [100, 1, 1]})
    assert result["plot"]["y"] == pytest.approx([0, 2, 2])
    assert result["values"]["peak_wavelength_nm"] == 510
    assert result["table"]["rows"][-1] == [520, 1, 2]


def test_isotope_percentages_and_rounding_tolerance():
    isotope_mass = {"isotope_mass_u": [10, 12]}
    assert (
        chemistry.isotope_average({**isotope_mass, "abundance_percent": [25, 75]})["values"]["mean_atomic_mass_u"]
        == 11.5
    )
    result = chemistry.isotope_average({**isotope_mass, "abundance_percent": [24.995, 74.995]})
    assert result["values"]["mean_atomic_mass_u"] == pytest.approx((10 * 24.995 + 12 * 74.995) / 99.99)


def test_even_and_empty_taxa():
    result = biology.community_diversity({"counts": [4, 0, 4, 4, 4]})["values"]
    assert result == pytest.approx(
        {"individuals": 16, "richness": 4, "shannon_nats": math.log(4), "gini_simpson": 0.75}
    )
    assert biology.community_diversity({"counts": [2, 0]})["values"]["gini_simpson"] == 0


def test_area_weighted_quadrat_density():
    result = biology.quadrat_density({"counts": [5, 0], "area_m2": [1, 4]})
    assert result["values"]["pooled_density_per_m2"] == 1
    assert result["table"]["rows"] == [[1, 5, 1, 5], [2, 0, 4, 0]]


def test_overlap_and_empty_site():
    overlap = biology.community_overlap({"site_a_counts": [1, 0, 4], "site_b_counts": [8, 1, 0]})["values"]
    assert overlap == pytest.approx(
        {"shared_taxa": 1, "union_taxa": 3, "jaccard_similarity": 1 / 3, "sorensen_similarity": 0.5}
    )
    assert biology.community_overlap({"site_a_counts": [0], "site_b_counts": [5]})["values"]["jaccard_similarity"] == 0


@pytest.mark.parametrize(
    ("a", "b", "degrees"),
    [
        ((0, 179), (0, -179), 2),
        ((0, 0), (0, 180), 180),
        ((90, 140), (90, -80), 0),
        ((20, -180), (20, 180), 0),
        ((0, 0), (0, 0.000001), 0.000001),
    ],
)
def test_great_circle_boundaries(a, b, degrees):
    result = earth.great_circle_distance(
        {"latitude_a": a[0], "longitude_a": a[1], "latitude_b": b[0], "longitude_b": b[1]}
    )["values"]
    assert result["central_angle_deg"] == pytest.approx(degrees)
    assert result["distance_km"] == pytest.approx(6371.0088 * math.radians(degrees))


def test_geothermal_temperature_inversion():
    inputs = {"shallow_depth_m": 500, "deep_depth_m": 750, "shallow_temperature_c": 20, "deep_temperature_c": 18}
    assert earth.geothermal_gradient(inputs)["values"]["gradient_c_per_km"] == -8


def test_wave_arrival_lag_and_zero_distance():
    speeds = {"p_speed_km_s": 6, "s_speed_km_s": 3}
    assert earth.seismic_distance({**speeds, "s_minus_p_s": 5})["values"] == {
        "path_length_km": 30,
        "p_travel_s": 5,
        "s_travel_s": 10,
    }
    assert earth.seismic_distance({**speeds, "s_minus_p_s": 0})["values"]["path_length_km"] == 0


def test_stream_mass_balance_and_zero_flow_interval():
    result = environment.flow_weighted_concentration(
        {
            "flow_m3_s": [2, 0, 1],
            "duration_s": [10, 10, 30],
            "concentration_mg_l": [3, 1000, 8],
        }
    )
    assert result["values"] == pytest.approx(
        {"total_water_m3": 50, "total_constituent_kg": 0.3, "mean_concentration_mg_l": 6}
    )
    assert result["table"]["rows"][1] == [2, 0, 0]


def test_event_water_budget_flags_measured_excess():
    event = {"rainfall_mm": 5, "area_km2": 4, "runoff_volume_m3": 15000}
    assert environment.rainfall_runoff(event)["values"] == {
        "rainfall_volume_m3": 20000,
        "runoff_depth_mm": 3.75,
        "runoff_coefficient": 0.75,
    }
    result = environment.rainfall_runoff({**event, "runoff_volume_m3": 25000})
    assert result["values"]["runoff_coefficient"] == 1.25
    assert "exceeds rainfall" in result["summary"]


def test_degree_days_threshold_and_cumulative_series():
    result = environment.degree_days({"daily_min_c": [0, 8, 10], "daily_max_c": [5, 16, 20], "base_c": 10})
    assert result["values"]["growing_degree_days_c"] == 7
    assert result["plot"]["y"] == [0, 2, 7]
    assert result["table"]["rows"][1] == [2, 12, 2, 2]


def test_density_units():
    assert materials.bulk_density({"dry_mass_g": 15, "bulk_volume_cm3": 10})["values"] == {
        "bulk_density_g_cm3": 1.5,
        "bulk_density_kg_m3": 1500,
    }


def test_tensile_curve_retains_reading_order_and_first_peak():
    inputs = {
        "force_n": [0, 400, 100, 400],
        "extension_mm": [0, 1, 0.25, 1.5],
        "original_area_mm2": 20,
        "gauge_length_mm": 50,
    }
    result = materials.tensile_response(inputs)
    assert result["values"]["engineering_strain_at_first_peak"] == 0.02
    assert result["plot"]["x"] == pytest.approx([0, 0.02, 0.005, 0.03])
    assert result["plot"]["y"] == [0, 20, 5, 20]


def test_porosity_and_impermeable_specimen():
    measurements = {"dry_mass_g": 80, "suspended_mass_g": 50, "saturated_mass_g": 90}
    assert materials.apparent_porosity(measurements)["values"]["apparent_open_porosity_percent"] == 25
    assert (
        materials.apparent_porosity({**measurements, "saturated_mass_g": 80})["values"][
            "apparent_open_porosity_percent"
        ]
        == 0
    )


@pytest.mark.parametrize(
    ("pack", "name", "overrides"),
    [
        (chemistry, "beer_lambert", {"molar_absorptivity": 0}),
        (chemistry, "transmission_spectrum", {"transmission_percent": [100, 20, 0, 15]}),
        (chemistry, "transmission_spectrum", {"wavelength_nm": [400, 500, 500, 700]}),
        (chemistry, "isotope_average", {"abundance_percent": [24, 40]}),
        (biology, "community_diversity", {"counts": [0, 0]}),
        (biology, "community_diversity", {"counts": [2.5]}),
        (biology, "quadrat_density", {"area_m2": [0, 3, 2]}),
        (biology, "community_overlap", {"site_a_counts": [0], "site_b_counts": [0]}),
        (biology, "community_overlap", {"site_a_counts": [1]}),
        (earth, "great_circle_distance", {"latitude_a": 91}),
        (earth, "geothermal_gradient", {"shallow_depth_m": 900}),
        (earth, "seismic_distance", {"s_speed_km_s": 6}),
        (environment, "flow_weighted_concentration", {"flow_m3_s": [0, 0]}),
        (environment, "flow_weighted_concentration", {"duration_s": [600]}),
        (environment, "rainfall_runoff", {"area_km2": 0}),
        (environment, "degree_days", {"daily_min_c": [20, 10, 12]}),
        (materials, "bulk_density", {"bulk_volume_cm3": 0}),
        (materials, "tensile_response", {"gauge_length_mm": -1}),
        (materials, "apparent_porosity", {"saturated_mass_g": 75}),
    ],
)
def test_invalid_observations(pack, name, overrides):
    operation = next(op for op in pack.OPERATIONS if op.id == name)
    with pytest.raises(ValueError):
        operation.calculate({**operation.example, **overrides})
