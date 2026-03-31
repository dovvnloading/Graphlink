import math
import re

import qtawesome as qta
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect, QWidget, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QFrame, QGridLayout,
    QSizePolicy, QScrollArea, QSlider, QLineEdit, QGraphicsObject, QCheckBox, QMenu, QComboBox, QMainWindow,
    QGraphicsItem
)
from PySide6.QtCore import Qt, Signal, QTimer, QPointF, Property, QParallelAnimationGroup, QPropertyAnimation, QEasingCurve, QRectF, QSize, QRect, QPoint
from PySide6.QtGui import QAction, QPixmap, QIcon, QPainter, QColor, QPainterPath, QBrush, QLinearGradient, QPen, QShortcut, QKeySequence, QFont, QGuiApplication, QFontMetrics
from graphite_config import get_current_palette

try:
    from spellchecker import SpellChecker
    SPELLCHECK_AVAILABLE = True
except ImportError:
    SPELLCHECK_AVAILABLE = False

try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False


class CustomTooltip(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("""
            QLabel {
                background-color: rgba(30, 30, 30, 0.9);
                color: #e0e0e0;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 6px;
                font-size: 11px;
            }
        """)

class TokenEstimator:
    _instance = None
    _encoding = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TokenEstimator, cls).__new__(cls)
            if TIKTOKEN_AVAILABLE:
                try:
                    cls._encoding = tiktoken.get_encoding("cl100k_base")
                except Exception:
                    cls._encoding = None
                    print("Warning: tiktoken installed, but failed to get encoding. Falling back to character count.")
        return cls._instance

    def count_tokens(self, text: str) -> int:
        if not text:
            return 0
        if self._encoding:
            return len(self._encoding.encode(text))
        else:
            return len(text) // 4

class TokenCounterWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("tokenCounterWidget")
        self.setFixedWidth(150)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)

        self.container = QWidget(self)
        self.container.setObjectName("tokenCounterContainer")
        main_layout.addWidget(self.container)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(15)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 1)
        self.container.setGraphicsEffect(shadow)

        grid_layout = QGridLayout(self.container)
        grid_layout.setContentsMargins(8, 6, 8, 6)
        grid_layout.setSpacing(4)

        grid_layout.addWidget(QLabel("Input:"), 0, 0)
        grid_layout.addWidget(QLabel("Output:"), 1, 0)
        grid_layout.addWidget(QLabel("Context:"), 2, 0)
        grid_layout.addWidget(QLabel("Total:"), 3, 0)

        self.input_label = QLabel("0")
        self.output_label = QLabel("0")
        self.context_label = QLabel("0")
        self.total_label = QLabel("0")

        for label in [self.input_label, self.output_label, self.context_label, self.total_label]:
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        grid_layout.addWidget(self.input_label, 0, 1)
        grid_layout.addWidget(self.output_label, 1, 1)
        grid_layout.addWidget(self.context_label, 2, 1)
        grid_layout.addWidget(self.total_label, 3, 1)

        self.setStyleSheet("""
            QWidget#tokenCounterWidget {
                background-color: transparent;
            }
            QWidget#tokenCounterContainer {
                background-color: rgba(45, 45, 45, 0.7);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 6px;
            }
            QLabel {
                background-color: transparent;
                color: #cccccc;
                font-size: 10px;
            }
        """)

    def update_counts(self, input_tokens=None, output_tokens=None, context_tokens=None, total_tokens=None):
        if input_tokens is not None:
            self.input_label.setText(f"{input_tokens:,}")
        if output_tokens is not None:
            self.output_label.setText(f"{output_tokens:,}")
        if context_tokens is not None:
            self.context_label.setText(f"{context_tokens:,}")
        if total_tokens is not None:
            self.total_label.setText(f"{total_tokens:,}")
    
    def reset(self):
        self.update_counts(0, 0, 0, 0)

class NavigationPin:
    def __init__(self):
        self.title = "Dummy Pin"
        self.note = ""
        self.scene = lambda: None 

