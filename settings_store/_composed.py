"""What every settings_store mixin relies on its composing class to provide.

The ten `*Ops` classes in this package are mixins, not standalone types:
each is composed exactly once, by graphlink_settings_store.py's
`class SettingsManager(PersistenceOps, GeneralSettingsOps, ...)`. They
freely use `self.state`, `self._save_state()` and the class constants
(`self.NOTIFICATION_TYPES`, `self.REASONING_LEVELS`, ...) that only exist
on the composed whole - correct at runtime, and completely invisible to a
type checker looking at one mixin in isolation.

That invisibility was not free. `mypy settings_store` reported 115 errors,
every single one `attr-defined`, and 101 of them were just three names:
`state` (66), `_save_state` (33) and `_protect_and_track` (2). The
remaining 14 were class constants. In other words the package was not
badly typed - it was untypeable, because nothing declared the contract
between a mixin and the class that composes it. That is the same shape as
the 23 `*Ops` mixins across backend/agent_dispatch/ and backend/domain/,
and it is a large part of why `[tool.mypy].files` has stayed at four
entries while the real error count grew from 642 to over a thousand.

Declaring the contract in one place fixes all 115 without changing a line
of behaviour.

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
        state: dict[str, Any]
        state_file: Any

        def _save_state(self, state_data: dict[str, Any] | None = None) -> None: ...

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
