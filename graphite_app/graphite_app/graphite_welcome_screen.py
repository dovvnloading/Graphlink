import qtawesome as qta
import random
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QFrame,
    QScrollArea, QMainWindow, QGraphicsOpacityEffect
)
from PySide6.QtCore import Qt, Signal, QTimer, QRectF, QSize, QPropertyAnimation, QEasingCurve
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
        self._current_category = None

        self.setWindowTitle("Graphlink - Welcome")
        self.setGeometry(0, 0, 800, 550)
        
        icon_path = r"C:\Users\Admin\source\repos\graphite_app\assets\graphite.ico"
        self.setWindowIcon(QIcon(str(icon_path)))

        self._init_starter_data()

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

    def _init_starter_data(self):
        """Initialize the categorized conversation starters relevant to Graphlink's workflow."""
        self.starter_categories = {
            "Tech": [
                "Design a scalable microservices architecture and break down each service.",
                "Help me debug a complex Python memory leak step-by-step.",
                "Outline an ML training workflow: data prep, training, and validation.",
                "Compare two sorting algorithms with code and complexity analysis.",
                "Evaluate authentication strategies for a modern web application.",
                "Review a refactoring strategy by mapping old vs. new architecture.",
                "Write a technical spec for a REST API and draft the JSON schemas.",
                "Brainstorm a data processing pipeline exploring batch vs. streaming."
            ],
            "Finance": [
                "Model a 5-year financial projection for a SaaS startup.",
                "Analyze the risks and benefits of algorithmic high-frequency trading.",
                "Draft a comprehensive investment strategy across multiple asset classes.",
                "Break down the tax implications of remote work across different states.",
                "Evaluate the economic impact of carbon pricing on manufacturing.",
                "Design a decentralized finance lending protocol architecture.",
                "Outline a framework for detecting fraud in credit card transactions.",
                "Compare the historical performance of value vs. growth investing."
            ],
            "Health": [
                "Design an experiment to test user retention in a fitness app.",
                "Map out a multi-step research plan for optimizing sleep patterns.",
                "Compare three different dietary approaches for long-term cardiovascular health.",
                "Draft a protocol for a clinical trial evaluating a new wellness device.",
                "Analyze the ethical considerations of AI in diagnostic medicine.",
                "Outline a public health response strategy for a novel pathogen.",
                "Evaluate the long-term impacts of microplastics on human physiology.",
                "Develop a personalized training block for a marathon prep."
            ],
            "Legal": [
                "Draft a mutual Non-Disclosure Agreement highlighting key termination clauses.",
                "Break down the GDPR compliance requirements for a new mobile app.",
                "Analyze the legal distinctions between independent contractors and employees.",
                "Outline a strategy for patenting a software algorithm in multiple jurisdictions.",
                "Compare open-source licenses: MIT, GPL, and Apache 2.0.",
                "Draft a terms of service agreement for a user-generated content platform.",
                "Evaluate the liability risks of deploying autonomous vehicles.",
                "Construct a framework for handling cross-border data transfer disputes."
            ],
            "Research": [
                "Map out a multi-step research plan for optimizing database queries.",
                "Draft a literature review outline on the impacts of quantum cryptography.",
                "Design a methodology for evaluating the bias in large language models.",
                "Compare historical economic inflation periods and extract key policy shifts.",
                "Analyze the sociological impacts of remote work on urban development.",
                "Outline an experimental design to test a new battery chemistry.",
                "Investigate the correlation between social media use and attention spans.",
                "Formulate a hypothesis and testing criteria for a new behavioral economics theory."
            ],
            "Planning": [
                "Draft a project proposal and explore different scenarios in parallel.",
                "Construct an incident response plan: communication, mitigation, post-mortem.",
                "Plan a content migration strategy, detailing mapping, extraction, and QA.",
                "Break down a multi-phase launch strategy for a new consumer hardware product.",
                "Outline a comprehensive 90-day onboarding program for new engineers.",
                "Design a disaster recovery plan for a multi-region cloud deployment.",
                "Map out an expansion strategy into an emerging international market.",
                "Develop a robust testing strategy covering unit, integration, and e2e."
            ]
        }
        
        # Build the 'All' category by taking 2 random items from every other category
        all_starters = []
        for category_list in self.starter_categories.values():
            all_starters.extend(random.sample(category_list, 2))
        random.shuffle(all_starters)
        
        self.starter_categories = {"All": all_starters} | self.starter_categories

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
        layout.setContentsMargins(0, 0, 0, 0)

        # Header and categories filter row
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        title = QLabel("Conversation Starters")
        title.setStyleSheet("font-size: 16px; font-weight: bold; background: transparent;")
        header_layout.addWidget(title)
        header_layout.addSpacing(15)

        self.category_buttons = {}
        for category in self.starter_categories.keys():
            btn = QPushButton(category)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("active", False)
            btn.setStyleSheet(self._get_category_button_style())
            btn.clicked.connect(lambda checked=False, c=category: self.set_starter_category(c))
            self.category_buttons[category] = btn
            header_layout.addWidget(btn)

        header_layout.addStretch()
        layout.addLayout(header_layout)

        # Scroll area for starter nodes
        self.starters_scroll_area = QScrollArea()
        self.starters_scroll_area.setWidgetResizable(True)
        self.starters_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.starters_scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.starters_scroll_area.setFixedHeight(110)
        self.starters_scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.starters_widget = QWidget()
        self.starters_widget.setStyleSheet("background: transparent;")
        self.starters_layout = QHBoxLayout(self.starters_widget)
        self.starters_layout.setSpacing(15)
        self.starters_layout.setContentsMargins(0, 0, 0, 0)

        # Apply Opacity Effect for cross-fading
        self.opacity_effect = QGraphicsOpacityEffect(self.starters_widget)
        self.opacity_effect.setOpacity(0.0)  # Start hidden for initial fade-in
        self.starters_widget.setGraphicsEffect(self.opacity_effect)

        self.fade_animation = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_animation.setEasingCurve(QEasingCurve.Type.InOutQuad)

        self.starters_scroll_area.setWidget(self.starters_widget)
        layout.addWidget(self.starters_scroll_area)
        
        self.scroll_timer = QTimer(self)
        self.scroll_timer.setInterval(30)
        self.scroll_timer.timeout.connect(self._tick_scroll)
        
        self.starters_scroll_area.enterEvent = lambda event: self.scroll_timer.stop()
        self.starters_scroll_area.leaveEvent = lambda event: self.scroll_timer.start()

        # Initialize the default category gracefully
        self._current_category = "All"
        self._update_button_styles("All")
        self._repopulate_starters("All")
        
        # Trigger initial gentle fade-in
        self.fade_animation.setStartValue(0.0)
        self.fade_animation.setEndValue(1.0)
        self.fade_animation.setDuration(400)
        self.fade_animation.finished.connect(self.scroll_timer.start)
        QTimer.singleShot(100, self.fade_animation.start)
        
        return container
    
    def _get_category_button_style(self):
        return """
            QPushButton {
                background: transparent;
                color: #888888;
                font-size: 12px;
                font-weight: bold;
                border: none;
                padding: 4px 8px;
                border-radius: 4px;
            }
            QPushButton:hover {
                color: #ffffff;
                background: #3a3a3a;
            }
            QPushButton[active="true"] {
                color: #2ecc71;
                background: #2a3d32;
            }
        """

    def _update_button_styles(self, category_name):
        for name, btn in self.category_buttons.items():
            is_active = (name == category_name)
            btn.setProperty("active", is_active)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def set_starter_category(self, category_name):
        if self._current_category == category_name:
            return

        self._current_category = category_name
        self._update_button_styles(category_name)
        self.scroll_timer.stop()

        # Disconnect any lingering signals to avoid double-firing during spam clicks
        try:
            self.fade_animation.finished.disconnect()
        except RuntimeError:
            pass

        # Fade out rapidly
        self.fade_animation.stop()
        self.fade_animation.setStartValue(self.opacity_effect.opacity())
        self.fade_animation.setEndValue(0.0)
        self.fade_animation.setDuration(150)
        self.fade_animation.finished.connect(self._on_fade_out_complete)
        self.fade_animation.start()

    def _on_fade_out_complete(self):
        # Swap content while invisible
        self._repopulate_starters(self._current_category)

        try:
            self.fade_animation.finished.disconnect()
        except RuntimeError:
            pass

        # Fade back in smoothly
        self.fade_animation.setStartValue(0.0)
        self.fade_animation.setEndValue(1.0)
        self.fade_animation.setDuration(250)
        self.fade_animation.finished.connect(self.scroll_timer.start)
        self.fade_animation.start()

    def _repopulate_starters(self, category_name):
        # Clear existing nodes
        while self.starters_layout.count():
            item = self.starters_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        # Get prompts and multiply to ensure seamless horizontal infinite scroll
        prompts = self.starter_categories.get(category_name, [])
        random.shuffle(prompts)
        
        # Ensure list is long enough to fill screen + overflow before repeating
        multiplier = max(2, (15 // len(prompts)) + 1) if prompts else 1
        full_starters_list = prompts * multiplier * 2 

        # Repopulate layout
        for prompt in full_starters_list:
            starter_node = StarterNodeWidget(prompt)
            starter_node.clicked.connect(lambda p=prompt: self.start_new_chat(prompt=p))
            self.starters_layout.addWidget(starter_node)

        # Reset scrollbar
        self.starters_scroll_area.horizontalScrollBar().setValue(0)

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
