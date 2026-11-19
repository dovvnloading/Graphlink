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