class CustomScrollBar(QWidget):
    valueChanged = Signal(float)
    
    def __init__(self, orientation=Qt.Orientation.Vertical, parent=None):
        super().__init__(parent)
        self.orientation = orientation
        self.value = 0
        self.handle_position = 0
        self.handle_pressed = False
        self.hover = False
        
        self.min_val = 0
        self.max_val = 99
        self.page_step = 10
        
        if orientation == Qt.Orientation.Vertical:
            self.setFixedWidth(8)
        else:
            self.setFixedHeight(8)
            
        self.setMouseTracking(True)
        
    def setRange(self, min_val, max_val):
        self.min_val = min_val
        self.max_val = max(min_val + 0.1, max_val) 
        self.value = max(min_val, min(self.value, max_val))
        self.update()
        
    def setValue(self, value):
        old_value = self.value
        self.value = max(self.min_val, min(self.max_val, value))
        if self.value != old_value:
            self.valueChanged.emit(self.value)
            self.update()
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        track_color = QColor("#2A2A2A")
        track_color.setAlpha(100)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        
        if self.orientation == Qt.Orientation.Vertical:
            painter.drawRoundedRect(1, 0, self.width() - 2, self.height(), 4, 4)
        else:
            painter.drawRoundedRect(0, 1, self.width(), self.height() - 2, 4, 4)
            
        range_size = self.max_val - self.min_val
        if range_size <= 0:
            return
            
        visible_ratio = min(1.0, self.page_step / (range_size + self.page_step))
        
        if self.orientation == Qt.Orientation.Vertical:
            handle_size = max(20, int(self.height() * visible_ratio))
            available_space = max(0, self.height() - handle_size)
            if range_size > 0:
                handle_position = int(available_space * 
                    ((self.value - self.min_val) / range_size))
            else:
                handle_position = 0
        else:
            handle_size = max(20, int(self.width() * visible_ratio))
            available_space = max(0, self.width() - handle_size)
            if range_size > 0:
                handle_position = int(available_space * 
                    ((self.value - self.min_val) / range_size))
            else:
                handle_position = 0
            
        handle_color = QColor("#6a6a6a") if self.hover else QColor("#555555")
        painter.setBrush(handle_color)
        
        if self.orientation == Qt.Orientation.Vertical:
            painter.drawRoundedRect(1, handle_position, self.width() - 2, handle_size, 3, 3)
        else:
            painter.drawRoundedRect(handle_position, 1, handle_size, self.height() - 2, 3, 3)
            
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.handle_pressed = True
            self.mouse_start_pos = event.position().toPoint()
            self.start_value = self.value
            
    def mouseMoveEvent(self, event):
        self.hover = True
        self.update()
        
        if self.handle_pressed:
            if self.orientation == Qt.Orientation.Vertical:
                delta = event.position().toPoint().y() - self.mouse_start_pos.y()
                available_space = max(1, self.height() - 20)
                delta_ratio = delta / available_space
            else:
                delta = event.position().toPoint().x() - self.mouse_start_pos.x()
                available_space = max(1, self.width() - 20)
                delta_ratio = delta / available_space
                
            range_size = self.max_val - self.min_val
            new_value = self.start_value + delta_ratio * range_size
            self.setValue(new_value)
            
    def mouseReleaseEvent(self, event):
        self.handle_pressed = False
        
    def enterEvent(self, event):
        self.hover = True
        self.update()
        
    def leaveEvent(self, event):
        self.hover = False
        self.update()

class CustomScrollArea(QWidget):
    def __init__(self, widget):
        super().__init__()
        self.widget = widget
        
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        self.viewport = QWidget()
        self.viewport.setLayout(QVBoxLayout())
        self.viewport.layout().setContentsMargins(0, 0, 0, 0)
        self.viewport.layout().addWidget(widget)
        
        self.v_scrollbar = CustomScrollBar(Qt.Orientation.Vertical)
        self.h_scrollbar = CustomScrollBar(Qt.Orientation.Horizontal)
        
        layout.addWidget(self.viewport, 0, 0)
        layout.addWidget(self.v_scrollbar, 0, 1)
        layout.addWidget(self.h_scrollbar, 1, 0)
        
        self.v_scrollbar.valueChanged.connect(self.updateVerticalScroll)
        self.h_scrollbar.valueChanged.connect(self.updateHorizontalScroll)
        
    def updateScrollbars(self):
        content_height = self.widget.height()
        viewport_height = self.viewport.height()
        
        if content_height > viewport_height:
            self.v_scrollbar.setRange(0, content_height - viewport_height)
            self.v_scrollbar.page_step = viewport_height
            self.v_scrollbar.show()
        else:
            self.v_scrollbar.hide()
            
        content_width = self.widget.width()
        viewport_width = self.viewport.width()
        
        if content_width > viewport_width:
            self.h_scrollbar.setRange(0, content_width - viewport_width)
            self.h_scrollbar.page_step = viewport_width
            self.h_scrollbar.show()
        else:
            self.h_scrollbar.hide()
            
    def updateVerticalScroll(self, value):
        self.viewport.move(self.viewport.x(), -int(value))
        
    def updateHorizontalScroll(self, value):
        self.viewport.move(-int(value), self.viewport.y())
        
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.updateScrollbars()

class ScrollHandle(QGraphicsObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.width = 6
        self.min_height = 20
        self.height = self.min_height
        self.hover = False
        self.dragging = False
        self.start_drag_pos = None
        self.start_drag_value = 0
        self.setAcceptHoverEvents(True)
        
    def boundingRect(self):
        return QRectF(0, 0, self.width, self.height)
        
    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    
        color = QColor("#6a6a6a") if self.hover else QColor("#555555")
        
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))
    
        painter.drawRoundedRect(0, 0, int(self.width), int(self.height), 3.0, 3.0)
        
    def hoverEnterEvent(self, event):
        self.hover = True
        self.update()
        super().hoverEnterEvent(event)
        
    def hoverLeaveEvent(self, event):
        self.hover = False
        self.update()
        super().hoverLeaveEvent(event)

