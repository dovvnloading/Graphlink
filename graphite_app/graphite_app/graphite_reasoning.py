from PySide6.QtWidgets import (
    QGraphicsObject, QGraphicsProxyWidget, QWidget, QVBoxLayout,
    QTextEdit, QPushButton, QLabel, QHBoxLayout, QSlider
)
from PySide6.QtCore import QRectF, Qt, Signal, QRect
from PySide6.QtGui import QPainter, QColor, QBrush, QPen, QPainterPath, QFont
import qtawesome as qta
from graphite_config import get_current_palette, get_graph_node_colors, get_neutral_button_colors, get_semantic_color
from graphite_canvas_items import HoverAnimationMixin
from graphite_memory import append_history, get_node_history
from graphite_plugin_context_menu import PluginNodeContextMenu

class ReasoningNode(QGraphicsObject, HoverAnimationMixin):
    """
    A specialized QGraphicsItem that provides a UI for a multi-step, iterative
    reasoning process to solve complex problems.
    """
    reasoning_requested = Signal(object) 

    NODE_WIDTH = 550
    NODE_HEIGHT = 700
    COLLAPSED_WIDTH = 250
    COLLAPSED_HEIGHT = 40
    CONNECTION_DOT_RADIUS = 5
    CONNECTION_DOT_OFFSET = 0

    def __init__(self, parent_node, parent=None):
        super().__init__(parent)
        HoverAnimationMixin.__init__(self)
        self.parent_node = parent_node
        self.children = []
        self.is_user = False 
        self.conversation_history = []
        
        self.is_collapsed = False
        self.collapse_button_rect = QRectF()

        self.prompt = ""
        self.thinking_budget = 3 
        self.status = "Idle"
        self.thought_process = ""

        self.setFlag(QGraphicsObject.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsObject.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsObject.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.hovered = False

        self.widget = QWidget()
        self.widget.setObjectName("reasoningMainWidget")
        self.widget.setFixedSize(self.NODE_WIDTH, self.NODE_HEIGHT)
        self.widget.setStyleSheet("""
            QWidget#reasoningMainWidget { background-color: transparent; color: #e0e0e0; }
            QWidget#reasoningMainWidget QLabel { background-color: transparent; }
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
        main_layout = QVBoxLayout(self.widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)
        
        node_colors = get_graph_node_colors()
        node_color = node_colors["header"]
        
        header_layout = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(qta.icon('fa5s.brain', color=node_color).pixmap(18, 18))
        header_layout.addWidget(icon)
        title_label = QLabel("Graphlink-Reasoning")
        title_label.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {node_color.name()}; background: transparent;")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)

        main_layout.addWidget(QLabel("Complex Prompt:"))
        self.prompt_input = QTextEdit()
        self.prompt_input.setPlaceholderText("Enter a complex problem or question that requires deep reasoning...")
        self.prompt_input.setFixedHeight(100)
        self.prompt_input.textChanged.connect(self._on_prompt_changed)
        main_layout.addWidget(self.prompt_input)

        budget_layout = QHBoxLayout()
        budget_layout.addWidget(QLabel("Thinking Budget:"))
        self.budget_slider = QSlider(Qt.Orientation.Horizontal)
        self.budget_slider.setMinimum(1)
        self.budget_slider.setMaximum(10)
        self.budget_slider.setValue(self.thinking_budget)
        self.budget_slider.setTickInterval(1)
        self.budget_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.budget_slider.valueChanged.connect(self._on_budget_changed)
        budget_layout.addWidget(self.budget_slider)
        self.budget_label = QLabel(str(self.thinking_budget))
        self.budget_label.setFixedWidth(25)
        self.budget_label.setStyleSheet("background: transparent;")
        budget_layout.addWidget(self.budget_label)
        main_layout.addLayout(budget_layout)

        self.run_button = QPushButton("Start Reasoning")
        self.run_button.clicked.connect(lambda: self.reasoning_requested.emit(self))
        main_layout.addWidget(self.run_button)
        
        self.status_label = QLabel("Status: Idle")
        self.status_label.setStyleSheet("color: #888; font-style: italic; background: transparent;")
        main_layout.addWidget(self.status_label)

        main_layout.addWidget(QLabel("Thought Process:"))
        self.thought_process_display = QTextEdit()
        self.thought_process_display.setReadOnly(True)
        self.thought_process_display.setPlaceholderText("The AI's step-by-step reasoning will appear here...")
        main_layout.addWidget(self.thought_process_display)

        for widget in [self.prompt_input, self.thought_process_display]:
            widget.setStyleSheet("""
                QTextEdit {
                    background-color: #252526; border: 1px solid #3f3f3f;
                    color: #cccccc; border-radius: 4px; padding: 5px;
                    font-family: Segoe UI, sans-serif;
                }
            """)
        
        button_colors = get_neutral_button_colors()

        self.run_button.setIcon(qta.icon('fa5s.cogs', color=button_colors["icon"].name()))
        self.run_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {button_colors["background"].name()};
                color: {button_colors["icon"].name()};
                border: 1px solid {button_colors["border"].name()};
                border-radius: 4px;
                padding: 8px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {button_colors["hover"].name()};
                border-color: {button_colors["hover"].lighter(112).name()};
            }}
            QPushButton:pressed {{
                background-color: {button_colors["pressed"].name()};
                border-color: {button_colors["border"].darker(105).name()};
            }}
            QPushButton:disabled {{
                background-color: #2b2b2b;
                border-color: #353535;
                color: #7b7b7b;
            }}
        """)

    def _on_prompt_changed(self):
        self.prompt = self.prompt_input.toPlainText()

    def _on_budget_changed(self, value):
        self.thinking_budget = value
        self.budget_label.setText(str(value))

    def set_running_state(self, is_running: bool):
        self.run_button.setEnabled(not is_running)
        self.prompt_input.setReadOnly(is_running)
        self.budget_slider.setEnabled(not is_running)
        self.run_button.setText("Reasoning..." if is_running else "Start Reasoning")
        if is_running:
            self.set_status("Thinking...")
        else:
            self.set_status("Completed")

    def set_status(self, status_text: str):
        self.status = status_text
        self.status_label.setText(f"Status: {status_text}")
        if "Thinking" in status_text or "Step" in status_text:
            self.status_label.setStyleSheet(f"color: {get_semantic_color('status_info').name()}; background: transparent;")
        elif "Completed" in status_text:
            self.status_label.setStyleSheet(f"color: {get_semantic_color('status_success').name()}; background: transparent;")
        else:
            self.status_label.setStyleSheet("color: #888; background: transparent;")

    def clear_thoughts(self):
        self.thought_process = ""
        self.thought_process_display.clear()
        
    def append_thought(self, step_title: str, thought_text: str):
        formatted_step = f"## {step_title}\n\n{thought_text}\n\n---\n\n"
        self.thought_process += formatted_step
        self.thought_process_display.setMarkdown(self.thought_process)
        scrollbar = self.thought_process_display.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def set_final_answer(self, final_text: str, parent_history=None):
        base_history = parent_history if parent_history is not None else get_node_history(self.parent_node)
        prompt_text = self.prompt.strip() if self.prompt.strip() else "[Reasoning Prompt]"
        self.conversation_history = append_history(base_history, [
            {'role': 'user', 'content': prompt_text},
            {'role': 'assistant', 'content': final_text}
        ])
        self.append_thought("Final Answer", final_text)
        self.set_status("Completed")

    def set_error(self, error_message: str):
        self.status = f"Error: {error_message}"
        self.status_label.setText(self.status)
        self.status_label.setStyleSheet(f"color: {get_semantic_color('status_error').name()}; font-weight: bold; background: transparent;")
        self.append_thought("Error", f"An error occurred during the process:\n\n{error_message}")
        self.set_running_state(False)

    def boundingRect(self):
        padding = self.CONNECTION_DOT_OFFSET + self.CONNECTION_DOT_RADIUS
        return QRectF(-padding, 0, self.width + 2 * padding, self.height)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = get_current_palette()
        node_colors = get_graph_node_colors()
        
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width, self.height, 10, 10)
        painter.setBrush(QColor("#2d2d2d"))
        
        node_color = node_colors["border"]
        pen = QPen(node_color, 1.5)

        if self.isSelected():
            pen = QPen(palette.SELECTION, 2)
        elif self.hovered:
            pen = QPen(QColor("#ffffff"), 2)
        
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

        if self.is_collapsed:
            painter.setPen(QColor("#ffffff"))
            font = QFont("Segoe UI", 10, QFont.Weight.Bold)
            painter.setFont(font)
            painter.drawText(QRectF(40, 0, self.width - 80, self.height), Qt.AlignmentFlag.AlignVCenter, "Graphlink-Reasoning")
            
            icon = qta.icon('fa5s.brain', color=node_color.name())
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
        if self.is_collapsed and event.button() == Qt.MouseButton.LeftButton:
            self.toggle_collapse()
            event.accept()
            return

        if self.collapse_button_rect.contains(event.pos()):
            self.toggle_collapse()
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton and self.scene():
            self.scene().is_dragging_item = True
            if hasattr(self.scene(), 'window'):
                self.scene().window.setCurrentNode(self)
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
