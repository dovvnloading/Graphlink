"""Cell-culture arithmetic for the grant-gated Biology Workbench.

Plans are theoretical volume calculations, not instructions for sterile handling,
centrifuge safety, or organism-specific culture conditions. Standard library only.
"""

from __future__ import annotations

import math

from graphlink_plugins.scientific.core import Field, Operation


_MAX_EXACT_COUNT = 2**53 - 1
_RCF_COEFFICIENT = 1.118e-5  # RCF (multiples of g) = coefficient * radius (cm) * rpm²


def _positive(data: dict, key: str) -> float:
    value = data[key]
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{key} must be a finite, positive number.")
    return value


def _checked(value: float, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} exceeds the supported numeric range.")
    return value


def _counts(values: list, label: str) -> list[int]:
    if not values or any(not math.isfinite(n) or n < 0 or n != int(n) for n in values):
        raise ValueError(f"{label} must contain nonnegative whole-cell counts.")
    result = [int(n) for n in values]
    if sum(result) > _MAX_EXACT_COUNT:
        raise ValueError(f"{label} exceeds the exact-count limit (2^53 - 1).")
    return result


def cell_count_viability(data: dict) -> dict:
    """Count equal-volume hemocytometer regions, correcting for sample dilution."""
    live = _counts(data["live_counts"], "Live counts")
    dead = _counts(data["dead_counts"], "Nonviable counts")
    if len(live) != len(dead):
        raise ValueError("Live and nonviable counts must describe the same regions.")
    live_total, dead_total = sum(live), sum(dead)
    total = live_total + dead_total
    if total == 0:
        raise ValueError("Viability is undefined when no cells were counted.")
    if total > _MAX_EXACT_COUNT:
        raise ValueError("Combined cell counts exceed the exact-count limit (2^53 - 1).")

    dilution = _positive(data, "dilution_factor")
    if dilution < 1:
        raise ValueError("The dilution factor must be at least 1 (undiluted).")
    volume_ul = _positive(data, "counted_volume_ul")
    sample_ml = _positive(data, "sample_volume_ml")
    multiplier = _checked(dilution * (1000 / volume_ul), "Dilution/volume multiplier")
    live_per_ml = _checked((live_total / len(live)) * multiplier, "Viable concentration")
    dead_per_ml = _checked((dead_total / len(dead)) * multiplier, "Nonviable concentration")
    total_per_ml = _checked((total / len(live)) * multiplier, "Total concentration")
    available = _checked(live_per_ml * sample_ml, "Viable cells in sample")
    viability = 100 * live_total / total

    return {
        "summary": (
            f"{viability:.4g}% viable cells; {live_per_ml:.6g} viable cells/mL "
            f"after a {dilution:g}-fold counting dilution. Counts are observations, "
            "not a measurement of culture health beyond dye exclusion."
        ),
        "values": {
            "regions_counted": len(live),
            "live_cells_counted": live_total,
            "nonviable_cells_counted": dead_total,
            "total_cells_counted": total,
            "viability_percent": viability,
            "viability_unit": "%",
            "viable_cells_per_ml": live_per_ml,
            "nonviable_cells_per_ml": dead_per_ml,
            "total_cells_per_ml": total_per_ml,
            "concentration_unit": "cells/mL",
            "sample_volume_ml": sample_ml,
            "sample_volume_unit": "mL",
            "viable_cells_in_sample": available,
            "viable_cells_in_sample_unit": "cells",
        },
        "table": {
            "columns": ["Counted region", "Live (cells)", "Nonviable (cells)", "Total (cells)"],
            "rows": [[i, a, b, a + b] for i, (a, b) in enumerate(zip(live, dead), 1)],
        },
    }


