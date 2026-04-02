import sys
import os
import re
import json
import tempfile
import subprocess
import markdown
from enum import Enum

from PySide6.QtWidgets import (
    QGraphicsItem, QGraphicsProxyWidget, QWidget, QVBoxLayout,
    QTextEdit, QPushButton, QLabel, QFrame, QHBoxLayout, QGridLayout,
    QTabWidget, QPlainTextEdit
)
from PySide6.QtCore import QRectF, Qt, Property, QPropertyAnimation, QEasingCurve, QPointF, QRegularExpression, QSize, QRect
from PySide6.QtGui import QPainter, QColor, QBrush, QPen, QPainterPath, QIcon, QSyntaxHighlighter, QTextCharFormat, QFont
import qtawesome as qta
from graphite_config import get_current_palette, get_graph_node_colors, get_neutral_button_colors, get_semantic_color
from graphite_lod import draw_lod_card, preview_text, sync_proxy_render_state

from graphite_agents_pycoder import PyCoderStage, PyCoderStatus, PythonREPL
from graphite_canvas_items import HoverAnimationMixin
from graphite_plugin_context_menu import PluginNodeContextMenu


class PythonHighlighter(QSyntaxHighlighter):
    """Syntax highlighter for Python code."""
    def __init__(self, document):
        super().__init__(document)
        self.highlighting_rules = []

        # Keywords
        keyword_format = QTextCharFormat()
        keyword_format.setForeground(QColor("#c678dd")) # Purple
        keyword_format.setFontWeight(QFont.Weight.Bold)
        keywords = [
            "and", "as", "assert", "break", "class", "continue", "def",
            "del", "elif", "else", "except", "False", "finally", "for",
            "from", "global", "if", "import", "in", "is", "lambda", "None",
            "nonlocal", "not", "or", "pass", "raise", "return", "True",
            "try", "while", "with", "yield"
        ]
        for word in keywords:
            pattern = QRegularExpression(rf"\b{word}\b")
            self.highlighting_rules.append((pattern, keyword_format))

        # Builtins
        builtin_format = QTextCharFormat()
        builtin_format.setForeground(QColor("#56b6c2")) # Cyan
        builtins = ["print", "len", "range", "int", "float", "str", "list", "dict", "set", "tuple", "open", "type", "dir"]
        for word in builtins:
            pattern = QRegularExpression(rf"\b{word}\b")
            self.highlighting_rules.append((pattern, builtin_format))

        # Numbers
        number_format = QTextCharFormat()
        number_format.setForeground(QColor("#d19a66")) # Orange
        self.highlighting_rules.append((QRegularExpression(r"\b[0-9]+(?:\.[0-9]+)?\b"), number_format))

        # Strings
        string_format = QTextCharFormat()
        string_format.setForeground(QColor("#98c379")) # Green
        self.highlighting_rules.append((QRegularExpression(r'".*?"'), string_format))
        self.highlighting_rules.append((QRegularExpression(r"'.*?'"), string_format))

        # Comments
        comment_format = QTextCharFormat()
        comment_format.setForeground(QColor("#5c6370")) # Grey
        comment_format.setFontItalic(True)
        self.highlighting_rules.append((QRegularExpression(r"#[^\n]*"), comment_format))
        
        # Functions
        function_format = QTextCharFormat()
        function_format.setForeground(QColor("#61afef")) # Blue
        self.highlighting_rules.append((QRegularExpression(r"\b[A-Za-z0-9_]+(?=\()"), function_format))

    def highlightBlock(self, text):
        for pattern, format in self.highlighting_rules:
            match_iterator = pattern.globalMatch(text)
            while match_iterator.hasNext():
                match = match_iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), format)


class LineNumberArea(QWidget):
    """Widget specifically for drawing line numbers beside a CodeEditor."""
    def __init__(self, editor):
        super().__init__(editor)
        self.codeEditor = editor

    def sizeHint(self):
        return QSize(self.codeEditor.lineNumberAreaWidth(), 0)

    def paintEvent(self, event):
        self.codeEditor.lineNumberAreaPaintEvent(event)


