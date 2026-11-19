"""Regression oracles and failure boundaries for the cell-culture Biology pack."""

import math
import tomllib
from pathlib import Path

import pytest

from graphlink_plugins.scientific.catalog import PACKS, PACK_FAMILIES, operations
from graphlink_plugins.scientific.core import VERSION, readable_result, run_operation


def _run(operation_id, **inputs):
    operation = next(op for op in operations("cell_culture") if op.id == operation_id)
    return run_operation("cell_culture", operation, inputs)


def test_cell_culture_manifest_discovery_and_example_receipts():
    repo = Path(__file__).resolve().parents[2]
    with (repo / "plugins" / "science_cell_culture" / "plugin.toml").open("rb") as stream:
        manifest = tomllib.load(stream)
    assert manifest["plugin"]["id"] == "science_cell_culture"
    assert manifest["plugin"]["version"] == VERSION
    assert manifest["scientific"]["family"] == "biology"
    assert manifest["scopes"]["grants"] == ["graph.read", "graph.mutate"]
    assert manifest["persistence"]["passive_node_kinds"] == ["workbench"]
    assert PACKS["cell_culture"][0] == "Cell Culture"
    assert PACK_FAMILIES["cell_culture"] == "biology"
    assert [op.id for op in operations("cell_culture")] == [
        "cell_count_viability", "seeding_plan", "serial_dilution_plan", "centrifuge_rcf_rpm",
        "plate_count_cfu", "most_probable_number", "growth_curve_parameters", "doubling_time",
    ]

    for operation in operations("cell_culture"):
        assert all(field.unit for field in operation.fields)
        artifact = run_operation("cell_culture", operation, operation.example)
        assert readable_result({"version": 1, "latest": artifact}, "cell_culture")
        assert artifact["provenance"]["packVersion"] == VERSION
        assert artifact["summary"] and artifact["values"]
        assert artifact["inputs"].keys() == operation.example.keys()


def test_hemocytometer_live_dead_counts_dilution_and_sample_total():
    # Four equal Neubauer 0.1-uL squares: 400 live + 40 nonviable cells.
    r = _run(
        "cell_count_viability",
        live_counts=[90, 110, 100, 100], dead_counts=[10, 10, 20, 0],
        dilution_factor=2, counted_volume_ul=0.1, sample_volume_ml=5,
    )
    v = r["values"]
    assert v["regions_counted"] == 4
    assert v["live_cells_counted"] == 400
    assert v["nonviable_cells_counted"] == 40
    assert v["total_cells_counted"] == 440
    assert v["viability_percent"] == pytest.approx(100 * 400 / 440)
    assert v["viable_cells_per_ml"] == pytest.approx(2_000_000)
    assert v["nonviable_cells_per_ml"] == pytest.approx(200_000)
    assert v["total_cells_per_ml"] == pytest.approx(2_200_000)
    assert v["viable_cells_in_sample"] == pytest.approx(10_000_000)
    assert v["concentration_unit"] == "cells/mL"
    assert r["table"]["rows"][2] == [3, 100, 20, 120]


def test_hemocytometer_independent_abcam_4675_mean_oracle():
    # Abcam, "Counting cells using a hemocytometer": live counts
    # [50, 40, 45, 52], 1:5 sample dilution -> 2,337,500 live cells/mL.
    r = _run(
        "cell_count_viability",
        live_counts=[50, 40, 45, 52], dead_counts=[5, 10, 5, 8],
        dilution_factor=5, counted_volume_ul=0.1, sample_volume_ml=2,
    )
    assert r["values"]["viable_cells_per_ml"] == pytest.approx(2_337_500)
    assert r["values"]["viability_percent"] == pytest.approx(100 * 187 / 215)
    # One half the counted volume doubles concentration without changing viability.
    small = _run(
        "cell_count_viability",
        live_counts=[50, 40, 45, 52], dead_counts=[5, 10, 5, 8],
        dilution_factor=5, counted_volume_ul=0.05, sample_volume_ml=2,
    )
    assert small["values"]["viable_cells_per_ml"] == pytest.approx(2 * r["values"]["viable_cells_per_ml"])
    assert small["values"]["viability_percent"] == r["values"]["viability_percent"]


