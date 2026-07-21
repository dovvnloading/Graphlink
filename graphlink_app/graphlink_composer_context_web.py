"""Web host for the composer-context island (Phase 5 increment 3) - absorbs
ComposerContextPopup (graphlink_composer_popups.py, deleted this increment).

See graphlink_composer_picker_web.py's module docstring for the shared
rationale (plain embedded child, dismiss_on_outside_focus for outside-click
parity, fixed width + content-negotiated height instead of the legacy's
Qt-layout-managed [400, 520] width range).
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect

from graphlink_composer_context_bridge import (
    COMPOSER_CONTEXT_MAX_HEIGHT,
    COMPOSER_CONTEXT_MIN_HEIGHT,
    ComposerContextBridge,
)
from graphlink_composer_popup_positioning import composer_picker_position
from graphlink_web_island_host import WebIslandHost

COMPOSER_CONTEXT_UNAVAILABLE_MESSAGE = (
    "Context review is unavailable because QtWebEngine failed to initialize."
)

COMPOSER_CONTEXT_WIDTH = 440


class ComposerContextHost(WebIslandHost):
    def __init__(self, composer_bridge, parent=None):
        bridge = ComposerContextBridge(composer_bridge)
        super().__init__(
            bridge=bridge,
            asset_dir_name="composer-context",
            bridge_object_name="composerContextBridge",
            min_height=COMPOSER_CONTEXT_MIN_HEIGHT,
            max_height=COMPOSER_CONTEXT_MAX_HEIGHT,
            unavailable_message=COMPOSER_CONTEXT_UNAVAILABLE_MESSAGE,
            dismiss_on_outside_focus=True,
            parent=parent,
        )
        self.setFixedWidth(COMPOSER_CONTEXT_WIDTH)
        self.bridge.heightRequested.connect(self.apply_requested_height)
        self.setVisible(False)

    def reposition(self, composer, viewport) -> None:
        if composer is None or viewport is None or self.parentWidget() is None:
            return
        composer_origin = composer.mapToGlobal(QPoint(0, 0))
        composer_rect = QRect(composer_origin, composer.size())
        viewport_origin = viewport.mapToGlobal(QPoint(0, 0))
        viewport_rect = QRect(viewport_origin, viewport.size())
        global_pos = composer_picker_position(viewport_rect, composer_rect, self.size())
        self.move(self.parentWidget().mapFromGlobal(global_pos))
