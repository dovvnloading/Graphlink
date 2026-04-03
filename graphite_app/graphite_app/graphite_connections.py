from PySide6.QtWidgets import QGraphicsItem
from PySide6.QtCore import (
    Qt, QRectF, QPointF, QTimer, QVariantAnimation, QEasingCurve
)
from PySide6.QtGui import (
    QPainter, QColor, QBrush, QPen, QPainterPath,
    QLinearGradient, QPainterPathStroker
)

from graphite_canvas.graphite_canvas_base import iter_scene_connection_lists
from graphite_canvas_items import Container, Frame, Note
from graphite_config import get_current_palette
from graphite_pycoder import PyCoderNode
from graphite_conversation_node import ConversationNode
from graphite_html_view import HtmlViewNode


def _fade_connections_enabled(item):
    scene = item.scene()
    return bool(scene and getattr(scene, "fade_connections_enabled", False))


def _sync_connection_visibility_mode(item):
    is_active = (
        getattr(item, "hover", False)
        or getattr(item, "hovered", False)
        or getattr(item, "is_selected", False)
    )
    item.setOpacity(1.0 if (not _fade_connections_enabled(item) or is_active) else 0.08)


class Pin(QGraphicsItem):
    """
    A draggable point on a ConnectionItem that allows the user to curve the path.
    Pins are children of a ConnectionItem.
    """
    def __init__(self, parent=None):
        """
        Initializes the Pin.

        Args:
            parent (QGraphicsItem, optional): The parent ConnectionItem. Defaults to None.
        """
        super().__init__(parent)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.hover = False
        self.radius = 5
        self._dragging = False
        
    def boundingRect(self):
        """
        Returns the bounding rectangle of the pin.

        Returns:
            QRectF: The bounding rectangle.
        """
        return QRectF(-self.radius, -self.radius, 
                     self.radius * 2, self.radius * 2)
        
    def paint(self, painter, option, widget=None):
        """
        Handles the custom painting of the pin.

        Args:
            painter (QPainter): The painter object.
            option (QStyleOptionGraphicsItem): Style options.
            widget (QWidget, optional): The widget being painted on. Defaults to None.
        """
        palette = get_current_palette()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Change color based on selection or hover state.
        if self.isSelected():
            color = palette.SELECTION
        elif self.hover:
            color = palette.AI_NODE
        else:
            color = QColor("#ffffff")
            
        painter.setPen(QPen(color.darker(120), 1))
        painter.setBrush(QBrush(color))
        painter.drawEllipse(self.boundingRect())
        
    def hoverEnterEvent(self, event):
        """Updates hover state when the mouse enters the pin."""
        self.hover = True
        self.update()
        super().hoverEnterEvent(event)
        
    def hoverLeaveEvent(self, event):
        """Updates hover state when the mouse leaves the pin."""
        self.hover = False
        self.update()
        super().hoverLeaveEvent(event)
        
    def mousePressEvent(self, event):
        """
        Handles mouse press events. A Ctrl+RightClick removes the pin.
        A regular left click initiates dragging.

        Args:
            event (QGraphicsSceneMouseEvent): The mouse press event.
        """
        if event.button() == Qt.MouseButton.RightButton and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            parent_connection = self.parentItem()
            if parent_connection and isinstance(parent_connection, ConnectionItem):
                parent_connection.remove_pin(self)
                if self.scene():
                    self.scene().removeItem(self)
                event.accept()
                return
        else:
            self._dragging = True
            super().mousePressEvent(event)
            
    def mouseReleaseEvent(self, event):
        """Handles mouse release to stop the dragging operation."""
        self._dragging = False
        super().mouseReleaseEvent(event)
            
    def itemChange(self, change, value):
        """
        Handles item changes, snapping the pin to a grid during movement and
        notifying the parent connection to update its path.

        Args:
            change (QGraphicsItem.GraphicsItemChange): The type of change.
            value: The new value of the changed attribute.

        Returns:
            The modified value or the result of the superclass implementation.
        """
        if change == QGraphicsItem.ItemPositionChange and self._dragging:
            grid_size = 5
            new_pos = QPointF(
                round(value.x() / grid_size) * grid_size,
                round(value.y() / grid_size) * grid_size
            )
            # Notify the parent connection to redraw its path
            if isinstance(self.parentItem(), ConnectionItem):
                self.parentItem().prepareGeometryChange()
                self.parentItem().update_path()
            return new_pos
        return super().itemChange(change, value)

