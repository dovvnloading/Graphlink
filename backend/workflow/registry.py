"""Version-pinned step adapter lookup, independent of the live runner."""

from __future__ import annotations

from typing import Any

from .adapter import StepAdapter
from .contracts import AdapterDescriptor


class StepAdapterRegistry:
    """Explicit registrations; never fall back to a different adapter version."""

    def __init__(self) -> None:
        self._adapters: dict[tuple[str, str], StepAdapter[Any]] = {}

    def register(self, adapter_id: str, adapter_version: str, adapter: StepAdapter[Any]) -> None:
        key = (adapter_id, adapter_version)
        if not adapter_id or not adapter_version:
            raise ValueError("adapter ID and version must be nonempty")
        if key in self._adapters:
            raise ValueError(f"adapter already registered: {adapter_id}@{adapter_version}")
        self._adapters[key] = adapter

    def resolve(self, adapter_id: str, adapter_version: str) -> StepAdapter[Any]:
        try:
            return self._adapters[adapter_id, adapter_version]
        except KeyError:
            raise KeyError(f"unknown adapter: {adapter_id}@{adapter_version}") from None

    def keys(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._adapters))

    def describe(self, adapter_id: str, adapter_version: str, config_json: str) -> AdapterDescriptor:
        adapter = self.resolve(adapter_id, adapter_version)
        return AdapterDescriptor(adapter_id, adapter_version, adapter.ports(config_json), adapter.side_effect_class)


def builtin_registry() -> StepAdapterRegistry:
    """Return a fresh registry of built-in adapters, with no service dependencies."""
    from .transforms import TRANSFORM_ADAPTERS

    registry = StepAdapterRegistry()
    for adapter in TRANSFORM_ADAPTERS:
        registry.register(adapter.adapter_id, adapter.adapter_version, adapter)
    return registry
