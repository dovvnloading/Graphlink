"""Canvas navigation pin used by the quick-jump overlay."""

from PySide6.QtWidgets import QDialog, QGraphicsItem
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QPainterPath

from .graphite_canvas_dialogs import PinEditDialog
from graphite_config import get_current_palette


class NavigationPin(QGraphicsItem):
    """
    A "bookmark" item that can be placed anywhere on the canvas. These pins
    are listed in an overlay, allowing users to quickly jump to different
    locations in a large graph.
    """
    def __init__(self, title="New Pin", note="", parent=None):
        """
        Initializes the NavigationPin.

        Args:
            title (str, optional): The display title of the pin.
            note (str, optional): An optional descriptive note for the pin.
            parent (QGraphicsItem, optional): The parent item. Defaults to None.
        """
        super().__init__(parent)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        
        self.title = title
        self.note = note
        self.hovered = False
        self.size = 32
        
    def boundingRect(self):
        """Returns the bounding rectangle of the pin's visual representation."""
        return QRectF(-self.size/2, -self.size/2, self.size, self.size)
        
    def paint(self, painter, option, widget=None):
        """Handles the custom painting of the pin icon."""
        palette = get_current_palette()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Change color based on selection or hover state.
        if self.isSelected(): pin_color = palette.SELECTION
        elif self.hovered: pin_color = palette.AI_NODE
        else: pin_color = palette.NAV_HIGHLIGHT
            
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(pin_color)
        
        # Draw the pin shape (a circle on top of a triangle).
        head_rect = QRectF(-10, -10, 20, 20)
        painter.drawEllipse(head_rect)
        
        path = QPainterPath()
        path.moveTo(0, 10); path.lineTo(-8, 25); path.lineTo(8, 25); path.closeSubpath()
        painter.setBrush(pin_color)
        painter.drawPath(path)
        
        # Show the title on hover or selection.
        if self.hovered or self.isSelected():
            painter.setPen(QPen(QColor("#ffffff")))
            font = QFont("Segoe UI", 8)
            painter.setFont(font)
            text_rect = QRectF(-50, -35, 100, 20)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self.title)
            
    def hoverEnterEvent(self, event):
        self.hovered = True; self.update(); super().hoverEnterEvent(event)
        
    def hoverLeaveEvent(self, event):
        self.hovered = False; self.update(); super().hoverLeaveEvent(event)
        
    def mouseDoubleClickEvent(self, event):
        """Opens an editing dialog on double-click."""
        dialog = PinEditDialog(self.title, self.note, self.scene().views()[0])
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.title = dialog.title_input.text()
            self.note = dialog.note_input.toPlainText()
            # Notify the pin overlay to update its list.
            if self.scene() and hasattr(self.scene().window, 'pin_overlay'):
                self.scene().window.pin_overlay.update_pin(self)
        super().mouseDoubleClickEvent(event)