class ConnectionItem(QGraphicsItem):
    """
    The base class for drawing a connection line between two nodes.
    It supports curved paths using draggable Pins and animated arrows to show data flow.
    """
    def __init__(self, start_node, end_node):
        """
        Initializes the ConnectionItem.

        Args:
            start_node (QGraphicsItem): The item where the connection starts.
            end_node (QGraphicsItem): The item where the connection ends.
        """
        super().__init__()
        self.start_node = start_node
        self.end_node = end_node
        self.setZValue(-1) # Draw behind nodes
        self.setAcceptHoverEvents(True)
        self.path = QPainterPath()
        self.pins = [] # List to hold Pin objects
        self.hover = False
        self.click_tolerance = 20.0 # Increased hitbox for easier clicking
        self.hover_path = None # Cached path for hover detection
        self.is_selected = False
        
        # Timer to delay the start of the arrow animation on long hover
        self.hover_start_timer = QTimer()
        self.hover_start_timer.setSingleShot(True)
        self.hover_start_timer.timeout.connect(self.startArrowAnimation)
        
        # Timer to drive the arrow animation frames
        self.animation_timer = QTimer()
        self.animation_timer.timeout.connect(self.updateArrows)
        
        # Animation properties
        self.arrows = []
        self.arrow_spacing = 30
        self.arrow_size = 10
        self.animation_speed = 2
        self.is_animating = False
        
        self.setAcceptHoverEvents(True)
        
        self.update_path()
        
        # Cache the item's painting to improve performance
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.sync_visibility_mode()

    def sync_visibility_mode(self):
        _sync_connection_visibility_mode(self)

    def boundingRect(self):
        """
        Returns the bounding rectangle of the connection path, including a generous
        padding to ensure the entire line and its hover area are accounted for.

        Returns:
            QRectF: The bounding rectangle.
        """
        if not self.path:
            return QRectF()
            
        padding = self.click_tolerance * 2
        return self.path.boundingRect().adjusted(-padding, -padding,
                                               padding, padding)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSceneHasChanged:
            self.sync_visibility_mode()
        return super().itemChange(change, value)

    def create_hover_path(self):
        """
        Creates a wider, invisible path based on the visible path to serve as a
        larger hitbox for mouse interactions.

        Returns:
            QPainterPath or None: The stroked path for hover detection.
        """
        if not self.path:
            return None
            
        stroke = QPainterPathStroker()
        stroke.setWidth(self.click_tolerance * 2)
        stroke.setCapStyle(Qt.PenCapStyle.RoundCap)
        stroke.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return stroke.createStroke(self.path)

    def contains_point(self, point):
        """
        Custom containment check to see if a point is "on" the line, using the
        wider hover path for easier interaction.

        Args:
            point (QPointF): The point to check.

        Returns:
            bool: True if the point is on or near the line, False otherwise.
        """
        if not self.hover_path:
            self.hover_path = self.create_hover_path()
            
        if not self.hover_path:
            return False
            
        point_rect = QRectF(
            point.x() - self.click_tolerance/2,
            point.y() - self.click_tolerance/2,
            self.click_tolerance,
            self.click_tolerance
        )
        
        return self.hover_path.intersects(point_rect)

    def get_node_scene_pos(self, node):
        """
        Gets the scene position of a node. (Currently unused but kept for potential future use).

        Args:
            node (QGraphicsItem): The node.

        Returns:
            QPointF: The node's position in scene coordinates.
        """
        return node.scenePos()
        
    def add_pin(self, scene_pos):
        """
        Adds a new draggable pin to the connection at a specific scene position.

        Args:
            scene_pos (QPointF): The position in the scene to add the pin.

        Returns:
            Pin: The newly created pin object.
        """
        pin = Pin(self)
        local_pos = self.mapFromScene(scene_pos)
        pin.setPos(local_pos)
        self.pins.append(pin)
        self.update_path()
        return pin
        
    def remove_pin(self, pin):
        """
        Removes a pin from the connection.

        Args:
            pin (Pin): The pin object to remove.
        """
        if pin in self.pins:
            self.pins.remove(pin)
            if pin.scene():
                pin.scene().removeItem(pin)
            self.update_path()
                
    def clear(self):
        """
        Clears all pins from the connection. (Note: This is a partial implementation
        and seems to be a remnant, as the main scene clear handles most cleanup).
        """
        if self.window and hasattr(self.window, 'pin_overlay'):
            self.window.pin_overlay.clear_pins()
        
        self.pins.clear()
        
        self.nodes.clear()
        self.connections.clear()
        self.frames.clear()
        
        super().clear()

    def _get_visual_rect(self, item):
        """
        Helper to get the effective visual rectangle of an item, accounting for
        its collapsed state if applicable.
        """
        if hasattr(item, 'is_collapsed') and item.is_collapsed:
            if hasattr(item, 'COLLAPSED_WIDTH') and hasattr(item, 'COLLAPSED_HEIGHT'):
                return QRectF(0, 0, item.COLLAPSED_WIDTH, item.COLLAPSED_HEIGHT)
        if hasattr(item, 'rect'): # Frame, Container
            return item.rect
        elif hasattr(item, 'width') and hasattr(item, 'height'): # Nodes, Charts, Notes
            return QRectF(0, 0, item.width, item.height)
        return item.boundingRect()

    def _get_effective_endpoint(self, item):
        """
        Finds the "effective" endpoint for a connection. If an item is inside a
        collapsed container or frame, the collapsed grouping item itself becomes
        the endpoint for drawing.

        Args:
            item (QGraphicsItem): The original endpoint item.

        Returns:
            QGraphicsItem: The effective endpoint item (either the original or a parent container).
        """
        effective = item
        current = item
        while current:
            if isinstance(current, (Container, Frame)) and getattr(current, 'is_collapsed', False):
                effective = current
            current = current.parentItem()
        return effective

    @staticmethod
    def _connection_signature(start_item, end_item):
        return (id(start_item), id(end_item))

    def _should_show_collapsed_connection(self, effective_start, effective_end):
        if not (
            isinstance(effective_start, (Container, Frame)) and getattr(effective_start, 'is_collapsed', False)
        ) and not (
            isinstance(effective_end, (Container, Frame)) and getattr(effective_end, 'is_collapsed', False)
        ):
            return True

        scene = self.scene()
        if not scene:
            return True

        signature = self._connection_signature(effective_start, effective_end)
        representative = None

        for conn_list in iter_scene_connection_lists(scene):
            for conn in conn_list:
                if not isinstance(conn, ConnectionItem):
                    continue
                if conn.scene() != scene or not getattr(conn, 'start_node', None) or not getattr(conn, 'end_node', None):
                    continue

                other_start = conn._get_effective_endpoint(conn.start_node)
                other_end = conn._get_effective_endpoint(conn.end_node)
                if self._connection_signature(other_start, other_end) != signature:
                    continue

                if representative is None or id(conn) < id(representative):
                    representative = conn

        return representative is None or representative is self

    def update_path(self):
        """
        Recalculates the QPainterPath of the connection based on the positions of the
        start and end nodes and any intermediate pins.
        """
        if not (self.start_node and self.end_node):
            return

        # Determine if the connection should be visible (e.g., hide if nodes are inside a collapsed container)
        effective_start = self._get_effective_endpoint(self.start_node)
        effective_end = self._get_effective_endpoint(self.end_node)
        
        if effective_start == effective_end:
            self.setVisible(False)
            return

        if not self._should_show_collapsed_connection(effective_start, effective_end):
            self.setVisible(False)
            return

        self.setVisible(True)

        old_path = self.path
        
        start_rect = self._get_visual_rect(effective_start)
        end_rect = self._get_visual_rect(effective_end)

        start_offset = getattr(effective_start, 'CONNECTION_DOT_OFFSET', 0)
        end_offset = getattr(effective_end, 'CONNECTION_DOT_OFFSET', 0)

        # Calculate start and end points in scene coordinates, then map them to item's local coordinates
        start_scene_pos = effective_start.mapToScene(QPointF(start_rect.width() + start_offset, start_rect.height() / 2))
        end_scene_pos = effective_end.mapToScene(QPointF(0 - end_offset, end_rect.height() / 2))
        
        start_pos = self.mapFromScene(start_scene_pos)
        end_pos = self.mapFromScene(end_scene_pos)
        
        new_path = QPainterPath()
        new_path.moveTo(start_pos)
        
        scene = self.scene()
        use_orthogonal = scene and scene.orthogonal_routing and not self.pins
        
        if use_orthogonal:
            # Draw a right-angled orthogonal path
            mid_x = start_pos.x() + (end_pos.x() - start_pos.x()) / 2
            new_path.lineTo(mid_x, start_pos.y())
            new_path.lineTo(mid_x, end_pos.y())
            new_path.lineTo(end_pos)
        elif self.pins:
            # Draw a path through a series of sorted pins
            sorted_pins = sorted(self.pins, key=lambda p: p.scenePos().x())
            points = [start_pos]
            
            for pin in sorted_pins:
                points.append(pin.pos())
            points.append(end_pos)
            
            # Draw cubic Bezier curves between each point (node-pin, pin-pin, pin-node)
            for i in range(len(points) - 1):
                current_point = points[i]
                next_point = points[i + 1]
                
                dx = next_point.x() - current_point.x()
                distance = min(abs(dx) / 2, 200)
                
                ctrl1_x = current_point.x() + distance
                ctrl1_y = current_point.y()
                ctrl2_x = next_point.x() - distance
                ctrl2_y = next_point.y()
                
                new_path.cubicTo(
                    ctrl1_x, ctrl1_y,
                    ctrl2_x, ctrl2_y,
                    next_point.x(), next_point.y()
                )
        else:
            # Draw a standard S-shaped cubic Bezier curve
            dx = end_pos.x() - start_pos.x()
            distance = min(abs(dx) / 2, 200)
            
            ctrl1_x = start_pos.x() + distance
            ctrl1_y = start_pos.y()
            ctrl2_x = end_pos.x() - distance
            ctrl2_y = end_pos.y()
            
            new_path.cubicTo(
                ctrl1_x, ctrl1_y,
                ctrl2_x, ctrl2_y,
                end_pos.x(), end_pos.y()
            )
        
        # If the path has changed, update geometry and cached hover path
        if new_path != old_path:
            self.path = new_path
            self.hover_path = None
            self.prepareGeometryChange()
            self.update()

    def startArrowAnimation(self):
        """Starts the animated arrow flow along the connection path."""
        if not self.is_animating:
            self.is_animating = True
            self.arrows = []
            path_length = self.path.length()
            
            # Pre-populate arrows along the path
            current_distance = 0
            while current_distance < path_length:
                self.arrows.append({
                    'pos': current_distance / path_length,
                    'opacity': 1.0,
                    'distance': current_distance
                })
                current_distance += self.arrow_spacing
            
            self.animation_timer.start(16) # ~60 FPS
            self.update()

    def stopArrowAnimation(self):
        """Stops the arrow animation and clears the arrows."""
        self.is_animating = False
        self.animation_timer.stop()
        self.arrows.clear()
        self.update()

    def updateArrows(self):
        """Updates the position of each arrow for the next animation frame."""
        if not self.is_animating:
            return
            
        path_length = self.path.length()
        arrows_to_remove = []
        
        for arrow in self.arrows:
            arrow['distance'] += self.animation_speed
            arrow['pos'] = arrow['distance'] / path_length
            
            # Mark arrows that have reached the end of the path for removal
            if arrow['pos'] >= 1:
                arrows_to_remove.append(arrow)
                
        for arrow in arrows_to_remove:
            self.arrows.remove(arrow)
            
        # Add a new arrow at the start if there's space
        if not self.arrows or self.arrows[0]['distance'] >= self.arrow_spacing:
            self.arrows.insert(0, {
                'pos': 0,
                'opacity': 1.0,
                'distance': 0
            })
        
        self.update()

    def drawArrow(self, painter, pos, opacity):
        """
        Draws a single arrow at a specific percentage along the path.

        Args:
            painter (QPainter): The painter object.
            pos (float): The position along the path (0.0 to 1.0).
            opacity (float): The opacity of the arrow.
        """
        if pos < 0 or pos > 1:
            return
        
        palette = get_current_palette()
        point = self.path.pointAtPercent(pos)
        angle = self.path.angleAtPercent(pos)
        
        # Define the arrow shape
        arrow = QPainterPath()
        arrow.moveTo(-self.arrow_size, -self.arrow_size/2)
        arrow.lineTo(0, 0)
        arrow.lineTo(-self.arrow_size, self.arrow_size/2)
        
        painter.save()
        
        # Translate and rotate the painter to draw the arrow correctly
        painter.translate(point)
        painter.rotate(-angle)
        
        # Interpolate the color based on the arrow's position along the gradient
        start_color = palette.USER_NODE if self.start_node.is_user else palette.AI_NODE
        end_color = palette.USER_NODE if self.end_node.is_user else palette.AI_NODE
        
        r = int(start_color.red() * (1 - pos) + end_color.red() * pos)
        g = int(start_color.green() * (1 - pos) + end_color.green() * pos)
        b = int(start_color.blue() * (1 - pos) + end_color.blue() * pos)
        
        color = QColor(r, g, b)
        color.setAlphaF(opacity)
        
        painter.setBrush(QBrush(color))
        painter.setPen(QPen(color, 1))
        
        painter.drawPath(arrow)
        painter.restore()

    def shape(self):
        """
        Returns the shape of the item used for collision detection and mouse events.
        We use the wider hover_path to make it easier to click.

        Returns:
            QPainterPath: The shape of the item.
        """
        if not self.hover_path:
            self.hover_path = self.create_hover_path()
        return self.hover_path if self.hover_path else self.path

    def paint(self, painter, option, widget=None):
        """
        Handles the custom painting of the connection line and its animated arrows.

        Args:
            painter (QPainter): The painter object.
            option (QStyleOptionGraphicsItem): Style options.
            widget (QWidget, optional): The widget being painted on. Defaults to None.
        """
        if not (self.start_node and self.end_node):
            return
            
        palette = get_current_palette()
        # Culling: Don't draw if the connection is off-screen
        view = self.scene().views()[0]
        view_rect = view.mapToScene(view.viewport().rect()).boundingRect()
        if not self.boundingRect().intersects(view_rect):
            return
            
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Create a gradient that follows the path
        gradient = QLinearGradient(
            self.path.pointAtPercent(0),
            self.path.pointAtPercent(1)
        )
        
        start_color = palette.USER_NODE if self.start_node.is_user else palette.AI_NODE
        end_color = palette.USER_NODE if self.end_node.is_user else palette.AI_NODE
        
        if self.hover or self.is_selected:
            start_color = start_color.lighter(120)
            end_color = end_color.lighter(120)
        
        gradient.setColorAt(0, start_color)
        gradient.setColorAt(1, end_color)
        
        width = 3 if (self.hover or self.is_selected) else 2
        pen = QPen(QBrush(gradient), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPath(self.path)
        
        if self.is_animating:
            for arrow in self.arrows:
                self.drawArrow(painter, arrow['pos'], arrow['opacity'])

    def hoverEnterEvent(self, event):
        """Handles mouse hover enter events."""
        point = event.pos()
        hover_rect = QRectF(
            point.x() - self.click_tolerance,
            point.y() - self.click_tolerance,
            self.click_tolerance * 2,
            self.click_tolerance * 2
        )
        
        if self.path.intersects(hover_rect) or self.contains_point(point):
            if not self.hover:
                self.hover = True
                self.hover_start_timer.start(1000) # Start timer for animation
                self.sync_visibility_mode()
                self.update()
        super().hoverEnterEvent(event)

    def hoverMoveEvent(self, event):
        """Handles mouse hover move events."""
        if self.contains_point(event.pos()):
            if not self.hover:
                self.hover = True
                self.hover_start_timer.start(1000)
                self.sync_visibility_mode()
                self.update()
        else:
            if self.hover:
                self.hover = False
                self.hover_start_timer.stop()
                self.stopArrowAnimation()
                self.sync_visibility_mode()
                self.update()
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event):
        """Handles mouse hover leave events."""
        self.hover = False
        self.hover_start_timer.stop()
        if self.is_animating:
            self.stopArrowAnimation()
        self.sync_visibility_mode()
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event):
        """Handles mouse press events, adding a pin on Ctrl+Click."""
        if self.contains_point(event.pos()):
            if event.button() == Qt.MouseButton.LeftButton and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                scene_pos = self.mapToScene(event.pos())
                self.add_pin(scene_pos)
                event.accept()
            else:
                event.ignore()
        else:
            event.ignore()

    def focusOutEvent(self, event):
        """Clears selection state when the item loses focus."""
        self.is_selected = False
        self.update()
        super().focusOutEvent(event)

