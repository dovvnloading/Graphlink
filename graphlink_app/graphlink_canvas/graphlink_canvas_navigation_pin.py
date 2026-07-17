"""Canvas navigation pin graphics item.

The item is deliberately a thin interaction/rendering projection. Persistent
metadata is owned by ``NavigationPinStore`` and mutations are routed through the
scene/controller boundary rather than directly into a window or panel.
"""

from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject
from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QPainterPath

from graphlink_config import get_current_palette


class NavigationPin(QGraphicsObject):
    """
    A "bookmark" item that can be placed anywhere on the canvas. These pins
    are listed in an overlay, allowing users to quickly jump to different
    locations in a large graph.
    """
    # QGraphicsScene uses boundingRect() to calculate the dirty region when an
    # item moves.  Keep the text and the complete pin shape inside that region;
    # painting outside it leaves stale pixels behind with MinimalViewportUpdate.
    # Includes the title, marker, and the comfortable triangle hit target.
    _PAINT_RECT = QRectF(-52.0, -37.0, 104.0, 68.0)
    _TITLE_RECT = QRectF(-50.0, -35.0, 100.0, 20.0)

    editRequested = Signal(str)
    contextMenuRequested = Signal(str, object)
    positionPreviewChanged = Signal(str, object)
    positionCommitted = Signal(str, object)

    def __init__(self, title="New Pin", note="", parent=None, pin_id=None):
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
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setCacheMode(QGraphicsItem.CacheMode.NoCache)
        
        self.pin_id = str(pin_id or "")
        self.title = title
        self.note = note
        self.hovered = False
        self._dragging = False
        
    def boundingRect(self):
        """Returns the bounding rectangle of the pin's visual representation."""
        return QRectF(self._PAINT_RECT)

    def shape(self):
        """Return the comfortable hit target for the visible marker."""
        path = QPainterPath()
        path.addEllipse(QRectF(-14.0, -14.0, 28.0, 28.0))
        path.moveTo(0.0, 7.0)
        path.lineTo(-12.0, 28.0)
        path.lineTo(12.0, 28.0)
        path.closeSubpath()
        return path
        
    def paint(self, painter, option, widget=None):
        """Handles the custom painting of the pin icon."""
        palette = get_current_palette()
        painter.save()
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
            painter.drawText(self._TITLE_RECT, Qt.AlignmentFlag.AlignCenter, self.title)

        painter.restore()
            
    def hoverEnterEvent(self, event):
        self.hovered = True; self.update(); super().hoverEnterEvent(event)
        
    def hoverLeaveEvent(self, event):
        self.hovered = False; self.update(); super().hoverLeaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.setSelected(True)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        was_dragging = self._dragging
        self._dragging = False
        super().mouseReleaseEvent(event)
        if was_dragging and event.button() == Qt.MouseButton.LeftButton:
            self.positionCommitted.emit(self.pin_id, self.pos())

    def itemChange(self, change, value):
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged
            and self.scene() is not None
        ):
            self.positionPreviewChanged.emit(self.pin_id, value)
        return super().itemChange(change, value)

    def apply_metadata(self, title, note):
        """Update display metadata after the store has accepted an edit."""
        self.title = str(title)
        self.note = str(note)
        self.update()

    def mouseDoubleClickEvent(self, event):
        """Delegate editing to the feature controller/editor surface."""
        self.editRequested.emit(self.pin_id)
        event.accept()

    def contextMenuEvent(self, event):
        self.contextMenuRequested.emit(self.pin_id, event.screenPos())
        event.accept()
