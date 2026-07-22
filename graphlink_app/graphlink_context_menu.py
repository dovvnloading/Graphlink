"""Shared, opaque context-menu configuration.

Qt menus are top-level popup windows.  Styling only ``QMenu`` in the
application stylesheet is not enough to guarantee that the native popup
surface is painted on every platform/style combination, especially when a
menu is created without a QWidget parent.  Keep the surface configuration in
one place and use it for application menus and standard editor menus alike.
"""

from __future__ import annotations

import atexit

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from graphlink_config import get_current_palette, get_surface_color, is_monochrome_theme, is_muted_theme
from graphlink_styles import FONT_FAMILY


def _colors() -> dict[str, str]:
    """Return the menu colors for the active Graphlink theme."""
    if is_monochrome_theme():
        return {
            "surface": get_surface_color("field"),
            "text": get_surface_color("text_primary"),
            "border": get_surface_color("border"),
            "hover": "#666666",
            "disabled": "#777777",
        }

    if is_muted_theme():
        return {
            "surface": get_surface_color("field"),
            "text": get_surface_color("text_primary"),
            "border": get_surface_color("border"),
            "hover": "#707070",
            "disabled": "#707070",
        }

    return {
        "surface": get_surface_color("field"),
        "text": get_surface_color("text_primary"),
        "border": get_surface_color("divider"),
        "hover": get_current_palette().SELECTION.name(),
        "disabled": get_surface_color("text_muted"),
    }


def context_menu_stylesheet() -> str:
    """Build the complete stylesheet for a Graphlink context menu."""
    colors = _colors()
    return f"""
        QMenu {{
            background-color: {colors['surface']};
            color: {colors['text']};
            border: 1px solid {colors['border']};
            border-radius: 8px;
            padding: 4px;
            font-family: {FONT_FAMILY};
            font-size: 12px;
        }}
        QMenu::item {{
            background-color: transparent;
            color: {colors['text']};
            padding: 7px 24px 7px 12px;
            min-height: 18px;
            border-radius: 4px;
        }}
        QMenu::item:selected {{
            background-color: {colors['hover']};
            color: #FFFFFF;
        }}
        QMenu::item:disabled {{
            color: {colors['disabled']};
        }}
        QMenu::separator {{
            height: 1px;
            background-color: {colors['border']};
            margin: 4px 8px;
        }}
    """


def _enforce_opaque_surface(menu: QMenu) -> None:
    """Re-apply attributes that Qt's stylesheet polish can reset."""
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
    menu.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
    menu.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
    menu.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    menu.setAutoFillBackground(True)


class _ContextMenuSurfaceGuard(QObject):
    """Keep native popup attributes intact through Qt polish/show events."""

    def __init__(self, menu: QMenu):
        super().__init__(menu)
        self.menu = menu
        self._timer = QTimer(menu)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._enforce)

    def eventFilter(self, watched, event):
        try:
            if watched is self.menu and event.type() in {
                QEvent.Type.Polish,
                QEvent.Type.Show,
                QEvent.Type.StyleChange,
            }:
                # QStyleSheetStyle can run after the event filter.  Defer one
                # turn so the final visible popup state is the opaque state.
                self._timer.start(0)
        except (AttributeError, RuntimeError, SystemError, TypeError):
            # PySide can dispatch a final event while its C++ wrappers are
            # already being torn down. The filter must never turn shutdown
            # into a failing process exit.
            return False
        return False

    def _enforce(self):
        try:
            _enforce_opaque_surface(self.menu)
        except (AttributeError, RuntimeError, SystemError, TypeError):
            return


class _ApplicationContextMenuFilter(QObject):
    """Configure QMenus created internally by Qt (for example text editors)."""

    def eventFilter(self, watched, event):
        try:
            if _is_qmenu(watched) and event.type() == QEvent.Type.Show:
                configure_context_menu(watched)
        except (AttributeError, RuntimeError, SystemError, TypeError):
            # During interpreter shutdown Shiboken may tear down QMenu's type
            # object before QApplication stops dispatching its event filter.
            # Treat that late callback as a no-op instead of leaking a Python
            # exception through QObject::eventFilter and failing pytest.
            return False
        return False

    def detach(self):
        """Remove this filter while QApplication and its wrappers are alive."""
        try:
            app = self.parent()
            if app is None:
                return
            app.removeEventFilter(self)
            if getattr(app, "_graphlink_context_menu_filter", None) is self:
                app._graphlink_context_menu_filter = None
        except (AttributeError, RuntimeError, SystemError, TypeError):
            return


def _is_qmenu(watched, menu_type=QMenu):
    """Return whether ``watched`` is a menu, including during Qt teardown."""
    try:
        return isinstance(watched, menu_type)
    except (RuntimeError, SystemError, TypeError):
        return False


def configure_context_menu(menu: QMenu) -> QMenu:
    """Make ``menu`` a fully painted, theme-consistent popup surface.

    The explicit widget attributes are intentional.  A QMenu is a native
    top-level popup, so a stylesheet alone can leave the backing surface
    translucent on Windows and with some platform styles.  We keep the
    rounded styling, but require an opaque backing surface so the graph
    canvas cannot bleed through the menu.
    """
    palette = menu.palette()
    surface = QColor(_colors()["surface"])
    for role in (
        QPalette.ColorRole.Window,
        QPalette.ColorRole.Base,
        QPalette.ColorRole.AlternateBase,
    ):
        palette.setColor(role, surface)
    menu.setPalette(palette)
    menu.setStyleSheet(context_menu_stylesheet())

    # QStyleSheetStyle may reset paint attributes during polish.  The guard
    # reapplies the native-surface contract after polish and immediately
    # after the popup becomes visible.
    _enforce_opaque_surface(menu)
    guard = getattr(menu, "_context_menu_surface_guard", None)
    if guard is None:
        guard = _ContextMenuSurfaceGuard(menu)
        menu._context_menu_surface_guard = guard
        menu.installEventFilter(guard)
    guard._timer.start(0)
    return menu


def install_context_menu_filter(app: QApplication) -> QObject:
    """Install the process-wide guard for menus created outside Graphlink code."""
    guard = getattr(app, "_graphlink_context_menu_filter", None)
    if guard is None:
        guard = _ApplicationContextMenuFilter(app)
        app._graphlink_context_menu_filter = guard
        app.installEventFilter(guard)
        app.aboutToQuit.connect(guard.detach)

        # pytest and some embedded hosts do not run the normal Qt quit path.
        # Register cleanup before PySide's module-shutdown handler so the
        # application filter is gone before Shiboken destroys QMenu's type.
        if not getattr(app, "_graphlink_context_menu_atexit", False):
            def _cleanup():
                try:
                    current = getattr(app, "_graphlink_context_menu_filter", None)
                    if current is not None:
                        current.detach()
                except (AttributeError, RuntimeError, SystemError, TypeError):
                    return

            atexit.register(_cleanup)
            app._graphlink_context_menu_atexit = True
    return guard


def create_context_menu(parent: QWidget | None = None, title: str | None = None) -> QMenu:
    """Create and configure a Graphlink context menu or submenu."""
    menu = QMenu(title, parent) if title is not None else QMenu(parent)
    return configure_context_menu(menu)


__all__ = [
    "configure_context_menu",
    "context_menu_stylesheet",
    "create_context_menu",
    "install_context_menu_filter",
]