class ScrollBar(QGraphicsObject):
    valueChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.width = 8
        self.height = 0
        self.value = 0 
        self.handle = ScrollHandle(self)
        self.update_handle_position()
        
    def boundingRect(self):
        return QRectF(0, 0, self.width, self.height)
        
    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        track_color = QColor("#2A2A2A")
        track_color.setAlpha(100)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(track_color))
        painter.drawRoundedRect(1, 0, self.width - 2, self.height, 4, 4)
        
    def set_range(self, visible_ratio):
        self.handle.height = max(self.handle.min_height, 
                               self.height * visible_ratio)
        self.update_handle_position()
        
    def set_value(self, value):
        new_value = max(0, min(1, value))
        if self.value != new_value:
            self.value = new_value
            self.valueChanged.emit(self.value)
            self.update_handle_position()
        
    def update_handle_position(self):
        max_y = self.height - self.handle.height
        self.handle.setPos(1, self.value * max_y)
        
    def mousePressEvent(self, event):
        handle_pos = self.handle.pos().y()
        click_pos = event.pos().y()
        
        if handle_pos <= click_pos <= handle_pos + self.handle.height:
            self.handle.dragging = True
            self.handle.start_drag_pos = click_pos
            self.handle.start_drag_value = self.value
        else:
            click_ratio = click_pos / self.height
            self.set_value(click_ratio)
                
    def mouseMoveEvent(self, event):
        if self.handle.dragging:
            delta = event.pos().y() - self.handle.start_drag_pos
            available_space = self.height - self.handle.height
            if available_space > 0:
                delta_ratio = delta / available_space
                new_value = self.handle.start_drag_value + delta_ratio
                self.set_value(new_value)
                
    def mouseReleaseEvent(self, event):
        self.handle.dragging = False
        self.handle.start_drag_pos = None

