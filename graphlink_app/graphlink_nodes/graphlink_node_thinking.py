import markdown
import qtawesome as qta
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QTextDocument
from PySide6.QtWidgets import QGraphicsItem

from graphlink_canvas_items import Container, HoverAnimationMixin
from graphlink_config import canvas_font, get_current_palette, get_graph_node_colors, get_surface_color
from graphlink_lod import draw_lod_card, lod_mode_for_item, preview_text
from graphlink_styles import FONT_FAMILY_NAME
from graphlink_widgets import ScrollBar


class ThinkingNode(QGraphicsItem, HoverAnimationMixin):
    """A graphical node for displaying the AI's reasoning or 'Chain of Thought' text."""

    PADDING = 15
    HEADER_HEIGHT = 30
    MAX_HEIGHT = 600
    SCROLLBAR_PADDING = 5
    CONTENT_PANEL_MARGIN_X = 12
    CONTENT_PANEL_TOP = 42
    CONTENT_PANEL_BOTTOM = 12
    CONTENT_PANEL_PADDING_X = 14
    CONTENT_PANEL_PADDING_Y = 12

    def __init__(self, thinking_text, parent_content_node, parent=None):
        super().__init__(parent)
        HoverAnimationMixin.__init__(self)
        self.thinking_text = thinking_text
        self.parent_content_node = parent_content_node
        self.is_docked = False
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption)
        self.setAcceptHoverEvents(True)
        self.hovered = False
        self.is_search_match = False
        self.dock_button_rect = QRectF()
        self.dock_button_hovered = False

        self.width = 500
        self.height = self.HEADER_HEIGHT + (self.PADDING * 2)

        self.document = QTextDocument()
        self._setup_document()

        self.scroll_value = 0
        self.scrollbar = ScrollBar(self)
        self.scrollbar.width = 8
        self.scrollbar.valueChanged.connect(self.update_scroll_position)
        self._recalculate_geometry()

    def _setup_document(self):
        font_family = FONT_FAMILY_NAME
        font_size = 9
        color = get_surface_color("text_secondary")

        if self.scene():
            font_family = self.scene().font_family
            font_size = self.scene().font_size - 1
            color = self.scene().font_color.lighter(120).name()

        stylesheet = (
            f"body {{ color: {color}; font-family: '{font_family}'; font-size: {font_size}pt; margin: 0; }}"
            f"p {{ color: {color}; margin: 0 0 0.6em 0; }}"
            f"p.thinking-kicker {{ color: {get_surface_color('text_label')}; font-size: 8.5pt; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; margin: 0 0 8px 0; }}"
            f"blockquote {{ border-left: 3px solid {get_surface_color('border_strong')}; padding-left: 10px; margin: 0.35em 0 0.8em 0; color: {get_surface_color('text_soft')}; }}"
            f"pre {{ background: {get_surface_color('inset_deep')}; border: 1px solid {get_surface_color('border')}; border-radius: 8px; padding: 10px 12px; margin: 0.35em 0 0.8em 0; color: {get_surface_color('text_primary')}; }}"
            f"code {{ background: {get_surface_color('inset_deep')}; border-radius: 4px; padding: 1px 4px; color: {get_surface_color('text_primary')}; }}"
            "pre code { background: transparent; padding: 0; }"
            "ul, ol { margin: 0 0 0.75em 0; padding-left: 20px; }"
            "li { margin-bottom: 0.2em; }"
        )
        self.document.setDefaultStyleSheet(stylesheet)
        content_html = markdown.markdown(self.thinking_text, extensions=['fenced_code'])
        html = f"<p class='thinking-kicker'>Assistant Thinking</p>{content_html}"
        self.document.setHtml(html)

    def _recalculate_geometry(self):
        doc_width = self._content_body_rect().width()
        self.document.setTextWidth(doc_width)

        new_content_height = max(1.0, self.document.size().height())
        new_height = min(
            self.MAX_HEIGHT,
            self.CONTENT_PANEL_TOP + self.CONTENT_PANEL_BOTTOM + (self.CONTENT_PANEL_PADDING_Y * 2) + new_content_height,
        )
        if new_height != self.height:
            self.prepareGeometryChange()
            self.height = new_height
        self.content_height = new_content_height

        is_scrollable = self.content_height > self._content_body_rect().height()
        self.scrollbar.setVisible(is_scrollable)

        if is_scrollable:
            panel_rect = self._content_panel_rect()
            self.scrollbar.height = panel_rect.height() - (self.SCROLLBAR_PADDING * 2)
            self.scrollbar.setPos(
                panel_rect.right() - self.scrollbar.width - self.SCROLLBAR_PADDING,
                panel_rect.top() + self.SCROLLBAR_PADDING,
            )
            visible_ratio = self._content_body_rect().height() / self.content_height
            self.scrollbar.set_range(visible_ratio)
        else:
            self.scroll_value = 0
            self.scrollbar.set_value(0)

        self.update()

    def update_font_settings(self, font_family, font_size, color):
        self._setup_document()

    def boundingRect(self):
        return QRectF(-5, -5, self.width + 10, self.height + 10)

    def _content_panel_rect(self):
        return QRectF(
            self.CONTENT_PANEL_MARGIN_X,
            self.CONTENT_PANEL_TOP,
            self.width - (self.CONTENT_PANEL_MARGIN_X * 2),
            self.height - self.CONTENT_PANEL_TOP - self.CONTENT_PANEL_BOTTOM,
        )

    def _content_body_rect(self):
        panel_rect = self._content_panel_rect()
        return panel_rect.adjusted(
            self.CONTENT_PANEL_PADDING_X,
            self.CONTENT_PANEL_PADDING_Y,
            -self.CONTENT_PANEL_PADDING_X,
            -self.CONTENT_PANEL_PADDING_Y,
        )

    def dock(self):
        self.is_docked = True
        self.hide()
        if self.parent_content_node:
            if hasattr(self.parent_content_node, "add_docked_child"):
                self.parent_content_node.add_docked_child(self)
            elif self not in self.parent_content_node.docked_thinking_nodes:
                self.parent_content_node.docked_thinking_nodes.append(self)
                self.parent_content_node.update()
        if self.scene():
            self.scene().update_connections()

    def undock(self):
        self.is_docked = False
        self.show()
        if self.parent_content_node:
            if hasattr(self.parent_content_node, "remove_docked_child"):
                self.parent_content_node.remove_docked_child(self)
            elif self in self.parent_content_node.docked_thinking_nodes:
                self.parent_content_node.docked_thinking_nodes.remove(self)
                self.parent_content_node.update()
        if self.scene():
            self.scene().update_connections()

    def paint(self, painter, option, widget=None):
        palette = get_current_palette()
        node_colors = get_graph_node_colors()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        lod_mode = lod_mode_for_item(self)

        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width, self.height, 10, 10)
        painter.setBrush(QColor(get_surface_color("field")))

        is_dragging = self.scene() and getattr(self.scene(), 'is_rubber_band_dragging', False)

        pen_color = node_colors["border"]
        if self.isSelected() and not is_dragging:
            pen = QPen(node_colors["selected_outline"], 2)
        elif self.hovered:
            pen = QPen(node_colors["hover_outline"], 2)
        else:
            pen = QPen(pen_color, 1)

        if lod_mode != "full":
            draw_lod_card(
                painter,
                QRectF(0, 0, self.width, self.height),
                accent=node_colors["header_start"],
                selection_color=palette.SELECTION,
                title="Assistant Thoughts",
                subtitle="Reasoning trace",
                preview=preview_text(self.thinking_text, fallback="[Empty]"),
                badge="THINK",
                mode=lod_mode,
                selected=self.isSelected() and not is_dragging,
                hovered=self.hovered,
                search_match=self.is_search_match,
            )
            self.dock_button_rect = QRectF()
            return
        painter.setPen(pen)
        painter.drawPath(path)

        header_path = QPainterPath()
        header_rect = QRectF(0, 0, self.width, self.HEADER_HEIGHT)
        header_path.addRoundedRect(header_rect, 10, 10)
        painter.setBrush(node_colors["header_start"])
        painter.drawPath(header_path)

        icon = qta.icon('fa5s.brain', color=get_surface_color("text_soft"))
        icon.paint(painter, QRectF(10, 7, 16, 16).toRect())

        painter.setPen(QColor(get_surface_color("text_soft")))
        font = canvas_font(self.scene(), delta=-1, weight=QFont.Weight.Bold)
        painter.setFont(font)
        title_metrics = QFontMetrics(font)

        self.dock_button_rect = QRectF(self.width - 28, 6, 18, 18)
        title_rect = QRectF(35, 0, max(120, self.dock_button_rect.left() - 43), self.HEADER_HEIGHT)
        title_text = title_metrics.elidedText("Assistant's Thoughts", Qt.TextElideMode.ElideRight, int(title_rect.width()))
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter, title_text)

        button_bg_color = QColor(get_surface_color("handle")) if self.dock_button_hovered else QColor(get_surface_color("divider"))
        painter.setBrush(button_bg_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self.dock_button_rect, 4, 4)
        dock_icon_color = get_surface_color("text_bright") if self.dock_button_hovered else get_surface_color("text_label")
        dock_icon = qta.icon('fa5s.arrow-up', color=dock_icon_color)
        dock_icon.paint(painter, self.dock_button_rect.adjusted(3, 3, -3, -3).toRect())

        panel_rect = self._content_panel_rect()
        painter.setBrush(QColor(get_surface_color("window")))
        painter.setPen(QPen(QColor(get_surface_color("border")), 1))
        painter.drawRoundedRect(panel_rect, 11, 11)

        painter.save()
        clip_rect = self._content_body_rect()
        painter.setClipRect(clip_rect)

        visible_height = clip_rect.height()
        scroll_offset = max(0.0, self.content_height - visible_height) * self.scroll_value
        painter.translate(clip_rect.left(), clip_rect.top() - scroll_offset)

        self.document.drawContents(painter)
        painter.restore()

    def wheelEvent(self, event):
        if not self.scrollbar.isVisible():
            event.ignore()
            return

        delta = event.delta() / 120
        scroll_range = self.content_height - self._content_body_rect().height()
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
        from graphlink_nodes.graphlink_node_thinking_menu import ThinkingNodeContextMenu

        menu = ThinkingNodeContextMenu(self)
        menu.exec(event.screenPos())

    def mousePressEvent(self, event):
        if self.dock_button_hovered and self.dock_button_rect.contains(event.pos()):
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
        was_hovered = self.dock_button_hovered
        self.dock_button_hovered = self.dock_button_rect.contains(event.pos())
        if was_hovered != self.dock_button_hovered:
            self.update()
        super().hoverMoveEvent(event)

    def hoverEnterEvent(self, event):
        self._handle_hover_enter(event)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.dock_button_hovered = False
        self._handle_hover_leave(event)
        super().hoverLeaveEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSceneHasChanged:
            if self.scene():
                self._setup_document()
            else:
                self._stop_hover_animation_timer()
        if change == QGraphicsItem.ItemPositionChange and self.scene():
            parent = self.parentItem()
            if parent and isinstance(parent, Container):
                parent.updateGeometry()
            if self.scene().is_dragging_item:
                return self.scene().snap_position(self, value)
        if change == QGraphicsItem.ItemPositionHasChanged and self.scene():
            self.scene().nodeMoved(self)
        return super().itemChange(change, value)


__all__ = ["ThinkingNode"]
