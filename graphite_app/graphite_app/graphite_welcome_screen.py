import qtawesome as qta
import random
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QFrame,
    QScrollArea, QMainWindow
)
from PySide6.QtCore import Qt, Signal, QTimer, QRectF, QSize
from PySide6.QtGui import (
    QIcon, QGuiApplication, QPainter, QColor, QBrush, QPen,
    QLinearGradient, QRadialGradient, QFont, QTextOption, QCursor,
    QFontMetrics
)
from datetime import datetime
from graphite_core import ChatSessionManager
from graphite_system_dialogs import HelpDialog
from graphite_config import get_current_palette
from graphite_widgets import CustomTooltip


class GridBackgroundWidget(QWidget):
    """A widget that draws a custom background with a dot grid and a vignette effect."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.grid_size = 20
        self.grid_opacity = 0.3
        self.grid_color = QColor("#555555")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#1e1e1e"))

        painter.setPen(Qt.PenStyle.NoPen)
        minor_color = QColor(self.grid_color)
        minor_color.setAlphaF(self.grid_opacity)
        painter.setBrush(minor_color)

        left, top, right, bottom = self.rect().left(), self.rect().top(), self.rect().right(), self.rect().bottom()
        
        minor_left = left - (left % self.grid_size)
        minor_top = top - (top % self.grid_size)
        dot_size = 1.5

        for x in range(minor_left, right, self.grid_size):
            for y in range(minor_top, bottom, self.grid_size):
                painter.drawEllipse(QRectF(x - dot_size / 2, y - dot_size / 2, dot_size, dot_size))

        vignette_gradient = QRadialGradient(self.rect().center(), max(self.width(), self.height()) / 1.5)
        vignette_gradient.setColorAt(0.4, QColor(30, 30, 30, 0))
        vignette_gradient.setColorAt(1.0, QColor(30, 30, 30, 255))
        painter.fillRect(self.rect(), vignette_gradient)


class StarterNodeWidget(QWidget):
    """A custom, clickable widget styled like a node, used for conversation starters."""
    clicked = Signal()

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setFixedSize(220, 90)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._text = text
        self._hovered = False
        self.setMouseTracking(True)

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        palette = get_current_palette()
        path = QRectF(0, 0, self.width(), self.height())

        gradient = QLinearGradient(path.topLeft(), path.bottomLeft())
        gradient.setColorAt(0, QColor("#4a4a4a"))
        gradient.setColorAt(1, QColor("#2d2d2d"))
        painter.setBrush(gradient)

        if self._hovered:
            pen = QPen(QColor("#ffffff"), 2)
        else:
            pen = QPen(palette.USER_NODE, 1.5)

        painter.setPen(pen)
        painter.drawRoundedRect(path, 10, 10)

        painter.setPen(QColor("#e0e0e0"))
        font = QFont("Segoe UI", 9)
        painter.setFont(font)
        
        text_rect = path.adjusted(10, 10, -10, -10)
        text_option = QTextOption(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap)
        painter.drawText(text_rect, self._text, text_option)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ProjectButton(QWidget):
    """A custom widget representing a clickable button for a recent project."""
    clicked = Signal()

    def __init__(self, title, updated_at, parent=None):
        super().__init__(parent)
        self._title = title
        self._updated_at = updated_at
        self._hovered = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

        self.tooltip_widget = CustomTooltip(self)
        self.tooltip_timer = QTimer(self)
        self.tooltip_timer.setSingleShot(True)
        self.tooltip_timer.setInterval(500)
        self.tooltip_timer.timeout.connect(self._show_tooltip)

    def sizeHint(self):
        return QSize(super().sizeHint().width(), 45)

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        self.tooltip_timer.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        self.tooltip_timer.stop()
        self.tooltip_widget.hide()
        super().leaveEvent(event)
        
    def _show_tooltip(self):
        self.tooltip_widget.setText(f"Last updated: {self._updated_at}")
        self.tooltip_widget.adjustSize()
        tooltip_pos = QCursor.pos()
        self.tooltip_widget.move(tooltip_pos.x() + 15, tooltip_pos.y() + 15)
        self.tooltip_widget.show()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        palette = get_current_palette()
        rect = self.rect()
        
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0, QColor("#4a4a4a"))
        gradient.setColorAt(1, QColor("#2d2d2d"))
        painter.setBrush(gradient)

        if self._hovered:
            pen = QPen(palette.SELECTION.lighter(120), 2)
        else:
            pen = QPen(palette.SELECTION, 1.5)
        
        painter.setPen(pen)
        painter.drawRoundedRect(rect.adjusted(1,1,-1,-1), 6, 6)

        painter.setPen(QColor("#e0e0e0"))
        font = QFont("Segoe UI", 10, QFont.Weight.Bold)
        painter.setFont(font)
        
        title_rect = rect.adjusted(12, 0, -12, 0)
        metrics = QFontMetrics(font)
        elided_title = metrics.elidedText(self._title, Qt.TextElideMode.ElideRight, title_rect.width())
        
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided_title)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class WelcomeScreen(QMainWindow):
    """
    The main window for the welcome screen, providing options to start new chats,
    load recent projects, or use conversation starters.
    """
    def __init__(self, settings_manager, main_window, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.session_manager = ChatSessionManager(window=None)
        self.main_window = main_window

        self.setWindowTitle("Graphlink - Welcome")
        self.setGeometry(0, 0, 800, 550)
        
        icon_path = r"C:\Users\Admin\source\repos\graphite_app\assets\graphite.ico"
        self.setWindowIcon(QIcon(str(icon_path)))

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        self.grid_background = GridBackgroundWidget(central_widget)
        self.content_container = QWidget(central_widget)
        self.content_container.setStyleSheet("background: transparent;")
        
        main_layout = QVBoxLayout(self.content_container)
        main_layout.setContentsMargins(40, 30, 40, 30)
        main_layout.setSpacing(25)

        main_layout.addWidget(self._create_header())
        main_layout.addWidget(self._create_recent_projects())
        main_layout.addWidget(self._create_starters())
        main_layout.addStretch()

        screen = QGuiApplication.primaryScreen().geometry()
        self.move(int((screen.width() - self.width()) / 2) - 420, int((screen.height() - self.height()) / 2))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.grid_background.setGeometry(self.centralWidget().rect())
        self.content_container.setGeometry(self.centralWidget().rect())

    def closeEvent(self, event):
        if hasattr(self, 'scroll_timer') and self.scroll_timer.isActive():
            self.scroll_timer.stop()
        super().closeEvent(event)

    def _create_header(self):
        header_widget = QWidget()
        layout = QVBoxLayout(header_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        
        title = QLabel("Graphlink")
        title.setStyleSheet("font-size: 32px; font-weight: bold; color: #2ecc71; background: transparent;")
        
        subtitle = QLabel("Welcome back. Let's create something new.")
        subtitle.setStyleSheet("font-size: 14px; color: #aaaaaa; background: transparent;")
        
        layout.addWidget(title)
        layout.addWidget(subtitle)
        return header_widget

    def _create_recent_projects(self):
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setSpacing(10)
        
        title = QLabel("Recent Projects")
        title.setStyleSheet("font-size: 16px; font-weight: bold; margin-bottom: 5px; background: transparent;")
        layout.addWidget(title)
        
        recent_chats = self.session_manager.db.get_all_chats()[:5]
        
        if not recent_chats:
            no_chats_label = QLabel("No recent projects found. Start a new chat to begin!")
            no_chats_label.setStyleSheet("color: #777777; font-style: italic; background: transparent;")
            layout.addWidget(no_chats_label)
        else:
            for chat_id, chat_title, _, updated_at in recent_chats:
                updated_dt = datetime.strptime(updated_at, '%Y-%m-%d %H:%M:%S')
                
                project_button = ProjectButton(chat_title, updated_dt.strftime('%Y-%m-%d %H:%M'))
                project_button.clicked.connect(lambda c_id=chat_id: self.load_project(c_id))
                layout.addWidget(project_button)
        
        return container

    def _create_starters(self):
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setSpacing(10)

        title = QLabel("Conversation Starters")
        title.setStyleSheet("font-size: 16px; font-weight: bold; margin-bottom: 5px; background: transparent;")
        layout.addWidget(title)

        self.starters_scroll_area = QScrollArea()
        self.starters_scroll_area.setWidgetResizable(True)
        self.starters_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.starters_scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.starters_scroll_area.setFixedHeight(110)
        self.starters_scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        starters_widget = QWidget()
        starters_widget.setStyleSheet("background: transparent;")
        starters_layout = QHBoxLayout(starters_widget)
        starters_layout.setSpacing(15)

        starters = [
            "Explain quantum computing like I'm five years old.",
            "Draft a polite but firm email to a client about an overdue invoice.",
            "What are the key differences between Python lists and tuples?",
            "Brainstorm three unique business ideas using AI.",
            "Write a short, four-line poem about the sunset.",
            "Create a 3-day workout plan for a beginner.",
            "Summarize the plot of 'Dune' in three sentences.",
            "Generate a list of 5 healthy, easy-to-make lunch recipes.",
            "Write a Python script to rename all files in a directory.",
            "Explain the concept of blockchain in simple terms."
        ]
        
        random.shuffle(starters)
        full_starters_list = starters + starters

        for prompt in full_starters_list:
            starter_node = StarterNodeWidget(prompt)
            starter_node.clicked.connect(lambda p=prompt: self.start_new_chat(prompt=p))
            starters_layout.addWidget(starter_node)

        self.starters_scroll_area.setWidget(starters_widget)
        layout.addWidget(self.starters_scroll_area)
        
        self.scroll_timer = QTimer(self)
        self.scroll_timer.setInterval(30)
        self.scroll_timer.timeout.connect(self._tick_scroll)
        
        QTimer.singleShot(100, self._setup_starter_animation)
        
        return container
    
    def _setup_starter_animation(self):
        self.scroll_timer.start()
        self.starters_scroll_area.enterEvent = lambda event: self.scroll_timer.stop()
        self.starters_scroll_area.leaveEvent = lambda event: self.scroll_timer.start()
        
    def _tick_scroll(self):
        try:
            scrollbar = self.starters_scroll_area.horizontalScrollBar()
            max_val = scrollbar.maximum()
            
            if max_val == 0: return

            current_val = scrollbar.value()
            new_val = current_val + 1
            
            half_point = max_val / 2
            
            if new_val >= half_point:
                scrollbar.setValue(int(new_val - half_point))
            else:
                scrollbar.setValue(new_val)
        except RuntimeError:
            self.scroll_timer.stop()

    def load_project(self, chat_id):
        if not self.main_window:
            return
        self.main_window.session_manager.load_chat(chat_id)
        self.main_window.update_title_bar()
        self.main_window.activateWindow()
        self.main_window.raise_()
        self.close()

    def start_new_chat(self, prompt=None):
        if not self.main_window:
            return
        self.main_window.new_chat()
        self.main_window.activateWindow()
        self.main_window.raise_()
        if prompt and isinstance(prompt, str):
            self.main_window.start_with_prompt(prompt)
        self.close()

    def open_library(self):
        if not self.main_window:
            return
        self.main_window.show_library()
        self.main_window.activateWindow()
        self.main_window.raise_()