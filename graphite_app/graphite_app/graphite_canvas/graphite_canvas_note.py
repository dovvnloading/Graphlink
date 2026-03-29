"""Canvas note item supporting markdown display and inline text editing."""

import markdown
import qtawesome as qta

from PySide6.QtWidgets import QDialog, QGraphicsItem, QApplication, QMessageBox
from PySide6.QtCore import Qt, QRectF, QPointF, QTimer
from PySide6.QtGui import (
    QFontMetrics, QPainter, QColor, QBrush, QPen, QFont, QPainterPath,
    QTextLayout, QTextOption, QLinearGradient, QConicalGradient, QTextDocument
)

from .graphite_canvas_dialogs import ColorPickerDialog
from graphite_config import get_current_palette
from graphite_widgets import ScrollBar


class Note(QGraphicsItem):
    """
    A "sticky note" item for adding annotations to the canvas. It supports
    rich text (Markdown), scrolling, and in-place text editing. It can also
    serve special roles like being a System Prompt or a Group Summary.
    """
    PADDING = 20
    HEADER_HEIGHT = 40
    DEFAULT_WIDTH = 200
    DEFAULT_HEIGHT = 150
    MAX_HEIGHT = 500
    CONTROL_GUTTER = 25
    SCROLLBAR_PADDING = 5
    
    def __init__(self, pos, parent=None):
        """
        Initializes the Note.

        Args:
            pos (QPointF): The initial position of the note on the scene.
            parent (QGraphicsItem, optional): The parent item. Defaults to None.
        """
        super().__init__(parent)
        self.setPos(pos)
        self._content = ""
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsScenePositionChanges)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self.is_system_prompt = False
        self.is_summary_note = False
        
        # Geometry and appearance
        self.width = self.DEFAULT_WIDTH
        self.height = self.DEFAULT_HEIGHT
        self.color = "#2d2d2d"
        self.header_color = None
        
        # State for in-place text editing
        self.editing = False
        self.edit_text = ""
        self.cursor_pos = 0
        self.cursor_visible = True
        
        # State for text selection
        self.selection_start = 0
        self.selection_end = 0
        self.selecting = False
        self.mouse_drag_start_pos = None
        
        self.hovered = False
        self.color_button_hovered = False
        
        self.cursor_timer = QTimer()
        self.cursor_timer.timeout.connect(self.toggle_cursor)
        self.cursor_timer.setInterval(500)
        
        self.color_button_rect = QRectF(0, 0, 24, 24)

        # QTextDocument for rich text rendering
        self.document = QTextDocument()
        self.content_height = 0
        self.scroll_value = 0
        self.scrollbar = ScrollBar(self)
        self.scrollbar.width = 8
        self.scrollbar.valueChanged.connect(self.update_scroll_position)
        self.content = "Add note..." # This triggers the setter

    @property
    def content(self):
        """Gets the note's content."""
        return self._content

    @content.setter
    def content(self, new_content):
        """
        Sets the note's content. If not in editing mode, it immediately updates
        the QTextDocument for rendering.
        """
        if self._content != new_content:
            self._content = new_content
            if not self.editing:
                self._setup_document()
            self.update()

    def _setup_document(self):
        """
        Configures the QTextDocument for rendering, applying styles from the
        scene and converting the Markdown content to HTML.
        """
        font_family, font_size, color = "Segoe UI", 10, "#dddddd"
        
        if self.scene():
            font_family = self.scene().font_family
            font_size = self.scene().font_size
            color = self.scene().font_color.name()

        stylesheet = f"""
            p, ul, ol, li, blockquote {{ color: {color}; font-family: '{font_family}'; font-size: {font_size}pt; }}
            pre {{ background-color: #1e1e1e; padding: 8px; border-radius: 4px; white-space: pre-wrap; font-family: Consolas, monospace; }}
        """
        self.document.setDefaultStyleSheet(stylesheet)
        
        html = markdown.markdown(self._content, extensions=['fenced_code', 'tables'])
        self.document.setHtml(html)
        
        self._recalculate_geometry()

    def _recalculate_geometry(self):
        """
        Calculates the note's height based on its content, adding a scrollbar
        if the content exceeds the maximum height.
        """
        self.prepareGeometryChange()

        # Pass 1: Calculate ideal size assuming no scrollbar.
        available_width = self.width - (self.PADDING * 2)
        self.document.setTextWidth(available_width)
        self.content_height = self.document.size().height()
        total_required_height = self.content_height + self.HEADER_HEIGHT + 20

        # Pass 2: Decide if a scrollbar is needed and adjust dimensions.
        is_scrollable = total_required_height > self.MAX_HEIGHT
        self.scrollbar.setVisible(is_scrollable)

        if is_scrollable:
            self.height = self.MAX_HEIGHT
            # Recalculate text width to make space for the scrollbar.
            available_width -= (self.scrollbar.width + self.SCROLLBAR_PADDING)
            self.document.setTextWidth(available_width)
            self.content_height = self.document.size().height()

            # Configure scrollbar geometry and range.
            self.scrollbar.height = self.height - self.HEADER_HEIGHT - (self.SCROLLBAR_PADDING * 2)
            self.scrollbar.setPos(self.width - self.scrollbar.width - self.SCROLLBAR_PADDING, self.HEADER_HEIGHT + self.SCROLLBAR_PADDING)
            visible_content_height = self.height - self.HEADER_HEIGHT - 20
            visible_ratio = visible_content_height / self.content_height if self.content_height > 0 else 1
            self.scrollbar.set_range(visible_ratio)
        else:
            self.height = total_required_height
        
        self.update()

    def boundingRect(self):
        """Returns the bounding rectangle of the item."""
        return QRectF(0, 0, self.width, self.height)
        
    def toggle_cursor(self):
        """Toggles the visibility of the text editing cursor."""
        self.cursor_visible = not self.cursor_visible
        self.update()

    def paint(self, painter, option, widget=None):
        """Handles the custom painting of the note."""
        palette = get_current_palette()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Draw a subtle drop shadow.
        shadow_path = QPainterPath()
        shadow_path.addRoundedRect(3, 3, self.width, self.height, 10, 10)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 30))
        painter.drawPath(shadow_path)
        
        # Draw the main body.
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width, self.height, 10, 10)
        
        pen = QPen(QColor("#555555"))
        if self.isSelected(): pen = QPen(palette.SELECTION, 2)
        elif self.hovered: pen = QPen(QColor("#ffffff"), 2)

        # Special outline for system prompt notes.
        if self.is_system_prompt:
            pen = QPen(QColor(palette.FRAME_COLORS["Purple Header"]["color"]), 1.5, Qt.PenStyle.DashLine)
            if self.isSelected() or self.hovered: pen.setWidth(2.5)

        painter.setPen(pen)
            
        gradient = QLinearGradient(QPointF(0, 0), QPointF(0, self.height))
        gradient.setColorAt(0, QColor("#4a4a4a"))
        gradient.setColorAt(1, QColor("#2d2d2d"))
        painter.setBrush(QBrush(gradient))
        painter.drawPath(path)
        
        # Draw the header.
        header_rect = QRectF(0, 0, self.width, self.HEADER_HEIGHT)
        header_path = QPainterPath()
        header_path.addRoundedRect(header_rect, 10, 10)
        header_gradient = QLinearGradient(header_rect.topLeft(), header_rect.bottomLeft())
        
        header_base_color = None
        if self.is_system_prompt: header_base_color = QColor(palette.FRAME_COLORS["Purple Header"]["color"])
        elif self.header_color: header_base_color = QColor(self.header_color)
        else: header_base_color = QColor(self.color).lighter(120)

        header_gradient.setColorAt(0, header_base_color)
        header_gradient.setColorAt(1, header_base_color.darker(110))
            
        painter.setBrush(QBrush(header_gradient))
        painter.drawPath(header_path)

        # Draw header icons for special note types.
        icon_rect = QRectF(10, (self.HEADER_HEIGHT - 16) / 2, 16, 16)
        if self.is_system_prompt:
            qta.icon('fa5s.cog', color='#ffffff').paint(painter, icon_rect.toRect())
        elif self.is_summary_note:
            qta.icon('fa5s.object-group', color='#ffffff').paint(painter, icon_rect.toRect())
        
        # Draw the color picker button.
        self.color_button_rect = QRectF(self.width - 34, 8, 24, 24)
        painter.setPen(QPen(QColor("#ffffff") if self.color_button_hovered else QColor("#555555")))
        painter.setBrush(QBrush(header_base_color))
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
            
        # --- Content Rendering ---
        painter.setPen(QPen(QColor("#ffffff")))
        font = QFont("Segoe UI", 10)
        painter.setFont(font)
        
        content_rect = QRectF(self.PADDING, self.HEADER_HEIGHT + 10, self.width - (self.PADDING * 2), self.height - self.HEADER_HEIGHT - 20)
        
        if self.editing:
            # --- In-place Text Editing Rendering ---
            # This is a manual implementation of a text editor's features, including
            # word wrap, cursor drawing, and selection highlighting.
            text = self.edit_text
            metrics = painter.fontMetrics()
            
            # Use QTextLayout to handle complex text wrapping.
            layout = QTextLayout(text, font)
            text_option = QTextOption(); text_option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
            layout.setTextOption(text_option)
            
            layout.beginLayout()
            height = 0; cursor_x = 0; cursor_y = 0; cursor_found = False
            text_lines = []
            
            # Break the text into lines based on the available width.
            while True:
                line = layout.createLine()
                if not line.isValid(): break
                line.setLineWidth(content_rect.width())
                line_height = metrics.height()
                text_lines.append({'line': line, 'y': height, 'text': text[line.textStart():line.textStart() + line.textLength()]})
                
                # Find the line containing the cursor to calculate its position.
                if not cursor_found and line.textStart() <= self.cursor_pos <= (line.textStart() + line.textLength()):
                    cursor_text = text[line.textStart():self.cursor_pos]
                    cursor_x = metrics.horizontalAdvance(cursor_text)
                    cursor_y = height
                    cursor_found = True
                height += line_height
            layout.endLayout()
            
            # Draw selection highlighting.
            if self.selection_start != self.selection_end:
                sel_start, sel_end = min(self.selection_start, self.selection_end), max(self.selection_start, self.selection_end)
                for line_info in text_lines:
                    line = line_info['line']
                    line_start, line_end = line.textStart(), line.textStart() + line.textLength()
                    if sel_start < line_end and sel_end > line_start:
                        start_x, width = 0, 0
                        if sel_start > line_start:
                            start_x = metrics.horizontalAdvance(text[line_start:sel_start])
                        sel_text = text[max(line_start, sel_start):min(line_end, sel_end)]
                        width = metrics.horizontalAdvance(sel_text)
                        sel_rect = QRectF(content_rect.left() + start_x, content_rect.top() + line_info['y'], width, metrics.height())
                        painter.fillRect(sel_rect, palette.SELECTION)
            
            # Draw the text lines.
            for line_info in text_lines:
                painter.drawText(QPointF(content_rect.left(), content_rect.top() + line_info['y'] + metrics.ascent()), line_info['text'])
            
            # Draw the cursor if visible and no text is selected.
            if self.cursor_visible and (not self.selecting or self.selection_start == self.selection_end):
                if cursor_found:
                    cursor_height = metrics.height()
                    painter.drawLine(int(content_rect.left() + cursor_x), int(content_rect.top() + cursor_y), int(content_rect.left() + cursor_x), int(content_rect.top() + cursor_y + cursor_height))
        else:
            # --- Display Mode Rendering ---
            # Render the pre-formatted QTextDocument.
            painter.save()
            painter.setClipRect(content_rect)
            
            # Apply scroll offset.
            visible_height = self.height - self.HEADER_HEIGHT - 20
            scrollable_distance = self.content_height - visible_height
            scroll_offset = scrollable_distance * self.scroll_value if scrollable_distance > 0 else 0
            painter.translate(self.PADDING, self.HEADER_HEIGHT + 10 - scroll_offset)

            # Draw a subtle background for the content area.
            container_path = QPainterPath()
            container_width, container_height = self.document.textWidth(), self.content_height
            container_path.addRoundedRect(0, 0, container_width, container_height, 5, 5)
            painter.setBrush(QColor(0, 0, 0, 25))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(container_path)

            self.document.drawContents(painter)
            painter.restore()

    def get_char_pos_at_x(self, x, y):
        """
        Calculates the character index in the raw text string corresponding to
        a given x, y coordinate within the note's content area. This is crucial
        for placing the cursor correctly when the user clicks.
        """
        metrics = QFontMetrics(QFont("Segoe UI", 10))
        content_rect = QRectF(self.PADDING, self.HEADER_HEIGHT + 10, self.width - (self.PADDING * 2), self.height - self.HEADER_HEIGHT - (self.PADDING * 2))
    
        # Use QTextLayout to determine line breaks and character positions.
        layout = QTextLayout(self.edit_text, QFont("Segoe UI", 10))
        layout.setTextOption(QTextOption(alignment=Qt.AlignmentFlag.AlignLeft, wrapMode=QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere))
    
        layout.beginLayout()
        height = 0
        clicked_line = None
        relative_x, relative_y = x - self.PADDING, y - (self.HEADER_HEIGHT + 10)
    
        # Find which line was clicked.
        while True:
            line = layout.createLine()
            if not line.isValid(): break
            line.setLineWidth(content_rect.width())
            line_height = metrics.height()
            if height <= relative_y < (height + line_height):
                clicked_line = line
                break
            height += line_height
        layout.endLayout()
    
        # Find the character index within the clicked line.
        if clicked_line:
            line_text = self.edit_text[clicked_line.textStart():clicked_line.textStart() + clicked_line.textLength()]
            text_width = 0
            for i, char in enumerate(line_text):
                char_width = metrics.horizontalAdvance(char)
                if text_width + (char_width / 2) > relative_x:
                    return clicked_line.textStart() + i
                text_width += char_width
            return clicked_line.textStart() + len(line_text)
    
        return len(self.edit_text)

    # --- Mouse and Key Event Handlers for Text Editing ---
    def mousePressEvent(self, event):
        """Handles mouse press for editing, selection, and button clicks."""
        if self.editing and event.pos().y() > self.HEADER_HEIGHT:
            self.selecting = True
            self.mouse_drag_start_pos = event.pos()
            char_pos = self.get_char_pos_at_x(event.pos().x(), event.pos().y())
            self.cursor_pos = self.selection_start = self.selection_end = char_pos
            self.update()
            event.accept()
        elif self.color_button_rect.contains(event.pos()):
            self.show_color_picker()
            event.accept()
        
        if event.button() == Qt.MouseButton.LeftButton and self.scene():
            self.scene().is_dragging_item = True
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        """Handles mouse release to end selection."""
        if self.selecting:
            self.selecting = False
            self.mouse_drag_start_pos = None
            event.accept()
        
        if self.scene():
            self.scene().is_dragging_item = False
            self.scene()._clear_smart_guides()
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        """Handles mouse drag to update text selection."""
        if self.selecting and self.editing:
            char_pos = self.get_char_pos_at_x(event.pos().x(), event.pos().y())
            self.selection_end = self.cursor_pos = char_pos
            self.update()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event):
        """Handles double-click to start editing or select a word."""
        if event.pos().y() > self.HEADER_HEIGHT:
            if not self.editing:
                self.editing = True
                self.edit_text = self.content
            
            char_pos = self.get_char_pos_at_x(event.pos().x(), event.pos().y())
            text = self.edit_text
            start = end = char_pos
            
            # Expand selection to the boundaries of the double-clicked word.
            while start > 0 and text[start-1].isalnum(): start -= 1
            while end < len(text) and text[end].isalnum(): end += 1
            
            self.selection_start, self.selection_end, self.cursor_pos = start, end, end
                
            self.cursor_timer.start()
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable)
            self.setFocus()
            self.update()
        else:
            super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        """Handles all keyboard input during text editing."""
        if not self.editing: return super().keyPressEvent(event)
            
        # Standard text editing shortcuts (copy, paste, cut, select all).
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.key() == Qt.Key.Key_C: self.copy_selection(); return
            elif event.key() == Qt.Key.Key_V: self.paste_text(); return
            elif event.key() == Qt.Key.Key_X: self.cut_selection(); return
            elif event.key() == Qt.Key.Key_A: self.select_all(); return
        
        if event.key() == Qt.Key.Key_Return and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.finishEditing()
        elif event.key() == Qt.Key.Key_Escape:
            self.editing = False; self.cursor_timer.stop(); self.update()
        elif event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            if self.selection_start != self.selection_end: self.delete_selection()
            elif event.key() == Qt.Key.Key_Backspace and self.cursor_pos > 0:
                self.edit_text = self.edit_text[:self.cursor_pos-1] + self.edit_text[self.cursor_pos:]
                self.cursor_pos -= 1
            elif event.key() == Qt.Key.Key_Delete and self.cursor_pos < len(self.edit_text):
                self.edit_text = self.edit_text[:self.cursor_pos] + self.edit_text[self.cursor_pos+1:]
            self.selection_start = self.selection_end = self.cursor_pos
            self.update()
        elif event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            # Handle cursor movement with and without Shift for selection.
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                if self.selection_start == self.selection_end: self.selection_start = self.cursor_pos
                self.cursor_pos = max(0, self.cursor_pos - 1) if event.key() == Qt.Key.Key_Left else min(len(self.edit_text), self.cursor_pos + 1)
                self.selection_end = self.cursor_pos
            else:
                if self.selection_start != self.selection_end:
                    self.cursor_pos = min(self.selection_start, self.selection_end) if event.key() == Qt.Key.Key_Left else max(self.selection_start, self.selection_end)
                else:
                    self.cursor_pos = max(0, self.cursor_pos - 1) if event.key() == Qt.Key.Key_Left else min(len(self.edit_text), self.cursor_pos + 1)
                self.selection_start = self.selection_end = self.cursor_pos
            self.update()
        elif event.key() == Qt.Key.Key_Home:
            # ... (Home/End key logic) ...
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                if self.selection_start == self.selection_end: self.selection_start = self.cursor_pos
                self.cursor_pos = self.selection_end = 0
            else:
                self.cursor_pos = self.selection_start = self.selection_end = 0
            self.update()
        elif event.key() == Qt.Key.Key_End:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                if self.selection_start == self.selection_end: self.selection_start = self.cursor_pos
                self.cursor_pos = self.selection_end = len(self.edit_text)
            else:
                self.cursor_pos = self.selection_start = self.selection_end = len(self.edit_text)
            self.update()
        elif event.key() == Qt.Key.Key_Return:
            # Insert newline.
            if self.selection_start != self.selection_end: self.delete_selection()
            self.edit_text = self.edit_text[:self.cursor_pos] + '\n' + self.edit_text[self.cursor_pos:]
            self.cursor_pos += 1; self.selection_start = self.selection_end = self.cursor_pos; self.update()
        elif len(event.text()) and event.text().isprintable():
            # Insert typed character.
            if self.selection_start != self.selection_end: self.delete_selection()
            self.edit_text = self.edit_text[:self.cursor_pos] + event.text() + self.edit_text[self.cursor_pos:]
            self.cursor_pos += 1; self.selection_start = self.selection_end = self.cursor_pos; self.update()

    def wheelEvent(self, event):
        """Handles mouse wheel scrolling for the note's content."""
        if self.editing or not self.scrollbar.isVisible():
            event.ignore(); return
            
        delta = event.angleDelta().y() / 120
        visible_height = self.height - self.HEADER_HEIGHT - 20
        scroll_range = self.content_height - visible_height
        
        if scroll_range <= 0: return

        scroll_delta = -(delta * 50) / scroll_range # 50 pixels per wheel tick
        
        new_value = max(0, min(1, self.scroll_value + scroll_delta))
        if new_value != self.scroll_value:
            self.scroll_value = new_value; self.scrollbar.set_value(new_value); self.update()
        event.accept()

    def update_scroll_position(self, value):
        """Slot connected to the scrollbar's valueChanged signal."""
        if self.scroll_value != value:
            self.scroll_value = value; self.update()

    # --- Text manipulation methods ---
    def copy_selection(self):
        if self.selection_start != self.selection_end:
            start, end = min(self.selection_start, self.selection_end), max(self.selection_start, self.selection_end)
            QApplication.clipboard().setText(self.edit_text[start:end])

    def cut_selection(self):
        if self.selection_start != self.selection_end:
            self.copy_selection(); self.delete_selection()

    def paste_text(self):
        text = QApplication.clipboard().text()
        if text:
            if self.selection_start != self.selection_end: self.delete_selection()
            self.edit_text = self.edit_text[:self.cursor_pos] + text + self.edit_text[self.cursor_pos:]
            self.cursor_pos += len(text); self.selection_start = self.selection_end = self.cursor_pos; self.update()

    def delete_selection(self):
        if self.selection_start != self.selection_end:
            start, end = min(self.selection_start, self.selection_end), max(self.selection_start, self.selection_end)
            self.edit_text = self.edit_text[:start] + self.edit_text[end:]
            self.cursor_pos = self.selection_start = self.selection_end = start
            self.update()

    def select_all(self):
        self.selection_start, self.selection_end = 0, len(self.edit_text)
        self.cursor_pos = self.selection_end; self.update()
        
    def hoverMoveEvent(self, event):
        """Updates hover state of the color button."""
        old_color_hover = self.color_button_hovered
        self.color_button_hovered = self.color_button_rect.contains(event.pos())
        if old_color_hover != self.color_button_hovered: self.update()
        self.setCursor(Qt.CursorShape.ArrowCursor)
            
    def hoverEnterEvent(self, event):
        self.hovered = True; self.update(); super().hoverEnterEvent(event)
        
    def hoverLeaveEvent(self, event):
        self.hovered = False; self.color_button_hovered = False
        self.setCursor(Qt.CursorShape.ArrowCursor); self.update(); super().hoverLeaveEvent(event)
        
    def show_color_picker(self):
        """Opens the color picker dialog."""
        dialog = ColorPickerDialog(self.scene().views()[0])
        note_pos = self.mapToScene(self.color_button_rect.topRight())
        view_pos = self.scene().views()[0].mapFromScene(note_pos)
        global_pos = self.scene().views()[0].mapToGlobal(view_pos)
        dialog.move(global_pos.x() + 10, global_pos.y())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            color, color_type = dialog.get_selected_color()
            if color_type == "full": self.color, self.header_color = color, None
            else: self.header_color = color
            self.update()
            
    def finishEditing(self):
        """Finalizes text editing, saving the content."""
        if self.editing:
            self.editing = False; self.content = self.edit_text
            self.cursor_timer.stop(); self.clearFocus()
            
    def focusOutEvent(self, event):
        """Ends editing when the note loses focus."""
        super().focusOutEvent(event); self.finishEditing()
        
    def itemChange(self, change, value):
        """Handles item changes."""
        if change == QGraphicsItem.ItemPositionChange and self.scene() and self.scene().is_dragging_item:
            parent = self.parentItem()
            from .graphite_canvas_container import Container
            if parent and isinstance(parent, Container): parent.updateGeometry()
            return self.scene().snap_position(self, value)

        if change == QGraphicsItem.ItemPositionHasChanged and self.scene():
            self.scene().nodeMoved(self)
        
        return super().itemChange(change, value)
