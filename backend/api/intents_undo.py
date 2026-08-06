"""ADR-010 stage 10.2/10.4/10.5: the undo/redo intent surface.

The stack itself lives in backend/domain/commands.py (CommandOps); this
module is only the WS-intent wrapper around it, in the same shape as every
other intents_*.py module - call the domain, then publish.

Undo/redo state (can-undo, can-redo, and the action labels) rides the scene
topic's own payload rather than a separate topic, because it changes on
exactly the same events the scene does: every mutation, every undo, every
redo, and a session load. A separate topic would need its own publish call
bolted onto all ~80 mutating intents to stay in sync, and would be wrong the
moment one was missed.
"""

from __future__ import annotations

from backend.api._shared import make_publish_scene
from backend.domain.commands import UndoRefusedError
from backend.domain.graph import SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState


def register_undo_intents(
    bus: SessionBus,
    document: SceneDocument,
    notifications: NotificationState,
) -> None:
    publish_scene = make_publish_scene(bus)

    async def _refuse(message: str) -> None:
        """A refusal is a normal, expected outcome (nothing to undo; a node
        is still generating), not an error - it surfaces through the same
        notification banner every other user-facing message uses, rather
        than raising back through the intent layer as a failure."""
        notifications.show(message, msg_type="info")
        await bus.publish("notification")

    async def undo():
        try:
            command = document.undo()
        except UndoRefusedError as exc:
            await _refuse(str(exc))
            return None
        await publish_scene()
        return command.label

    async def redo():
        try:
            command = document.redo()
        except UndoRefusedError as exc:
            await _refuse(str(exc))
            return None
        await publish_scene()
        return command.label

    async def undo_run(run_id):
        """ADR-010 stage 10.5: reverse a whole agent build in one action.
        Deliberately reports how many steps came back rather than staying
        silent - "undo this build" reversing 7 nodes should say so, since
        the user cannot count them once they are gone."""
        try:
            count = document.undo_run(str(run_id))
        except UndoRefusedError as exc:
            await _refuse(str(exc))
            return 0
        if count == 0:
            await _refuse("Nothing from that run is left to undo.")
            return 0
        await publish_scene()
        return count

    bus.register_intent("scene", "undo", undo)
    bus.register_intent("scene", "redo", redo)
    bus.register_intent("scene", "undoRun", undo_run)