class ContentConnectionItem(QGraphicsItem):
    """
    A specialized connection item with a dashed line style, used to link a
    ChatNode to its associated content nodes (like CodeNode).
    """
    def __init__(self, start_node, end_node):
        """
        Initializes the ContentConnectionItem.

        Args:
            start_node (ChatNode): The parent ChatNode.
            end_node (CodeNode): The child content node.
        """
        super().__init__()
        self.start_node = start_node # This will be a ChatNode
        self.end_node = end_node     # This will be a CodeNode
        self.setZValue(-1)
        self.path = QPainterPath()
        self.setAcceptHoverEvents(True)
        self.hover = False
        
        # Timers for hover animation
        self.hover_start_timer = QTimer()
        self.hover_start_timer.setSingleShot(True)
        self.hover_start_timer.timeout.connect(self.startArrowAnimation)
        
        self.animation_timer = QTimer()
        self.animation_timer.timeout.connect(self.updateArrows)
        
        self.arrows = []
        self.arrow_spacing = 30
        self.arrow_size = 8
        self.animation_speed = 1.5
        self.is_animating = False

        self.update_path()
        self.sync_visibility_mode()

    def sync_visibility_mode(self):
        _sync_connection_visibility_mode(self)

    def _get_visual_rect(self, item):
        """Helper to get the visual rectangle of an item, accounting for collapsed state."""
        if hasattr(item, 'is_collapsed') and item.is_collapsed:
            if hasattr(item, 'COLLAPSED_WIDTH') and hasattr(item, 'COLLAPSED_HEIGHT'):
                return QRectF(0, 0, item.COLLAPSED_WIDTH, item.COLLAPSED_HEIGHT)
        if hasattr(item, 'rect'): # Frame, Container
            return item.rect
        elif hasattr(item, 'width') and hasattr(item, 'height'): # Nodes, Charts, Notes
            return QRectF(0, 0, item.width, item.height)
        return item.boundingRect()

    def _get_effective_endpoint(self, item):
        """Helper to find the effective endpoint if inside a collapsed container."""
        current = item
        while current:
            parent = current.parentItem()
            if isinstance(parent, Container) and parent.is_collapsed:
                return parent
            current = parent
        return item

    def boundingRect(self):
        """Returns the bounding rectangle of the item."""
        return self.path.boundingRect().adjusted(-2, -2, 2, 2)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSceneHasChanged:
            self.sync_visibility_mode()
        return super().itemChange(change, value)

    def update_path(self):
        """Recalculates the path, which is a straight line from bottom-center to top-center."""
        if not (self.start_node and self.end_node):
            return

        effective_start = self._get_effective_endpoint(self.start_node)
        effective_end = self._get_effective_endpoint(self.end_node)

        if effective_start == effective_end:
            self.setVisible(False)
            return
        else:
            self.setVisible(True)

        self.prepareGeometryChange()
        
        start_rect = self._get_visual_rect(effective_start)
        end_rect = self._get_visual_rect(effective_end)
        
        # Connect from the bottom-center of the start node to the top-center of the end node
        start_scene_pos = effective_start.mapToScene(QPointF(start_rect.width() / 2, start_rect.height()))
        end_scene_pos = effective_end.mapToScene(QPointF(end_rect.width() / 2, 0))

        start_pos = self.mapFromScene(start_scene_pos)
        end_pos = self.mapFromScene(end_scene_pos)

        self.path = QPainterPath()
        self.path.moveTo(start_pos)
        self.path.lineTo(end_pos)
        self.update()

    def startArrowAnimation(self):
        """Starts the animated arrow flow."""
        if not self.is_animating:
            self.is_animating = True
            self.arrows = []
            path_length = self.path.length()
            
            current_distance = 0
            while current_distance < path_length:
                self.arrows.append({'pos': current_distance / path_length, 'distance': current_distance})
                current_distance += self.arrow_spacing
            
            self.animation_timer.start(16)
            self.update()

    def stopArrowAnimation(self):
        """Stops the animated arrow flow."""
        self.is_animating = False
        self.animation_timer.stop()
        self.arrows.clear()
        self.update()

    def updateArrows(self):
        """Updates arrow positions for animation."""
        if not self.is_animating: return
        path_length = self.path.length()
        for arrow in self.arrows:
            arrow['distance'] += self.animation_speed
            arrow['pos'] = arrow['distance'] / path_length
        self.arrows = [a for a in self.arrows if a['pos'] < 1]
        if not self.arrows or self.arrows[0]['distance'] >= self.arrow_spacing:
            self.arrows.insert(0, {'pos': 0, 'distance': 0})
        self.update()

    def drawArrow(self, painter, pos, color):
        """Draws a single arrow on the path."""
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
        
    def paint(self, painter, option, widget=None):
        """Paints the dashed connection line."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#888888"), 1.5, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawPath(self.path)

        if self.is_animating:
            for arrow in self.arrows:
                self.drawArrow(painter, arrow['pos'], QColor("#888888"))

    def hoverEnterEvent(self, event):
        """Handles hover enter event."""
        self.hover = True
        self.hover_start_timer.start(500)
        self.sync_visibility_mode()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        """Handles hover leave event."""
        self.hover = False
        self.hover_start_timer.stop()
        self.stopArrowAnimation()
        self.sync_visibility_mode()
        super().hoverLeaveEvent(event)

class DocumentConnectionItem(QGraphicsItem):
    """
    A specialized connection item with a dotted line style, used to link a
    ChatNode to its associated DocumentNode.
    """
    def __init__(self, start_node, end_node):
        """
        Initializes the DocumentConnectionItem.

        Args:
            start_node (ChatNode): The parent ChatNode.
            end_node (DocumentNode): The child document node.
        """
        super().__init__()
        self.start_node = start_node # ChatNode
        self.end_node = end_node     # DocumentNode
        self.setZValue(-1)
        self.path = QPainterPath()
        self.setAcceptHoverEvents(True)
        self.hover = False
        
        self.hover_start_timer = QTimer()
        self.hover_start_timer.setSingleShot(True)
        self.hover_start_timer.timeout.connect(self.startArrowAnimation)
        
        self.animation_timer = QTimer()
        self.animation_timer.timeout.connect(self.updateArrows)
        
        self.arrows = []
        self.arrow_spacing = 30
        self.arrow_size = 8
        self.animation_speed = 1.5
        self.is_animating = False
        self.update_path()
        self.sync_visibility_mode()

    def sync_visibility_mode(self):
        _sync_connection_visibility_mode(self)

    def _get_visual_rect(self, item):
        """Helper to get the visual rectangle of an item."""
        if hasattr(item, 'is_collapsed') and item.is_collapsed:
            if hasattr(item, 'COLLAPSED_WIDTH') and hasattr(item, 'COLLAPSED_HEIGHT'):
                return QRectF(0, 0, item.COLLAPSED_WIDTH, item.COLLAPSED_HEIGHT)
        if hasattr(item, 'rect'): # Frame, Container
            return item.rect
        elif hasattr(item, 'width') and hasattr(item, 'height'): # Nodes, Charts, Notes
            return QRectF(0, 0, item.width, item.height)
        return item.boundingRect()

    def _get_effective_endpoint(self, item):
        """Helper to find the effective endpoint if inside a collapsed container."""
        current = item
        while current:
            parent = current.parentItem()
            if isinstance(parent, Container) and parent.is_collapsed:
                return parent
            current = parent
        return item

    def boundingRect(self):
        """Returns the bounding rectangle of the item."""
        return self.path.boundingRect().adjusted(-2, -2, 2, 2)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSceneHasChanged:
            self.sync_visibility_mode()
        return super().itemChange(change, value)

    def update_path(self):
        """Recalculates the path as a straight line."""
        if not (self.start_node and self.end_node):
            return

        if self.end_node and getattr(self.end_node, 'is_docked', False):
            self.setVisible(False)
            return

        effective_start = self._get_effective_endpoint(self.start_node)
        effective_end = self._get_effective_endpoint(self.end_node)

        if effective_start == effective_end:
            self.setVisible(False)
            return
        else:
            self.setVisible(True)

        self.prepareGeometryChange()
        
        start_rect = self._get_visual_rect(effective_start)
        end_rect = self._get_visual_rect(effective_end)

        start_scene_pos = effective_start.mapToScene(QPointF(start_rect.width() / 2, start_rect.height()))
        end_scene_pos = effective_end.mapToScene(QPointF(end_rect.width() / 2, 0))

        start_pos = self.mapFromScene(start_scene_pos)
        end_pos = self.mapFromScene(end_scene_pos)

        self.path = QPainterPath()
        self.path.moveTo(start_pos)
        self.path.lineTo(end_pos)
        self.update()

    def startArrowAnimation(self):
        """Starts the arrow animation."""
        if not self.is_animating:
            self.is_animating = True
            self.arrows = []
            path_length = self.path.length()
            current_distance = 0
            while current_distance < path_length:
                self.arrows.append({'pos': current_distance / path_length, 'distance': current_distance})
                current_distance += self.arrow_spacing
            self.animation_timer.start(16)
            self.update()

    def stopArrowAnimation(self):
        """Stops the arrow animation."""
        self.is_animating = False
        self.animation_timer.stop()
        self.arrows.clear()
        self.update()

    def updateArrows(self):
        """Updates arrow positions for animation."""
        if not self.is_animating: return
        path_length = self.path.length()
        for arrow in self.arrows:
            arrow['distance'] += self.animation_speed
            arrow['pos'] = arrow['distance'] / path_length
        self.arrows = [a for a in self.arrows if a['pos'] < 1]
        if not self.arrows or self.arrows[0]['distance'] >= self.arrow_spacing:
            self.arrows.insert(0, {'pos': 0, 'distance': 0})
        self.update()

    def drawArrow(self, painter, pos, color):
        """Draws a single arrow on the path."""
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
        
    def paint(self, painter, option, widget=None):
        """Paints the dotted connection line."""
        if self.end_node and getattr(self.end_node, 'is_docked', False):
            return

        palette = get_current_palette()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(palette.NAV_HIGHLIGHT, 1.5, Qt.PenStyle.DotLine)
        painter.setPen(pen)
        painter.drawPath(self.path)

        if self.is_animating:
            for arrow in self.arrows:
                self.drawArrow(painter, arrow['pos'], palette.NAV_HIGHLIGHT)

    def hoverEnterEvent(self, event):
        """Handles hover enter event."""
        self.hover = True
        self.hover_start_timer.start(500)
        self.sync_visibility_mode()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        """Handles hover leave event."""
        self.hover = False
        self.hover_start_timer.stop()
        self.stopArrowAnimation()
        self.sync_visibility_mode()
        super().hoverLeaveEvent(event)

class ImageConnectionItem(QGraphicsItem):
    """
    A specialized connection item with a dash-dot line style, used to link a
    ChatNode to its associated ImageNode.
    """
    def __init__(self, start_node, end_node):
        """
        Initializes the ImageConnectionItem.

        Args:
            start_node (ChatNode): The parent ChatNode.
            end_node (ImageNode): The child image node.
        """
        super().__init__()
        self.start_node = start_node # ChatNode
        self.end_node = end_node     # ImageNode
        self.setZValue(-1)
        self.path = QPainterPath()
        self.setAcceptHoverEvents(True)
        self.hover = False
        
        self.hover_start_timer = QTimer()
        self.hover_start_timer.setSingleShot(True)
        self.hover_start_timer.timeout.connect(self.startArrowAnimation)
        
        self.animation_timer = QTimer()
        self.animation_timer.timeout.connect(self.updateArrows)
        
        self.arrows = []
        self.arrow_spacing = 30
        self.arrow_size = 8
        self.animation_speed = 1.5
        self.is_animating = False
        self.update_path()
        self.sync_visibility_mode()

    def sync_visibility_mode(self):
        _sync_connection_visibility_mode(self)

    def _get_visual_rect(self, item):
        """Helper to get the visual rectangle of an item."""
        if hasattr(item, 'is_collapsed') and item.is_collapsed:
            if hasattr(item, 'COLLAPSED_WIDTH') and hasattr(item, 'COLLAPSED_HEIGHT'):
                return QRectF(0, 0, item.COLLAPSED_WIDTH, item.COLLAPSED_HEIGHT)
        if hasattr(item, 'rect'): # Frame, Container
            return item.rect
        elif hasattr(item, 'width') and hasattr(item, 'height'): # Nodes, Charts, Notes
            return QRectF(0, 0, item.width, item.height)
        return item.boundingRect()

    def _get_effective_endpoint(self, item):
        """Helper to find the effective endpoint if inside a collapsed container."""
        current = item
        while current:
            parent = current.parentItem()
            if isinstance(parent, Container) and parent.is_collapsed:
                return parent
            current = parent
        return item

    def boundingRect(self):
        """Returns the bounding rectangle of the item."""
        return self.path.boundingRect().adjusted(-2, -2, 2, 2)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSceneHasChanged:
            self.sync_visibility_mode()
        return super().itemChange(change, value)

    def update_path(self):
        """Recalculates the path as a straight line."""
        if not (self.start_node and self.end_node):
            return

        effective_start = self._get_effective_endpoint(self.start_node)
        effective_end = self._get_effective_endpoint(self.end_node)

        if effective_start == effective_end:
            self.setVisible(False)
            return
        else:
            self.setVisible(True)

        self.prepareGeometryChange()
        
        start_rect = self._get_visual_rect(effective_start)
        end_rect = self._get_visual_rect(effective_end)

        start_scene_pos = effective_start.mapToScene(QPointF(start_rect.width() / 2, start_rect.height()))
        end_scene_pos = effective_end.mapToScene(QPointF(end_rect.width() / 2, 0))

        start_pos = self.mapFromScene(start_scene_pos)
        end_pos = self.mapFromScene(end_scene_pos)

        self.path = QPainterPath()
        self.path.moveTo(start_pos)
        self.path.lineTo(end_pos)
        self.update()

    def startArrowAnimation(self):
        """Starts the arrow animation."""
        if not self.is_animating:
            self.is_animating = True
            self.arrows = []
            path_length = self.path.length()
            current_distance = 0
            while current_distance < path_length:
                self.arrows.append({'pos': current_distance / path_length, 'distance': current_distance})
                current_distance += self.arrow_spacing
            self.animation_timer.start(16)
            self.update()

    def stopArrowAnimation(self):
        """Stops the arrow animation."""
        self.is_animating = False
        self.animation_timer.stop()
        self.arrows.clear()
        self.update()

    def updateArrows(self):
        """Updates arrow positions for animation."""
        if not self.is_animating: return
        path_length = self.path.length()
        for arrow in self.arrows:
            arrow['distance'] += self.animation_speed
            arrow['pos'] = arrow['distance'] / path_length
        self.arrows = [a for a in self.arrows if a['pos'] < 1]
        if not self.arrows or self.arrows[0]['distance'] >= self.arrow_spacing:
            self.arrows.insert(0, {'pos': 0, 'distance': 0})
        self.update()

    def drawArrow(self, painter, pos, color):
        """Draws a single arrow on the path."""
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
        
    def paint(self, painter, option, widget=None):
        """Paints the dash-dot connection line."""
        palette = get_current_palette()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(palette.AI_NODE, 1.5, Qt.PenStyle.DashDotLine)
        painter.setPen(pen)
        painter.drawPath(self.path)

        if self.is_animating:
            for arrow in self.arrows:
                self.drawArrow(painter, arrow['pos'], palette.AI_NODE)

    def hoverEnterEvent(self, event):
        """Handles hover enter event."""
        self.hover = True
        self.hover_start_timer.start(500)
        self.sync_visibility_mode()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        """Handles hover leave event."""
        self.hover = False
        self.hover_start_timer.stop()
        self.stopArrowAnimation()
        self.sync_visibility_mode()
        super().hoverLeaveEvent(event)

class ThinkingConnectionItem(ContentConnectionItem):
    """
    A specialized connection item with a fine dotted line style, used to link a
    ChatNode to its associated ThinkingNode.
    """
    def paint(self, painter, option, widget=None):
        """Paints the dotted connection line."""
        if self.end_node and getattr(self.end_node, 'is_docked', False):
            return

        palette = get_current_palette()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        pen_color = QColor("#95a5a6") # A soft gray-blue
        if self.hover:
            pen_color = pen_color.lighter(130)

        pen = QPen(pen_color, 1.5, Qt.PenStyle.DotLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawPath(self.path)

        if self.is_animating:
            for arrow in self.arrows:
                self.drawArrow(painter, arrow['pos'], pen_color)

class SystemPromptConnectionItem(QGraphicsItem):
    """
    A visually distinct connection with a pulsing effect, used to link a
    System Prompt Note to the root of a conversation branch.
    """
    def __init__(self, start_node, end_node):
        """
        Initializes the SystemPromptConnectionItem.

        Args:
            start_node (Note): The note acting as the system prompt.
            end_node (ChatNode): The root node of the conversation branch.
        """
        super().__init__()
        self.start_node = start_node # This will be a Note
        self.end_node = end_node     # This will be a ChatNode
        self.setZValue(-1)
        self.setAcceptHoverEvents(True)
        self.path = QPainterPath()
        self.hovered = False
        self._pulse_value = 0.0

        # Animation for the pulsing effect
        self.pulse_animation = QVariantAnimation()
        self.pulse_animation.setStartValue(2.0)
        self.pulse_animation.setEndValue(4.0)
        self.pulse_animation.setDuration(1500)
        self.pulse_animation.setLoopCount(-1)
        self.pulse_animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        self.pulse_animation.valueChanged.connect(self._on_pulse_update)
        self.pulse_animation.start()
        
        self.update_path()
        self.sync_visibility_mode()

    def sync_visibility_mode(self):
        _sync_connection_visibility_mode(self)

    def _on_pulse_update(self, value):
        """Slot to update the pulse value from the animation and trigger a repaint."""
        self._pulse_value = value
        self.update()

    def itemChange(self, change, value):
        """Stops the animation when the item is removed from the scene."""
        if change == QGraphicsItem.ItemSceneHasChanged:
            if self.pulse_animation:
                self.pulse_animation.stop()
            self.sync_visibility_mode()
        return super().itemChange(change, value)

    def _get_visual_rect(self, item):
        """Helper to get the visual rectangle of an item."""
        if hasattr(item, 'is_collapsed') and item.is_collapsed:
            if hasattr(item, 'COLLAPSED_WIDTH') and hasattr(item, 'COLLAPSED_HEIGHT'):
                return QRectF(0, 0, item.COLLAPSED_WIDTH, item.COLLAPSED_HEIGHT)
        if hasattr(item, 'rect'): # Frame, Container
            return item.rect
        elif hasattr(item, 'width') and hasattr(item, 'height'): # Nodes, Charts, Notes
            return QRectF(0, 0, item.width, item.height)
        return item.boundingRect()

    def _get_effective_endpoint(self, item):
        """Helper to find the effective endpoint if inside a collapsed container."""
        current = item
        while current:
            parent = current.parentItem()
            if isinstance(parent, Container) and parent.is_collapsed:
                return parent
            current = parent
        return item

    def boundingRect(self):
        """Returns the bounding rectangle of the item."""
        return self.path.boundingRect().adjusted(-5, -5, 5, 5)

    def update_path(self):
        """Recalculates the path as a curved line."""
        if not (self.start_node and self.end_node):
            return

        effective_start = self._get_effective_endpoint(self.start_node)
        effective_end = self._get_effective_endpoint(self.end_node)
        
        if effective_start == effective_end:
            self.setVisible(False)
            return
        else:
            self.setVisible(True)

        self.prepareGeometryChange()
        
        start_rect = self._get_visual_rect(effective_start)
        end_rect = self._get_visual_rect(effective_end)

        start_scene_pos = effective_start.mapToScene(QPointF(start_rect.width() / 2, start_rect.height()))
        end_scene_pos = effective_end.mapToScene(QPointF(end_rect.width() / 2, 0))

        start_pos = self.mapFromScene(start_scene_pos)
        end_pos = self.mapFromScene(end_scene_pos)

        self.path = QPainterPath()
        self.path.moveTo(start_pos)
        
        # Create a gentle curve for the path
        dy = end_pos.y() - start_pos.y()
        ctrl1 = QPointF(start_pos.x(), start_pos.y() + dy / 2)
        ctrl2 = QPointF(end_pos.x(), end_pos.y() - dy / 2)
        
        self.path.cubicTo(ctrl1, ctrl2, end_pos)
        self.update()
        
    def paint(self, painter, option, widget=None):
        """Paints the pulsing connection line."""
        palette = get_current_palette()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        base_color = QColor(palette.FRAME_COLORS["Purple Header"]["color"])
        if self.hovered:
            base_color = base_color.lighter(130)

        gradient = QLinearGradient(self.path.pointAtPercent(0), self.path.pointAtPercent(1))
        gradient.setColorAt(0, base_color.lighter(110))
        gradient.setColorAt(1, base_color)

        # The pen width is driven by the pulse animation value
        pen = QPen(QBrush(gradient), self._pulse_value, Qt.PenStyle.SolidLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawPath(self.path)

    def hoverEnterEvent(self, event):
        """Handles hover enter event."""
        self.hovered = True
        self.sync_visibility_mode()
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        """Handles hover leave event."""
        self.hovered = False
        self.sync_visibility_mode()
        self.update()
        super().hoverLeaveEvent(event)

class PyCoderConnectionItem(QGraphicsItem):
    """
    A specialized connection for PyCoder nodes, featuring a purple dashed line.
    """
    def __init__(self, start_node, end_node):
        """
        Initializes the PyCoderConnectionItem.

        Args:
            start_node (QGraphicsItem): The parent node.
            end_node (PyCoderNode): The child PyCoder node.
        """
        super().__init__()
        self.start_node = start_node
        self.end_node = end_node
        self.setZValue(-1)
        self.path = QPainterPath()
        self.setAcceptHoverEvents(True)
        self.hover = False
        
        self.hover_start_timer = QTimer()
        self.hover_start_timer.setSingleShot(True)
        self.hover_start_timer.timeout.connect(self.startArrowAnimation)
        
        self.animation_timer = QTimer()
        self.animation_timer.timeout.connect(self.updateArrows)
        
        self.arrows = []
        self.arrow_spacing = 30
        self.arrow_size = 8
        self.animation_speed = 1.5
        self.is_animating = False
        self.update_path()
        self.sync_visibility_mode()

    def sync_visibility_mode(self):
        _sync_connection_visibility_mode(self)

    def _get_visual_rect(self, item):
        """Helper to get the visual rectangle of an item."""
        if hasattr(item, 'is_collapsed') and item.is_collapsed:
            if hasattr(item, 'COLLAPSED_WIDTH') and hasattr(item, 'COLLAPSED_HEIGHT'):
                return QRectF(0, 0, item.COLLAPSED_WIDTH, item.COLLAPSED_HEIGHT)
        if hasattr(item, 'rect'):
            return item.rect
        elif hasattr(item, 'width') and hasattr(item, 'height'):
            return QRectF(0, 0, item.width, item.height)
        return item.boundingRect()

    def _get_effective_endpoint(self, item):
        """Helper to find the effective endpoint if inside a collapsed container."""
        current = item
        while current:
            parent = current.parentItem()
            if isinstance(parent, Container) and parent.is_collapsed:
                return parent
            current = parent
        return item

    def boundingRect(self):
        """Returns the bounding rectangle of the item."""
        return self.path.boundingRect().adjusted(-2, -2, 2, 2)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSceneHasChanged:
            self.sync_visibility_mode()
        return super().itemChange(change, value)

    def update_path(self):
        """Recalculates the path of the connection."""
        if not (self.start_node and self.end_node):
            return

        effective_start = self._get_effective_endpoint(self.start_node)
        effective_end = self._get_effective_endpoint(self.end_node)

        if effective_start == effective_end:
            self.setVisible(False)
            return
        else:
            self.setVisible(True)

        self.prepareGeometryChange()

        start_rect = self._get_visual_rect(effective_start)
        end_rect = self._get_visual_rect(effective_end)

        start_offset = getattr(effective_start, 'CONNECTION_DOT_OFFSET', 0)
        end_offset = getattr(effective_end, 'CONNECTION_DOT_OFFSET', 0)

        start_scene_pos = effective_start.mapToScene(QPointF(start_rect.width() + start_offset, start_rect.height() / 2))
        end_scene_pos = effective_end.mapToScene(QPointF(0 - end_offset, end_rect.height() / 2))

        start_pos = self.mapFromScene(start_scene_pos)
        end_pos = self.mapFromScene(end_scene_pos)

        path = QPainterPath()
        path.moveTo(start_pos)

        scene = self.scene()
        if scene and scene.orthogonal_routing:
            mid_x = start_pos.x() + (end_pos.x() - start_pos.x()) / 2
            path.lineTo(mid_x, start_pos.y())
            path.lineTo(mid_x, end_pos.y())
            path.lineTo(end_pos)
        else:
            dx = end_pos.x() - start_pos.x()
            distance = min(abs(dx) / 2, 100)
            ctrl1 = QPointF(start_pos.x() + distance, start_pos.y())
            ctrl2 = QPointF(end_pos.x() - distance, end_pos.y())
            path.cubicTo(ctrl1, ctrl2, end_pos)
            
        self.path = path
        self.update()

    def startArrowAnimation(self):
        """Starts the arrow animation."""
        if not self.is_animating:
            self.is_animating = True
            self.arrows = []
            path_length = self.path.length()
            current_distance = 0
            while current_distance < path_length:
                self.arrows.append({'pos': current_distance / path_length, 'distance': current_distance})
                current_distance += self.arrow_spacing
            self.animation_timer.start(16)
            self.update()

    def stopArrowAnimation(self):
        """Stops the arrow animation."""
        self.is_animating = False
        self.animation_timer.stop()
        self.arrows.clear()
        self.update()

    def updateArrows(self):
        """Updates arrow positions for animation."""
        if not self.is_animating: return
        path_length = self.path.length()
        for arrow in self.arrows:
            arrow['distance'] += self.animation_speed
            arrow['pos'] = arrow['distance'] / path_length
        self.arrows = [a for a in self.arrows if a['pos'] < 1]
        if not self.arrows or self.arrows[0]['distance'] >= self.arrow_spacing:
            self.arrows.insert(0, {'pos': 0, 'distance': 0})
        self.update()

    def drawArrow(self, painter, pos, color):
        """Draws a single arrow on the path."""
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

    def paint(self, painter, option, widget=None):
        """Paints the purple dashed line."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = get_current_palette()
        pycoder_color = QColor(palette.FRAME_COLORS["Purple Header"]["color"])
        pen = QPen(pycoder_color, 2, Qt.PenStyle.DashLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawPath(self.path)

        if self.is_animating:
            for arrow in self.arrows:
                self.drawArrow(painter, arrow['pos'], pycoder_color)

    def hoverEnterEvent(self, event):
        """Handles hover enter event."""
        self.hover = True
        self.hover_start_timer.start(500)
        self.sync_visibility_mode()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        """Handles hover leave event."""
        self.hover = False
        self.hover_start_timer.stop()
        self.stopArrowAnimation()
        self.sync_visibility_mode()
        super().hoverLeaveEvent(event)

class ConversationConnectionItem(ConnectionItem):
    """
    A visually distinct connection for ConversationNodes, featuring a purple dashed line.
    This class inherits from ConnectionItem and overrides the paint method.
    """
    def paint(self, painter, option, widget=None):
        """
        Handles the custom painting of the connection line.

        Args:
            painter (QPainter): The painter object.
            option (QStyleOptionGraphicsItem): Style options.
            widget (QWidget, optional): The widget being painted on. Defaults to None.
        """
        if not (self.start_node and self.end_node):
            return
            
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = get_current_palette()
        node_color = QColor(palette.FRAME_COLORS["Purple"]["color"])

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
        """Draws a single animated arrow on the path."""
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

class ReasoningConnectionItem(ConnectionItem):
    """
    A visually distinct connection for ReasoningNode, featuring a blue dash-dot-dot line.
    """
    def paint(self, painter, option, widget=None):
        """
        Handles the custom painting of the connection line.

        Args:
            painter (QPainter): The painter object.
            option (QStyleOptionGraphicsItem): Style options.
            widget (QWidget, optional): The widget being painted on. Defaults to None.
        """
        if not (self.start_node and self.end_node):
            return
            
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = get_current_palette()
        node_color = QColor(palette.FRAME_COLORS["Blue"]["color"])

        pen = QPen(node_color, 2, Qt.PenStyle.DashDotDotLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)

        if self.hover:
            pen.setWidth(3)
        
        painter.setPen(pen)
        painter.drawPath(self.path)

        if self.is_animating:
            for arrow in self.arrows:
                self.drawArrow(painter, arrow['pos'], node_color)

    def drawArrow(self, painter, pos, color):
        """Draws a single animated arrow on the path."""
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

class GroupSummaryConnectionItem(ConnectionItem):
    """
    A connection from a ChatNode (source) to a summary Note (destination).
    It is visually distinct and typically connects from the top of the ChatNode
    to the bottom of the Note.
    """
    def __init__(self, start_node, end_node):
        """
        Initializes the GroupSummaryConnectionItem.

        Args:
            start_node (ChatNode): The source node being summarized.
            end_node (Note): The destination note containing the summary.
        """
        super().__init__(start_node, end_node)
        self.setZValue(-2) # Draw behind regular connections
        self.animation_speed = 1.0 
        self.arrow_size = 8

    def update_path(self):
        """Recalculates the path for the summary connection."""
        if not (self.start_node and self.end_node and self.start_node.scene() and self.end_node.scene()):
            return

        effective_start = self._get_effective_endpoint(self.start_node)
        effective_end = self._get_effective_endpoint(self.end_node)

        if effective_start == effective_end:
            self.setVisible(False)
            return
        else:
            self.setVisible(True)

        self.prepareGeometryChange()

        start_rect = self._get_visual_rect(effective_start)
        end_rect = self._get_visual_rect(effective_end)
        
        # Connect from top-center of source to bottom-center of destination
        start_scene_pos = effective_start.mapToScene(QPointF(start_rect.center().x(), 0))
        end_scene_pos = effective_end.mapToScene(QPointF(end_rect.center().x(), end_rect.height()))

        start_pos = self.mapFromScene(start_scene_pos)
        end_pos = self.mapFromScene(end_scene_pos)

        new_path = QPainterPath()
        new_path.moveTo(start_pos)
        
        scene = self.scene()
        use_orthogonal = scene and scene.orthogonal_routing and not self.pins
        
        if use_orthogonal:
            mid_y = start_pos.y() + (end_pos.y() - start_pos.y()) / 2
            new_path.lineTo(start_pos.x(), mid_y)
            new_path.lineTo(end_pos.x(), mid_y)
            new_path.lineTo(end_pos)
        elif self.pins:
            sorted_pins = sorted(self.pins, key=lambda p: p.scenePos().y())
            points = [start_pos] + [pin.pos() for pin in sorted_pins] + [end_pos]
            
            for i in range(len(points) - 1):
                p1 = points[i]
                p2 = points[i+1]
                dy = p2.y() - p1.y()
                distance = min(abs(dy) / 2, 150)
                ctrl1 = QPointF(p1.x(), p1.y() + distance)
                ctrl2 = QPointF(p2.x(), p2.y() - distance)
                new_path.cubicTo(ctrl1, ctrl2, p2)
        else:
            dy = end_pos.y() - start_pos.y()
            distance = min(abs(dy) / 2, 150)
            ctrl1 = QPointF(start_pos.x(), start_pos.y() - distance)
            ctrl2 = QPointF(end_pos.x(), end_pos.y() + distance)
            new_path.cubicTo(ctrl1, ctrl2, end_pos)

        self.path = new_path
        self.hover_path = None
        self.update()

    def paint(self, painter, option, widget=None):
        """Paints the gray dashed line for the summary connection."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#888888"), 1.5, Qt.PenStyle.DashLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawPath(self.path)

        if self.is_animating:
            for arrow in self.arrows:
                self.drawArrow(painter, arrow['pos'], 1.0)

    def drawArrow(self, painter, pos, opacity):
        """Draws a single animated arrow for the summary connection."""
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
        
        color = QColor("#888888")
        if self.hover:
            color = QColor("#bbbbbb")
        color.setAlphaF(opacity)
        
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        
        painter.drawPath(arrow)
        painter.restore()

class HtmlConnectionItem(ConnectionItem):
    """
    A specialized connection for HtmlView nodes, featuring an orange dash-dot-dot line.
    """
    def paint(self, painter, option, widget=None):
        """
        Handles the custom painting of the connection line.
        """
        if not (self.start_node and self.end_node):
            return
            
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = get_current_palette()
        node_color = QColor(palette.FRAME_COLORS["Orange"]["color"])

        pen = QPen(node_color, 2, Qt.PenStyle.DashDotDotLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)

        if self.hover:
            pen.setWidth(3)
        
        painter.setPen(pen)
        painter.drawPath(self.path)

        if self.is_animating:
            for arrow in self.arrows:
                self.drawArrow(painter, arrow['pos'], node_color)

    def drawArrow(self, painter, pos, color):
        """Draws a single animated arrow on the path."""
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
