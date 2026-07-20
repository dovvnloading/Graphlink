"""Desktop-side state bridge for the command-palette island.

Snapshot-and-execute shape, distinct from both composer's live-editing shape
and notification's event-push shape: Python snapshots CommandManager's
commands once when the palette opens, JS filters/navigates that snapshot
entirely client-side (no per-keystroke round trip), and exactly one real
intent flows back - executeCommand(id) - which re-validates against
CommandManager's LIVE state (not the snapshot) before ever calling a
command's callback. This is the concrete meaning of the migration checklist's
"availability snapshotted at open; executeCommand(id) re-validates at execute
time (modal exec() -> async callback)".

Wire ids are simply the command's index in the snapshot taken at open() -
safe because CommandManager.register_command() is only ever called once, at
startup, from WindowNavigationMixin._setup_commands() (verified: grep finds
no other call site) - so CommandManager.commands is append-only-then-frozen
for the rest of the process's life. No id concept needs to live on
CommandManager itself; identity is purely a wire-layer concern here.

CommandManager's callback/condition are raw Python callables and must never
reach JS - _build_state_payload() only ever emits id/name/aliases per
command, never the dict entries themselves.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from graphlink_island_bridge import IslandBridge


class CommandPaletteBridge(IslandBridge, QObject):
    stateChanged = Signal(str)
    # Qt-only side channel; see NotificationBridge's identical field.
    # CommandPaletteWebHost connects this straight to setVisible() so the
    # host's real Qt-level visibility matches "is the palette actually open"
    # rather than whatever visibility it happened to inherit from its parent.
    visibilityChanged = Signal(bool)

    def __init__(self, command_manager, parent=None):
        QObject.__init__(self, parent)
        IslandBridge.__init__(self)
        self._command_manager = command_manager
        self._visible = False
        self._notice: str | None = None
        # Snapshot of CommandManager.commands taken by open(); list index is
        # the wire id for this open session. Deliberately the FULL list, not
        # get_available_commands()'s already-filtered subset - condition() is
        # re-evaluated fresh in _build_state_payload() every publish, so a
        # command whose availability changes while the palette sits open
        # (e.g. selection cleared) drops out of the next snapshot on its own.
        self._commands: list[dict[str, Any]] = []

    def _transport_send(self, payload_json: str) -> None:
        self.stateChanged.emit(payload_json)

    def _build_state_payload(self) -> dict[str, Any]:
        commands = [
            {"id": str(index), "name": cmd["name"], "aliases": cmd["aliases"]}
            for index, cmd in enumerate(self._commands)
            if cmd["condition"]()
        ]
        return {
            "visible": self._visible,
            "commands": commands,
            "notice": self._notice,
        }

    @Slot()
    def ready(self):
        self.publish()

    def open(self) -> None:
        """Python-only entry point - never a Slot, only show_command_palette()
        calls this (JS never opens the palette itself). Cheap to call even
        though it looks like a full re-snapshot: CommandManager.commands is
        frozen after startup, so this just takes a fresh list reference, not
        real registration work."""
        was_visible = self._visible
        self._commands = list(self._command_manager.commands)
        self._notice = None
        self._visible = True
        self.publish()
        if not was_visible:
            self.visibilityChanged.emit(True)

    @Slot(str)
    def executeCommand(self, command_id: str):
        cmd = self._resolve(command_id)
        if cmd is None or not cmd["condition"]():
            # Re-validation failed: either a garbage id, or - the real case
            # this exists for - the command's own condition() no longer holds
            # because app state changed while the palette sat open (e.g. the
            # targeted selection was cleared). Republish directly rather than
            # calling open() again: open() resets _notice to None as its
            # first act, which would erase this exact message before it ever
            # reached JS. _commands is left untouched - ids already handed to
            # JS stay valid; the stale entry simply drops out of the
            # condition()-filtered list this publish rebuilds.
            self._notice = "That command is no longer available."
            self.publish()
            return
        self._visible = False
        self._notice = None
        self.publish()
        self.visibilityChanged.emit(False)
        # Deferred to the next event-loop tick, not called synchronously here
        # - several callbacks (generate_image, generate_chart, the
        # plugin_portal._create_*_node family) pop their own dialogs or kick
        # off async work, and invoking one of those mid-QWebChannel-slot-
        # invocation is worth avoiding on general principle. This is the
        # concrete async equivalent of the old code returning from a blocking
        # dialog.exec() before calling command['callback']().
        QTimer.singleShot(0, cmd["callback"])

    def _resolve(self, command_id: str) -> dict[str, Any] | None:
        try:
            index = int(command_id)
        except ValueError:
            return None
        if index < 0 or index >= len(self._commands):
            return None
        return self._commands[index]

    @Slot()
    def dismiss(self):
        if not self._visible:
            return
        self._visible = False
        self._notice = None
        self.publish()
        self.visibilityChanged.emit(False)
