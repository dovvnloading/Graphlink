import qtawesome as qta
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from graphlink_chat_library_web import ChatLibraryWebHost
from graphlink_config import get_current_palette, get_surface_color


class ChatLibraryDialog(QDialog):
    """A custom library window for managing saved chat sessions.

    Phase 4 increment 4: a genuine hybrid. This native, frameless, drag-able
    QDialog shell is retained exactly as it was (window chrome, drop shadow,
    title bar, eventFilter drag mechanic, Close button, centering, Escape-to-
    close), but its former content region (search box + New Chat/Rename/Delete
    toolbar + QListWidget + status label) is replaced by an embedded
    ChatLibraryWebHost that renders the list/search/CRUD in React. All the DB
    reads/writes and the new-chat/load intents now live in ChatLibraryBridge;
    delete/rename confirmation is a two-step in-UI confirm on the web side.
    """

    def __init__(self, session_manager, parent=None):
        super().__init__(parent)
        self.session_manager = session_manager
        self._drag_offset = None

        self.setObjectName("libraryWindow")
        self.setWindowTitle("Chat Library")
        # UI-refactor P1 (audit B3/B4): no longer a fresh top-level frameless
        # stay-on-top window per open (whose first-show race was the
        # first-click no-op) - a plain embedded child widget, cached once,
        # opened/scrimmed/clamped by OverlayManager. WA_DeleteOnClose dropped
        # for the same reason: the instance persists.
        # QDialog sets the Dialog WINDOW flag by default even with a parent -
        # without this explicit Widget flag it still opens as a top-level
        # window (translucent + frameless-stripped = an INVISIBLE one).
        self.setWindowFlags(Qt.WindowType.Widget)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(False)
        self.resize(560, 640)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(12, 12, 12, 14)
        outer_layout.setSpacing(0)

        # UI-refactor P1: the QGraphicsDropShadowEffect that used to sit on
        # this shell WAS audit finding B7 - the "library body is a black
        # void". A QGraphicsEffect on an ancestor forces software rendering
        # that the GPU-composited ChatLibraryWebHost inside cannot join, so
        # the entire web region painted as a solid black rectangle while the
        # native header rendered fine. No ancestor of a webview may carry a
        # QGraphicsEffect, ever.
        self.shell = QFrame()
        self.shell.setObjectName("libraryShell")
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

        # The former search/toolbar/list/status region, now rendered by React.
        self.web_host = ChatLibraryWebHost(self.session_manager, self)
        root_layout.addWidget(self.web_host, 1)

        self._drag_widgets = (
            self.title_bar,
            self.icon_badge,
            self.section_label,
            self.header_title,
            self.header_meta,
        )
        for widget in self._drag_widgets:
            widget.installEventFilter(self)

        self.on_theme_changed()

    def _position_window(self):
        # P1: parent-RELATIVE centering with a clamp inside the parent rect
        # (this is a plain child widget now - audit B4's "no dialog pixel
        # outside the window" applies here too).
        parent = self.parentWidget()
        if parent is None:
            return
        margin = 16
        width = min(self.width(), parent.width() - 2 * margin)
        height = min(self.height(), parent.height() - 2 * margin)
        self.resize(max(width, 0), max(height, 0))
        x = (parent.width() - self.width()) // 2
        y = (parent.height() - self.height()) // 2
        self.move(max(margin, x), max(margin, y))

    def show_centered(self):
        self._position_window()
        self.show()
        self.raise_()
        # Give keyboard focus to the web content so its search input (which
        # autofocuses on mount) can receive typing immediately - the web
        # equivalent of the legacy search_input.setFocus().
        self.web_host.setFocus()

    def on_theme_changed(self):
        palette = get_current_palette()
        accent = palette.SELECTION.name()
        panel_gray = "rgba(42, 42, 42, 248)"
        line_gray = "rgba(255, 255, 255, 0.08)"
        muted_text = get_surface_color("chrome_inactive")
        badge_gray = "rgba(255, 255, 255, 0.025)"

        self.icon_badge.setPixmap(qta.icon("fa5s.book", color=accent).pixmap(16, 16))

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
                color: {get_surface_color("text_strong")};
                font-size: 16px;
                font-weight: 700;
            }}
            QLabel#libraryWindowMeta {{
                color: {muted_text};
                font-size: 11px;
            }}
            QPushButton#libraryCloseButton {{
                background-color: rgba(255, 255, 255, 0.04);
                color: {get_surface_color("text_strong")};
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                padding: 8px 14px;
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton#libraryCloseButton:hover {{
                background-color: rgba(255, 255, 255, 0.08);
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
        # Construct-per-open (WA_DeleteOnClose): tear the embedded host down on
        # close so each open/close cycle unregisters it from the shared
        # shutdown registry instead of leaking a dead reference into _hosts.
        if self.web_host is not None:
            self.web_host.prepare_for_shutdown()
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