class CodeEditor(QPlainTextEdit):
    """A custom QPlainTextEdit that includes line numbers and syntax highlighting."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.lineNumberArea = LineNumberArea(self)

        self.blockCountChanged.connect(self.updateLineNumberAreaWidth)
        self.updateRequest.connect(self.updateLineNumberArea)
        
        self.updateLineNumberAreaWidth(0)

    def lineNumberAreaWidth(self):
        digits = 1
        max_val = max(1, self.blockCount())
        while max_val >= 10:
            max_val /= 10
            digits += 1
        space = 10 + self.fontMetrics().horizontalAdvance('9') * digits
        return space

    def updateLineNumberAreaWidth(self, _):
        self.setViewportMargins(self.lineNumberAreaWidth(), 0, 0, 0)

    def updateLineNumberArea(self, rect, dy):
        if dy:
            self.lineNumberArea.scroll(0, dy)
        else:
            self.lineNumberArea.update(0, rect.y(), self.lineNumberArea.width(), rect.height())
        
        if rect.contains(self.viewport().rect()):
            self.updateLineNumberAreaWidth(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.lineNumberArea.setGeometry(QRect(cr.left(), cr.top(), self.lineNumberAreaWidth(), cr.height()))

    def lineNumberAreaPaintEvent(self, event):
        painter = QPainter(self.lineNumberArea)
        painter.fillRect(event.rect(), QColor("#1e1e1e").lighter(120))

        block = self.firstVisibleBlock()
        blockNumber = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(blockNumber + 1)
                painter.setPen(QColor("#6b6b6b"))
                font = painter.font()
                font.setFamily("Consolas")
                painter.setFont(font)
                painter.drawText(0, top, self.lineNumberArea.width() - 5, self.fontMetrics().height(),
                                 Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, number)

            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            blockNumber += 1


class PyCoderMode(Enum):
    AI_DRIVEN = 1
    MANUAL = 2


class StatusIconWidget(QWidget):
    """A custom-painted widget for displaying crisp, vector-based status icons."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self._status = PyCoderStatus.PENDING
        self._angle = 0

        self.animation = QPropertyAnimation(self, b"angle")
        self.animation.setStartValue(0)
        self.animation.setEndValue(360)
        self.animation.setDuration(1200)
        self.animation.setLoopCount(-1)
        self.animation.setEasingCurve(QEasingCurve.Type.Linear)

    def set_status(self, status):
        if self._status != status:
            self._status = status
            if self._status == PyCoderStatus.RUNNING:
                self.animation.start()
            else:
                self.animation.stop()
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(2, 2, -2, -2)
        palette = get_current_palette()

        if self._status == PyCoderStatus.PENDING:
            painter.setPen(QPen(QColor("#555555"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rect)
        elif self._status == PyCoderStatus.RUNNING:
            pen = QPen(palette.AI_NODE, 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(rect, int(self._angle * 16), int(270 * 16))
        elif self._status == PyCoderStatus.SUCCESS:
            painter.setPen(QPen(palette.USER_NODE, 2))
            painter.setBrush(palette.USER_NODE)
            painter.drawEllipse(rect)
            
            check_path = QPainterPath()
            check_path.moveTo(rect.center().x() - 4, rect.center().y())
            check_path.lineTo(rect.center().x() - 1, rect.center().y() + 3)
            check_path.lineTo(rect.center().x() + 4, rect.center().y() - 2)
            
            pen = QPen(QColor("white"), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(check_path)
        elif self._status == PyCoderStatus.FAILURE:
            error_color = get_semantic_color("status_error")
            painter.setPen(QPen(error_color, 2))
            painter.setBrush(error_color)
            painter.drawEllipse(rect)

            pen = QPen(QColor("white"), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            center = rect.center()
            painter.drawLine(center.x() - 3, center.y() - 3, center.x() + 3, center.y() + 3)
            painter.drawLine(center.x() - 3, center.y() + 3, center.x() + 3, center.y() - 3)

    @Property(int)
    def angle(self):
        return self._angle

    @angle.setter
    def angle(self, value):
        self._angle = value
        self.update()


class StatusItemWidget(QWidget):
    """A widget representing a single step in the status tracker checklist."""
    def __init__(self, text, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.icon_widget = StatusIconWidget()
        self.text_label = QLabel(text)
        self.text_label.setStyleSheet("color: #888888; font-size: 11px;")

        layout.addWidget(self.icon_widget)
        layout.addWidget(self.text_label)
        layout.addStretch()

        self.set_status(PyCoderStatus.PENDING)

    def set_status(self, status):
        self.icon_widget.set_status(status)
        palette = get_current_palette()
        if status == PyCoderStatus.PENDING:
            self.text_label.setStyleSheet("color: #888888; font-style: italic; font-size: 11px;")
        elif status == PyCoderStatus.RUNNING:
            self.text_label.setStyleSheet(f"color: {palette.AI_NODE.name()}; font-style: normal; font-weight: bold; font-size: 11px;")
        elif status == PyCoderStatus.SUCCESS:
            self.text_label.setStyleSheet(f"color: {palette.USER_NODE.name()}; font-style: normal; font-weight: bold; font-size: 11px;")
        elif status == PyCoderStatus.FAILURE:
            self.text_label.setStyleSheet(f"color: {get_semantic_color('status_error').name()}; font-style: normal; font-weight: bold; font-size: 11px;")


class StatusTrackerWidget(QWidget):
    """The checklist widget that holds multiple StatusItemWidgets in a neat grid."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.stages = {
            PyCoderStage.ANALYZE: StatusItemWidget("Analyze Prompt"),
            PyCoderStage.GENERATE: StatusItemWidget("Generate Code"),
            PyCoderStage.EXECUTE: StatusItemWidget("Execute & Repair"),
            PyCoderStage.ANALYZE_RESULT: StatusItemWidget("Final Analysis")
        }

        layout = QGridLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        layout.addWidget(self.stages[PyCoderStage.ANALYZE], 0, 0)
        layout.addWidget(self.stages[PyCoderStage.GENERATE], 0, 1)
        layout.addWidget(self.stages[PyCoderStage.EXECUTE], 1, 0)
        layout.addWidget(self.stages[PyCoderStage.ANALYZE_RESULT], 1, 1)
            
        self.setStyleSheet("""
            StatusTrackerWidget {
                background-color: #1e1e1e;
                border: 1px solid #3f3f3f;
                border-radius: 6px;
            }
            QLabel { background: transparent; }
        """)
        
    def update_status(self, stage, status):
        if stage in self.stages:
            self.stages[stage].set_status(status)

    def reset_statuses(self):
        for stage_widget in self.stages.values():
            stage_widget.set_status(PyCoderStatus.PENDING)


class PyCoderNode(QGraphicsItem, HoverAnimationMixin):
    """
    A specialized QGraphicsItem that provides a UI for both AI-driven code generation
    and manual code execution, styled like a modern IDE pane.
    """
    supports_branch_context_toggle = True

    NODE_WIDTH = 550
    NODE_HEIGHT = 700
    COLLAPSED_WIDTH = 250
    COLLAPSED_HEIGHT = 40
    CONNECTION_DOT_RADIUS = 5
    CONNECTION_DOT_OFFSET = 0

    def __init__(self, parent_node, mode=PyCoderMode.AI_DRIVEN, parent=None):
        super().__init__(parent)
        HoverAnimationMixin.__init__(self)
        self.parent_node = parent_node
        self.mode = mode
        self.children = []
        self.conversation_history = []
        self.is_user = False
        
        self.is_running = False
        self.is_collapsed = False
        self.collapse_button_rect = QRectF()
        self.repl = PythonREPL()

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption)
        self.setAcceptHoverEvents(True)
        self.hovered = False
        self._render_lod_mode = "full"

        self.widget = QWidget()
        self.widget.setObjectName("pyCoderMainWidget")
        self.widget.setFixedSize(self.NODE_WIDTH, self.NODE_HEIGHT)
        self.widget.setStyleSheet("""
            QWidget#pyCoderMainWidget {
                background-color: transparent;
                color: #e0e0e0;
                font-family: 'Segoe UI', sans-serif;
            }
            QWidget#pyCoderMainWidget QLabel {
                background-color: transparent;
            }
        """)

        self._setup_ui()

        self.proxy = QGraphicsProxyWidget(self)
        self.proxy.setWidget(self.widget)

        self._update_ui_for_mode()

    def __del__(self):
        if hasattr(self, 'repl') and self.repl:
            self.repl.stop()

    @property
    def width(self):
        return self.COLLAPSED_WIDTH if self.is_collapsed else self.NODE_WIDTH

    @property
    def height(self):
        return self.COLLAPSED_HEIGHT if self.is_collapsed else self.NODE_HEIGHT

    def set_collapsed(self, collapsed):
        if self.is_collapsed != collapsed:
            self.is_collapsed = collapsed
            self.proxy.setVisible(not self.is_collapsed and self._render_lod_mode == "full")
            self.prepareGeometryChange()
            if self.scene():
                self.sync_view_lod()
                self.scene().update_connections()
                self.scene().nodeMoved(self)
            self.update()

    def toggle_collapse(self):
        self.set_collapsed(not self.is_collapsed)

    def sync_view_lod(self, view_rect=None, zoom=None):
        sync_proxy_render_state(self, view_rect, zoom)
        if not self.is_collapsed:
            self.update()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self.widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(12)

        node_colors = get_graph_node_colors()
        pycoder_color = node_colors["header"]

        # --- Header ---
        header_layout = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(qta.icon('fa5s.laptop-code', color=pycoder_color).pixmap(20, 20))
        header_layout.addWidget(icon)
        
        self.title_label = QLabel("Py-Coder (AI-Driven)")
        self.title_label.setStyleSheet(f"font-weight: bold; font-size: 15px; color: {pycoder_color.name()}; background: transparent;")
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()
        
        self.mode_toggle_button = QPushButton()
        self.mode_toggle_button.setFixedSize(30, 30)
        self.mode_toggle_button.clicked.connect(self._toggle_mode)
        self.mode_toggle_button.setStyleSheet("""
            QPushButton { border: 1px solid #555; border-radius: 15px; background-color: #2a2a2a; }
            QPushButton:hover { background-color: #4f4f4f; border: 1px solid #777; }
        """)
        header_layout.addWidget(self.mode_toggle_button)
        main_layout.addLayout(header_layout)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: #3f3f3f; border: none; height: 1px;")
        main_layout.addWidget(line)

        # --- AI-Driven Input Container ---
        self.prompt_container = QWidget()
        prompt_layout = QVBoxLayout(self.prompt_container)
        prompt_layout.setContentsMargins(0, 0, 0, 0)
        prompt_layout.setSpacing(5)
        
        self.ai_prompt_label = QLabel("Instruction Prompt:")
        self.ai_prompt_label.setStyleSheet("color: #aaaaaa; font-weight: bold; font-size: 12px; background: transparent;")
        prompt_layout.addWidget(self.ai_prompt_label)
        
        self.prompt_input = QTextEdit()
        self.prompt_input.setPlaceholderText("e.g., 'Calculate the factorial of 15 and explain the result.'")
        self.prompt_input.setFixedHeight(70)
        self.prompt_input.setStyleSheet("""
            QTextEdit { 
                background-color: #1e1e1e; border: 1px solid #3f3f3f; 
                border-radius: 6px; padding: 8px; color: #ffffff;
                font-family: 'Segoe UI', sans-serif;
            }
            QTextEdit:focus { border: 1px solid #9b59b6; }
        """)
        prompt_layout.addWidget(self.prompt_input)
        
        self.generate_button = QPushButton(" Generate & Execute")
        self.generate_button.clicked.connect(self._on_run_clicked)
        prompt_layout.addWidget(self.generate_button)
        main_layout.addWidget(self.prompt_container)

        # --- Manual Input Container ---
        self.manual_code_container = QWidget()
        manual_layout = QVBoxLayout(self.manual_code_container)
        manual_layout.setContentsMargins(0, 0, 0, 0)
        manual_layout.setSpacing(5)
        
        self.manual_code_label = QLabel("Python Code:")
        self.manual_code_label.setStyleSheet("color: #aaaaaa; font-weight: bold; font-size: 12px; background: transparent;")
        manual_layout.addWidget(self.manual_code_label)

        self.code_input = CodeEditor()
        self.code_input.setPlaceholderText("Enter Python code to execute...")
        self.code_input.setFixedHeight(140)
        self.code_input.setStyleSheet("""
            QPlainTextEdit { 
                background-color: #1e1e1e; border: 1px solid #3f3f3f; 
                border-radius: 6px; padding: 8px; color: #dcdcaa;
                font-family: Consolas, Monaco, monospace; font-size: 13px;
            }
            QPlainTextEdit:focus { border: 1px solid #9b59b6; }
        """)
        manual_layout.addWidget(self.code_input)

        self.run_button = QPushButton(" Run Code")
        self.run_button.clicked.connect(self._on_run_clicked)
        manual_layout.addWidget(self.run_button)
        main_layout.addWidget(self.manual_code_container)

        # Apply common button styles
        button_colors = get_neutral_button_colors()
        self._default_btn_style = f"""
            QPushButton {{
                background-color: {button_colors["background"].name()};
                color: {button_colors["icon"].name()};
                border: 1px solid {button_colors["border"].name()};
                border-radius: 6px;
                padding: 10px; font-weight: bold; font-size: 13px;
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
        """
        for btn in [self.generate_button, self.run_button]:
            btn.setStyleSheet(self._default_btn_style)
        
        self.generate_button.setIcon(qta.icon('fa5s.cogs', color=button_colors["icon"].name()))
        self.run_button.setIcon(qta.icon('fa5s.play', color=button_colors["icon"].name()))

        # --- Status Tracker ---
        self.status_tracker = StatusTrackerWidget()
        main_layout.addWidget(self.status_tracker)

        # --- Output Tabs ---
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 1px solid #3f3f3f; background: #1e1e1e; border-radius: 6px;
            }}
            QTabBar::tab {{
                background: #252526; color: #aaaaaa; padding: 8px 16px;
                border: 1px solid #3f3f3f; border-bottom: none;
                border-top-left-radius: 6px; border-top-right-radius: 6px;
                margin-right: 2px; font-weight: bold;
            }}
            QTabBar::tab:selected {{
                background: #1e1e1e; color: #ffffff;
                border-top: 2px solid {pycoder_color.name()};
                border-bottom: 1px solid #1e1e1e;
            }}
            QTabBar::tab:hover:!selected {{
                background: #2d2d2d; color: #ffffff;
            }}
        """)

        # 1. Generated Code Tab
        self.generated_code_display = CodeEditor()
        self.generated_code_display.setReadOnly(True)
        self.generated_code_display.setPlaceholderText("Generated code will appear here...")
        self.generated_code_display.setStyleSheet("""
            QPlainTextEdit { background-color: transparent; color: #dcdcaa; font-family: Consolas, monospace; border: none; padding: 10px; font-size: 13px; }
        """)
        self.tabs.addTab(self.generated_code_display, qta.icon('fa5s.code', color='#ccc'), "Code")

        # 2. Terminal / Output Tab
        self.output_display = QTextEdit()
        self.output_display.setReadOnly(True)
        self.output_display.setPlaceholderText("Execution output will appear here...")
        self.output_display.setStyleSheet("""
            QTextEdit { background-color: #0d0d0d; color: #2ecc71; font-family: Consolas, monospace; border: none; padding: 10px; font-size: 12px; }
        """)
        self.tabs.addTab(self.output_display, qta.icon('fa5s.terminal', color='#ccc'), "Terminal")

        # 3. AI Analysis Tab
        self.ai_analysis_display = QTextEdit()
        self.ai_analysis_display.setReadOnly(True)
        self.ai_analysis_display.setPlaceholderText("AI analysis and explanations will appear here...")
        self.ai_analysis_display.setStyleSheet("""
            QTextEdit { background-color: transparent; border: none; padding: 10px; }
        """)
        # Base styling for markdown content inside the text edit
        self.ai_analysis_display.document().setDefaultStyleSheet("""
            p, ul, ol, li { color: #e0e0e0; font-family: 'Segoe UI', sans-serif; font-size: 13px; line-height: 1.5; }
            h1, h2, h3, h4 { color: #ffffff; font-weight: bold; margin-bottom: 5px; }
            pre { background-color: #2d2d2d; padding: 10px; border-radius: 6px; font-family: Consolas, monospace; color: #dcdcaa; }
            code { background-color: #3f3f3f; color: #dcdcaa; padding: 2px 4px; border-radius: 4px; font-family: Consolas, monospace; }
            blockquote { border-left: 3px solid #555555; padding-left: 10px; color: #aaaaaa; }
        """)
        self.tabs.addTab(self.ai_analysis_display, qta.icon('fa5s.robot', color='#ccc'), "Analysis")

        main_layout.addWidget(self.tabs)
        
        # Attach Syntax Highlighters
        self.highlighter_manual = PythonHighlighter(self.code_input.document())
        self.highlighter_generated = PythonHighlighter(self.generated_code_display.document())

    def _toggle_mode(self):
        if self.mode == PyCoderMode.AI_DRIVEN:
            self.mode = PyCoderMode.MANUAL
        else:
            self.mode = PyCoderMode.AI_DRIVEN
        self._update_ui_for_mode()
    
    def _update_ui_for_mode(self):
        is_ai_mode = self.mode == PyCoderMode.AI_DRIVEN

        self.prompt_container.setVisible(is_ai_mode)
        self.status_tracker.setVisible(is_ai_mode)
        self.manual_code_container.setVisible(not is_ai_mode)

        if is_ai_mode:
            self.title_label.setText("Py-Coder (AI-Driven)")
            self.mode_toggle_button.setIcon(qta.icon('fa5s.user-edit', color='#ccc'))
            self.mode_toggle_button.setToolTip("Switch to Manual Mode")
            self.tabs.setTabVisible(0, True) # Show Code tab
            self.tabs.setCurrentIndex(0)
        else:
            self.title_label.setText("Py-Coder (Manual)")
            self.mode_toggle_button.setIcon(qta.icon('fa5s.magic', color='#ccc'))
            self.mode_toggle_button.setToolTip("Switch to AI-Driven Mode")
            self.tabs.setTabVisible(0, False) # Hide Code tab (it's in the top input box now)
            self.tabs.setCurrentIndex(1) # Default to Terminal tab

    def _on_run_clicked(self):
        if self.scene() and hasattr(self.scene(), 'window'):
            if not self.is_running:
                self.tabs.setCurrentIndex(1 if self.mode == PyCoderMode.MANUAL else 0)
            self.scene().window.execute_pycoder_node(self)

    def boundingRect(self):
        padding = self.CONNECTION_DOT_OFFSET + self.CONNECTION_DOT_RADIUS
        return QRectF(-padding, 0, self.width + 2 * padding, self.height)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = get_current_palette()
        node_colors = get_graph_node_colors()
        render_mode = getattr(self, "_render_lod_mode", "full")
        
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width, self.height, 10, 10)
        painter.setBrush(QColor("#2d2d2d"))
        
        pycoder_color = node_colors["border"]
        pen = QPen(pycoder_color, 1.5)

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

        if not self.is_collapsed and render_mode != "full":
            self.collapse_button_rect = QRectF()
            draw_lod_card(
                painter,
                QRectF(0, 0, self.width, self.height),
                accent=pycoder_color,
                selection_color=palette.SELECTION,
                title=f"Py-Coder ({'AI' if self.mode == PyCoderMode.AI_DRIVEN else 'Manual'})",
                subtitle="Running" if self.is_running else ("AI-driven execution" if self.mode == PyCoderMode.AI_DRIVEN else "Manual execution"),
                preview=preview_text(self.get_prompt(), self.get_code(), self.output_display.toPlainText(), fallback="Python workflow"),
                badge="PY",
                mode=render_mode,
                selected=self.isSelected(),
                hovered=self.hovered,
                connection_radius=self.CONNECTION_DOT_RADIUS,
            )
            return

        if self.is_collapsed:
            painter.setPen(QColor("#ffffff"))
            font = QFont("Segoe UI", 10, QFont.Weight.Bold)
            painter.setFont(font)
            title = f"Py-Coder ({'AI' if self.mode == PyCoderMode.AI_DRIVEN else 'Manual'})"
            painter.drawText(QRectF(40, 0, self.width - 80, self.height), Qt.AlignmentFlag.AlignVCenter, title)
            
            icon = qta.icon('fa5s.laptop-code', color=pycoder_color.name())
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
        if change == QGraphicsItem.ItemPositionChange and self.scene() and self.scene().is_dragging_item:
            return self.scene().snap_position(self, value)
        if change == QGraphicsItem.ItemPositionHasChanged and self.scene():
            self.scene().nodeMoved(self)
        return super().itemChange(change, value)

    def hoverEnterEvent(self, event):
        self._handle_hover_enter(event)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._handle_hover_leave(event)
        super().hoverLeaveEvent(event)

    def get_prompt(self):
        return self.prompt_input.toPlainText()

    def get_code(self):
        return self.code_input.toPlainText()

    def set_code(self, text):
        self.generated_code_display.setPlainText(text)
        self.code_input.setPlainText(text)

    def set_output(self, text):
        self.output_display.setPlainText(text)
        if text.strip() and self.mode == PyCoderMode.AI_DRIVEN:
            self.tabs.setCurrentIndex(1)

    def set_ai_analysis(self, text):
        html = markdown.markdown(text, extensions=['fenced_code', 'tables'])
        self.ai_analysis_display.setHtml(html)
        if text.strip():
            self.tabs.setCurrentIndex(2 if self.mode == PyCoderMode.AI_DRIVEN else 1)
        
    def update_status(self, stage, status):
        self.status_tracker.update_status(stage, status)

    def reset_statuses(self):
        self.status_tracker.reset_statuses()

    def set_running_state(self, is_running):
        self.is_running = is_running
        self.code_input.setReadOnly(is_running)
        self.prompt_input.setReadOnly(is_running)
        
        stop_style = """
            QPushButton {
                background-color: #e74c3c;
                color: white; border: none; border-radius: 6px;
                padding: 10px; font-weight: bold; font-size: 13px;
            }
            QPushButton:hover { background-color: #c0392b; }
            QPushButton:pressed { background-color: #a93226; }
        """
        
        if is_running:
            self.run_button.setText(" Stop Execution")
            self.generate_button.setText(" Stop Generation")
            self.run_button.setStyleSheet(stop_style)
            self.generate_button.setStyleSheet(stop_style)
            self.generate_button.setIcon(qta.icon('fa5s.stop', color='white'))
            self.run_button.setIcon(qta.icon('fa5s.stop', color='white'))
        else:
            self.run_button.setText(" Run Code")
            self.generate_button.setText(" Generate & Execute")
            self.run_button.setStyleSheet(self._default_btn_style)
            self.generate_button.setStyleSheet(self._default_btn_style)
            self.generate_button.setIcon(qta.icon('fa5s.cogs', color='white'))
            self.run_button.setIcon(qta.icon('fa5s.play', color='white'))
