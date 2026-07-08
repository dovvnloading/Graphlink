"""Shared popup-style combo box for plugin nodes rendered inside the graphics scene.

A plain QComboBox pops its list using a top-level native popup that is positioned
relative to the on-screen widget. That breaks for a combo living inside a
QGraphicsProxyWidget (the popup lands in the wrong place and does not follow the
canvas). ComboPopup/PopupComboBox replace that with a framed QListWidget popup that is
anchored by mapping the combo's rect through the proxy -> scene -> view -> global
coordinate chain, so it lines up correctly on the canvas.

Extracted verbatim from graphite_plugin_code_review.py (where it was named
CodeReviewComboPopup/CodeReviewPopupComboBox) so it can outlive the Code Review plugin's
removal - Gitlink still depends on it. This module has no plugin-specific logic.
"""

from PySide6.QtCore import (
    QEvent,
    QPoint,
    QPointF,
    QRect,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)


class ComboPopup(QFrame):
    item_selected = Signal(int, str)
    popup_closed = Signal()

    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setObjectName("pluginComboPopupFrame")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._owner_combo = None
        self._close_monitor_active = False

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self.shell = QFrame()
        self.shell.setObjectName("pluginComboPopupShell")
        outer_layout.addWidget(self.shell)

        shell_layout = QVBoxLayout(self.shell)
        shell_layout.setContentsMargins(4, 4, 4, 4)
        shell_layout.setSpacing(0)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("pluginComboPopupList")
        self.list_widget.setFrameShape(QFrame.Shape.NoFrame)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.list_widget.setSpacing(2)
        self.list_widget.setMouseTracking(True)
        self.list_widget.itemClicked.connect(self._emit_item_selection)
        self.list_widget.itemActivated.connect(self._emit_item_selection)
        shell_layout.addWidget(self.list_widget)

        self.apply_style()

    def apply_style(self, accent_color="#2d6fa3"):
        self.setStyleSheet(
            f"""
            QFrame#pluginComboPopupFrame {{
                background-color: #1f2327;
                border: 1px solid #353b43;
                border-radius: 10px;
            }}
            QFrame#pluginComboPopupShell {{
                background: transparent;
                border: none;
            }}
            QListWidget#pluginComboPopupList {{
                background: transparent;
                color: #ffffff;
                border: none;
                outline: none;
                padding: 2px;
            }}
            QListWidget#pluginComboPopupList::item {{
                background: transparent;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                min-height: 26px;
                padding: 6px 10px;
            }}
            QListWidget#pluginComboPopupList::item:hover {{
                background-color: #2a3037;
            }}
            QListWidget#pluginComboPopupList::item:selected {{
                background-color: {accent_color};
                color: #ffffff;
            }}
            QListWidget#pluginComboPopupList::item:selected:hover {{
                background-color: {accent_color};
                color: #ffffff;
            }}
            """
        )

    def populate_from_combo(self, combo):
        current_index = combo.currentIndex()
        current_text = combo.currentText()

        self.list_widget.clear()
        for index in range(combo.count()):
            text = combo.itemText(index)
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.list_widget.addItem(item)

        if current_index < 0 and current_text:
            current_index = combo.findText(current_text)

        if 0 <= current_index < self.list_widget.count():
            self.list_widget.setCurrentRow(current_index)
            current_item = self.list_widget.item(current_index)
            if current_item is not None:
                self.list_widget.scrollToItem(
                    current_item,
                    QAbstractItemView.ScrollHint.PositionAtCenter,
                )
        else:
            self.list_widget.clearSelection()

    def _screen_anchor_rect(self, combo):
        host_widget = combo.window()
        proxy = None
        if host_widget is not None and hasattr(host_widget, "graphicsProxyWidget"):
            proxy = host_widget.graphicsProxyWidget()

        if proxy is not None and proxy.scene() is not None and proxy.scene().views():
            view = proxy.scene().views()[0]
            top_left_host = combo.mapTo(host_widget, QPoint(0, 0))
            top_right_host = combo.mapTo(host_widget, QPoint(combo.width(), 0))
            bottom_left_host = combo.mapTo(host_widget, QPoint(0, combo.height()))

            top_left_scene = proxy.mapToScene(QPointF(top_left_host))
            top_right_scene = proxy.mapToScene(QPointF(top_right_host))
            bottom_left_scene = proxy.mapToScene(QPointF(bottom_left_host))

            top_left_view = view.mapFromScene(top_left_scene)
            top_right_view = view.mapFromScene(top_right_scene)
            bottom_left_view = view.mapFromScene(bottom_left_scene)

            top_left_global = view.viewport().mapToGlobal(top_left_view)
            width = max(1, abs(top_right_view.x() - top_left_view.x()))
            height = max(1, abs(bottom_left_view.y() - top_left_view.y()))
            top_level_window = view.viewport().window()
            if top_level_window is None:
                view_window_attr = getattr(view, "window", None)
                if callable(view_window_attr):
                    top_level_window = view_window_attr()
                else:
                    top_level_window = view_window_attr
            return QRect(top_left_global, QSize(width, height)), top_level_window

        top_left_global = combo.mapToGlobal(QPoint(0, 0))
        return QRect(top_left_global, combo.size()), combo.window()

    def show_for_combo(self, combo):
        self._owner_combo = combo
        anchor_rect, top_level_window = self._screen_anchor_rect(combo)
        if top_level_window is not None and self.parentWidget() is not top_level_window:
            self.setParent(top_level_window, self.windowFlags())

        self.populate_from_combo(combo)
        if self.list_widget.count() == 0:
            return

        font_metrics = combo.fontMetrics()
        max_text_width = 0
        for index in range(combo.count()):
            max_text_width = max(max_text_width, font_metrics.horizontalAdvance(combo.itemText(index)))

        row_height = self.list_widget.sizeHintForRow(0)
        if row_height <= 0:
            row_height = 34

        visible_rows = min(max(self.list_widget.count(), 1), 10)
        popup_width = max(anchor_rect.width(), min(max_text_width + 56, 560))
        popup_height = (visible_rows * row_height) + 22
        self.resize(popup_width, popup_height)

        target_global = anchor_rect.bottomLeft() + QPoint(0, 4)
        screen = QGuiApplication.screenAt(target_global) or QGuiApplication.primaryScreen()
        available_geometry = screen.availableGeometry() if screen else None

        x = target_global.x()
        y = target_global.y()

        if available_geometry is not None:
            if x + self.width() > available_geometry.right() - 12:
                x = available_geometry.right() - self.width() - 12

            if y + self.height() > available_geometry.bottom() - 12:
                above_global = combo.mapToGlobal(QPoint(0, -(self.height() + 4)))
                y = max(available_geometry.top() + 12, above_global.y())

            x = max(available_geometry.left() + 12, x)
            y = max(available_geometry.top() + 12, y)

        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self.list_widget.setFocus()
        QTimer.singleShot(0, self._start_close_monitor)

    def _emit_item_selection(self, item):
        if item is None:
            return
        index = item.data(Qt.ItemDataRole.UserRole)
        self.item_selected.emit(index, item.text())

    def _start_close_monitor(self):
        if not self.isVisible() or self._close_monitor_active:
            return
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            self._close_monitor_active = True

    def eventFilter(self, watched, event):
        if not self.isVisible():
            return False

        if event.type() not in {QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick}:
            return False

        global_pos = event.globalPosition().toPoint()
        if self.frameGeometry().contains(global_pos):
            return False

        if self._owner_combo is not None:
            combo_anchor_rect, _ = self._screen_anchor_rect(self._owner_combo)
            if combo_anchor_rect.contains(global_pos):
                self.hide()
                return True

        self.hide()
        return False

    def hideEvent(self, event):
        if self._close_monitor_active:
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
            self._close_monitor_active = False
        self.popup_closed.emit()
        super().hideEvent(event)

    def focusOutEvent(self, event):
        self.hide()
        super().focusOutEvent(event)


class PopupComboBox(QComboBox):
    about_to_show_popup = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._popup = ComboPopup()
        self._popup.item_selected.connect(self._apply_popup_selection)
        self._popup.popup_closed.connect(self._handle_popup_closed)
        self._popup_closing = False
        self.destroyed.connect(self._cleanup_popup)

    def apply_popup_style(self, accent_color):
        self._popup.apply_style(accent_color)

    def showPopup(self):
        self.about_to_show_popup.emit()
        if self._popup is not None and self._popup.isVisible():
            self.hidePopup()
            return
        if not self.isEnabled() or self.count() == 0:
            return
        self._popup.show_for_combo(self)

    def hidePopup(self):
        if self._popup is not None and self._popup.isVisible():
            self._popup_closing = True
            self._popup.hide()
            self._popup_closing = False
        super().hidePopup()

    def _apply_popup_selection(self, index, text):
        if 0 <= index < self.count():
            self.setCurrentIndex(index)
        else:
            self.setCurrentText(text)
        self.hidePopup()
        self.setFocus()

    def _handle_popup_closed(self):
        if not self._popup_closing:
            super().hidePopup()

    def _cleanup_popup(self):
        if self._popup is not None:
            self._popup.hide()
            self._popup.deleteLater()
            self._popup = None
