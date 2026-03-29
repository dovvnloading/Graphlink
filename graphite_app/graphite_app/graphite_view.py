from PySide6.QtWidgets import (
    QGraphicsView, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QPushButton,
    QApplication, QLineEdit, QTextEdit, QGraphicsProxyWidget
)
from PySide6.QtCore import Qt, QPointF, QRectF, QTimer, QRect
from PySide6.QtGui import QPainter, QColor, QBrush, QPen, QGuiApplication

from graphite_scene import ChatScene
from graphite_node import ChatNode, CodeNode, DocumentNode, ImageNode
from graphite_connections import ConnectionItem
from graphite_canvas_items import Frame, Note, NavigationPin, ChartItem
from graphite_widgets import CustomScrollBar, GridControl, SearchOverlay, PinOverlay, FontControl
from graphite_minimap import MinimapWidget

class ChatView(QGraphicsView):
    """
    The main graphical view for displaying and interacting with the chat scene.

    This class handles user input for navigation (panning, zooming), item selection,
    and manages the rendering of the background grid and overlay widgets like the
    minimap and control panels.
    """
    def __init__(self, window):
        """
        Initializes the ChatView.

        Args:
            window (QMainWindow): The main application window, used for context.
        """
        super().__init__()
        self.window = window
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        
        # Disable default scrollbars to use custom ones.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        # Initialize custom scrollbars.
        self.v_scrollbar = CustomScrollBar(Qt.Orientation.Vertical, self)
        self.h_scrollbar = CustomScrollBar(Qt.Orientation.Horizontal, self)
        
        # Connect custom scrollbars to the view's internal scrollbars.
        self.v_scrollbar.valueChanged.connect(lambda v: self.verticalScrollBar().setValue(int(v)))
        self.h_scrollbar.valueChanged.connect(lambda h: self.horizontalScrollBar().setValue(int(h)))
        
        self.setScene(ChatScene(window))
        
        # Set interaction modes.
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        
        # State attributes for panning and zooming.
        self._panning = False
        self._last_mouse_pos = None
        self._zoom_factor = 1.0
        self._drag_factor = 1.0  # For controlling pan speed.
        
        # State attributes for "zoom to selection" functionality.
        self._expanding = False
        self._expand_start = None
        self._expand_rect = None
        self._original_transform = None
        
        self._setup_drag_control()
        
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        
        # Initialize overlay control widgets.
        self.grid_control = GridControl(self)
        self.grid_control.snapToGridChanged.connect(self._on_snap_toggled)
        self.grid_control.orthogonalConnectionsChanged.connect(self._on_ortho_toggled)
        self.grid_control.smartGuidesChanged.connect(self._on_guides_toggled)
        self.grid_control.fadeConnectionsChanged.connect(self._on_fade_connections_toggled)
        
        self.font_control = FontControl(self)
        self.font_control.fontFamilyChanged.connect(self.scene().setFontFamily)
        self.font_control.fontSizeChanged.connect(self.scene().setFontSize)
        self.font_control.fontColorChanged.connect(self.scene().setFontColor)
        
        # Hide overlays by default.
        self.control_widget.setVisible(False)
        self.grid_control.setVisible(False)
        self.font_control.setVisible(False)
        
        self._current_mouse_pos = None
        
        # Set a very large scene rect to allow extensive panning.
        self.setSceneRect(-100000, -100000, 200000, 200000)

        # Attributes for keyboard-based panning (WASD).
        self.keys_pressed = set()
        self.pan_speed = 15
        self.pan_timer = QTimer(self)
        self.pan_timer.setInterval(16) # approx 60fps
        self.pan_timer.timeout.connect(self._handle_key_pan)

        # Initialize the minimap widget.
        self.minimap_widget = MinimapWidget(self.scene(), self)
        self.minimap_widget.nodeSelected.connect(self._on_minimap_node_selected)
        self.scene().scene_changed.connect(self.minimap_widget.update_nodes)
        self._highlighted_from_nav_node = None
        self._controls_overlay_visible = False

        self._initial_show = True
        
    def _clear_nav_highlight(self):
        """Removes the navigation highlight from the previously navigated node."""
        if self._highlighted_from_nav_node and self._highlighted_from_nav_node.scene() == self.scene():
            self._highlighted_from_nav_node.is_last_navigated = False
            self._highlighted_from_nav_node.update()
        self._highlighted_from_nav_node = None

    def _on_minimap_node_selected(self, target_node):
        """
        Handles node selection from the minimap, centering the view on the
        target node and highlighting the previously selected node.

        Args:
            target_node (ChatNode): The node selected in the minimap.
        """
        selected = self.scene().selectedItems()
        previous_node = None
        if selected and isinstance(selected[0], ChatNode):
            previous_node = selected[0]

        self._clear_nav_highlight()

        self.scene().clearSelection()
        target_node.setSelected(True)
        self.centerOn(target_node)
        self.window.setCurrentNode(target_node)

        # Highlight the previous node to show where the user navigated from.
        if previous_node and previous_node != target_node:
            self._highlighted_from_nav_node = previous_node
            self._highlighted_from_nav_node.is_last_navigated = True
            self._highlighted_from_nav_node.update()

    def _on_snap_toggled(self, checked):
        """Slot to enable or disable snap-to-grid in the scene."""
        self.scene().snap_to_grid = checked

    def _on_ortho_toggled(self, checked):
        """Slot to enable or disable orthogonal connection routing in the scene."""
        self.scene().orthogonal_routing = checked
        self.scene().update_connections()

    def _on_guides_toggled(self, checked):
        """Slot to enable or disable smart alignment guides in the scene."""
        self.scene().smart_guides = checked

    def _on_fade_connections_toggled(self, checked):
        """Slot to enable or disable low-visibility connection rendering."""
        self.scene().setFadeConnectionsEnabled(checked)

    def showEvent(self, event):
        """
        Overrides the show event to correctly position overlays on first launch.

        Args:
            event (QShowEvent): The show event.
        """
        super().showEvent(event)
        # We only need to do this on the very first show event.
        if self._initial_show:
            self._update_overlay_positions()
            self.minimap_widget.update_nodes()
            self._initial_show = False

    def dragEnterEvent(self, event):
        """Accepts drag events if they contain file URLs."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        """Accepts drag move events with file URLs."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        """
        Handles dropped files by staging them for the next message.

        Args:
            event (QDropEvent): The drop event.
        """
        if event.mimeData().hasUrls():
            file_paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
            if file_paths and hasattr(self.window, 'stage_dropped_files'):
                self.window.stage_dropped_files(file_paths)
        event.acceptProposedAction()

    def _setup_drag_control(self):
        """Initializes the UI for the pan speed control widget."""
        from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QPushButton
        self.control_widget = QWidget(self)
        self.control_widget.setObjectName("dragControlPanel")
        main_layout = QVBoxLayout(self.control_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(6)

        self.control_widget.setStyleSheet("""
            QWidget#dragControlPanel {
                background-color: rgba(24, 24, 24, 0.88);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 10px;
            }
            QLabel, QSlider, QPushButton {
                background-color: transparent;
                border: none;
            }
        """)

        icon_slider_layout = QHBoxLayout()
        icon_slider_layout.setContentsMargins(0, 0, 0, 0)
        icon_slider_layout.setSpacing(10)

        label = QLabel("Drag", self.control_widget)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFixedWidth(30)
        label.setStyleSheet("""
            QLabel {
                background-color: rgba(0, 0, 0, 0); border-radius: 5px; font-size: 10px;
                font-weight: bold; color: #cccccc;
            }
        """)
        icon_slider_layout.addWidget(label)

        self.drag_slider = QSlider(Qt.Orientation.Horizontal, self.control_widget)
        self.drag_slider.setFixedWidth(130)
        self.drag_slider.setMinimum(10)
        self.drag_slider.setMaximum(100)
        self.drag_slider.setValue(100)
        self.drag_slider.valueChanged.connect(self._update_drag)
        self.drag_slider.setToolTip(f"{self.drag_slider.value()}%")
        self.drag_slider.valueChanged.connect(
            lambda: self.drag_slider.setToolTip(f"{self.drag_slider.value()}%")
        )

        self.drag_slider.setStyleSheet("""
            QSlider::handle:horizontal {
                background-color: #2ecc71; border-radius: 6px; width: 16px; margin: -6px 0;
            }
            QSlider::groove:horizontal {
                background-color: rgba(255, 255, 255, 0.16); height: 4px; border-radius: 2px;
            }
        """)

        icon_slider_layout.addWidget(self.drag_slider)
        main_layout.addLayout(icon_slider_layout)

        notches_layout = QHBoxLayout()
        notches_layout.setContentsMargins(0, 0, 0, 0)
        notches_layout.setSpacing(12)

        button_labels = [(25, "25%"), (50, "50%"), (75, "75%"), (100, "100%")]
        for value, label in button_labels:
            button = QPushButton(label, self.control_widget)
            button.setFixedSize(40, 25)
            button.setStyleSheet("""
                QPushButton {
                    color: white; background-color: rgba(63, 63, 63, 0.4); border: none;
                    border-radius: 5px; font-size: 10px; padding: 2px;
                }
                QPushButton:hover { background-color: rgba(85, 85, 85, 0.6); }
                QPushButton:pressed { background-color: rgba(46, 204, 113, 0.3); color: black; }
            """)
            button.clicked.connect(lambda _, v=value: self._set_slider_value(v))
            notches_layout.addWidget(button)

        main_layout.addLayout(notches_layout)
        self.control_widget.setFixedSize(200, 90)

    def _set_slider_value(self, value):
        """Sets the value of the drag slider and updates the drag factor."""
        self.drag_slider.setValue(value)
        self._update_drag()

    def _update_overlay_positions(self):
        """
        Calculates and sets the positions of all floating overlay widgets
        (search, controls, minimap, etc.) relative to the viewport.
        """
        padding = 10
        viewport_width = self.viewport().width()
        
        # Position right-aligned overlays, stacking them vertically.
        current_y_right = padding
        search_overlay = self.findChild(SearchOverlay)
        if search_overlay and search_overlay.isVisible():
            search_overlay.move(viewport_width - search_overlay.width() - padding, current_y_right)
            current_y_right += search_overlay.height() + padding

        if self.control_widget.isVisible():
            self.control_widget.move(viewport_width - self.control_widget.width() - padding, current_y_right)
            current_y_right += self.control_widget.height() + padding

        if self.grid_control.isVisible():
            self.grid_control.move(viewport_width - self.grid_control.width() - padding, current_y_right)
            current_y_right += self.grid_control.height() + padding
        
        if self.font_control.isVisible():
            self.font_control.move(viewport_width - self.font_control.width() - padding, current_y_right)

        if self.minimap_widget.isVisible():
            self.minimap_widget.setFixedHeight(int(self.viewport().height() * 0.7))
            self.minimap_widget.move(
                self.viewport().width() - self.minimap_widget.width() - 5,
                (self.viewport().height() - self.minimap_widget.height()) // 2
            )

        # Position left-aligned overlays.
        current_y_left = padding
        pin_overlay = self.findChild(PinOverlay)
        if pin_overlay and pin_overlay.isVisible():
            pin_overlay.move(padding, current_y_left)

    def _update_drag(self):
        """Updates the panning speed factor based on the drag slider's value."""
        self._drag_factor = self.drag_slider.value() / 100.0

    def toggle_overlays_visibility(self, visible):
        """
        Shows or hides the control overlay widgets.

        Args:
            visible (bool): True to show the overlays, False to hide them.
        """
        self._controls_overlay_visible = visible
        self.control_widget.setVisible(visible)
        self.grid_control.setVisible(visible)
        self.font_control.setVisible(visible)
        self.minimap_widget.setVisible(not visible)
        self.minimap_widget.setEnabled(not visible)
        self.minimap_widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, visible)
        self._update_overlay_positions()

    def updateScrollbars(self):
        """
        Synchronizes the state of the custom scrollbars with the view's internal
        Qt scrollbars.
        """
        v_bar = self.verticalScrollBar()
        self.v_scrollbar.setRange(v_bar.minimum(), v_bar.maximum())
        self.v_scrollbar.page_step = v_bar.pageStep()
        self.v_scrollbar.setValue(v_bar.value())
        
        h_bar = self.horizontalScrollBar()
        self.h_scrollbar.setRange(h_bar.minimum(), h_bar.maximum())
        self.h_scrollbar.page_step = h_bar.pageStep()
        self.h_scrollbar.setValue(h_bar.value())

    def mousePressEvent(self, event):
        """
        Handles mouse press events to initiate panning or zoom-to-selection.

        Args:
            event (QMouseEvent): The mouse event.
        """
        self._clear_nav_highlight()

        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            # Start "zoom to selection" mode.
            self._expanding = True
            self._expand_start = event.position().toPoint()
            self._expand_rect = None
            self._original_transform = self.transform()
            event.accept()
        elif event.button() == Qt.MouseButton.MiddleButton:
            # Start panning mode.
            self._panning = True
            self._last_mouse_pos = event.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            # Handle rubber band drag selection.
            if event.button() == Qt.MouseButton.LeftButton and self.itemAt(event.pos()) is None:
                if self.scene():
                    self.scene().is_rubber_band_dragging = True
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """
        Handles mouse move events for panning or updating the zoom-to-selection rectangle.

        Args:
            event (QMouseEvent): The mouse event.
        """
        # Failsafe to stop panning if the middle mouse button is released unexpectedly.
        if self._panning and not (QGuiApplication.mouseButtons() & Qt.MouseButton.MiddleButton):
            self._panning = False
            self._last_mouse_pos = None
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)

        if self._expanding and self._expand_start:
            # Update the zoom rectangle.
            self._current_mouse_pos = event.position().toPoint()
            self._expand_rect = QRectF(
                self.mapToScene(self._expand_start),
                self.mapToScene(self._current_mouse_pos)
            ).normalized()
            
            self.viewport().update() # Trigger a repaint to draw the rectangle.
            event.accept()
        elif self._panning and self._last_mouse_pos is not None:
            # Pan the view based on mouse movement.
            delta = event.position().toPoint() - self._last_mouse_pos
            delta *= self._drag_factor
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self.updateScrollbars()
            self._last_mouse_pos = event.position().toPoint()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """
        Handles mouse release events to finalize panning or zoom-to-selection.

        Args:
            event (QMouseEvent): The mouse event.
        """
        if self._expanding and self._expand_rect:
            # Fit the view to the drawn rectangle.
            self.fitInView(self._expand_rect, Qt.AspectRatioMode.KeepAspectRatio)
            self._zoom_factor = self.transform().m11()
            self._expanding = False
            self._expand_rect = None
            event.accept()
        elif self._panning:
            # Stop panning.
            self._panning = False
            self._last_mouse_pos = None
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
        else:
            is_dragging = False
            if self.scene():
                is_dragging = self.scene().is_rubber_band_dragging
            
            super().mouseReleaseEvent(event)
            
            if is_dragging and self.scene():
                self.scene().is_rubber_band_dragging = False

    def _handle_key_pan(self):
        """
        Timer-driven method to handle smooth panning with WASD keys.
        """
        dx, dy = 0, 0
        
        if Qt.Key.Key_W in self.keys_pressed: dy -= self.pan_speed
        if Qt.Key.Key_S in self.keys_pressed: dy += self.pan_speed
        if Qt.Key.Key_A in self.keys_pressed: dx -= self.pan_speed
        if Qt.Key.Key_D in self.keys_pressed: dx += self.pan_speed
            
        if dx != 0 or dy != 0:
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + dx)
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() + dy)
            self.updateScrollbars()

    def keyPressEvent(self, event):
        """
        Handles key press events for navigation (WASD) and zooming (Q/E).

        Args:
            event (QKeyEvent): The key event.
        """
        # Ignore keyboard navigation if a text input field in the scene has focus.
        focused_item = self.scene().focusItem() if self.scene() else None
        if focused_item and (
            getattr(focused_item, 'editing', False) or
            isinstance(focused_item, QGraphicsProxyWidget)
        ):
            super().keyPressEvent(event)
            return

        # A global check for any text input field in the app having focus.
        if isinstance(QApplication.focusWidget(), (QLineEdit, QTextEdit)):
            super().keyPressEvent(event)
            return

        key = event.key()
        if key in (Qt.Key.Key_W, Qt.Key.Key_A, Qt.Key.Key_S, Qt.Key.Key_D):
            self.keys_pressed.add(key)
            if not self.pan_timer.isActive():
                self.pan_timer.start()
            event.accept()
            return

        if key == Qt.Key.Key_E: # Zoom In
            factor = 1.05
            self._zoom_factor *= factor
            if self._zoom_factor <= 4.0:
                self.scale(factor, factor)
            else:
                self._zoom_factor /= factor
            event.accept()
            return
            
        if key == Qt.Key.Key_Q: # Zoom Out
            factor = 0.95
            self._zoom_factor *= factor
            if self._zoom_factor >= 0.1:
                self.scale(factor, factor)
            else:
                self._zoom_factor /= factor
            event.accept()
            return
        
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        """
        Handles key release events to stop panning or cancel zoom-to-selection.

        Args:
            event (QKeyEvent): The key event.
        """
        if not event.isAutoRepeat() and event.key() in (Qt.Key.Key_W, Qt.Key.Key_A, Qt.Key.Key_S, Qt.Key.Key_D):
            self.keys_pressed.discard(event.key())
            if not self.keys_pressed:
                self.pan_timer.stop()
            event.accept()
            return

        if event.key() == Qt.Key.Key_Shift:
            # Cancel zoom-to-selection by restoring the original transform.
            if self._expanding and self._original_transform:
                self.setTransform(self._original_transform)
                self._zoom_factor = self._original_transform.m11()
                self._expanding = False
                self._expand_rect = None
                self.viewport().update()
        elif event.key() == Qt.Key.Key_Escape:
            if self._original_transform:
                self.setTransform(self._original_transform)
                self._zoom_factor = self._original_transform.m11()
                self._expanding = False
                self._expand_rect = None
                self.viewport().update()
        super().keyReleaseEvent(event)

    def wheelEvent(self, event):
        """
        Handles mouse wheel events for zooming or scrolling the view/items.

        Args:
            event (QWheelEvent): The wheel event.
        """
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Zoom the view if Ctrl is held.
            zoom_in = event.angleDelta().y() > 0
            factor = 1.1 if zoom_in else 0.9
            new_zoom_factor = self._zoom_factor * factor
                
            if 0.1 <= new_zoom_factor <= 4.0:
                self.scale(factor, factor)
                self._zoom_factor = new_zoom_factor
            return

        # Check if the item under the cursor is scrollable (e.g., a ChatNode with overflow).
        item = self.itemAt(event.position().toPoint())
        is_item_scrollable = (
            isinstance(item, (ChatNode, DocumentNode)) and
            not item.is_collapsed and
            item.scrollbar.isVisible()
        )

        if is_item_scrollable:
            # Pass the event to the item for internal scrolling.
            super().wheelEvent(event)
        else:
            # Scroll the main view if no scrollable item is under the cursor.
            v_bar = self.verticalScrollBar()
            h_bar = self.horizontalScrollBar()
            
            # Scroll horizontally if Shift is held.
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                delta = event.angleDelta().y() if event.angleDelta().y() != 0 else event.angleDelta().x()
                h_bar.setValue(h_bar.value() - delta)
            else:
                delta = event.angleDelta().y()
                v_bar.setValue(v_bar.value() - delta)
                
            self.updateScrollbars()

    def paintEvent(self, event):
        """
        Overrides the paint event to draw the "zoom to selection" rectangle.

        Args:
            event (QPaintEvent): The paint event.
        """
        super().paintEvent(event)
        
        if self._expanding and self._expand_start and self._current_mouse_pos:
            painter = QPainter(self.viewport())
            painter.setPen(QPen(QColor("#2ecc71"), 2, Qt.PenStyle.DashLine))
            painter.setBrush(QBrush(QColor(46, 204, 113, 30)))
            
            rect = QRectF(self._expand_start, self._current_mouse_pos).normalized()
            painter.drawRect(rect)

    def resizeEvent(self, event):
        """
        Handles resize events to reposition overlays and scrollbars.

        Args:
            event (QResizeEvent): The resize event.
        """
        super().resizeEvent(event)
    
        self._update_overlay_positions()
    
        scrollbar_width = self.v_scrollbar.width()
        scrollbar_height = self.h_scrollbar.height()
    
        self.v_scrollbar.setGeometry(self.width() - scrollbar_width, 0, scrollbar_width, self.height() - scrollbar_height)
        self.h_scrollbar.setGeometry(0, self.height() - scrollbar_height, self.width() - scrollbar_width, scrollbar_height)
    
        self.updateScrollbars()

    def scrollContentsBy(self, dx, dy):
        """
        Overrides the scroll contents event to ensure custom scrollbars are updated.

        Args:
            dx (int): The change in horizontal scroll position.
            dy (int): The change in vertical scroll position.
        """
        super().scrollContentsBy(dx, dy)
        self.updateScrollbars()
            
    def reset_zoom(self):
        """Resets the view's transform to its default state (100% zoom)."""
        self.resetTransform()
        self._zoom_factor = 1.0
        
    def fit_all(self):
        """Zooms and pans the view to fit all items in the scene."""
        if self.scene() and not self.scene().nodes and not self.scene().code_nodes and not self.scene().image_nodes:
            return
        self.fitInView(self.scene().itemsBoundingRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom_factor = self.transform().m11()

    def drawBackground(self, painter, rect):
        """
        Draws the custom background, including the grid.

        Args:
            painter (QPainter): The painter to use for drawing.
            rect (QRectF): The portion of the scene to be drawn.
        """
        super().drawBackground(painter, rect)
    
        painter.fillRect(rect, QColor("#252526"))
    
        grid_size = self.grid_control.grid_size
        opacity = self.grid_control.grid_opacity
        style = self.grid_control.grid_style
        base_color = self.grid_control.grid_color
    
        if opacity <= 0:
            return
            
        zoom = self.transform().m11()
        
        # Level of Detail thresholds: hide finer grid lines when zoomed out.
        LOD_MINOR = 0.5
        LOD_MAJOR = 0.15
        
        left, top, right, bottom = int(rect.left()), int(rect.top()), int(rect.right()), int(rect.bottom())
        
        # Draw major grid lines (every 10 grid units).
        if zoom > LOD_MAJOR:
            major_grid_size = grid_size * 10
            major_color = QColor(base_color)
            major_color.setAlphaF(min(1.0, opacity * 1.5)) 
            painter.setPen(QPen(major_color, 1.0 / zoom))
            
            major_left = left - (left % major_grid_size)
            major_top = top - (top % major_grid_size)

            for x in range(major_left, right, major_grid_size):
                painter.drawLine(x, top, x, bottom)
            for y in range(major_top, bottom, major_grid_size):
                painter.drawLine(left, y, right, y)

        # Draw minor grid lines/dots.
        if zoom > LOD_MINOR:
            minor_color = QColor(base_color)
            minor_color.setAlphaF(opacity)
            
            pen = QPen(minor_color, 1.0 / zoom)
            minor_left = left - (left % grid_size)
            minor_top = top - (top % grid_size)
            
            if style == 'Lines':
                pen.setStyle(Qt.PenStyle.SolidLine)
                painter.setPen(pen)
                for x in range(minor_left, right, grid_size):
                    painter.drawLine(x, top, x, bottom)
                for y in range(minor_top, bottom, grid_size):
                    painter.drawLine(left, y, right, y)
            
            elif style == 'Dots':
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(minor_color)
                dot_size = 1.0 / zoom
                for x in range(minor_left, right, grid_size):
                    for y in range(minor_top, bottom, grid_size):
                        painter.drawRect(QRectF(x - dot_size / 2, y - dot_size / 2, dot_size, dot_size))

            elif style == 'Cross':
                pen.setStyle(Qt.PenStyle.SolidLine)
                painter.setPen(pen)
                cross_size = 4.0 / zoom
                for x in range(minor_left, right, grid_size):
                    for y in range(minor_top, bottom, grid_size):
                        painter.drawLine(QPointF(x - cross_size / 2, y), QPointF(x + cross_size / 2, y))
                        painter.drawLine(QPointF(x, y - cross_size / 2), QPointF(x, y + cross_size / 2))
