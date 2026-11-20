"""What every settings_store mixin relies on its composing class to provide.

The ten `*Ops` classes in this package are mixins, not standalone types:
each is composed exactly once, by graphlink_settings_store.py's
`class SettingsManager(PersistenceOps, GeneralSettingsOps, ...)`. They
freely use `self.state`, `self._save_state()` and the class constants
(`self.NOTIFICATION_TYPES`, `self.REASONING_LEVELS`, ...) that only exist
on the composed whole - correct at runtime, and completely invisible to a
type checker looking at one mixin in isolation.

Mixin-based components are difficult for static type checkers to analyze
in isolation: the shared attributes are provided by the composed class,
not declared on individual mixins. The same pattern appears in the
backend/agent_dispatch/ and backend/domain/ mixin families.

Declaring the contract in one place makes these attributes explicit
without changing runtime behaviour.

EVERYTHING HERE IS TYPE_CHECKING-ONLY. At runtime this class is empty, so
inheriting it adds no attributes, no methods, and no `__init__` - the real
implementations still come from PersistenceOps and from SettingsManager's
own class body, exactly as before. It is a declaration of what the
composed object has, not a second source of it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


class SettingsManagerParts:
    """Type-only declaration of the composed SettingsManager's shared surface.

    Mixins in this package inherit it so a checker can see what they use.
    PersistenceOps inherits it too - it supplies `state`/`_save_state`/
    `_protect_and_track` itself, but consumes the class constants like any
    sibling.
    """

    if TYPE_CHECKING:
        # Supplied by PersistenceOps (assigned in its __init__, or defined
        # as real methods on it - these annotations are overridden by those
        # real definitions, never the other way round).
        _api_catalog_requests: dict[str, object]
        state: dict[str, Any]
        state_file: Any

        def _save_state(self, state_data: dict[str, Any] | None = None, *, raise_on_error: bool = False) -> None: ...

        def _protect_and_track(self, value: str) -> str: ...

        # Supplied by SettingsManager's own class body. Closed vocabularies
        # and lookup tables the mixins validate against.
        NOTIFICATION_TYPES: tuple[str, ...]
        REASONING_LEVELS: tuple[str, ...]
        LOG_LEVELS: tuple[str, ...]
        THEMES: tuple[str, ...]
        SECRET_KEYS: tuple[str, ...]
        OLLAMA_MODEL_TASKS: tuple[Any, ...]
        LEGACY_PRODUCT_MODEL_IDS: set[str]
        CURRENT_SCHEMA_VERSION: int
