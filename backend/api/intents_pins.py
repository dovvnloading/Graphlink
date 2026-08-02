"""ADR-002 stage 2.6 (PR3/3, the final slice): navigation pins.

Relocated VERBATIM from backend/canvas.py's former register_canvas
(closures at lines 388-409; registration calls at lines 435-438) - pure
code motion, no behavior change.
"""

from __future__ import annotations

from graphlink_navigation_pins import NavigationPinRecord

from backend.api._shared import make_publish_scene
from backend.domain.graph import SceneDocument
from backend.events import SessionBus


def register_pins_intents(bus: SessionBus, document: SceneDocument) -> None:
    publish_scene = make_publish_scene(bus)

    async def add_pin(title, x, y, note=""):
        record = NavigationPinRecord.create(title=title, x=x, y=y, note=note)
        document.pins.add(record)
        await publish_scene()
        return record.pin_id

    async def move_pin(pin_id, x, y):
        document.pins.move(pin_id, x, y)
        await publish_scene()

    async def remove_pin(pin_id):
        document.pins.remove(pin_id)
        await publish_scene()

    async def update_pin(pin_id, title, note):
        # NavigationPinRecord.create() validation (non-empty/length-bounded
        # title, length-bounded note) runs via with_updates -> create's own
        # field validators, same as add_pin's path - a bad edit raises
        # NavigationPinValidationError, which is a ValueError subclass and
        # therefore already reported to the caller as an intent error.
        document.pins.update(pin_id, title=str(title), note=str(note))
        await publish_scene()

    bus.register_intent("scene", "addPin", add_pin)
    bus.register_intent("scene", "movePin", move_pin)
    bus.register_intent("scene", "removePin", remove_pin)
    bus.register_intent("scene", "updatePin", update_pin)
