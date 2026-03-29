"""Canvas frame item for visually grouping nodes without owning them."""

import qtawesome as qta

from PySide6.QtWidgets import QDialog, QGraphicsItem, QGraphicsProxyWidget
from PySide6.QtCore import Qt, QRectF, QPointF, QTimer, QVariantAnimation
from PySide6.QtGui import (
    QPainter, QColor, QBrush, QPen, QFont, QPainterPath, QLinearGradient, QConicalGradient
)

from .graphite_canvas_base import CanvasHeaderLineEdit, update_connections_for_items
from .graphite_canvas_dialogs import ColorPickerDialog
from graphite_config import get_current_palette


class Frame(QGraphicsItem):
    """
    A simpler grouping item that acts as a background for other QGraphicsItems.

    Unlike a Container, a Frame does not own its children. It simply draws a
    background behind a group of nodes. Nodes can be moved freely in and out of it.
    It supports resizing via handles and can be "locked" to move its nodes with it.
    """
    PADDING = 30
    HEADER_HEIGHT = 40
    HANDLE_SIZE = 8
    COLLAPSED_WIDTH = 260
    COLLAPSED_HEIGHT = 50
    DEFAULT_NOTE = "Add note..."
    
    def __init__(self, nodes, parent=None):
        """
        Initializes the Frame.

        Args:
            nodes (list[QGraphicsItem]): The list of items to be framed.
            parent (QGraphicsItem, optional): The parent item. Defaults to None.
        """
        super().__init__(parent)
        self.nodes = nodes
        self.note = self.DEFAULT_NOTE
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsScenePositionChanges)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        
        # Load icons for the lock/unlock button.
        self.lock_icon = qta.icon('fa.lock', color='#ffffff')
        self.unlock_icon = qta.icon('fa.unlock-alt', color='#ffffff')
        self.lock_icon_hover = qta.icon('fa.lock', color='#3498db')
        self.unlock_icon_hover = qta.icon('fa.unlock-alt', color='#2DBB6A')
        
        # State attributes
        self.is_locked = True
        self.is_collapsed = False
        self.expanded_rect = QRectF()
        self.rect = QRectF()
        self.color = "#2d2d2d"
        self.header_color = None 
        
        self.collapse_button_rect = QRectF(0, 0, 24, 24)
        self.collapse_button_hovered = False
        self.lock_button_rect = QRectF(0, 0, 24, 24)
        self.lock_button_hovered = False
        self.color_button_rect = QRectF(0, 0, 24, 24)
        self.color_button_hovered = False
        
        self.hovered = False
        self.editing = False
        self._disposed = False
        
        # Resizing handle attributes
        self.handles = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w']
        self.handle_cursors = {
            'nw': Qt.CursorShape.SizeFDiagCursor, 'se': Qt.CursorShape.SizeFDiagCursor,
            'ne': Qt.CursorShape.SizeBDiagCursor, 'sw': Qt.CursorShape.SizeBDiagCursor,
            'n': Qt.CursorShape.SizeVerCursor, 's': Qt.CursorShape.SizeVerCursor,
            'e': Qt.CursorShape.SizeHorCursor, 'w': Qt.CursorShape.SizeHorCursor
        }
        self.handle_rects = {}
        self.resize_handle = None
        self.resizing = False
        self.resize_start_rect = None
        self.resize_start_pos = None

        # Animation for the "unlocked" state outline.
        self.outline_animation = QVariantAnimation()
        self.outline_animation.setDuration(2000)
        self.outline_animation.setStartValue(0.0)
        self.outline_animation.setEndValue(1.0)
        self.outline_animation.setLoopCount(-1)
        self.outline_animation.valueChanged.connect(self._on_outline_animation_tick)
        
        self.title_editor = CanvasHeaderLineEdit()
        self.title_editor_proxy = QGraphicsProxyWidget(self)
        self.title_editor_proxy.setWidget(self.title_editor)
        self.title_editor_proxy.setZValue(5)
        self.title_editor_proxy.hide()
        self.title_editor.committed.connect(self._commit_note_edit)
        self.title_editor.canceled.connect(self._cancel_note_edit)

        self.updateGeometry()
        self._apply_lock_state()
        self.setToolTip(self.note)

    def _teardown_async_helpers(self):
        if self._disposed:
            return
        self._disposed = True
        self.outline_animation.stop()
        try:
            self.outline_animation.valueChanged.disconnect()
        except (TypeError, RuntimeError):
            pass
        self.outline_animation.deleteLater()

    def dispose(self):
        self._teardown_async_helpers()

    def _on_outline_animation_tick(self, *_):
        if self._disposed:
            return
        try:
            self.update()
        except RuntimeError:
            self._teardown_async_helpers()
        
    def _update_child_connections(self):
        """Forces an update of connections attached to nodes within the frame."""
        update_connections_for_items(self.scene(), self.get_connection_endpoints())

    def get_connection_endpoints(self):
        return [node for node in self.nodes if node is not None]

    def _header_text_rect(self):
        return QRectF(
            self.rect.left() + 12,
            self.rect.top() + 7,
            max(80, self.rect.width() - 130),
            self.HEADER_HEIGHT - 14,
        )

    def _update_title_editor_geometry(self):
        text_rect = self._header_text_rect()
        self.title_editor_proxy.setPos(text_rect.topLeft())
        self.title_editor.setFixedSize(max(80, int(text_rect.width())), max(24, int(text_rect.height())))

    def _normalize_note(self, text):
        text = text.strip()
        return text or self.DEFAULT_NOTE

    def _begin_note_editing(self):
        self.editing = True
        self._update_title_editor_geometry()
        self.title_editor_proxy.show()
        self.title_editor.begin(self.note)
        self.update()

    def _commit_note_edit(self, text=None):
        if not self.editing:
            return
        self.note = self._normalize_note(text if text is not None else self.title_editor.text())
        self.title_editor_proxy.hide()
        self.editing = False
        self.setToolTip(self.note)
        self.update()

    def _cancel_note_edit(self):
        if not self.editing:
            return
        self.title_editor_proxy.hide()
        self.editing = False
        self.update()

    def _reparent_node(self, node, new_parent):
        scene_pos = node.scenePos()
        node.setParentItem(new_parent)
        if new_parent:
            node.setPos(new_parent.mapFromScene(scene_pos))
        else:
            node.setPos(scene_pos)

    def _apply_lock_state(self):
        content_attached = self.is_locked or self.is_collapsed
        target_parent = self if content_attached else self.parentItem()

        for node in self.nodes:
            if node is None:
                continue
            if node.parentItem() is not target_parent:
                self._reparent_node(node, target_parent)
            node.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, not content_attached)
            node.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, not content_attached)

    def _content_bounds_in_local_space(self):
        bounds = QRectF()
        for node in self.nodes:
            scene_rect = node.sceneBoundingRect()
            local_rect = QRectF(
                self.mapFromScene(scene_rect.topLeft()),
                self.mapFromScene(scene_rect.bottomRight()),
            ).normalized()
            bounds = bounds.united(local_rect)
        return bounds

    def calculate_minimum_size(self):
        """
        Calculates the smallest possible rectangle that can contain all nodes
        in the frame, including padding. This is used to constrain resizing.
        """
        if not self.nodes:
            return QRectF()

        return self._content_bounds_in_local_space().adjusted(
            -self.PADDING,
            -self.PADDING - self.HEADER_HEIGHT,
            self.PADDING,
            self.PADDING,
        )

    def get_handle_rects(self):
        """
        Calculates the screen rectangles for all eight resize handles.
        It defines a larger "hit" rectangle for easier mouse interaction.
        """
        rects = {}
        rect = self.rect
    
        visual_handle_size = self.HANDLE_SIZE
        hit_handle_size = 16
    
        half_visual = visual_handle_size / 2
        half_hit = hit_handle_size / 2
    
        # Define visual and hit rects for each handle position (nw, ne, se, sw, n, s, e, w).
        rects['nw'] = {
            'visual': QRectF(rect.left() - half_visual, rect.top() - half_visual, visual_handle_size, visual_handle_size),
            'hit': QRectF(rect.left() - half_hit, rect.top() - half_hit, hit_handle_size, hit_handle_size)
        }
        # ... (definitions for other handles) ...
        rects['ne'] = {'visual': QRectF(rect.right() - half_visual, rect.top() - half_visual, visual_handle_size, visual_handle_size), 'hit': QRectF(rect.right() - half_hit, rect.top() - half_hit, hit_handle_size, hit_handle_size)}
        rects['se'] = {'visual': QRectF(rect.right() - half_visual, rect.bottom() - half_visual, visual_handle_size, visual_handle_size), 'hit': QRectF(rect.right() - half_hit, rect.bottom() - half_hit, hit_handle_size, hit_handle_size)}
        rects['sw'] = {'visual': QRectF(rect.left() - half_visual, rect.bottom() - half_visual, visual_handle_size, visual_handle_size), 'hit': QRectF(rect.left() - half_hit, rect.bottom() - half_hit, hit_handle_size, hit_handle_size)}
        rects['n'] = {'visual': QRectF(rect.center().x() - half_visual, rect.top() - half_visual, visual_handle_size, visual_handle_size), 'hit': QRectF(rect.center().x() - half_hit, rect.top() - half_hit, hit_handle_size, hit_handle_size)}
        rects['s'] = {'visual': QRectF(rect.center().x() - half_visual, rect.bottom() - half_visual, visual_handle_size, visual_handle_size), 'hit': QRectF(rect.center().x() - half_hit, rect.bottom() - half_hit, hit_handle_size, hit_handle_size)}
        rects['e'] = {'visual': QRectF(rect.right() - half_visual, rect.center().y() - half_visual, visual_handle_size, visual_handle_size), 'hit': QRectF(rect.right() - half_hit, rect.center().y() - half_hit, hit_handle_size, hit_handle_size)}
        rects['w'] = {'visual': QRectF(rect.left() - half_visual, rect.center().y() - half_visual, visual_handle_size, visual_handle_size), 'hit': QRectF(rect.left() - half_hit, rect.center().y() - half_hit, hit_handle_size, hit_handle_size)}
    
        return rects

    def handle_at(self, pos):
        """
        Determines which resize handle, if any, is at a given position.

        Args:
            pos (QPointF): The position to check, in the frame's local coordinates.

        Returns:
            str or None: The identifier of the handle (e.g., 'nw', 'e'), or None.
        """
        for handle, rects in self.get_handle_rects().items():
            if rects['hit'].contains(pos):
                return handle
        return None

    def updateGeometry(self):
        """
        Recalculates the frame's bounding rectangle to encompass all its nodes.
        This is called when nodes are added, moved, or the frame is unlocked.
        """
        if self.is_collapsed:
            self.prepareGeometryChange()
            self.rect = QRectF(0, 0, self.COLLAPSED_WIDTH, self.COLLAPSED_HEIGHT)
            self._update_title_editor_geometry()
            parent = self.parentItem()
            from .graphite_canvas_container import Container
            if parent and isinstance(parent, Container):
                parent.updateGeometry()
            return

        if not self.nodes:
            if not self.rect.isValid():
                self.prepareGeometryChange()
                self.rect = QRectF(0, 0, 320, 180)
            self._update_title_editor_geometry()
            return
        old_rect = QRectF(self.rect)
        new_rect = self.calculate_minimum_size()
        final_rect = old_rect.united(new_rect) if old_rect.isValid() else new_rect

        if final_rect != self.rect:
            self.prepareGeometryChange()
            self.rect = final_rect
        self.expanded_rect = QRectF(self.rect)
        self._update_title_editor_geometry()

        parent = self.parentItem()
        from .graphite_canvas_container import Container
        if parent and isinstance(parent, Container):
            parent.updateGeometry()

    def boundingRect(self):
        """Returns the bounding rectangle of the item."""
        return self.rect

    def toggle_lock(self):
        """Toggles the locked state of the frame."""
        self.is_locked = not self.is_locked
    
        # Start/stop the "unlocked" animation.
        if not self.is_locked and not self.is_collapsed:
            self.outline_animation.start()
        else:
            self.outline_animation.stop()
        self._apply_lock_state()
        self.updateGeometry()
        self._update_child_connections()
        self.update()

    def toggle_collapse(self):
        """Toggle the frame between compact and expanded states."""
        scene_center = self.mapToScene(self.rect.center()) if self.rect.isValid() else QPointF()

        if self.editing:
            self.finishEditing()

        if not self.is_collapsed and self.rect.isValid():
            self.expanded_rect = QRectF(self.rect)

        self.is_collapsed = not self.is_collapsed

        for node in self.nodes:
            if node is not None:
                node.setVisible(not self.is_collapsed)

        if self.is_collapsed:
            self.outline_animation.stop()
        elif not self.is_locked:
            self.outline_animation.start()

        self._apply_lock_state()
        self.updateGeometry()
        if scene_center != QPointF():
            self.setPos(scene_center - self.rect.center())
        self._update_child_connections()
        if self.scene():
            self.scene().update_connections()
        self.update()

    def mouseDoubleClickEvent(self, event):
        """Starts title editing on a double-click in the header."""
        if self.is_collapsed:
            super().mouseDoubleClickEvent(event)
            return

        if self._header_text_rect().contains(event.pos()):
            self._begin_note_editing()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        """Handles mouse presses for resizing and button clicks."""
        if self.is_collapsed:
            if event.button() == Qt.MouseButton.LeftButton and self.collapse_button_rect.contains(event.pos()):
                self.toggle_collapse()
                event.accept()
                return

            if event.button() == Qt.MouseButton.LeftButton and self.scene():
                self.scene().is_dragging_item = True
            super().mousePressEvent(event)
            return

        if self.isSelected():
            handle = self.handle_at(event.pos())
            if handle:
                # Start resizing if a handle is clicked.
                self.resizing = True
                self.resize_handle = handle
                self.resize_start_rect = self.rect
                self.resize_start_pos = event.pos()
                event.accept()
                return
                
        if self.editing and not self._header_text_rect().contains(event.pos()):
            self.finishEditing()

        if self.color_button_rect.contains(event.pos()):
            self.show_color_picker()
            event.accept()
            return
        elif self.collapse_button_rect.contains(event.pos()):
            self.toggle_collapse()
            event.accept()
            return
        elif self.lock_button_rect.contains(event.pos()):
            self.toggle_lock()
            event.accept()
            return
        
        if event.button() == Qt.MouseButton.LeftButton and self.scene():
            self.scene().is_dragging_item = True
        super().mousePressEvent(event)
        
    def mouseReleaseEvent(self, event):
        """Handles mouse release to stop resizing or dragging."""
        if self.resizing:
            self.resizing = False
            self.resize_handle = None
            self.resize_start_rect = None
            self.resize_start_pos = None
            event.accept()
        
        if self.scene():
            self.scene().is_dragging_item = False
            self.scene()._clear_smart_guides()
            
        super().mouseReleaseEvent(event)
        if self.is_locked:
            self.updateGeometry()

    def mouseMoveEvent(self, event):
        """Handles mouse movement for resizing the frame."""
        if self.resizing and self.resize_handle:
            delta = event.pos() - self.resize_start_pos
            new_rect = QRectF(self.resize_start_rect)

            min_rect = self.calculate_minimum_size()
            min_width = min_rect.width()
            min_height = min_rect.height()
    
            # Snap resize delta to a grid for cleaner resizing.
            grid_size = 10
            delta.setX(round(delta.x() / grid_size) * grid_size)
            delta.setY(round(delta.y() / grid_size) * grid_size)
    
            # Apply delta to the appropriate edges of the rect based on the handle being dragged.
            if 'n' in self.resize_handle:
                max_top = self.resize_start_rect.bottom() - min_height
                new_top = min(self.resize_start_rect.top() + delta.y(), max_top)
                new_rect.setTop(min(new_top, min_rect.top()))
            if 's' in self.resize_handle:
                min_bottom = self.resize_start_rect.top() + min_height
                new_bottom = max(self.resize_start_rect.bottom() + delta.y(), min_bottom)
                new_rect.setBottom(max(new_bottom, min_rect.bottom()))
            if 'w' in self.resize_handle:
                max_left = self.resize_start_rect.right() - min_width
                new_left = min(self.resize_start_rect.left() + delta.x(), max_left)
                new_rect.setLeft(min(new_left, min_rect.left()))
            if 'e' in self.resize_handle:
                min_right = self.resize_start_rect.left() + min_width
                new_right = max(self.resize_start_rect.right() + delta.x(), min_right)
                new_rect.setRight(max(new_right, min_rect.right()))
    
            if new_rect != self.rect:
                self.prepareGeometryChange()
                self.rect = new_rect
                self._update_title_editor_geometry()
                self._update_child_connections()
    
            event.accept()
    
        elif self.is_locked:
            # If locked, move all child nodes along with the frame.
            super().mouseMoveEvent(event)

            if self.scene():
                for node in self.nodes:
                    self.scene().nodeMoved(node)

        else:
            # If unlocked, only the frame moves.
            super().mouseMoveEvent(event)
            if self.scene():
                moving_node = next((node for node in self.nodes if node.isUnderMouse()), None)
                if moving_node:
                    self.scene().nodeMoved(moving_node)
            
    def update_all_connections(self):
        """A utility to force-update all connections related to this frame's nodes."""
        if not self.scene(): return
        for node in self.nodes:
            self.scene().nodeMoved(node)

    def hoverMoveEvent(self, event):
        """Updates UI based on hover position (resize handles, buttons)."""
        if self.is_collapsed:
            old_collapse_hover = self.collapse_button_hovered
            self.collapse_button_hovered = self.collapse_button_rect.contains(event.pos())
            if old_collapse_hover != self.collapse_button_hovered:
                self.update()
            self.unsetCursor()
            super().hoverMoveEvent(event)
            return

        if self.isSelected():
            handle = self.handle_at(event.pos())
            if handle:
                self.setCursor(self.handle_cursors[handle])
                return
                
        old_collapse_hover = self.collapse_button_hovered
        old_lock_hover = self.lock_button_hovered
        old_color_hover = self.color_button_hovered
        
        self.collapse_button_hovered = self.collapse_button_rect.contains(event.pos())
        self.lock_button_hovered = self.lock_button_rect.contains(event.pos())
        self.color_button_hovered = self.color_button_rect.contains(event.pos())
        
        if (
            old_collapse_hover != self.collapse_button_hovered or
            old_lock_hover != self.lock_button_hovered or
            old_color_hover != self.color_button_hovered
        ):
            self.update()

        if self._header_text_rect().contains(event.pos()) and not (
            self.collapse_button_hovered or self.lock_button_hovered or self.color_button_hovered
        ):
            self.setCursor(Qt.CursorShape.IBeamCursor)
        else:
            self.unsetCursor()
        super().hoverMoveEvent(event)

    def hoverEnterEvent(self, event):
        self.hovered = True; self.update(); super().hoverEnterEvent(event)
        
    def hoverLeaveEvent(self, event):
        self.hovered = False; self.collapse_button_hovered = False; self.lock_button_hovered = False; self.color_button_hovered = False
        self.unsetCursor(); self.update(); super().hoverLeaveEvent(event)
        
    def show_color_picker(self):
        """Opens the color picker dialog."""
        scene = self.scene()
        if not scene or not scene.views():
            return

        view = scene.views()[0]
        dialog = ColorPickerDialog(view)
        frame_pos = self.mapToScene(self.color_button_rect.topRight())
        view_pos = view.mapFromScene(frame_pos)
        global_pos = view.mapToGlobal(view_pos)
        dialog.move(global_pos.x() + 10, global_pos.y())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            color, color_type = dialog.get_selected_color()
            if color_type == "default":
                self.color = "#2d2d2d"
                self.header_color = None
            elif color_type == "full":
                self.color = color
                self.header_color = None
            else: # header
                self.header_color = color
            self.update()

    def finishEditing(self):
        """Finalizes title editing."""
        self._commit_note_edit()
        
    def itemChange(self, change, value):
        """Handles item changes."""
        # Clean up animation when removed from scene.
        if change == QGraphicsItem.ItemSceneHasChanged and value is None:
            self._teardown_async_helpers()
            self.title_editor_proxy.hide()

        # Apply snapping when moved.
        if change == QGraphicsItem.ItemPositionChange and self.scene() and self.scene().is_dragging_item:
            parent = self.parentItem()
            from .graphite_canvas_container import Container
            if parent and isinstance(parent, Container): parent.updateGeometry()
            return self.scene().snap_position(self, value)

        # Update child connections after move is complete.
        if change == QGraphicsItem.ItemPositionHasChanged and self.scene():
            QTimer.singleShot(0, self._update_child_connections)
            self.scene().nodeMoved(self)

        return super().itemChange(change, value)

    def keyPressEvent(self, event):
        """While the embedded editor is open, let it own keyboard input."""
        if self.editing:
            event.accept()
            return

        return super().keyPressEvent(event)

    def paint(self, painter, option, widget=None):
        """Handles the custom painting of the frame."""
        palette = get_current_palette()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self.is_collapsed:
            base_color = QColor(self.color)
            outline_color = palette.SELECTION if self.isSelected() else palette.AI_NODE if self.hovered else QColor("#555555")
            path = QPainterPath()
            path.addRoundedRect(self.rect, 10, 10)

            painter.setPen(QPen(outline_color, 2))
            painter.setBrush(QBrush(base_color))
            painter.drawPath(path)

            self.collapse_button_rect = QRectF(self.rect.right() - 34, self.rect.top() + 10, 24, 24)
            self.lock_button_rect = QRectF()
            self.color_button_rect = QRectF()

            painter.setPen(QPen(QColor("#ffffff")))
            font = QFont("Segoe UI", 11, QFont.Weight.Bold)
            painter.setFont(font)
            title_rect = QRectF(self.rect.left() + 14, self.rect.top(), self.rect.width() - 56, self.rect.height())
            display_note = painter.fontMetrics().elidedText(self.note, Qt.TextElideMode.ElideRight, int(title_rect.width()))
            painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, display_note)

            expand_icon = qta.icon(
                'fa5s.expand-arrows-alt',
                color='#ffffff' if self.collapse_button_hovered or self.hovered else '#9a9a9a',
            )
            expand_icon.paint(painter, self.collapse_button_rect.adjusted(3, 3, -3, -3).toRect())
            return
    
        # Draw main body with gradient.
        gradient = QLinearGradient(self.rect.topLeft(), self.rect.bottomLeft())
        base_color = QColor(self.color)
        gradient.setColorAt(0, base_color)
        gradient.setColorAt(1, base_color.darker(120))
    
        # Determine outline color based on state.
        if self.isSelected():
            outline_color = palette.SELECTION
        elif self.hovered:
            outline_color = palette.AI_NODE
        else:
            outline_color = QColor("#555555")
        
        path = QPainterPath()
        path.addRoundedRect(self.rect, 10, 10)
    
        painter.setPen(QPen(outline_color, 2))
        painter.setBrush(QBrush(gradient))
        painter.drawPath(path)
    
        # Draw animated outline if unlocked.
        if not self.is_locked:
            outline_path = QPainterPath()
            outline_path.addRoundedRect(self.rect.adjusted(-2, -2, 2, 2), 10, 10)
            gradient = QConicalGradient(self.rect.center(), 360 * self.outline_animation.currentValue())
            blue, green = palette.AI_NODE, palette.USER_NODE
            gradient.setColorAt(0.0, blue)
            gradient.setColorAt(0.5, green)
            gradient.setColorAt(1.0, blue)
            painter.setPen(QPen(QBrush(gradient), 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(outline_path)
    
        # Draw header.
        header_rect = QRectF(self.rect.left(), self.rect.top(), self.rect.width(), self.HEADER_HEIGHT)
        header_gradient = QLinearGradient(header_rect.topLeft(), header_rect.bottomLeft())
        header_base_color = QColor(self.header_color) if self.header_color else QColor(self.color).lighter(120)
        header_gradient.setColorAt(0, header_base_color)
        header_gradient.setColorAt(1, header_base_color.darker(110))
        header_path = QPainterPath()
        header_path.addRoundedRect(header_rect, 10, 10)
        painter.setBrush(QBrush(header_gradient))
        painter.drawPath(header_path)

        # Draw collapse button.
        self.collapse_button_rect = QRectF(self.rect.right() - 102, self.rect.top() + 8, 24, 24)
        painter.setPen(QPen(QColor("#ffffff") if self.collapse_button_hovered else QColor("#555555")))
        painter.setBrush(QBrush(QColor("#3f3f3f")))
        painter.drawEllipse(self.collapse_button_rect)
        collapse_icon = qta.icon(
            'fa5s.compress-arrows-alt',
            color='#ffffff' if self.collapse_button_hovered else '#bbbbbb',
        )
        collapse_icon.paint(painter, self.collapse_button_rect.adjusted(4, 4, -4, -4).toRect())
    
        # Draw lock button.
        self.lock_button_rect = QRectF(self.rect.right() - 68, self.rect.top() + 8, 24, 24)
        painter.setPen(QPen(palette.USER_NODE if self.lock_button_hovered else QColor("#555555")))
        painter.setBrush(QBrush(QColor("#3f3f3f")))
        painter.drawEllipse(self.lock_button_rect)
        icon = self.lock_icon_hover if self.is_locked and self.lock_button_hovered else self.lock_icon if self.is_locked else self.unlock_icon_hover if self.lock_button_hovered else self.unlock_icon
        icon_size = 18
        icon_pixmap = icon.pixmap(icon_size, icon_size)
        icon_x = self.lock_button_rect.center().x() - icon_size / 2
        icon_y = self.lock_button_rect.center().y() - icon_size / 2
        painter.drawPixmap(int(icon_x), int(icon_y), icon_pixmap)
    
        # Draw color button.
        self.color_button_rect = QRectF(self.rect.right() - 34, self.rect.top() + 8, 24, 24)
        painter.setPen(QPen(QColor("#ffffff") if self.color_button_hovered else QColor("#555555")))
        painter.setBrush(QBrush(QColor(self.header_color if self.header_color else self.color)))
        painter.drawEllipse(self.color_button_rect)
        icon_color = QColor("#ffffff"); icon_color.setAlpha(180)
        painter.setPen(QPen(icon_color))
        circle_size, spacing = 4, 3
        total_width = (circle_size * 3) + (spacing * 2)
        x_start = self.color_button_rect.center().x() - (total_width / 2)
        y_pos = self.color_button_rect.center().y() - (circle_size / 2)
        for i in range(3):
            x_pos = x_start + (i * (circle_size + spacing))
            painter.drawEllipse(QRectF(x_pos, y_pos, circle_size, circle_size))
    
        # Draw title (note).
        painter.setPen(QPen(QColor("#ffffff")))
        font = QFont("Segoe UI", 10)
        painter.setFont(font)
        text_rect = self._header_text_rect()
        if not self.editing:
            display_note = painter.fontMetrics().elidedText(self.note, Qt.TextElideMode.ElideRight, int(text_rect.width()))
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, display_note)
            
        # Draw resize handles if selected.
        if self.isSelected():
            painter.setPen(QPen(palette.SELECTION, 1))
            painter.setBrush(QBrush(palette.SELECTION))
            for handle_rects in self.get_handle_rects().values():
                painter.drawRect(handle_rects['visual'])
