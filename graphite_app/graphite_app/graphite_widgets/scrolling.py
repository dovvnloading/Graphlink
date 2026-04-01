"""Custom scrollbar widgets for Qt and graphics scenes."""

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter
from PySide6.QtWidgets import QGraphicsObject, QGridLayout, QVBoxLayout, QWidget

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


