"""Canvas container item for owning and collapsing grouped scene items."""

import qtawesome as qta

from PySide6.QtWidgets import QDialog, QGraphicsItem, QGraphicsProxyWidget
from PySide6.QtCore import Qt, QRectF, QPointF, QTimer, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QPainter, QColor, QBrush, QPen, QFont, QPainterPath, QLinearGradient, QCursor

from .graphite_canvas_base import CanvasHeaderLineEdit, GhostFrame, update_connections_for_items
from .graphite_canvas_dialogs import ColorPickerDialog
from graphite_config import get_current_palette, get_graph_node_colors


class Container(QGraphicsItem):
    """
    An advanced grouping item that acts as a parent to other QGraphicsItems.

    Unlike a Frame, a Container "owns" its children. When the container is moved,
    all contained items move with it. It supports a collapsed state to hide its
    contents and save screen space, and features in-place title editing.
    """
    PADDING = 30
    HEADER_HEIGHT = 40
    COLLAPSED_HEIGHT = 50
    COLLAPSED_WIDTH = 250
    DEFAULT_TITLE = "New Container"
    
    def __init__(self, items, parent=None):
        """
        Initializes the Container.

        Args:
            items (list[QGraphicsItem]): The list of items to be contained.
            parent (QGraphicsItem, optional): The parent item. Defaults to None.
        """
        super().__init__(parent)
        self.contained_items = items
        self.title = self.DEFAULT_TITLE
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsScenePositionChanges)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        
        # State attributes
        self.is_collapsed = False
        self.expanded_rect = QRectF() # Caches the size before collapsing.
        
        self.rect = QRectF()
        self.color = "#3a3a3a"
        self.header_color = None 
        
        # Rects for hover detection of UI buttons in the header.
        self.color_button_rect = QRectF()
        self.collapse_button_rect = QRectF()
        self.color_button_hovered = False
        self.collapse_button_hovered = False
        
        self.hovered = False
        self.editing = False # True when the title is being edited.
        self._disposed = False

        # Animation for the pulsing glow effect in collapsed mode.
        self.pulse_animation = QVariantAnimation()
        self.pulse_animation.setDuration(1500)
        self.pulse_animation.setStartValue(2.0)
        self.pulse_animation.setEndValue(4.0)
        self.pulse_animation.setLoopCount(-1)
        self.pulse_animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        self.pulse_animation.valueChanged.connect(self._on_pulse_animation_tick)

        # Timer to show a preview "ghost frame" on long hover when collapsed.
        self.ghost_frame_timer = QTimer()
        self.ghost_frame_timer.setSingleShot(True)
        self.ghost_frame_timer.setInterval(2000)
        self.ghost_frame_timer.timeout.connect(self._on_ghost_frame_timeout)
        self.ghost_frame_hide_timer = QTimer()
        self.ghost_frame_hide_timer.setSingleShot(True)
        self.ghost_frame_hide_timer.setInterval(3000)
        self.ghost_frame_hide_timer.timeout.connect(self._on_hide_ghost_frame_timeout)
        self.ghost_frame = None

        self.title_editor = CanvasHeaderLineEdit()
        self.title_editor_proxy = QGraphicsProxyWidget(self)
        self.title_editor_proxy.setWidget(self.title_editor)
        self.title_editor_proxy.setZValue(5)
        self.title_editor_proxy.hide()
        self.title_editor.committed.connect(self._commit_title_edit)
        self.title_editor.canceled.connect(self._cancel_title_edit)

        # Re-parent all contained items to this container.
        for item in self.contained_items:
            item.setParentItem(self)

        self.updateGeometry()
        self.setToolTip(self.title)

    def _teardown_async_helpers(self):
        if self._disposed:
            return
        self._disposed = True

        for timer in (self.ghost_frame_timer, self.ghost_frame_hide_timer):
            timer.stop()
            try:
                timer.timeout.disconnect()
            except (TypeError, RuntimeError):
                pass
            timer.deleteLater()

        self.pulse_animation.stop()
        try:
            self.pulse_animation.valueChanged.disconnect()
        except (TypeError, RuntimeError):
            pass
        self.pulse_animation.deleteLater()

        try:
            self._hide_ghost_frame()
        except RuntimeError:
            self.ghost_frame = None

    def dispose(self):
        self._teardown_async_helpers()

    def _on_pulse_animation_tick(self, *_):
        if self._disposed:
            return
        try:
            self.update()
        except RuntimeError:
            self._teardown_async_helpers()

    def _on_ghost_frame_timeout(self):
        if self._disposed:
            return
        try:
            self._show_ghost_frame()
        except RuntimeError:
            self._teardown_async_helpers()

    def _on_hide_ghost_frame_timeout(self):
        if self._disposed:
            return
        try:
            self._hide_ghost_frame()
        except RuntimeError:
            self._teardown_async_helpers()

    def _show_ghost_frame(self):
        """
        Creates and displays the GhostFrame preview when the container is collapsed
        and hovered over for a set duration.
        """
        scene = self.scene()
        if scene and scene.views() and self.is_collapsed:
            # Determine the rectangle to show. Use the cached expanded_rect if valid.
            rect_to_show = self.expanded_rect
            if not rect_to_show.isValid():
                # If no cached rect, calculate it from the hidden children.
                bounding_rect = QRectF()
                for item in self.contained_items:
                    item_rect = item.mapToParent(item.boundingRect()).boundingRect()
                    bounding_rect = bounding_rect.united(item_rect)
                rect_to_show = bounding_rect.adjusted(-self.PADDING, -self.PADDING - self.HEADER_HEIGHT, self.PADDING, self.PADDING)

            if not rect_to_show.isValid():
                return

            # Position the ghost frame centered on the current cursor position.
            view = scene.views()[0]
            cursor_pos_global = QCursor.pos()
            cursor_pos_view = view.mapFromGlobal(cursor_pos_global)
            cursor_pos_scene = view.mapToScene(cursor_pos_view)

            ghost_width = rect_to_show.width()
            ghost_height = rect_to_show.height()

            top_left_pos = QPointF(
                cursor_pos_scene.x() - ghost_width / 2,
                cursor_pos_scene.y() - ghost_height / 2
            )

            self.ghost_frame = GhostFrame(QRectF(0, 0, ghost_width, ghost_height))
            self.ghost_frame.setPos(top_left_pos)
            scene.addItem(self.ghost_frame)
            
            # Set a timer to automatically hide the ghost frame.
            self.ghost_frame_hide_timer.start()

    def _hide_ghost_frame(self):
        """Removes the GhostFrame from the scene if it exists."""
        if self.ghost_frame and self.ghost_frame.scene():
            self.ghost_frame.scene().removeItem(self.ghost_frame)
        self.ghost_frame = None

    def boundingRect(self):
        """Returns the bounding rectangle of the item, with a small margin."""
        return self.rect.adjusted(-5, -5, 5, 5)

    def _header_text_rect(self):
        return QRectF(
            self.rect.left() + 12,
            self.rect.top() + 7,
            max(80, self.rect.width() - 96),
            self.HEADER_HEIGHT - 14,
        )

    def _update_title_editor_geometry(self):
        text_rect = self._header_text_rect()
        self.title_editor_proxy.setPos(text_rect.topLeft())
        self.title_editor.setFixedSize(max(80, int(text_rect.width())), max(24, int(text_rect.height())))

    def _normalize_title(self, text):
        text = text.strip()
        return text or self.DEFAULT_TITLE

    def _begin_title_editing(self):
        if self.is_collapsed:
            return
        self.editing = True
        self._update_title_editor_geometry()
        self.title_editor_proxy.show()
        self.title_editor.begin(self.title)
        self.update()

    def _commit_title_edit(self, text=None):
        if not self.editing:
            return
        self.title = self._normalize_title(text if text is not None else self.title_editor.text())
        self.title_editor_proxy.hide()
        self.editing = False
        self.setToolTip(self.title)
        self.update()

    def _cancel_title_edit(self):
        if not self.editing:
            return
        self.title_editor_proxy.hide()
        self.editing = False
        self.update()

    def updateGeometry(self):
        """
        Recalculates the container's bounding rectangle based on its state
        (collapsed or expanded) and the geometry of its contained items.
        """
        self.prepareGeometryChange()
        if self.is_collapsed:
            # Use fixed dimensions when collapsed.
            self.rect = QRectF(0, 0, self.COLLAPSED_WIDTH, self.COLLAPSED_HEIGHT)
        else:
            if not self.contained_items:
                # If empty, use default dimensions.
                self.rect = QRectF(0, 0, 300, 150)
                self.expanded_rect = self.rect
            else:
                # Calculate the union of all contained items' bounding rects.
                bounding_rect = QRectF()
                for item in self.contained_items:
                    item_rect = item.mapToParent(item.boundingRect()).boundingRect()
                    bounding_rect = bounding_rect.united(item_rect)

                # Adjust the final rect to include padding and header height.
                self.rect = bounding_rect.adjusted(-self.PADDING, -self.PADDING - self.HEADER_HEIGHT, self.PADDING, self.PADDING)
                self.expanded_rect = self.rect # Cache this size for when we collapse.

        self._update_title_editor_geometry()
        
        # Notify the scene that this item and its children have effectively moved.
        scene = self.scene()
        if scene:
            for item in self.contained_items:
                scene.nodeMoved(item)
            scene.nodeMoved(self)

    def get_connection_endpoints(self):
        """Return all descendant items that may own connections."""
        from .graphite_canvas_frame import Frame

        endpoints = []
        for item in self.contained_items:
            endpoints.append(item)
            if isinstance(item, Container):
                endpoints.extend(item.get_connection_endpoints())
            elif isinstance(item, Frame):
                endpoints.extend(item.get_connection_endpoints())
        return endpoints

    def _update_child_connections(self):
        """
        Forces an update of all connections attached to any item inside this container.
        This is necessary after the container moves or resizes.
        """
        update_connections_for_items(self.scene(), self.get_connection_endpoints())

    def toggle_collapse(self):
        """Toggles the container between its collapsed and expanded states."""
        # Store the center point to re-center the container after resizing.
        scene_center = self.mapToScene(self.rect.center())
        
        # Cache the expanded rect just before collapsing.
        if not self.is_collapsed:
            self.expanded_rect = self.rect
        
        self.is_collapsed = not self.is_collapsed
        
        # Show/hide contained items and start/stop pulsing animation.
        if self.is_collapsed:
            for item in self.contained_items:
                item.setVisible(False)
            self.pulse_animation.start()
        else:
            for item in self.contained_items:
                item.setVisible(True)
            self.pulse_animation.stop()

        # Recalculate geometry and reposition to maintain the center point.
        self.updateGeometry()
        new_pos = scene_center - self.rect.center()
        self.setPos(new_pos)

        # Update connections.
        self._update_child_connections()
        if self.scene():
            self.scene().update_connections()

    def paint(self, painter, option, widget=None):
        """Handles the custom painting of the container."""
        palette = get_current_palette()
        node_colors = get_graph_node_colors()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # --- Collapsed State Painting ---
        if self.is_collapsed:
            # Draw the pulsing glow effect.
            pulse_value = self.pulse_animation.currentValue() or 0.0
            glow_color = QColor(node_colors["selected_outline"])
            glow_color.setAlpha(100)
            painter.setPen(QPen(glow_color, pulse_value))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(self.rect, 10, 10)

            # Draw the main collapsed body.
            path = QPainterPath()
            path.addRoundedRect(self.rect, 10, 10)
            base_color = QColor(self.color)
            painter.setPen(QPen(node_colors["selected_outline"], 2))
            painter.setBrush(base_color)
            painter.drawPath(path)

            self.collapse_button_rect = QRectF(self.rect.right() - 34, self.rect.top() + 10, 24, 24)

            # Draw the title text in collapsed mode.
            painter.setPen(QColor("#ffffff"))
            font = QFont("Segoe UI", 12, QFont.Weight.Bold)
            painter.setFont(font)
            title_rect = QRectF(self.rect.left() + 14, self.rect.top(), self.rect.width() - 56, self.rect.height())
            display_title = painter.fontMetrics().elidedText(self.title, Qt.TextElideMode.ElideRight, int(title_rect.width()))
            painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, display_title)

            expand_icon = qta.icon(
                'fa5s.expand-arrows-alt',
                color='#ffffff' if self.collapse_button_hovered or self.hovered else '#9a9a9a',
            )
            expand_icon.paint(painter, self.collapse_button_rect.adjusted(3, 3, -3, -3).toRect())
            return

        # --- Expanded State Painting ---
        # Draw the main body with a gradient.
        gradient = QLinearGradient(self.rect.topLeft(), self.rect.bottomLeft())
        base_color = QColor(self.color)
        gradient.setColorAt(0, base_color)
        gradient.setColorAt(1, base_color.darker(120))
    
        outline_color = node_colors["selected_outline"] if self.isSelected() else node_colors["hover_outline"] if self.hovered else node_colors["border"]
        
        path = QPainterPath()
        path.addRoundedRect(self.rect, 10, 10)
    
        painter.setPen(QPen(outline_color, 2))
        painter.setBrush(QBrush(gradient))
        painter.drawPath(path)
    
        # Draw the header area with its own gradient.
        header_rect = QRectF(self.rect.left(), self.rect.top(), self.rect.width(), self.HEADER_HEIGHT)
        header_path = QPainterPath()
        header_path.addRoundedRect(header_rect, 10, 10)
        
        header_gradient = QLinearGradient(header_rect.topLeft(), header_rect.bottomLeft())
        header_base_color = QColor(self.header_color) if self.header_color else QColor(node_colors["header_start"])
        header_gradient.setColorAt(0, header_base_color)
        header_gradient.setColorAt(1, QColor(node_colors["header_end"]))

        painter.setBrush(QBrush(header_gradient))
        painter.drawPath(header_path)
    
        # Define and draw the collapse and color buttons in the header.
        self.collapse_button_rect = QRectF(self.rect.right() - 68, self.rect.top() + 8, 24, 24)
        self.color_button_rect = QRectF(self.rect.right() - 34, self.rect.top() + 8, 24, 24)
        
        # Draw Collapse Button
        painter.setBrush(QBrush(QColor(node_colors["header_start"])))
        pen_color = node_colors["hover_outline"] if self.collapse_button_hovered else node_colors["border"]
        painter.setPen(QPen(pen_color))
        painter.drawEllipse(self.collapse_button_rect)
        icon = qta.icon('fa5s.compress-arrows-alt', color='white')
        icon.paint(painter, self.collapse_button_rect.adjusted(4, 4, -4, -4).toRect())

        # Draw Color Button
        painter.setPen(QPen(QColor("#ffffff") if self.color_button_hovered else node_colors["border"]))
        painter.setBrush(QBrush(header_base_color))
        painter.drawEllipse(self.color_button_rect)

        # Draw the three dots icon on the color button.
        painter.setPen(QPen(QColor(255, 255, 255, 180)))
        center = self.color_button_rect.center()
        painter.drawEllipse(center + QPointF(-6, 0), 2, 2)
        painter.drawEllipse(center, 2, 2)
        painter.drawEllipse(center + QPointF(6, 0), 2, 2)

        # Draw the title, either in display or editing mode.
        painter.setPen(QPen(QColor("#ffffff")))
        font = QFont("Segoe UI", 10, QFont.Weight.Bold)
        painter.setFont(font)
        text_rect = self._header_text_rect()

        if not self.editing:
            display_title = painter.fontMetrics().elidedText(self.title, Qt.TextElideMode.ElideRight, int(text_rect.width()))
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, display_title)

    def finishEditing(self):
        """Finalizes the title editing process."""
        self._commit_title_edit()

    def mouseDoubleClickEvent(self, event):
        """Handles double-clicks to start title editing or expand if collapsed."""
        if self.is_collapsed:
            super().mouseDoubleClickEvent(event)
            return

        # Start editing if the double-click is in the header area.
        if self._header_text_rect().contains(event.pos()):
            self._begin_title_editing()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        """Handles clicks on the header buttons."""
        if self.is_collapsed:
            if event.button() == Qt.MouseButton.LeftButton and self.collapse_button_rect.contains(event.pos()):
                self.toggle_collapse()
                event.accept()
                return

            if event.button() == Qt.MouseButton.LeftButton and self.scene():
                self.scene().is_dragging_item = True
            super().mousePressEvent(event)
            return

        if self.editing and not self._header_text_rect().contains(event.pos()):
            self.finishEditing()

        if not self.is_collapsed and self.collapse_button_rect.contains(event.pos()):
            self.toggle_collapse()
            event.accept()
        elif not self.is_collapsed and self.color_button_rect.contains(event.pos()):
            self.show_color_picker()
            event.accept()
        else:
            super().mousePressEvent(event)
    
    def hoverMoveEvent(self, event):
        """Updates the hover state of the header buttons."""
        old_collapse_hover = self.collapse_button_hovered
        self.collapse_button_hovered = self.collapse_button_rect.contains(event.pos())
        if self.is_collapsed:
            if old_collapse_hover != self.collapse_button_hovered:
                self.update()
        else:
            self.color_button_hovered = self.color_button_rect.contains(event.pos())
            if self._header_text_rect().contains(event.pos()) and not (self.collapse_button_hovered or self.color_button_hovered):
                self.setCursor(Qt.CursorShape.IBeamCursor)
            else:
                self.unsetCursor()
            self.update()
        super().hoverMoveEvent(event)

    def hoverEnterEvent(self, event):
        """Handles hover enter events."""
        self.hovered = True
        if self.is_collapsed:
            self.ghost_frame_timer.start() # Start timer for ghost preview.
        self.update()
        super().hoverEnterEvent(event)
        
    def hoverLeaveEvent(self, event):
        """Handles hover leave events."""
        self.hovered = False
        self.collapse_button_hovered = False
        self.color_button_hovered = False
        self.unsetCursor()
        self.ghost_frame_timer.stop() # Cancel ghost preview.
        self._hide_ghost_frame()
        self.update()
        super().hoverLeaveEvent(event)
    
    def show_color_picker(self):
        """Opens the color picker dialog to change the container's color."""
        scene = self.scene()
        if not scene or not scene.views():
            return

        view = scene.views()[0]
        dialog = ColorPickerDialog(view)
        # Position the dialog near the color button.
        frame_pos = self.mapToScene(self.color_button_rect.topRight())
        view_pos = view.mapFromScene(frame_pos)
        global_pos = view.mapToGlobal(view_pos)
        dialog.move(global_pos.x() + 10, global_pos.y())
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            color, color_type = dialog.get_selected_color()
            if color_type == "default":
                self.color = "#3a3a3a"
                self.header_color = None
            elif color_type == "full":
                self.color = color
                self.header_color = None
            else: # header
                self.header_color = color
            self.update()

    def itemChange(self, change, value):
        """Handles item changes, such as movement or scene removal."""
        # Clean up timers when the item is removed from the scene.
        if change == QGraphicsItem.ItemSceneHasChanged and value is None:
            self._teardown_async_helpers()
            self.title_editor_proxy.hide()

        # Apply snapping when being moved.
        if change == QGraphicsItem.ItemPositionChange and self.scene() and self.scene().is_dragging_item:
            parent = self.parentItem()
            if parent and isinstance(parent, Container):
                parent.updateGeometry()
            return self.scene().snap_position(self, value)

        # Update child connections after the move is complete.
        if change == QGraphicsItem.ItemPositionHasChanged and self.scene():
            QTimer.singleShot(0, self._update_child_connections)
            self.scene().nodeMoved(self)

        return super().itemChange(change, value)

    def keyPressEvent(self, event):
        """While the embedded editor is open, let it own keyboard input."""
        if self.editing:
            event.accept()
            return

        super().keyPressEvent(event)
