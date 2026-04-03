"""Text input widgets and attachment pills for chat composition."""

import re

import qtawesome as qta
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from graphite_config import get_current_palette

try:
    from spellchecker import SpellChecker
    SPELLCHECK_AVAILABLE = True
except ImportError:
    SPELLCHECK_AVAILABLE = False

class SpellCheckLineEdit(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        if not SPELLCHECK_AVAILABLE:
            return

        self.spell = SpellChecker()
        self.misspelled_words = set()
        self.error_spans = []

        self.textChanged.connect(self._check_spelling)

    def _check_spelling(self, text):
        self.misspelled_words.clear()
        self.error_spans.clear()
        
        words = re.finditer(r'\b\w+\b', text)
        for match in words:
            word = match.group(0)
            if self.spell.unknown([word]):
                self.misspelled_words.add(word)
                self.error_spans.append((match.start(), match.end()))
        
        self.update() 

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.error_spans:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        pen = QPen(Qt.red)
        pen.setCosmetic(True)
        painter.setPen(pen)

        fm = self.fontMetrics()
        text = self.text()
        
        from PySide6.QtWidgets import QStyle, QStyleOptionFrame
        opt = QStyleOptionFrame()
        self.initStyleOption(opt)
        contents = self.style().subElementRect(QStyle.SubElement.SE_LineEditContents, opt)
        left_m, top_m, right_m, bottom_m = self.getTextMargins()
        text_rect = contents.adjusted(left_m, top_m, -right_m, -bottom_m)
        
        vpad = max(0, (text_rect.height() - fm.height()) // 2)
        cur_idx = self.cursorPosition()
        cur_left = self.cursorRect().left()
        x_offset = cur_left - fm.horizontalAdvance(text[:cur_idx])
        
        baseline_y = (
            text_rect.top()
            + vpad
            + fm.ascent()
            + max(2, int(fm.descent() * 0.95))
        )

        wave_len = 4
        wave_amp = 1.5
        clip_left, clip_right = text_rect.left(), text_rect.right()

        for start, end in self.error_spans:
            sx = text_rect.left() + fm.horizontalAdvance(text[:start]) + x_offset
            ex = text_rect.left() + fm.horizontalAdvance(text[:end]) + x_offset

            if ex < clip_left or sx > clip_right:
                continue
            sx = max(sx, clip_left)
            ex = min(ex, clip_right)

            path = QPainterPath()
            x = sx
            path.moveTo(x, baseline_y)
            while x < ex:
                mid = x + wave_len / 2.0
                nx = min(x + wave_len, ex)
                path.quadTo(mid, baseline_y + wave_amp, nx, baseline_y)
                x = nx

            painter.strokePath(path, pen)

    def getStyleOption(self):
        from PySide6.QtWidgets import QStyleOptionFrame
        opt = QStyleOptionFrame()
        self.initStyleOption(opt)
        return opt

    def contextMenuEvent(self, event):
        if not SPELLCHECK_AVAILABLE:
            super().contextMenuEvent(event)
            return

        menu = self.createStandardContextMenu()
        
        char_index = self.cursorPositionAt(event.pos())
        
        word_span = None
        clicked_word = ""
        for start, end in self.error_spans:
            if start <= char_index < end:
                word_span = (start, end)
                clicked_word = self.text()[start:end]
                break

        if clicked_word:
            suggestions = self.spell.candidates(clicked_word)
            if suggestions:
                menu.addSeparator()
                for suggestion in sorted(list(suggestions))[:5]:
                    action = QAction(suggestion, self)
                    action.triggered.connect(lambda checked=False, s=suggestion, ws=word_span: self._replace_word(s, ws[0], ws[1]))
                    menu.addAction(action)

        menu.exec(event.globalPos())

    def _replace_word(self, suggestion, start, end):
        current_text = self.text()
        new_text = current_text[:start] + suggestion + current_text[end:]
        self.setText(new_text)

class _BlackHoleEditor(QPlainTextEdit):
    sendRequested = Signal()
    largePasteDetected = Signal(str)
    filesDropped = Signal(list)
    textDropped = Signal(str)
    heightAdjusted = Signal(int)
    dragStateChanged = Signal(bool)
    focusChanged = Signal(bool)

    LARGE_PASTE_CHAR_THRESHOLD = 1400
    LARGE_PASTE_LINE_THRESHOLD = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self._min_height = 40
        self._max_height = 170
        self.setObjectName("blackHoleEditor")
        self.setFixedHeight(self._min_height)
        self.setTabChangesFocus(False)
        self.setAcceptDrops(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().documentLayout().documentSizeChanged.connect(self._adjust_height)
        self.textChanged.connect(self._adjust_height)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            elif not (
                event.modifiers() & Qt.KeyboardModifier.ControlModifier
                or event.modifiers() & Qt.KeyboardModifier.AltModifier
                or event.modifiers() & Qt.KeyboardModifier.MetaModifier
            ):
                self.sendRequested.emit()
                event.accept()
            else:
                super().keyPressEvent(event)
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event):
        self.focusChanged.emit(True)
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self.focusChanged.emit(False)
        super().focusOutEvent(event)

    def insertFromMimeData(self, source):
        if source and source.hasText() and not source.hasUrls():
            pasted_text = source.text()
            if self._is_large_paste(pasted_text):
                self.largePasteDetected.emit(pasted_text)
                return
        super().insertFromMimeData(source)

    def dragEnterEvent(self, event):
        if self._can_consume_mime_data(event.mimeData()):
            self.dragStateChanged.emit(True)
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._can_consume_mime_data(event.mimeData()):
            self.dragStateChanged.emit(True)
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self.dragStateChanged.emit(False)
        event.accept()

    def dropEvent(self, event):
        self.dragStateChanged.emit(False)
        if self._emit_drop_payload(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def _emit_drop_payload(self, mime_data):
        if not mime_data:
            return False

        if mime_data.hasUrls():
            file_paths = [url.toLocalFile() for url in mime_data.urls() if url.isLocalFile()]
            if file_paths:
                self.filesDropped.emit(file_paths)
                return True

        if mime_data.hasText():
            dropped_text = mime_data.text()
            if dropped_text and dropped_text.strip():
                self.textDropped.emit(dropped_text)
                return True

        return False

    def _can_consume_mime_data(self, mime_data):
        if not mime_data:
            return False
        if mime_data.hasUrls():
            return any(url.isLocalFile() for url in mime_data.urls())
        return mime_data.hasText() and bool(mime_data.text().strip())

    def _is_large_paste(self, pasted_text):
        if not pasted_text:
            return False
        char_count = len(pasted_text)
        line_count = pasted_text.count('\n') + 1
        return (
            char_count >= self.LARGE_PASTE_CHAR_THRESHOLD
            or line_count >= self.LARGE_PASTE_LINE_THRESHOLD
        )

    def _adjust_height(self, *_):
        doc_height = int(self.document().size().height())
        frame_height = int(self.frameWidth()) * 2
        target_height = max(self._min_height, min(self._max_height, doc_height + frame_height + 8))
        self.setFixedHeight(target_height)
        if doc_height + frame_height + 8 > self._max_height:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        else:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.heightAdjusted.emit(target_height)


class ComposerSurface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._radius = 14.0
        self._background = QColor("#2a2a2a")
        self._border = QColor("#3f3f3f")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)

    def set_colors(self, background: QColor, border: QColor):
        self._background = QColor(background)
        self._border = QColor(border)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        rect = QRectF(self.rect())
        rect.adjust(1.0, 1.0, -1.0, -1.0)

        fill_path = QPainterPath()
        fill_path.addRoundedRect(rect, self._radius, self._radius)
        painter.fillPath(fill_path, self._background)

        if self._border.alpha() > 0:
            pen = QPen(self._border)
            pen.setWidthF(1.25)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(fill_path)


class ContextAttachmentPill(QFrame):
    removeRequested = Signal(str)

    def __init__(self, attachment, parent=None):
        super().__init__(parent)
        self.attachment = attachment
        self.attachment_key = attachment.get('path', '')
        self.setObjectName("contextAttachmentPill")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 8, 5)
        layout.setSpacing(6)

        icon_name = self._icon_name_for_attachment(attachment)
        icon_color = self._icon_color_for_attachment(attachment)

        icon_label = QLabel(self)
        icon_label.setPixmap(qta.icon(icon_name, color=icon_color).pixmap(12, 12))
        icon_label.setStyleSheet("background: transparent;")
        layout.addWidget(icon_label)

        title_label = QLabel(self._shorten_title(attachment.get('name') or "Context"), self)
        title_label.setStyleSheet("background: transparent; color: #f2f2f2; font-size: 11px; font-weight: bold;")
        layout.addWidget(title_label)

        meta_text = self._meta_text_for_attachment(attachment)
        if meta_text:
            meta_label = QLabel(meta_text, self)
            meta_label.setStyleSheet("background: transparent; color: #aab4bc; font-size: 10px;")
            layout.addWidget(meta_label)

        remove_button = QPushButton("x", self)
        remove_button.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_button.setFixedSize(16, 16)
        remove_button.setFlat(True)
        remove_button.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #b8c2cc;
                border: none;
                border-radius: 8px;
                font-size: 10px;
                font-weight: bold;
                padding: 0;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.12);
                color: #ffffff;
            }
        """)
        remove_button.clicked.connect(lambda: self.removeRequested.emit(self.attachment_key))
        layout.addWidget(remove_button)

        self.setStyleSheet("""
            QFrame#contextAttachmentPill {
                background-color: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 14px;
            }
        """)

    def _icon_name_for_attachment(self, attachment):
        kind = attachment.get('kind')
        context_label = (attachment.get('context_label') or "").lower()
        if kind == 'image':
            return 'fa5s.image'
        if kind == 'audio':
            return 'fa5s.music'
        if 'pdf' in context_label:
            return 'fa5s.file-pdf'
        if 'code' in context_label:
            return 'fa5s.file-code'
        return 'fa5s.file-alt'

    def _icon_color_for_attachment(self, attachment):
        if attachment.get('kind') == 'image':
            return '#5dade2'
        if attachment.get('kind') == 'audio':
            return '#76d7c4'
        context_label = (attachment.get('context_label') or "").lower()
        if 'pdf' in context_label:
            return '#f1948a'
        if 'code' in context_label:
            return '#58d68d'
        return '#f7dc6f'

    def _meta_text_for_attachment(self, attachment):
        parts = []
        context_label = attachment.get('context_label')
        if context_label:
            parts.append(context_label)

        token_count = attachment.get('token_count')
        if isinstance(token_count, int) and token_count > 0:
            parts.append(f"{token_count:,} tok")

        line_count = attachment.get('line_count')
        if isinstance(line_count, int) and line_count > 1 and attachment.get('kind') != 'image':
            parts.append(f"{line_count:,} lines")

        if attachment.get('kind') in {'image', 'audio'} and attachment.get('byte_size'):
            kb_size = max(1, int(round(attachment['byte_size'] / 1024)))
            parts.append(f"{kb_size} KB")

        return " | ".join(parts)

    def _shorten_title(self, title):
        if len(title) <= 34:
            return title
        return f"{title[:31]}..."


class ChatInputTextEdit(QWidget):
    sendRequested = Signal()
    largePasteDetected = Signal(str)
    filesDropped = Signal(list)
    textDropped = Signal(str)
    attachmentRemoved = Signal(str)
    composerHeightChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drop_active = False
        self._is_focused = False
        self._context_items = []
        self.setObjectName("blackHoleComposer")
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(6)

        self.pill_scroll = QScrollArea(self)
        self.pill_scroll.setObjectName("blackHolePillStrip")
        self.pill_scroll.setWidgetResizable(True)
        self.pill_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.pill_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.pill_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.pill_scroll.setFixedHeight(34)
        self.pill_scroll.hide()

        self.pill_host = QWidget(self.pill_scroll)
        self.pill_host.setObjectName("blackHolePillHost")
        self.pill_layout = QHBoxLayout(self.pill_host)
        self.pill_layout.setContentsMargins(0, 0, 0, 0)
        self.pill_layout.setSpacing(6)
        self.pill_layout.addStretch(1)
        self.pill_scroll.setWidget(self.pill_host)
        outer_layout.addWidget(self.pill_scroll)

        self.surface = ComposerSurface(self)
        self.surface.setObjectName("blackHoleSurface")
        self.surface.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        outer_layout.addWidget(self.surface)

        surface_layout = QVBoxLayout(self.surface)
        surface_layout.setContentsMargins(10, 7, 10, 7)
        surface_layout.setSpacing(0)

        self.editor = _BlackHoleEditor(self.surface)
        self.editor.setFrameStyle(QFrame.Shape.NoFrame)
        self.editor.viewport().setAutoFillBackground(False)
        self.editor.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.editor.sendRequested.connect(self.sendRequested.emit)
        self.editor.largePasteDetected.connect(self.largePasteDetected.emit)
        self.editor.filesDropped.connect(self.filesDropped.emit)
        self.editor.textDropped.connect(self.textDropped.emit)
        self.editor.heightAdjusted.connect(self._sync_height)
        self.editor.dragStateChanged.connect(self._set_drop_active)
        self.editor.focusChanged.connect(self._set_editor_focused)
        surface_layout.addWidget(self.editor)

        self._apply_styles()
        self._sync_height()

    def text(self):
        return self.editor.toPlainText()

    def setText(self, text):
        self.editor.setPlainText(text)
        self.editor._adjust_height()

    def clear(self):
        self.editor.clear()
        self.editor._adjust_height()

    def insertPlainText(self, text):
        self.editor.insertPlainText(text)

    def setPlaceholderText(self, text):
        self.editor.setPlaceholderText(text)

    def setEnabled(self, enabled):
        super().setEnabled(enabled)
        self.surface.setEnabled(enabled)
        self.editor.setEnabled(enabled)
        for pill in self.findChildren(ContextAttachmentPill):
            pill.setEnabled(enabled)
        self._apply_styles()

    def setFocus(self):
        self.editor.setFocus()

    def focusWidget(self):
        return self.editor

    def on_theme_changed(self):
        self._apply_styles()
        self.set_context_items(self._context_items)

    def set_context_items(self, items):
        self._context_items = list(items or [])
        self._clear_pills()

        if not self._context_items:
            self.pill_scroll.hide()
            self._sync_height()
            return

        for attachment in self._context_items:
            pill = ContextAttachmentPill(attachment, self.pill_host)
            pill.removeRequested.connect(self.attachmentRemoved.emit)
            self.pill_layout.insertWidget(self.pill_layout.count() - 1, pill)

        self.pill_scroll.show()
        self._sync_height()

    def dragEnterEvent(self, event):
        if self._can_consume_mime_data(event.mimeData()):
            self._set_drop_active(True)
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._can_consume_mime_data(event.mimeData()):
            self._set_drop_active(True)
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self._set_drop_active(False)
        event.accept()

    def dropEvent(self, event):
        self._set_drop_active(False)
        if self._emit_drop_payload(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def _can_consume_mime_data(self, mime_data):
        if not mime_data:
            return False
        if mime_data.hasUrls():
            return any(url.isLocalFile() for url in mime_data.urls())
        return mime_data.hasText() and bool(mime_data.text().strip())

    def _emit_drop_payload(self, mime_data):
        if not mime_data:
            return False

        if mime_data.hasUrls():
            file_paths = [url.toLocalFile() for url in mime_data.urls() if url.isLocalFile()]
            if file_paths:
                self.filesDropped.emit(file_paths)
                return True

        if mime_data.hasText():
            dropped_text = mime_data.text()
            if dropped_text and dropped_text.strip():
                self.textDropped.emit(dropped_text)
                return True

        return False

    def _clear_pills(self):
        while self.pill_layout.count() > 1:
            item = self.pill_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _set_drop_active(self, active):
        self._drop_active = bool(active)
        self._apply_styles()

    def _set_editor_focused(self, focused):
        self._is_focused = bool(focused)
        self._apply_styles()

    def _sync_height(self, *_):
        pills_height = 0
        if self._context_items:
            pills_height = max(self.pill_scroll.height(), self.pill_scroll.sizeHint().height(), 34)
        surface_height = max(50, self.editor.height() + 14)
        self.surface.setFixedHeight(surface_height)
        total_height = surface_height + pills_height
        if pills_height:
            total_height += 6
        self.setFixedHeight(total_height)
        self.updateGeometry()
        self.composerHeightChanged.emit(total_height)

    def paintEvent(self, event):
        # Keep the outer composer shell transparent so only the rounded surface paints.
        event.accept()

    def _apply_styles(self):
        palette = get_current_palette()
        if self._drop_active:
            border_color = QColor(palette.SELECTION.lighter(115))
            bg_color = QColor(44, 44, 44, 244)
        elif self._is_focused:
            border_color = QColor("#6a6a6a")
            bg_color = QColor(43, 43, 43, 238)
        else:
            border_color = QColor(76, 76, 76, 180)
            bg_color = QColor(40, 40, 40, 228)
        self.surface.set_colors(bg_color, border_color)

        self.setStyleSheet(f"""
            QWidget#blackHoleComposer {{
                background-color: transparent;
                border: none;
            }}
            QScrollArea#blackHolePillStrip {{
                background: transparent;
                border: none;
            }}
            QWidget#blackHolePillHost, QWidget#qt_scrollarea_viewport {{
                background-color: transparent;
                border: none;
            }}
            QPlainTextEdit#blackHoleEditor {{
                background-color: transparent;
                color: #d4d4d4;
                border: none;
                padding: 4px 6px 4px 6px;
                selection-background-color: #264f78;
            }}
            QPlainTextEdit#blackHoleEditor:disabled {{
                color: #7b7b7b;
            }}
        """)


