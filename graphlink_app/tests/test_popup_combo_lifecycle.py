"""Regression coverage for custom combo popup teardown on Windows/Qt."""

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from graphlink_plugins.common.combo import PopupComboBox


def test_popup_combo_ignores_a_popup_deleted_before_combo_cleanup():
    combo = PopupComboBox()
    combo.addItems(["First", "Second"])
    popup = combo._popup

    popup.deleteLater()
    QApplication.sendPostedEvents(popup, QEvent.Type.DeferredDelete)
    QApplication.processEvents()

    assert not isValid(popup)
    combo.hidePopup()
    combo._cleanup_popup()
    assert combo._popup is None

    combo.deleteLater()
    QApplication.sendPostedEvents(combo, QEvent.Type.DeferredDelete)
