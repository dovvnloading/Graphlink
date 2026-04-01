import qtawesome as qta
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QTextDocument
from PySide6.QtWidgets import QApplication, QGraphicsItem

from graphite_canvas_items import Container, HoverAnimationMixin
from graphite_config import get_current_palette, get_graph_node_colors, get_semantic_color

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
            return f'<pre style="color: #ffffff; white-space: pre-wrap;">{code}</pre>'
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

    def __init__(self, code, language, parent_content_node, parent=None):
        super().__init__(parent)
        HoverAnimationMixin.__init__(self)
        self.code = code
        self.language = language
        self.parent_content_node = parent_content_node
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.hovered = False
        self.is_search_match = False

        self.highlighter = CodeHighlighter()
        self.document = QTextDocument()
        self.document.setDefaultStyleSheet(self.highlighter.get_stylesheet())
        self.document.setHtml(self.highlighter.highlight(self.code, self.language))

        self.width = 600
        doc_width = self.width - (self.PADDING * 2)
        self.document.setTextWidth(doc_width)

        content_height = self.document.size().height()
        self.height = min(self.MAX_HEIGHT, content_height + self.HEADER_HEIGHT + self.PADDING)

    def boundingRect(self):
        return QRectF(-5, -5, self.width + 10, self.height + 10)

    def paint(self, painter, option, widget=None):
        palette = get_current_palette()
        node_colors = get_graph_node_colors()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width, self.height, 10, 10)

        painter.setBrush(QColor("#1e1e1e"))

        is_dragging = self.scene() and getattr(self.scene(), 'is_rubber_band_dragging', False)

        if self.isSelected() and not is_dragging:
            painter.setPen(QPen(node_colors["selected_outline"], 2))
        elif self.hovered:
            painter.setPen(QPen(node_colors["hover_outline"], 2))
        else:
            painter.setPen(QPen(node_colors["border"], 1))
        painter.drawPath(path)

        if self.is_search_match:
            highlight_pen = QPen(get_semantic_color("search_highlight"), 2.5)
            highlight_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(highlight_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        header_path = QPainterPath()
        header_rect = QRectF(0, 0, self.width, self.HEADER_HEIGHT)
        header_path.addRoundedRect(header_rect, 10, 10)
        painter.setBrush(node_colors["header_start"])
        painter.drawPath(header_path)

        painter.setPen(QColor("#cccccc"))
        font = QFont('Consolas', 9)
        painter.setFont(font)
        painter.drawText(header_rect.adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter, f"Language: {self.language or 'auto-detected'}")

        copy_icon = qta.icon('fa5s.copy', color='#cccccc')
        copy_icon.paint(painter, QRectF(self.width - 28, 7, 16, 16).toRect())

        painter.save()
        painter.translate(self.PADDING, self.HEADER_HEIGHT)
        clip_rect = QRectF(0, 0, self.width - (self.PADDING * 2), self.height - self.HEADER_HEIGHT - self.PADDING)
        painter.setClipRect(clip_rect)
        self.document.drawContents(painter)
        painter.restore()

    def contextMenuEvent(self, event):
        from graphite_nodes.graphite_node_code_menu import CodeNodeContextMenu

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


__all__ = ["CodeHighlighter", "CodeNode"]
