"""The command-palette island's outbound wire contract, as typed Python
dataclasses.

THIS IS A WIRE FORMAT, NOT A DOMAIN MODEL - see graphlink_composer_payload.py
for the fuller rationale. `CommandManager`'s own `commands` list (in
graphlink_command_palette.py) is deliberately NOT reused here: each entry
there carries a live `callback` (a Python closure) and `condition` (a Python
callable) - neither is serializable, and neither may ever reach the web side.
This payload's `CommandEntryPayload` carries only what the web side needs to
render and filter a list: a stable id (assigned by CommandPaletteBridge, see
its own module docstring), the display name, and the lowercased search
aliases (`_filter_commands`'s old substring-matching moves entirely to JS,
so aliases must ship in full - see graphlink_command_palette_bridge.py).

Nothing in the running app constructs these directly; they are the schema
source of truth, cross-checked against a live CommandPaletteBridge snapshot
by tests/test_command_palette_payload_schema.py.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CommandEntryPayload:
    id: str
    name: str
    aliases: list[str]


@dataclass
class CommandPaletteStatePayload:
    """The complete published snapshot, including the envelope fields
    IslandBridge.publish() adds to every island's payload."""

    schemaVersion: int
    revision: int
    visible: bool
    # Only commands whose condition() passed at the moment this snapshot was
    # taken - matches CommandManager.get_available_commands()'s own filter,
    # applied once at open() time. executeCommand() re-checks condition()
    # again, live, at execute time - this list is a snapshot, not a promise.
    commands: list[CommandEntryPayload]
    # Set when executeCommand() finds a command whose condition() no longer
    # holds (state changed while the palette sat open) or an id the current
    # snapshot doesn't recognize. None when there is nothing to report. JS
    # renders this as a transient inline message and clears it locally on the
    # next keystroke or open() - it is not itself round-tripped back.
    notice: str | None = None
    # See ComposerStatePayload's identical field for the full negotiation
    # rationale; optional for the same reason (models a sender predating this
    # field, not today's - IslandBridge.publish() always emits it).
    minCompatibleSchemaVersion: int | None = None
