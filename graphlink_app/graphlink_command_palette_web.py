"""Web host for the command-palette island.

Needs a thin WebIslandHost subclass, same reason as NotificationWebHost: not
legacy-widget impersonation (there's exactly one call site,
show_command_palette(), already being rewritten by this same migration), but
because the host needs bespoke behavior beyond what the generic base
provides - a fixed size (no negotiated-height channel; the palette is a
static 600x400 list UI, not variable content), and reposition-relative-to-
parent math the generic base has no opinion on.
"""

from __future__ import annotations

from graphlink_command_palette_bridge import CommandPaletteBridge
from graphlink_web_island_host import WebIslandHost

COMMAND_PALETTE_WIDTH = 600
COMMAND_PALETTE_HEIGHT = 400

COMMAND_PALETTE_UNAVAILABLE_MESSAGE = (
    "The command palette is unavailable because QtWebEngine failed to initialize."
)


class CommandPaletteWebHost(WebIslandHost):
    def __init__(self, command_manager, parent=None):
        bridge = CommandPaletteBridge(command_manager)
        super().__init__(
            bridge=bridge,
            asset_dir_name="command-palette",
            bridge_object_name="commandPaletteBridge",
            unavailable_message=COMMAND_PALETTE_UNAVAILABLE_MESSAGE,
            parent=parent,
        )
        self.setFixedSize(COMMAND_PALETTE_WIDTH, COMMAND_PALETTE_HEIGHT)
        self.bridge.visibilityChanged.connect(self.setVisible)
        self.setVisible(False)  # old dialog: never shown until show_command_palette()

    def update_position(self):
        """Centers the host over its parent, offset up 100px - matches the
        old CommandPaletteDialog's positioning formula (parent_center minus
        half its own size, minus a 100px vertical offset so it floats above
        the composer rather than dead-center over it), translated from the
        old top-level QDialog.move()'s screen coordinates to this host's
        parent-relative child-widget coordinates."""
        parent = self.parent()
        if parent is None:
            return
        rect = parent.rect()
        target_x = (rect.width() - self.width()) // 2
        target_y = (rect.height() - self.height()) // 2 - 100
        self.move(target_x, target_y)
