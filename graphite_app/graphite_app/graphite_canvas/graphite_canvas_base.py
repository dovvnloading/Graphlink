"""Shared canvas item primitives and animation helpers."""

from PySide6.QtWidgets import QGraphicsItem, QLineEdit
from PySide6.QtCore import Qt, QRectF, QTimer, Signal
from PySide6.QtGui import QPainter, QColor, QBrush, QPen

from graphite_config import get_current_palette


class HoverAnimationMixin:
    """
    A mixin class that provides functionality for triggering an animated effect
    on ancestor connections after a long hover.

    When a node is hovered over for a set duration, this mixin traces back
    through its parent connections, activating an animated arrow flow to visualize
    the conversational path leading to the hovered node.
    """
    def __init__(self):
        """Initializes the HoverAnimationMixin."""
        self.incoming_connection = None # The connection leading *to* this node.
        # A single-shot timer to detect a "long hover".
        self.long_hover_timer = QTimer()
        self.long_hover_timer.setSingleShot(True)
        self.long_hover_timer.setInterval(750) # 750ms delay before triggering.
        self.long_hover_timer.timeout.connect(self.trigger_ancestor_animation)

    def trigger_ancestor_animation(self):
        """
        Starts the arrow animation on the incoming connection and recursively
        calls this method on its parent node to animate the entire ancestral path.
        """
        if self.incoming_connection:
            self.incoming_connection.startArrowAnimation()
        
        parent = getattr(self, 'parent_node', None)
        if parent and hasattr(parent, 'trigger_ancestor_animation'):
            parent.trigger_ancestor_animation()

    def stop_ancestor_animation(self):
        """
        Stops the arrow animation on the incoming connection and recursively
        calls this method on its parent node to stop all animations in the path.
        """
        if self.incoming_connection:
            self.incoming_connection.stopArrowAnimation()
            
        parent = getattr(self, 'parent_node', None)
        if parent and hasattr(parent, 'stop_ancestor_animation'):
            parent.stop_ancestor_animation()

    def _handle_hover_enter(self, event):
        """
        A standardized hover enter handler for any QGraphicsItem using this mixin.
        It sets the hover state and starts the long-hover timer.
        """
        self.hovered = True
        self.long_hover_timer.start()
        self.update()

    def _handle_hover_leave(self, event):
        """
        A standardized hover leave handler. It clears the hover state, stops the
        timer, and stops any active ancestor animations.
        """
        self.hovered = False
        self.long_hover_timer.stop()
        self.stop_ancestor_animation()
        self.update()


class GhostFrame(QGraphicsItem):
    """
    A temporary, semi-transparent QGraphicsItem that appears when hovering over a
    collapsed Container. It provides a visual preview of the container's size and
    position if it were to be expanded, helping the user understand the layout.
    """
    def __init__(self, rect, parent=None):
        """
        Initializes the GhostFrame.

        Args:
            rect (QRectF): The rectangle defining the size and shape of the ghost frame.
            parent (QGraphicsItem, optional): The parent item. Defaults to None.
        """
        super().__init__(parent)
        self.rect = rect
        self.setZValue(-5)  # Ensure it's drawn in the background.

    def boundingRect(self):
        """Returns the bounding rectangle of the item."""
        return self.rect

    def paint(self, painter, option, widget=None):
        """
        Handles the custom painting of the ghost frame.

        Args:
            painter (QPainter): The painter object.
            option (QStyleOptionGraphicsItem): Provides style information.
            widget (QWidget, optional): The widget being painted on. Defaults to None.
        """
        palette = get_current_palette()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Define a semi-transparent, dashed pen for the outline.
        pen_color = palette.SELECTION.lighter(120)
        pen_color.setAlpha(200)
        pen = QPen(pen_color, 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        
        # Define a very transparent brush for the fill.
        brush_color = palette.SELECTION
        brush_color.setAlpha(50)
        painter.setBrush(brush_color)
        
        painter.drawRoundedRect(self.rect, 10, 10)


class CanvasHeaderLineEdit(QLineEdit):
    """A small header editor used by canvas grouping items."""

    committed = Signal(str)
    canceled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancelled = False
        self.setFrame(False)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            """
            QLineEdit {
                background-color: rgba(26, 26, 26, 0.96);
                color: #f1f1f1;
                border: 1px solid #5a5a5a;
                border-radius: 5px;
                padding: 4px 8px;
                selection-background-color: #5a5a5a;
                selection-color: #ffffff;
            }
            QLineEdit:focus {
                border-color: #7a7a7a;
            }
            """
        )
        self.editingFinished.connect(self._emit_commit_if_needed)

    def begin(self, text: str):
        self._cancelled = False
        self.setText(text)
        self.show()
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.selectAll()

    def _emit_commit_if_needed(self):
        if self._cancelled:
            self._cancelled = False
            return
        self.committed.emit(self.text())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._cancelled = True
            self.canceled.emit()
            self.clearFocus()
            event.accept()
            return

        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clearFocus()
            event.accept()
            return

        super().keyPressEvent(event)


def iter_scene_connection_lists(scene):
    """Yield every known connection list tracked by the scene."""
    if not scene:
        return

    list_names = (
        "connections",
        "content_connections",
        "document_connections",
        "image_connections",
        "thinking_connections",
        "system_prompt_connections",
        "pycoder_connections",
        "code_sandbox_connections",
        "web_connections",
        "conversation_connections",
        "reasoning_connections",
        "group_summary_connections",
        "html_connections",
        "artifact_connections",
        "workflow_connections",
        "graph_diff_connections",
    )

    for name in list_names:
        yield getattr(scene, name, [])


def update_connections_for_items(scene, items):
    """Refresh all connection paths touching any of the provided items."""
    if not scene:
        return

    endpoints = {item for item in items if item is not None}
    if not endpoints:
        return

    for conn_list in iter_scene_connection_lists(scene):
        for conn in conn_list:
            if getattr(conn, "start_node", None) in endpoints or getattr(conn, "end_node", None) in endpoints:
                conn.update_path()
