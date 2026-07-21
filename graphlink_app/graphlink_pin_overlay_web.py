"""Web host for the pin-overlay island - Phase 5 increment 1.

A plain embedded child QFrame (no Window flag) - the legacy PinOverlay this
replaces is a plain QFrame too, shown/hidden via setVisible() at every real
call site EXCEPT toggle_pin_overlay's own `.close()` call, which this port
changes to `.setVisible(False)` specifically to sidestep the closeEvent-
teardown risk class every closable/reopenable *floating* WebIslandHost in
this migration has needed to guard against - simpler to avoid the call path
entirely here than to add another override, since (like DocumentViewer) this
host has no Window flag and is never meant to be a real top-level window.

Height is content-dependent (matches the legacy panel's own
_resize_for_content()/reposition() dance) - negotiated via the same
min_height/max_height + apply_requested_height mechanism NotificationWebHost
already uses, with React measuring and reporting its own rendered height via
the bridge's resize(height) Slot.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Signal
from PySide6.QtGui import QCursor

from graphlink_context_menu import create_context_menu
from graphlink_pin_overlay_bridge import (
    PIN_OVERLAY_MAX_HEIGHT,
    PIN_OVERLAY_MIN_HEIGHT,
    PinOverlayBridge,
)
from graphlink_web_island_host import WebIslandHost

PIN_OVERLAY_UNAVAILABLE_MESSAGE = (
    "Navigation pins are unavailable because QtWebEngine failed to initialize."
)

PIN_OVERLAY_WIDTH = 400


class PinOverlayHost(WebIslandHost):
    # Emitted whenever this host transitions to hidden (via setVisible(False)
    # from ANY path, not just toggle_pin_overlay) - mirrors the legacy
    # PinOverlay.hideEvent's identical "closed" signal, which
    # ChatWindow._handle_pin_overlay_closed uses to uncheck the toolbar Pins
    # button regardless of how the panel was hidden.
    closed = Signal()

    def __init__(self, chat_view, controller, parent=None):
        bridge = PinOverlayBridge(chat_view, controller)
        super().__init__(
            bridge=bridge,
            asset_dir_name="pin-overlay",
            bridge_object_name="pinOverlayBridge",
            min_height=PIN_OVERLAY_MIN_HEIGHT,
            max_height=PIN_OVERLAY_MAX_HEIGHT,
            unavailable_message=PIN_OVERLAY_UNAVAILABLE_MESSAGE,
            parent=parent,
        )
        self._chat_view = chat_view
        self._controller = controller
        self.setFixedWidth(PIN_OVERLAY_WIDTH)
        self._anchor_widget = None
        self.bridge.heightRequested.connect(self.apply_requested_height)
        self.setVisible(False)

    def edit_pin(self, pin) -> None:
        """Facade preserved verbatim for the legacy PinOverlay's own public
        API - ChatWindow.edit_navigation_pin() (a canvas NavigationPin's own
        double-click/edit action, via graphlink_scene.py's
        _on_navigation_pin_edit_requested) calls this exactly as it called
        the old widget's method of the same name.

        Unlike the legacy modal (a separate floating window, shown
        independent of whether the panel itself was open), the async draft
        editor (Phase 5 increment 2) now lives INSIDE this panel - so a
        canvas-triggered edit also needs to make the panel itself visible, a
        real behavior addition the in-panel-view design requires that the
        legacy call path never needed."""
        self.bridge.editPin(pin.pin_id)
        if not self.isVisible():
            if self._anchor_widget is not None:
                self.show_for_anchor(self._anchor_widget)
            else:
                self.setVisible(True)
                self.raise_()

    def show_pin_context_menu(self, pin, global_pos=None) -> None:
        """Facade preserved verbatim - ChatWindow.
        show_navigation_pin_context_menu() (a canvas NavigationPin's own
        right-click, via graphlink_scene.py's
        _on_navigation_pin_context_requested) calls this exactly as it
        called the old widget's method of the same name."""
        if pin is None or pin.scene() != self._chat_view.scene():
            return
        menu = create_context_menu(self, "Navigation pin")
        focus_action = menu.addAction("Focus canvas")
        edit_action = menu.addAction("Edit pin")
        menu.addSeparator()
        delete_action = menu.addAction("Delete pin")
        action = menu.exec(global_pos or QCursor.pos())
        if action == focus_action:
            self._controller.focus(pin)
        elif action == edit_action:
            self.edit_pin(pin)
        elif action == delete_action:
            self._controller.remove(pin)

    def show_for_anchor(self, anchor_widget) -> None:
        self._anchor_widget = anchor_widget
        self.reposition()
        self.setVisible(True)
        self.raise_()

    def reposition(self) -> None:
        if self._anchor_widget is None or self.parentWidget() is None:
            return
        target = self._anchor_widget.mapTo(self.parentWidget(), QPoint(0, self._anchor_widget.height() + 6))
        margin = 12
        x = max(margin, min(target.x(), self.parentWidget().width() - self.width() - margin))
        y = max(margin, min(target.y(), self.parentWidget().height() - self.height() - margin))
        self.move(x, y)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.closed.emit()
