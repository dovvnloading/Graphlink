from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QGraphicsDropShadowEffect, QApplication
from PySide6.QtCore import QTimer, QPropertyAnimation, QEasingCurve, QRect, Qt, Signal
from PySide6.QtGui import QPixmap, QIcon, QColor
import markdown
import qtawesome as qta
from graphite_config import get_current_palette

class CustomTitleBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.setObjectName("titleBar")
        
        icon_path = r"C:\Users\Admin\source\repos\graphite_app\assets\graphite.ico"
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 0, 0)

        icon_label = QLabel()
        icon_pixmap = QPixmap(str(icon_path)).scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        icon_label.setPixmap(icon_pixmap)
        layout.addWidget(icon_label)
        
        self.title = QLabel("Graphlink")
        layout.addWidget(self.title)
        layout.addStretch()
        
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(0)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        
        self.minimize_btn = QPushButton("🗕")
        self.maximize_btn = QPushButton("🗖")
        self.close_btn = QPushButton("✕")
        
        for btn in (self.minimize_btn, self.maximize_btn, self.close_btn):
            btn.setFixedSize(34, 26)
            btn.setObjectName("titleBarButton")
            btn_layout.addWidget(btn)
        
        self.close_btn.setObjectName("closeButton")
        
        self.minimize_btn.clicked.connect(self.parent.showMinimized)
        self.maximize_btn.clicked.connect(self.toggle_maximize)
        self.close_btn.clicked.connect(self.parent.close)
        
        button_widget = QWidget()
        button_widget.setObjectName("titleBarButtons")
        button_widget.setLayout(btn_layout)
        layout.addWidget(button_widget)
        
        self.pressing = False
        self.start_pos = None

    def setTitle(self, title):
        self.title.setText(title)
        
    def toggle_maximize(self):
        if self.parent.isMaximized():
            self.parent.showNormal()
            self.maximize_btn.setText("🗖")
        else:
            self.parent.showMaximized()
            self.maximize_btn.setText("🗗")
            
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.pressing = True
            self.start_pos = event.globalPosition().toPoint()
            
    def mouseMoveEvent(self, event):
        if self.pressing:
            if self.parent.isMaximized():
                self.parent.showNormal()
            delta = event.globalPosition().toPoint() - self.start_pos
            self.parent.move(self.parent.x() + delta.x(), self.parent.y() + delta.y())
            self.start_pos = event.globalPosition().toPoint()
            
    def mouseReleaseEvent(self, event):
        self.pressing = False

