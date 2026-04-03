import os

import qtawesome as qta
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QTextDocument
from PySide6.QtWidgets import QGraphicsItem

from graphite_audio import format_duration
from graphite_canvas_items import Container, HoverAnimationMixin
from graphite_config import get_current_palette, get_graph_node_colors
from graphite_lod import draw_lod_card, lod_mode_for_item, preview_text
from graphite_widgets import ScrollBar


class DocumentNode(QGraphicsItem, HoverAnimationMixin):
    """A graphical node for uploaded file attachments such as documents and audio."""

    DEFAULT_WIDTH = 500
    PADDING = 15
    HEADER_HEIGHT = 30
    MAX_HEIGHT = 600
    SCROLLBAR_PADDING = 5
    COLLAPSED_WIDTH = 260
    COLLAPSED_HEIGHT = 52
    BUTTON_SIZE = 18

    def __init__(
        self,
        title,
        content,
        parent_content_node,
        attachment_kind="document",
        file_path="",
        mime_type=None,
        duration_seconds=None,
        byte_size=None,
        preview_label=None,
        parent=None,
    ):
        super().__init__(parent)
        HoverAnimationMixin.__init__(self)
        self.title = title
        self.parent_content_node = parent_content_node
        self.attachment_kind = (attachment_kind or "document").lower()
        self.file_path = file_path or ""
        self.mime_type = mime_type or ""
        self.duration_seconds = duration_seconds
        self.byte_size = byte_size
        self.preview_label = preview_label or ""
        self.content = content if content is not None else ""
        if self.attachment_kind == "audio" and not self.content:
            self.content = self._build_audio_details()
        if not self.preview_label:
            self.preview_label = self._build_preview_label()

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption)
        self.setAcceptHoverEvents(True)
        self.hovered = False
        self.is_search_match = False
        self.is_collapsed = False
        self.is_docked = False

        self.width = self.DEFAULT_WIDTH
        self.height = self.HEADER_HEIGHT + (self.PADDING * 2)

        self.collapse_button_rect = QRectF()
        self.dock_button_rect = QRectF()
        self.collapse_button_hovered = False
        self.dock_button_hovered = False

        self.document = QTextDocument()
        self.scroll_value = 0
        self.scrollbar = ScrollBar(self)
        self.scrollbar.width = 8
        self.scrollbar.valueChanged.connect(self.update_scroll_position)

        self._setup_document()
        self._recalculate_geometry()

    def _format_byte_size(self):
        if not self.byte_size:
            return "Unknown"

        size = float(self.byte_size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024.0 or unit == "TB":
                if unit == "B":
                    return f"{int(size)} {unit}"
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{int(self.byte_size)} B"

    def _build_audio_details(self):
        lines = ["Audio attachment"]
        if self.duration_seconds is not None:
            lines.append(f"Duration: {format_duration(self.duration_seconds)}")
        if self.mime_type:
            lines.append(f"Format: {self.mime_type}")
        if self.byte_size:
            lines.append(f"Size: {self._format_byte_size()}")
        if self.file_path:
            lines.append(f"Path: {self.file_path}")
        return "\n".join(lines)

    def _build_preview_label(self):
        if self.attachment_kind == "audio":
            duration_label = format_duration(self.duration_seconds) if self.duration_seconds is not None else "Audio"
            return f"Audio | {duration_label}"

        _, extension = os.path.splitext(self.title or "")
        extension = extension.lower()
        if extension == ".pdf":
            return "PDF"
        if extension == ".docx":
            return "DOCX"
        if extension:
            return extension.lstrip(".").upper()
        return "Document"

    def _icon_name(self):
        return "fa5s.music" if self.attachment_kind == "audio" else "fa5s.file-alt"

    def _badge_text(self):
        return "AUDIO" if self.attachment_kind == "audio" else "FILE"

    def _subtitle_text(self):
        return "Audio Attachment" if self.attachment_kind == "audio" else "File Attachment"

    def docked_label(self):
        return self.title or self._subtitle_text()

    def _setup_document(self):
        font_family = "Segoe UI"
        font_size = 10
        color = "#dddddd"

        if self.scene():
            font_family = self.scene().font_family
            font_size = self.scene().font_size
            color = self.scene().font_color.name()

        stylesheet = (
            f"body {{ color: {color}; font-family: '{font_family}'; font-size: {font_size}pt; }}"
            f"p {{ margin: 0 0 0.4em 0; }}"
        )
        self.document.setDefaultStyleSheet(stylesheet)
        self.document.setPlainText(self.content)

    def _recalculate_geometry(self):
        if self.is_collapsed:
            return

        doc_width = self.width - (self.PADDING * 2)
        self.document.setTextWidth(doc_width)

        self.content_height = max(1.0, self.document.size().height())
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
        else:
            self.scroll_value = 0
            self.scrollbar.set_value(0)

        self.prepareGeometryChange()
        self.update()

    def _current_dimensions(self):
        if self.is_collapsed:
            return self.COLLAPSED_WIDTH, self.COLLAPSED_HEIGHT
        return self.width, self.height

    def _update_geometry_for_state(self):
        self.prepareGeometryChange()
        if self.is_collapsed:
            self.width = self.COLLAPSED_WIDTH
            self.height = self.COLLAPSED_HEIGHT
            self.scrollbar.setVisible(False)
            self.scroll_value = 0
        else:
            self.width = self.DEFAULT_WIDTH
            self._recalculate_geometry()

        if self.scene():
            self.scene().update_connections()
        parent = self.parentItem()
        if parent and isinstance(parent, Container):
            parent.updateGeometry()
        self.update()

    def set_collapsed(self, collapsed):
        collapsed = bool(collapsed)
        if self.is_collapsed != collapsed:
            self.is_collapsed = collapsed
            self._update_geometry_for_state()

    def toggle_collapse(self):
        self.set_collapsed(not self.is_collapsed)

    def dock(self):
        self.is_docked = True
        self.hide()
        if self.parent_content_node:
            if hasattr(self.parent_content_node, "add_docked_child"):
                self.parent_content_node.add_docked_child(self)
            else:
                docked_nodes = getattr(self.parent_content_node, "docked_attachment_nodes", None)
                if docked_nodes is not None and self not in docked_nodes:
                    docked_nodes.append(self)
                self.parent_content_node.update()
        if self.scene():
            self.scene().update_connections()

    def undock(self):
        self.is_docked = False
        self.show()
        if self.parent_content_node:
            if hasattr(self.parent_content_node, "remove_docked_child"):
                self.parent_content_node.remove_docked_child(self)
            else:
                docked_nodes = getattr(self.parent_content_node, "docked_attachment_nodes", None)
                if docked_nodes is not None and self in docked_nodes:
                    docked_nodes.remove(self)
                self.parent_content_node.update()
        if self.scene():
            self.scene().update_connections()

    def update_font_settings(self, font_family, font_size, color):
        self._setup_document()
        if not self.is_collapsed:
            self._recalculate_geometry()

    def boundingRect(self):
        current_width, current_height = self._current_dimensions()
        return QRectF(-5, -5, current_width + 10, current_height + 10)

    def _update_action_button_rects(self, current_width, current_height):
        button_y = max(6.0, (self.HEADER_HEIGHT - self.BUTTON_SIZE) / 2) if not self.is_collapsed else (current_height - self.BUTTON_SIZE) / 2
        self.collapse_button_rect = QRectF(current_width - 28, button_y, self.BUTTON_SIZE, self.BUTTON_SIZE)
        self.dock_button_rect = QRectF(current_width - 50, button_y, self.BUTTON_SIZE, self.BUTTON_SIZE)

    def _paint_action_button(self, painter, rect, icon_name, hovered):
        button_bg = QColor(255, 255, 255, 44 if hovered else 26)
        button_border = QColor(255, 255, 255, 110 if hovered else 70)
        painter.setBrush(button_bg)
        painter.setPen(button_border)
        painter.drawRoundedRect(rect, 4, 4)
        icon = qta.icon(icon_name, color="#ffffff" if hovered else "#d4d7da")
        icon.paint(painter, rect.adjusted(3, 3, -3, -3).toRect())

    def _paint_collapsed_state(self, painter, current_width, current_height, pen):
        node_colors = get_graph_node_colors()
        painter.setBrush(QColor("#24272c"))
        painter.setPen(pen)
        painter.drawRoundedRect(0, 0, current_width, current_height, current_height / 2, current_height / 2)

        icon = qta.icon(self._icon_name(), color="#d6d9dc")
        icon.paint(painter, QRectF(12, 14, 16, 16).toRect())

        title_font = QFont(self.scene().font_family if self.scene() else "Segoe UI", 9, QFont.Weight.Bold)
        painter.setFont(title_font)
        painter.setPen(QColor("#f0f3f6"))
        title_metrics = QFontMetrics(title_font)
        title_width = current_width - 84
        elided_title = title_metrics.elidedText(self.title or self._subtitle_text(), Qt.TextElideMode.ElideRight, title_width)
        painter.drawText(QRectF(36, 9, title_width, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided_title)

        subtitle_font = QFont(self.scene().font_family if self.scene() else "Segoe UI", 8)
        painter.setFont(subtitle_font)
        painter.setPen(QColor("#aab2bb"))
        subtitle_metrics = QFontMetrics(subtitle_font)
        subtitle_text = subtitle_metrics.elidedText(self.preview_label or self._subtitle_text(), Qt.TextElideMode.ElideRight, title_width)
        painter.drawText(QRectF(36, 26, title_width, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, subtitle_text)

    def paint(self, painter, option, widget=None):
        palette = get_current_palette()
        node_colors = get_graph_node_colors()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        current_width, current_height = self._current_dimensions()
        lod_mode = lod_mode_for_item(self)
        is_dragging = self.scene() and getattr(self.scene(), "is_rubber_band_dragging", False)

        if not self.is_collapsed and lod_mode != "full":
            draw_lod_card(
                painter,
                QRectF(0, 0, current_width, current_height),
                accent=node_colors["header_start"],
                selection_color=palette.SELECTION,
                title=self.title or self._subtitle_text(),
                subtitle=self._subtitle_text(),
                preview=preview_text(self.content or self.preview_label, fallback="[Empty]"),
                badge=self._badge_text(),
                mode=lod_mode,
                selected=self.isSelected() and not is_dragging,
                hovered=self.hovered,
                search_match=self.is_search_match,
            )
            self.collapse_button_rect = QRectF()
            self.dock_button_rect = QRectF()
            return

        if self.isSelected() and not is_dragging:
            pen = QPen(node_colors["selected_outline"], 2)
        elif self.hovered:
            pen = QPen(node_colors["hover_outline"], 2)
        else:
            pen = QPen(node_colors["border"], 1)

        if self.is_collapsed:
            self._paint_collapsed_state(painter, current_width, current_height, pen)
        else:
            path = QPainterPath()
            path.addRoundedRect(0, 0, self.width, self.height, 10, 10)
            painter.setBrush(QColor("#2d2d2d"))
            painter.setPen(pen)
            painter.drawPath(path)

            header_path = QPainterPath()
            header_rect = QRectF(0, 0, self.width, self.HEADER_HEIGHT)
            header_path.addRoundedRect(header_rect, 10, 10)
            painter.setBrush(node_colors["header_start"])
            painter.drawPath(header_path)

            icon = qta.icon(self._icon_name(), color="#cccccc")
            icon.paint(painter, QRectF(10, 7, 16, 16).toRect())

            painter.setPen(QColor("#cccccc"))
            title_font = QFont("Segoe UI", 9, QFont.Weight.Bold)
            painter.setFont(title_font)
            title_metrics = QFontMetrics(title_font)
            elided_title = title_metrics.elidedText(self.title or self._subtitle_text(), Qt.TextElideMode.ElideRight, self.width - 120)
            painter.drawText(header_rect.adjusted(35, 0, -70, 0), Qt.AlignmentFlag.AlignVCenter, elided_title)

            badge_font = QFont("Segoe UI", 7, QFont.Weight.DemiBold)
            painter.setFont(badge_font)
            badge_metrics = QFontMetrics(badge_font)
            badge_text = self.preview_label or self._subtitle_text()
            badge_width = min(160, badge_metrics.horizontalAdvance(badge_text) + 14)
            badge_rect = QRectF(self.width - badge_width - 58, 7, badge_width, 16)
            painter.setBrush(QColor(255, 255, 255, 18))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(badge_rect, 8, 8)
            painter.setPen(QColor("#d6d9dc"))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge_metrics.elidedText(badge_text, Qt.TextElideMode.ElideRight, int(badge_rect.width() - 10)))

            painter.save()
            painter.translate(self.PADDING, self.HEADER_HEIGHT + 5)
            clip_rect = QRectF(0, 0, self.width - (self.PADDING * 2), self.height - self.HEADER_HEIGHT - self.PADDING)
            painter.setClipRect(clip_rect)

            scroll_offset = (self.content_height - (self.height - self.HEADER_HEIGHT - self.PADDING)) * self.scroll_value
            painter.translate(0, -scroll_offset)

            self.document.drawContents(painter)
            painter.restore()

        if self.hovered:
            self._update_action_button_rects(current_width, current_height)
            self._paint_action_button(painter, self.dock_button_rect, "fa5s.arrow-up", self.dock_button_hovered)
            self._paint_action_button(
                painter,
                self.collapse_button_rect,
                "fa5s.expand-arrows-alt" if self.is_collapsed else "fa5s.compress-arrows-alt",
                self.collapse_button_hovered,
            )
        else:
            self.collapse_button_rect = QRectF()
            self.dock_button_rect = QRectF()

    def wheelEvent(self, event):
        if self.is_collapsed or not self.scrollbar.isVisible():
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
        if event.button() == Qt.MouseButton.LeftButton and self.collapse_button_rect.contains(event.pos()):
            self.toggle_collapse()
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton and self.dock_button_rect.contains(event.pos()):
            self.dock()
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton and self.scene():
            self.scene().is_dragging_item = True
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self.scene():
            self.scene().is_dragging_item = False
            self.scene()._clear_smart_guides()
        super().mouseReleaseEvent(event)

    def hoverMoveEvent(self, event):
        collapse_hovered = self.collapse_button_rect.contains(event.pos())
        dock_hovered = self.dock_button_rect.contains(event.pos())
        if collapse_hovered != self.collapse_button_hovered or dock_hovered != self.dock_button_hovered:
            self.collapse_button_hovered = collapse_hovered
            self.dock_button_hovered = dock_hovered
            self.update()
        super().hoverMoveEvent(event)

    def hoverEnterEvent(self, event):
        self._handle_hover_enter(event)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.collapse_button_hovered = False
        self.dock_button_hovered = False
        self._handle_hover_leave(event)
        super().hoverLeaveEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSceneHasChanged and self.scene():
            self._setup_document()
            if not self.is_collapsed:
                self._recalculate_geometry()
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
