"""Typed SI input measurements across every Scientific Workbench pack."""

from copy import deepcopy

import pytest

from graphlink_plugins.scientific.catalog import PACKS, operations
from graphlink_plugins.scientific.core import (
    Field, Operation, fingerprint, readable_result, run_operation,
    validated_inputs, validated_inputs_with_units,
)
from graphlink_plugins.scientific.units import dimension_name, parse_unit


@pytest.mark.parametrize("pack", PACKS)
def test_every_numeric_field_publishes_a_unit_and_dimension(pack):
    for operation in operations(pack):
        schema = operation.describe()
        assert len(schema["fields"]) == len(operation.fields)
        for field in operation.fields:
            if field.kind == "text":
                continue
            assert field.default_unit, (pack, operation.id, field.key)
            if field.column_units:
                assert field.dimension.startswith("mixed:")
                assert len(field.column_units) == len(field.column_dimensions)
                assert all(dimension_name(parse_unit(unit).dimensions) == dimension
                           for unit, dimension in zip(field.column_units, field.column_dimensions))
            else:
                assert field.dimension == dimension_name(parse_unit(field.default_unit).dimensions)
            published = next(item for item in schema["fields"] if item["key"] == field.key)
            assert published["default_unit"] == field.default_unit
            assert published["dimension"] == field.dimension


def probe():
    return Operation(
        "unit_probe", "Unit probe", "Echo normalized fields", (
            Field("distance", "Distance", "number", unit="m"),
            Field("pressure", "Pressure", "number", unit="Pa"),
            Field("temperature", "Absolute temperature", "number", unit="K"),
            Field("rise", "Temperature difference", "number", unit="K", default_unit="delta_K"),
            Field("samples", "Distances", "vector", unit="m"),
            Field("grid", "Pressure matrix", "matrix", unit="Pa"),
            Field("count", "Count", "integer", unit="count"),
        ), {
            "distance": 0.025, "pressure": 1200, "temperature": 273.15,
            "rise": 10, "samples": [0.025, 0.02],
            "grid": [[1200, 1]], "count": 2,
        }, lambda data: {
            "summary": "Normalized", "values": {"distance_m": data["distance"],
              "pressure_pa": data["pressure"], "temperature_k": data["temperature"],
              "rise_k": data["rise"]},
        },
    )


def literals():
    return {
        "distance": "25 mm", "pressure": "1.2 kPa", "temperature": "32 degF",
        "rise": "18 delta_degF", "samples": ["25 mm", "2 cm"],
        "grid": [["1.2 kPa", "1 Pa"]], "count": "2 count",
    }


def test_exact_si_conversions_and_units_in_receipt():
    result = run_operation("core", probe(), literals())
    assert result["inputs"]["distance"] == pytest.approx(0.025)
    assert result["inputs"]["pressure"] == 1200
    assert result["inputs"]["temperature"] == pytest.approx(273.15, abs=1e-12)
    assert result["inputs"]["rise"] == 10
    assert result["inputs"]["samples"] == pytest.approx([0.025, 0.02])
    assert result["inputs"]["grid"] == [[1200, 1]]
    assert result["inputs"]["count"] == 2
    assert result["provenance"]["inputUnits"] == {
        "distance": "mm", "pressure": "kPa", "temperature": "degF",
        "rise": "delta_degF", "samples": ["mm", "cm"],
        "grid": [["kPa", "Pa"]], "count": "count",
    }
    assert readable_result({"version": 1, "latest": result}, "core")


def test_affine_temperature_points_are_not_temperature_differences():
    op = probe()
    base = literals()
    base["temperature"] = "80 degF"
    assert validated_inputs(op, base)["temperature"] == pytest.approx(299.81666666666666)
    base["temperature"] = "25 degC"
    assert validated_inputs(op, base)["temperature"] == pytest.approx(298.15)
    base["temperature"] = "-459.67 degF"
    assert validated_inputs(op, base)["temperature"] == pytest.approx(0, abs=1e-12)
    base["temperature"] = "-459.68 degF"
    with pytest.raises(ValueError, match="Absolute temperature.*0 K"):
        validated_inputs(op, base)
    base["temperature"] = "273.15 K"
    base["rise"] = "10 K"
    assert validated_inputs(op, base)["rise"] == 10
    base["rise"] = "18 degF"
    with pytest.raises(ValueError, match="temperature.*difference"):
        validated_inputs(op, base)


