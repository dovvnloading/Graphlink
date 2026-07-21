"""Web host for the composer-picker island (Phase 5 increment 3) - absorbs
ComposerPickerPopup (graphlink_composer_popups.py, deleted this increment).

A plain embedded child QFrame (no Window flag), matching every Phase 5 host
so far - the legacy popup's own Qt.WindowType.Tool floating-window shape
doesn't survive the migration (see graphlink_overlay_coordinator.py's module
docstring: none of Phase 5's real surfaces are full-viewport or need a
separate top-level window; each already gets correct click-pass-through via
WebIslandHost's native rounded-corner masking). Outside-click-close (this
phase's own named exit criterion) is reimplemented via WebIslandHost's
dismiss_on_outside_focus option rather than the legacy's app-wide
MouseButtonPress eventFilter - see that option's own docstring for why
QApplication.focusChanged is the more robust translation once the popup is a
QWebEngineView-hosted embedded child rather than a separate native window.

Height is content-negotiated exactly like PinOverlayHost/NotificationWebHost;
width is a single fixed value (COMPOSER_PICKER_WIDTH) rather than porting the
legacy's own Qt-layout-managed [380, 440] range - CSS text-ellipsis handles
long labels within a fixed width, the same simplification DocumentViewer/
PinOverlay already made over their own legacy widgets' flexible sizing.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect

from graphlink_composer_picker_bridge import (
    COMPOSER_PICKER_MAX_HEIGHT,
    COMPOSER_PICKER_MIN_HEIGHT,
    ComposerPickerBridge,
)
from graphlink_composer_popup_positioning import composer_picker_position
from graphlink_web_island_host import WebIslandHost

COMPOSER_PICKER_UNAVAILABLE_MESSAGE = (
    "The model/reasoning picker is unavailable because QtWebEngine failed to initialize."
)

COMPOSER_PICKER_WIDTH = 400


class ComposerPickerHost(WebIslandHost):
    def __init__(self, composer_bridge, parent=None):
        bridge = ComposerPickerBridge(composer_bridge)
        super().__init__(
            bridge=bridge,
            asset_dir_name="composer-picker",
            bridge_object_name="composerPickerBridge",
            min_height=COMPOSER_PICKER_MIN_HEIGHT,
            max_height=COMPOSER_PICKER_MAX_HEIGHT,
            unavailable_message=COMPOSER_PICKER_UNAVAILABLE_MESSAGE,
            dismiss_on_outside_focus=True,
            parent=parent,
        )
        self.setFixedWidth(COMPOSER_PICKER_WIDTH)
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