def seeding_plan(data: dict) -> dict:
    """Plan suspension and medium volumes with explicit preparation overage."""
    concentration = _positive(data, "viable_cells_per_ml")
    cells_per_vessel = data["target_cells_per_vessel"]
    vessels = data["vessels"]
    if not 1 <= cells_per_vessel <= _MAX_EXACT_COUNT:
        raise ValueError("Target cells per vessel must be a positive whole number.")
    if not 1 <= vessels <= 1_000_000:
        raise ValueError("Vessel count must be between 1 and 1,000,000.")
    final_ml = _positive(data, "final_volume_ml_per_vessel")
    overage = data["overage_percent"]
    if not math.isfinite(overage) or not 0 <= overage <= 100:
        raise ValueError("Preparation overage must be between 0 and 100 percent.")

    suspension_ml = _checked(cells_per_vessel / concentration, "Suspension per vessel")
    if suspension_ml > final_ml:
        raise ValueError(
            "The required suspension volume exceeds the final vessel volume; "
            "concentrate the suspension or revise the seeding design."
        )
    medium_ml = final_ml - suspension_ml
    prepared_vessels = vessels * (1 + overage / 100)
    bulk_suspension = _checked(suspension_ml * prepared_vessels, "Bulk suspension")
    bulk_medium = _checked(medium_ml * prepared_vessels, "Bulk medium")
    bulk_volume = _checked(final_ml * prepared_vessels, "Bulk final volume")
    bulk_cells = _checked(cells_per_vessel * prepared_vessels, "Bulk viable cells")

    return {
        "summary": (
            f"Seed {vessels} vessels at {cells_per_vessel:g} viable cells each "
            f"in {final_ml:g} mL per vessel. Prepare {bulk_volume:.6g} mL "
            f"total including {overage:g}% overage."
        ),
        "values": {
            "viable_cells_per_ml": concentration,
            "concentration_unit": "cells/mL",
            "target_cells_per_vessel": cells_per_vessel,
            "vessels": vessels,
            "overage_percent": overage,
            "suspension_ml_per_vessel": suspension_ml,
            "medium_ml_per_vessel": medium_ml,
            "final_volume_ml_per_vessel": final_ml,
            "bulk_suspension_ml": bulk_suspension,
            "bulk_medium_ml": bulk_medium,
            "bulk_volume_ml": bulk_volume,
            "bulk_viable_cells": bulk_cells,
            "volume_unit": "mL",
            "cell_count_unit": "cells",
        },
        "table": {
            "columns": [
                "Preparation", "Suspension (mL)", "Medium (mL)",
                "Total (mL)", "Viable cells (cells)",
            ],
            "rows": [
                ["Per vessel", suspension_ml, medium_ml, final_ml, cells_per_vessel],
                ["Bulk with overage", bulk_suspension, bulk_medium, bulk_volume, bulk_cells],
            ],
        },
    }


def _unit_label(label: str) -> str:
    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 40 or any(ord(c) < 32 for c in label):
        raise ValueError("Concentration unit must be a 1–40 character label.")
    return label.strip()


def serial_dilution_plan(data: dict) -> dict:
    """Backward-plan a serial dilution so EVERY tube retains the target volume.

    The volume mixed at an intermediate step is its desired retained volume
    plus the aliquot that the following tube will take from it.
    """
    initial = _positive(data, "initial_concentration")
    unit = _unit_label(data["concentration_unit"])
    factor = _positive(data, "step_dilution_factor")
    if factor <= 1:
        raise ValueError("The per-step dilution factor must be greater than 1.")
    steps = data["steps"]
    if not 1 <= steps <= 100:
        raise ValueError("Serial dilution supports 1–100 steps.")
    retained_ml = _positive(data, "retained_volume_ml")

    prepared = [0.0] * steps
    required_ml = retained_ml
    for i in range(steps - 1, -1, -1):
        prepared[i] = _checked(required_ml, "Prepared tube volume")
        required_ml = _checked(retained_ml + required_ml / factor, "Required upstream volume")

    stock_ml = _checked(prepared[0] / factor, "Starting solution aliquot")
    if stock_ml <= 0:
        raise ValueError("The starting aliquot underflows the numeric range.")

    rows = []
    concentration = initial
    for i, prepared_ml in enumerate(prepared, 1):
        concentration = _checked(concentration / factor, "Dilution concentration")
        if concentration <= 0:
            raise ValueError("The diluted concentration underflows the numeric range.")
        transfer_ml = _checked(prepared_ml / factor, "Transfer volume")
        diluent_ml = _checked(prepared_ml - transfer_ml, "Diluent volume")
        if transfer_ml <= 0 or diluent_ml <= 0:
            raise ValueError("Transfer or diluent volume underflows the numeric range.")
        rows.append([i, concentration, transfer_ml, diluent_ml, prepared_ml, retained_ml])

    total_diluent = _checked(math.fsum(row[3] for row in rows), "Total diluent")
    total_retained = _checked(retained_ml * steps, "Total retained volume")
    return {
        "summary": (
            f"{steps} successive {factor:g}-fold dilutions: {initial:.6g} to "
            f"{concentration:.6g} {unit}. Back-calculated transfers leave "
            f"{retained_ml:g} mL in each tube after downstream transfers."
        ),
        "values": {
            "initial_concentration": initial,
            "final_concentration": concentration,
            "concentration_unit": unit,
            "step_dilution_factor": factor,
            "steps": steps,
            "retained_volume_ml_per_tube": retained_ml,
            "starting_solution_ml": stock_ml,
            "total_diluent_ml": total_diluent,
            "total_retained_ml": total_retained,
            "volume_unit": "mL",
        },
        "table": {
            "columns": [
                "Tube", f"Concentration ({unit})", "Transfer from previous (mL)",
                "Add diluent (mL)", "Volume mixed (mL)", "Volume left (mL)",
            ],
            "rows": rows,
        },
    }


