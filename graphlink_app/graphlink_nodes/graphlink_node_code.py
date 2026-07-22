import qtawesome as qta
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QTextDocument
from PySide6.QtWidgets import QApplication, QGraphicsItem

from graphlink_canvas_items import Container, HoverAnimationMixin
from graphlink_config import canvas_font, canvas_font_color, get_current_palette, get_graph_node_colors, get_semantic_color, get_surface_color
from graphlink_lod import draw_lod_card, lod_mode_for_item, preview_text
from graphlink_styles import FONT_FAMILY_NAME
from graphlink_widgets import ScrollBar

try:
    from pygments import highlight
    from pygments.formatters import HtmlFormatter
    from pygments.lexers import get_lexer_by_name, guess_lexer

    PYGMENTS_AVAILABLE = True
except ImportError:
    PYGMENTS_AVAILABLE = False


class CodeHighlighter:
    """A wrapper for the Pygments library to provide syntax highlighting."""

    def __init__(self, style='monokai'):
        if not PYGMENTS_AVAILABLE:
            return
        self.formatter = HtmlFormatter(style=style, nobackground=True, cssclass="code")

    def highlight(self, code, language):
        if not PYGMENTS_AVAILABLE:
            return f'<pre style="color: {get_surface_color("text_bright")}; white-space: pre-wrap;">{code}</pre>'
        try:
            if not language:
                lexer = guess_lexer(code)
            else:
                lexer = get_lexer_by_name(language)
        except Exception:
            lexer = get_lexer_by_name('text')

        return highlight(code, lexer, self.formatter)

    def get_stylesheet(self):
        if not PYGMENTS_AVAILABLE:
            return ""
        return self.formatter.get_style_defs('.code')


