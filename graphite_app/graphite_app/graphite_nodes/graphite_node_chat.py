import markdown
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QTextDocument,
)
from PySide6.QtWidgets import QGraphicsItem

from graphite_canvas_items import Container, Frame, HoverAnimationMixin
from graphite_config import get_current_palette, get_semantic_color, is_monochrome_theme
from graphite_widgets import ScrollBar


class ChatNode(QGraphicsItem, HoverAnimationMixin):
    DEFAULT_WIDTH = 420
    MIN_HEIGHT = 110
    MAX_HEIGHT = 640
    PADDING = 15
    HEADER_HEIGHT = 34
    COLLAPSED_WIDTH = 290
    COLLAPSED_HEIGHT = 58
    SCROLLBAR_PADDING = 6
    CONTROL_GUTTER = 34
    CONNECTION_DOT_RADIUS = 5
    CONNECTION_DOT_OFFSET = 0
    BORDER_RADIUS = 12

    def __init__(self, text, is_user=True, parent=None):
        super().__init__(parent)
        HoverAnimationMixin.__init__(self)
        self.raw_content = text
        self.is_user = is_user
        self.children = []
        self.parent_node = None
        self.incoming_connection = None
        self.conversation_history = []
        self.docked_thinking_nodes = []
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.hovered = False

        self.width = self.DEFAULT_WIDTH
        self.height = self.MIN_HEIGHT
        self.content_height = 0

        self.is_collapsed = False
        self.collapse_button_rect = QRectF()

        self.scroll_value = 0
        self.scrollbar = ScrollBar(self)
        self.scrollbar.width = 8
        self.scrollbar.valueChanged.connect(self.update_scroll_position)

        self.document = QTextDocument()
        self.document.setDocumentMargin(0)
        self._setup_document()

        self.is_dimmed = False
        self.is_search_match = False
        self.is_last_navigated = False

    @property
    def text(self):
        if isinstance(self.raw_content, str):
            return self.raw_content

        text_parts = []
        if isinstance(self.raw_content, list):
            for part in self.raw_content:
                if isinstance(part, dict) and part.get('type') == 'text':
                    text_parts.append(part.get('text', ''))
                elif isinstance(part, str):
                    text_parts.append(part)

        return "\n".join(part for part in text_parts if part)

    def _current_dimensions(self):
        if self.is_collapsed:
            return self.COLLAPSED_WIDTH, self.COLLAPSED_HEIGHT
        return self.width, self.height

    def _role_label(self):
        return "You" if self.is_user else "Assistant"

    def _role_descriptor(self):
        return "Prompt" if self.is_user else "Response"

    def _role_accent(self):
        palette = get_current_palette()
        return QColor(palette.USER_NODE if self.is_user else palette.AI_NODE)

    def _mix_color(self, base, accent, ratio):
        base_color = QColor(base)
        accent_color = QColor(accent)
        mix = max(0.0, min(1.0, float(ratio)))
        return QColor(
            round(base_color.red() + (accent_color.red() - base_color.red()) * mix),
            round(base_color.green() + (accent_color.green() - base_color.green()) * mix),
            round(base_color.blue() + (accent_color.blue() - base_color.blue()) * mix),
        )

    def _surface_colors(self):
        accent = self._role_accent()
        monochrome = is_monochrome_theme()

        body_start = self._mix_color(QColor("#25282d"), accent, 0.04 if not monochrome else 0.02)
        body_end = self._mix_color(QColor("#171a1f"), accent, 0.02 if not monochrome else 0.01)
        header_start = self._mix_color(QColor("#2c333b"), accent, 0.30 if not monochrome else 0.08)
        header_end = self._mix_color(QColor("#181c22"), accent, 0.18 if not monochrome else 0.04)
        badge_fill = self._mix_color(QColor("#262c33"), accent, 0.58 if not monochrome else 0.12)
        badge_text = QColor("#f3fff8") if self.is_user else QColor("#eef7ff")
        descriptor_text = self._mix_color(QColor("#9ca6b0"), accent, 0.14 if not monochrome else 0.04)
        content_panel_fill = QColor("#111417")
        content_panel_border = self._mix_color(QColor("#343a42"), accent, 0.08 if not monochrome else 0.03)

        return {
            "accent": accent,
            "body_start": body_start,
            "body_end": body_end,
            "header_start": header_start,
            "header_end": header_end,
            "badge_fill": badge_fill,
            "badge_text": badge_text,
            "descriptor_text": descriptor_text,
            "content_panel_fill": content_panel_fill,
            "content_panel_border": content_panel_border,
        }

    def _get_default_stylesheet(self, color, font_family, font_size):
        base_text = QColor(color)
        accent = self._role_accent()
        muted_text = self._mix_color(base_text, QColor("#c7d0d9"), 0.18 if not is_monochrome_theme() else 0.04)
        link_color = accent.lighter(125)
        quote_border = self._mix_color(QColor("#4b5560"), accent, 0.55 if not is_monochrome_theme() else 0.10)
        code_bg = self._mix_color(QColor("#111418"), accent, 0.10 if not is_monochrome_theme() else 0.03)
        table_border = self._mix_color(QColor("#39414a"), accent, 0.26 if not is_monochrome_theme() else 0.06)

        return f"""
            body {{
                color: {base_text.name()};
                font-family: '{font_family}';
                font-size: {font_size}pt;
                margin: 0;
                padding: 0;
                background: transparent;
            }}
            p {{
                margin-top: 0;
                margin-bottom: 0.55em;
                color: {base_text.name()};
            }}
            ul, ol {{
                margin-top: 0;
                margin-bottom: 0.65em;
                padding-left: 22px;
                color: {base_text.name()};
            }}
            li {{
                margin-bottom: 0.2em;
            }}
            h1, h2, h3, h4, h5, h6 {{
                color: #ffffff;
                font-family: '{font_family}';
                font-weight: bold;
                margin-top: 0.2em;
                margin-bottom: 0.35em;
            }}
            h1 {{ font-size: {font_size + 4}pt; }}
            h2 {{ font-size: {font_size + 3}pt; }}
            h3 {{ font-size: {font_size + 2}pt; }}
            pre {{
                background: {code_bg.name()};
                border: 1px solid {table_border.name()};
                border-radius: 8px;
                padding: 10px 12px;
                margin: 0.35em 0 0.8em 0;
                color: {base_text.name()};
                white-space: pre-wrap;
            }}
            code {{
                background: {code_bg.name()};
                color: {base_text.name()};
                border-radius: 4px;
                padding: 1px 4px;
            }}
            pre code {{
                background: transparent;
                padding: 0;
            }}
            blockquote {{
                border-left: 3px solid {quote_border.name()};
                padding-left: 10px;
                margin: 0.4em 0 0.75em 0;
                color: {muted_text.name()};
            }}
            a {{
                color: {link_color.name()};
                text-decoration: none;
            }}
            hr {{
                border: none;
                border-top: 1px solid {table_border.name()};
                height: 1px;
                margin: 0.75em 0;
            }}
            table {{
                border-collapse: collapse;
                width: 100%;
                margin: 0.4em 0 0.8em 0;
            }}
            th, td {{
                border: 1px solid {table_border.name()};
                padding: 6px 8px;
                color: {base_text.name()};
            }}
            th {{
                background: {code_bg.name()};
                font-weight: bold;
            }}
        """

    def _setup_document(self):
        font_family = "Segoe UI"
        font_size = 10
        color = "#dddddd"

        if self.scene():
            font_family = self.scene().font_family
            font_size = self.scene().font_size
            color = self.scene().font_color.name()

        self.document.setDefaultStyleSheet(self._get_default_stylesheet(color, font_family, font_size))
        source_text = self.text.strip()
        html = markdown.markdown(source_text, extensions=['fenced_code', 'tables', 'nl2br', 'sane_lists']) if source_text else "<p>[Empty]</p>"
        self.document.setHtml(html)
        self._recalculate_geometry()

    def _visible_content_height(self):
        return max(1.0, self.height - self.HEADER_HEIGHT - (self.PADDING * 2))

    def _recalculate_geometry(self):
        self.prepareGeometryChange()

        available_width = self.width - (self.PADDING * 2) - self.CONTROL_GUTTER
        self.document.setTextWidth(max(120, available_width))

        self.content_height = max(1.0, self.document.size().height())
        total_required_height = self.content_height + self.HEADER_HEIGHT + (self.PADDING * 2)
        self.height = max(self.MIN_HEIGHT, min(self.MAX_HEIGHT, total_required_height))

        visible_content_height = self._visible_content_height()
        is_scrollable = self.content_height > visible_content_height + 1
        self.scrollbar.setVisible(is_scrollable)

        self.scrollbar.height = max(0, self.height - self.HEADER_HEIGHT - (self.SCROLLBAR_PADDING * 2))
        self.scrollbar.setPos(
            self.width - self.scrollbar.width - self.SCROLLBAR_PADDING,
            self.HEADER_HEIGHT + self.SCROLLBAR_PADDING,
        )

        visible_ratio = min(1.0, visible_content_height / self.content_height) if self.content_height > 0 else 1.0
        self.scrollbar.set_range(visible_ratio)

        max_scroll_distance = max(0.0, self.content_height - visible_content_height)
        if max_scroll_distance <= 0:
            self.scroll_value = 0
        else:
            self.scroll_value = max(0.0, min(1.0, self.scroll_value))
        self.scrollbar.set_value(self.scroll_value)
        self.update()

    def boundingRect(self):
        current_width, current_height = self._current_dimensions()
        padding = self.CONNECTION_DOT_OFFSET + self.CONNECTION_DOT_RADIUS + 1
        return QRectF(-padding, -5, current_width + 10 + (2 * padding), current_height + 10)

    def _update_geometry_for_state(self):
        if self.is_collapsed:
            self.prepareGeometryChange()
            self.width = self.COLLAPSED_WIDTH
            self.height = self.COLLAPSED_HEIGHT
            self.scrollbar.setVisible(False)
            self.scroll_value = 0
        else:
            self.width = self.DEFAULT_WIDTH
            self._recalculate_geometry()

        scene = self.scene()
        if scene:
            scene.update_connections()
        parent = self.parentItem()
        if parent and isinstance(parent, (Frame, Container)):
            parent.updateGeometry()
        self.update()

    def set_collapsed(self, collapsed):
        if self.is_collapsed != collapsed:
            self.is_collapsed = collapsed
            self._update_geometry_for_state()

    def toggle_collapse(self):
        self.set_collapsed(not self.is_collapsed)

    def update_font_settings(self, font_family, font_size, color):
        self._setup_document()

    def show_context_menu(self, screen_pos=None):
        from graphite_nodes.graphite_node_chat_menu import ChatNodeContextMenu

        menu = ChatNodeContextMenu(self)
        menu.exec(screen_pos or QCursor.pos())

    def contextMenuEvent(self, event):
        self.show_context_menu(event.screenPos() if event else None)

    def _paint_header(self, painter, current_width):
        colors = self._surface_colors()

        header_rect = QRectF(0, 0, current_width, self.HEADER_HEIGHT)
        corner_radius = min(self.BORDER_RADIUS, self.HEADER_HEIGHT, current_width / 2)
        header_path = QPainterPath()
        header_path.moveTo(header_rect.left(), header_rect.bottom())
        header_path.lineTo(header_rect.left(), header_rect.top() + corner_radius)
        header_path.quadTo(header_rect.left(), header_rect.top(), header_rect.left() + corner_radius, header_rect.top())
        header_path.lineTo(header_rect.right() - corner_radius, header_rect.top())
        header_path.quadTo(header_rect.right(), header_rect.top(), header_rect.right(), header_rect.top() + corner_radius)
        header_path.lineTo(header_rect.right(), header_rect.bottom())
        header_path.closeSubpath()

        header_gradient = QLinearGradient(QPointF(0, 0), QPointF(0, self.HEADER_HEIGHT))
        header_gradient.setColorAt(0, colors["header_start"])
        header_gradient.setColorAt(1, colors["header_end"])
        painter.setBrush(QBrush(header_gradient))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(header_path)

        painter.setPen(QPen(colors["content_panel_border"], 1))
        painter.drawLine(10, self.HEADER_HEIGHT, current_width - 10, self.HEADER_HEIGHT)

        font_family = self.scene().font_family if self.scene() else "Segoe UI"
        badge_font = QFont(font_family, 8, QFont.Weight.DemiBold)
        painter.setFont(badge_font)
        badge_metrics = QFontMetrics(badge_font)
        badge_text = self._role_label()
        badge_width = badge_metrics.horizontalAdvance(badge_text) + 18
        badge_rect = QRectF(12, 8, badge_width, 18)

        painter.setBrush(colors["badge_fill"])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(badge_rect, 9, 9)

        painter.setPen(colors["badge_text"])
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge_text)

        descriptor_font = QFont(font_family, 8)
        painter.setFont(descriptor_font)
        painter.setPen(colors["descriptor_text"])
        painter.drawText(
            QRectF(badge_rect.right() + 10, 0, current_width - badge_rect.right() - 60, self.HEADER_HEIGHT),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._role_descriptor(),
        )

    def _paint_collapse_button(self, painter, button_x):
        self.collapse_button_rect = QRectF(button_x, 8, 18, 18)
        painter.setBrush(QColor(255, 255, 255, 28))
        painter.setPen(QColor(255, 255, 255, 110))
        painter.drawRoundedRect(self.collapse_button_rect, 4, 4)

        icon_pen = QPen(QColor("#ffffff"), 1.8)
        painter.setPen(icon_pen)
        center = self.collapse_button_rect.center()
        painter.drawLine(int(center.x() - 4), int(center.y()), int(center.x() + 4), int(center.y()))
        if self.is_collapsed:
            painter.drawLine(int(center.x()), int(center.y() - 4), int(center.x()), int(center.y() + 4))

    def paint(self, painter, option, widget=None):
        palette = get_current_palette()
        colors = self._surface_colors()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        current_width, current_height = self._current_dimensions()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 34))
        shadow_path = QPainterPath()
        shadow_path.addRoundedRect(3, 4, current_width, current_height, self.BORDER_RADIUS, self.BORDER_RADIUS)
        painter.drawPath(shadow_path)

        path = QPainterPath()
        path.addRoundedRect(0, 0, current_width, current_height, self.BORDER_RADIUS, self.BORDER_RADIUS)

        gradient = QLinearGradient(QPointF(0, 0), QPointF(0, current_height))
        gradient.setColorAt(0, colors["body_start"])
        gradient.setColorAt(1, colors["body_end"])
        painter.setBrush(QBrush(gradient))

        is_dragging = self.scene() and getattr(self.scene(), 'is_rubber_band_dragging', False)
        pen = QPen(colors["accent"].lighter(105), 1.4)
        if self.isSelected() and not is_dragging:
            pen = QPen(palette.SELECTION, 2.2)
        elif self.hovered:
            pen = QPen(QColor("#ffffff"), 2)

        painter.setPen(pen)
        painter.drawPath(path)

        painter.save()
        painter.setClipPath(path)
        accent_fill = colors["accent"]
        accent_fill.setAlpha(160)
        painter.setBrush(accent_fill)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(QRectF(0, 0, 5, current_height))
        painter.restore()

        painter.setBrush(colors["accent"])
        painter.setPen(Qt.PenStyle.NoPen)

        dot_rect_left = QRectF(
            -self.CONNECTION_DOT_RADIUS,
            (current_height / 2) - self.CONNECTION_DOT_RADIUS,
            self.CONNECTION_DOT_RADIUS * 2,
            self.CONNECTION_DOT_RADIUS * 2,
        )
        painter.drawPie(dot_rect_left, 90 * 16, -180 * 16)

        dot_rect_right = QRectF(
            current_width - self.CONNECTION_DOT_RADIUS,
            (current_height / 2) - self.CONNECTION_DOT_RADIUS,
            self.CONNECTION_DOT_RADIUS * 2,
            self.CONNECTION_DOT_RADIUS * 2,
        )
        painter.drawPie(dot_rect_right, 90 * 16, 180 * 16)

        self._paint_header(painter, current_width)

        if self.docked_thinking_nodes:
            indicator_color = QColor("#95a5a6").lighter(130)
            painter.setBrush(indicator_color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF((current_width / 2) - 4, 6, 8, 8))

        if self.hovered:
            button_x = self.width - 26 if not self.is_collapsed else current_width - 26
            if not self.is_collapsed and self.scrollbar.isVisible():
                button_x = self.scrollbar.pos().x() - 24
            self._paint_collapse_button(painter, button_x)
        else:
            self.collapse_button_rect = QRectF()

        if self.is_last_navigated:
            highlight_pen = QPen(palette.NAV_HIGHLIGHT, 2.5, Qt.PenStyle.DashLine)
            highlight_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(highlight_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        if self.is_search_match:
            highlight_pen = QPen(get_semantic_color("search_highlight"), 2.5)
            highlight_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(highlight_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        if self.is_collapsed:
            font_family = self.scene().font_family if self.scene() else "Segoe UI"
            snippet_font = QFont(font_family, 9)
            painter.setFont(snippet_font)
            painter.setPen(QColor("#f0f3f6"))
            metrics = QFontMetrics(snippet_font)
            text_to_show = self.text.split('\n')[0].strip() or "[Empty]"
            elided_text = metrics.elidedText(text_to_show, Qt.TextElideMode.ElideRight, current_width - 26)
            painter.drawText(
                QRectF(12, self.HEADER_HEIGHT - 1, current_width - 24, current_height - self.HEADER_HEIGHT - 6),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                elided_text,
            )
        else:
            painter.save()

            content_area_width = self.width - (self.PADDING * 2) - self.CONTROL_GUTTER
            content_rect = QRectF(
                self.PADDING - 2,
                self.HEADER_HEIGHT + 6,
                content_area_width + 4,
                max(20, self.height - self.HEADER_HEIGHT - 12),
            )
            painter.setBrush(colors["content_panel_fill"])
            painter.setPen(QPen(colors["content_panel_border"], 1))
            painter.drawRoundedRect(content_rect, 10, 10)

            clip_rect = QRectF(self.PADDING, self.HEADER_HEIGHT + self.PADDING, content_area_width, self._visible_content_height())
            painter.setClipRect(clip_rect)

            max_scroll_distance = max(0.0, self.content_height - self._visible_content_height())
            scroll_offset = max_scroll_distance * self.scroll_value
            painter.translate(self.PADDING, self.HEADER_HEIGHT + self.PADDING - scroll_offset)
            self.document.drawContents(painter)
            painter.restore()

        if self.is_dimmed:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 96))
            painter.drawRoundedRect(0, 0, current_width, current_height, self.BORDER_RADIUS, self.BORDER_RADIUS)

    def wheelEvent(self, event):
        if self.is_collapsed or not self.scrollbar.isVisible():
            event.ignore()
            return

        delta = event.delta() / 120
        new_value = max(0.0, min(1.0, self.scroll_value - (delta * 0.1)))
        if new_value != self.scroll_value:
            self.scroll_value = new_value
            self.scrollbar.set_value(new_value)
            self.update()
        event.accept()

    def update_scroll_position(self, value):
        clamped_value = max(0.0, min(1.0, value))
        if self.scroll_value != clamped_value:
            self.scroll_value = clamped_value
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.hovered and self.collapse_button_rect.contains(event.pos()):
            self.toggle_collapse()
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            scene = self.scene()
            if scene:
                if hasattr(scene, 'window'):
                    scene.window.setCurrentNode(self)
                scene.is_dragging_item = True
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
            self.scene().nodeMoved(self)

            parent = self.parentItem()
            if parent and isinstance(parent, Container):
                parent.updateGeometry()

            if self.scene().is_dragging_item:
                return self.scene().snap_position(self, value)
        return super().itemChange(change, value)

    def update_content(self, new_content):
        self.raw_content = new_content
        self._setup_document()
        scene = self.scene()
        if scene:
            scene.update_connections()
        parent = self.parentItem()
        if parent and isinstance(parent, (Frame, Container)):
            parent.updateGeometry()
        self.update()


__all__ = ["ChatNode"]
