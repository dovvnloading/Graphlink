"""Version-pinned, immutable lookup of workflow adapter implementations.

Registration and resolution never prepare inputs, authorize operations,
dispatch work, or change wire records.
"""
from collections.abc import Iterable, Iterator
from types import MappingProxyType


class StepAdapterRegistry[T]:
    def __init__(self, entries: Iterable[tuple[str, str, T]]) -> None:
        adapters: dict[tuple[str, str], T] = {}
        for adapter_id, version, adapter in entries:
            key = (adapter_id, version)
            if not adapter_id or not version:
                raise ValueError("An adapter needs an ID and version.")
            if key in adapters:
                raise ValueError(f"Duplicate workflow adapter: {adapter_id}@{version}")
            adapters[key] = adapter
        self._adapters = MappingProxyType(adapters)

    def require(self, adapter_id: str, version: str) -> T:
        """Resolve the exact version, never fall back to a newer adapter."""
        return self._adapters[adapter_id, version]

    def __iter__(self) -> Iterator[tuple[str, str]]:
        return iter(self._adapters)
