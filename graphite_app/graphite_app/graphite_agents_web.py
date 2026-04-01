from PySide6.QtWidgets import (
    QGraphicsItem, QGraphicsProxyWidget, QWidget, QVBoxLayout,
    QTextEdit, QPushButton, QLabel, QHBoxLayout, QGraphicsObject
)
from PySide6.QtCore import QRectF, Qt, QPointF, Signal, QTimer, QRect
from PySide6.QtGui import QPainter, QColor, QBrush, QPen, QPainterPath, QFont
import qtawesome as qta
from graphite_config import get_current_palette, get_graph_node_colors, get_neutral_button_colors, get_semantic_color
from graphite_connections import ConnectionItem
from graphite_canvas_items import HoverAnimationMixin
from graphite_memory import append_history, get_node_history
from graphite_plugin_context_menu import PluginNodeContextMenu


class WebConnectionItem(ConnectionItem):
    """
    A specialized ConnectionItem with a distinct visual style (orange dash-dot line)
    to represent the link to a WebNode.
    """
    def paint(self, painter, option, widget=None):
        """
        Handles the custom painting of the connection line.

        Args:
            painter (QPainter): The painter to use.
            option (QStyleOptionGraphicsItem): Provides style options.
            widget (QWidget, optional): The widget being painted on. Defaults to None.
        """
        if not (self.start_node and self.end_node):
            return
            
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        node_colors = get_graph_node_colors()
        web_color = node_colors["header"]

        # Use a dash-dot line style to distinguish it from other connection types.
        pen = QPen(web_color, 2, Qt.PenStyle.DashDotLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)

        if self.hover:
            pen.setWidth(3)
        
        painter.setPen(pen)
        painter.drawPath(self.path)

        # Draw animated arrows if the animation is active.
        if self.is_animating:
            for arrow in self.arrows:
                self.drawArrow(painter, arrow['pos'], web_color)

    def drawArrow(self, painter, pos, color):
        """
        Draws a single animated arrow along the connection path.

        Args:
            painter (QPainter): The painter to use.
            pos (float): The position along the path (0.0 to 1.0).
            color (QColor): The color of the arrow.
        """
        if pos < 0 or pos > 1:
            return
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