class SpellCheckLineEdit(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        if not SPELLCHECK_AVAILABLE:
            return

        self.spell = SpellChecker()
        self.misspelled_words = set()
        self.error_spans = []

        self.textChanged.connect(self._check_spelling)

    def _check_spelling(self, text):
        self.misspelled_words.clear()
        self.error_spans.clear()
        
        words = re.finditer(r'\b\w+\b', text)
        for match in words:
            word = match.group(0)
            if self.spell.unknown([word]):
                self.misspelled_words.add(word)
                self.error_spans.append((match.start(), match.end()))
        
        self.update() 

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.error_spans:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        pen = QPen(Qt.red)
        pen.setCosmetic(True)
        painter.setPen(pen)

        fm = self.fontMetrics()
        text = self.text()
        
        from PySide6.QtWidgets import QStyle, QStyleOptionFrame
        opt = QStyleOptionFrame()
        self.initStyleOption(opt)
        contents = self.style().subElementRect(QStyle.SubElement.SE_LineEditContents, opt)
        left_m, top_m, right_m, bottom_m = self.getTextMargins()
        text_rect = contents.adjusted(left_m, top_m, -right_m, -bottom_m)
        
        vpad = max(0, (text_rect.height() - fm.height()) // 2)
        cur_idx = self.cursorPosition()
        cur_left = self.cursorRect().left()
        x_offset = cur_left - fm.horizontalAdvance(text[:cur_idx])
        
        baseline_y = (
            text_rect.top()
            + vpad
            + fm.ascent()
            + max(2, int(fm.descent() * 0.95))
        )

        wave_len = 4
        wave_amp = 1.5
        clip_left, clip_right = text_rect.left(), text_rect.right()

        for start, end in self.error_spans:
            sx = text_rect.left() + fm.horizontalAdvance(text[:start]) + x_offset
            ex = text_rect.left() + fm.horizontalAdvance(text[:end]) + x_offset

            if ex < clip_left or sx > clip_right:
                continue
            sx = max(sx, clip_left)
            ex = min(ex, clip_right)

            path = QPainterPath()
            x = sx
            path.moveTo(x, baseline_y)
            while x < ex:
                mid = x + wave_len / 2.0
                nx = min(x + wave_len, ex)
                path.quadTo(mid, baseline_y + wave_amp, nx, baseline_y)
                x = nx

            painter.strokePath(path, pen)

    def getStyleOption(self):
        from PySide6.QtWidgets import QStyleOptionFrame
        opt = QStyleOptionFrame()
        self.initStyleOption(opt)
        return opt

    def contextMenuEvent(self, event):
        if not SPELLCHECK_AVAILABLE:
            super().contextMenuEvent(event)
            return

        menu = self.createStandardContextMenu()
        
        char_index = self.cursorPositionAt(event.pos())
        
        word_span = None
        clicked_word = ""
        for start, end in self.error_spans:
            if start <= char_index < end:
                word_span = (start, end)
                clicked_word = self.text()[start:end]
                break

        if clicked_word:
            suggestions = self.spell.candidates(clicked_word)
            if suggestions:
                menu.addSeparator()
                for suggestion in sorted(list(suggestions))[:5]:
                    action = QAction(suggestion, self)
                    action.triggered.connect(lambda checked=False, s=suggestion, ws=word_span: self._replace_word(s, ws[0], ws[1]))
                    menu.addAction(action)

        menu.exec(event.globalPos())

    def _replace_word(self, suggestion, start, end):
        current_text = self.text()
        new_text = current_text[:start] + suggestion + current_text[end:]
        self.setText(new_text)

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

class PinOverlay(QFrame):
    closed = Signal()
    BASE_WIDTH = 360

    def __init__(self, canvas_view, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.window = canvas_view
        self.canvas_view = canvas_view
        self.pins = []
        self.setObjectName("pinFlyoutPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(self.BASE_WIDTH, 280)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 10)
        shadow.setColor(Qt.GlobalColor.black)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(12, 12, 12, 14)
        outer_layout.setSpacing(0)

        self.container = QFrame()
        self.container.setObjectName("pinFlyoutShell")
        self.container.setGraphicsEffect(shadow)
        outer_layout.addWidget(self.container)

        main_layout = QVBoxLayout(self.container)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        header_widget = QWidget()
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)

        self.icon_badge = QLabel()
        self.icon_badge.setObjectName("pinFlyoutBadge")
        self.icon_badge.setFixedSize(28, 28)
        self.icon_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(self.icon_badge, 0, Qt.AlignmentFlag.AlignTop)

        header_text_column = QVBoxLayout()
        header_text_column.setContentsMargins(0, 0, 0, 0)
        header_text_column.setSpacing(2)

        self.header_text = QLabel("Navigation Pins")
        self.header_text.setObjectName("pinFlyoutTitle")
        header_text_column.addWidget(self.header_text)

        self.header_body = QLabel("Quick-jump bookmarks for important spots on the canvas.")
        self.header_body.setObjectName("pinFlyoutMeta")
        self.header_body.setWordWrap(True)
        header_text_column.addWidget(self.header_body)
        header_layout.addLayout(header_text_column, 1)

        self.close_btn = QPushButton("Close")
        self.close_btn.setObjectName("pinFlyoutCloseButton")
        self.close_btn.clicked.connect(self.close)
        header_layout.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignTop)

        main_layout.addWidget(header_widget)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("pinScrollArea")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.pin_list = QWidget()
        self.pin_list.setObjectName("pinScrollContent")
        self.pin_layout = QVBoxLayout(self.pin_list)
        self.pin_layout.setSpacing(6)
        self.pin_layout.setContentsMargins(0, 0, 0, 0)
        self.pin_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.scroll.setWidget(self.pin_list)
        main_layout.addWidget(self.scroll, 1)

        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(0, 0, 0, 0)
        footer_row.setSpacing(8)

        self.pin_count_label = QLabel("")
        self.pin_count_label.setObjectName("pinFlyoutCount")
        footer_row.addWidget(self.pin_count_label, 1, Qt.AlignmentFlag.AlignVCenter)

        self.add_btn = QPushButton("Drop New Pin")
        self.add_btn.setObjectName("pinAddButton")
        self.add_btn.setIcon(qta.icon('fa5s.map-pin', color='white'))
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.clicked.connect(self.create_pin)
        footer_row.addWidget(self.add_btn, 0, Qt.AlignmentFlag.AlignRight)

        main_layout.addLayout(footer_row)

        self.on_theme_changed()

    def on_theme_changed(self):
        palette = get_current_palette()
        accent = palette.SELECTION.name()
        accent_color = QColor(palette.SELECTION)
        brightness = (accent_color.red() * 299 + accent_color.green() * 587 + accent_color.blue() * 114) / 1000
        accent_text = "#161616" if brightness > 145 else "#f7f9fb"
        muted_text = "#8d8d8d"
        soft_text = "#d9e1ea"
        hover_gray = "rgba(255, 255, 255, 0.055)"
        badge_gray = "rgba(255, 255, 255, 0.025)"

        self.icon_badge.setPixmap(qta.icon('fa5s.map-marked-alt', color=accent).pixmap(14, 14))
        self.add_btn.setIcon(qta.icon('fa5s.map-pin', color=accent_text))
        self.setStyleSheet(f"""
            PinOverlay {{
                background-color: transparent;
            }}
            QFrame#pinFlyoutPanel {{
                background: transparent;
                border: none;
            }}
            QFrame#pinFlyoutShell {{
                background-color: rgba(42, 42, 42, 248);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 14px;
            }}
            QFrame#pinFlyoutShell QLabel,
            QFrame#pinFlyoutShell QWidget {{
                background: transparent;
            }}
            QLabel#pinFlyoutBadge {{
                background-color: {badge_gray};
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 14px;
            }}
            QLabel#pinFlyoutTitle {{
                color: #f3f5f8;
                font-size: 15px;
                font-weight: 700;
            }}
            QLabel#pinFlyoutMeta, QLabel#pinFlyoutCount {{
                color: {muted_text};
                font-size: 11px;
            }}
            QPushButton#pinFlyoutCloseButton {{
                background-color: rgba(255, 255, 255, 0.04);
                color: #f3f5f8;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                padding: 8px 14px;
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton#pinFlyoutCloseButton:hover {{
                background-color: rgba(255, 255, 255, 0.08);
            }}
            QPushButton#pinAddButton {{
                background-color: {accent};
                color: {accent_text};
                border: none;
                border-radius: 8px;
                padding: 9px 14px;
                font-size: 11px;
                font-weight: 700;
            }}
            QPushButton#pinAddButton:hover {{
                background-color: {accent_color.lighter(108).name()};
            }}
            QPushButton#pinAddButton:disabled {{
                background-color: #555555;
                color: #c9c9c9;
            }}
            QScrollArea#pinScrollArea, QWidget#pinScrollContent {{
                background: transparent;
                border: none;
            }}
            QFrame#pinEntryCard {{
                background-color: transparent;
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 10px;
            }}
            QPushButton#pinEntryButton {{
                background-color: transparent;
                border: none;
                border-radius: 8px;
                padding: 10px 10px 10px 4px;
                color: #f3f5f8;
                text-align: left;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton#pinEntryButton:hover {{
                background-color: {hover_gray};
            }}
            QLabel#pinEntryNote {{
                color: {muted_text};
                font-size: 11px;
                padding-left: 6px;
            }}
            QPushButton#pinEntryDeleteButton {{
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 8px;
                padding: 6px;
                min-width: 28px;
                min-height: 28px;
            }}
            QPushButton#pinEntryDeleteButton:hover {{
                background-color: {hover_gray};
                border-color: rgba(255, 255, 255, 0.06);
            }}
            QLabel#pinEmptyState {{
                color: {soft_text};
                font-size: 12px;
                padding: 18px 10px;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255, 255, 255, 0.18);
                min-height: 20px;
                border-radius: 4px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)
        self.refresh_pins()

    def refresh_pins(self):
        self.pins = [pin for pin in self.pins if pin.scene() is not None]

        while self.pin_layout.count():
            item = self.pin_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if self.pins:
            for pin in self.pins:
                if pin.scene():
                    self._create_pin_button(pin)
        else:
            empty_label = QLabel("No pins yet. Drop one from the current canvas view to make quick return points.")
            empty_label.setObjectName("pinEmptyState")
            empty_label.setWordWrap(True)
            self.pin_layout.addWidget(empty_label)

        self.add_btn.setEnabled(len(self.pins) < 10)
        self.pin_count_label.setText(f"{len(self.pins)} / 10 pins")

    def _create_pin_button(self, pin):
        palette = get_current_palette()
        pin_widget = QFrame()
        pin_widget.setObjectName("pinEntryCard")

        layout = QHBoxLayout(pin_widget)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(2)

        btn = QPushButton(pin.title)
        btn.setObjectName("pinEntryButton")
        btn.setIcon(qta.icon('fa5s.map-pin', color=palette.NAV_HIGHLIGHT.name()))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda: self.navigate_to_pin(pin))
        text_column.addWidget(btn)

        if pin.note:
            note_label = QLabel(pin.note)
            note_label.setObjectName("pinEntryNote")
            note_label.setWordWrap(True)
            text_column.addWidget(note_label)

        layout.addLayout(text_column, 1)

        del_btn = QPushButton()
        del_btn.setObjectName("pinEntryDeleteButton")
        del_btn.setIcon(qta.icon('fa5s.times', color='#666666'))
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.clicked.connect(lambda: self.remove_pin(pin))
        layout.addWidget(del_btn)

        self.pin_layout.addWidget(pin_widget)

    def create_pin(self):
        if len(self.pins) >= 10:
            return

        scene = self.canvas_view.scene()
        view = self.canvas_view
        center = view.mapToScene(view.viewport().rect().center())

        pin = scene.add_navigation_pin(center)
        self.pins.append(pin)
        self.refresh_pins()

    def remove_pin(self, pin):
        if pin in self.pins:
            scene = self.canvas_view.scene()

            if pin in scene.pins:
                scene.pins.remove(pin)

            if pin.scene() == scene:
                scene.removeItem(pin)

            self.pins.remove(pin)
            self.refresh_pins()

    def navigate_to_pin(self, pin):
        if pin.scene():
            view = self.canvas_view
            view.centerOn(pin)
            pin.setSelected(True)

    def clear_pins(self):
        self.pins.clear()
        self.refresh_pins()

    def update_pin(self, pin):
        if pin in self.pins and pin.scene():
            self.refresh_pins()
            
    def add_pin_button(self, pin):
        if len(self.pins) >= 10 or pin in self.pins:
            return

        if pin.scene():
            self.pins.append(pin)
            self.refresh_pins()

    def show_for_anchor(self, anchor_widget):
        self.on_theme_changed()

        row_count = max(1, min(len(self.pins), 6))
        target_height = 164 + (row_count * 64)
        self.resize(self.BASE_WIDTH, max(236, min(target_height, 452)))

        target_global = anchor_widget.mapToGlobal(QPoint(0, anchor_widget.height() + 6))
        screen = QGuiApplication.screenAt(target_global) or QGuiApplication.primaryScreen()
        available_geometry = screen.availableGeometry() if screen else None

        x = target_global.x()
        y = target_global.y()

        if available_geometry is not None:
            max_x = available_geometry.right() - self.width() - 12
            max_y = available_geometry.bottom() - self.height() - 12
            x = max(available_geometry.left() + 12, min(x, max_x))
            y = max(available_geometry.top() + 12, min(y, max_y))

        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.closed.emit()

class FontControl(QWidget):
    fontFamilyChanged = Signal(str)
    fontSizeChanged = Signal(int)
    fontColorChanged = Signal(QColor)

    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.canvas = QWidget(self)
        self.canvas.setObjectName("fontControlPanel")
        main_layout = QVBoxLayout(self.canvas)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)
        
        self.canvas.setStyleSheet("""
            QWidget#fontControlPanel {
                background-color: rgba(24, 24, 24, 0.9);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 10px;
            }
            QLabel, QSlider, QPushButton, QComboBox {
                background-color: transparent;
                border: none;
            }
            QComboBox {
                color: #d0d0d0; font-size: 11px;
                border: 1px solid #555;
                background-color: #4a4a4a;
                border-radius: 4px;
                padding: 4px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView { background-color: #3f3f3f; border: 1px solid #555; }
        """)

        font_label = QLabel("Font", self.canvas)
        font_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font_label.setStyleSheet("background-color: transparent; border: none; font-size: 10px; font-weight: bold; color: #cccccc;")
        main_layout.addWidget(font_label)

        self.font_family_combo = QComboBox(self.canvas)
        self.font_family_combo.addItems([
            "Segoe UI", "Arial", "Verdana", "Tahoma", "Consolas",
            "Calibri", "Cambria", "Lucida Grande", "Trebuchet MS",
            "Courier New", "Times New Roman", "Georgia", "System UI",
            "DejaVu Sans", "Segoe UI Variable", "Arial Rounded MT Bold"
        ])
        self.font_family_combo.currentTextChanged.connect(self.fontFamilyChanged.emit)
        main_layout.addWidget(self.font_family_combo)

        self.font_size_slider = QSlider(Qt.Orientation.Horizontal, self.canvas)
        self.font_size_slider.setFixedWidth(160)
        self.font_size_slider.setMinimum(8)
        self.font_size_slider.setMaximum(16)
        self.font_size_slider.setValue(10)
        self.font_size_slider.valueChanged.connect(self.fontSizeChanged.emit)
        self.font_size_slider.setToolTip(f"{self.font_size_slider.value()}pt")
        self.font_size_slider.valueChanged.connect(lambda v: self.font_size_slider.setToolTip(f"{v}pt"))
        self.font_size_slider.setStyleSheet("""
            QSlider::handle:horizontal { background-color: #555555; border-radius: 6px; width: 16px; margin: -6px 0; }
            QSlider::groove:horizontal { background-color: rgba(255, 255, 255, 0.16); height: 4px; border-radius: 2px; }
        """)
        main_layout.addWidget(self.font_size_slider, alignment=Qt.AlignmentFlag.AlignCenter)

        color_presets_layout = QHBoxLayout()
        color_presets_layout.setContentsMargins(0, 0, 0, 0)
        color_presets_layout.setSpacing(10)
        preset_colors = ["#f0f0f0", "#c7c7c7", "#949494", "#6d8599"]
        for color_hex in preset_colors:
            button = QPushButton("", self.canvas)
            button.setFixedSize(32, 20)
            button.setStyleSheet(f"background-color: {color_hex}; border: 2px solid #2d2d2d; border-radius: 5px;")
            button.clicked.connect(lambda checked, c=color_hex: self.fontColorChanged.emit(QColor(c)))
            color_presets_layout.addWidget(button)
        main_layout.addLayout(color_presets_layout)

        self.setFixedSize(200, 160)
        self.canvas.setFixedSize(200, 160)
        
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 0)
        self.canvas.setGraphicsEffect(shadow)

class GridControl(QWidget):
    snapToGridChanged = Signal(bool)
    orthogonalConnectionsChanged = Signal(bool)
    smartGuidesChanged = Signal(bool)
    fadeConnectionsChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.grid_size = 10
        self.grid_opacity = 0.3
        self.grid_style = "Dots"
        self.grid_color = QColor("#555555")
        
        self.canvas = QWidget(self)
        self.canvas.setObjectName("gridControlPanel")
        main_layout = QVBoxLayout(self.canvas)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        self.on_theme_changed()

        grid_label = QLabel("Grid", self.canvas)
        grid_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid_label.setStyleSheet("background-color: transparent; border: none; font-size: 10px; font-weight: bold; color: #cccccc;")
        main_layout.addWidget(grid_label)
        
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal, self.canvas)
        self.opacity_slider.setFixedWidth(160)
        self.opacity_slider.setMinimum(0)
        self.opacity_slider.setMaximum(100)
        self.opacity_slider.setValue(int(self.grid_opacity * 100))
        self.opacity_slider.valueChanged.connect(self._update_opacity)
        self.opacity_slider.setToolTip(f"{self.opacity_slider.value()}%")
        self.opacity_slider.valueChanged.connect(
            lambda: self.opacity_slider.setToolTip(f"{self.opacity_slider.value()}%")
        )
        self.opacity_slider.setStyleSheet("""
            QSlider::handle:horizontal { background-color: #555555; border-radius: 6px; width: 16px; margin: -6px 0; }
            QSlider::groove:horizontal { background-color: rgba(0, 0, 0, 0.2); height: 4px; border-radius: 2px; }
        """)
        main_layout.addWidget(self.opacity_slider, alignment=Qt.AlignmentFlag.AlignCenter)
        
        grid_presets_layout = QHBoxLayout()
        grid_presets_layout.setContentsMargins(0, 0, 0, 0)
        grid_presets_layout.setSpacing(12)
        preset_sizes = [(10, "10px"), (20, "20px"), (50, "50px"), (100, "100px")]
        for size, label_text in preset_sizes:
            button = QPushButton(label_text, self.canvas)
            button.setFixedSize(40, 25)
            button.setStyleSheet("""
                QPushButton {
                    color: white; background-color: rgba(63, 63, 63, 0.4);
                    border: none; border-radius: 5px; font-size: 10px; padding: 2px;
                }
                QPushButton:hover { background-color: rgba(85, 85, 85, 0.6); }
                QPushButton:pressed { background-color: rgba(46, 204, 113, 0.3); color: black; }
            """)
            button.clicked.connect(lambda checked, s=size: self._set_grid_size(s))
            grid_presets_layout.addWidget(button)
        main_layout.addLayout(grid_presets_layout)

        style_presets_layout = QHBoxLayout()
        style_presets = [("Dots", "fa5s.ellipsis-h"), ("Lines", "fa5s.grip-lines"), ("Cross", "fa5s.plus")]
        for style, icon_name in style_presets:
            button = QPushButton(qta.icon(icon_name, color='white'), "", self.canvas)
            button.setFixedSize(40, 25)
            button.setStyleSheet("background-color: rgba(63, 63, 63, 0.4); border: none; border-radius: 5px;")
            button.setToolTip(style)
            button.clicked.connect(lambda checked, s=style: self._set_grid_style(s))
            style_presets_layout.addWidget(button)
        main_layout.addLayout(style_presets_layout)

        color_presets_layout = QHBoxLayout()
        color_presets_layout.setContentsMargins(0, 0, 0, 0)
        color_presets_layout.setSpacing(12)
        preset_colors = ["#404040", "#555555", "#2ecc71", "#3498db"]
        for color_hex in preset_colors:
            button = QPushButton("", self.canvas)
            button.setFixedSize(40, 25)
            button.setStyleSheet(f"background-color: {color_hex}; border: 2px solid #2d2d2d; border-radius: 5px;")
            button.clicked.connect(lambda checked, c=color_hex: self._set_grid_color(c))
            color_presets_layout.addWidget(button)
        main_layout.addLayout(color_presets_layout)

        align_label = QLabel("Alignment & Routing", self.canvas)
        align_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        align_label.setStyleSheet("background-color: transparent; border: none; font-size: 10px; font-weight: bold; color: #cccccc; margin-top: 10px;")
        main_layout.addWidget(align_label)

        self.snap_grid_checkbox = QCheckBox("Snap to Grid")
        self.snap_grid_checkbox.toggled.connect(self.snapToGridChanged.emit)
        main_layout.addWidget(self.snap_grid_checkbox)

        self.ortho_conn_checkbox = QCheckBox("Orthogonal Connections")
        self.ortho_conn_checkbox.toggled.connect(self.orthogonalConnectionsChanged.emit)
        main_layout.addWidget(self.ortho_conn_checkbox)

        self.smart_guides_checkbox = QCheckBox("Smart Guides")
        self.smart_guides_checkbox.toggled.connect(self.smartGuidesChanged.emit)
        main_layout.addWidget(self.smart_guides_checkbox)

        signals_label = QLabel("Connection Rendering", self.canvas)
        signals_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        signals_label.setStyleSheet("background-color: transparent; border: none; font-size: 10px; font-weight: bold; color: #cccccc; margin-top: 10px;")
        main_layout.addWidget(signals_label)

        self.fade_connections_checkbox = QCheckBox("Faded Connections")
        self.fade_connections_checkbox.setToolTip("Keep connections quiet until you hover them.")
        self.fade_connections_checkbox.toggled.connect(self.fadeConnectionsChanged.emit)
        main_layout.addWidget(self.fade_connections_checkbox)
        
        self.setFixedSize(200, 360)
        self.canvas.setFixedSize(200, 360)
        
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 0)
        self.canvas.setGraphicsEffect(shadow)

    def on_theme_changed(self):
        palette = get_current_palette()
        selection_color = palette.SELECTION.name()
        selection_border = palette.SELECTION.darker(110).name()

        self.canvas.setStyleSheet(f"""
            QWidget#gridControlPanel {{
                background-color: rgba(24, 24, 24, 0.9);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 10px;
            }}
            QLabel, QSlider, QPushButton, QCheckBox {{
                background-color: transparent;
                border: none;
            }}
            QCheckBox {{
                color: #cccccc;
                font-size: 11px;
            }}
            QCheckBox::indicator {{ width: 16px; height: 16px; }}
            QCheckBox::indicator:unchecked {{
                border: 1px solid #555; background-color: #3f3f3f; border-radius: 4px;
            }}
            QCheckBox::indicator:checked {{
                background-color: {selection_color}; border: 1px solid {selection_border};
                image: url(C:/Users/Admin/source/repos/graphite_app/assets/check.png);
                border-radius: 4px;
            }}
        """)
        
    def _update_opacity(self, value):
        self.grid_opacity = value / 100.0
        if self.parent():
            self.parent().update()
            
    def _set_grid_size(self, size):
        self.grid_size = size
        if self.parent():
            self.parent().update()

    def _set_grid_style(self, style):
        self.grid_style = style
        if self.parent():
            self.parent().update()

    def _set_grid_color(self, color_hex):
        self.grid_color = QColor(color_hex)
        if self.parent():
            self.parent().update()

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
        import math
        palette = get_current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        
        # 1. Inner orbital dots (Digital feel)
        painter.setPen(Qt.PenStyle.NoPen)
        inner_radius = 9
        for i in range(3):
            offset_deg = i * 120
            # angle3 runs from 140 to 500, reverse it and apply offset
            rad = math.radians(-self._angle3 * 1.5 + offset_deg)
            x = cx + inner_radius * math.cos(rad)
            y = cy + inner_radius * math.sin(rad)
            
            c = QColor(palette.NAV_HIGHLIGHT)
            c.setAlpha(200)
            painter.setBrush(c)
            painter.drawEllipse(QPointF(x, y), 2.5, 2.5)

        # 2. Middle Squiggle (Organic feel)
        path = QPainterPath()
        base_radius = 17
        amplitude = 2.5
        freq = 4
        
        # angle1 drives the base rotation and the morphing
        morph_phase = math.radians(self._angle1 * 1.5)
        rot_phase = math.radians(self._angle1)
        
        for i in range(361):
            rad = math.radians(i)
            # radius oscillates creating a continuous wave
            r = base_radius + amplitude * math.sin(freq * rad + morph_phase)
            draw_rad = rad + rot_phase
            
            x = cx + r * math.cos(draw_rad)
            y = cy + r * math.sin(draw_rad)
            
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
                
        pen = QPen(palette.USER_NODE, 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        # 3. Outer Dot Ring Comet (Modern Loading tail)
        painter.setPen(Qt.PenStyle.NoPen)
        num_dots = 18
        outer_radius = 27
        tail_length = 240  # Degrees the tail spans
        
        for i in range(num_dots):
            dot_angle_deg = i * (360 / num_dots)
            # angle2 (70 -> 430) drives the comet head
            diff = (self._angle2 - dot_angle_deg) % 360
            
            if diff < tail_length:
                # 1.0 at head, 0.0 at tail end
                progress = 1.0 - (diff / tail_length)
                # Curve the progress so the tail drops off elegantly
                eased = progress ** 1.8 
                
                opacity = int(eased * 255)
                size = 1.0 + (eased * 3.5)  # Size from 1.0 to 4.5
                
                rad = math.radians(dot_angle_deg)
                x = cx + outer_radius * math.cos(rad)
                y = cy + outer_radius * math.sin(rad)
                
                c = QColor(palette.SELECTION)
                c.setAlpha(opacity)
                painter.setBrush(c)
                painter.drawEllipse(QPointF(x, y), size, size)
    
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

        self.status_label = QLabel("Version Beta-0.5.3 | © 2026 All Rights Reserved.")
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
