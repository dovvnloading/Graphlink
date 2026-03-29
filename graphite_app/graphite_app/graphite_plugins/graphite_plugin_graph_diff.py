import difflib
import json
import re

from PySide6.QtCore import QRect, QRectF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsObject,
    QGraphicsProxyWidget,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
import qtawesome as qta

import api_provider
import graphite_config as config
from graphite_canvas_items import HoverAnimationMixin
from graphite_connections import ConnectionItem
from graphite_config import get_current_palette, get_semantic_color


GRAPH_DIFF_SCROLLBAR_STYLE = """
    QScrollBar:vertical {
        background: #252526;
        width: 10px;
        margin: 0px;
        border-radius: 5px;
    }
    QScrollBar::handle:vertical {
        background-color: #555555;
        min-height: 25px;
        border-radius: 5px;
    }
    QScrollBar::handle:vertical:hover {
        background-color: #6a6a6a;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0px;
        background: none;
    }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
        background: none;
    }
    QScrollBar:horizontal {
        background: #252526;
        height: 10px;
        margin: 0px;
        border-radius: 5px;
    }
    QScrollBar::handle:horizontal {
        background-color: #555555;
        min-width: 25px;
        border-radius: 5px;
    }
    QScrollBar::handle:horizontal:hover {
        background-color: #6a6a6a;
    }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
        width: 0px;
        background: none;
    }
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
        background: none;
    }
"""


def _flatten_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(part for part in parts if part)
    return str(content)


def _clean_text(text, limit=2500):
    text = re.sub(r"\n{3,}", "\n\n", text or "").strip()
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _node_label(node):
    name_map = {
        "ChatNode": "Chat Node",
        "PyCoderNode": "Py-Coder",
        "CodeSandboxNode": "Execution Sandbox",
        "WebNode": "Graphlink-Web",
        "ConversationNode": "Conversation Node",
        "ReasoningNode": "Graphlink-Reasoning",
        "HtmlViewNode": "HTML Renderer",
        "ArtifactNode": "Artifact / Drafter",
        "WorkflowNode": "Workflow Architect",
        "QualityGateNode": "Quality Gate",
        "GitlinkNode": "Gitlink",
    }
    return name_map.get(node.__class__.__name__, node.__class__.__name__)


def _extract_node_text(node):
    parts = []

    if hasattr(node, "text") and isinstance(getattr(node, "text"), str):
        parts.append(node.text)

    if hasattr(node, "conversation_history"):
        for message in getattr(node, "conversation_history", [])[-6:]:
            role = message.get("role", "unknown").title()
            content = _flatten_content(message.get("content", ""))
            if content.strip():
                parts.append(f"{role}: {content.strip()}")

    for attr in ("prompt", "thinking_text", "thought_process", "blueprint_markdown", "review_markdown", "html_content"):
        value = getattr(node, attr, "")
        if isinstance(value, str) and value.strip():
            parts.append(value)

    for getter_name, prefix in (("get_goal", "Goal"), ("get_constraints", "Constraints"), ("get_criteria", "Acceptance Criteria")):
        getter = getattr(node, getter_name, None)
        if callable(getter):
            try:
                value = getter().strip()
                if value:
                    parts.append(f"{prefix}: {value}")
            except Exception:
                pass

    if hasattr(node, "query_input"):
        query = node.query_input.text().strip()
        if query:
            parts.append(f"Query: {query}")

    if hasattr(node, "summary_text") and isinstance(getattr(node, "summary_text"), str):
        if node.summary_text.strip():
            parts.append(node.summary_text)

    if hasattr(node, "prompt_input"):
        try:
            prompt_text = node.prompt_input.toPlainText().strip()
            if prompt_text:
                parts.append(prompt_text)
        except Exception:
            pass

    if hasattr(node, "get_requirements"):
        try:
            requirements_text = node.get_requirements().strip()
            if requirements_text:
                parts.append(f"Requirements:\n{requirements_text}")
        except Exception:
            pass

    if hasattr(node, "get_code"):
        try:
            code_text = node.get_code().strip()
            if code_text:
                parts.append(code_text)
        except Exception:
            pass

    for widget_name in ("output_display", "ai_analysis_display"):
        widget = getattr(node, widget_name, None)
        try:
            text = widget.toPlainText().strip() if widget else ""
            if text:
                parts.append(text)
        except Exception:
            pass

    if hasattr(node, "instruction_input"):
        try:
            instruction = node.instruction_input.toPlainText().strip()
            if instruction:
                parts.append(instruction)
        except Exception:
            pass

    if hasattr(node, "get_artifact_content"):
        try:
            artifact_text = node.get_artifact_content().strip()
            if artifact_text:
                parts.append(artifact_text)
        except Exception:
            pass

    unique_parts = []
    seen = set()
    for part in parts:
        cleaned = _clean_text(part, limit=1200)
        if cleaned and cleaned not in seen:
            unique_parts.append(cleaned)
            seen.add(cleaned)
    return "\n\n".join(unique_parts)


