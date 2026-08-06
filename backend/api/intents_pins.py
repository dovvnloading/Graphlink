"""ADR-002 stage 2.6 (PR3/3, the final slice): navigation pins.

Relocated VERBATIM from backend/canvas.py's former register_canvas
(closures at lines 388-409; registration calls at lines 435-438) - pure
code motion, no behavior change.
"""

from __future__ import annotations

from backend.api._shared import make_publish_scene
from backend.domain.graph import SceneDocument
from backend.events import SessionBus


def register_pins_intents(bus: SessionBus, document: SceneDocument) -> None:
    publish_scene = make_publish_scene(bus)

    # ADR-010 close-out: pins are List A (undoable) - "pin" is named
    # explicitly in the classification rule's A definition. record_command's
    # own pin support snapshots the WHOLE store per call (see
    # backend/domain/commands.py), so no node_ids/edge_ids-style scoping
    # parameter is needed here - the wrap is a one-line change per intent.
    async def add_pin(title, x, y, note=""):
        # Kwargs form, NOT a pre-built NavigationPinRecord: only the
        # record=None path inside NavigationPinStore.add() auto-assigns the
        # incrementing sort_order (len(self._records)) - a pre-built record
        # is appended as-is with the dataclass's sort_order=0 default, which
        # left every pin after the first at sort_order 0 (the persisted
        # ordering key chat_library.py loads by). Validation is unchanged:
        # both paths run NavigationPinRecord.create()'s field validators.
        record, _command = document.record_command(
            "addPin", "user",
            lambda: document.pins.add(title=title, x=x, y=y, note=note),
        )
        await publish_scene()
        return record.pin_id

    async def move_pin(pin_id, x, y):
        document.record_command(
            "movePin", "user", lambda: document.pins.move(pin_id, x, y),
        )
        await publish_scene()

    async def remove_pin(pin_id):
        document.record_command(
            "removePin", "user", lambda: document.pins.remove(pin_id),
        )
        await publish_scene()

    async def update_pin(pin_id, title, note):
        # NavigationPinRecord.create() validation (non-empty/length-bounded
        # title, length-bounded note) runs via with_updates -> create's own
        # field validators, same as add_pin's path - a bad edit raises
        # NavigationPinValidationError, which is a ValueError subclass and
        # therefore already reported to the caller as an intent error. That
        # raise happens INSIDE the record_command-wrapped mutator, before
        # the store is actually touched, so a rejected edit produces no
        # command at all - nothing partial to undo.
        document.record_command(
            "updatePin", "user",
            lambda: document.pins.update(pin_id, title=str(title), note=str(note)),
        )
        await publish_scene()

    bus.register_intent("scene", "addPin", add_pin)
    bus.register_intent("scene", "movePin", move_pin)
    bus.register_intent("scene", "removePin", remove_pin)
    bus.register_intent("scene", "updatePin", update_pin)
