import json
import re

from PySide6.QtCore import QRectF, Qt, Signal, QThread, QRect
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsObject,
    QGraphicsProxyWidget,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
import qtawesome as qta

import api_provider
import graphite_config as config
from graphite_canvas_items import HoverAnimationMixin
from graphite_config import get_current_palette, get_semantic_color
from graphite_connections import ConnectionItem


WORKFLOW_PLUGIN_ICONS = {
    "System Prompt": "fa5s.cog",
    "Py-Coder": "fa5s.code",
    "Gitlink": "fa5s.link",
    "Execution Sandbox": "fa5s.shield-alt",
    "Artifact / Drafter": "fa5s.file-alt",
    "Graphlink-Web": "fa5s.globe-americas",
    "Conversation Node": "fa5s.comments",
    "Graphlink-Reasoning": "fa5s.brain",
    "HTML Renderer": "fa5s.code",
    "Quality Gate": "fa5s.check-circle",
}

WORKFLOW_ALLOWED_PLUGINS = list(WORKFLOW_PLUGIN_ICONS.keys())

WORKFLOW_SCROLLBAR_STYLE = """
    QScrollBar:vertical {
        background: #1a1d20;
        width: 10px;
        margin: 0px;
        border-radius: 5px;
    }
    QScrollBar::handle:vertical {
        background-color: #555b63;
        min-height: 25px;
        border-radius: 5px;
    }
    QScrollBar::handle:vertical:hover {
        background-color: #6a727c;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0px;
        background: none;
    }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
        background: none;
    }
    QScrollBar:horizontal {
        background: #1a1d20;
        height: 10px;
        margin: 0px;
        border-radius: 5px;
    }
    QScrollBar::handle:horizontal {
        background-color: #555b63;
        min-width: 25px;
        border-radius: 5px;
    }
    QScrollBar::handle:horizontal:hover {
        background-color: #6a727c;
    }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
        width: 0px;
        background: none;
    }
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
        background: none;
    }
"""