class _LegacyNotificationBanner(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("notificationBanner")
        self.setFixedWidth(420)
        self.setMinimumHeight(60)
        self.setVisible(False)

        # Vital for custom QWidget subclasses to render their QSS backgrounds
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 10, 10, 10)

        self.message_label = QLabel()
        self.message_label.setObjectName("notificationLabel")
        self.message_label.setWordWrap(True)
        self.message_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        layout.addWidget(self.message_label, stretch=1)

        self.copy_button = QPushButton("Copy")
        self.copy_button.setObjectName("notificationCopyButton")
        self.copy_button.setFixedHeight(24)
        self.copy_button.clicked.connect(self.copy_message)
        layout.addWidget(self.copy_button, alignment=Qt.AlignmentFlag.AlignTop)

        self.close_button = QPushButton("✕")
        self.close_button.setObjectName("notificationCloseButton")
        self.close_button.setFixedSize(24, 24)
        self.close_button.clicked.connect(self.hide_banner)
        layout.addWidget(self.close_button, alignment=Qt.AlignmentFlag.AlignTop)

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide_banner)

        self.animation = QPropertyAnimation(self, b"geometry", self)
        self.animation.finished.connect(self._handle_animation_finished)
        self._hide_after_animation = False
        
        self.margin_bottom = 20
        self.margin_right = 20

    def show_message(self, message, duration_ms=5000, msg_type="info"):
        main_window = self.window()
        if (
            main_window
            and hasattr(main_window, "should_show_notification")
            and not main_window.should_show_notification(msg_type)
        ):
            return

        accent_color = "#3498db"
        if msg_type == "error":
            accent_color = "#e74c3c"
            duration_ms = 0
        elif msg_type == "success":
            accent_color = "#2ecc71"
        elif msg_type == "warning":
            accent_color = "#e67e22"
            duration_ms = max(duration_ms, 10000)

        self.hide_timer.stop()
        self.animation.stop()
        self._hide_after_animation = False

        self.setStyleSheet(f"""
            QWidget#notificationBanner {{
                background-color: #2b2b2b;
                border: 1px solid #444444;
                border-left: 4px solid {accent_color};
                border-radius: 6px;
            }}
            QLabel#notificationLabel {{
                color: #e0e0e0;
                font-size: 13px;
                background-color: transparent;
            }}
            QPushButton#notificationCopyButton {{
                background-color: #343434;
                border: 1px solid #4b4b4b;
                color: #dcdcdc;
                font-size: 11px;
                padding: 2px 10px;
                border-radius: 4px;
            }}
            QPushButton#notificationCopyButton:hover {{
                background-color: #3f3f3f;
                color: #ffffff;
            }}
            QPushButton#notificationCloseButton {{
                background-color: transparent;
                border: none;
                color: #888888;
                font-size: 14px;
                border-radius: 4px;
            }}
            QPushButton#notificationCloseButton:hover {{
                background-color: #3f3f3f;
                color: #ffffff;
            }}
        """)

        self.message_label.setText(message)
        self.message_label.setToolTip(message)
        self.adjustSize()
        
        if self.parent():
            parent_rect = self.parent().rect()
            target_x = parent_rect.width() - self.width() - self.margin_right
            target_y = parent_rect.height() - self.height() - self.margin_bottom
            start_x = parent_rect.width() # Start off-screen to the right
            
            self.setGeometry(start_x, target_y, self.width(), self.height())
            self.setVisible(True)
            self.raise_()

            self.animation.setDuration(350)
            self.animation.setStartValue(QRect(start_x, target_y, self.width(), self.height()))
            self.animation.setEndValue(QRect(target_x, target_y, self.width(), self.height()))
            self.animation.setEasingCurve(QEasingCurve.Type.OutBack)
            self.animation.start()

        if duration_ms > 0:
            self.hide_timer.start(duration_ms)

    def copy_message(self):
        QApplication.clipboard().setText(self.message_label.text())

    def hide_banner(self):
        if not self.isVisible():
            return
             
        self.hide_timer.stop()
        self.animation.stop()
        self._hide_after_animation = True
        
        if self.parent():
            parent_rect = self.parent().rect()
            target_x = parent_rect.width() # Move off-screen to the right
            current_y = self.y()
            current_x = self.x()
            
            self.animation.setDuration(300)
            self.animation.setStartValue(QRect(current_x, current_y, self.width(), self.height()))
            self.animation.setEndValue(QRect(target_x, current_y, self.width(), self.height()))
            self.animation.setEasingCurve(QEasingCurve.Type.InBack)
            self.animation.start()
        else:
            self.setVisible(False)

    def _handle_animation_finished(self):
        if self._hide_after_animation:
            self._hide_after_animation = False
            self.setVisible(False)
             
    def update_position(self):
        if self.isVisible() and self.parent():
            parent_rect = self.parent().rect()
            target_x = parent_rect.width() - self.width() - self.margin_right
            target_y = parent_rect.height() - self.height() - self.margin_bottom
            self.move(target_x, target_y)