class WebNode(QGraphicsObject, HoverAnimationMixin):
    """
    A QGraphicsItem representing a web search plugin node on the canvas.

    This node provides a user interface for entering a search query, initiating a
    web search via a worker thread, and displaying the status and summarized
    results of that search.
    """
    run_clicked = Signal(object) # Emits self when the run button is clicked.
    
    NODE_WIDTH = 450
    NODE_HEIGHT = 400
    COLLAPSED_WIDTH = 250
    COLLAPSED_HEIGHT = 40
    CONNECTION_DOT_RADIUS = 5
    CONNECTION_DOT_OFFSET = 0

    def __init__(self, parent_node, parent=None):
        """
        Initializes the WebNode.

        Args:
            parent_node (QGraphicsItem): The node from which this WebNode branches.
            parent (QGraphicsItem, optional): The parent graphics item. Defaults to None.
        """
        super().__init__(parent)
        HoverAnimationMixin.__init__(self)
        self.parent_node = parent_node
        self.children = []
        self.is_user = False # Considered an AI-generated node for history purposes.
        self.conversation_history = []
        
        self.is_collapsed = False
        self.collapse_button_rect = QRectF()

        # State attributes for the web search process.
        self.query = ""
        self.status = "Idle"
        self.summary = ""
        self.sources = []

        # Standard graphics item setup.
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.hovered = False

        # Use a QGraphicsProxyWidget to embed standard Qt widgets into the graphics item.
        self.widget = QWidget()
        self.widget.setObjectName("webNodeMainWidget")
        self.widget.setFixedSize(self.NODE_WIDTH, self.NODE_HEIGHT)
        self.widget.setStyleSheet("""
            QWidget#webNodeMainWidget {
                background-color: transparent;
                color: #e0e0e0;
            }
            QWidget#webNodeMainWidget QLabel {
                background-color: transparent;
            }
        """)
        
        self._setup_ui()
        
        self.proxy = QGraphicsProxyWidget(self)
        self.proxy.setWidget(self.widget)

    @property
    def width(self):
        """Returns the dynamic width of the node."""
        return self.COLLAPSED_WIDTH if self.is_collapsed else self.NODE_WIDTH

    @property
    def height(self):
        """Returns the dynamic height of the node."""
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
        """Constructs the internal widget layout and components of the node."""
        main_layout = QVBoxLayout(self.widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)
        
        node_colors = get_graph_node_colors()
        web_color = node_colors["header"]
        
        # --- Header Section ---
        header_layout = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(qta.icon('fa5s.globe-americas', color=web_color).pixmap(18, 18))
        header_layout.addWidget(icon)
        title_label = QLabel("Web Search")
        title_label.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {web_color.name()}; background: transparent;")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)

        # --- Query Input Section ---
        main_layout.addWidget(QLabel("Search Query:"))
        self.query_input = QTextEdit()
        self.query_input.setPlaceholderText("Enter a search query, e.g., 'Best Italian restaurants in NYC'")
        self.query_input.setFixedHeight(60)
        self.query_input.textChanged.connect(self._on_query_changed)
        main_layout.addWidget(self.query_input)

        # --- Run Button ---
        self.run_button = QPushButton("Fetch Information")
        self.run_button.clicked.connect(lambda: self.run_clicked.emit(self))
        main_layout.addWidget(self.run_button)

        # --- Status Label ---
        self.status_label = QLabel("Status: Idle")
        self.status_label.setStyleSheet("color: #888; font-style: italic; background: transparent;")
        main_layout.addWidget(self.status_label)

        # --- Result Display Section ---
        main_layout.addWidget(QLabel("Summary:"))
        self.summary_display = QTextEdit()
        self.summary_display.setReadOnly(True)
        self.summary_display.setPlaceholderText("Web search results will be summarized here...")
        main_layout.addWidget(self.summary_display)

        # Apply common styles to text edit widgets.
        for widget in [self.query_input, self.summary_display]:
            widget.setStyleSheet("""
                QTextEdit {
                    background-color: #252526; border: 1px solid #3f3f3f;
                    color: #cccccc; border-radius: 4px; padding: 5px;
                    font-family: 'Segoe UI', sans-serif;
                }
            """)

        # Style the run button with a contrasting text color based on background brightness.
        button_colors = get_neutral_button_colors()

        self.run_button.setIcon(qta.icon('fa5s.search', color=button_colors["icon"].name()))
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
        
    def _on_query_changed(self):
        """Slot to update the internal query state when the input text changes."""
        self.query = self.query_input.toPlainText()

    def set_query(self, text: str):
        """Programmatically sets the query text in the input widget."""
        self.query_input.setText(text)
        self.query = text

    def set_status(self, status_text: str):
        """
        Updates the status label to provide feedback on the search process.

        Args:
            status_text (str): The new status message to display.
        """
        self.status = status_text
        self.status_label.setText(f"Status: {status_text}")
        self.status_label.setStyleSheet(f"color: {get_semantic_color('status_info').name()}; background: transparent;")

    def set_running_state(self, is_running: bool):
        """
        Enables or disables UI elements based on the running state.

        Args:
            is_running (bool): True if the search is active, False otherwise.
        """
        self.run_button.setEnabled(not is_running)
        self.query_input.setReadOnly(is_running)
        self.run_button.setText("Processing..." if is_running else "Fetch Information")

    def set_result(self, summary: str, sources: list):
        """
        Displays the final summary and source links in the result area.

        Args:
            summary (str): The summarized text from the web search.
            sources (list[str]): A list of source URLs.
        """
        self.summary = summary
        self.sources = sources
        
        # Format sources as clickable Markdown links.
        source_links = "\n".join([f"- [{src}]({src})" for src in sources])
        full_text = f"{summary}\n\n---\n\n**Sources:**\n{source_links}"
        
        self.summary_display.setMarkdown(full_text)
        self.set_status("Completed")
        self.status_label.setStyleSheet(f"color: {get_semantic_color('status_success').name()}; background: transparent;")
        
        # Update conversation history for potential child nodes.
        self.conversation_history = append_history(get_node_history(self.parent_node), [
            {'role': 'assistant', 'content': full_text}
        ])

    def set_error(self, error_message: str):
        """
        Displays an error message in the status and result areas.

        Args:
            error_message (str): The error message to display.
        """
        self.status = f"Error: {error_message}"
        self.status_label.setText(self.status)
        self.status_label.setStyleSheet(f"color: {get_semantic_color('status_error').name()}; font-weight: bold; background: transparent;")
        self.summary_display.setText(f"An error occurred during the process:\n\n{error_message}")

    def boundingRect(self):
        """Returns the bounding rectangle of the node, including padding for connection dots."""
        padding = self.CONNECTION_DOT_OFFSET + self.CONNECTION_DOT_RADIUS
        return QRectF(-padding, 0, self.width + 2 * padding, self.height)
        
    def paint(self, painter, option, widget=None):
        """
        Handles the custom painting of the node's border, background, and connection dots.

        Args:
            painter (QPainter): The painter to use.
            option (QStyleOptionGraphicsItem): Provides style options.
            widget (QWidget, optional): The widget being painted on. Defaults to None.
        """
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = get_current_palette()
        node_colors = get_graph_node_colors()
        
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width, self.height, 10, 10)
        painter.setBrush(QColor("#2d2d2d"))
        
        web_color = node_colors["border"]
        pen = QPen(web_color, 1.5)

        if self.isSelected():
            pen = QPen(palette.SELECTION, 2)
        elif self.hovered:
            pen = QPen(QColor("#ffffff"), 2)
        
        painter.setPen(pen)
        painter.drawPath(path)
        
        # Draw connection dots for linking.
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
            painter.drawText(QRectF(40, 0, self.width - 80, self.height), Qt.AlignmentFlag.AlignVCenter, "Web Search")
            
            icon = qta.icon('fa5s.globe-americas', color=web_color.name())
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
        """Handles mouse press to set the current node context and start dragging."""
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
        """Handles mouse release to stop dragging and clear smart guides."""
        if self.scene():
            self.scene().is_dragging_item = False
            self.scene()._clear_smart_guides()
        super().mouseReleaseEvent(event)

    def itemChange(self, change, value):
        """
        Handles item changes, applying snapping logic during position changes.

        Args:
            change (QGraphicsItem.GraphicsItemChange): The type of change.
            value: The new value for the changed attribute.

        Returns:
            The modified value or the result of the superclass implementation.
        """
        if change == QGraphicsItem.ItemPositionChange and self.scene() and self.scene().is_dragging_item:
            return self.scene().snap_position(self, value)
        if change == QGraphicsItem.ItemPositionHasChanged and self.scene():
            self.scene().nodeMoved(self)
        return super().itemChange(change, value)

    def hoverEnterEvent(self, event):
        """Handles hover enter event using the mixin."""
        self._handle_hover_enter(event)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        """Handles hover leave event using the mixin."""
        self._handle_hover_leave(event)
        super().hoverLeaveEvent(event)