class WorkflowArchitectAgent:
    """
    Produces an execution blueprint that decides which existing Graphlink plugins
    should be used next for the current task.
    """

    SYSTEM_PROMPT = """
You are Graphlink's Workflow Architect.
Your job is to examine the user's goal and design the smallest, highest-leverage execution plan using Graphlink's existing tools.

Allowed plugins:
- System Prompt
- Py-Coder
- Gitlink
- Execution Sandbox
- Artifact / Drafter
- Graphlink-Web
- Conversation Node
- Graphlink-Reasoning
- HTML Renderer
- Quality Gate

Rules:
1. Prefer the fewest plugins that will realistically finish the work well.
2. Recommend at most 4 plugins.
3. Only recommend HTML Renderer when the task clearly benefits from rendering HTML or UI output.
4. Only recommend System Prompt when a persistent role/persona change would materially improve the branch.
5. Favor Graphlink-Web for current facts or research, Graphlink-Reasoning for decomposition, Gitlink for repository grounding and codebase context, Py-Coder for lightweight implementation or debugging, Execution Sandbox for dependency-aware Python runs or reproducible package-based experiments, Artifact / Drafter for specs/docs, Conversation Node for deep iterative sub-work, and Quality Gate for acceptance review, hardening, or release-readiness checks.
6. Output valid JSON only. No markdown fences, no preamble.

Return exactly this shape:
{
  "title": "Short workflow title",
  "mission_brief": "2-4 sentence explanation of the best next move",
  "recommended_plugins": [
    {
      "plugin": "Plugin Name",
      "priority": "high",
      "why": "Why this plugin should be used next",
      "starter_prompt": "The exact first prompt/query/instruction to seed into that plugin"
    }
  ],
  "workflow_steps": [
    "Step 1 ..."
  ],
  "deliverables": [
    "Deliverable 1"
  ],
  "risks": [
    "Risk 1"
  ],
  "success_signals": [
    "Signal 1"
  ]
}
"""

    def _flatten_content(self, content):
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
            return "\n".join(part for part in text_parts if part)
        return str(content)

    def _clean_json_response(self, raw_text):
        block_match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", raw_text, re.IGNORECASE)
        if block_match:
            return block_match.group(1).strip()

        json_match = re.search(r"(\{[\s\S]*\})", raw_text)
        if json_match:
            return json_match.group(1).strip()

        return raw_text.strip()

    def _fallback_plan(self, goal, constraints, history):
        lowered = f"{goal}\n{constraints}".lower()
        recommendations = []

        def add(plugin, why, starter_prompt, priority="high"):
            if plugin in WORKFLOW_ALLOWED_PLUGINS and not any(item["plugin"] == plugin for item in recommendations):
                recommendations.append({
                    "plugin": plugin,
                    "priority": priority,
                    "why": why,
                    "starter_prompt": starter_prompt,
                })

        if any(term in lowered for term in ["latest", "current", "research", "competitor", "market", "news", "trend"]):
            add(
                "Graphlink-Web",
                "The goal depends on external facts or discovery, so web grounding should happen before synthesis.",
                goal,
                "high",
            )

        if any(term in lowered for term in ["plan", "strategy", "compare", "tradeoff", "architecture", "system", "complex"]):
            add(
                "Graphlink-Reasoning",
                "This work benefits from decomposition, critique, and a structured decision path.",
                f"Break this goal into an execution plan with key tradeoffs and checks:\n\n{goal}",
                "high",
            )

        if any(term in lowered for term in ["repo", "repository", "github", "git", "codebase", "checkout", "branch", "monorepo"]):
            add(
                "Gitlink",
                "Repository-aware work is easier when the branch is grounded in a concrete codebase snapshot instead of only freeform chat context.",
                f"Connect this branch to the relevant repository, load the most useful files or full repo scope, and prepare structured XML context for the follow-up work:\n\n{goal}",
                "high",
            )

        if any(term in lowered for term in ["code", "bug", "fix", "build", "implement", "python", "script", "refactor", "ui", "frontend", "html", "css"]):
            add(
                "Py-Coder",
                "Implementation work will move faster once the task is converted into an executable coding prompt.",
                f"Implement this carefully and explain the key design decisions:\n\n{goal}",
                "high",
            )

        if any(term in lowered for term in ["dependency", "dependencies", "requirements", "virtualenv", "venv", "pandas", "numpy", "matplotlib", "scipy", "sandbox", "library install"]):
            add(
                "Execution Sandbox",
                "This task benefits from an isolated dependency-aware runtime instead of the lighter shared Py-Coder path.",
                f"Build and run the required Python workflow inside an isolated sandbox, using only the libraries declared in requirements.txt:\n\n{goal}",
                "high",
            )

        if any(term in lowered for term in ["spec", "doc", "draft", "proposal", "write", "report", "summary", "prd", "brief"]):
            add(
                "Artifact / Drafter",
                "A living document will help keep the work product aligned while the branch evolves.",
                f"Create or refine the working document for this goal:\n\n{goal}",
                "medium",
            )

        if any(term in lowered for term in ["ship", "shipping", "production", "ready", "qa", "quality", "validate", "validation", "acceptance", "hardening", "release"]):
            add(
                "Quality Gate",
                "This goal needs a stronger acceptance review so the branch can be judged against a real shipping bar.",
                f"Review this branch for production readiness and identify the highest-value remaining gaps:\n\n{goal}",
                "medium",
            )

        if not recommendations:
            add(
                "Graphlink-Reasoning",
                "A structured plan is the safest first move when the objective is broad or ambiguous.",
                f"Create the best execution plan for this objective:\n\n{goal}",
                "high",
            )
            add(
                "Artifact / Drafter",
                "Capturing the plan as a living artifact makes downstream execution easier.",
                f"Draft a concise working brief for this objective:\n\n{goal}",
                "medium",
            )
            add(
                "Quality Gate",
                "A final acceptance review step helps close the loop between planning and production readiness.",
                f"Define how this objective should be judged when the branch is ready:\n\n{goal}",
                "low",
            )

        steps = [
            "Use the first recommended plugin to create the initial working direction.",
            "Use the follow-up plugins only where they materially advance the branch.",
            "Keep results inside the graph so downstream nodes inherit the execution context.",
        ]

        if constraints.strip():
            steps.insert(1, f"Honor these constraints while executing: {constraints.strip()}")

        return {
            "title": "Workflow Blueprint",
            "mission_brief": "The app already has strong specialist nodes, so the next highest-leverage move is to orchestrate them in a tighter sequence for this goal.",
            "recommended_plugins": recommendations[:4],
            "workflow_steps": steps,
            "deliverables": [
                "A clear first action in the recommended plugin stack",
                "A graph branch whose children inherit a concrete execution plan",
            ],
            "risks": [
                "Using too many plugins can create busywork instead of leverage",
                "If the first node is under-specified, downstream branches will inherit weak context",
            ],
            "success_signals": [
                "The next plugin can start with a specific seeded prompt",
                "The branch has a concrete path from analysis to execution",
            ],
        }

    def _normalize_plan(self, plan, goal, constraints, history):
        if not isinstance(plan, dict):
            plan = {}

        normalized_recommendations = []
        for item in plan.get("recommended_plugins", [])[:4]:
            if not isinstance(item, dict):
                continue
            plugin = str(item.get("plugin", "")).strip()
            if plugin not in WORKFLOW_ALLOWED_PLUGINS:
                continue
            why = str(item.get("why", "")).strip() or "This plugin is a good fit for the current objective."
            starter_prompt = str(item.get("starter_prompt", "")).strip() or goal
            priority = str(item.get("priority", "medium")).strip().lower()
            if priority not in {"high", "medium", "low"}:
                priority = "medium"
            normalized_recommendations.append({
                "plugin": plugin,
                "priority": priority,
                "why": why,
                "starter_prompt": starter_prompt,
            })

        if not normalized_recommendations:
            return self._fallback_plan(goal, constraints, history)

        def normalize_list(key, fallback_items):
            items = []
            for value in plan.get(key, []):
                text = str(value).strip()
                if text:
                    items.append(text)
            return items or fallback_items

        return {
            "title": str(plan.get("title", "")).strip() or "Workflow Blueprint",
            "mission_brief": str(plan.get("mission_brief", "")).strip() or "Use the recommended plugins in order, keeping the branch focused on the smallest set of tools that can finish the work well.",
            "recommended_plugins": normalized_recommendations,
            "workflow_steps": normalize_list("workflow_steps", ["Start with the highest-priority plugin recommendation and keep the branch tightly scoped."]),
            "deliverables": normalize_list("deliverables", ["A branch with a concrete plan and a clear first move."]),
            "risks": normalize_list("risks", ["Over-orchestrating the branch can slow execution."]),
            "success_signals": normalize_list("success_signals", ["The next node has a precise, usable seeded prompt."]),
        }

    def _build_markdown(self, plan):
        lines = [
            f"# {plan['title']}",
            "",
            "## Mission Brief",
            plan["mission_brief"],
            "",
            "## Recommended Plugins",
        ]

        for item in plan["recommended_plugins"]:
            lines.append(f"- **{item['plugin']}** ({item['priority'].title()}): {item['why']}")
            lines.append(f"  Starter prompt: `{item['starter_prompt']}`")

        lines.extend(["", "## Workflow Steps"])
        for index, step in enumerate(plan["workflow_steps"], start=1):
            lines.append(f"{index}. {step}")

        lines.extend(["", "## Deliverables"])
        for item in plan["deliverables"]:
            lines.append(f"- {item}")

        lines.extend(["", "## Risks"])
        for item in plan["risks"]:
            lines.append(f"- {item}")

        lines.extend(["", "## Success Signals"])
        for item in plan["success_signals"]:
            lines.append(f"- {item}")

        return "\n".join(lines)

    def get_response(self, goal, constraints, history):
        history_lines = []
        for msg in history[-8:]:
            role = msg.get("role", "user").title()
            content = self._flatten_content(msg.get("content", ""))
            if content.strip():
                history_lines.append(f"{role}: {content.strip()[:1200]}")

        history_text = "\n\n".join(history_lines) if history_lines else "No prior branch history."
        user_prompt = f"""
Goal:
{goal}

Constraints:
{constraints if constraints.strip() else "None provided."}

Branch History:
{history_text}
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
            cleaned = self._clean_json_response(raw_text)
            parsed = json.loads(cleaned)
            normalized = self._normalize_plan(parsed, goal, constraints, history)
        except Exception:
            normalized = self._fallback_plan(goal, constraints, history)

        normalized["blueprint_markdown"] = self._build_markdown(normalized)
        return normalized


class WorkflowWorkerThread(QThread):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, goal, constraints, history):
        super().__init__()
        self.goal = goal
        self.constraints = constraints
        self.history = history
        self.agent = WorkflowArchitectAgent()
        self._is_running = True

    def run(self):
        try:
            if not self._is_running:
                return
            result = self.agent.get_response(self.goal, self.constraints, self.history)
            if self._is_running:
                self.finished.emit(result)
        except Exception as exc:
            if self._is_running:
                self.error.emit(str(exc))
        finally:
            self._is_running = False

    def stop(self):
        self._is_running = False


class WorkflowRecommendationCard(QFrame):
    add_requested = Signal(str, str)

    def __init__(self, recommendation, accent_color, parent=None):
        super().__init__(parent)
        self.recommendation = recommendation
        self.setObjectName("workflowRecommendationCard")
        self.setStyleSheet(f"""
            QFrame#workflowRecommendationCard {{
                background-color: #24282d;
                border: 1px solid #353b43;
                border-radius: 8px;
            }}
            QLabel {{
                background: transparent;
                color: #d7dce2;
            }}
            QLabel#workflowPluginTitle {{
                color: #ffffff;
                font-weight: bold;
                font-size: 13px;
            }}
            QLabel#workflowMeta {{
                color: {accent_color};
                background-color: transparent;
                font-size: 10px;
                font-weight: bold;
            }}
            QLabel#workflowReason {{
                color: #c8d0d8;
                font-size: 12px;
            }}
            QLabel#workflowPrompt {{
                color: #a9b4bf;
                font-size: 11px;
                padding-top: 4px;
                border-top: 1px solid #31363d;
            }}
            QPushButton {{
                background-color: {accent_color};
                color: black;
                border: none;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #79d884;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        icon_label = QLabel()
        icon_name = WORKFLOW_PLUGIN_ICONS.get(recommendation["plugin"], "fa5s.puzzle-piece")
        icon_label.setPixmap(qta.icon(icon_name, color=accent_color).pixmap(16, 16))
        header_layout.addWidget(icon_label)

        title_label = QLabel(recommendation["plugin"])
        title_label.setObjectName("workflowPluginTitle")
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        priority_label = QLabel(recommendation["priority"].upper())
        priority_label.setObjectName("workflowMeta")
        header_layout.addWidget(priority_label)
        layout.addLayout(header_layout)

        why_label = QLabel(recommendation["why"])
        why_label.setObjectName("workflowReason")
        why_label.setWordWrap(True)
        layout.addWidget(why_label)

        prompt_label = QLabel(f"Seed: {recommendation['starter_prompt']}")
        prompt_label.setObjectName("workflowPrompt")
        prompt_label.setWordWrap(True)
        layout.addWidget(prompt_label)

        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.addStretch()
        add_button = QPushButton("Add Node")
        add_button.clicked.connect(lambda: self.add_requested.emit(recommendation["plugin"], recommendation["starter_prompt"]))
        action_layout.addWidget(add_button)
        layout.addLayout(action_layout)


class WorkflowConnectionItem(ConnectionItem):
    def paint(self, painter, option, widget=None):
        if not (self.start_node and self.end_node):
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = get_current_palette()
        node_color = QColor(palette.FRAME_COLORS["Green"]["color"])
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


class WorkflowNode(QGraphicsObject, HoverAnimationMixin):
    workflow_requested = Signal(object)
    plugin_requested = Signal(object, str, str)

    NODE_WIDTH = 760
    NODE_HEIGHT = 690
    COLLAPSED_WIDTH = 260
    COLLAPSED_HEIGHT = 40
    CONNECTION_DOT_RADIUS = 5
    CONNECTION_DOT_OFFSET = 0

    def __init__(self, parent_node, parent=None):
        super().__init__(parent)
        HoverAnimationMixin.__init__(self)
        self.parent_node = parent_node
        self.children = []
        self.is_user = False
        self.conversation_history = []
        self.goal = ""
        self.constraints = ""
        self.status = "Idle"
        self.blueprint_markdown = ""
        self.recommendations = []
        self.is_search_match = False
        self.is_collapsed = False
        self.collapse_button_rect = QRectF()

        self.setFlag(QGraphicsObject.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsObject.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsObject.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.hovered = False

        self.widget = QWidget()
        self.widget.setObjectName("workflowMainWidget")
        self.widget.setFixedSize(self.NODE_WIDTH, self.NODE_HEIGHT)
        self.widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.widget.setStyleSheet("""
            QWidget#workflowMainWidget {
                background-color: transparent;
                color: #e0e0e0;
            }
            QWidget#workflowMainWidget QLabel {
                background-color: transparent;
            }
        """)

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
        node_color = QColor(palette.FRAME_COLORS["Green"]["color"])
        brightness = (node_color.red() * 299 + node_color.green() * 587 + node_color.blue() * 114) / 1000
        button_text_color = "black" if brightness > 128 else "white"

        self.widget.setStyleSheet(f"""
            QWidget#workflowMainWidget {{
                background-color: transparent;
                color: #e0e0e0;
                font-family: 'Segoe UI', sans-serif;
            }}
            QWidget#workflowMainWidget QLabel {{
                background-color: transparent;
            }}
            QFrame#workflowBriefShell {{
                background-color: #202327;
                border: 1px solid #353b43;
                border-radius: 10px;
            }}
            QLabel#workflowSectionTitle {{
                color: #ffffff;
                font-size: 12px;
                font-weight: bold;
            }}
            QLabel#workflowSectionHint {{
                color: #95a0ab;
                font-size: 11px;
            }}
            QLabel#workflowFieldLabel {{
                color: #c7cdd4;
                font-size: 11px;
                font-weight: bold;
            }}
            QLabel#workflowMetricBadge {{
                color: {node_color.name()};
                background-color: rgba(87, 214, 111, 0.1);
                border: 1px solid rgba(87, 214, 111, 0.24);
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
                font-family: 'Segoe UI', sans-serif;
            }}
            QTextEdit:focus {{
                border: 1px solid {node_color.name()};
            }}
            QTabWidget::pane {{
                border: 1px solid #3a4048;
                background: #1d2024;
                border-radius: 8px;
            }}
            QTabBar::tab {{
                background: #25282d;
                color: #97a1ab;
                padding: 8px 14px;
                border: 1px solid #3a4048;
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                margin-right: 2px;
                font-weight: bold;
            }}
            QTabBar::tab:selected {{
                background: #1d2024;
                color: #ffffff;
                border-top: 2px solid {node_color.name()};
                border-bottom: 1px solid #1d2024;
            }}
            QTabBar::tab:hover:!selected {{
                background: #2d3136;
                color: #ffffff;
            }}
        """)

        main_layout = QVBoxLayout(self.widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(4, 0, 4, 0)
        header_layout.setSpacing(8)
        icon = QLabel()
        icon.setPixmap(qta.icon("fa5s.project-diagram", color=node_color).pixmap(18, 18))
        header_layout.addWidget(icon)
        title_label = QLabel("Workflow Architect")
        title_label.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {node_color.name()};")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: #3f3f3f; border: none; height: 1px;")
        main_layout.addWidget(line)

        briefing_surface = QFrame()
        briefing_surface.setObjectName("workflowBriefShell")
        briefing_surface.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        briefing_layout = QVBoxLayout(briefing_surface)
        briefing_layout.setContentsMargins(14, 12, 14, 12)
        briefing_layout.setSpacing(8)

        briefing_header = QHBoxLayout()
        briefing_header.setContentsMargins(0, 0, 0, 0)
        briefing_title = QLabel("Mission Brief")
        briefing_title.setObjectName("workflowSectionTitle")
        briefing_header.addWidget(briefing_title)
        briefing_header.addStretch()
        briefing_hint = QLabel("Goal + constraints")
        briefing_hint.setObjectName("workflowMetricBadge")
        briefing_header.addWidget(briefing_hint)
        briefing_layout.addLayout(briefing_header)

        mission_label = QLabel("Mission")
        mission_label.setObjectName("workflowFieldLabel")
        briefing_layout.addWidget(mission_label)
        self.goal_input = QTextEdit()
        self.goal_input.setPlaceholderText("Describe the goal, outcome, or feature you want the branch to accomplish...")
        self.goal_input.setFixedHeight(96)
        self.goal_input.setStyleSheet("QTextEdit { font-size: 12px; }" + WORKFLOW_SCROLLBAR_STYLE)
        self.goal_input.textChanged.connect(self._on_goal_changed)
        briefing_layout.addWidget(self.goal_input)

        constraints_label = QLabel("Constraints / Context")
        constraints_label.setObjectName("workflowFieldLabel")
        briefing_layout.addWidget(constraints_label)
        self.constraints_input = QTextEdit()
        self.constraints_input.setPlaceholderText("Optional: budget, quality bar, constraints, audience, time pressure, tech stack...")
        self.constraints_input.setFixedHeight(68)
        self.constraints_input.setStyleSheet("QTextEdit { font-size: 12px; }" + WORKFLOW_SCROLLBAR_STYLE)
        self.constraints_input.textChanged.connect(self._on_constraints_changed)
        briefing_layout.addWidget(self.constraints_input)

        controls_panel = QWidget()
        controls_layout = QHBoxLayout(controls_panel)
        controls_layout.setContentsMargins(0, 4, 0, 0)
        controls_layout.setSpacing(10)
        self.run_button = QPushButton("Design Workflow")
        self.run_button.setIcon(qta.icon("fa5s.magic", color=button_text_color))
        self.run_button.clicked.connect(lambda: self.workflow_requested.emit(self))
        controls_layout.addWidget(self.run_button)

        self.status_label = QLabel("Idle")
        self.status_label.setObjectName("workflowMetricBadge")
        controls_layout.addWidget(self.status_label)
        controls_layout.addStretch()
        status_hint = QLabel("Uses the current branch context to generate a sequenced plugin plan.")
        status_hint.setObjectName("workflowSectionHint")
        controls_layout.addWidget(status_hint)
        briefing_layout.addWidget(controls_panel)

        main_layout.addWidget(briefing_surface)

        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setDocumentMode(True)

        blueprint_panel = QWidget()
        blueprint_layout = QVBoxLayout(blueprint_panel)
        blueprint_layout.setContentsMargins(0, 0, 0, 0)
        blueprint_layout.setSpacing(8)

        blueprint_subtitle = QLabel("Execution blueprint and ordered workflow steps.")
        blueprint_subtitle.setObjectName("workflowSectionHint")
        blueprint_subtitle.setWordWrap(True)
        blueprint_layout.addWidget(blueprint_subtitle)

        self.plan_display = QTextEdit()
        self.plan_display.setReadOnly(True)
        self.plan_display.setPlaceholderText("Your recommended plugin sequence and execution blueprint will appear here...")
        self.plan_display.document().setDefaultStyleSheet("""
            h1, h2 { color: #ffffff; }
            p, li { color: #d6d6d6; font-family: 'Segoe UI', sans-serif; font-size: 12px; }
            code { background-color: #31353b; padding: 2px 4px; border-radius: 4px; color: #b5f5bf; }
        """)
        self.plan_display.setStyleSheet("""
            QTextEdit {
                font-size: 12px;
                background-color: transparent;
                border: none;
                padding: 6px 2px 2px 2px;
            }
        """ + WORKFLOW_SCROLLBAR_STYLE)
        blueprint_layout.addWidget(self.plan_display, stretch=1)

        recommendations_panel = QWidget()
        recommendations_layout = QVBoxLayout(recommendations_panel)
        recommendations_layout.setContentsMargins(0, 0, 0, 0)
        recommendations_layout.setSpacing(8)

        recommendation_header = QHBoxLayout()
        recommendation_header.setContentsMargins(0, 0, 0, 0)
        recommendation_title = QLabel("Launch the recommended specialist nodes directly from this list.")
        recommendation_title.setObjectName("workflowSectionHint")
        recommendation_title.setWordWrap(True)
        recommendation_header.addWidget(recommendation_title, 1)
        recommendation_header.addStretch()
        self.recommendation_count_label = QLabel("0")
        self.recommendation_count_label.setObjectName("workflowMetricBadge")
        recommendation_header.addWidget(self.recommendation_count_label)
        recommendations_layout.addLayout(recommendation_header)

        self.recommendation_scroll = QScrollArea()
        self.recommendation_scroll.setWidgetResizable(True)
        self.recommendation_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.recommendation_scroll.setStyleSheet("""
            QScrollArea {
                background-color: transparent;
                border: none;
            }
            QScrollArea > QWidget > QWidget {
                background: transparent;
            }
        """ + WORKFLOW_SCROLLBAR_STYLE)

        self.recommendation_content = QWidget()
        self.recommendation_content.setStyleSheet("background: transparent;")
        self.recommendation_layout = QVBoxLayout(self.recommendation_content)
        self.recommendation_layout.setContentsMargins(0, 6, 0, 0)
        self.recommendation_layout.setSpacing(8)
        self.recommendation_layout.addWidget(self._build_empty_state())
        self.recommendation_layout.addStretch()
        self.recommendation_scroll.setWidget(self.recommendation_content)
        recommendations_layout.addWidget(self.recommendation_scroll)

        self.workspace_tabs.addTab(blueprint_panel, qta.icon("fa5s.map-signs", color="#cccccc"), "Blueprint")
        self.workspace_tabs.addTab(recommendations_panel, qta.icon("fa5s.project-diagram", color="#cccccc"), "Recommended Nodes (0)")
        main_layout.addWidget(self.workspace_tabs, stretch=1)

        self.run_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {node_color.name()};
                color: {button_text_color};
                border: none;
                border-radius: 8px;
                padding: 9px 16px;
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

    def _build_empty_state(self):
        label = QLabel("No recommendations yet. Run the workflow architect to generate the next best specialist nodes for this branch.")
        label.setWordWrap(True)
        label.setStyleSheet("color: #8b929b; padding: 12px; background-color: #171a1d; border: 1px dashed #353b42; border-radius: 8px;")
        return label

    def _clear_recommendations(self):
        while self.recommendation_layout.count():
            item = self.recommendation_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _on_goal_changed(self):
        self.goal = self.goal_input.toPlainText()

    def _on_constraints_changed(self):
        self.constraints = self.constraints_input.toPlainText()

    def get_goal(self):
        return self.goal_input.toPlainText()

    def get_constraints(self):
        return self.constraints_input.toPlainText()

    def set_running_state(self, is_running):
        self.run_button.setEnabled(not is_running)
        self.goal_input.setReadOnly(is_running)
        self.constraints_input.setReadOnly(is_running)
        self.run_button.setText("Designing..." if is_running else "Design Workflow")
        self.set_status("Designing workflow..." if is_running else "Completed")

    def set_status(self, status_text):
        self.status = status_text
        self.status_label.setText(status_text)
        if "Designing" in status_text:
            info_color = get_semantic_color("status_info")
            self.status_label.setStyleSheet(f"color: {info_color.name()}; background-color: rgba({info_color.red()}, {info_color.green()}, {info_color.blue()}, 0.1); border: 1px solid rgba({info_color.red()}, {info_color.green()}, {info_color.blue()}, 0.22); border-radius: 10px; padding: 3px 8px; font-size: 11px; font-weight: bold;")
        elif "Completed" in status_text:
            success_color = get_semantic_color("status_success")
            self.status_label.setStyleSheet(f"color: {success_color.name()}; background-color: rgba({success_color.red()}, {success_color.green()}, {success_color.blue()}, 0.1); border: 1px solid rgba({success_color.red()}, {success_color.green()}, {success_color.blue()}, 0.22); border-radius: 10px; padding: 3px 8px; font-size: 11px; font-weight: bold;")
        elif "Error" in status_text:
            error_color = get_semantic_color("status_error")
            self.status_label.setStyleSheet(f"color: {error_color.name()}; background-color: rgba({error_color.red()}, {error_color.green()}, {error_color.blue()}, 0.1); border: 1px solid rgba({error_color.red()}, {error_color.green()}, {error_color.blue()}, 0.22); border-radius: 10px; padding: 3px 8px; font-size: 11px; font-weight: bold;")
        else:
            self.status_label.setStyleSheet("color: #9aa3ad; background-color: rgba(154, 163, 173, 0.08); border: 1px solid rgba(154, 163, 173, 0.18); border-radius: 10px; padding: 3px 8px; font-size: 11px; font-weight: bold;")

    def set_plan(self, plan):
        self.blueprint_markdown = plan.get("blueprint_markdown", "")
        self.recommendations = plan.get("recommended_plugins", [])
        self.plan_display.setMarkdown(self.blueprint_markdown)
        self.recommendation_count_label.setText(str(len(self.recommendations)))
        self.workspace_tabs.setTabText(1, f"Recommended Nodes ({len(self.recommendations)})")

        palette = get_current_palette()
        accent_color = palette.FRAME_COLORS["Green"]["color"]
        self._clear_recommendations()

        if not self.recommendations:
            self.recommendation_layout.addWidget(self._build_empty_state())
        else:
            for recommendation in self.recommendations:
                card = WorkflowRecommendationCard(recommendation, accent_color)
                card.add_requested.connect(lambda plugin, prompt, node=self: self.plugin_requested.emit(node, plugin, prompt))
                self.recommendation_layout.addWidget(card)
        self.recommendation_layout.addStretch()
        self.set_status("Completed")

    def set_error(self, error_message):
        self.blueprint_markdown = f"## Error\n\n{error_message}"
        self.plan_display.setMarkdown(self.blueprint_markdown)
        self.recommendations = []
        self.recommendation_count_label.setText("0")
        self.workspace_tabs.setTabText(1, "Recommended Nodes (0)")
        self._clear_recommendations()
        self.recommendation_layout.addWidget(self._build_empty_state())
        self.recommendation_layout.addStretch()
        self.set_status(f"Error: {error_message}")

    def boundingRect(self):
        padding = self.CONNECTION_DOT_OFFSET + self.CONNECTION_DOT_RADIUS
        return QRectF(-padding, 0, self.width + 2 * padding, self.height)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = get_current_palette()
        node_color = QColor(palette.FRAME_COLORS["Green"]["color"])

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
            painter.drawText(QRectF(40, 0, self.width - 80, self.height), Qt.AlignmentFlag.AlignVCenter, "Workflow Architect")
            qta.icon("fa5s.project-diagram", color=node_color.name()).paint(painter, QRect(10, 10, 20, 20))
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