class CodeNode(QGraphicsItem, HoverAnimationMixin):
    """A graphical node for displaying formatted code with syntax highlighting."""

    PADDING = 15
    HEADER_HEIGHT = 30
    MAX_HEIGHT = 800
    SCROLLBAR_PADDING = 6

    def __init__(self, code, language, parent_content_node, parent=None):
        super().__init__(parent)
        HoverAnimationMixin.__init__(self)
        self.code = code
        self.language = language
        self.parent_content_node = parent_content_node
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption)
        self.setAcceptHoverEvents(True)
        self.hovered = False
        self.is_search_match = False

        self.highlighter = CodeHighlighter()
        self.document = QTextDocument()
        self.scroll_value = 0.0
        self.content_height = 1.0
        self.scrollbar = ScrollBar(self)
        self.scrollbar.valueChanged.connect(self._on_scroll_value_changed)
        self._set_document_style()

        self.width = 600
        doc_width = self.width - (self.PADDING * 2)
        self.document.setTextWidth(doc_width)

        self._recalculate_geometry()

    def _set_document_style(self):
        scene = self.scene()
        family = getattr(scene, "font_family", FONT_FAMILY_NAME)
        size = max(1, int(getattr(scene, "font_size", 10)))
        color = canvas_font_color(scene, get_surface_color("text_primary")).name()
        stylesheet = self.highlighter.get_stylesheet()
        stylesheet += f" body, pre {{ font-family: '{family}'; font-size: {size}pt; color: {color}; }}"
        self.document.setDefaultStyleSheet(stylesheet)
        self.document.setHtml(self.highlighter.highlight(self.code, self.language))

    def _content_rect(self):
        return QRectF(
            self.PADDING,
            self.HEADER_HEIGHT,
            max(1.0, self.width - (self.PADDING * 2) - self.scrollbar.width - self.SCROLLBAR_PADDING),
            max(1.0, self.height - self.HEADER_HEIGHT - self.PADDING),
        )

    def _recalculate_geometry(self):
        content_width = max(1.0, self.width - (self.PADDING * 2) - self.scrollbar.width - self.SCROLLBAR_PADDING)
        self.document.setTextWidth(content_width)
        new_content_height = max(1.0, self.document.size().height())
        new_height = min(self.MAX_HEIGHT, new_content_height + self.HEADER_HEIGHT + self.PADDING)
        if new_height != getattr(self, "height", None):
            self.prepareGeometryChange()
            self.height = new_height
        self.content_height = new_content_height

        content_rect = self._content_rect()
        is_scrollable = self.content_height > content_rect.height()
        self.scrollbar.setVisible(is_scrollable)
        if is_scrollable:
            self.scrollbar.height = content_rect.height()
            self.scrollbar.setPos(self.width - self.PADDING - self.scrollbar.width, content_rect.top())
            self.scrollbar.set_range(content_rect.height() / self.content_height)
        else:
            self.scroll_value = 0.0
            self.scrollbar.set_value(0.0)
        self.update()

    def _on_scroll_value_changed(self, value):
        self.scroll_value = value
        self.update()

    def update_font_settings(self, font_family, font_size, color):
        self._set_document_style()
        self._recalculate_geometry()

    def boundingRect(self):
        return QRectF(-5, -5, self.width + 10, self.height + 10)

    def paint(self, painter, option, widget=None):
        palette = get_current_palette()
        node_colors = get_graph_node_colors()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        lod_mode = lod_mode_for_item(self)

        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width, self.height, 10, 10)

        painter.setBrush(QColor(get_surface_color("window")))

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
                title="Code",
                subtitle=f"Language: {self.language or 'auto-detected'}",
                preview=preview_text(self.code, fallback="[Empty]"),
                badge="CODE",
                mode=lod_mode,
                selected=self.isSelected() and not is_dragging,
                hovered=self.hovered,
                search_match=self.is_search_match,
            )
            return
        painter.drawPath(path)

        header_path = QPainterPath()
        header_rect = QRectF(0, 0, self.width, self.HEADER_HEIGHT)
        header_path.addRoundedRect(header_rect, 10, 10)
        painter.setBrush(node_colors["header_start"])
        painter.drawPath(header_path)

        painter.setPen(QColor(get_surface_color("text_soft")))
        font = canvas_font(self.scene(), delta=-1)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        label_rect = QRectF(10, 0, self.width - 48, self.HEADER_HEIGHT)
        label_text = metrics.elidedText(
            f"Language: {self.language or 'auto-detected'}",
            Qt.TextElideMode.ElideRight,
            int(label_rect.width()),
        )
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter, label_text)

        copy_icon = qta.icon('fa5s.copy', color=get_surface_color("text_soft"))
        copy_icon.paint(painter, QRectF(self.width - 28, 7, 16, 16).toRect())

        painter.save()
        content_rect = self._content_rect()
        painter.setClipRect(content_rect)
        scroll_offset = max(0.0, self.content_height - content_rect.height()) * self.scroll_value
        painter.translate(content_rect.left(), content_rect.top() - scroll_offset)
        self.document.drawContents(painter)
        painter.restore()

        if self.scrollbar.isVisible():
            self.scrollbar.height = content_rect.height()
            self.scrollbar.setPos(self.width - self.PADDING - self.scrollbar.width, content_rect.top())

        # Drawn LAST, on top of the header and content, so the search ring is
        # exactly the node's outline. Drawing it right after the body path
        # (as this did previously) left the 2.5px search pen active for the
        # header drawPath, painting a spurious full-width tinted line under the
        # header. Matches ChatNode/ImageNode's ring-last ordering.
        if self.is_search_match:
            highlight_pen = QPen(get_semantic_color("search_highlight"), 2.5)
            highlight_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(highlight_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

    def contextMenuEvent(self, event):
        from graphlink_nodes.graphlink_node_code_menu import CodeNodeContextMenu

        menu = CodeNodeContextMenu(self)
        menu.exec(event.screenPos())

    def mousePressEvent(self, event):
        copy_rect = QRectF(self.width - 32, 4, 24, 24)
        if copy_rect.contains(event.pos()):
            QApplication.clipboard().setText(self.code)
            main_window = self.scene().window if self.scene() else None
            if main_window and hasattr(main_window, 'notification_banner'):
                main_window.notification_banner.show_message("Code copied to clipboard.", 3000, "success")
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

    def wheelEvent(self, event):
        if self.scrollbar.isVisible() and self.content_height > self._content_rect().height():
            delta = event.delta() / 120.0
            step = min(0.25, 48.0 / max(1.0, self.content_height - self._content_rect().height()))
            self.scrollbar.set_value(self.scrollbar.value - delta * step)
            event.accept()
            return
        super().wheelEvent(event)

    def hoverEnterEvent(self, event):
        self._handle_hover_enter(event)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._handle_hover_leave(event)
        super().hoverLeaveEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSceneHasChanged and value is None:
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


__all__ = ["CodeHighlighter", "CodeNode"]
