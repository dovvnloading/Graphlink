"""UI-refactor P1 (doc/UI_QA_AUDIT.md section 7): the one owner of every
transient surface's OPEN/DISMISS lifecycle.

The audit's section-2 findings were a class, not incidents: popups stacked
(B1) because each surface managed only itself; Escape closed nothing (B2)
because no one owned it (and webview focus swallows key events aimed at
QShortcuts); Settings escaped the main window and z-ordered under Library
(B4) because it was a screen-clamped top-level Tool window; chips desynced
(B6) because the toolbar island latched click-state locally.

OverlayManager fixes the class:
- one registry of named surfaces, two tiers: POPOVER (anchored, light) and
  DIALOG (centered, scrimmed);
- single-open per tier + dialogs close popovers: opening anything closes
  whatever else is open (the recorded policy - simplest model that makes
  B1 impossible; no surface today needs to coexist with another);
- one QApplication event filter: Escape closes the top surface wherever
  focus lives (verified against webview focus in the P1 drive test), and a
  mouse press outside an open popover dismisses it (dialogs dismiss via
  their close button / Escape / scrim click, not stray outside clicks -
  the legacy SettingsDialog's stay-open-during-scans rationale, kept);
- a shared scrim widget under dialogs: modality the user can see, a
  guaranteed z-order (scrim just under dialog, both above everything
  registered with OverlayCoordinator), and click-to-dismiss;
- a visibility_changed(name, bool) signal the window binds to the toolbar
  payload, so chip active-states reflect REAL visibility, never latched
  click-state.

Positioning stays with each surface (popovers keep their show_for_anchor/
reposition functions; dialogs center-and-clamp inside the window via
reposition_dialogs(), called from the window's resize path) - this class
owns lifecycle, OverlayCoordinator keeps owning the embedded-overlay
reposition/raise pass it already owns. Complementary, not overlapping.

This is deliberately not a QWidget (except the scrim): like
OverlayCoordinator, it holds behavior, not chrome.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QWidget

POPOVER = "popover"
DIALOG = "dialog"


class DialogScrim(QWidget):
    """Semi-transparent full-window layer shown under an open dialog.

    Absorbs every mouse press (modality) and reports clicks so the manager
    can close the dialog. Painted, not stylesheet'd: a plain translucent
    fill with no child machinery.
    """

    def __init__(self, parent, on_pressed):
        super().__init__(parent)
        self._on_pressed = on_pressed
        self.setVisible(False)
        # Alpha tuned to read as "background demoted", not "blackout".
        self._fill = QColor(0, 0, 0, 110)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._fill)

    def mousePressEvent(self, event):
        event.accept()
        self._on_pressed()

    def resize_to_parent(self):
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())


class _Surface:
    __slots__ = ("name", "tier", "widget_fn", "open_fn", "close_fn", "is_open_fn")

    def __init__(self, name, tier, widget_fn, open_fn, close_fn, is_open_fn):
        self.name = name
        self.tier = tier
        self.widget_fn = widget_fn        # () -> QWidget | None (for hit-testing/z)
        self.open_fn = open_fn            # () -> None (position + show)
        self.close_fn = close_fn          # () -> None (hide)
        self.is_open_fn = is_open_fn      # () -> bool


class OverlayManager(QObject):
    visibility_changed = Signal(str, bool)

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self._surfaces: dict[str, _Surface] = {}
        self._open_name: str | None = None
        self._scrim = DialogScrim(window, self._on_scrim_pressed)
        QApplication.instance().installEventFilter(self)

    # -- registration ------------------------------------------------------

    def register(self, name, tier, *, widget_fn, open_fn, close_fn, is_open_fn):
        assert tier in (POPOVER, DIALOG)
        assert name not in self._surfaces, f"surface {name!r} registered twice"
        self._surfaces[name] = _Surface(name, tier, widget_fn, open_fn, close_fn, is_open_fn)
        # Hosts exposing escape_pressed (WebIslandHost's widget-scoped Escape
        # shortcut - fires even when Chromium owns keyboard focus) dismiss
        # through the manager like any other Escape.
        widget = widget_fn()
        signal = getattr(widget, "escape_pressed", None)
        if signal is not None:
            signal.connect(self.close_all)

    # -- core lifecycle ----------------------------------------------------

    def toggle(self, name):
        if self.is_open(name):
            self.close(name)
        else:
            self.open(name)

    def open(self, name):
        surface = self._surfaces[name]
        # Single-open policy across BOTH tiers: opening anything closes
        # whatever else is open first (audit B1).
        if self._open_name is not None and self._open_name != name:
            self.close(self._open_name)
        if surface.tier == DIALOG:
            self._scrim.resize_to_parent()
            self._scrim.setVisible(True)
            self._scrim.raise_()
        surface.open_fn()
        widget = surface.widget_fn()
        if widget is not None:
            widget.raise_()
            # Lazy surfaces register with widget_fn returning None until
            # first open - hook escape_pressed on first sight, INCLUDING
            # descendants: a dialog's Escape source is usually the embedded
            # WebIslandHost inside it (its widget-scoped shortcut fires while
            # Chromium owns the keyboard focus), not the outer shell.
            for candidate in (widget, *widget.findChildren(QWidget)):
                signal = getattr(candidate, "escape_pressed", None)
                if signal is not None and not getattr(candidate, "_gl_escape_hooked", False):
                    signal.connect(self.close_all)
                    candidate._gl_escape_hooked = True
        # No focus-steal here (an earlier draft did): hosts opting into
        # dismiss_on_outside_focus would see the focus move and immediately
        # close themselves. Escape coverage comes from the app filter plus
        # each WebIslandHost's widget-scoped Escape shortcut instead.
        self._open_name = name
        self.visibility_changed.emit(name, True)

    def close(self, name):
        surface = self._surfaces.get(name)
        if surface is None:
            return
        # Capture BEFORE close_fn(): hiding a widget delivers ShowEvent/
        # HideEvent SYNCHRONOUSLY through this manager's own application
        # event filter, so any state read after close_fn() can observe a
        # mid-close world (found by the P1 acceptance tests - the scrim
        # stayed up because _open_name had been re-derived to None by the
        # time the tier check ran).
        was_open = surface.is_open_fn() or self._open_name == name
        was_current = self._open_name == name
        surface.close_fn()
        if surface.tier == DIALOG and was_current:
            self._scrim.setVisible(False)
        if self._open_name == name:
            self._open_name = None
        if was_open:
            self.visibility_changed.emit(name, False)

    def close_all(self):
        if self._open_name is not None:
            self.close(self._open_name)

    def is_open(self, name):
        surface = self._surfaces.get(name)
        return surface is not None and surface.is_open_fn()

    def open_surface_name(self):
        # Re-derive from real visibility, never trust the cached name alone:
        # a surface hidden behind the manager's back (e.g. its own legacy
        # close path) must not leave the manager believing it is open.
        if self._open_name is not None and self.is_open(self._open_name):
            return self._open_name
        return None

    # -- window integration ------------------------------------------------

    def reposition_for_resize(self):
        """Called from the window's resize path: keep the scrim full-window
        and let the open dialog re-clamp itself (its open_fn re-centers)."""
        name = self.open_surface_name()
        self._scrim.resize_to_parent()
        if name is not None and self._surfaces[name].tier == DIALOG:
            self._surfaces[name].open_fn()
            widget = self._surfaces[name].widget_fn()
            if widget is not None:
                widget.raise_()

    def _on_scrim_pressed(self):
        name = self.open_surface_name()
        if name is not None:
            self.close(name)

    # -- global dismissal (audit B2 + popover outside-click) ---------------

    def eventFilter(self, watched, event):
        # NO state mutation here: this filter runs synchronously inside
        # show/hide calls made by open()/close() themselves, so writing
        # _open_name from here races the very methods that own it (the P1
        # acceptance tests caught exactly that). open_surface_name() already
        # re-derives from real visibility for reads.
        name = self.open_surface_name()
        if name is None:
            return False

        if (
            event.type() in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride)
            and event.key() == Qt.Key.Key_Escape
        ):
            # ShortcutOverride included deliberately: with keyboard focus
            # inside a QWebEngineView, Chromium ACCEPTS the override and the
            # KeyPress never reaches Qt shortcuts or this filter - but the
            # override event itself passes through application filters
            # first. Consuming it here is the only reliable Escape path over
            # web content (verified live; the widget-scoped QShortcut and
            # plain KeyPress handling cover every native-focus case).
            self.close(name)
            event.accept()
            return True

        if event.type() == QEvent.Type.MouseButtonPress:
            surface = self._surfaces[name]
            if surface.tier == POPOVER:
                widget = surface.widget_fn()
                if widget is not None and widget.isVisible():
                    global_pos = event.globalPosition().toPoint()
                    inside = widget.rect().contains(widget.mapFromGlobal(global_pos))
                    if not inside:
                        self.close(name)
                        # Do NOT consume: the click still lands (canvas
                        # click both dismisses and acts - the standard
                        # light-dismiss contract).
        return False
