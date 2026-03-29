from PySide6.QtWidgets import QWidget, QLabel
from PySide6.QtCore import Qt, Signal, QTimer, QRect
from PySide6.QtGui import QPainter, QColor, QLinearGradient, QCursor

from graphite_node import ChatNode
from graphite_config import get_current_palette

class MinimapWidget(QWidget):
    """
    A vertical minimap widget that provides a high-level overview of the nodes
    in the scene. It displays nodes as small indicators, allows for quick
    navigation by clicking, and supports scrolling for large numbers of nodes.
    """
    nodeSelected = Signal(object)
    MAX_VISIBLE_NODES = 25
    INDICATOR_SPACING = 6
    INDICATOR_HEIGHT = 3

    def __init__(self, scene, parent=None):
        """
        Initializes the MinimapWidget.

        Args:
            scene (QGraphicsScene): The scene whose nodes will be displayed.
            parent (QWidget, optional): The parent widget. Defaults to None.
        """
        super().__init__(parent)
        self.scene = scene
        self.nodes = []
        self._hovered_node = None
        self._is_near_cursor = False
        self._scroll_offset = 0
        self.setMouseTracking(True)
        self.setFixedWidth(40)

        # Timer to delay showing the tooltip until the user hovers for a moment.
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(500)
        self._hover_timer.timeout.connect(self._show_tooltip)

        # The tooltip widget itself.
        self._tooltip_widget = QLabel(self.parent())
        self._tooltip_widget.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self._tooltip_widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._tooltip_widget.setStyleSheet("""
            QLabel {
                background-color: rgba(30, 30, 30, 0.9);
                color: #e0e0e0;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 6px;
                font-size: 11px;
            }
        """)
        self._tooltip_widget.hide()

    def update_nodes(self):
        """
        Updates the internal list of nodes from the scene. This should be called
        whenever the scene's node list changes.
        """
        # The scene's `nodes` list is maintained in creation order. This is the source of truth.
        self.nodes = list(self.scene.nodes)
        
        # Adjust scroll offset if it becomes out of bounds after a node update (e.g., deletion).
        max_offset = max(0, len(self.nodes) - self.MAX_VISIBLE_NODES)
        self._scroll_offset = max(0, min(self._scroll_offset, max_offset))
        
        self.update() # Trigger a repaint.

    def paintEvent(self, event):
        """
        Handles the custom painting of the minimap indicators.

        Args:
            event (QPaintEvent): The paint event.
        """
        palette = get_current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if not self.nodes:
            return

        # The minimap fades in when the cursor is near.
        opacity_factor = 1.0 if self._is_near_cursor else 0.4
        
        # Get the slice of nodes that are currently visible in the minimap viewport.
        visible_nodes = self.nodes[self._scroll_offset : self._scroll_offset + self.MAX_VISIBLE_NODES]
        num_to_display = len(visible_nodes)
        
        if num_to_display == 0:
            return

        # Calculate the vertical offset to center the block of indicators in the widget.
        total_indicators_height = num_to_display * self.INDICATOR_SPACING
        y_offset = (self.height() - total_indicators_height) / 2

        for i, node in enumerate(visible_nodes):
            y_top = y_offset + (i * self.INDICATOR_SPACING)

            line_rect = QRect(0, int(y_top), self.width(), self.INDICATOR_HEIGHT)
            gradient = QLinearGradient(line_rect.topLeft(), line_rect.topRight())
            
            # Determine the color of the indicator.
            color = palette.USER_NODE if node.is_user else palette.AI_NODE
            if node == self._hovered_node:
                color = QColor("#ffffff") # Highlight hovered node in white.

            transparent = QColor(color)
            transparent.setAlpha(0)
            color.setAlphaF(opacity_factor)

            # Use a gradient to fade the indicator edges for a softer look.
            gradient.setColorAt(0, transparent)
            gradient.setColorAt(0.2, color)
            gradient.setColorAt(0.8, color)
            gradient.setColorAt(1, transparent)

            painter.fillRect(line_rect, gradient)

    def _update_hover_state(self, local_pos):
        """
        Updates the hover state based on the cursor's position. This determines
        the minimap's opacity and which node is currently hovered.

        Args:
            local_pos (QPoint): The cursor position in the widget's local coordinates.
        """
        # Check if the cursor is close to the minimap to trigger the fade-in effect.
        distance_to_edge = self.parent().width() - self.mapToGlobal(local_pos).x()
        new_is_near = distance_to_edge < 150
        if new_is_near != self._is_near_cursor:
            self._is_near_cursor = new_is_near
            self.update()

        # Determine which node indicator is under the cursor.
        hovered_node = self._node_at(local_pos)
        if hovered_node != self._hovered_node:
            self._hovered_node = hovered_node
            self._hover_timer.stop()
            self._hide_tooltip()
            if hovered_node:
                self._hover_timer.start() # Start the timer to show the tooltip.
            self.update()

    def mouseMoveEvent(self, event):
        """Handles mouse move events to update the hover state."""
        self._update_hover_state(event.pos())

    def mousePressEvent(self, event):
        """Handles mouse press events to select a node."""
        node = self._node_at(event.pos())
        if node:
            self.nodeSelected.emit(node)

    def leaveEvent(self, event):
        """Resets the hover state when the mouse leaves the widget."""
        self._is_near_cursor = False
        self._hovered_node = None
        self._hover_timer.stop()
        self._hide_tooltip()
        self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event):
        """
        Handles mouse wheel events to scroll through the list of nodes.

        Args:
            event (QWheelEvent): The wheel event.
        """
        num_nodes = len(self.nodes)
        if num_nodes <= self.MAX_VISIBLE_NODES:
            return

        # Scroll up or down.
        delta = -1 if event.angleDelta().y() > 0 else 1
        new_offset = self._scroll_offset + delta
        
        # Clamp the scroll offset within valid bounds.
        max_offset = num_nodes - self.MAX_VISIBLE_NODES
        self._scroll_offset = max(0, min(new_offset, max_offset))
        
        self.update()
        
        # Update hover state as the indicators under the cursor may have changed.
        local_pos = self.mapFromGlobal(QCursor.pos())
        self._update_hover_state(local_pos)

    def _node_at(self, pos):
        """
        Determines which node corresponds to the given position within the widget.

        Args:
            pos (QPoint): The position in local coordinates.

        Returns:
            ChatNode or None: The node at the position, or None if no node is there.
        """
        visible_nodes = self.nodes[self._scroll_offset : self._scroll_offset + self.MAX_VISIBLE_NODES]
        num_to_display = len(visible_nodes)
        
        if num_to_display == 0:
            return None
        
        total_indicators_height = num_to_display * self.INDICATOR_SPACING
        y_offset = (self.height() - total_indicators_height) / 2

        # Check if the Y position is within the block of indicators.
        if not (y_offset <= pos.y() < y_offset + total_indicators_height):
            return None

        # Calculate the index based on the Y position.
        clicked_index_in_view = int((pos.y() - y_offset) // self.INDICATOR_SPACING)
        
        if 0 <= clicked_index_in_view < num_to_display:
            return visible_nodes[clicked_index_in_view]
        return None

    def _show_tooltip(self):
        """
        Creates and shows a tooltip with a preview of the hovered node's content.
        """
        if not self._hovered_node: return

        # Get a short preview of the node's text.
        text_preview = self._hovered_node.text.strip().split('\n')[0]
        if len(text_preview) > 50:
            text_preview = text_preview[:47] + "..."
        if not text_preview:
            text_preview = "[Attachment/Content Node]"
        
        self._tooltip_widget.setText(text_preview)
        self._tooltip_widget.adjustSize()
        
        # Position the tooltip to the left of the cursor.
        tooltip_pos = QCursor.pos()
        tooltip_pos.setX(tooltip_pos.x() - self._tooltip_widget.width() - 15)
        self._tooltip_widget.move(tooltip_pos)
        self._tooltip_widget.show()
    
    def _hide_tooltip(self):
        """Hides the tooltip widget."""
        self._tooltip_widget.hide()