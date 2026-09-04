"""Guard the guard: settings_store/_composed.py must stay type-only.

SettingsManagerParts exists so a type checker can see the contract between
the ten settings_store mixins and the SettingsManager that composes them.
It sits LAST in that class's MRO, immediately before object, which makes it
the perfect place for an accident: anything defined there outside the
`if TYPE_CHECKING:` block becomes a real attribute that silently shadows
nothing today, but would be picked up the moment a sibling mixin stopped
defining its own - a fallback nobody asked for, in the one class written to
have no behaviour at all.

Same posture as tests/test_domain_purity.py and test_node_state_migration.py:
the property is cheap to assert and expensive to notice by eye. This is also
the reason [tool.mypy].files can include settings_store at all, so it is
worth keeping honest.
"""

from __future__ import annotations

import inspect

from graphlink_settings_store import SettingsManager
from settings_store._composed import SettingsManagerParts

# Everything SettingsManagerParts declares, and who really supplies it. Kept
# explicit rather than derived: the point is to notice when the list changes.
_DECLARED_INSTANCE_SURFACE = ("state", "state_file", "_save_state", "_protect_and_track")
_DECLARED_CLASS_CONSTANTS = (
    "NOTIFICATION_TYPES", "REASONING_LEVELS", "LOG_LEVELS", "THEMES",
    "SECRET_KEYS", "OLLAMA_MODEL_TASKS", "LEGACY_PRODUCT_MODEL_IDS",
    "CURRENT_SCHEMA_VERSION",
)


def test_the_declaration_base_has_no_runtime_body():
    """Nothing but dunders. A method or attribute here would be a real
    implementation in a class whose entire purpose is to have none."""
    own = [name for name in vars(SettingsManagerParts) if not name.startswith("__")]
    assert own == [], f"SettingsManagerParts gained a runtime member: {own}"


def test_it_defines_no_initializer():
    """An __init__ here would land in SettingsManager's MRO after every real
    mixin and quietly change construction."""
    assert "__init__" not in vars(SettingsManagerParts)


def test_every_declared_name_is_really_provided_by_the_composed_class():
    """The declaration has to describe reality, or it is just a way to make
    mypy agree with a lie. A name dropped from SettingsManager (or renamed)
    must fail here rather than keep type-checking against nothing."""
    manager = SettingsManager.__new__(SettingsManager)  # no __init__: class surface only
    for name in _DECLARED_CLASS_CONSTANTS:
        assert hasattr(type(manager), name), f"SettingsManager no longer defines {name!r}"
    for name in ("_save_state", "_protect_and_track"):
        assert callable(getattr(type(manager), name, None)), f"SettingsManager no longer defines {name!r}()"


def test_protect_and_track_signature_matches_the_declaration():
    """The one declared method with a non-trivial signature. A drift here
    type-checks clean and breaks at runtime."""
    real = inspect.signature(SettingsManager._protect_and_track)
    assert list(real.parameters) == ["self", "value"]


def test_the_base_sits_last_in_the_mro():
    """Immediately before object: it must never take precedence over a real
    mixin's implementation of the same name."""
    mro = SettingsManager.__mro__
    assert mro[-1] is object
    assert mro[-2] is SettingsManagerParts