class NotificationBanner(QWidget):
    TYPE_STYLES = {
        "info": {
            "accent": "#4da3ff",
            "title": "Notice",
            "icon": "fa5s.info-circle",
            "icon_color": "#8fc2ff",
            "copy_hover": "#274d72",
            "dismiss_hover": "#394453",
            "close_hover": "rgba(77, 163, 255, 0.16)",
        },
        "success": {
            "accent": "#3ecf8e",
            "title": "Success",
            "icon": "fa5s.check-circle",
            "icon_color": "#7ce7b7",
            "copy_hover": "#24573f",
            "dismiss_hover": "#394453",
            "close_hover": "rgba(62, 207, 142, 0.16)",
        },
        "warning": {
            "accent": "#f0a63a",
            "title": "Warning",
            "icon": "fa5s.exclamation-triangle",
            "icon_color": "#ffc76f",
            "copy_hover": "#694723",
            "dismiss_hover": "#4c4235",
            "close_hover": "rgba(240, 166, 58, 0.16)",
        },
        "error": {
            "accent": "#ef5a5a",
            "title": "Action Needed",
            "icon": "fa5s.exclamation-circle",
            "icon_color": "#ff8c8c",
            "copy_hover": "#6d2d2d",
            "dismiss_hover": "#523838",
            "close_hover": "rgba(239, 90, 90, 0.16)",
        },
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("notificationBanner")
        self.setFixedWidth(460)
        self.setMinimumHeight(108)
        self.setVisible(False)

        # Vital for custom QWidget subclasses to render their QSS backgrounds.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

        self._default_copy_text = "Copy details"
        self._default_dismiss_text = "Dismiss"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 14, 14)
        layout.setSpacing(10)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        self.status_icon_label = QLabel()
        self.status_icon_label.setObjectName("notificationStatusIcon")
        self.status_icon_label.setFixedSize(18, 18)
        self.status_icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(self.status_icon_label, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.status_label = QLabel("Notice")
        self.status_label.setObjectName("notificationStatusLabel")
        header_layout.addWidget(self.status_label, alignment=Qt.AlignmentFlag.AlignVCenter)
        header_layout.addStretch()

        self.close_button = QPushButton()
        self.close_button.setObjectName("notificationCloseButton")
        self.close_button.setFixedSize(28, 28)
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setToolTip("Dismiss notification")
        self.close_button.clicked.connect(self.hide_banner)
        header_layout.addWidget(self.close_button, alignment=Qt.AlignmentFlag.AlignTop)

        layout.addLayout(header_layout)

        self.message_label = QLabel()
        self.message_label.setObjectName("notificationLabel")
        self.message_label.setWordWrap(True)
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.message_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        layout.addWidget(self.message_label)

        footer_layout = QHBoxLayout()
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(8)
        footer_layout.addStretch()

        self.dismiss_button = QPushButton(self._default_dismiss_text)
        self.dismiss_button.setObjectName("notificationDismissButton")
        self.dismiss_button.setFixedHeight(30)
        self.dismiss_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dismiss_button.clicked.connect(self.hide_banner)
        footer_layout.addWidget(self.dismiss_button)

        self.copy_button = QPushButton(self._default_copy_text)
        self.copy_button.setObjectName("notificationCopyButton")
        self.copy_button.setFixedHeight(30)
        self.copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_button.clicked.connect(self.copy_message)
        footer_layout.addWidget(self.copy_button)

        layout.addLayout(footer_layout)

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide_banner)

        self.copy_feedback_timer = QTimer(self)
        self.copy_feedback_timer.setSingleShot(True)
        self.copy_feedback_timer.timeout.connect(self._reset_copy_feedback)

        self.animation = QPropertyAnimation(self, b"geometry", self)
        self.animation.finished.connect(self._handle_animation_finished)
        self._hide_after_animation = False

        self.margin_bottom = 20
        self.margin_right = 20

    def show_message(self, message, duration_ms=5000, msg_type="info"):
        main_window = self.window()
        if (
            main_window
            and hasattr(main_window, "should_show_notification")
            and not main_window.should_show_notification(msg_type)
        ):
            return

        style = self.TYPE_STYLES.get(msg_type, self.TYPE_STYLES["info"])
        accent_color = style["accent"]
        if msg_type == "error":
            duration_ms = 0
        elif msg_type == "warning":
            duration_ms = max(duration_ms, 10000)

        self.hide_timer.stop()
        self.copy_feedback_timer.stop()
        self.animation.stop()
        self._hide_after_animation = False

        self.setStyleSheet(f"""
            QWidget#notificationBanner {{
                background-color: #171b22;
                border: 1px solid #313946;
                border-left: 4px solid {accent_color};
                border-radius: 12px;
            }}
            QLabel#notificationStatusLabel {{
                color: #f3f6fb;
                font-size: 12px;
                font-weight: 700;
                background-color: transparent;
            }}
            QLabel#notificationLabel {{
                color: #d4dbe6;
                font-size: 13px;
                background-color: transparent;
            }}
            QLabel#notificationStatusIcon {{
                background-color: transparent;
            }}
            QPushButton#notificationDismissButton,
            QPushButton#notificationCopyButton {{
                background-color: #212733;
                border: 1px solid #3a4352;
                color: #e8edf7;
                font-size: 11px;
                font-weight: 600;
                padding: 0 12px;
                border-radius: 7px;
            }}
            QPushButton#notificationCopyButton:hover {{
                background-color: {style["copy_hover"]};
                color: #ffffff;
            }}
            QPushButton#notificationDismissButton:hover {{
                background-color: {style["dismiss_hover"]};
                color: #ffffff;
            }}
            QPushButton#notificationCloseButton {{
                background-color: transparent;
                border: none;
                border-radius: 8px;
            }}
            QPushButton#notificationCloseButton:hover {{
                background-color: {style["close_hover"]};
            }}
        """)

        self.status_label.setText(style["title"])
        self.status_icon_label.setPixmap(qta.icon(style["icon"], color=style["icon_color"]).pixmap(16, 16))
        self.close_button.setIcon(qta.icon("fa5s.times", color="#cfd6e4"))
        self.copy_button.setIcon(qta.icon("fa5s.copy", color="#e8edf7"))
        self.message_label.setText(message)
        self.message_label.setToolTip(message)
        self._reset_copy_feedback()
        self.adjustSize()

        if self.parent():
            parent_rect = self.parent().rect()
            target_x = parent_rect.width() - self.width() - self.margin_right
            target_y = parent_rect.height() - self.height() - self.margin_bottom
            start_x = parent_rect.width()

            self.setGeometry(start_x, target_y, self.width(), self.height())
            self.setVisible(True)
            self.raise_()

            self.animation.setDuration(350)
            self.animation.setStartValue(QRect(start_x, target_y, self.width(), self.height()))
            self.animation.setEndValue(QRect(target_x, target_y, self.width(), self.height()))
            self.animation.setEasingCurve(QEasingCurve.Type.OutBack)
            self.animation.start()

        if duration_ms > 0:
            self.hide_timer.start(duration_ms)

    def copy_message(self):
        QApplication.clipboard().setText(self.message_label.text())
        self.copy_button.setText("Copied")
        self.copy_feedback_timer.start(1600)

    def _reset_copy_feedback(self):
        self.copy_button.setText(self._default_copy_text)

    def hide_banner(self):
        if not self.isVisible():
            return

        self.hide_timer.stop()
        self.copy_feedback_timer.stop()
        self.animation.stop()
        self._hide_after_animation = True

        if self.parent():
            parent_rect = self.parent().rect()
            target_x = parent_rect.width()
            current_y = self.y()
            current_x = self.x()

            self.animation.setDuration(300)
            self.animation.setStartValue(QRect(current_x, current_y, self.width(), self.height()))
            self.animation.setEndValue(QRect(target_x, current_y, self.width(), self.height()))
            self.animation.setEasingCurve(QEasingCurve.Type.InBack)
            self.animation.start()
        else:
            self.setVisible(False)

    def _handle_animation_finished(self):
        if self._hide_after_animation:
            self._hide_after_animation = False
            self.setVisible(False)

    def update_position(self):
        if self.isVisible() and self.parent():
            parent_rect = self.parent().rect()
            target_x = parent_rect.width() - self.width() - self.margin_right
            target_y = parent_rect.height() - self.height() - self.margin_bottom
            self.move(target_x, target_y)

