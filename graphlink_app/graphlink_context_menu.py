"""Shared, opaque context-menu configuration.

Qt menus are top-level popup windows.  Styling only ``QMenu`` in the
application stylesheet is not enough to guarantee that the native popup
surface is painted on every platform/style combination, especially when a
menu is created without a QWidget parent.  Keep the surface configuration in
one place and use it for application menus and standard editor menus alike.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from graphlink_config import get_current_palette, is_monochrome_theme, is_muted_theme


def _colors() -> dict[str, str]:
    """Return the menu colors for the active Graphlink theme."""
    if is_monochrome_theme():
        return {
            "surface": "#2A2A2A",
            "text": "#DDDDDD",
            "border": "#444444",
            "hover": "#666666",
            "disabled": "#777777",
        }

    if is_muted_theme():
        return {
            "surface": "#232323",
            "text": "#D1D1D1",
            "border": "#383838",
            "hover": "#707070",
            "disabled": "#707070",
        }

    return {
        "surface": "#272727",
        "text": "#DCDCDC",
        "border": "#424242",
        "hover": get_current_palette().SELECTION.name(),
        "disabled": "#767676",
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
            font-family: 'Segoe UI', sans-serif;
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
        if watched is self.menu and event.type() in {
            QEvent.Type.Polish,
            QEvent.Type.Show,
            QEvent.Type.StyleChange,
        }:
            # QStyleSheetStyle can run after the event filter.  Defer one
            # turn so the final visible popup state is the opaque state.
            self._timer.start(0)
        return False

    def _enforce(self):
        _enforce_opaque_surface(self.menu)


class _ApplicationContextMenuFilter(QObject):
    """Configure QMenus created internally by Qt (for example text editors)."""

    def eventFilter(self, watched, event):
        if isinstance(watched, QMenu) and event.type() == QEvent.Type.Show:
            configure_context_menu(watched)
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
