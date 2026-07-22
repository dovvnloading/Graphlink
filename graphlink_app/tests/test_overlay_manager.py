"""UI-refactor P1 (doc/UI_QA_AUDIT.md section 7): OverlayManager unit tests +
the phase's acceptance criteria as executable checks.

Acceptance (audit section 7, P1): single-open across every surface,
Escape closes whatever is open, and no dialog pixel outside the window at
any window size. Unit tier here uses plain QWidgets as fake surfaces so the
manager's LOGIC is pinned independent of any real host; the integration
tier drives a REAL ChatWindow's registered surfaces (plugins/pins popovers,
settings/library dialogs) through toolbar-intent entry points at three
window sizes.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

_APP = QApplication.instance() or QApplication([])

from graphlink_overlay_manager import DIALOG, POPOVER, OverlayManager


class _FakeSurface:
    def __init__(self, parent):
        self.widget = QWidget(parent)
        self.widget.setVisible(False)
        self.open_calls = 0

    def open(self):
        self.open_calls += 1
        self.widget.setVisible(True)

    def close(self):
        self.widget.setVisible(False)

    def is_open(self):
        return self.widget.isVisible()


@pytest.fixture
def rig():
    window = QWidget()
    window.resize(800, 600)
    # Children of a hidden parent always report isVisible() False - the
    # manager's is_open contract is real visibility, so the rig must show.
    window.show()
    manager = OverlayManager(window)
    surfaces = {}
    for name, tier in (("pop_a", POPOVER), ("pop_b", POPOVER), ("dlg_a", DIALOG), ("dlg_b", DIALOG)):
        surface = _FakeSurface(window)
        surfaces[name] = surface
        manager.register(
            name, tier,
            widget_fn=lambda s=surface: s.widget,
            open_fn=surface.open,
            close_fn=surface.close,
            is_open_fn=surface.is_open,
        )
    yield window, manager, surfaces
    # The manager installs a QApplication-wide event filter; remove it so
    # rigs never stack across tests.
    QApplication.instance().removeEventFilter(manager)
    window.deleteLater()


class TestSingleOpenPolicy:
    def test_opening_a_popover_closes_the_other_popover(self, rig):
        _, manager, surfaces = rig
        manager.open("pop_a")
        manager.open("pop_b")
        assert not surfaces["pop_a"].is_open()
        assert surfaces["pop_b"].is_open()

    def test_opening_a_dialog_closes_an_open_popover(self, rig):
        _, manager, surfaces = rig
        manager.open("pop_a")
        manager.open("dlg_a")
        assert not surfaces["pop_a"].is_open()
        assert surfaces["dlg_a"].is_open()

    def test_opening_a_dialog_closes_the_other_dialog(self, rig):
        _, manager, surfaces = rig
        manager.open("dlg_a")
        manager.open("dlg_b")
        assert not surfaces["dlg_a"].is_open()
        assert surfaces["dlg_b"].is_open()

    def test_toggle_closes_when_open_and_opens_when_closed(self, rig):
        _, manager, surfaces = rig
        manager.toggle("pop_a")
        assert surfaces["pop_a"].is_open()
        manager.toggle("pop_a")
        assert not surfaces["pop_a"].is_open()

    def test_at_most_one_surface_visible_after_any_sequence(self, rig):
        _, manager, surfaces = rig
        for name in ("pop_a", "dlg_a", "pop_b", "dlg_b", "pop_a"):
            manager.toggle(name)
            visible = [n for n, s in surfaces.items() if s.is_open()]
            assert len(visible) <= 1, f"multiple surfaces open: {visible}"


class TestDialogScrim:
    def test_scrim_visible_only_while_a_dialog_is_open(self, rig):
        _, manager, surfaces = rig
        assert not manager._scrim.isVisible()
        manager.open("pop_a")
        assert not manager._scrim.isVisible(), "popovers must not scrim"
        manager.open("dlg_a")
        assert manager._scrim.isVisible()
        manager.close("dlg_a")
        assert not manager._scrim.isVisible()

    def test_scrim_click_closes_the_dialog(self, rig):
        _, manager, surfaces = rig
        manager.open("dlg_a")
        manager._on_scrim_pressed()
        assert not surfaces["dlg_a"].is_open()
        assert not manager._scrim.isVisible()

    def test_scrim_covers_the_whole_window_after_resize(self, rig):
        window, manager, _ = rig
        manager.open("dlg_a")
        window.resize(1200, 900)
        manager.reposition_for_resize()
        assert manager._scrim.geometry() == window.rect()


class TestVisibilityChangedSignal:
    def test_emits_true_on_open_and_false_on_close(self, rig):
        _, manager, _ = rig
        events = []
        manager.visibility_changed.connect(lambda name, is_open: events.append((name, is_open)))
        manager.open("pop_a")
        manager.close("pop_a")
        assert events == [("pop_a", True), ("pop_a", False)]

    def test_single_open_displacement_emits_close_for_the_displaced(self, rig):
        _, manager, _ = rig
        events = []
        manager.visibility_changed.connect(lambda name, is_open: events.append((name, is_open)))
        manager.open("pop_a")
        manager.open("dlg_a")
        assert ("pop_a", False) in events
        assert events[-1] == ("dlg_a", True)

    def test_open_surface_name_rederives_from_real_visibility(self, rig):
        _, manager, surfaces = rig
        manager.open("pop_a")
        # A surface hidden behind the manager's back must not be reported open.
        surfaces["pop_a"].close()
        assert manager.open_surface_name() is None


class TestEscapeClosesEverything:
    @pytest.mark.parametrize("name", ["pop_a", "pop_b", "dlg_a", "dlg_b"])
    def test_escape_closes_the_open_surface(self, rig, name):
        window, manager, surfaces = rig
        window.show()
        manager.open(name)
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtCore import QEvent
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        # Delivered through the application filter exactly as a real key
        # press would be seen, whatever widget owns focus.
        assert manager.eventFilter(window, event) is True
        assert not surfaces[name].is_open()

    def test_escape_with_nothing_open_is_not_consumed(self, rig):
        window, manager, _ = rig
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtCore import QEvent
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        assert manager.eventFilter(window, event) is False


class TestRealChatWindowAcceptanceDrive:
    """The audit's P1 acceptance, against the real window: toolbar-intent
    entry points, three window sizes, geometry containment."""

    @pytest.fixture(scope="class")
    def window(self):
        from unittest.mock import patch
        from graphlink_window import ChatWindow
        import graphlink_licensing

        with patch.object(ChatWindow, "show_previous_crash_notice", create=True):
            settings_manager = graphlink_licensing.SettingsManager()
            win = ChatWindow(settings_manager)
        win.show()
        QApplication.processEvents()
        yield win
        QApplication.instance().removeEventFilter(win.overlay_manager)
        win.close()

    def test_single_open_across_real_surfaces(self, window):
        manager = window.overlay_manager
        window.resize(1100, 750)
        manager.open("plugins")
        assert manager.is_open("plugins")
        manager.open("pins")
        assert not manager.is_open("plugins")
        manager.open("settings")
        assert not manager.is_open("pins")
        assert manager.is_open("settings")
        manager.open("library")
        assert not manager.is_open("settings")
        assert manager.is_open("library")
        manager.close_all()
        assert manager.open_surface_name() is None

    @pytest.mark.parametrize("size", [(900, 600), (1300, 850), (700, 500)])
    def test_no_dialog_pixel_outside_the_window_at_any_size(self, window, size):
        manager = window.overlay_manager
        window.resize(*size)
        for name in ("settings", "library", "about"):
            manager.open(name)
            widget = manager._surfaces[name].widget_fn()
            assert widget is not None and widget.isVisible(), f"{name} did not open"
            geometry = widget.geometry()
            assert window.rect().contains(geometry), (
                f"{name} at window size {size} escapes the window: "
                f"{geometry} vs {window.rect()}"
            )
            manager.close(name)

    def test_escape_closes_each_real_surface(self, window):
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtCore import QEvent
        manager = window.overlay_manager
        for name in ("plugins", "pins", "settings", "library"):
            manager.open(name)
            event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
            assert manager.eventFilter(window, event) is True, f"Escape not consumed for {name}"
            assert not manager.is_open(name), f"Escape did not close {name}"

    def test_library_first_open_actually_shows(self, window):
        # Audit B3: the first Library invocation used to be a no-op. The
        # cached embedded child must be visible on the FIRST open call.
        manager = window.overlay_manager
        manager.close_all()
        window.library_dialog = None  # force the true first-open path
        manager.open("library")
        assert window.library_dialog is not None
        assert window.library_dialog.isVisible()
        manager.close("library")
