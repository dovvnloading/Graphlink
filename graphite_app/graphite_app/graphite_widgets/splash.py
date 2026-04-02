"""Animated splash screen widgets."""

from PySide6.QtCore import QPointF, Property, QEasingCurve, QParallelAnimationGroup, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QLabel, QVBoxLayout, QWidget
from graphite_config import get_current_palette
from .loading_visuals import paint_orbital_loading_spinner

class SplashAnimationWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(80, 80)
        self._angle1 = 0.0
        self._angle2 = 0.0
        self._angle3 = 0.0

        self.anim1 = QPropertyAnimation(self, b'angle1')
        self.anim1.setStartValue(0)
        self.anim1.setEndValue(360)
        self.anim1.setDuration(1200)
        self.anim1.setEasingCurve(QEasingCurve.Type.InOutCubic)

        self.anim2 = QPropertyAnimation(self, b'angle2')
        self.anim2.setStartValue(70)
        self.anim2.setEndValue(430)
        self.anim2.setDuration(1000)
        self.anim2.setEasingCurve(QEasingCurve.Type.InOutSine)

        self.anim3 = QPropertyAnimation(self, b'angle3')
        self.anim3.setStartValue(140)
        self.anim3.setEndValue(500)
        self.anim3.setDuration(1500)
        self.anim3.setEasingCurve(QEasingCurve.Type.InOutQuad)

        self.anim_group = QParallelAnimationGroup()
        self.anim_group.addAnimation(self.anim1)
        self.anim_group.addAnimation(self.anim2)
        self.anim_group.addAnimation(self.anim3)
        self.anim_group.setLoopCount(-1)
        self.anim_group.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        paint_orbital_loading_spinner(
            painter,
            self.rect(),
            self._angle1,
            self._angle2,
            self._angle3,
        )
    
    @Property(float)
    def angle1(self): return self._angle1
    @angle1.setter
    def angle1(self, value):
        self._angle1 = value
        self.update()

    @Property(float)
    def angle2(self): return self._angle2
    @angle2.setter
    def angle2(self, value):
        self._angle2 = value
        self.update()

    @Property(float)
    def angle3(self): return self._angle3
    @angle3.setter
    def angle3(self, value):
        self._angle3 = value
        self.update()

class AnimatedWordLogo(QWidget):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.text = text
        self.setFixedSize(360, 80)
        self._progress = 0.0

        self.animation = QPropertyAnimation(self, b"progress")
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setDuration(2200)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.start()

    @Property(float)
    def progress(self):
        return self._progress

    @progress.setter
    def progress(self, value):
        self._progress = value
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        font = QFont("Segoe UI", 34, QFont.Weight.Bold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2)
        painter.setFont(font)
        metrics = QFontMetrics(font)

        total_width = metrics.horizontalAdvance(self.text)
        start_x = (self.width() - total_width) / 2
        base_y = self.height() / 2 + metrics.ascent() / 2 - 10

        char_delay = 0.5 / len(self.text)
        palette = get_current_palette()
        
        curve = QEasingCurve(QEasingCurve.Type.OutBack)
        curve.setOvershoot(1.5)

        for i, char in enumerate(self.text):
            char_start = i * char_delay
            char_end = char_start + 0.5
            
            if self._progress <= char_start:
                local_p = 0.0
            elif self._progress >= char_end:
                local_p = 1.0
            else:
                local_p = (self._progress - char_start) / 0.5
            
            ease_p = curve.valueForProgress(local_p)
            
            opacity = max(0, min(255, int(local_p * 255))) 
            
            y_offset = (1.0 - ease_p) * 25.0
            
            if i < 5: 
                color = QColor("#ffffff")
            else:
                color = QColor(palette.SELECTION)
                
            color.setAlpha(opacity)
            painter.setPen(color)
            painter.drawText(QPointF(start_x, base_y + y_offset), char)
            
            start_x += metrics.horizontalAdvance(char)

class SplashScreen(QWidget):
    def __init__(self, main_window, welcome_screen, show_welcome=True):
        super().__init__()
        self.main_window = main_window
        self.welcome_screen = welcome_screen
        self.show_welcome = show_welcome
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(400, 300)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)

        self.container = QWidget(self)
        self.container.setObjectName("splashContainer")
        main_layout.addWidget(self.container)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 190))
        shadow.setOffset(0, 2)
        self.container.setGraphicsEffect(shadow)
        
        content_layout = QVBoxLayout(self.container)
        content_layout.setSpacing(15)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.logo_widget = AnimatedWordLogo("Graphlink")
        content_layout.addWidget(self.logo_widget, alignment=Qt.AlignmentFlag.AlignCenter)
        
        self.animation_widget = SplashAnimationWidget()
        content_layout.addWidget(self.animation_widget, alignment=Qt.AlignmentFlag.AlignCenter)

        self.status_label = QLabel("Version Beta-0.5.3 | Â© 2026 All Rights Reserved.")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content_layout.addWidget(self.status_label)

        self.setStyleSheet("""
            QWidget#splashContainer {
                background-color: #1e1e1e;
                border-radius: 8px;
            }
            QLabel {
                color: #717573;
            }
        """)
        
        screen = QGuiApplication.primaryScreen().geometry()
        self.move(int((screen.width() - self.width()) / 2), int((screen.height() - self.height()) / 2))
        
        QTimer.singleShot(3500, self.close_splash)

    def close_splash(self):
        self.main_window.show()
        if self.show_welcome:
            self.welcome_screen.show()
        self.close()