class DocumentViewerPanel(QWidget):
    close_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(500)
        self.setObjectName("documentViewerPanel")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        header_widget = QWidget()
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        icon_label = QLabel()
        icon_label.setPixmap(qta.icon('fa5s.book-open', color='white').pixmap(16, 16))
        header_layout.addWidget(icon_label)

        title_label = QLabel("Document View")
        title_label.setStyleSheet("color: white; font-weight: bold; font-size: 14px;")
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        close_button = QPushButton(qta.icon('fa5s.times', color='white'), "")
        close_button.setFixedSize(24, 24)
        close_button.setToolTip("Close Panel")
        close_button.clicked.connect(self.close_requested.emit)
        header_layout.addWidget(close_button)
        main_layout.addWidget(header_widget)

        self.content_viewer = QTextEdit()
        self.content_viewer.setReadOnly(True)
        main_layout.addWidget(self.content_viewer)

        self.on_theme_changed()

    def set_document_content(self, markdown_text):
        html = markdown.markdown(markdown_text, extensions=['fenced_code', 'tables'])
        self.content_viewer.setHtml(html)

    def on_theme_changed(self):
        palette = get_current_palette()
        self.setStyleSheet(f"""
            QWidget#documentViewerPanel {{
                background-color: #252526;
                border-right: 1px solid #3f3f3f;
            }}
            QTextEdit {{
                background-color: #2d2d2d;
                border: 1px solid #3f3f3f;
                color: #e0e0e0;
                font-size: 13px;
                padding: 8px;
            }}
            QPushButton {{
                background-color: transparent;
                border: none;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: #3f3f3f;
            }}
        """)
