"""Regression coverage for the shared opaque context-menu surface."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMenu, QPlainTextEdit

from graphlink_context_menu import (
    configure_context_menu,
    create_context_menu,
    install_context_menu_filter,
)


_APP = QApplication.instance() or QApplication([])


def test_configure_context_menu_forces_an_opaque_surface():
    menu = configure_context_menu(QMenu())
    menu.addAction("Copy")
    menu.addSeparator()
    menu.addAction("Delete")
    menu.ensurePolished()
    menu.adjustSize()
    QApplication.processEvents()

    assert not menu.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert menu.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
    assert menu.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    assert menu.autoFillBackground()
    assert menu.palette().color(menu.backgroundRole()).alpha() == 255
    assert "background-color:" in menu.styleSheet()


def test_context_menu_factory_configures_submenus_with_the_same_contract():
    submenu = create_context_menu(title="More")

    assert isinstance(submenu, QMenu)
    assert not submenu.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert submenu.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
    assert submenu.palette().color(submenu.backgroundRole()).alpha() == 255


def test_application_filter_configures_qt_created_editor_context_menus():
    install_context_menu_filter(_APP)
    editor = QPlainTextEdit()
    editor.setPlainText("Graphlink standard menu")
    editor.show()
    menu = editor.createStandardContextMenu()
    menu.popup(editor.mapToGlobal(editor.rect().center()))
    QApplication.processEvents()

    assert not menu.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert menu.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
    assert menu.palette().color(menu.backgroundRole()).alpha() == 255

    menu.close()
    editor.close()