def centrifuge_rcf_rpm(data: dict) -> dict:
    """RCF at the specified radius, with rpm and RCF as alternate input kinds."""
    radius_cm = _positive(data, "rotor_radius_cm")
    kind = data["input_kind"].strip().lower()
    if kind not in ("rpm", "rcf"):
        raise ValueError("Input kind must be 'rpm' or 'rcf'.")
    value = data["input_value"]
    if not math.isfinite(value) or value < 0:
        raise ValueError("Speed or RCF must be nonnegative and finite.")
    coefficient = _checked(_RCF_COEFFICIENT * radius_cm, "RCF radius coefficient")
    if coefficient <= 0:
        raise ValueError("The radius is too small for a representable conversion.")

    if kind == "rpm":
        rpm = value
        rcf = _checked(coefficient * rpm * rpm, "RCF")
    else:
        rcf = value
        rpm = _checked(math.sqrt(rcf / coefficient), "RPM")

    return {
        "summary": (
            f"At rotor radius {radius_cm:g} cm, {rpm:.6g} rpm corresponds "
            f"to {rcf:.6g} × g. Use the rotor manufacturer's radius definition "
            "and do not exceed instrument or vessel limits."
        ),
        "values": {
            "rotor_radius_cm": radius_cm,
            "radius_unit": "cm",
            "rpm": rpm,
            "rpm_unit": "rev/min",
            "rcf_x_g": rcf,
            "rcf_unit": "× g (relative to standard gravity)",
            "input_kind": kind,
        },
    }


OPERATIONS = (
    Operation(
        "cell_count_viability", "Hemocytometer count and viability",
        "Live/nonviable counts across equal-volume regions; correct by counting dilution, "
        "estimate cells/mL and dye-exclusion viability.",
        (
            Field("live_counts", "Live cells in each counted region", "vector", unit="cells"),
            Field("dead_counts", "Nonviable cells in each counted region", "vector", unit="cells"),
            Field("dilution_factor", "Total dilution of original sample for counting", "number", unit="1"),
            Field("counted_volume_ul", "Volume of each counted region", "number", unit="uL"),
            Field("sample_volume_ml", "Volume of original cell suspension", "number", unit="mL"),
        ),
        {
            "live_counts": [90, 110, 100, 100], "dead_counts": [10, 10, 20, 0],
            "dilution_factor": 2, "counted_volume_ul": 0.1, "sample_volume_ml": 5,
        },
        cell_count_viability,
    ),
    Operation(
        "seeding_plan", "Viable-cell seeding preparation",
        "Plan per-vessel and bulk cell suspension/medium volumes with a preparation overage.",
        (
            Field("viable_cells_per_ml", "Live cell suspension concentration", "number", unit="cells/mL"),
            Field("target_cells_per_vessel", "Target viable cells per vessel", "integer", unit="cells"),
            Field("vessels", "Number of vessels", "integer", unit="1"),
            Field("final_volume_ml_per_vessel", "Final culture volume per vessel", "number", unit="mL"),
            Field("overage_percent", "Extra preparation volume", "number", unit="%"),
        ),
        {
            "viable_cells_per_ml": 2_000_000, "target_cells_per_vessel": 100_000,
            "vessels": 12, "final_volume_ml_per_vessel": 2, "overage_percent": 10,
        },
        seeding_plan,
    ),
    Operation(
        "serial_dilution_plan", "Serial dilution with retained tube volumes",
        "Plan each successive transfer and diluent addition so all tubes retain the "
        "requested volume, including intermediate tubes.",
        (
            Field("initial_concentration", "Starting solution concentration", "number",
                  unit="selected concentration unit"),
            Field("concentration_unit", "Concentration unit label (e.g. cells/mL)", "text",
                  unit="unit label"),
            Field("step_dilution_factor", "Dilution factor at each step", "number", unit="1"),
            Field("steps", "Number of serial dilution steps", "integer", unit="1"),
            Field("retained_volume_ml", "Required remaining volume in each tube", "number", unit="mL"),
        ),
        {
            "initial_concentration": 1_000_000, "concentration_unit": "cells/mL",
            "step_dilution_factor": 10, "steps": 3, "retained_volume_ml": 1,
        },
        serial_dilution_plan,
    ),
    Operation(
        "centrifuge_rcf_rpm", "Centrifuge RCF ↔ rpm",
        "Convert rotor speed and relative centrifugal force at a user-supplied radius.",
        (
            Field("input_kind", "Input kind: rpm or rcf (× g)", "text", unit="choice"),
            Field("input_value", "Speed (rpm) or relative force (× g)", "number", unit="1"),
            Field("rotor_radius_cm", "Radius from axis to sample (often r_max)", "number", unit="cm"),
        ),
        {"input_kind": "rpm", "input_value": 3000, "rotor_radius_cm": 10},
        centrifuge_rcf_rpm,
    ),
)
