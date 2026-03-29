import re
import markdown
from PySide6.QtWidgets import (
    QGraphicsObject, QGraphicsProxyWidget, QWidget, QVBoxLayout,
    QHBoxLayout, QTextEdit, QPushButton, QLabel, QSplitter, QTabWidget,
    QPlainTextEdit, QFrame, QSizePolicy
)
from PySide6.QtCore import QRectF, Qt, Signal, QThread, QPointF, QSize
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath, QFont, QBrush, QLinearGradient
import qtawesome as qta
from graphite_config import get_current_palette
from graphite_config import get_semantic_color
from graphite_canvas_items import HoverAnimationMixin
from graphite_connections import ConnectionItem
import graphite_config as config
import api_provider

ARTIFACT_SCROLLBAR_STYLE = """
    QScrollBar:vertical {
        background: #252526;
        width: 10px;
        margin: 0px;
        border-radius: 5px;
    }
    QScrollBar::handle:vertical {
        background-color: #555555;
        min-height: 25px;
        border-radius: 5px;
    }
    QScrollBar::handle:vertical:hover {
        background-color: #6a6a6a;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0px;
        background: none;
    }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
        background: none;
    }
    QScrollBar:horizontal {
        background: #252526;
        height: 10px;
        margin: 0px;
        border-radius: 5px;
    }
    QScrollBar::handle:horizontal {
        background-color: #555555;
        min-width: 25px;
        border-radius: 5px;
    }
    QScrollBar::handle:horizontal:hover {
        background-color: #6a6a6a;
    }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
        width: 0px;
        background: none;
    }
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
        background: none;
    }
"""


class ArtifactInstructionInput(QTextEdit):
    submit_requested = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.submit_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

class ArtifactAgent:
    """
    An agent specialized in iteratively creating and refining a living document.
    """
    def __init__(self):
        self.system_prompt = """You are an expert Document Drafting Assistant (Artifacts).
Your primary task is to create, update, or refine a 'living document' based on user instructions and the context of the conversation.

RULES:
1. You will receive the conversation history, and your system instructions contain the CURRENT state of the document.
2. You must output the ENTIRE updated document enclosed exactly within <artifact> and </artifact> tags. Do NOT truncate or abbreviate the document. If you are changing one paragraph, you must still output the whole document with that change applied.
3. After the </artifact> tag, provide a brief conversational response acknowledging the changes, explaining your thought process, or asking for clarification.
4. If the document is currently empty, create the first draft based entirely on the instruction.
5. Always use Markdown formatting for the document content.
"""

    def get_response(self, current_artifact, history):
        # We inject the document state directly into the system prompt to maintain clean alternating history
        system_with_doc = self.system_prompt + f"\n\n--- CURRENT DOCUMENT STATE ---\n{current_artifact if current_artifact else '(Document is currently empty)'}\n"
        
        messages = [{'role': 'system', 'content': system_with_doc}]
        for msg in history:
            messages.append(msg)
            
        response = api_provider.chat(task=config.TASK_CHAT, messages=messages)
        raw_text = response['message']['content']
        
        # Parse out the artifact and the conversational response
        artifact_match = re.search(r'<artifact>(.*?)</artifact>', raw_text, re.DOTALL)
        if artifact_match:
            new_artifact = artifact_match.group(1).strip()
            ai_message = raw_text.replace(artifact_match.group(0), "").strip()
        else:
            # If the model fails to use the tags, fallback to treating the whole response as the artifact
            new_artifact = raw_text.strip()
            ai_message = "I have updated the document."
            
        return new_artifact, ai_message

class ArtifactWorkerThread(QThread):
    finished = Signal(str, str) # new_document, ai_message
    error = Signal(str)

    def __init__(self, current_artifact, history):
        super().__init__()
        self.current_artifact = current_artifact
        self.history = history
        self.agent = ArtifactAgent()
        self._is_running = True

    def run(self):
        try:
            if not self._is_running: return
            new_doc, ai_msg = self.agent.get_response(self.current_artifact, self.history)
            if self._is_running:
                self.finished.emit(new_doc, ai_msg)
        except Exception as e:
            if self._is_running:
                self.error.emit(str(e))
        finally:
            self._is_running = False

    def stop(self):
        self._is_running = False