def _collect_branch_nodes(node):
    lineage = []
    seen = set()
    cursor = node
    while cursor and id(cursor) not in seen:
        lineage.append(cursor)
        seen.add(id(cursor))
        cursor = getattr(cursor, "parent_node", None)
    lineage.reverse()
    return lineage


def build_branch_payload(node, branch_name):
    lineage = _collect_branch_nodes(node)
    sections = []
    for index, branch_node in enumerate(lineage, start=1):
        content = _extract_node_text(branch_node)
        if not content:
            continue
        sections.append(f"Step {index}: {_node_label(branch_node)}\n{content}")

    transcript = "\n\n---\n\n".join(sections) if sections else "No branch transcript available."
    label = f"{branch_name}: {_node_label(node)}"
    preview = "\n\n".join(sections[-3:]) if sections else transcript
    return {
        "label": label,
        "depth": len(lineage),
        "transcript": transcript,
        "preview": _clean_text(preview, limit=2800),
    }


class GraphDiffAnalyzer:
    SYSTEM_PROMPT = """
You are Graphlink's Branch Lens.
Compare two distinct graph branches and explain how they diverge in logic, code direction, and intent.

Rules:
1. Be concrete and specific.
2. Focus on divergence, not generic summaries.
3. If one branch is stronger, say so and explain why.
4. Output valid JSON only. No markdown fences.

Return exactly:
{
  "overview": "2-4 sentence summary of how the branches differ",
  "branch_a_focus": "What branch A is optimizing for",
  "branch_b_focus": "What branch B is optimizing for",
  "logic_differences": ["..."],
  "code_differences": ["..."],
  "intent_differences": ["..."],
  "recommended_branch": "branch_a|branch_b|tie",
  "recommendation_reason": "Why that branch is currently stronger",
  "note_summary": "A concise note-ready summary of the divergence"
}
"""

    def _clean_json_response(self, raw_text):
        block_match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", raw_text, re.IGNORECASE)
        if block_match:
            return block_match.group(1).strip()
        json_match = re.search(r"(\{[\s\S]*\})", raw_text)
        if json_match:
            return json_match.group(1).strip()
        return raw_text.strip()

    def _fallback_result(self, left_payload, right_payload):
        left_text = left_payload["transcript"]
        right_text = right_payload["transcript"]
        similarity = difflib.SequenceMatcher(None, left_text, right_text).ratio()
        left_lines = [line for line in left_text.splitlines() if line.strip()]
        right_lines = [line for line in right_text.splitlines() if line.strip()]
        diff_lines = list(difflib.unified_diff(left_lines, right_lines, lineterm=""))
        trimmed_diff = "\n".join(diff_lines[:20]) if diff_lines else "The branches are structurally similar but vary in emphasis."

        logic_differences = [
            f"{left_payload['label']} depth: {left_payload['depth']} step(s)",
            f"{right_payload['label']} depth: {right_payload['depth']} step(s)",
            f"Similarity score: {similarity:.2f}",
        ]
        code_differences = [
            "Branch A appears more code-oriented." if re.search(r"code|python|function|class|html|script", left_text, re.IGNORECASE) else "Branch A is less code-heavy.",
            "Branch B appears more code-oriented." if re.search(r"code|python|function|class|html|script", right_text, re.IGNORECASE) else "Branch B is less code-heavy.",
        ]
        intent_differences = [
            "Branch A leans more toward execution and implementation." if left_payload["depth"] >= right_payload["depth"] else "Branch A stays comparatively tighter in scope.",
            "Branch B leans more toward exploration and variation." if right_payload["depth"] >= left_payload["depth"] else "Branch B stays comparatively tighter in scope.",
        ]

        recommended_branch = "branch_a" if left_payload["depth"] >= right_payload["depth"] else "branch_b"
        recommendation_reason = "The branch with more concrete downstream steps currently offers a clearer execution path."

        comparison_markdown = "\n".join([
            "# Graph Diff",
            "",
            "## Overview",
            f"These branches are related but diverge in emphasis and downstream execution. Their textual similarity is approximately **{similarity:.2f}**, which suggests the paths share some context but have meaningfully different branch details.",
            "",
            "## Branch Focus",
            f"- **Branch A:** {left_payload['label']}",
            f"- **Branch B:** {right_payload['label']}",
            "",
            "## Logic Differences",
            *[f"- {item}" for item in logic_differences],
            "",
            "## Code Differences",
            *[f"- {item}" for item in code_differences],
            "",
            "## Intent Differences",
            *[f"- {item}" for item in intent_differences],
            "",
            "## Raw Delta Snapshot",
            "```diff",
            trimmed_diff or "No line-level divergence detected.",
            "```",
            "",
            "## Recommendation",
            f"**{recommended_branch.replace('_', ' ').title()}** currently looks stronger because {recommendation_reason}",
        ])

        note_summary = "\n".join([
            "# Graph Diff Summary",
            "",
            f"- Branch A: {left_payload['label']}",
            f"- Branch B: {right_payload['label']}",
            f"- Key divergence: Similarity score {similarity:.2f} with different branch depth and emphasis.",
            f"- Recommendation: {recommended_branch.replace('_', ' ').title()} is currently stronger.",
        ])

        return {
            "comparison_markdown": comparison_markdown,
            "note_summary": note_summary,
        }

    def get_response(self, left_payload, right_payload):
        user_prompt = f"""
Branch A Label:
{left_payload['label']}

Branch A Transcript:
{left_payload['transcript']}

Branch B Label:
{right_payload['label']}

Branch B Transcript:
{right_payload['transcript']}
"""
        try:
            response = api_provider.chat(
                task=config.TASK_CHAT,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )
            raw_text = response["message"]["content"]
            parsed = json.loads(self._clean_json_response(raw_text))
            logic = [str(item).strip() for item in parsed.get("logic_differences", []) if str(item).strip()]
            code = [str(item).strip() for item in parsed.get("code_differences", []) if str(item).strip()]
            intent = [str(item).strip() for item in parsed.get("intent_differences", []) if str(item).strip()]
            recommended = str(parsed.get("recommended_branch", "tie")).strip()
            reason = str(parsed.get("recommendation_reason", "")).strip()

            comparison_markdown = "\n".join([
                "# Graph Diff",
                "",
                "## Overview",
                str(parsed.get("overview", "")).strip() or "The two branches diverge in meaningful ways.",
                "",
                "## Branch Focus",
                f"- **Branch A:** {str(parsed.get('branch_a_focus', '')).strip() or left_payload['label']}",
                f"- **Branch B:** {str(parsed.get('branch_b_focus', '')).strip() or right_payload['label']}",
                "",
                "## Logic Differences",
                *([f"- {item}" for item in logic] or ["- No major logic divergence detected."]),
                "",
                "## Code Differences",
                *([f"- {item}" for item in code] or ["- No major code divergence detected."]),
                "",
                "## Intent Differences",
                *([f"- {item}" for item in intent] or ["- No major intent divergence detected."]),
                "",
                "## Recommendation",
                f"**{recommended.replace('_', ' ').title()}**",
                reason or "Neither branch clearly dominates yet.",
            ])

            note_summary = str(parsed.get("note_summary", "")).strip() or comparison_markdown
            return {
                "comparison_markdown": comparison_markdown,
                "note_summary": note_summary,
            }
        except Exception:
            return self._fallback_result(left_payload, right_payload)