def test_seeding_volume_balance_and_count_to_seeding_handoff():
    counted = _run(
        "cell_count_viability",
        live_counts=[90, 110, 100, 100], dead_counts=[10, 10, 20, 0],
        dilution_factor=2, counted_volume_ul=0.1, sample_volume_ml=5,
    )
    seeded = _run(
        "seeding_plan",
        viable_cells_per_ml=counted["values"]["viable_cells_per_ml"],
        target_cells_per_vessel=100_000, vessels=12,
        final_volume_ml_per_vessel=2, overage_percent=10,
    )
    v = seeded["values"]
    assert v["suspension_ml_per_vessel"] == pytest.approx(0.05)
    assert v["medium_ml_per_vessel"] == pytest.approx(1.95)
    assert v["bulk_suspension_ml"] == pytest.approx(0.66)
    assert v["bulk_medium_ml"] == pytest.approx(25.74)
    assert v["bulk_volume_ml"] == pytest.approx(26.4)
    assert v["bulk_viable_cells"] == pytest.approx(1_320_000)
    assert v["bulk_suspension_ml"] + v["bulk_medium_ml"] == pytest.approx(v["bulk_volume_ml"])
    assert seeded["table"]["rows"][0][1:] == pytest.approx([0.05, 1.95, 2, 100_000])
    no_extra = _run(
        "seeding_plan", viable_cells_per_ml=2_000_000,
        target_cells_per_vessel=100_000, vessels=12,
        final_volume_ml_per_vessel=2, overage_percent=0,
    )
    assert no_extra["values"]["bulk_volume_ml"] == pytest.approx(24)


def test_serial_dilution_retains_exact_requested_volume_in_each_tube():
    r = _run(
        "serial_dilution_plan",
        initial_concentration=1_000_000, concentration_unit="cells/mL",
        step_dilution_factor=10, steps=3, retained_volume_ml=1,
    )
    v, rows = r["values"], r["table"]["rows"]
    assert v["final_concentration"] == pytest.approx(1_000)
    assert v["starting_solution_ml"] == pytest.approx(0.111)
    assert v["total_diluent_ml"] == pytest.approx(2.889)
    assert v["total_retained_ml"] == pytest.approx(3)
    assert [row[1] for row in rows] == pytest.approx([1e5, 1e4, 1e3])
    assert [row[2] for row in rows] == pytest.approx([0.111, 0.11, 0.1])
    assert [row[3] for row in rows] == pytest.approx([0.999, 0.99, 0.9])
    assert [row[4] for row in rows] == pytest.approx([1.11, 1.1, 1])
    for i, row in enumerate(rows):
        downstream_transfer = rows[i + 1][2] if i + 1 < len(rows) else 0
        assert row[2] + row[3] == pytest.approx(row[4])
        assert row[4] - downstream_transfer == pytest.approx(row[5])
        assert row[5] == pytest.approx(1)
    assert v["starting_solution_ml"] + v["total_diluent_ml"] == pytest.approx(3)


@pytest.mark.parametrize("factor,steps,retained", [(2, 1, 0.5), (2, 5, 2), (3, 8, 0.1)])
def test_serial_dilution_stepwise_mass_balance(factor, steps, retained):
    r = _run(
        "serial_dilution_plan", initial_concentration=1000,
        concentration_unit="mg/mL", step_dilution_factor=factor,
        steps=steps, retained_volume_ml=retained,
    )
    rows = r["table"]["rows"]
    assert r["values"]["final_concentration"] == pytest.approx(1000 / factor**steps)
    assert all(row[5] == pytest.approx(retained) for row in rows)
    for i, row in enumerate(rows):
        assert row[2] * factor == pytest.approx(row[4])
        if i:
            assert rows[i - 1][4] - row[2] == pytest.approx(retained)
    assert r["values"]["starting_solution_ml"] + r["values"]["total_diluent_ml"] == pytest.approx(
        steps * retained
    )