@pytest.mark.parametrize("bad,expected", [
    ("4 kg", "length"), ("7 furlongs", "length"),
    ("1e999 mm", "range"), ("not-a-value", "length"),
])
def test_invalid_units_and_values_report_expected_dimension(bad, expected):
    raw = literals()
    raw["distance"] = bad
    with pytest.raises(ValueError, match=expected):
        validated_inputs(probe(), raw)


def test_bare_numbers_and_old_saved_receipts_stay_valid():
    op = probe()
    bare = dict(op.example)
    normalized, used = validated_inputs_with_units(op, bare)
    assert normalized == {**bare, "distance": float(bare["distance"]),
                           "pressure": float(bare["pressure"]),
                           "temperature": float(bare["temperature"]),
                           "rise": float(bare["rise"]),
                           "samples": list(map(float, bare["samples"])),
                           "grid": [[float(v) for v in row] for row in bare["grid"]]}
    assert used["distance"] == "m"
    assert used["rise"] == "delta_K"
    result = run_operation("core", op, bare)
    old = deepcopy(result)
    receipt = old["provenance"]
    del receipt["inputUnits"]
    receipt["receiptHash"] = fingerprint({key: value for key, value in receipt.items() if key != "receiptHash"})
    assert readable_result({"version": 1, "latest": old}, "core")
    assert result["provenance"]["inputHash"] == old["provenance"]["inputHash"]
    assert result["provenance"]["resultHash"] == old["provenance"]["resultHash"]


def test_existing_pack_calculation_is_identical_to_bare_defaults():
    op = next(operation for operation in operations("materials") if operation.id == "bulk_density")
    saved = run_operation("materials", op, op.example)
    converted = run_operation("materials", op, {
        "dry_mass_g": "0.0375 kg", "bulk_volume_cm3": "0.015 L",
    })
    assert converted["inputs"]["dry_mass_g"] == pytest.approx(37.5)
    assert converted["inputs"]["bulk_volume_cm3"] == pytest.approx(15)
    assert converted["values"]["bulk_density_kg_m3"] == pytest.approx(
        saved["values"]["bulk_density_kg_m3"]
    )



def test_mixed_unit_materials_matrix_converts_each_column_independently():
    operation = next(op for op in operations("materials") if op.id == "stress_strain_analysis")
    field = operation.fields[0]
    assert field.column_units == ("mm/mm", "MPa")
    original = run_operation("materials", operation, operation.example)
    raw = deepcopy(operation.example)
    raw["engineering_curve"][1] = ["0.05 %", "0.1 GPa"]
    converted = run_operation("materials", operation, raw)
    assert converted["inputs"]["engineering_curve"][1] == pytest.approx([0.0005, 100])
    assert converted["provenance"]["inputUnits"]["engineering_curve"][1] == ["%", "GPa"]
    assert converted["values"] == original["values"]



def test_integer_inputs_reject_rounded_fractional_counts():
    raw = literals()
    for count in ("2.0000000000000001 count", "0.5 count", "9007199254740993 count"):
        raw["count"] = count
        with pytest.raises(ValueError, match="exact integer"):
            validated_inputs(probe(), raw)


def test_fracture_toughness_converts_between_compatible_stress_sqrt_length_units():
    op = next(operation for operation in operations("materials") if operation.id == "fracture_critical_length")
    raw = dict(op.example)
    raw["fracture_toughness_mpa_sqrt_m"] = "1000 kPa√m"
    result = run_operation("materials", op, raw)
    assert result["inputs"]["fracture_toughness_mpa_sqrt_m"] == 1
    assert result["provenance"]["inputUnits"]["fracture_toughness_mpa_sqrt_m"] == "kPa√m"



def test_spawn_worker_preserves_units_on_real_workbench_execution():
    import asyncio
    from graphlink_plugins.scientific.execution import execute_in_process

    operation = next(op for op in operations("materials") if op.id == "bulk_density")
    normalized, used = validated_inputs_with_units(operation, {
        "dry_mass_g": "0.0375 kg", "bulk_volume_cm3": "0.015 L",
    })

    async def run_worker():
        return await execute_in_process(
            "materials", operation, normalized, None, timeout_s=20,
            cancel_event=asyncio.Event(), input_units=used,
        )

    result = asyncio.run(run_worker())
    assert result["inputs"] == {"dry_mass_g": 37.5, "bulk_volume_cm3": 15.0}
    assert result["provenance"]["inputUnits"] == {
        "dry_mass_g": "kg", "bulk_volume_cm3": "L",
    }
    assert readable_result({"version": 1, "latest": result}, "materials")
