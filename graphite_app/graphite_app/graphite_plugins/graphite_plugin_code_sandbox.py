import json
import uuid

import markdown
import qtawesome as qta
from PySide6.QtCore import QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsProxyWidget,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from graphite_agents_code_sandbox import SandboxStage
from graphite_agents_pycoder import PyCoderStatus
from graphite_canvas_items import HoverAnimationMixin
from graphite_config import get_current_palette, get_semantic_color
from graphite_connections import ConnectionItem
from graphite_pycoder import CodeEditor, PythonHighlighter, StatusItemWidget


SANDBOX_SCROLLBAR_STYLE = """
    QScrollBar:vertical {
        background: #1b1f23;
        width: 10px;
        margin: 0px;
        border-radius: 5px;
    }
    QScrollBar::handle:vertical {
        background-color: #58616b;
        min-height: 24px;
        border-radius: 5px;
    }
    QScrollBar::handle:vertical:hover {
        background-color: #6f7a86;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0px;
        background: none;
    }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
        background: none;
    }
"""


class SandboxStatusTracker(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.stages = {
            SandboxStage.GENERATE: StatusItemWidget("Generate Code"),
            SandboxStage.PREPARE: StatusItemWidget("Prepare Venv"),
            SandboxStage.INSTALL: StatusItemWidget("Install Dependencies"),
            SandboxStage.EXECUTE: StatusItemWidget("Execute Script"),
            SandboxStage.ANALYZE: StatusItemWidget("Review Output"),
        }

        layout = QGridLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setHorizontalSpacing(14)
        layout.setVerticalSpacing(8)

        layout.addWidget(self.stages[SandboxStage.GENERATE], 0, 0)
        layout.addWidget(self.stages[SandboxStage.PREPARE], 0, 1)
        layout.addWidget(self.stages[SandboxStage.INSTALL], 0, 2)
        layout.addWidget(self.stages[SandboxStage.EXECUTE], 1, 0)
        layout.addWidget(self.stages[SandboxStage.ANALYZE], 1, 1)

        self.setStyleSheet("""
            SandboxStatusTracker {
                background-color: #1d2126;
                border: 1px solid #353b42;
                border-radius: 8px;
            }
            QLabel {
                background: transparent;
            }
        """)

    def update_status(self, stage, status):
        if stage in self.stages:
            self.stages[stage].set_status(status)

    def reset_statuses(self):
        for widget in self.stages.values():
            widget.set_status(PyCoderStatus.PENDING)


class CodeSandboxConnectionItem(ConnectionItem):
    def paint(self, painter, option, widget=None):
        if not (self.start_node and self.end_node):
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = get_current_palette()
        node_color = QColor(palette.FRAME_COLORS["Blue Header"]["color"])
        pen = QPen(node_color, 2, Qt.PenStyle.DashLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)

        if self.hover:
            pen.setWidth(3)

        painter.setPen(pen)
        painter.drawPath(self.path)

        if self.is_animating:
            for arrow in self.arrows:
                self.drawArrow(painter, arrow["pos"], node_color)

    def drawArrow(self, painter, pos, color):
        if pos < 0 or pos > 1:
            return

        point = self.path.pointAtPercent(pos)
        angle = self.path.angleAtPercent(pos)
        arrow = QPainterPath()
        arrow.moveTo(-self.arrow_size, -self.arrow_size / 2)
        arrow.lineTo(0, 0)
        arrow.lineTo(-self.arrow_size, self.arrow_size / 2)

        painter.save()
        painter.translate(point)
        painter.rotate(-angle)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(arrow)
        painter.restore()


class CodeSandboxNode(QGraphicsObject, HoverAnimationMixin):
    sandbox_requested = Signal(object)

    NODE_WIDTH = 620
    NODE_HEIGHT = 790
    COLLAPSED_WIDTH = 280
    COLLAPSED_HEIGHT = 42
    CONNECTION_DOT_RADIUS = 5
    CONNECTION_DOT_OFFSET = 0

    def __init__(self, parent_node, parent=None):
        super().__init__(parent)
        HoverAnimationMixin.__init__(self)
        self.parent_node = parent_node
        self.children = []
        self.conversation_history = []
        self.is_user = False

        self.is_running = False
        self.is_collapsed = False
        self.hovered = False
        self.is_disposed = False
        self.worker_thread = None
        self.last_run_mode = "generate"
        self.status = "Idle"
        self.sandbox_id = uuid.uuid4().hex[:12]
        self.collapse_button_rect = QRectF()

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)

        self.widget = QWidget()
        self.widget.setObjectName("codeSandboxWidget")
        self.widget.setFixedSize(self.NODE_WIDTH, self.NODE_HEIGHT)
        self.widget.setStyleSheet("""
            QWidget#codeSandboxWidget {
                background: transparent;
                color: #e4e7eb;
                font-family: 'Segoe UI', sans-serif;
            }
            QWidget#codeSandboxWidget QLabel {
                background: transparent;
            }
        """)

        self._setup_ui()

        self.proxy = QGraphicsProxyWidget(self)
        self.proxy.setWidget(self.widget)

    def __del__(self):
        self.dispose()

    @property
    def width(self):
        return self.COLLAPSED_WIDTH if self.is_collapsed else self.NODE_WIDTH

    @property
    def height(self):
        return self.COLLAPSED_HEIGHT if self.is_collapsed else self.NODE_HEIGHT

    def _setup_ui(self):
        main_layout = QVBoxLayout(self.widget)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        palette = get_current_palette()
        accent = QColor(palette.FRAME_COLORS["Blue Header"]["color"])

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)

        icon_label = QLabel()
        icon_label.setPixmap(qta.icon("fa5s.shield-alt", color=accent).pixmap(20, 20))
        header_layout.addWidget(icon_label)

        title_column = QVBoxLayout()
        title_column.setContentsMargins(0, 0, 0, 0)
        title_column.setSpacing(2)

        title_label = QLabel("Execution Sandbox")
        title_label.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {accent.name()};")
        title_column.addWidget(title_label)

        subtitle_label = QLabel("Isolated Python runs with per-node requirements and a virtualenv-backed execution lane.")
        subtitle_label.setWordWrap(True)
        subtitle_label.setStyleSheet("font-size: 11px; color: #9aa5b1;")
        title_column.addWidget(subtitle_label)

        header_layout.addLayout(title_column, 1)

        self.env_pill = QLabel("Virtualenv")
        self.env_pill.setStyleSheet(f"""
            QLabel {{
                padding: 4px 10px;
                border-radius: 11px;
                background-color: rgba(52, 152, 219, 0.14);
                border: 1px solid rgba(52, 152, 219, 0.35);
                color: {accent.name()};
                font-size: 11px;
                font-weight: 600;
            }}
        """)
        header_layout.addWidget(self.env_pill)

        self.status_pill = QLabel("Idle")
        header_layout.addWidget(self.status_pill)
        main_layout.addLayout(header_layout)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("background-color: #353b42; border: none; height: 1px;")
        main_layout.addWidget(divider)

        briefing_layout = QHBoxLayout()
        briefing_layout.setContentsMargins(0, 0, 0, 0)
        briefing_layout.setSpacing(12)

        prompt_card = self._create_surface_card()
        prompt_layout = QVBoxLayout(prompt_card)
        prompt_layout.setContentsMargins(14, 14, 14, 14)
        prompt_layout.setSpacing(8)

        prompt_header = QLabel("Task Brief")
        prompt_header.setStyleSheet("font-size: 12px; font-weight: 700; color: #ffffff;")
        prompt_layout.addWidget(prompt_header)

        prompt_hint = QLabel("Describe what to build, test, analyze, or transform. The sandbox agent will generate code against the branch context.")
        prompt_hint.setWordWrap(True)
        prompt_hint.setStyleSheet("font-size: 11px; color: #9aa5b1;")
        prompt_layout.addWidget(prompt_hint)

        self.prompt_input = QTextEdit()
        self.prompt_input.setPlaceholderText("Example: Load a CSV with pandas, compute weekly retention deltas, and print the strongest anomaly.")
        self.prompt_input.setFixedHeight(132)
        self.prompt_input.setStyleSheet(f"""
            QTextEdit {{
                background-color: #15181c;
                border: 1px solid #343a40;
                border-radius: 8px;
                padding: 10px;
                color: #f4f6f8;
                font-size: 12px;
            }}
            QTextEdit:focus {{
                border: 1px solid {accent.name()};
            }}
            {SANDBOX_SCROLLBAR_STYLE}
        """)
        prompt_layout.addWidget(self.prompt_input)
        briefing_layout.addWidget(prompt_card, 3)

        deps_card = self._create_surface_card()
        deps_layout = QVBoxLayout(deps_card)
        deps_layout.setContentsMargins(14, 14, 14, 14)
        deps_layout.setSpacing(8)

        deps_header_layout = QHBoxLayout()
        deps_header_layout.setContentsMargins(0, 0, 0, 0)
        deps_header_layout.setSpacing(8)

        deps_header = QLabel("requirements.txt")
        deps_header.setStyleSheet("font-size: 12px; font-weight: 700; color: #ffffff;")
        deps_header_layout.addWidget(deps_header)
        deps_header_layout.addStretch()

        self.dependency_pill = QLabel("0 libs")
        deps_header_layout.addWidget(self.dependency_pill)
        deps_layout.addLayout(deps_header_layout)

        deps_hint = QLabel("One dependency per line. The sandbox automatically rebuilds the environment when this manifest changes.")
        deps_hint.setWordWrap(True)
        deps_hint.setStyleSheet("font-size: 11px; color: #9aa5b1;")
        deps_layout.addWidget(deps_hint)

        self.requirements_input = QPlainTextEdit()
        self.requirements_input.setPlaceholderText("pandas\nnumpy\nmatplotlib")
        self.requirements_input.setFixedHeight(132)
        self.requirements_input.textChanged.connect(self._update_dependency_pill)
        self.requirements_input.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: #15181c;
                border: 1px solid #343a40;
                border-radius: 8px;
                padding: 10px;
                color: #d4e2f0;
                font-family: Consolas, Monaco, monospace;
                font-size: 12px;
            }}
            QPlainTextEdit:focus {{
                border: 1px solid {accent.name()};
            }}
            {SANDBOX_SCROLLBAR_STYLE}
        """)
        deps_layout.addWidget(self.requirements_input)
        briefing_layout.addWidget(deps_card, 2)

        main_layout.addLayout(briefing_layout)

        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(10)

        self.generate_button = QPushButton("Generate & Run")
        self.generate_button.clicked.connect(self._request_generate_run)
        self.run_button = QPushButton("Run Current Code")
        self.run_button.clicked.connect(self._request_manual_run)

        self._default_primary_style = f"""
            QPushButton {{
                background-color: {accent.name()};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 10px 14px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                background-color: {accent.lighter(110).name()};
            }}
            QPushButton:pressed {{
                background-color: {accent.darker(115).name()};
            }}
            QPushButton:disabled {{
                background-color: #414851;
                color: #7d8792;
            }}
        """
        self._default_secondary_style = f"""
            QPushButton {{
                background-color: #20262c;
                color: #dbe4ec;
                border: 1px solid #39414a;
                border-radius: 8px;
                padding: 10px 14px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                border: 1px solid {accent.name()};
                color: #ffffff;
            }}
            QPushButton:pressed {{
                background-color: #171c21;
            }}
            QPushButton:disabled {{
                background-color: #1f2429;
                color: #6f7780;
                border: 1px solid #32383f;
            }}
        """
        self.generate_button.setStyleSheet(self._default_primary_style)
        self.run_button.setStyleSheet(self._default_secondary_style)
        self.generate_button.setIcon(qta.icon("fa5s.magic", color="white"))
        self.run_button.setIcon(qta.icon("fa5s.play", color="#dbe4ec"))

        action_layout.addWidget(self.generate_button)
        action_layout.addWidget(self.run_button)

        action_hint = QLabel("Use the prompt for agent-driven generation, or run the current script directly with the declared dependencies.")
        action_hint.setWordWrap(True)
        action_hint.setStyleSheet("font-size: 11px; color: #8e99a5;")
        action_layout.addWidget(action_hint, 1)
        main_layout.addLayout(action_layout)

        self.status_tracker = SandboxStatusTracker()
        main_layout.addWidget(self.status_tracker)

        code_card = self._create_surface_card()
        code_layout = QVBoxLayout(code_card)
        code_layout.setContentsMargins(14, 14, 14, 14)
        code_layout.setSpacing(8)

        code_header_layout = QHBoxLayout()
        code_header_layout.setContentsMargins(0, 0, 0, 0)
        code_header_layout.setSpacing(8)

        code_header = QLabel("Sandbox Script")
        code_header.setStyleSheet("font-size: 12px; font-weight: 700; color: #ffffff;")
        code_header_layout.addWidget(code_header)
        code_header_layout.addStretch()

        code_hint = QLabel("Editable execution file")
        code_hint.setStyleSheet("font-size: 11px; color: #8e99a5;")
        code_header_layout.addWidget(code_hint)
        code_layout.addLayout(code_header_layout)

        self.code_input = CodeEditor()
        self.code_input.setPlaceholderText("Generated or handwritten Python will appear here.")
        self.code_input.setFixedHeight(250)
        self.code_input.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: #111418;
                border: 1px solid #343a40;
                border-radius: 8px;
                padding: 8px;
                color: #dce6f2;
                font-family: Consolas, Monaco, monospace;
                font-size: 12px;
            }}
            QPlainTextEdit:focus {{
                border: 1px solid {accent.name()};
            }}
            {SANDBOX_SCROLLBAR_STYLE}
        """)
        code_layout.addWidget(self.code_input)
        main_layout.addWidget(code_card)

        self.results_tabs = QTabWidget()
        self.results_tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 1px solid #353b42;
                background: #171b20;
                border-radius: 8px;
            }}
            QTabBar::tab {{
                background: #1e242a;
                color: #9aa5b1;
                padding: 8px 14px;
                border: 1px solid #353b42;
                border-bottom: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                margin-right: 2px;
                font-weight: 700;
            }}
            QTabBar::tab:selected {{
                background: #171b20;
                color: #ffffff;
                border-top: 2px solid {accent.name()};
            }}
            QTabBar::tab:hover:!selected {{
                background: #242b32;
                color: #ffffff;
            }}
        """)

        self.output_display = QTextEdit()
        self.output_display.setReadOnly(True)
        self.output_display.setPlaceholderText("Virtualenv setup, dependency install logs, and execution output will appear here...")
        self.output_display.setStyleSheet(f"""
            QTextEdit {{
                background-color: #0f1317;
                color: #97dcae;
                border: none;
                padding: 12px;
                font-family: Consolas, Monaco, monospace;
                font-size: 11px;
            }}
            {SANDBOX_SCROLLBAR_STYLE}
        """)
        self.results_tabs.addTab(self.output_display, qta.icon("fa5s.terminal", color="#c9d3dd"), "Terminal")

        self.ai_analysis_display = QTextEdit()
        self.ai_analysis_display.setReadOnly(True)
        self.ai_analysis_display.setPlaceholderText("Execution review, debugging notes, and outcome analysis will appear here...")
        self.ai_analysis_display.setStyleSheet(f"""
            QTextEdit {{
                background-color: transparent;
                border: none;
                padding: 12px;
                font-size: 12px;
                color: #e7edf4;
            }}
            {SANDBOX_SCROLLBAR_STYLE}
        """)
        self.ai_analysis_display.document().setDefaultStyleSheet("""
            p, ul, ol, li { color: #e7edf4; font-family: 'Segoe UI', sans-serif; font-size: 13px; line-height: 1.5; }
            h1, h2, h3, h4 { color: #ffffff; font-weight: bold; margin-bottom: 5px; }
            pre { background-color: #222a31; padding: 10px; border-radius: 6px; font-family: Consolas, monospace; color: #d6e3f0; }
            code { background-color: #2d3640; color: #d6e3f0; padding: 2px 4px; border-radius: 4px; font-family: Consolas, monospace; }
            blockquote { border-left: 3px solid #4d6a87; padding-left: 10px; color: #aab7c4; }
        """)
        self.results_tabs.addTab(self.ai_analysis_display, qta.icon("fa5s.search", color="#c9d3dd"), "Review")
        main_layout.addWidget(self.results_tabs, 1)

        self.highlighter_code = PythonHighlighter(self.code_input.document())
        self._update_dependency_pill()
        self._update_status_pill()

    def _create_surface_card(self):
        card = QWidget()
        card.setObjectName("sandboxSurfaceCard")
        card.setStyleSheet("""
            QWidget#sandboxSurfaceCard {
                background-color: #1a1f24;
                border: 1px solid #353b42;
                border-radius: 10px;
            }
        """)
        return card

    def _count_dependencies(self):
        manifest = self.get_requirements()
        if not manifest:
            return 0
        return len([line for line in manifest.splitlines() if line.strip() and not line.strip().startswith("#")])

    def _update_dependency_pill(self):
        count = self._count_dependencies()
        noun = "lib" if count == 1 else "libs"
        palette = get_current_palette()
        accent = QColor(palette.FRAME_COLORS["Blue Header"]["color"])
        self.dependency_pill.setText(f"{count} {noun}")
        self.dependency_pill.setStyleSheet(f"""
            QLabel {{
                padding: 3px 9px;
                border-radius: 10px;
                background-color: rgba(52, 152, 219, 0.12);
                border: 1px solid rgba(52, 152, 219, 0.32);
                color: {accent.name()};
                font-size: 11px;
                font-weight: 700;
            }}
        """)

    def _update_status_pill(self, tone="info"):
        tone_color = {
            "info": get_semantic_color("status_info"),
            "success": get_semantic_color("status_success"),
            "error": get_semantic_color("status_error"),
            "warning": get_semantic_color("status_warning"),
        }.get(tone, get_semantic_color("status_info"))
        red = tone_color.red()
        green = tone_color.green()
        blue = tone_color.blue()
        self.status_pill.setText(self.status)
        self.status_pill.setStyleSheet(f"""
            QLabel {{
                padding: 4px 10px;
                border-radius: 11px;
                background-color: rgba({red}, {green}, {blue}, 0.14);
                border: 1px solid rgba({red}, {green}, {blue}, 0.32);
                color: {tone_color.name()};
                font-size: 11px;
                font-weight: 700;
            }}
        """)

    def _request_generate_run(self):
        self.last_run_mode = "generate"
        self.sandbox_requested.emit(self)

    def _request_manual_run(self):
        self.last_run_mode = "manual"
        self.sandbox_requested.emit(self)

    def boundingRect(self):
        padding = self.CONNECTION_DOT_OFFSET + self.CONNECTION_DOT_RADIUS
        return QRectF(-padding, 0, self.width + (padding * 2), self.height)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = get_current_palette()
        accent = QColor(palette.FRAME_COLORS["Blue Header"]["color"])

        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width, self.height, 12, 12)
        painter.setBrush(QColor("#2b2b2b"))

        pen = QPen(accent, 1.6)
        if self.isSelected():
            pen = QPen(palette.SELECTION, 2.2)
        elif self.hovered:
            pen = QPen(QColor("#ffffff"), 2.0)

        painter.setPen(pen)
        painter.drawPath(path)

        dot_color = accent
        if self.isSelected() or self.hovered:
            dot_color = pen.color().lighter(108)
        painter.setBrush(dot_color)
        painter.setPen(Qt.PenStyle.NoPen)

        left_dot = QRectF(
            -self.CONNECTION_DOT_RADIUS,
            (self.height / 2) - self.CONNECTION_DOT_RADIUS,
            self.CONNECTION_DOT_RADIUS * 2,
            self.CONNECTION_DOT_RADIUS * 2,
        )
        right_dot = QRectF(
            self.width - self.CONNECTION_DOT_RADIUS,
            (self.height / 2) - self.CONNECTION_DOT_RADIUS,
            self.CONNECTION_DOT_RADIUS * 2,
            self.CONNECTION_DOT_RADIUS * 2,
        )
        painter.drawPie(left_dot, 90 * 16, -180 * 16)
        painter.drawPie(right_dot, 90 * 16, 180 * 16)

        if self.is_collapsed:
            painter.setPen(QColor("#ffffff"))
            painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
            painter.drawText(QRectF(42, 0, self.width - 84, self.height), Qt.AlignmentFlag.AlignVCenter, "Execution Sandbox")
            qta.icon("fa5s.shield-alt", color=accent.name()).paint(painter, QRect(12, 11, 18, 18))
            self.collapse_button_rect = QRectF(self.width - 34, 6, 28, 28)
            expand_icon = qta.icon(
                "fa5s.expand-arrows-alt",
                color="#ffffff" if self.hovered else "#8d98a5",
            )
            expand_icon.paint(painter, QRect(int(self.width - 30), 10, 18, 18))
            return

        if self.hovered:
            self.collapse_button_rect = QRectF(self.width - 34, 6, 28, 28)
            painter.setBrush(QColor(255, 255, 255, 28))
            painter.setPen(QColor(255, 255, 255, 120))
            painter.drawRoundedRect(self.collapse_button_rect.adjusted(5, 5, -5, -5), 4, 4)
            center = self.collapse_button_rect.center()
            painter.setPen(QPen(QColor("#ffffff"), 2))
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
            if hasattr(self.scene(), "window"):
                self.scene().window.setCurrentNode(self)

        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self.scene():
            self.scene().is_dragging_item = False
            self.scene()._clear_smart_guides()
        super().mouseReleaseEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene() and self.scene().is_dragging_item:
            return self.scene().snap_position(self, value)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged and self.scene():
            self.scene().nodeMoved(self)
        return super().itemChange(change, value)

    def hoverEnterEvent(self, event):
        self._handle_hover_enter(event)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._handle_hover_leave(event)
        super().hoverLeaveEvent(event)

    def set_collapsed(self, collapsed):
        if self.is_collapsed == collapsed:
            return
        self.is_collapsed = collapsed
        self.proxy.setVisible(not collapsed)
        self.prepareGeometryChange()
        if self.scene():
            self.scene().update_connections()
            self.scene().nodeMoved(self)
        self.update()

    def toggle_collapse(self):
        self.set_collapsed(not self.is_collapsed)

    def dispose(self):
        if self.is_disposed:
            return
        self.is_disposed = True
        worker = getattr(self, "worker_thread", None)
        if worker and worker.isRunning():
            worker.stop()
        self.worker_thread = None

    def get_prompt(self):
        return self.prompt_input.toPlainText()

    def get_requirements(self):
        return self.requirements_input.toPlainText().strip()

    def get_code(self):
        return self.code_input.toPlainText()

    def set_requirements(self, text):
        self.requirements_input.setPlainText(text or "")
        self._update_dependency_pill()

    def set_code(self, text):
        self.code_input.setPlainText(text or "")

    def clear_terminal_output(self):
        self.output_display.clear()

    def append_terminal_output(self, text):
        if not text:
            return
        cursor = self.output_display.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.output_display.setTextCursor(cursor)
        self.output_display.insertPlainText(text)
        self.output_display.ensureCursorVisible()
        self.results_tabs.setCurrentIndex(0)

    def set_output(self, text):
        self.output_display.setPlainText(text or "")
        if text and text.strip():
            self.results_tabs.setCurrentIndex(0)

    def set_ai_analysis(self, text):
        html = markdown.markdown(text or "", extensions=["fenced_code", "tables"])
        self.ai_analysis_display.setHtml(html)
        if text and text.strip():
            self.results_tabs.setCurrentIndex(1)

    def update_status(self, stage, status):
        self.status_tracker.update_status(stage, status)
        if status == PyCoderStatus.RUNNING:
            stage_map = {
                SandboxStage.GENERATE: "Generating",
                SandboxStage.PREPARE: "Preparing",
                SandboxStage.INSTALL: "Installing",
                SandboxStage.EXECUTE: "Executing",
                SandboxStage.ANALYZE: "Reviewing",
            }
            self.status = stage_map.get(stage, "Running")
            self._update_status_pill("info")
        elif status == PyCoderStatus.FAILURE:
            self.status = "Needs attention"
            self._update_status_pill("error")

    def reset_statuses(self):
        self.status_tracker.reset_statuses()
        self.status = "Idle"
        self._update_status_pill("info")

    def set_running_state(self, is_running):
        self.is_running = is_running
        self.prompt_input.setReadOnly(is_running)
        self.requirements_input.setReadOnly(is_running)
        self.code_input.setReadOnly(is_running)

        stop_style = """
            QPushButton {
                background-color: #b84d4d;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 10px 14px;
                font-size: 12px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #cc5a5a;
            }
            QPushButton:pressed {
                background-color: #9f3f3f;
            }
        """

        if is_running:
            self.status = "Running"
            self._update_status_pill("info")
            self.generate_button.setText("Stop Sandbox")
            self.run_button.setText("Stop Sandbox")
            self.generate_button.setStyleSheet(stop_style)
            self.run_button.setStyleSheet(stop_style)
            self.generate_button.setIcon(qta.icon("fa5s.stop", color="white"))
            self.run_button.setIcon(qta.icon("fa5s.stop", color="white"))
        else:
            if self.status == "Running":
                self.status = "Ready"
            self.generate_button.setText("Generate & Run")
            self.run_button.setText("Run Current Code")
            self.generate_button.setStyleSheet(self._default_primary_style)
            self.run_button.setStyleSheet(self._default_secondary_style)
            self.generate_button.setIcon(qta.icon("fa5s.magic", color="white"))
            self.run_button.setIcon(qta.icon("fa5s.play", color="#dbe4ec"))

            tone = "success" if self.status == "Ready" else "info"
            self._update_status_pill(tone)

    def set_error(self, error_text):
        self.status = "Error"
        self._update_status_pill("error")
        self.set_ai_analysis(f"An error occurred:\n\n{error_text}")
