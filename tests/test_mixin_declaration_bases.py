"""The type-only mixin declaration bases must stay type-only.

Three of them now exist, one per mixin family:

    settings_store/_composed.py       SettingsManagerParts
    backend/domain/_composed.py       SceneDocumentParts
    backend/agent_dispatch/_composed.py  DispatcherParts

Each declares what a mixin's composing class provides, so a checker can read
one mixin in isolation. Together they took `mypy backend` from 1,059 errors
to 844 and eliminated the "*Ops has no attribute" class entirely.

Each also sits LAST in its composed class's MRO, immediately before object,
which makes it the perfect place for an accident: anything defined outside
the `if TYPE_CHECKING:` block becomes a real attribute that shadows nothing
today but would be silently picked up the moment a sibling mixin stopped
defining its own. A fallback nobody asked for, in the one class written to
have no behaviour at all.

tests/test_settings_mixin_contract.py covers SettingsManagerParts in more
depth (signature matching, name-by-name provisioning). This file holds the
properties that must be true of ALL of them, so a fourth base added later is
covered by construction rather than by remembering.
"""

from __future__ import annotations

import pytest

from backend.agent_dispatch._composed import DispatcherParts
from backend.agents import AgentDispatcher
from backend.domain._composed import SceneDocumentParts
from backend.domain.graph import SceneDocument
from graphlink_settings_store import SettingsManager
from settings_store._composed import SettingsManagerParts

# (declaration base, the class that composes it)
BASES = [
    pytest.param(SettingsManagerParts, SettingsManager, id="settings"),
    pytest.param(SceneDocumentParts, SceneDocument, id="domain"),
    pytest.param(DispatcherParts, AgentDispatcher, id="dispatch"),
]


@pytest.mark.parametrize("base, composed", BASES)
def test_the_declaration_base_has_no_runtime_body(base, composed):
    """Nothing but dunders. A method or attribute here would be a real
    implementation in a class whose entire purpose is to have none."""
    own = [name for name in vars(base) if not name.startswith("__")]
    assert own == [], f"{base.__name__} gained a runtime member: {own}"


@pytest.mark.parametrize("base, composed", BASES)
def test_it_defines_no_initializer(base, composed):
    """An __init__ here would land in the composed class's MRO after every
    real mixin and quietly change construction."""
    assert "__init__" not in vars(base)


@pytest.mark.parametrize("base, composed", BASES)
def test_the_base_sits_last_in_the_mro(base, composed):
    """Immediately before object: it must never take precedence over a real
    mixin's implementation of the same name."""
    mro = composed.__mro__
    assert mro[-1] is object
    assert mro[-2] is base, [c.__name__ for c in mro]


@pytest.mark.parametrize("base, composed", BASES)
def test_every_mixin_in_the_family_inherits_it(base, composed):
    """A mixin that does not inherit the base is invisible to the checker
    again - the exact hole these bases were added to close. Catches a new
    sibling landing without one."""
    mixins = [
        cls for cls in composed.__mro__
        if cls not in (composed, base, object) and cls.__name__.endswith("Ops")
    ]
    assert mixins, f"no *Ops mixins found in {composed.__name__}'s MRO"
    missing = [cls.__name__ for cls in mixins if not issubclass(cls, base)]
    assert not missing, (
        f"these {composed.__name__} mixins do not inherit {base.__name__}, so nothing "
        f"declares what they use from the composition: {missing}"
    )
