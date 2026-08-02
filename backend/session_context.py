"""SessionContext - ADR-002 stage 2.1d.

SessionBus (backend/events.py) is deliberately a generic pub/sub
primitive with no fixed attribute set. Two pieces of real session state -
the AgentDispatcher and the SceneDocument - are nonetheless read from
OTHER modules than the one that creates them: backend/app.py creates
both (in _configure_session), and backend/assets.py plus backend/app.py's
own disconnect-cleanup path read them back. Before this module, that
cross-module read was a bare dynamic attribute (bus.agent_dispatcher,
bus.canvas_document) with no guard - a SessionBus built without going
through _configure_session (any test that constructs one directly) has
neither attribute, and a read raised a bare AttributeError several
frames from the real cause.

This module is the one typed seam for that cross-module read.

Deliberately narrower than originally scoped: backend/chat_library.py's
bus.chat_mutation_guard/bus.chat_save_state and backend/autosave.py's
bus.autosave_guarded_tick/bus.autosave_task were also proposed for this
treatment. A repo-wide grep before writing this module found zero
PRODUCTION/cross-module readers of any of the four (backend/chat_library.py
and backend/autosave.py's own test files DO read them extensively -
~20 sites in backend/tests/test_chat_library.py alone - but no non-test
module anywhere in the repo ever does, unlike agent_dispatcher/
canvas_document, which backend/app.py and backend/assets.py both read).
Moving all four here would force every test that calls
register_chat_library/register_autosave against a bare SessionBus (close
to a dozen) to also construct a SessionContext first, for no production
benefit - so they stay exactly as they are, plain dynamic bus attributes.

One concrete seam where this narrower scope could age: backend/autosave.py's
own docstring on bus.autosave_task names a "future graceful-shutdown path"
that would cancel it on last-disconnect - symmetric to how this module's
agent_dispatcher already gets cancelled there (backend/app.py's
ws_endpoint). No such code exists today (confirmed by the same grep), but
if it's ever added, autosave_task would need exactly this treatment too.

Lives in its own leaf module rather than backend/events.py: SessionBus
must stay free of the domain layer (SceneDocument lives in
backend/canvas.py, AgentDispatcher in backend/agents.py; both already
import backend.events for SessionBus's own type hint, so the reverse
import would cycle). This module imports events.py, canvas.py, and
agents.py - none of which import it back.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.agents import AgentDispatcher
from backend.canvas import SceneDocument
from backend.events import SessionBus

_ATTR = "_session_context"


@dataclass(frozen=True)
class SessionContext:
    """Frozen (ADR-002 stage 2.1d adversarial review finding): agent_dispatcher/
    canvas_document are ALSO captured independently by closure inside
    register_canvas/register_plugins/register_chat_library at
    _configure_session time, while backend/assets.py instead re-reads via
    get_session_context() fresh on every request. Nothing today reassigns
    either field on an existing SessionContext, but if something ever did,
    those two paths would silently fork onto different objects - frozen
    makes that structurally impossible rather than merely currently
    unexercised."""

    agent_dispatcher: AgentDispatcher
    canvas_document: SceneDocument


class SessionNotConfiguredError(RuntimeError):
    """Raised by get_session_context() when nothing was ever attached -
    e.g. a SessionBus built directly instead of through
    backend.app._configure_session (the only real caller of
    attach_session_context in production)."""


def attach_session_context(bus: SessionBus, context: SessionContext) -> None:
    setattr(bus, _ATTR, context)


def get_session_context(bus: SessionBus) -> SessionContext:
    context = getattr(bus, _ATTR, None)
    if context is None:
        raise SessionNotConfiguredError(
            f"SessionBus {bus.session_id!r} has no SessionContext attached - "
            "it was not built via backend.app._configure_session. If this is "
            "a test constructing a SessionBus directly, call "
            "attach_session_context(bus, SessionContext(...)) first."
        )
    return context
