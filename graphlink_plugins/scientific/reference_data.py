"""Versioned registry of bundled scientific reference datasets.

The registry hashes the exact in-tree tables and standard parameter sets used
by calculations. Dictionary and row order are included in the checksum because
order can affect tie-breaking and output tables.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module

from .core import fingerprint


@dataclass(frozen=True)
class Dataset:
    id: str
    version: str
    pack: str
    symbols: tuple[str, ...]
    source: str
    # Historical versions archive their values here when the live pack changes.
    # Structure matches the same [symbol, ordered-value] pairs as current data.
    snapshot: object | None = None


# Append a new version when a bundled standard changes. Archive the old
# version's ordered payload in Dataset.snapshot before changing the live
# tables: old receipts must continue to validate against their own version.
_DATASETS = (
    Dataset("astronomy.iau_galactic_rotation", "1.0.0", "astronomy", ("_EQ_TO_GAL",),
            "Astronomical Almanac ICRS/J2000 Galactic rotation"),
    Dataset("chemistry.ciaaw_abridged_atomic_weights", "2024.0.0", "chemistry",
            ("_ATOMIC_WEIGHTS",), "https://ciaaw.org/abridged-atomic-weights.htm"),
    Dataset("earth.wgs84_ellipsoid", "1.0.0", "earth", ("_WGS_A", "_WGS_F"),
            "NGA World Geodetic System 1984"),
    Dataset("earth.standard_atmosphere_1976", "1.0.0", "earth",
            ("_ISA_LAYERS", "_ISA_RE", "_ISA_R", "_ISA_G"),
            "US Standard Atmosphere 1976 / ISA dry-air layers"),
    Dataset("environment.epa_aqi_breakpoints", "2024.0.0", "environment",
            ("_AQI_INDEX_BANDS", "_AQI_BREAKPOINTS", "_AQI_UNITS", "_AQI_CATEGORIES"),
            "US EPA-454/B-24-002 (May 2024), Table 6"),
    Dataset("environment.briggs_pg_rural", "1.0.0", "environment", ("_PG_RURAL",),
            "Briggs rural Pasquill–Gifford coefficients"),
    Dataset("environment.briggs_pg_urban", "1.0.0", "environment", ("_PG_URBAN",),
            "Briggs urban Pasquill–Gifford coefficients"),
    Dataset("materials.astm_e140_table_1", "2019.0.0", "materials", ("_E140_TABLE_1",),
            "ASTM E140-12b (2019 reapproval), Table 1"),
    Dataset("materials.astm_e140_table_2", "2019.0.0", "materials", ("_E140_TABLE_2",),
            "ASTM E140-12b (2019 reapproval), Table 2"),
)
_REGISTRY = {(item.id, item.version): item for item in _DATASETS}
_ACTIVE = {item.id: item.version for item in _DATASETS}
if len(_REGISTRY) != len(_DATASETS):
    raise ValueError("Duplicate scientific reference dataset ID and version.")

_OPERATION_DATASETS = {
    ("chemistry", "molar_mass"): ("chemistry.ciaaw_abridged_atomic_weights",),
    ("earth", "geodesic_inverse"): ("earth.wgs84_ellipsoid",),
    ("earth", "utm_forward"): ("earth.wgs84_ellipsoid",),
    ("earth", "utm_inverse"): ("earth.wgs84_ellipsoid",),
    ("earth", "isa_atmosphere"): ("earth.standard_atmosphere_1976",),
    ("environment", "air_quality_index"): ("environment.epa_aqi_breakpoints",),
}


def _ordered(value):
    if isinstance(value, dict):
        return [[k, _ordered(v)] for k, v in value.items()]
    if isinstance(value, (tuple, list)):
        return [_ordered(item) for item in value]
    return value


def reference_receipt(dataset_id: str, version: str | None = None) -> dict[str, str]:
    resolved = version if version is not None else _ACTIVE[dataset_id]
    item = _REGISTRY[(dataset_id, resolved)]
    if item.snapshot is not None:
        values = item.snapshot
    else:
        module = import_module(f".packs.{item.pack}", __package__)
        values = [[name, _ordered(getattr(module, name))] for name in item.symbols]
    return {"id": item.id, "version": item.version, "checksum": fingerprint(values)}


def list_reference_datasets() -> tuple[dict[str, str], ...]:
    return tuple({**reference_receipt(item.id, item.version), "source": item.source}
                 for item in _DATASETS)


def references_for_operation(pack: str, operation_id: str, inputs: dict) -> list[dict[str, str]]:
    """Return only datasets consulted by this operation with these inputs."""
    ids = list(_OPERATION_DATASETS.get((pack, operation_id), ()))
    if (pack, operation_id) == ("astronomy", "coordinate_frames"):
        if any(isinstance(inputs.get(key), str) and inputs[key].strip().lower() == "galactic"
               for key in ("source_frame", "target_frame")):
            ids.append("astronomy.iau_galactic_rotation")
    elif (pack, operation_id) == ("environment", "gaussian_plume"):
        terrain = inputs.get("terrain", "")
        if isinstance(terrain, str) and terrain.strip().lower() in ("rural", "urban"):
            ids.append("environment.briggs_pg_" + terrain.strip().lower())
    elif (pack, operation_id) == ("materials", "hardness_e140"):
        table = inputs.get("e140_table")
        selected = ({"table1_non_austenitic_hrc": "materials.astm_e140_table_1",
                     "table2_non_austenitic_hrb": "materials.astm_e140_table_2"}.get(table)
                    if isinstance(table, str) else None)
        if selected:
            ids.append(selected)
    return [reference_receipt(item) for item in sorted(ids)]


def references_match(pack: str, operation_id: str, inputs: dict, actual: object) -> bool:
    """Verify exact dependencies and registered historical versions.

    Old receipts stay readable after a newer dataset becomes active. They must
    refer to the same dataset IDs that the operation used, and their versioned
    checksum must match a retained snapshot (or unchanged live values).
    """
    try:
        current = references_for_operation(pack, operation_id, inputs)
        if type(actual) is not list or len(actual) != len(current):
            return False
        for saved, expected in zip(actual, current):
            if (type(saved) is not dict or set(saved) != {"id", "version", "checksum"}
                    or type(saved.get("id")) is not str or saved["id"] != expected["id"]
                    or type(saved.get("version")) is not str):
                return False
            if saved != reference_receipt(saved["id"], saved["version"]):
                return False
        return True
    except (KeyError, ValueError, TypeError, AttributeError):
        return False
