"""Search and loading overlay widgets."""

import qtawesome as qta
from PySide6.QtCore import Property, QEasingCurve, QParallelAnimationGroup, QPropertyAnimation, QRectF, Qt, Signal
from PySide6.QtGui import QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import QGraphicsObject, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget
from graphite_config import get_current_palette

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
        return QRectF(-20, -20, 40, 40)

    def paint(self, painter, option, widget):
        palette = get_current_palette()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        pen1 = QPen(palette.USER_NODE, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen1)
        painter.drawArc(self.boundingRect().toRect(), int(self._angle1 * 16), 120 * 16)
        
        pen2 = QPen(palette.AI_NODE, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen2)
        painter.drawArc(self.boundingRect().adjusted(5, 5, -5, -5).toRect(), int(self._angle2 * 16), 120 * 16)

        pen3 = QPen(palette.NAV_HIGHLIGHT.darker(120), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen3)
        painter.drawArc(self.boundingRect().adjusted(10, 10, -10, -10).toRect(), int(self._angle3 * 16), 120 * 16)

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


