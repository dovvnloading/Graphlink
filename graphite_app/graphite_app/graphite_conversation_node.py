from PySide6.QtWidgets import (
    QGraphicsObject, QGraphicsProxyWidget, QWidget, QVBoxLayout,
    QLineEdit, QPushButton, QHBoxLayout, QLabel, QGraphicsView, QGraphicsScene,
    QMenu, QApplication
)
from PySide6.QtCore import QTimer, Qt, Signal, QRectF, QPointF, QDateTime, QRect
from PySide6.QtGui import QPainter, QColor, QBrush, QPen, QPainterPath, QTextDocument, QAction, QCursor, QFont
import qtawesome as qta
import markdown
from graphite_config import get_current_palette, get_graph_node_colors, get_neutral_button_colors, get_semantic_color
from graphite_canvas_items import HoverAnimationMixin
from graphite_lod import draw_lod_card, preview_text, sync_proxy_render_state
from graphite_plugin_context_menu import PluginNodeContextMenu

class ChatMessageBubbleItem(QGraphicsObject):
    """
    An enhanced Chat Bubble supporting Markdown, Timestamps, 
    and context-menu interactions for Copying and Pruning.
    """
    deleted = Signal(object) # Emits self when the message is deleted

    def __init__(self, text, is_user, timestamp=None, parent=None):
        super().__init__(parent)
        self.raw_text = text
        self.is_user = is_user
        self.is_search_match = False
        self.timestamp = timestamp or QDateTime.currentDateTime().toString("hh:mm AP")
        
        self.document = QTextDocument()
        palette = get_current_palette()
        self.document.setDefaultStyleSheet("""
            p, ul, ol, li, blockquote { color: #e0e0e0; margin: 0; font-family: 'Segoe UI'; font-size: 12px; }
            pre { background-color: #1e1e1e; padding: 8px; border-radius: 4px; white-space: pre-wrap; font-family: Consolas, monospace; }
            a { color: %s; }
            .timestamp { color: #666666; font-size: 9px; }
        """ % palette.AI_NODE.name())
        
        html_content = markdown.markdown(text, extensions=['fenced_code', 'tables'])
        # Append timestamp to the HTML
        meta_html = f"<div style='text-align: {'right' if is_user else 'left'};' class='timestamp'>{self.timestamp}</div>"
        self.document.setHtml(html_content + meta_html)

        MAX_BUBBLE_WIDTH = (ConversationNode.NODE_WIDTH - 80) * 0.85
        padding = 12
        self.document.setTextWidth(MAX_BUBBLE_WIDTH - (2 * padding))

        self.width = self.document.size().width() + (2 * padding)
        self.height = self.document.size().height() + (2 * padding)

    def boundingRect(self):
        return QRectF(0, 0, self.width, self.height)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self.boundingRect(), 12, 12)
        
        user_bubble_color = get_semantic_color("conversation_user_bubble")
        ai_bubble_color = get_semantic_color("conversation_ai_bubble")
        
        painter.setBrush(user_bubble_color if self.is_user else ai_bubble_color)
        painter.setPen(QPen(QColor(255,255,255,20), 1))
        painter.drawPath(path)

        if self.is_search_match:
            painter.setPen(QPen(get_semantic_color("search_highlight"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
            
        painter.save()
        painter.translate(12, 12)
        self.document.drawContents(painter)
        painter.restore()

    def contextMenuEvent(self, event):
        menu = QMenu()
        copy_action = QAction(qta.icon('fa5s.copy', color='white'), "Copy Message", menu)
        copy_action.triggered.connect(self._copy_to_clipboard)
        
        delete_action = QAction(qta.icon('fa5s.trash-alt', color='#e74c3c'), "Delete from History", menu)
        delete_action.triggered.connect(lambda: self.deleted.emit(self))
        
        menu.addAction(copy_action)
        menu.addSeparator()
        menu.addAction(delete_action)
        # Use QCursor.pos() for reliable mapping in complex graphics scenes
        menu.exec(QCursor.pos())

    def _copy_to_clipboard(self):
        QApplication.clipboard().setText(self.raw_text)


class TypingIndicatorItem(QGraphicsObject):
    """A SOTA visual indicator showing the AI is currently generating a response."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._dot_opacity = [1.0, 1.0, 1.0]
        self._timer = QTimer()
        self._timer.timeout.connect(self._animate)
        self._timer.start(300)
        self._counter = 0

    def _animate(self):
        self._counter = (self._counter + 1) % 4
        for i in range(3):
            self._dot_opacity[i] = 1.0 if self._counter == i + 1 else 0.3
        self.update()

    def boundingRect(self):
        return QRectF(0, 0, 60, 30)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self.boundingRect(), 15, 15)
        painter.setBrush(QColor("#323232"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)

        for i in range(3):
            color = QColor(255, 255, 255)
            color.setAlphaF(self._dot_opacity[i])
            painter.setBrush(color)
            painter.drawEllipse(15 + (i * 12), 12, 6, 6)


class ConversationNode(QGraphicsObject, HoverAnimationMixin):
    ai_request_sent = Signal(object, list)

    NODE_WIDTH = 550
    NODE_HEIGHT = 600
    COLLAPSED_WIDTH = 250
    COLLAPSED_HEIGHT = 40
    CONNECTION_DOT_RADIUS = 5
    CONNECTION_DOT_OFFSET = 0

    def __init__(self, parent_node, parent=None):
        super().__init__(parent)
        HoverAnimationMixin.__init__(self)
        self.parent_node = parent_node
        self.children = []
        self.conversation_history = []
        self.is_user = False
        
        self.is_collapsed = False
        self.collapse_button_rect = QRectF()

        self.setFlag(QGraphicsObject.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsObject.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsObject.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setFlag(QGraphicsObject.GraphicsItemFlag.ItemUsesExtendedStyleOption)
        self.setAcceptHoverEvents(True)
        self.hovered = False
        self._render_lod_mode = "full"

        self.widget = QWidget()
        self.widget.setObjectName("conversationMainWidget")
        self.widget.setFixedSize(self.NODE_WIDTH, self.NODE_HEIGHT)
        self.widget.setStyleSheet("""
            QWidget#conversationMainWidget { background-color: transparent; color: #e0e0e0; }
            QWidget#conversationMainWidget QLabel { background-color: transparent; }
        """)

        self._message_items = []
        self._next_message_y = 10
        self._typing_indicator = None

        self._setup_ui()
        self.proxy = QGraphicsProxyWidget(self)
        self.proxy.setWidget(self.widget)

    @property
    def width(self):
        return self.COLLAPSED_WIDTH if self.is_collapsed else self.NODE_WIDTH

    @property
    def height(self):
        return self.COLLAPSED_HEIGHT if self.is_collapsed else self.NODE_HEIGHT

    def set_collapsed(self, collapsed):
        if self.is_collapsed != collapsed:
            self.is_collapsed = collapsed
            self.proxy.setVisible(not self.is_collapsed and self._render_lod_mode == "full")
            self.prepareGeometryChange()
            if self.scene():
                self.sync_view_lod()
                self.scene().update_connections()
                self.scene().nodeMoved(self)
            self.update()

    def toggle_collapse(self):
        self.set_collapsed(not self.is_collapsed)

    def sync_view_lod(self, view_rect=None, zoom=None):
        sync_proxy_render_state(self, view_rect, zoom)
        if not self.is_collapsed:
            self.update()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self.widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)
        
        node_colors = get_graph_node_colors()
        node_color = node_colors["header"]

        header_layout = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(qta.icon('fa5s.comments', color=node_color).pixmap(18, 18))
        header_layout.addWidget(icon)
        title_label = QLabel("Conversation")
        title_label.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {node_color.name()}; background: transparent;")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)

        self.internal_scene = QGraphicsScene()
        self.internal_view = QGraphicsView(self.internal_scene)
        self.internal_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.internal_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.internal_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.internal_view.setStyleSheet("background-color: #1a1a1a; border: 1px solid #333; border-radius: 6px;")
        main_layout.addWidget(self.internal_view)

        input_layout = QHBoxLayout()
        input_layout.setSpacing(8)
        self.message_input = QLineEdit()
        self.message_input.setPlaceholderText("Type a message...")
        self.message_input.returnPressed.connect(self.send_message)
        
        self.send_button = QPushButton()
        self.send_button.setFixedSize(36, 36)
        
        input_layout.addWidget(self.message_input)
        input_layout.addWidget(self.send_button)
        main_layout.addLayout(input_layout)

        self.send_button.clicked.connect(self.send_message)
        self._update_button_style()

    def _update_button_style(self):
        button_colors = get_neutral_button_colors()
        self.send_button.setIcon(qta.icon('fa5s.paper-plane', color=button_colors["icon"].name()))
        self.send_button.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {button_colors["background"].name()};
                border: 1px solid {button_colors["border"].name()};
                border-radius: 18px;
            }}
            QPushButton:hover {{
                background-color: {button_colors["hover"].name()};
                border-color: {button_colors["hover"].lighter(112).name()};
            }}
            QPushButton:pressed {{
                background-color: {button_colors["pressed"].name()};
                border-color: {button_colors["border"].darker(105).name()};
            }}
            """
        )

    def _add_bubble(self, text, is_user):
        bubble_item = ChatMessageBubbleItem(text, is_user)
        bubble_item.deleted.connect(self._remove_message)
        
        if is_user:
            x_pos = self.internal_view.width() - bubble_item.width - 25
        else:
            x_pos = 10
            
        bubble_item.setPos(x_pos, self._next_message_y)
        self.internal_scene.addItem(bubble_item)
        self._message_items.append(bubble_item)
        
        self._next_message_y += bubble_item.height + 12
        self._update_internal_scene_rect()
        QTimer.singleShot(10, lambda: self.internal_view.verticalScrollBar().setValue(self.internal_view.verticalScrollBar().maximum()))

    def _remove_message(self, bubble_item):
        """SOTA Ability: Prune conversation history by deleting specific bubbles."""
        try:
            idx = self._message_items.index(bubble_item)
            # Remove from logic history
            if idx < len(self.conversation_history):
                self.conversation_history.pop(idx)
            
            # Remove from visual list
            self._message_items.pop(idx)
            self.internal_scene.removeItem(bubble_item)
            
            # Re-layout remaining items
            self._next_message_y = 10
            for item in self._message_items:
                if item.is_user:
                    x = self.internal_view.width() - item.width - 25
                else:
                    x = 10
                item.setPos(x, self._next_message_y)
                self._next_message_y += item.height + 12
            
            self._update_internal_scene_rect()
            bubble_item.deleteLater()
        except ValueError:
            pass

    def set_typing(self, is_typing):
        """Toggles the typing indicator visibility."""
        if is_typing:
            if not self._typing_indicator:
                self._typing_indicator = TypingIndicatorItem()
                self.internal_scene.addItem(self._typing_indicator)
            self._typing_indicator.setPos(10, self._next_message_y)
            self._typing_indicator.show()
        elif self._typing_indicator:
            self._typing_indicator.hide()

    def _update_internal_scene_rect(self):
        self.internal_scene.setSceneRect(0, 0, self.internal_view.width() - 20, max(self.internal_view.height(), self._next_message_y + 50))

    def send_message(self):
        text = self.message_input.text().strip()
        if not text: return
        self.add_user_message(text)
        self.ai_request_sent.emit(self, self.conversation_history)
        self.message_input.clear()
        self.set_input_enabled(False)

    def add_user_message(self, text: str):
        self._add_bubble(text, True)
        self.conversation_history.append({'role': 'user', 'content': text})

    def add_ai_message(self, text: str):
        self._add_bubble(text, False)
        self.conversation_history.append({'role': 'assistant', 'content': text})
        self.set_input_enabled(True)

    def set_input_enabled(self, enabled: bool):
        self.message_input.setEnabled(enabled)
        self.send_button.setEnabled(enabled)
        self.set_typing(not enabled)
        if enabled: self.message_input.setFocus()
    
    def set_history(self, history: list):
        self.internal_scene.clear()
        self._message_items.clear()
        self._next_message_y = 10
        self.conversation_history = []
        for message in history:
            role = message.get('role')
            content = message.get('content', '')
            if role == 'user': self.add_user_message(content)
            elif role == 'assistant': self.add_ai_message(content)
        if self.conversation_history and self.conversation_history[-1]['role'] == 'assistant':
            self.conversation_history.pop()

    def update_search_highlight(self, search_text):
        if not search_text: search_text = ""
        search_text = search_text.lower()
        found_item = None
        for item in self._message_items:
            is_match = search_text and search_text in item.raw_text.lower()
            if item.is_search_match != is_match:
                item.is_search_match = is_match
                item.update()
            if is_match and not found_item: found_item = item
        if found_item: self.internal_view.ensureVisible(found_item, 50, 50)

    def boundingRect(self):
        padding = self.CONNECTION_DOT_RADIUS + self.CONNECTION_DOT_OFFSET
        return QRectF(-padding, 0, self.width + 2 * padding, self.height)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = get_current_palette()
        node_colors = get_graph_node_colors()
        render_mode = getattr(self, "_render_lod_mode", "full")
        
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width, self.height, 10, 10)
        painter.setBrush(QColor("#2d2d2d"))
        
        node_color = node_colors["border"]
        pen = QPen(node_color, 1.5)
        
        if self.isSelected(): pen = QPen(palette.SELECTION, 2)
        elif self.hovered: pen = QPen(QColor("#ffffff"), 2)
        
        painter.setPen(pen)
        painter.drawPath(path)
        
        dot_color = node_colors["dot"]
        if self.isSelected() or self.hovered:
            dot_color = pen.color().lighter(110) if self.isSelected() else node_colors["hover_dot"]
        painter.setBrush(dot_color)
        painter.setPen(Qt.PenStyle.NoPen)
        
        dot_rect_left = QRectF(-self.CONNECTION_DOT_RADIUS, (self.height / 2) - self.CONNECTION_DOT_RADIUS, self.CONNECTION_DOT_RADIUS * 2, self.CONNECTION_DOT_RADIUS * 2)
        painter.drawPie(dot_rect_left, 90 * 16, -180 * 16)
        
        dot_rect_right = QRectF(self.width - self.CONNECTION_DOT_RADIUS, (self.height / 2) - self.CONNECTION_DOT_RADIUS, self.CONNECTION_DOT_RADIUS * 2, self.CONNECTION_DOT_RADIUS * 2)
        painter.drawPie(dot_rect_right, 90 * 16, 180 * 16)

        if not self.is_collapsed and render_mode != "full":
            latest_message = ""
            if self.conversation_history:
                latest_message = self.conversation_history[-1].get("content", "")
            self.collapse_button_rect = QRectF()
            draw_lod_card(
                painter,
                QRectF(0, 0, self.width, self.height),
                accent=node_color,
                selection_color=palette.SELECTION,
                title="Conversation",
                subtitle=f"{len(self.conversation_history)} messages",
                preview=preview_text(latest_message, fallback="Branch conversation"),
                badge="CHAT",
                mode=render_mode,
                selected=self.isSelected(),
                hovered=self.hovered,
                connection_radius=self.CONNECTION_DOT_RADIUS,
            )
            return

        if self.is_collapsed:
            painter.setPen(QColor("#ffffff"))
            font = QFont("Segoe UI", 10, QFont.Weight.Bold)
            painter.setFont(font)
            painter.drawText(QRectF(40, 0, self.width - 80, self.height), Qt.AlignmentFlag.AlignVCenter, "Conversation")
            
            icon = qta.icon('fa5s.comments', color=node_color.name())
            icon.paint(painter, QRect(10, 10, 20, 20))
            
            self.collapse_button_rect = QRectF(self.width - 35, 5, 30, 30)
            expand_icon = qta.icon('fa5s.expand-arrows-alt', color='#ffffff' if self.hovered else '#888888')
            expand_icon.paint(painter, QRect(int(self.width - 30), 10, 20, 20))
        else:
            if self.hovered:
                self.collapse_button_rect = QRectF(self.width - 35, 5, 30, 30)
                painter.setBrush(QColor(255, 255, 255, 30))
                painter.setPen(QColor(255, 255, 255, 150))
                painter.drawRoundedRect(self.collapse_button_rect.adjusted(6,6,-6,-6), 4, 4)
                
                icon_pen = QPen(QColor("#ffffff"), 2)
                painter.setPen(icon_pen)
                center = self.collapse_button_rect.center()
                painter.drawLine(int(center.x() - 4), int(center.y()), int(center.x() + 4), int(center.y()))
            else:
                self.collapse_button_rect = QRectF()

    def mousePressEvent(self, event):
        if self.collapse_button_rect.contains(event.pos()):
            self.toggle_collapse()
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton and self.scene():
            self.scene().is_dragging_item = True
            if hasattr(self.scene(), 'window'): self.scene().window.setCurrentNode(self)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        menu = PluginNodeContextMenu(self)
        menu.exec(event.screenPos())

    def mouseReleaseEvent(self, event):
        if self.scene():
            self.scene().is_dragging_item = False
            self.scene()._clear_smart_guides()
        super().mouseReleaseEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsObject.GraphicsItemChange.ItemPositionChange and self.scene() and self.scene().is_dragging_item:
            return self.scene().snap_position(self, value)
        if change == QGraphicsObject.GraphicsItemChange.ItemPositionHasChanged and self.scene():
            self.scene().nodeMoved(self)
        return super().itemChange(change, value)

    def hoverEnterEvent(self, event):
        self._handle_hover_enter(event)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._handle_hover_leave(event)
        super().hoverLeaveEvent(event)
