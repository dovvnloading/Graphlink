"""Scientific reference registry and exact enzyme/mass unit regression tests."""
from copy import deepcopy
from fractions import Fraction

import pytest

from graphlink_plugins.scientific.catalog import operations
from graphlink_plugins.scientific.core import VERSION, fingerprint, readable_result, run_operation
from graphlink_plugins.scientific.reference_data import (
    list_reference_datasets, reference_receipt, references_for_operation,
)
from graphlink_plugins.scientific.units import convert_measurement, parse_unit


def run(pack, op_id, **inputs):
    op = next(op for op in operations(pack) if op.id == op_id)
    return run_operation(pack, op, {**op.example, **inputs})


def readable(pack, result):
    return readable_result({"version": 1, "latest": result}, pack)


@pytest.mark.parametrize(("pack", "op"), [
    ("core", "convert_unit"), ("biology", "michaelis_menten_fit"),
    ("chemistry", "molar_mass"), ("earth", "geodesic_inverse"),
    ("earth", "utm_forward"), ("earth", "isa_atmosphere"),
    ("environment", "air_quality_index"), ("environment", "gaussian_plume"),
    ("materials", "hardness_e140"), ("astronomy", "coordinate_frames"),
])
def test_receipts_pin_exact_used_datasets(pack, op):
    result = run(pack, op)
    assert result["provenance"]["packVersion"] == VERSION == "1.2.0"
    assert result["provenance"]["referenceData"] == references_for_operation(pack, op, result["inputs"])
    assert readable(pack, result)


def test_registry_inventory_has_unique_versioned_checksums():
    datasets = list_reference_datasets()
    assert len(datasets) == 9
    assert len({(item["id"], item["version"]) for item in datasets}) == 9
    assert all(len(item["checksum"]) == 64 and item["source"] for item in datasets)


def test_selected_hardness_plume_and_galactic_datasets_only():
    hrc = run("materials", "hardness_e140", e140_table="table1_non_austenitic_hrc",
              from_scale="HRC", to_scale="HV", hardness_reading=40)
    hrb = run("materials", "hardness_e140", e140_table="table2_non_austenitic_hrb",
              from_scale="HRB", to_scale="HV", hardness_reading=80)
    assert hrc["provenance"]["referenceData"] == [reference_receipt("materials.astm_e140_table_1")]
    assert hrb["provenance"]["referenceData"] == [reference_receipt("materials.astm_e140_table_2")]
    assert run("environment", "gaussian_plume")["provenance"]["referenceData"] == [
        reference_receipt("environment.briggs_pg_rural")]
    assert run("environment", "gaussian_plume", terrain="urban")["provenance"]["referenceData"] == [
        reference_receipt("environment.briggs_pg_urban")]
    assert run("astronomy", "coordinate_frames")["provenance"]["referenceData"] == []
    assert run("astronomy", "coordinate_frames", target_frame="galactic")["provenance"]["referenceData"] == [
        reference_receipt("astronomy.iau_galactic_rotation")]


@pytest.mark.parametrize("mutation", ["missing", "empty", "checksum", "version", "extra"])
def test_forged_reference_rejected_even_with_rehashed_receipt(mutation):
    result = deepcopy(run("chemistry", "molar_mass"))
    receipt = result["provenance"]
    refs = receipt["referenceData"]
    if mutation == "missing":
        del receipt["referenceData"]
    elif mutation == "empty":
        receipt["referenceData"] = []
    elif mutation == "checksum":
        refs[0]["checksum"] = "0" * 64
    elif mutation == "version":
        refs[0]["version"] = "2099.0.0"
    else:
        refs.append(reference_receipt("earth.wgs84_ellipsoid"))
    receipt["receiptHash"] = fingerprint({k: v for k, v in receipt.items() if k != "receiptHash"})
    assert not readable("chemistry", result)


def test_old_110_receipts_remain_readable():
    result = run("chemistry", "molar_mass")
    receipt = result["provenance"]
    del receipt["referenceData"]
    receipt["packVersion"] = "1.1.0"
    output = {k: result[k] for k in ("summary", "values", "table")}
    receipt["resultHash"] = fingerprint(["chemistry", "1.1.0", result["operationId"],
                                         receipt["inputHash"], output])
    receipt["receiptHash"] = fingerprint({k: v for k, v in receipt.items() if k != "receiptHash"})
    assert readable("chemistry", result)


def test_u_and_da_units_with_prefixes_and_compounds():
    assert parse_unit("U").scale == Fraction(1, 60_000_000)
    assert parse_unit("U").dimensions == parse_unit("kat").dimensions
    assert parse_unit("mU").scale == parse_unit("U").scale / 1000
    assert convert_measurement(1, "U", "µmol/min") == pytest.approx(1)
    assert convert_measurement(1, "kU", "kat") == pytest.approx(1 / 60_000)
    assert parse_unit("Da").scale == parse_unit("u").scale
    assert parse_unit("Da").dimensions == parse_unit("kg").dimensions
    assert convert_measurement(2, "kDa", "Da") == pytest.approx(2000)
    assert convert_measurement(1, "Da", "kg") == pytest.approx(1.66053906660e-27)
    assert parse_unit("U/mL").dimensions == parse_unit("kat/L").dimensions
    with pytest.raises(ValueError, match="Incompatible SI dimensions"):
        convert_measurement(1, "Da", "U")