def test_centrifuge_conversion_both_directions_and_radius_dependence():
    # Eppendorf RCF = 1.118e-5 * r_cm * rpm²; 10 cm, 3000 rpm = 1006.2 ×g.
    forward = _run(
        "centrifuge_rcf_rpm", input_kind="rpm", input_value=3000, rotor_radius_cm=10,
    )
    assert forward["values"]["rpm"] == 3000
    assert forward["values"]["rcf_x_g"] == pytest.approx(1006.2)
    backward = _run(
        "centrifuge_rcf_rpm", input_kind="rcf",
        input_value=forward["values"]["rcf_x_g"], rotor_radius_cm=10,
    )
    assert backward["values"]["rpm"] == pytest.approx(3000)
    assert backward["values"]["rcf_x_g"] == pytest.approx(1006.2)
    half_radius = _run(
        "centrifuge_rcf_rpm", input_kind="rpm", input_value=3000, rotor_radius_cm=5,
    )
    assert half_radius["values"]["rcf_x_g"] == pytest.approx(503.1)
    zero = _run(
        "centrifuge_rcf_rpm", input_kind="rcf", input_value=0, rotor_radius_cm=10,
    )
    assert zero["values"]["rpm"] == zero["values"]["rcf_x_g"] == 0
    assert math.isfinite(forward["values"]["rcf_x_g"])


def test_plate_count_pools_original_sample_exposure_across_dilutions():
    r = _run(
        "plate_count_cfu",
        colony_counts=[50, 20], reciprocal_dilutions=[100, 1000],
        plated_volume_ml=0.1,
    )
    v = r["values"]
    assert v["plates_counted"] == 2
    assert v["colonies_counted"] == 70
    assert v["plates_in_30_to_300_range"] == 1
    assert v["original_sample_exposure_ml"] == pytest.approx(0.0011)
    assert v["cfu_per_ml"] == pytest.approx(70 / 0.0011)
    assert v["single_colony_increment_cfu_per_ml"] == pytest.approx(1 / 0.0011)
    assert v["concentration_unit"] == "CFU/mL"
    # Unequal reciprocal dilutions must not be treated as equally weighted.
    assert v["cfu_per_ml"] != pytest.approx((50_000 + 200_000) / 2)
    assert r["table"]["rows"][0][5] == pytest.approx(50_000)
    assert r["table"]["rows"][1][5] == pytest.approx(200_000)


def test_plate_count_equal_dilutions_zero_plates_and_volume_scaling():
    ordinary = _run(
        "plate_count_cfu",
        colony_counts=[45, 55], reciprocal_dilutions=[1000, 1000],
        plated_volume_ml=0.1,
    )
    assert ordinary["values"]["cfu_per_ml"] == pytest.approx(500_000)
    doubled_volume = _run(
        "plate_count_cfu",
        colony_counts=[45, 55], reciprocal_dilutions=[1000, 1000],
        plated_volume_ml=0.2,
    )
    assert doubled_volume["values"]["cfu_per_ml"] == pytest.approx(250_000)
    zero = _run(
        "plate_count_cfu",
        colony_counts=[0, 0], reciprocal_dilutions=[100, 100],
        plated_volume_ml=1,
    )
    assert zero["values"]["cfu_per_ml"] == 0
    assert "not proof of absence" in zero["summary"]


def test_mpn_one_dilution_has_analytic_poisson_mle():
    # P(positive) = 1 - exp(-MPN * original sample mL), and one positive of
    # two tubes has p=1/2 -> MPN = ln(2) when each tube samples 1 mL.
    r = _run(
        "most_probable_number",
        positive_tubes=[1], total_tubes=[2],
        reciprocal_dilutions=[1], inoculum_volume_ml=1,
    )
    assert r["values"]["mpn_per_ml"] == pytest.approx(math.log(2), rel=1e-12)
    assert r["table"]["rows"][0][5] == pytest.approx(0.5, abs=1e-12)
    assert r["table"]["rows"][0][6] == pytest.approx(1)
    half_volume = _run(
        "most_probable_number",
        positive_tubes=[1], total_tubes=[2],
        reciprocal_dilutions=[1], inoculum_volume_ml=0.5,
    )
    assert half_volume["values"]["mpn_per_ml"] == pytest.approx(2 * math.log(2))


def test_mpn_three_dilutions_against_independent_likelihood_root():
    r = _run(
        "most_probable_number",
        positive_tubes=[5, 3, 0], total_tubes=[5, 5, 5],
        reciprocal_dilutions=[1, 10, 100], inoculum_volume_ml=1,
    )
    estimate = r["values"]["mpn_per_ml"]
    # Independently bracketed Poisson score root for 5-3-0 with 5 tubes/level.
    assert estimate == pytest.approx(7.924271863163129, rel=1e-10)
    assert r["values"]["positive_tubes"] == 8
    assert r["values"]["negative_tubes"] == 7
    assert r["values"]["concentration_unit"] == "MPN/mL"
    assert r["table"]["rows"][1][5] == pytest.approx(-math.expm1(-estimate / 10))


