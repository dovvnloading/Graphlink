from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QTextDocument
from PySide6.QtWidgets import QGraphicsItem

from graphite_canvas_items import Container, HoverAnimationMixin
from graphite_config import get_current_palette, get_graph_node_colors
from graphite_widgets import ScrollBar


class DocumentNode(QGraphicsItem, HoverAnimationMixin):
    """A graphical node for displaying the content of a text-based document."""

    PADDING = 15
    HEADER_HEIGHT = 30
    MAX_HEIGHT = 600
    SCROLLBAR_PADDING = 5

    def __init__(self, title, content, parent_content_node, parent=None):
        super().__init__(parent)
        HoverAnimationMixin.__init__(self)
        self.title = title
        self.content = content
        self.parent_content_node = parent_content_node
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.hovered = False
        self.is_search_match = False
        self.is_collapsed = False

        self.width = 500

        self.document = QTextDocument()
        self._setup_document()

        self.scroll_value = 0
        self.scrollbar = ScrollBar(self)
        self.scrollbar.width = 8
        self.scrollbar.valueChanged.connect(self.update_scroll_position)
        self._recalculate_geometry()

    def _setup_document(self):
        font_family = "Segoe UI"
        font_size = 10
        color = "#dddddd"

        if self.scene():
            font_family = self.scene().font_family
            font_size = self.scene().font_size
            color = self.scene().font_color.name()

        stylesheet = f"p {{ color: {color}; font-family: '{font_family}'; font-size: {font_size}pt; }}"
        self.document.setDefaultStyleSheet(stylesheet)
        self.document.setPlainText(self.content)

    def _recalculate_geometry(self):
        doc_width = self.width - (self.PADDING * 2)
        self.document.setTextWidth(doc_width)

        self.content_height = self.document.size().height()
        self.height = min(self.MAX_HEIGHT, self.content_height + self.HEADER_HEIGHT + self.PADDING)

        is_scrollable = self.content_height + self.HEADER_HEIGHT + self.PADDING > self.height
        self.scrollbar.setVisible(is_scrollable)

        if is_scrollable:
            self.scrollbar.height = self.height - self.HEADER_HEIGHT - (self.SCROLLBAR_PADDING * 2)
            self.scrollbar.setPos(
                self.width - self.scrollbar.width - self.SCROLLBAR_PADDING,
                self.HEADER_HEIGHT + self.SCROLLBAR_PADDING,
            )
            visible_ratio = (self.height - self.HEADER_HEIGHT - self.PADDING) / self.content_height
            self.scrollbar.set_range(visible_ratio)

        self.prepareGeometryChange()
        self.update()

    def update_font_settings(self, font_family, font_size, color):
        self._setup_document()

    def boundingRect(self):
        return QRectF(-5, -5, self.width + 10, self.height + 10)

    def paint(self, painter, option, widget=None):
        palette = get_current_palette()
        node_colors = get_graph_node_colors()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width, self.height, 10, 10)
        painter.setBrush(QColor("#2d2d2d"))

        is_dragging = self.scene() and getattr(self.scene(), 'is_rubber_band_dragging', False)

        if self.isSelected() and not is_dragging:
            painter.setPen(QPen(node_colors["selected_outline"], 2))
        elif self.hovered:
            painter.setPen(QPen(node_colors["hover_outline"], 2))
        else:
            painter.setPen(QPen(node_colors["border"], 1))
        painter.drawPath(path)

        header_path = QPainterPath()
        header_rect = QRectF(0, 0, self.width, self.HEADER_HEIGHT)
        header_path.addRoundedRect(header_rect, 10, 10)
        painter.setBrush(node_colors["header_start"])
        painter.drawPath(header_path)

        import qtawesome as qta

        file_icon = qta.icon('fa5s.file-alt', color='#cccccc')
        file_icon.paint(painter, QRectF(10, 7, 16, 16).toRect())

        painter.setPen(QColor("#cccccc"))
        font = QFont('Segoe UI', 9, QFont.Weight.Bold)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        elided_title = metrics.elidedText(self.title, Qt.TextElideMode.ElideRight, self.width - 50)
        painter.drawText(header_rect.adjusted(35, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter, elided_title)

        painter.save()
        painter.translate(self.PADDING, self.HEADER_HEIGHT + 5)
        clip_rect = QRectF(0, 0, self.width - (self.PADDING * 2), self.height - self.HEADER_HEIGHT - self.PADDING)
        painter.setClipRect(clip_rect)

        scroll_offset = (self.content_height - (self.height - self.HEADER_HEIGHT - self.PADDING)) * self.scroll_value
        painter.translate(0, -scroll_offset)

        self.document.drawContents(painter)
        painter.restore()

    def wheelEvent(self, event):
        if not self.scrollbar.isVisible():
            event.ignore()
            return

        delta = event.delta() / 120
        scroll_range = self.content_height - (self.height - self.HEADER_HEIGHT)
        if scroll_range <= 0:
            return

        scroll_delta = -(delta * 50) / scroll_range

        new_value = max(0, min(1, self.scroll_value + scroll_delta))
        self.scroll_value = new_value
        self.scrollbar.set_value(new_value)
        self.update()
        event.accept()

    def update_scroll_position(self, value):
        self.scroll_value = value
        self.update()

    def contextMenuEvent(self, event):
        from graphite_nodes.graphite_node_document_menu import DocumentNodeContextMenu

        menu = DocumentNodeContextMenu(self)
        menu.exec(event.screenPos())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.scene():
            self.scene().is_dragging_item = True
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self.scene():
            self.scene().is_dragging_item = False
            self.scene()._clear_smart_guides()
        super().mouseReleaseEvent(event)

    def hoverEnterEvent(self, event):
        self._handle_hover_enter(event)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._handle_hover_leave(event)
        super().hoverLeaveEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSceneHasChanged and self.scene():
            self._setup_document()
        if change == QGraphicsItem.ItemPositionChange and self.scene():
            parent = self.parentItem()
            if parent and isinstance(parent, Container):
                parent.updateGeometry()

            if self.scene().is_dragging_item:
                return self.scene().snap_position(self, value)
        if change == QGraphicsItem.ItemPositionHasChanged and self.scene():
            self.scene().nodeMoved(self)
        return super().itemChange(change, value)


__all__ = ["DocumentNode"]
