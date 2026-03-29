from datetime import datetime

import qtawesome as qta
from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from graphite_config import get_current_palette, get_neutral_button_colors


class ChatLibraryDialog(QDialog):
    """
    A custom library window for managing saved chat sessions.
    """

    def __init__(self, session_manager, parent=None):
        super().__init__(parent)
        self.session_manager = session_manager
        self._drag_offset = None

        self.setObjectName("libraryWindow")
        self.setWindowTitle("Chat Library")
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(False)
        self.resize(560, 640)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(12, 12, 12, 14)
        outer_layout.setSpacing(0)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 10)
        shadow.setColor(Qt.GlobalColor.black)

        self.shell = QFrame()
        self.shell.setObjectName("libraryShell")
        self.shell.setGraphicsEffect(shadow)
        outer_layout.addWidget(self.shell)

        root_layout = QVBoxLayout(self.shell)
        root_layout.setContentsMargins(16, 14, 16, 16)
        root_layout.setSpacing(12)

        self.title_bar = QFrame()
        self.title_bar.setObjectName("libraryTitleBar")
        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(10)

        self.icon_badge = QLabel()
        self.icon_badge.setObjectName("libraryIconBadge")
        self.icon_badge.setFixedSize(34, 34)
        self.icon_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_layout.addWidget(self.icon_badge, 0, Qt.AlignmentFlag.AlignTop)

        header_column = QVBoxLayout()
        header_column.setContentsMargins(0, 0, 0, 0)
        header_column.setSpacing(2)

        self.section_label = QLabel("LIBRARY")
        self.section_label.setObjectName("librarySectionLabel")
        header_column.addWidget(self.section_label)

        self.header_title = QLabel("Chat Library")
        self.header_title.setObjectName("libraryWindowTitle")
        header_column.addWidget(self.header_title)

        self.header_meta = QLabel("Browse, reopen, rename, and clean up saved projects.")
        self.header_meta.setObjectName("libraryWindowMeta")
        self.header_meta.setWordWrap(True)
        header_column.addWidget(self.header_meta)

        title_layout.addLayout(header_column, 1)

        self.close_button = QPushButton("Close")
        self.close_button.setObjectName("libraryCloseButton")
        self.close_button.clicked.connect(self.close)
        title_layout.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        root_layout.addWidget(self.title_bar)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("librarySearchInput")
        self.search_input.setPlaceholderText("Search chats...")
        self.search_input.textChanged.connect(self.filter_chats)
        root_layout.addWidget(self.search_input)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(8)

        self.new_chat_btn = self._create_action_button("New Chat")
        self.new_chat_btn.clicked.connect(self.new_chat)
        toolbar.addWidget(self.new_chat_btn)

        self.rename_btn = self._create_action_button("Rename")
        self.rename_btn.clicked.connect(self.rename_selected)
        toolbar.addWidget(self.rename_btn)

        self.delete_btn = self._create_action_button("Delete", danger=True)
        self.delete_btn.clicked.connect(self.delete_selected)
        toolbar.addWidget(self.delete_btn)

        toolbar.addStretch(1)
        root_layout.addLayout(toolbar)

        self.chat_list = QListWidget()
        self.chat_list.setObjectName("libraryList")
        self.chat_list.setFrameShape(QFrame.Shape.NoFrame)
        self.chat_list.setSpacing(6)
        self.chat_list.setAlternatingRowColors(False)
        self.chat_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.chat_list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.chat_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.chat_list.itemDoubleClicked.connect(self.load_chat)
        self.chat_list.itemActivated.connect(self.load_chat)
        self.chat_list.itemSelectionChanged.connect(self._update_action_states)
        root_layout.addWidget(self.chat_list, 1)

        self.status_label = QLabel()
        self.status_label.setObjectName("libraryStatusLabel")
        root_layout.addWidget(self.status_label)

        self._drag_widgets = (
            self.title_bar,
            self.icon_badge,
            self.section_label,
            self.header_title,
            self.header_meta,
        )
        for widget in self._drag_widgets:
            widget.installEventFilter(self)

        self.refresh_chat_list()
        self.on_theme_changed()

    def _create_action_button(self, text, danger=False):
        button = QPushButton(text)
        button.setObjectName("libraryActionButton")
        button.setProperty("danger", danger)
        return button

    def _position_window(self):
        parent = self.parentWidget()
        if parent is not None and parent.isVisible():
            parent_geometry = parent.frameGeometry()
            target = parent_geometry.center() - self.rect().center()
            self.move(target)
            return

        screen = QGuiApplication.primaryScreen()
        available_geometry = screen.availableGeometry() if screen else None
        if available_geometry is None:
            return

        target = available_geometry.center() - self.rect().center()
        self.move(target)

    def show_centered(self):
        self._position_window()
        self.show()
        self.raise_()
        self.activateWindow()
        self.search_input.setFocus()

    def on_theme_changed(self):
        palette = get_current_palette()
        button_colors = get_neutral_button_colors()
        accent = palette.SELECTION.name()
        panel_gray = "rgba(42, 42, 42, 248)"
        line_gray = "rgba(255, 255, 255, 0.08)"
        muted_text = "#8d8d8d"
        soft_text = "#d5d9df"
        hover_gray = "rgba(255, 255, 255, 0.055)"
        badge_gray = "rgba(255, 255, 255, 0.025)"

        self.icon_badge.setPixmap(qta.icon("fa5s.book", color=accent).pixmap(16, 16))
        self.new_chat_btn.setIcon(qta.icon("fa5s.plus", color=button_colors["icon"].name()))
        self.rename_btn.setIcon(qta.icon("fa5s.edit", color=button_colors["icon"].name()))
        self.delete_btn.setIcon(qta.icon("fa5s.trash", color=button_colors["icon"].name()))

        self.setStyleSheet(f"""
            QDialog#libraryWindow {{
                background: transparent;
                border: none;
            }}
            QFrame#libraryShell {{
                background-color: {panel_gray};
                border: 1px solid {line_gray};
                border-radius: 14px;
            }}
            QFrame#libraryTitleBar,
            QFrame#libraryTitleBar QLabel,
            QFrame#libraryTitleBar QWidget {{
                background: transparent;
            }}
            QLabel#libraryIconBadge {{
                background-color: {badge_gray};
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 17px;
            }}
            QLabel#librarySectionLabel {{
                color: {muted_text};
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.14em;
            }}
            QLabel#libraryWindowTitle {{
                color: #f3f5f8;
                font-size: 16px;
                font-weight: 700;
            }}
            QLabel#libraryWindowMeta {{
                color: {muted_text};
                font-size: 11px;
            }}
            QLineEdit#librarySearchInput {{
                background-color: #2d2d2d;
                border: 1px solid #3f3f3f;
                border-radius: 9px;
                color: #ffffff;
                padding: 10px 12px;
                selection-background-color: #264f78;
            }}
            QLineEdit#librarySearchInput:hover {{
                border-color: #4a4a4a;
            }}
            QLineEdit#librarySearchInput:focus {{
                border-color: {accent};
            }}
            QPushButton#libraryActionButton {{
                background-color: {button_colors["background"].name()};
                color: #f3f5f8;
                border: 1px solid {button_colors["border"].name()};
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton#libraryActionButton:hover {{
                background-color: {button_colors["hover"].name()};
                border-color: {button_colors["hover"].lighter(112).name()};
            }}
            QPushButton#libraryActionButton:pressed {{
                background-color: {button_colors["pressed"].name()};
            }}
            QPushButton#libraryActionButton:disabled {{
                color: #818181;
                border-color: rgba(255, 255, 255, 0.04);
                background-color: rgba(255, 255, 255, 0.03);
            }}
            QPushButton#libraryActionButton[danger="true"] {{
                border-color: rgba(255, 255, 255, 0.1);
            }}
            QPushButton#libraryCloseButton {{
                background-color: rgba(255, 255, 255, 0.04);
                color: #f3f5f8;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                padding: 8px 14px;
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton#libraryCloseButton:hover {{
                background-color: rgba(255, 255, 255, 0.08);
            }}
            QListWidget#libraryList {{
                background: transparent;
                color: #ffffff;
                border: none;
                outline: none;
            }}
            QListWidget#libraryList::item {{
                background-color: rgba(255, 255, 255, 0.025);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 10px;
                margin: 0px;
                padding: 12px 14px;
            }}
            QListWidget#libraryList::item:hover {{
                background-color: {hover_gray};
                border-color: rgba(255, 255, 255, 0.08);
            }}
            QListWidget#libraryList::item:selected {{
                background-color: {accent};
                border-color: {accent};
                color: #ffffff;
            }}
            QLabel#libraryStatusLabel {{
                color: {soft_text};
                font-size: 11px;
                padding-left: 2px;
            }}
        """)

    def eventFilter(self, watched, event):
        if watched in self._drag_widgets:
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if event.type() == QEvent.Type.MouseMove and event.buttons() & Qt.MouseButton.LeftButton and self._drag_offset is not None:
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                return True
            if event.type() == QEvent.Type.MouseButtonRelease:
                self._drag_offset = None
                return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event):
        self._drag_offset = None
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            current_item = self.chat_list.currentItem()
            if current_item and not current_item.isHidden():
                self.load_chat(current_item)
                return
        super().keyPressEvent(event)

    def refresh_chat_list(self):
        self.chat_list.clear()
        chats = self.session_manager.db.get_all_chats()

        for chat_id, title, created_at, updated_at in chats:
            created_dt = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
            updated_dt = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")

            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, chat_id)
            item.setText(
                f"{title}\n"
                f"Updated {updated_dt.strftime('%b %d, %Y %I:%M %p')}\n"
                f"Created {created_dt.strftime('%b %d, %Y %I:%M %p')}"
            )
            item.setSizeHint(QSize(0, 78))
            self.chat_list.addItem(item)

        if self.chat_list.count():
            self.chat_list.setCurrentRow(0)

        self.update_status()
        self._update_action_states()

    def update_status(self):
        total_count = self.chat_list.count()
        visible_count = sum(not self.chat_list.item(i).isHidden() for i in range(total_count))

        if total_count == 0:
            self.status_label.setText("No saved chats yet.")
        elif visible_count != total_count:
            noun = "chat" if total_count == 1 else "chats"
            self.status_label.setText(f"Showing {visible_count} of {total_count} saved {noun}.")
        else:
            noun = "chat" if total_count == 1 else "chats"
            self.status_label.setText(f"{total_count} saved {noun}.")

    def _update_action_states(self):
        current_item = self.chat_list.currentItem()
        has_selection = current_item is not None and not current_item.isHidden()
        self.rename_btn.setEnabled(has_selection)
        self.delete_btn.setEnabled(has_selection)

    def filter_chats(self, text):
        query = text.strip().lower()
        first_visible_row = None

        for row in range(self.chat_list.count()):
            item = self.chat_list.item(row)
            is_hidden = query not in item.text().lower()
            item.setHidden(is_hidden)
            if not is_hidden and first_visible_row is None:
                first_visible_row = row

        current_item = self.chat_list.currentItem()
        if current_item is None or current_item.isHidden():
            if first_visible_row is not None:
                self.chat_list.setCurrentRow(first_visible_row)
            else:
                self.chat_list.clearSelection()

        self.update_status()
        self._update_action_states()

    def new_chat(self):
        if self.parent() and hasattr(self.parent(), "new_chat"):
            if self.parent().new_chat(parent_for_dialog=self):
                self.close()

    def delete_selected(self):
        current_item = self.chat_list.currentItem()
        if current_item is None or current_item.isHidden():
            return

        chat_id = current_item.data(Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(
            self,
            "Delete Chat",
            "Are you sure you want to delete this chat?\nThis action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.session_manager.db.delete_chat(chat_id)
            self.refresh_chat_list()
            self.filter_chats(self.search_input.text())

    def rename_selected(self):
        current_item = self.chat_list.currentItem()
        if current_item is None or current_item.isHidden():
            return

        chat_id = current_item.data(Qt.ItemDataRole.UserRole)
        current_title = current_item.text().split("\n", 1)[0]

        new_title, ok = QInputDialog.getText(
            self,
            "Rename Chat",
            "Enter new title:",
            text=current_title,
        )

        if ok and new_title:
            self.session_manager.db.rename_chat(chat_id, new_title)
            self.refresh_chat_list()
            self.filter_chats(self.search_input.text())

    def load_chat(self, item):
        if item is None or item.isHidden():
            return

        chat_id = item.data(Qt.ItemDataRole.UserRole)
        try:
            self.session_manager.load_chat(chat_id)
            if self.session_manager.window:
                self.session_manager.window.update_title_bar()
            self.close()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load chat: {str(exc)}")
