"""Canvas navigation pin graphics item.

The item is deliberately a thin interaction/rendering projection. Persistent
metadata is owned by ``NavigationPinStore`` and mutations are routed through the
scene/controller boundary rather than directly into a window or panel.
"""

from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject
from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QFontMetrics, QPainterPath


class NavigationPin(QGraphicsObject):
    """
    A "bookmark" item that can be placed anywhere on the canvas. These pins
    are listed in an overlay, allowing users to quickly jump to different
    locations in a large graph.
    """
    # QGraphicsScene uses boundingRect() to calculate the dirty region when an
    # item moves.  Keep the text and the complete pin shape inside that region;
    # painting outside it leaves stale pixels behind with MinimalViewportUpdate.
    # Includes the label, beacon, connector, and ground marker. Keeping the
    # complete visual inside the dirty region prevents stale pixels while the
    # pin is dragged with MinimalViewportUpdate.
    _PAINT_RECT = QRectF(-90.0, -54.0, 180.0, 92.0)
    _LABEL_RECT = QRectF(-82.0, -52.0, 164.0, 28.0)
    _BEACON_RECT = QRectF(-15.0, -15.0, 30.0, 30.0)

    editRequested = Signal(str)
    contextMenuRequested = Signal(str, object)
    positionPreviewChanged = Signal(str, object)
    positionCommitted = Signal(str, object)

    def __init__(self, title="Waypoint", note="", parent=None, pin_id=None):
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
        path.addRoundedRect(self._BEACON_RECT, 8.0, 8.0)
        path.moveTo(-2.0, 13.0)
        path.lineTo(-2.0, 30.0)
        path.lineTo(2.0, 30.0)
        path.lineTo(2.0, 13.0)
        path.closeSubpath()
        path.addEllipse(QRectF(-6.0, 25.0, 12.0, 8.0))
        return path
        
    def paint(self, painter, option, widget=None):
        """Handles the custom painting of the pin icon."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        selected = self.isSelected()
        active = selected or self.hovered
        marker_fill = QColor("#c8c8c8" if selected else "#a0a0a0" if self.hovered else "#777777")
        marker_border = QColor("#f2f2f2" if selected else "#bdbdbd" if self.hovered else "#555555")
        surface = QColor("#252525")

        # A beacon-style marker makes navigation pins visually distinct from
        # connection pins: rounded square body, inset waypoint core, stem, and
        # ground contact. It deliberately uses grayscale only.
        if active:
            halo = QColor("#d8d8d8")
            halo.setAlpha(35 if not selected else 55)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(QRectF(-22.0, -22.0, 44.0, 44.0))

        painter.setPen(QPen(marker_border, 2.0))
        painter.setBrush(marker_fill)
        painter.drawRoundedRect(self._BEACON_RECT, 8.0, 8.0)

        painter.setPen(QPen(surface, 1.5))
        painter.setBrush(surface)
        painter.drawEllipse(QRectF(-5.0, -5.0, 10.0, 10.0))

        painter.setPen(QPen(marker_border, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(0.0, 15.0, 0.0, 28.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(marker_border)
        painter.drawEllipse(QRectF(-5.0, 25.0, 10.0, 7.0))

        if active:
            label_surface = QColor("#242424")
            label_border = QColor("#777777" if selected else "#555555")
            painter.setPen(QPen(label_border, 1.0))
            painter.setBrush(label_surface)
            painter.drawRoundedRect(self._LABEL_RECT, 10.0, 10.0)

            # Keep the label anchored without recreating the old triangle silhouette.
            painter.setPen(QPen(label_border, 1.0))
            painter.drawLine(0.0, self._LABEL_RECT.bottom(), 0.0, -15.0)

            font = QFont("Segoe UI", 8)
            font.setWeight(QFont.Weight.DemiBold if selected else QFont.Weight.Normal)
            painter.setFont(font)
            painter.setPen(QColor("#f0f0f0"))
            title = QFontMetrics(font).elidedText(
                str(self.title or "Waypoint"), Qt.TextElideMode.ElideRight, 142
            )
            painter.drawText(self._LABEL_RECT, Qt.AlignmentFlag.AlignCenter, title)

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
