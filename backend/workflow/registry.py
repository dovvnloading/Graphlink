"""Version-pinned lookup shared by typed and legacy workflow adapters."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any, cast

from .adapter import StepAdapter
from .contracts import AdapterDescriptor


class StepAdapterRegistry[T]:
    """Explicit registrations; never fall back to a different adapter version."""

    def __init__(self) -> None:
        self._adapters: Mapping[tuple[str, str], T] = {}

    def register(self, adapter_id: str, adapter_version: str, adapter: T) -> None:
        if not isinstance(self._adapters, dict):
            raise TypeError("adapter registry is frozen")
        key = (adapter_id, adapter_version)
        if not adapter_id or not adapter_version:
            raise ValueError("adapter ID and version must be nonempty")
        if key in self._adapters:
            raise ValueError(f"adapter already registered: {adapter_id}@{adapter_version}")
        self._adapters[key] = adapter

    def resolve(self, adapter_id: str, adapter_version: str) -> T:
        try:
            return self._adapters[adapter_id, adapter_version]
        except KeyError:
            raise KeyError(f"unknown adapter: {adapter_id}@{adapter_version}") from None

    def keys(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._adapters))

    def with_entries[U](self, entries: Iterable[tuple[str, str, U]]) -> StepAdapterRegistry[T | U]:
        """Extend this registry in a new, immutable snapshot; do not mutate the source."""
        combined = StepAdapterRegistry[T | U]()
        for (adapter_id, version), adapter in self._adapters.items():
            combined.register(adapter_id, version, adapter)
        for adapter_id, version, extra_adapter in entries:
            combined.register(adapter_id, version, extra_adapter)
        combined._adapters = MappingProxyType(combined._adapters)
        return combined

    def describe(self, adapter_id: str, adapter_version: str, config_json: str) -> AdapterDescriptor:
        adapter = self.resolve(adapter_id, adapter_version)
        if not hasattr(adapter, "ports") or not hasattr(adapter, "side_effect_class"):
            raise TypeError(f"adapter has no typed workflow descriptor: {adapter_id}@{adapter_version}")
        typed_adapter = cast(StepAdapter[Any], adapter)
        return AdapterDescriptor(
            adapter_id, adapter_version, typed_adapter.ports(config_json), typed_adapter.side_effect_class,
        )


def builtin_registry() -> StepAdapterRegistry[StepAdapter[Any]]:
    """Return a fresh registry of built-in adapters, with no service dependencies."""
    from .transforms import TRANSFORM_ADAPTERS

    registry = StepAdapterRegistry[StepAdapter[Any]]()
    for adapter in TRANSFORM_ADAPTERS:
        registry.register(adapter.adapter_id, adapter.adapter_version, adapter)
    return registry
