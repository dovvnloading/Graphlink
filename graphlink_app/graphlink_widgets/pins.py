"""Navigation-pin management panel and editor.

The panel is a projection of ``ChatScene.pin_store``. It intentionally does not
keep a second authoritative pin list or rebuild an arbitrary widget tree for every
mutation.
"""

from __future__ import annotations

import qtawesome as qta
from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QPoint,
    QRect,
    QSortFilterProxyModel,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QStyledItemDelegate,
    QStyle,
)

from graphlink_context_menu import create_context_menu
from graphlink_navigation_pins import (
    MAX_PIN_NOTE_LENGTH,
    MAX_PIN_TITLE_LENGTH,
    NavigationPinValidationError,
)


PIN_ID_ROLE = Qt.ItemDataRole.UserRole
PIN_NOTE_ROLE = Qt.ItemDataRole.UserRole + 1
PIN_POSITION_ROLE = Qt.ItemDataRole.UserRole + 2


class NavigationPinsListModel(QAbstractListModel):
    """Qt list model backed by the scene's authoritative pin store."""

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._records = list(store.records)
        store.subscribe(self._store_changed)

    def dispose(self):
        if self.store is not None:
            self.store.unsubscribe(self._store_changed)
            self.store = None

    def reset_from_store(self):
        """Refresh after a scene/store swap without exposing Qt internals."""
        if self.store is not None:
            self._store_changed("reset", self.store.records)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._records)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._records):
            return None
        record = self._records[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return record.title
        if role == Qt.ItemDataRole.ToolTipRole:
            return record.note or record.title
        if role == PIN_ID_ROLE:
            return record.pin_id
        if role == PIN_NOTE_ROLE:
            return record.note
        if role == PIN_POSITION_ROLE:
            return record.position
        return None

    def record_at(self, row):
        return self._records[row] if 0 <= row < len(self._records) else None

    def _store_changed(self, event, payload):
        if event == "added":
            row, record = payload
            self.beginInsertRows(QModelIndex(), row, row)
            self._records.insert(row, record)
            self.endInsertRows()
        elif event == "updated":
            row, _before, after = payload
            if 0 <= row < len(self._records):
                self._records[row] = after
                index = self.index(row, 0)
                self.dataChanged.emit(index, index)
        elif event == "removed":
            row, _record = payload
            if 0 <= row < len(self._records):
                self.beginRemoveRows(QModelIndex(), row, row)
                self._records.pop(row)
                self.endRemoveRows()
        elif event == "reset":
            self.beginResetModel()
            self._records = list(payload)
            self.endResetModel()


class NavigationPinsFilterModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._query = ""

    def set_query(self, query):
        self._query = str(query or "").strip().casefold()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        if not self._query:
            return True
        model = self.sourceModel()
        index = model.index(source_row, 0, source_parent)
        title = str(model.data(index, Qt.ItemDataRole.DisplayRole) or "")
        note = str(model.data(index, PIN_NOTE_ROLE) or "")
        return self._query in title.casefold() or self._query in note.casefold()


class NavigationPinDelegate(QStyledItemDelegate):
    """Spacious grayscale row renderer with deterministic text elision."""

    ROW_HEIGHT = 58
    NOTE_ROW_HEIGHT = 72

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pin_icon = qta.icon("fa5s.map-pin", color="#858585")
        self._selected_pin_icon = qta.icon("fa5s.map-pin", color="#d0d0d0")

    def sizeHint(self, option, index):
        note = str(index.data(PIN_NOTE_ROLE) or "")
        height = self.NOTE_ROW_HEIGHT if note else self.ROW_HEIGHT
        return QSize(option.rect.width(), height)

    def paint(self, painter, option, index):
        painter.save()
        rect = option.rect.adjusted(2, 4, -2, -4)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#3d3d3d" if selected else "#292929"))
        painter.drawRoundedRect(rect, 10, 10)

        marker_rect = QRect(rect.left() + 12, rect.center().y() - 10, 20, 20)
        (self._selected_pin_icon if selected else self._pin_icon).paint(
            painter, marker_rect, Qt.AlignmentFlag.AlignCenter
        )

        title = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        note = str(index.data(PIN_NOTE_ROLE) or "")
        title_font = QFont(option.font)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor("#f0f0f0"))
        title_rect = rect.adjusted(44, 8, -16, -rect.height() // 2)
        painter.drawText(
            title_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(title_font).elidedText(title, Qt.TextElideMode.ElideRight, title_rect.width()),
        )

        if note:
            note_font = QFont(option.font)
            note_font.setPointSize(max(8, note_font.pointSize() - 1))
            painter.setFont(note_font)
            painter.setPen(QColor("#9b9b9b"))
            note_rect = rect.adjusted(44, rect.height() // 2 - 2, -16, -8)
            painter.drawText(
                note_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                QFontMetrics(note_font).elidedText(note, Qt.TextElideMode.ElideRight, note_rect.width()),
            )
        painter.restore()


class NavigationPinEditor(QDialog):
    """Shared, validated editor for creating and editing a navigation pin."""

    def __init__(self, title="Waypoint", note="", parent=None, creating=False):
        super().__init__(parent)
        self.setWindowTitle("Add navigation pin" if creating else "Edit navigation pin")
        self.setModal(True)
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(10)

        heading = QLabel("Name this canvas location" if creating else "Update this canvas location")
        heading.setObjectName("navigationPinEditorHeading")
        layout.addWidget(heading)

        title_label = QLabel("Title")
        self.title_input = QLineEdit(str(title))
        self.title_input.setMaxLength(MAX_PIN_TITLE_LENGTH)
        self.title_input.setPlaceholderText("e.g. Research checkpoint")
        self.title_input.setAccessibleName("Navigation pin title")
        layout.addWidget(title_label)
        layout.addWidget(self.title_input)

        note_label = QLabel("Note (optional)")
        self.note_input = QTextEdit()
        self.note_input.setPlainText(str(note))
        self.note_input.setMaximumHeight(110)
        self.note_input.setAccessibleName("Navigation pin note")
        layout.addWidget(note_label)
        layout.addWidget(self.note_input)

        self.error_label = QLabel("")
        self.error_label.setObjectName("navigationPinEditorError")
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.title_input.selectAll()
        self.title_input.setFocus()

    def _validate_and_accept(self):
        title = self.title_input.text().strip()
        note = self.note_input.toPlainText().strip()
        try:
            if not title:
                raise NavigationPinValidationError("A title is required")
            if len(title) > MAX_PIN_TITLE_LENGTH:
                raise NavigationPinValidationError("The title is too long")
            if len(note) > MAX_PIN_NOTE_LENGTH:
                raise NavigationPinValidationError("The note is too long")
        except NavigationPinValidationError as error:
            self.error_label.setText(str(error))
            self.error_label.setVisible(True)
            return
        self.accept()

    def values(self):
        return self.title_input.text().strip(), self.note_input.toPlainText().strip()


class PinOverlay(QFrame):
    """Model-driven in-window navigation-pin panel."""

    closed = Signal()
    BASE_WIDTH = 400
    MAX_HEIGHT = 560
    MIN_HEIGHT = 276

    def __init__(self, canvas_view, parent=None, controller=None):
        super().__init__(parent)
        self.canvas_view = canvas_view
        self.controller = controller
        self._anchor_widget = None
        self.setObjectName("pinFlyoutPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumWidth(self.BASE_WIDTH)
        self.resize(self.BASE_WIDTH, self.MIN_HEIGHT)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 10)
        shadow.setColor(Qt.GlobalColor.black)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(14, 14, 16, 16)
        outer_layout.setSpacing(0)

        self.container = QFrame()
        self.container.setObjectName("pinFlyoutShell")
        self.container.setGraphicsEffect(shadow)
        outer_layout.addWidget(self.container)

        main_layout = QVBoxLayout(self.container)
        main_layout.setContentsMargins(18, 16, 18, 16)
        main_layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(12)
        self.icon_badge = QLabel()
        self.icon_badge.setObjectName("pinFlyoutBadge")
        self.icon_badge.setFixedSize(34, 34)
        self.icon_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.icon_badge, 0, Qt.AlignmentFlag.AlignVCenter)

        heading_column = QVBoxLayout()
        heading_column.setSpacing(4)
        self.header_text = QLabel("Navigation pins")
        self.header_text.setObjectName("pinFlyoutTitle")
        heading_column.addWidget(self.header_text)
        self.header_body = QLabel("Revisit saved canvas locations")
        self.header_body.setObjectName("pinFlyoutMeta")
        self.header_body.setWordWrap(False)
        heading_column.addWidget(self.header_body)
        header.addLayout(heading_column, 1)

        self.close_btn = QPushButton("Close")
        self.close_btn.setObjectName("pinFlyoutCloseButton")
        self.close_btn.setFixedHeight(32)
        self.close_btn.clicked.connect(self.close)
        header.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        main_layout.addLayout(header)

        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 2, 0, 0)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search pins...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumHeight(38)
        self.search_input.setAccessibleName("Search navigation pins")
        self.search_input.textChanged.connect(self._filter_changed)
        search_row.addWidget(self.search_input, 1)
        main_layout.addLayout(search_row)

        scene = self.canvas_view.scene()
        self.pin_model = NavigationPinsListModel(scene.pin_store, self)
        self.pin_filter = NavigationPinsFilterModel(self)
        self.pin_filter.setSourceModel(self.pin_model)
        self.pin_list = QListView()
        self.pin_list.setObjectName("pinListView")
        self.pin_list.setModel(self.pin_filter)
        self.pin_list.setItemDelegate(NavigationPinDelegate(self.pin_list))
        self.pin_list.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.pin_list.setEditTriggers(QListView.EditTrigger.NoEditTriggers)
        self.pin_list.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.pin_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.pin_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.pin_list.setSpacing(2)
        self.pin_list.setUniformItemSizes(False)
        self.pin_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.pin_list.clicked.connect(self._navigate_from_index)
        self.pin_list.customContextMenuRequested.connect(self._show_context_menu)
        scene.selectionChanged.connect(self._sync_selection)
        main_layout.addWidget(self.pin_list, 1)

        footer_shell = QFrame()
        footer_shell.setObjectName("pinFlyoutFooter")
        footer = QHBoxLayout(footer_shell)
        footer.setContentsMargins(12, 6, 10, 6)
        footer.setSpacing(12)
        self.pin_count_label = QLabel("")
        self.pin_count_label.setObjectName("pinFlyoutCount")
        self.pin_count_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        footer.addWidget(self.pin_count_label, 1)
        self.add_btn = QPushButton("Add pin here")
        self.add_btn.setObjectName("pinAddButton")
        self.add_btn.setMinimumSize(126, 36)
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.clicked.connect(self.create_pin)
        footer.addWidget(self.add_btn)
        main_layout.addWidget(footer_shell)

        self.pin_model.rowsInserted.connect(self._update_summary)
        self.pin_model.rowsRemoved.connect(self._update_summary)
        self.pin_model.modelReset.connect(self._update_summary)
        self._apply_style()
        self._update_summary()

    @property
    def pins(self):
        """Compatibility projection; the store remains authoritative."""
        scene = self.canvas_view.scene()
        return list(getattr(scene, "ordered_navigation_pins", lambda: scene.pins)())

    def _scene(self):
        return self.canvas_view.scene()

    def _apply_style(self):
        # Keep this surface neutral even when a legacy theme palette still has a
        # chromatic selection color. Pins are navigation chrome, not status.
        accent = "#8c8c8c"
        accent_text = "#161616"
        self.icon_badge.setPixmap(qta.icon("fa5s.map-marked-alt", color="#b8b8b8").pixmap(15, 15))
        self.add_btn.setIcon(qta.icon("fa5s.map-pin", color=accent_text))
        self.setStyleSheet(
            f"""
            QFrame#pinFlyoutPanel {{ background: transparent; border: none; }}
            QFrame#pinFlyoutShell {{
                background: #292929;
                border: 1px solid #505050;
                border-radius: 14px;
            }}
            QLabel#pinFlyoutBadge {{
                background: #333333;
                border: 1px solid #4b4b4b;
                border-radius: 15px;
            }}
            QLabel#pinFlyoutTitle {{ color: #f2f2f2; font-size: 15px; font-weight: 700; }}
            QLabel#pinFlyoutMeta, QLabel#pinFlyoutCount {{ color: #999999; font-size: 11px; }}
            QFrame#pinFlyoutFooter {{
                background: #202020; border: 1px solid #383838; border-radius: 10px;
            }}
            QLineEdit {{
                background: #202020; color: #eeeeee; border: 1px solid #494949;
                border-radius: 9px; padding: 8px 12px; min-height: 20px;
            }}
            QLineEdit:focus {{ border-color: #858585; }}
            QListView#pinListView {{
                background: #202020; border: 1px solid #454545; border-radius: 11px;
                padding: 7px; outline: none;
            }}
            QScrollBar:vertical {{
                background: transparent; width: 10px; margin: 8px 2px 8px 0;
            }}
            QScrollBar::handle:vertical {{
                background: #5d5d5d; min-height: 28px; border-radius: 5px;
            }}
            QScrollBar::handle:vertical:hover {{ background: #7a7a7a; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QPushButton#pinFlyoutCloseButton {{
                background: #353535; color: #eeeeee; border: 1px solid #555555;
                border-radius: 9px; min-width: 64px; padding: 0 12px;
                font-size: 11px; font-weight: 600;
            }}
            QPushButton#pinFlyoutCloseButton:hover {{ background: #454545; }}
            QPushButton#pinAddButton {{
                background: {accent}; color: {accent_text}; border: none;
                border-radius: 9px; min-height: 36px; padding: 0 16px;
                font-size: 11px; font-weight: 700;
            }}
            QPushButton#pinAddButton:hover {{ background: #a0a0a0; }}
            QPushButton#pinAddButton:disabled {{ background: #555555; color: #c9c9c9; }}
            QMenu {{ background: #252525; color: #eeeeee; border: 1px solid #505050; padding: 4px; }}
            QMenu::item {{ padding: 7px 24px 7px 12px; border-radius: 5px; }}
            QMenu::item:selected {{ background: #454545; }}
            QLabel#navigationPinEditorError {{ color: #b5b5b5; font-size: 11px; }}
            """
        )

    def on_theme_changed(self):
        self._apply_style()
        self.pin_list.viewport().update()

    def _update_summary(self, *args):
        count = self.pin_model.rowCount()
        self.pin_count_label.setText(f"{count} saved location" + ("" if count == 1 else "s"))
        self.add_btn.setEnabled(count < 100)
        if self.isVisible():
            self._resize_for_content()
            self.reposition()

    def _filter_changed(self, text):
        self.pin_filter.set_query(text)
        if self.isVisible():
            self._resize_for_content()
            self.reposition()

    def _pin_from_proxy_index(self, index):
        if not index.isValid():
            return None
        pin_id = self.pin_filter.data(index, PIN_ID_ROLE)
        return self._scene()._navigation_pin_item(pin_id)

    def _navigate_from_index(self, index):
        pin = self._pin_from_proxy_index(index)
        if pin is not None:
            self.navigate_to_pin(pin)

    def _sync_selection(self):
        selected_pin = next(
            (item for item in self._scene().selectedItems() if hasattr(item, "pin_id")),
            None,
        )
        if selected_pin is None:
            return
        for row in range(self.pin_model.rowCount()):
            if self.pin_model.data(self.pin_model.index(row, 0), PIN_ID_ROLE) != selected_pin.pin_id:
                continue
            proxy_index = self.pin_filter.mapFromSource(self.pin_model.index(row, 0))
            if proxy_index.isValid():
                self.pin_list.setCurrentIndex(proxy_index)
            break

    def _show_context_menu(self, position):
        index = self.pin_list.indexAt(position)
        pin = self._pin_from_proxy_index(index)
        if pin is None:
            return
        self.show_pin_context_menu(pin, self.pin_list.viewport().mapToGlobal(position))

    def show_pin_context_menu(self, pin, global_pos=None):
        if pin is None or pin.scene() != self._scene():
            return
        menu = create_context_menu(self, "Navigation pin")
        focus_action = menu.addAction("Focus canvas")
        edit_action = menu.addAction("Edit pin")
        menu.addSeparator()
        delete_action = menu.addAction("Delete pin")
        action = menu.exec(global_pos or QCursor.pos())
        if action == focus_action:
            self.navigate_to_pin(pin)
        elif action == edit_action:
            self.edit_pin(pin)
        elif action == delete_action:
            self.remove_pin(pin)

    def create_pin(self):
        view = self.canvas_view
        center = view.mapToScene(view.viewport().rect().center())
        pin = (
            self.controller.create_at(center)
            if self.controller is not None
            else self._scene().add_navigation_pin(center)
        )
        if self.edit_pin(pin, creating=True) is False:
            if self.controller is not None:
                self.controller.remove(pin)
            else:
                self._scene().remove_navigation_pin(pin)

    def remove_pin(self, pin):
        if self.controller is not None:
            self.controller.remove(pin)
        else:
            self._scene().remove_navigation_pin(pin)

    def navigate_to_pin(self, pin):
        if pin is None or pin.scene() != self._scene():
            return
        if self.controller is not None:
            self.controller.focus(pin)
        else:
            view = self.canvas_view
            self._scene().clearSelection()
            pin.setSelected(True)
            view.ensureVisible(pin, 48, 48)
            view.centerOn(pin)

    def edit_pin(self, pin, creating=False):
        editor = NavigationPinEditor(pin.title, pin.note, self, creating=creating)
        if editor.exec() != QDialog.DialogCode.Accepted:
            return False if creating else None
        title, note = editor.values()
        if self.controller is not None:
            self.controller.update(pin, title=title, note=note)
        else:
            self._scene().update_navigation_pin(pin, title=title, note=note)
        return True

    def refresh_pins(self):
        """Synchronize the model from the current scene store after a scene swap."""
        scene = self._scene()
        if self.pin_model.store is scene.pin_store:
            self.pin_model.reset_from_store()
        else:
            self.pin_model.dispose()
            self.pin_model = NavigationPinsListModel(scene.pin_store, self)
            self.pin_filter.setSourceModel(self.pin_model)
        self._update_summary()

    def update_pin(self, pin):
        self.refresh_pins()

    def add_pin_button(self, pin):
        self.refresh_pins()

    def clear_pins(self):
        self.refresh_pins()

    def show_for_anchor(self, anchor_widget):
        self._anchor_widget = anchor_widget
        self.refresh_pins()
        self._resize_for_content()
        self.reposition()
        self.show()
        self.raise_()
        self.activateWindow()
        self.search_input.setFocus()

    def _resize_for_content(self):
        rows = min(max(1, self.pin_filter.rowCount()), 6)
        list_height = 18
        for row in range(self.pin_filter.rowCount()):
            index = self.pin_filter.index(row, 0)
            has_note = bool(self.pin_filter.data(index, PIN_NOTE_ROLE))
            list_height += (
                NavigationPinDelegate.NOTE_ROW_HEIGHT
                if has_note
                else NavigationPinDelegate.ROW_HEIGHT
            )
        list_height += max(0, rows - 1) * self.pin_list.spacing()
        chrome_height = 224
        self.resize(self.BASE_WIDTH, min(self.MAX_HEIGHT, max(self.MIN_HEIGHT, chrome_height + list_height)))

    def reposition(self):
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

    def closeEvent(self, event):
        self._anchor_widget = None
        super().closeEvent(event)
