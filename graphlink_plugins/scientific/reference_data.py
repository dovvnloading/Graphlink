"""Versioned scientific reference receipts, with declarations owned by each pack.

Each pack declares REFERENCE_DATASETS alongside its tables and operation
dependencies alongside its operations. Dictionary and row order are included
in the checksum because order can affect tie-breaking and output tables.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module

from .core import Operation, fingerprint


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


# Pack modules export REFERENCE_DATASETS. The registry loads them on demand so
# adding a new pack (or a dataset in that pack) never edits this shared module.
# Append new versions in the owning pack and snapshot old ordered values before
# changing a live table: saved receipts must remain verifiable.
_REGISTRY: dict[tuple[str, str], Dataset] = {}
_ACTIVE: dict[str, str] = {}
_PACK_DATASETS: dict[str, tuple[Dataset, ...]] = {}


def _load_pack_datasets(pack: str) -> tuple[Dataset, ...]:
    if pack not in _PACK_DATASETS:
        module = import_module(f".packs.{pack}", __package__)
        datasets = getattr(module, "REFERENCE_DATASETS", ())
        if not isinstance(datasets, tuple):
            raise ValueError("Scientific reference declarations must be a tuple.")
        keys = set()
        for item in datasets:
            if (not isinstance(item, Dataset) or item.pack != pack
                    or not item.id.startswith(f"{pack}.")
                    or not item.version or not item.symbols or not item.source):
                raise ValueError(f"Invalid scientific reference dataset declaration in {pack}.")
            key = (item.id, item.version)
            if key in keys or key in _REGISTRY:
                raise ValueError("Duplicate scientific reference dataset ID and version.")
            keys.add(key)
        for item in datasets:
            _REGISTRY[(item.id, item.version)] = item
            _ACTIVE[item.id] = item.version
        _PACK_DATASETS[pack] = datasets
    return _PACK_DATASETS[pack]


def _ordered(value):
    if isinstance(value, dict):
        return [[k, _ordered(v)] for k, v in value.items()]
    if isinstance(value, (tuple, list)):
        return [_ordered(item) for item in value]
    return value


def reference_receipt(dataset_id: str, version: str | None = None) -> dict[str, str]:
    _load_pack_datasets(dataset_id.partition(".")[0])
    resolved = version if version is not None else _ACTIVE[dataset_id]
    item = _REGISTRY[(dataset_id, resolved)]
    if item.snapshot is not None:
        values = item.snapshot
    else:
        module = import_module(f".packs.{item.pack}", __package__)
        values = [[name, _ordered(getattr(module, name))] for name in item.symbols]
    return {"id": item.id, "version": item.version, "checksum": fingerprint(values)}


def list_reference_datasets() -> tuple[dict[str, str], ...]:
    from .catalog import PACKS

    for pack in sorted(PACKS):
        _load_pack_datasets(pack)
    return tuple(
        {**reference_receipt(item.id, item.version), "source": item.source}
        for pack in sorted(_PACK_DATASETS) for item in _PACK_DATASETS[pack]
    )


def references_for_operation(
    pack: str, operation_id: str, inputs: dict, *, operation: Operation | None = None,
) -> list[dict[str, str]]:
    """Return only datasets declared by this operation for these inputs."""
    if operation is None:
        from .catalog import PACKS, operations

        # Ad-hoc calculations in the numerical test harness have no catalog.
        if pack not in PACKS:
            return []
        operation = next((op for op in operations(pack) if op.id == operation_id), None)
    if operation is None:
        return []
    declared = operation.reference_datasets
    ids = declared(inputs) if callable(declared) else declared
    if not isinstance(ids, tuple) or any(not isinstance(item, str) for item in ids):
        raise ValueError("Operation reference datasets must be a tuple of IDs.")
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate reference datasets for an operation.")
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