def test_mpn_nondetection_is_a_boundary_estimate_not_an_absence_claim():
    zero = _run(
        "most_probable_number",
        positive_tubes=[0, 0, 0], total_tubes=[5, 5, 5],
        reciprocal_dilutions=[1, 10, 100], inoculum_volume_ml=1,
    )
    assert zero["values"]["mpn_per_ml"] == 0
    assert all(row[5] == 0 for row in zero["table"]["rows"])
    assert "not proof of absence" in zero["summary"]


def _synthetic_culture(times, base=10_000, amplitude=4, mu=0.5, lag=4):
    # Three-parameter modified Gompertz equation in natural-log abundance.
    return [
        base * math.exp(amplitude * math.exp(
            -math.exp(1 + (mu * math.e / amplitude) * (lag - t))
        ))
        for t in times
    ]


def test_growth_curve_modified_gompertz_recovers_lag_max_rate_and_asymptote():
    times = list(range(0, 22, 2))
    r = _run(
        "growth_curve_parameters",
        time=times, abundance=_synthetic_culture(times),
        time_unit="h", abundance_unit="CFU/mL",
    )
    v = r["values"]
    assert v["observations"] == len(times)
    assert v["lag_time"] == pytest.approx(4, abs=0.1)
    assert v["maximum_specific_growth_rate"] == pytest.approx(0.5, rel=0.03)
    assert v["asymptote_abundance"] == pytest.approx(10_000 * math.exp(4), rel=0.03)
    assert v["doubling_time_at_max_growth"] == pytest.approx(math.log(2) / 0.5, rel=0.03)
    assert v["growth_rate_unit"] == "1/h"
    assert v["lag_time_unit"] == "h"
    assert v["asymptote_unit"] == "CFU/mL"
    assert v["r_squared_log"] > 0.9999
    assert len(r["table"]["rows"]) == len(times)
    assert r["plot"]["x"] == times
    assert r["plot"]["y"] == pytest.approx([row[2] for row in r["table"]["rows"]])


def test_growth_curve_time_shift_and_unit_rescale():
    times = list(range(0, 22, 2))
    base = _run(
        "growth_curve_parameters",
        time=times, abundance=_synthetic_culture(times),
        time_unit="h", abundance_unit="CFU/mL",
    )
    scaled = _run(
        "growth_curve_parameters",
        time=[(t + 10) * 60 for t in times],
        abundance=_synthetic_culture(times),
        time_unit="min", abundance_unit="CFU/mL",
    )
    a, b = base["values"], scaled["values"]
    assert b["lag_time"] == pytest.approx((a["lag_time"] + 10) * 60, rel=1e-5)
    assert b["maximum_specific_growth_rate"] == pytest.approx(
        a["maximum_specific_growth_rate"] / 60, rel=1e-5
    )
    assert b["asymptote_abundance"] == pytest.approx(a["asymptote_abundance"], rel=1e-5)
    assert b["doubling_time_at_max_growth"] == pytest.approx(
        a["doubling_time_at_max_growth"] * 60, rel=1e-5
    )


def test_two_point_doubling_time_and_unit_conversion():
    r = _run(
        "doubling_time",
        initial_abundance=100_000, final_abundance=800_000,
        elapsed_time=6, time_unit="h", abundance_unit="CFU/mL",
    )
    v = r["values"]
    assert v["doublings_observed"] == pytest.approx(3)
    assert v["doubling_time"] == pytest.approx(2)
    assert v["specific_growth_rate"] == pytest.approx(math.log(2) / 2)
    assert v["doubling_time_unit"] == "h"
    converted = _run(
        "doubling_time",
        initial_abundance=100_000, final_abundance=800_000,
        elapsed_time="360 min", time_unit="h", abundance_unit="CFU/mL",
    )
    assert converted["values"]["doubling_time"] == pytest.approx(2)


