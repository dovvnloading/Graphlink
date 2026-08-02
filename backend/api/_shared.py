"""ADR-002 stage 2.6: the one genuinely cross-cutting helper every
register_*_intents module needs.

publish_scene was a single closure inside register_canvas's own body
(`async def publish_scene(): await bus.publish("scene")`), invoked by
roughly 60 of its ~89 intents across every feature area - by far the most
pervasive dependency in the file. Once those intents scattered across
many modules, that single closure can no longer be shared directly; each
module instead calls make_publish_scene(bus) once, at the top of its own
register_*_intents function, to get its own equivalent callable. Relocated
verbatim from backend/canvas.py's former register_canvas (lines 288-289).

publish_token_counter (former lines 294-307) is used by exactly two
intents (sendMessage, regenerateResponse), both of which live in
backend/api/intents_chat.py - factored here anyway (not left as a local
closure there) only because intents_chat.py is already the tightest of
the split modules against the ADR's 300-line-per-registration-function
cap; see that module's own docstring.

make_publish_grid (ADR-002 stage 2.6 PR3) is publish_scene's grid-control
counterpart, needed once backend/api/intents_grid.py's own
register_grid_intents becomes the last consumer of register_canvas's
former local `publish_grid` closure. Relocated verbatim from
backend/canvas.py's former register_canvas (former lines 302-303).
"""

from __future__ import annotations

from backend.events import SessionBus


def make_publish_scene(bus: SessionBus):
    async def publish_scene():
        await bus.publish("scene")

    return publish_scene


def make_publish_grid(bus: SessionBus):
    async def publish_grid():
        await bus.publish("grid-control")

    return publish_grid


def make_publish_token_counter(bus: SessionBus):
    # R8a: guarded, unlike publish_scene above - "scene" is registered in
    # register_canvas itself, always present by construction. token_counter's
    # own topic is registered elsewhere (backend/token_counter.py's
    # register_token_counter, called once per session in backend/app.py),
    # and the ~30 pre-R8a canvas/agents tests that construct a bare
    # SessionBus + register_canvas directly never call it - has_topic keeps
    # sendMessage/regenerateResponse working in exactly those tests instead
    # of raising UnknownTopicError the first time either intent runs.
    async def publish_token_counter():
        if bus.has_topic("token-counter"):
            await bus.publish("token-counter")

    return publish_token_counter