class GraphDiffWorkerThread(QThread):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, left_payload, right_payload):
        super().__init__()
        self.left_payload = left_payload
        self.right_payload = right_payload
        self.agent = GraphDiffAnalyzer()
        self._is_running = True

    def run(self):
        try:
            if not self._is_running:
                return
            result = self.agent.get_response(self.left_payload, self.right_payload)
            if self._is_running:
                self.finished.emit(result)
        except Exception as exc:
            if self._is_running:
                self.error.emit(str(exc))
        finally:
            self._is_running = False

    def stop(self):
        self._is_running = False


class GraphDiffConnectionItem(ConnectionItem):
    def paint(self, painter, option, widget=None):
        if not (self.start_node and self.end_node):
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        node_color = get_semantic_color("status_warning")
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


class GraphDiffNode(QGraphicsObject, HoverAnimationMixin):
    compare_requested = Signal(object)
    note_requested = Signal(object)

    NODE_WIDTH = 920
    NODE_HEIGHT = 720
    COLLAPSED_WIDTH = 280
    COLLAPSED_HEIGHT = 40
    CONNECTION_DOT_RADIUS = 5
    CONNECTION_DOT_OFFSET = 0

    def __init__(self, left_source_node, right_source_node, parent=None):
        super().__init__(parent)
        HoverAnimationMixin.__init__(self)
        self.left_source_node = left_source_node
        self.right_source_node = right_source_node
        self.parent_node = None
        self.children = []
        self.status = "Idle"
        self.comparison_markdown = ""
        self.note_summary = ""
        self.left_branch_payload = build_branch_payload(left_source_node, "Branch A")
        self.right_branch_payload = build_branch_payload(right_source_node, "Branch B")
        self.is_search_match = False
        self.is_collapsed = False
        self.is_disposed = False
        self.worker_thread = None
        self.collapse_button_rect = QRectF()

        self.setFlag(QGraphicsObject.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsObject.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsObject.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.hovered = False

        self.widget = QWidget()
        self.widget.setObjectName("graphDiffMainWidget")
        self.widget.setFixedSize(self.NODE_WIDTH, self.NODE_HEIGHT)

        self._setup_ui()

        self.proxy = QGraphicsProxyWidget(self)
        self.proxy.setWidget(self.widget)

    @property
    def width(self):
        return self.COLLAPSED_WIDTH if self.is_collapsed else self.NODE_WIDTH

    @property
    def height(self):
        return self.COLLAPSED_HEIGHT if self.is_collapsed else self.NODE_HEIGHT

    def set_collapsed(self, collapsed):
        if self.is_collapsed != collapsed:
            self.is_collapsed = collapsed
            self.proxy.setVisible(not self.is_collapsed)
            self.prepareGeometryChange()
            if self.scene():
                self.scene().update_connections()
                self.scene().nodeMoved(self)
            self.update()

    def toggle_collapse(self):
        self.set_collapsed(not self.is_collapsed)

    def _setup_ui(self):
        palette = get_current_palette()
        node_color = QColor(palette.FRAME_COLORS["Orange"]["color"])
        brightness = (node_color.red() * 299 + node_color.green() * 587 + node_color.blue() * 114) / 1000
        button_text_color = "black" if brightness > 128 else "white"

        self.widget.setStyleSheet(f"""
            QWidget#graphDiffMainWidget {{
                background-color: transparent;
                color: #e0e0e0;
                font-family: 'Segoe UI', sans-serif;
            }}
            QWidget#graphDiffMainWidget QLabel {{
                background-color: transparent;
            }}
            QFrame#graphDiffBranchCard, QFrame#graphDiffSummaryCard {{
                background-color: #1f2328;
                border: 1px solid #343a41;
                border-radius: 8px;
            }}
            QLabel#graphDiffSectionTitle {{
                color: #ffffff;
                font-size: 12px;
                font-weight: bold;
            }}
            QLabel#graphDiffBadge {{
                color: {node_color.name()};
                background-color: rgba(243, 156, 18, 0.12);
                border: 1px solid rgba(243, 156, 18, 0.28);
                border-radius: 10px;
                padding: 3px 8px;
                font-size: 11px;
                font-weight: bold;
            }}
            QTextEdit {{
                background-color: #15181b;
                border: 1px solid #2f353d;
                color: #d7dce2;
                border-radius: 7px;
                padding: 8px;
                selection-background-color: #264f78;
            }}
            QTextEdit:focus {{
                border: 1px solid {node_color.name()};
            }}
        """)

        main_layout = QVBoxLayout(self.widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)

        header_layout = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(qta.icon("fa5s.code-branch", color=node_color).pixmap(18, 18))
        header_layout.addWidget(icon)

        title_label = QLabel("Branch Lens")
        title_label.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {node_color.name()}; background: transparent;")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: #3f3f3f; border: none; height: 1px;")
        main_layout.addWidget(line)

        source_bar = QHBoxLayout()
        source_bar.setSpacing(8)
        self.left_branch_label = QLabel(self.left_branch_payload["label"])
        self.left_branch_label.setObjectName("graphDiffBadge")
        source_bar.addWidget(self.left_branch_label)
        self.right_branch_label = QLabel(self.right_branch_payload["label"])
        self.right_branch_label.setObjectName("graphDiffBadge")
        source_bar.addWidget(self.right_branch_label)
        source_bar.addStretch()

        self.status_label = QLabel("Idle")
        self.status_label.setObjectName("graphDiffBadge")
        source_bar.addWidget(self.status_label)
        main_layout.addLayout(source_bar)

        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(8)
        self.compare_button = QPushButton("Compare Branches")
        self.compare_button.setIcon(qta.icon("fa5s.exchange-alt", color=button_text_color))
        self.compare_button.clicked.connect(lambda: self.compare_requested.emit(self))
        controls_layout.addWidget(self.compare_button)

        self.note_button = QPushButton("Create Summary Note")
        self.note_button.setIcon(qta.icon("fa5s.sticky-note", color=button_text_color))
        self.note_button.clicked.connect(lambda: self.note_requested.emit(self))
        self.note_button.setEnabled(False)
        controls_layout.addWidget(self.note_button)
        controls_layout.addStretch()
        main_layout.addLayout(controls_layout)

        branch_compare_widget = QWidget()
        branch_compare_layout = QHBoxLayout(branch_compare_widget)
        branch_compare_layout.setContentsMargins(0, 0, 0, 0)
        branch_compare_layout.setSpacing(10)

        left_card = QFrame()
        left_card.setObjectName("graphDiffBranchCard")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(12, 10, 12, 10)
        left_layout.setSpacing(6)
        left_title = QLabel("Branch A Snapshot")
        left_title.setObjectName("graphDiffSectionTitle")
        left_layout.addWidget(left_title)
        self.left_preview = QTextEdit()
        self.left_preview.setReadOnly(True)
        self.left_preview.setStyleSheet("QTextEdit { font-size: 12px; }" + GRAPH_DIFF_SCROLLBAR_STYLE)
        left_layout.addWidget(self.left_preview)

        right_card = QFrame()
        right_card.setObjectName("graphDiffBranchCard")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(12, 10, 12, 10)
        right_layout.setSpacing(6)
        right_title = QLabel("Branch B Snapshot")
        right_title.setObjectName("graphDiffSectionTitle")
        right_layout.addWidget(right_title)
        self.right_preview = QTextEdit()
        self.right_preview.setReadOnly(True)
        self.right_preview.setStyleSheet("QTextEdit { font-size: 12px; }" + GRAPH_DIFF_SCROLLBAR_STYLE)
        right_layout.addWidget(self.right_preview)

        branch_compare_layout.addWidget(left_card, 1)
        branch_compare_layout.addWidget(right_card, 1)
        main_layout.addWidget(branch_compare_widget, 1)

        summary_card = QFrame()
        summary_card.setObjectName("graphDiffSummaryCard")
        summary_layout = QVBoxLayout(summary_card)
        summary_layout.setContentsMargins(12, 10, 12, 10)
        summary_layout.setSpacing(6)
        summary_title = QLabel("Divergence Summary")
        summary_title.setObjectName("graphDiffSectionTitle")
        summary_layout.addWidget(summary_title)
        self.diff_display = QTextEdit()
        self.diff_display.setReadOnly(True)
        self.diff_display.setPlaceholderText("Run the diff checker to generate a side-by-side analysis of the two branches.")
        self.diff_display.setStyleSheet("QTextEdit { font-size: 12px; }" + GRAPH_DIFF_SCROLLBAR_STYLE)
        summary_layout.addWidget(self.diff_display)
        main_layout.addWidget(summary_card, 1)

        self.compare_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {node_color.name()};
                color: {button_text_color};
                border: none;
                border-radius: 6px;
                padding: 9px 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {node_color.lighter(110).name()};
            }}
            QPushButton:disabled {{
                background-color: #555555;
                color: #cccccc;
            }}
        """)
        self.note_button.setStyleSheet(self.compare_button.styleSheet())

        self.refresh_source_views()
        self.set_status("Idle")

    def refresh_source_views(self):
        self.left_branch_payload = build_branch_payload(self.left_source_node, "Branch A")
        self.right_branch_payload = build_branch_payload(self.right_source_node, "Branch B")
        self.left_branch_label.setText(self.left_branch_payload["label"])
        self.right_branch_label.setText(self.right_branch_payload["label"])
        self.left_preview.setPlainText(self.left_branch_payload["preview"])
        self.right_preview.setPlainText(self.right_branch_payload["preview"])

    def get_comparison_payloads(self):
        self.refresh_source_views()
        return self.left_branch_payload, self.right_branch_payload

    def set_running_state(self, is_running):
        if self.is_disposed:
            return
        self.compare_button.setEnabled(not is_running)
        self.note_button.setEnabled(bool(self.note_summary.strip()) and not is_running)
        if is_running:
            self.set_status("Comparing...")
        elif self.status.startswith("Error"):
            return
        elif self.comparison_markdown.strip():
            self.set_status("Completed")
        else:
            self.set_status("Idle")

    def set_status(self, status_text):
        if self.is_disposed:
            return
        self.status = status_text
        self.status_label.setText(status_text)
        if "Comparing" in status_text:
            info_color = get_semantic_color("status_info")
            self.status_label.setStyleSheet(f"color: {info_color.name()}; background-color: rgba({info_color.red()}, {info_color.green()}, {info_color.blue()}, 0.1); border: 1px solid rgba({info_color.red()}, {info_color.green()}, {info_color.blue()}, 0.22); border-radius: 10px; padding: 3px 8px; font-size: 11px; font-weight: bold;")
        elif "Completed" in status_text or "Ready" in status_text:
            warning_color = get_semantic_color("status_warning")
            self.status_label.setStyleSheet(f"color: {warning_color.name()}; background-color: rgba({warning_color.red()}, {warning_color.green()}, {warning_color.blue()}, 0.1); border: 1px solid rgba({warning_color.red()}, {warning_color.green()}, {warning_color.blue()}, 0.22); border-radius: 10px; padding: 3px 8px; font-size: 11px; font-weight: bold;")
        elif "Error" in status_text:
            error_color = get_semantic_color("status_error")
            self.status_label.setStyleSheet(f"color: {error_color.name()}; background-color: rgba({error_color.red()}, {error_color.green()}, {error_color.blue()}, 0.1); border: 1px solid rgba({error_color.red()}, {error_color.green()}, {error_color.blue()}, 0.22); border-radius: 10px; padding: 3px 8px; font-size: 11px; font-weight: bold;")
        else:
            self.status_label.setStyleSheet("color: #9aa3ad; background-color: rgba(154, 163, 173, 0.08); border: 1px solid rgba(154, 163, 173, 0.18); border-radius: 10px; padding: 3px 8px; font-size: 11px; font-weight: bold;")

    def set_result(self, result):
        if self.is_disposed:
            return
        self.comparison_markdown = result.get("comparison_markdown", "")
        self.note_summary = result.get("note_summary", "")
        self.diff_display.setMarkdown(self.comparison_markdown)
        self.note_button.setEnabled(bool(self.note_summary.strip()))
        self.set_status("Completed")

    def set_error(self, error_message):
        if self.is_disposed:
            return
        self.comparison_markdown = f"## Error\n\n{error_message}"
        self.note_summary = ""
        self.diff_display.setMarkdown(self.comparison_markdown)
        self.note_button.setEnabled(False)
        self.set_status(f"Error: {error_message}")

    def dispose(self):
        if self.is_disposed:
            return

        self.is_disposed = True

        worker_thread = self.worker_thread
        self.worker_thread = None
        if worker_thread:
            try:
                worker_thread.stop()
            except Exception:
                pass
            for signal in (worker_thread.finished, worker_thread.error):
                try:
                    signal.disconnect()
                except (TypeError, RuntimeError):
                    pass
            try:
                if worker_thread.isRunning():
                    worker_thread.finished.connect(worker_thread.deleteLater)
                else:
                    worker_thread.deleteLater()
            except RuntimeError:
                pass

        proxy = getattr(self, "proxy", None)
        if proxy is not None:
            try:
                embedded_widget = proxy.widget()
            except RuntimeError:
                embedded_widget = None
            try:
                proxy.setWidget(None)
            except RuntimeError:
                pass
            if embedded_widget is not None:
                try:
                    embedded_widget.hide()
                    embedded_widget.deleteLater()
                except RuntimeError:
                    pass

    def boundingRect(self):
        padding = self.CONNECTION_DOT_OFFSET + self.CONNECTION_DOT_RADIUS
        return QRectF(-padding, 0, self.width + 2 * padding, self.height)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = get_current_palette()
        node_color = QColor(palette.FRAME_COLORS["Orange"]["color"])

        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width, self.height, 10, 10)
        painter.setBrush(QColor("#2d2d2d"))

        pen = QPen(node_color, 1.5)
        if self.isSelected():
            pen = QPen(palette.SELECTION, 2)
        elif self.is_search_match:
            pen = QPen(get_semantic_color("search_highlight"), 2)
        elif self.hovered:
            pen = QPen(QColor("#ffffff"), 2)

        painter.setPen(pen)
        painter.drawPath(path)

        dot_color = node_color if not (self.isSelected() or self.hovered) else pen.color().lighter(110)
        painter.setBrush(dot_color)
        painter.setPen(Qt.PenStyle.NoPen)

        dot_rect_left = QRectF(-self.CONNECTION_DOT_RADIUS, (self.height / 2) - self.CONNECTION_DOT_RADIUS, self.CONNECTION_DOT_RADIUS * 2, self.CONNECTION_DOT_RADIUS * 2)
        painter.drawPie(dot_rect_left, 90 * 16, -180 * 16)

        dot_rect_right = QRectF(self.width - self.CONNECTION_DOT_RADIUS, (self.height / 2) - self.CONNECTION_DOT_RADIUS, self.CONNECTION_DOT_RADIUS * 2, self.CONNECTION_DOT_RADIUS * 2)
        painter.drawPie(dot_rect_right, 90 * 16, 180 * 16)

        if self.is_collapsed:
            painter.setPen(QColor("#ffffff"))
            painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
            painter.drawText(QRectF(40, 0, self.width - 80, self.height), Qt.AlignmentFlag.AlignVCenter, "Branch Lens")
            qta.icon("fa5s.code-branch", color=node_color.name()).paint(painter, QRect(10, 10, 20, 20))
            self.collapse_button_rect = QRectF(self.width - 35, 5, 30, 30)
            qta.icon("fa5s.expand-arrows-alt", color="#ffffff" if self.hovered else "#888888").paint(painter, QRect(int(self.width - 30), 10, 20, 20))
        else:
            if self.hovered:
                self.collapse_button_rect = QRectF(self.width - 35, 5, 30, 30)
                painter.setBrush(QColor(255, 255, 255, 30))
                painter.setPen(QColor(255, 255, 255, 150))
                painter.drawRoundedRect(self.collapse_button_rect.adjusted(6, 6, -6, -6), 4, 4)
                painter.setPen(QPen(QColor("#ffffff"), 2))
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
            if hasattr(self.scene(), "window"):
                self.scene().window.setCurrentNode(self)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self.scene():
            self.scene().is_dragging_item = False
            self.scene()._clear_smart_guides()
        super().mouseReleaseEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsObject.GraphicsItemChange.ItemPositionChange and self.scene() and self.scene().is_dragging_item:
            return self.scene().snap_position(self, value)
        if change == QGraphicsObject.GraphicsItemChange.ItemPositionHasChanged and self.scene():
            self.scene().nodeMoved(self)
        return super().itemChange(change, value)

    def hoverEnterEvent(self, event):
        self._handle_hover_enter(event)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._handle_hover_leave(event)
        super().hoverLeaveEvent(event)