@pytest.mark.parametrize("op,changes", [
    ("plate_count_cfu", {"colony_counts": [12], "reciprocal_dilutions": [10, 100]}),
    ("plate_count_cfu", {"colony_counts": [-1]}),
    ("plate_count_cfu", {"colony_counts": [5.5]}),
    ("plate_count_cfu", {"colony_counts": [2**53]}),
    ("plate_count_cfu", {"reciprocal_dilutions": [0, 1000]}),
    ("plate_count_cfu", {"plated_volume_ml": 0}),
    ("plate_count_cfu", {"plated_volume_ml": 1e-300, "reciprocal_dilutions": [1e308, 1e308]}),
    ("most_probable_number", {"positive_tubes": [2, 1], "total_tubes": [2]}),
    ("most_probable_number", {"positive_tubes": [6, 3, 0]}),
    ("most_probable_number", {"positive_tubes": [5, 5, 5]}),
    ("most_probable_number", {"total_tubes": [0, 5, 5]}),
    ("most_probable_number", {"total_tubes": [5, 2.5, 5]}),
    ("most_probable_number", {"reciprocal_dilutions": [1, 1, 100]}),
    ("most_probable_number", {"reciprocal_dilutions": [1, 10, 0.5]}),
    ("most_probable_number", {"inoculum_volume_ml": 0}),
    ("growth_curve_parameters", {"time": [0, 1], "abundance": [1, 2]}),
    ("growth_curve_parameters", {"abundance": [1] * 11}),
    ("growth_curve_parameters", {"abundance": [0] + [1] * 10}),
    ("growth_curve_parameters", {"time": [0] * 11}),
    ("growth_curve_parameters", {"time": list(range(11)),
                                  "abundance": [2**t for t in range(11)]}),
    ("doubling_time", {"initial_abundance": 0}),
    ("doubling_time", {"final_abundance": 100_000}),
    ("doubling_time", {"final_abundance": -5}),
    ("doubling_time", {"elapsed_time": 0}),
    ("doubling_time", {"elapsed_time": float("nan")}),
])
def test_new_cell_culture_invalid_inputs(op, changes):
    example = next(o.example for o in operations("cell_culture") if o.id == op)
    with pytest.raises(ValueError):
        _run(op, **{**example, **changes})




@pytest.mark.parametrize("op,changes", [
    ("cell_count_viability", {"live_counts": [1, 2], "dead_counts": [1]}),
    ("cell_count_viability", {"live_counts": [0], "dead_counts": [0]}),
    ("cell_count_viability", {"live_counts": [1.5], "dead_counts": [1]}),
    ("cell_count_viability", {"live_counts": [-1], "dead_counts": [1]}),
    ("cell_count_viability", {"live_counts": [2**53 - 1], "dead_counts": [1]}),
    ("cell_count_viability", {"dilution_factor": 0.5}),
    ("cell_count_viability", {"counted_volume_ul": 0}),
    ("cell_count_viability", {"sample_volume_ml": -1}),
    ("seeding_plan", {"viable_cells_per_ml": 0}),
    ("seeding_plan", {"target_cells_per_vessel": 0}),
    ("seeding_plan", {"vessels": 0}),
    ("seeding_plan", {"vessels": 1_000_001}),
    ("seeding_plan", {"overage_percent": -1}),
    ("seeding_plan", {"overage_percent": 101}),
    ("seeding_plan", {"viable_cells_per_ml": 100, "final_volume_ml_per_vessel": 0.1}),
    ("serial_dilution_plan", {"step_dilution_factor": 1}),
    ("serial_dilution_plan", {"steps": 0}),
    ("serial_dilution_plan", {"steps": 101}),
    ("serial_dilution_plan", {"retained_volume_ml": 0}),
    ("serial_dilution_plan", {"concentration_unit": "\n"}),
    ("serial_dilution_plan", {"initial_concentration": 1, "step_dilution_factor": 1e308, "steps": 2}),
    ("centrifuge_rcf_rpm", {"input_kind": "angular velocity"}),
    ("centrifuge_rcf_rpm", {"input_value": -100}),
    ("centrifuge_rcf_rpm", {"rotor_radius_cm": 0}),
    ("centrifuge_rcf_rpm", {"input_value": float("nan")}),
])
def test_invalid_design_inputs_are_rejected(op, changes):
    example = next(o.example for o in operations("cell_culture") if o.id == op)
    with pytest.raises(ValueError):
        _run(op, **{**example, **changes})


def test_inputs_require_complete_exact_schema():
    op = next(o for o in operations("cell_culture") if o.id == "seeding_plan")
    with pytest.raises(ValueError, match="exactly"):
        run_operation("cell_culture", op, {"vessels": 3})
    with pytest.raises(ValueError, match="exactly"):
        run_operation("cell_culture", op, {**op.example, "unapproved": 1})
