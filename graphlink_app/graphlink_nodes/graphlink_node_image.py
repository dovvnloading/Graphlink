import qtawesome as qta
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem

from graphlink_canvas_items import Container, HoverAnimationMixin
from graphlink_config import canvas_font, canvas_font_color, get_current_palette, get_graph_node_colors
from graphlink_lod import draw_lod_card, lod_mode_for_item, preview_text


class ImageNode(QGraphicsItem, HoverAnimationMixin):
    """A graphical node for displaying an image."""

    PADDING = 15
    HEADER_HEIGHT = 30
    MAX_HEIGHT = 600

    def __init__(self, image_bytes, parent_content_node, prompt="", parent=None):
        super().__init__(parent)
        HoverAnimationMixin.__init__(self)
        self.image_bytes = image_bytes
        self.parent_content_node = parent_content_node
        self.prompt = prompt

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption)
        self.setAcceptHoverEvents(True)
        self.hovered = False
        self.is_search_match = False

        self.image = QImage.fromData(self.image_bytes)
        self.image_valid = not self.image.isNull()
        self.width = 512 + (self.PADDING * 2)

        if self.image_valid:
            aspect_ratio = self.image.height() / self.image.width() if self.image.width() > 0 else 1
            content_width = self.width - (self.PADDING * 2)
            content_height = content_width * aspect_ratio
            self.height = min(self.MAX_HEIGHT, content_height + self.HEADER_HEIGHT + (self.PADDING * 2))
        else:
            self.height = 400

    def update_font_settings(self, font_family, font_size, color):
        self.update()

    def boundingRect(self):
        return QRectF(-5, -5, self.width + 10, self.height + 10)

    def paint(self, painter, option, widget=None):
        palette = get_current_palette()
        node_colors = get_graph_node_colors()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        lod_mode = lod_mode_for_item(self)

        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width, self.height, 10, 10)

        painter.setBrush(QColor("#2D2D2D"))

        is_dragging = self.scene() and getattr(self.scene(), 'is_rubber_band_dragging', False)

        if self.isSelected() and not is_dragging:
            painter.setPen(QPen(node_colors["selected_outline"], 2))
        elif self.hovered:
            painter.setPen(QPen(node_colors["hover_outline"], 2))
        else:
            painter.setPen(QPen(node_colors["border"], 1))

        if lod_mode != "full":
            draw_lod_card(
                painter,
                QRectF(0, 0, self.width, self.height),
                accent=node_colors["header_start"],
                selection_color=palette.SELECTION,
                title="Image",
                subtitle="Generated asset",
                preview=preview_text(self.prompt, fallback="Visual content"),
                badge="IMG",
                mode=lod_mode,
                selected=self.isSelected() and not is_dragging,
                hovered=self.hovered,
            )
            return
        painter.drawPath(path)

        header_path = QPainterPath()
        header_rect = QRectF(0, 0, self.width, self.HEADER_HEIGHT)
        header_path.addRoundedRect(header_rect, 10, 10)
        painter.setBrush(node_colors["header_start"])
        painter.drawPath(header_path)

        icon = qta.icon('fa5s.image', color='#CCCCCC')
        icon.paint(painter, QRectF(10, 7, 16, 16).toRect())

        painter.setPen(QColor("#CCCCCC"))
        font = canvas_font(self.scene(), delta=-1)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        elided_prompt = metrics.elidedText(f"Image: {self.prompt}", Qt.TextElideMode.ElideRight, self.width - 50)
        painter.drawText(header_rect.adjusted(35, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter, elided_prompt)

        image_rect = QRectF(
            self.PADDING,
            self.HEADER_HEIGHT + self.PADDING,
            self.width - (self.PADDING * 2),
            self.height - self.HEADER_HEIGHT - (self.PADDING * 2),
        )
        if self.image_valid:
            scale = min(
                image_rect.width() / max(1, self.image.width()),
                image_rect.height() / max(1, self.image.height()),
            )
            target_width = self.image.width() * scale
            target_height = self.image.height() * scale
            target_rect = QRectF(
                image_rect.center().x() - target_width / 2,
                image_rect.center().y() - target_height / 2,
                target_width,
                target_height,
            )
            painter.drawImage(target_rect, self.image)
        else:
            painter.save()
            placeholder_color = canvas_font_color(self.scene(), "#B1B1B1")
            placeholder_color.setAlpha(180)
            painter.setPen(QPen(placeholder_color, 1, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(255, 255, 255, 10))
            painter.drawRoundedRect(image_rect, 8, 8)
            painter.setPen(placeholder_color)
            painter.setFont(canvas_font(self.scene(), delta=-1))
            painter.drawText(image_rect, Qt.AlignmentFlag.AlignCenter, "Image unavailable")
            painter.restore()

    def contextMenuEvent(self, event):
        from graphlink_nodes.graphlink_node_image_menu import ImageNodeContextMenu

        menu = ImageNodeContextMenu(self)
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
        if change == QGraphicsItem.ItemPositionChange and self.scene():
            parent = self.parentItem()
            if parent and isinstance(parent, Container):
                parent.updateGeometry()

            if self.scene().is_dragging_item:
                return self.scene().snap_position(self, value)
        if change == QGraphicsItem.ItemPositionHasChanged and self.scene():
            self.scene().nodeMoved(self)
        return super().itemChange(change, value)


__all__ = ["ImageNode"]