class ArtifactConnectionItem(ConnectionItem):
    """A specialized connection featuring a turquoise dashed line."""
    def paint(self, painter, option, widget=None):
        if not (self.start_node and self.end_node):
            return
            
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        node_color = get_semantic_color("artifact")

        pen = QPen(node_color, 2, Qt.PenStyle.DashLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)

        if self.hover:
            pen.setWidth(3)
        
        painter.setPen(pen)
        painter.drawPath(self.path)

        if self.is_animating:
            for arrow in self.arrows:
                self.drawArrow(painter, arrow['pos'], node_color)

    def drawArrow(self, painter, pos, color):
        if pos < 0 or pos > 1: return
        point = self.path.pointAtPercent(pos)
        angle = self.path.angleAtPercent(pos)
        
        arrow = QPainterPath()
        arrow.moveTo(-self.arrow_size, -self.arrow_size/2)
        arrow.lineTo(0, 0)
        arrow.lineTo(-self.arrow_size, self.arrow_size/2)
        
        painter.save()
        painter.translate(point)
        painter.rotate(-angle)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(arrow)
        painter.restore()

class ArtifactNode(QGraphicsObject, HoverAnimationMixin):
    artifact_requested = Signal(object)

    NODE_WIDTH = 850
    NODE_HEIGHT = 650
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
        self.local_history = []
        self.chat_html_cache = ""
        self.is_collapsed = False
        self.collapse_button_rect = QRectF()
        
        self.setFlag(QGraphicsObject.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsObject.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsObject.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.hovered = False

        self.widget = QWidget()
        self.widget.setObjectName("artifactMainWidget")
        self.widget.setFixedSize(self.NODE_WIDTH, self.NODE_HEIGHT)
        self.widget.setStyleSheet("""
            QWidget#artifactMainWidget { background-color: transparent; color: #e0e0e0; font-family: 'Segoe UI', sans-serif; }
            QWidget#artifactMainWidget QLabel { background-color: transparent; }
        """)
        
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
            self.proxy.setVisible(not self.is_collapsed)
            self.prepareGeometryChange()
            if self.scene():
                self.scene().update_connections()
                self.scene().nodeMoved(self)
            self.update()

    def toggle_collapse(self):
        self.set_collapsed(not self.is_collapsed)

    def _setup_ui(self):
        artifact_color = get_semantic_color("artifact")
        artifact_hover_color = artifact_color.lighter(115)
        artifact_icon_text = artifact_color.name()
        artifact_button_icon = "#1e1e1e" if artifact_color.lightness() > 150 else "#f3f3f3"
        main_layout = QVBoxLayout(self.widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)
        
        # --- Header ---
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(4, 0, 0, 0)
        header_layout.setSpacing(8)
        icon = QLabel()
        icon.setPixmap(qta.icon('fa5s.file-code', color=artifact_icon_text).pixmap(18, 18))
        header_layout.addWidget(icon)
        title_label = QLabel("Artifact Drafter")
        title_label.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {artifact_icon_text}; background: transparent;")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)

        # --- Separator Line ---
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: #3f3f3f; border: none; height: 1px;")
        main_layout.addWidget(line)

        # --- Main Splitter ---
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.splitter.setStyleSheet("""
            QSplitter::handle:horizontal {
                width: 8px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3f3f3f, stop:0.5 #555555, stop:1 #3f3f3f);
            }
        """ + f"""
            QSplitter::handle:horizontal:hover {{ background: {artifact_icon_text}; }}
        """)

        # --- Left Pane: Chat & Instructions ---
        left_pane = QWidget()
        left_layout = QVBoxLayout(left_pane)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        # Chat Log
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                border: 1px solid #3f3f3f;
                border-radius: 6px;
                padding: 10px;
            }
        """ + ARTIFACT_SCROLLBAR_STYLE)
        self.chat_display.document().setDefaultStyleSheet("""
            p, ul, ol, li { color: #d4d4d4; font-family: 'Segoe UI', sans-serif; font-size: 13px; line-height: 1.4; margin-top: 2px; margin-bottom: 8px;}
            pre { background-color: #2d2d2d; padding: 8px; border-radius: 4px; font-family: Consolas, monospace; color: #dcdcaa; }
            code { background-color: #3f3f3f; padding: 2px 4px; border-radius: 4px; font-family: Consolas, monospace; }
        """)
        left_layout.addWidget(self.chat_display, stretch=1)

        # SOTA Unified Input Area
        input_container = QWidget()
        input_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        input_container.setMinimumHeight(72)
        input_container.setStyleSheet("""
            QWidget {
                background-color: #1e1e1e;
                border: 1px solid #3f3f3f;
                border-radius: 8px;
            }
        """)
        input_layout = QHBoxLayout(input_container)
        input_layout.setContentsMargins(12, 10, 10, 10)
        input_layout.setSpacing(10)

        self.instruction_input = ArtifactInstructionInput()
        self.instruction_input.setPlaceholderText("Instruct the AI to update the artifact...")
        self.instruction_input.setAcceptRichText(False)
        self.instruction_input.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.instruction_input.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.instruction_input.setWordWrapMode(self.instruction_input.wordWrapMode())
        self.instruction_input.setMinimumHeight(50)
        self.instruction_input.setMaximumHeight(110)
        self.instruction_input.setStyleSheet("""
            QTextEdit {
                background-color: transparent;
                border: none;
                color: #ffffff;
                font-size: 13px;
                font-family: 'Segoe UI', sans-serif;
                padding: 2px 0px;
            }
        """ + ARTIFACT_SCROLLBAR_STYLE)
        self.instruction_input.submit_requested.connect(lambda: self.artifact_requested.emit(self))
        input_layout.addWidget(self.instruction_input, stretch=1)

        self.update_button = QPushButton()
        self.update_button.setFixedSize(40, 40)
        self.update_button.setIconSize(QSize(16, 16))
        self.update_button.setIcon(qta.icon('fa5s.arrow-up', color=artifact_button_icon))
        self.update_button.clicked.connect(lambda: self.artifact_requested.emit(self))
        self.update_button.setStyleSheet(f"""
            QPushButton {{ background-color: {artifact_icon_text}; border: none; border-radius: 20px; margin: 0px; }}
            QPushButton:hover {{ background-color: {artifact_hover_color.name()}; }}
            QPushButton:disabled {{ background-color: #444444; }}
        """)
        self.update_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        input_layout.addWidget(self.update_button)

        left_layout.addWidget(input_container)

        # --- Right Pane: Living Document ---
        right_pane = QWidget()
        right_layout = QVBoxLayout(right_pane)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border: 1px solid #3f3f3f; background: #1e1e1e; border-radius: 6px; border-top-left-radius: 0px; }}
            QTabBar::tab {{ background: transparent; color: #888888; padding: 6px 16px; border: none; font-weight: bold; font-size: 12px; margin-right: 4px; }}
            QTabBar::tab:selected {{ color: {artifact_icon_text}; border-bottom: 2px solid {artifact_icon_text}; }}
            QTabBar::tab:hover:!selected {{ color: #ffffff; }}
        """)

        self.raw_editor = QPlainTextEdit()
        self.raw_editor.setPlaceholderText("The raw markdown / code will appear here. You can manually edit it.")
        self.raw_editor.setStyleSheet("""
            QPlainTextEdit {
                background-color: #1e1e1e; 
                color: #dcdcaa; 
                font-family: Consolas, Monaco, monospace; 
                border: none; 
                padding: 12px; 
                font-size: 13px;
            }
        """ + ARTIFACT_SCROLLBAR_STYLE)
        
        self.preview_display = QTextEdit()
        self.preview_display.setReadOnly(True)
        self.preview_display.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.preview_display.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.preview_display.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                border: none;
                padding: 12px;
                border-radius: 6px;
            }
        """ + ARTIFACT_SCROLLBAR_STYLE)
        self.preview_display.document().setDefaultStyleSheet("""
            p, ul, ol, li { color: #e0e0e0; font-family: 'Segoe UI', sans-serif; font-size: 13px; line-height: 1.5; margin-bottom: 10px; }
            h1, h2, h3, h4 { color: #ffffff; font-weight: bold; margin-top: 15px; margin-bottom: 8px; }
            pre { background-color: #2d2d2d; padding: 10px; border-radius: 6px; font-family: Consolas, monospace; color: #dcdcaa; }
            code { background-color: #3f3f3f; color: #dcdcaa; padding: 2px 4px; border-radius: 4px; font-family: Consolas, monospace; }
            blockquote { border-left: 3px solid #555555; padding-left: 10px; color: #aaaaaa; }
        """)

        self.tabs.addTab(self.raw_editor, "Markdown")
        self.tabs.addTab(self.preview_display, "Preview")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        right_layout.addWidget(self.tabs)

        self.splitter.addWidget(left_pane)
        self.splitter.addWidget(right_pane)
        self.splitter.setSizes([300, 550])

        main_layout.addWidget(self.splitter, 1)

    def _on_tab_changed(self, index):
        if index == 1: # Switching to Live Preview
            content = self.raw_editor.toPlainText()
            html = markdown.markdown(content, extensions=['fenced_code', 'tables'])
            self.preview_display.setHtml(html)

    def get_instruction(self):
        return self.instruction_input.toPlainText()

    def get_artifact_content(self):
        return self.raw_editor.toPlainText()

    def set_artifact_content(self, text):
        self.raw_editor.setPlainText(text)
        if self.tabs.currentIndex() == 1:
            html = markdown.markdown(text, extensions=['fenced_code', 'tables'])
            self.preview_display.setHtml(html)

    def add_chat_message(self, text, is_user=False):
        role_color = get_current_palette().USER_NODE.name() if is_user else get_semantic_color("artifact").name()
        role_name = "You" if is_user else "Artifact Drafter"
        
        # Strip thinking tags from UI if they exist
        display_text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
        if not display_text:
            display_text = "[Processing...]"

        html = markdown.markdown(display_text, extensions=['fenced_code'])
        
        # SOTA styling with left border indicator and crisp typography
        formatted = f"""
        <div style='margin-bottom: 15px; border-left: 3px solid {role_color}; padding-left: 10px;'>
            <span style='color: {role_color}; font-weight: bold; font-size: 11px; text-transform: uppercase;'>{role_name}</span><br>
            {html}
        </div>
        """
        
        self.chat_html_cache += formatted
        self.chat_display.setHtml(self.chat_html_cache)
        
        self.local_history.append({'role': 'user' if is_user else 'assistant', 'content': text})
        
        scrollbar = self.chat_display.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def set_running_state(self, is_running):
        self.instruction_input.setReadOnly(is_running)
        self.raw_editor.setReadOnly(is_running)
        
        if is_running:
            self.update_button.setEnabled(False)
            self.update_button.setIcon(qta.icon('fa5s.stop', color='#aaaaaa'))
            self.update_button.setStyleSheet("QPushButton { background-color: #444444; border: none; border-radius: 20px; margin: 0px; }")
        else:
            artifact_color = get_semantic_color("artifact")
            artifact_hover_color = artifact_color.lighter(115)
            artifact_button_icon = "#1e1e1e" if artifact_color.lightness() > 150 else "#f3f3f3"
            self.update_button.setEnabled(True)
            self.update_button.setIcon(qta.icon('fa5s.arrow-up', color=artifact_button_icon))
            self.update_button.setStyleSheet(f"QPushButton {{ background-color: {artifact_color.name()}; border: none; border-radius: 20px; margin: 0px; }} QPushButton:hover {{ background-color: {artifact_hover_color.name()}; }}")
            self.instruction_input.clear()

    def boundingRect(self):
        padding = self.CONNECTION_DOT_OFFSET + self.CONNECTION_DOT_RADIUS
        return QRectF(-padding, 0, self.width + 2 * padding, self.height)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = get_current_palette()
        
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width, self.height, 10, 10)
        painter.setBrush(QColor("#2d2d2d"))
        
        node_color = get_semantic_color("artifact")
        pen = QPen(node_color, 1.5)

        if self.isSelected():
            pen = QPen(palette.SELECTION, 2)
        elif self.hovered:
            pen = QPen(QColor("#ffffff"), 2)
        
        painter.setPen(pen)
        painter.drawPath(path)
        
        dot_color = node_color
        if self.isSelected() or self.hovered:
            dot_color = pen.color().lighter(110)
        
        painter.setBrush(dot_color)
        painter.setPen(Qt.PenStyle.NoPen)
        
        dot_rect_left = QRectF(-self.CONNECTION_DOT_RADIUS, (self.height / 2) - self.CONNECTION_DOT_RADIUS, self.CONNECTION_DOT_RADIUS * 2, self.CONNECTION_DOT_RADIUS * 2)
        painter.drawPie(dot_rect_left, 90 * 16, -180 * 16)
        
        dot_rect_right = QRectF(self.width - self.CONNECTION_DOT_RADIUS, (self.height / 2) - self.CONNECTION_DOT_RADIUS, self.CONNECTION_DOT_RADIUS * 2, self.CONNECTION_DOT_RADIUS * 2)
        painter.drawPie(dot_rect_right, 90 * 16, 180 * 16)

        if self.is_collapsed:
            painter.setPen(QColor("#ffffff"))
            font = QFont("Segoe UI", 10, QFont.Weight.Bold)
            painter.setFont(font)
            painter.drawText(QRectF(40, 0, self.width - 80, self.height), Qt.AlignmentFlag.AlignVCenter, "Artifact Drafter")
            
            icon = qta.icon('fa5s.file-code', color=node_color.name())
            icon.paint(painter, QRectF(10, 10, 20, 20).toRect())
            
            self.collapse_button_rect = QRectF(self.width - 35, 5, 30, 30)
            expand_icon = qta.icon('fa5s.expand-arrows-alt', color='#ffffff' if self.hovered else '#888888')
            expand_icon.paint(painter, QRectF(self.width - 30, 10, 20, 20).toRect())
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
            if hasattr(self.scene(), 'window'):
                self.scene().window.setCurrentNode(self)
        super().mousePressEvent(event)

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
