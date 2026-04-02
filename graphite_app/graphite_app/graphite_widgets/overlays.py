"""Search and loading overlay widgets."""

import qtawesome as qta
from PySide6.QtCore import Property, QEasingCurve, QParallelAnimationGroup, QPropertyAnimation, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtWidgets import QGraphicsObject, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget
from graphite_config import get_current_palette
from .loading_visuals import paint_orbital_loading_spinner

class LoadingAnimation(QGraphicsObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setZValue(100) 
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

    def boundingRect(self):
        return QRectF(-24, -24, 48, 48)

    def paint(self, painter, option, widget):
        paint_orbital_loading_spinner(
            painter,
            self.boundingRect(),
            self._angle1,
            self._angle2,
            self._angle3,
        )

    def start(self):
        self.anim_group.start()

    def stop(self):
        self.anim_group.stop()

    @Property(float)
    def angle1(self):
        return self._angle1

    @angle1.setter
    def angle1(self, value):
        self._angle1 = value
        self.update()

    @Property(float)
    def angle2(self):
        return self._angle2

    @angle2.setter
    def angle2(self, value):
        self._angle2 = value
        self.update()

    @Property(float)
    def angle3(self):
        return self._angle3

    @angle3.setter
    def angle3(self, value):
        self._angle3 = value
        self.update()


class GhostNodePreview(QGraphicsObject):
    """A transient placeholder that reserves and visualizes a pending node spawn."""

    def __init__(self, width=420, height=128, title="Generating response", subtitle="Node reserved here", parent=None):
        super().__init__(parent)
        self.width = max(220.0, float(width))
        self.height = max(88.0, float(height))
        self.title = title
        self.subtitle = subtitle
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(False)
        self.setZValue(90)

    def boundingRect(self):
        return QRectF(-8, -8, self.width + 16, self.height + 16)

    def paint(self, painter, option, widget):
        palette = get_current_palette()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        body_rect = QRectF(0, 0, self.width, self.height)
        outline = QColor(palette.SELECTION)
        outline.setAlpha(170)
        fill = QColor(palette.SELECTION)
        fill.setAlpha(28)
        glow = QColor(palette.SELECTION)
        glow.setAlpha(54)

        shadow_path = QPainterPath()
        shadow_path.addRoundedRect(body_rect.adjusted(4, 5, 4, 5), 12, 12)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 36))
        painter.drawPath(shadow_path)

        node_path = QPainterPath()
        node_path.addRoundedRect(body_rect, 12, 12)
        painter.setBrush(fill)
        painter.setPen(QPen(outline, 1.6, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap))
        painter.drawPath(node_path)

        painter.save()
        painter.setClipPath(node_path)
        painter.fillRect(QRectF(body_rect.left(), body_rect.top(), 5, body_rect.height()), glow)
        painter.restore()

        spinner_rect = QRectF(18, (self.height - 26) * 0.5, 26, 26)
        paint_orbital_loading_spinner(painter, spinner_rect, 42.0, 170.0, 264.0)

        title_font = QFont("Segoe UI", 10, QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor("#eef6ff"))
        painter.drawText(
            QRectF(58, 24, self.width - 76, 22),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.title,
        )

        subtitle_font = QFont("Segoe UI", 8)
        painter.setFont(subtitle_font)
        painter.setPen(QColor("#aab8c8"))
        painter.drawText(
            QRectF(58, 50, self.width - 76, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.subtitle,
        )

        painter.restore()

class SearchOverlay(QWidget):
    textChanged = Signal(str)
    findNext = Signal()
    findPrevious = Signal()
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(300)
        self.setObjectName("searchOverlay")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Find...")
        self.search_input.textChanged.connect(self.textChanged.emit)
        self.search_input.returnPressed.connect(self.findNext.emit)
        layout.addWidget(self.search_input)

        self.results_label = QLabel("0 / 0")
        layout.addWidget(self.results_label)

        prev_btn = QPushButton(qta.icon('fa5s.chevron-up', color='white'), "")
        prev_btn.setFixedSize(24, 24)
        prev_btn.setToolTip("Previous match (Shift+Enter)")
        prev_btn.clicked.connect(self.findPrevious.emit)
        layout.addWidget(prev_btn)
        
        QShortcut(QKeySequence("Shift+Return"), self.search_input, self.findPrevious.emit)
        QShortcut(QKeySequence("Shift+Enter"), self.search_input, self.findPrevious.emit)

        next_btn = QPushButton(qta.icon('fa5s.chevron-down', color='white'), "")
        next_btn.setFixedSize(24, 24)
        next_btn.setToolTip("Next match (Enter)")
        next_btn.clicked.connect(self.findNext.emit)
        layout.addWidget(next_btn)
        
        close_btn = QPushButton(qta.icon('fa5s.times', color='white'), "")
        close_btn.setFixedSize(24, 24)
        close_btn.setToolTip("Close (Esc)")
        close_btn.clicked.connect(self.closed.emit)
        layout.addWidget(close_btn)
        
        QShortcut(QKeySequence("Esc"), self, self.closed.emit)

        self.setStyleSheet("""
            QWidget#searchOverlay {
                background-color: #2d2d2d;
                border: 1px solid #3f3f3f;
                border-radius: 5px;
            }
            QLabel { color: #ccc; background-color: transparent; }
            QLineEdit {
                border: 1px solid #555;
                background-color: #3f3f3f;
                border-radius: 3px;
                padding: 4px;
            }
            QPushButton {
                background-color: transparent;
                border: none;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #555;
            }
        """)

    def update_results_label(self, current, total):
        self.results_label.setText(f"{current} / {total}")
        if total > 0 and current > 0:
            self.results_label.setStyleSheet("color: #fff;")
        elif total > 0 and current == 0:
             self.results_label.setStyleSheet("color: #ccc;")
        else:
            self.results_label.setStyleSheet("color: #e74c3c;")

    def focus_input(self):
        self.search_input.setFocus()
        self.search_input.selectAll()


