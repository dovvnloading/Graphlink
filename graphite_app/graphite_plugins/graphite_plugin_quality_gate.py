from PySide6.QtCore import QRect, QRectF, Qt, QThread, Signal
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

from graphite_canvas_items import HoverAnimationMixin
from graphite_config import get_current_palette, get_semantic_color
from graphite_connections import ConnectionItem
from graphite_plugins.graphite_plugin_context_menu import PluginNodeContextMenu
from graphite_plugins.quality_gate.scoring import (
    QUALITY_GATE_PLUGIN_ICONS,
    QualityGateAnalyzer,
    build_quality_gate_payload,
)


QUALITY_GATE_SCROLLBAR_STYLE = """
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



class QualityGateWorkerThread(QThread):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, goal, criteria, payload):
        super().__init__()
        self.goal = goal
        self.criteria = criteria
        self.payload = payload
        self.agent = QualityGateAnalyzer()
        self._is_running = True

    def run(self):
        try:
            if not self._is_running:
                return
            result = self.agent.get_response(self.goal, self.criteria, self.payload)
            if self._is_running:
                self.finished.emit(result)
        except Exception as exc:
            if self._is_running:
                self.error.emit(str(exc))
        finally:
            self._is_running = False

    def stop(self):
        self._is_running = False


class QualityGateRecommendationCard(QFrame):
    add_requested = Signal(str, str)

    def __init__(self, recommendation, accent_color, parent=None):
        super().__init__(parent)
        self.recommendation = recommendation
        self.setObjectName("qualityGateRecommendationCard")
        self.setStyleSheet(f"""
            QFrame#qualityGateRecommendationCard {{
                background-color: #24282d;
                border: 1px solid #353b43;
                border-radius: 8px;
            }}
            QLabel {{
                background: transparent;
                color: #d7dce2;
            }}
            QLabel#qualityGatePluginTitle {{
                color: #ffffff;
                font-weight: bold;
                font-size: 13px;
            }}
            QLabel#qualityGateMeta {{
                color: {accent_color};
                background-color: transparent;
                font-size: 10px;
                font-weight: bold;
            }}
            QLabel#qualityGateReason {{
                color: #c8d0d8;
                font-size: 12px;
            }}
            QLabel#qualityGatePrompt {{
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
                background-color: #f7d85b;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        icon_label = QLabel()
        icon_name = QUALITY_GATE_PLUGIN_ICONS.get(recommendation["plugin"], "fa5s.puzzle-piece")
        icon_label.setPixmap(qta.icon(icon_name, color=accent_color).pixmap(16, 16))
        header_layout.addWidget(icon_label)

        title_label = QLabel(recommendation["plugin"])
        title_label.setObjectName("qualityGatePluginTitle")
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        priority_label = QLabel(recommendation["priority"].upper())
        priority_label.setObjectName("qualityGateMeta")
        header_layout.addWidget(priority_label)
        layout.addLayout(header_layout)

        why_label = QLabel(recommendation["why"])
        why_label.setObjectName("qualityGateReason")
        why_label.setWordWrap(True)
        layout.addWidget(why_label)

        prompt_label = QLabel(f"Seed: {recommendation['starter_prompt']}")
        prompt_label.setObjectName("qualityGatePrompt")
        prompt_label.setWordWrap(True)
        layout.addWidget(prompt_label)

        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.addStretch()
        add_button = QPushButton("Add Node")
        add_button.clicked.connect(lambda: self.add_requested.emit(recommendation["plugin"], recommendation["starter_prompt"]))
        action_layout.addWidget(add_button)
        layout.addLayout(action_layout)


class QualityGateConnectionItem(ConnectionItem):
    def paint(self, painter, option, widget=None):
        if not (self.start_node and self.end_node):
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = get_current_palette()
        node_color = QColor(palette.FRAME_COLORS["Yellow"]["color"])
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


class QualityGateNode(QGraphicsObject, HoverAnimationMixin):
    supports_branch_context_toggle = True
    review_requested = Signal(object)
    plugin_requested = Signal(object, str, str)
    note_requested = Signal(object)

    NODE_WIDTH = 820
    NODE_HEIGHT = 760
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
        self.criteria = ""
        self.status = "Idle"
        self.verdict = "pending"
        self.readiness_score = 0
        self.review_markdown = ""
        self.note_summary = ""
        self.recommendations = []
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
        self.widget.setObjectName("qualityGateMainWidget")
        self.widget.setFixedSize(self.NODE_WIDTH, self.NODE_HEIGHT)
        self.widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.widget.setStyleSheet("""
            QWidget#qualityGateMainWidget {
                background-color: transparent;
                color: #e0e0e0;
            }
            QWidget#qualityGateMainWidget QLabel {
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
        node_color = QColor(palette.FRAME_COLORS["Yellow"]["color"])
        brightness = (node_color.red() * 299 + node_color.green() * 587 + node_color.blue() * 114) / 1000
        button_text_color = "black" if brightness > 128 else "white"
        badge_rgba = f"{node_color.red()}, {node_color.green()}, {node_color.blue()}"

        self.widget.setStyleSheet(f"""
            QWidget#qualityGateMainWidget {{
                background-color: transparent;
                color: #e0e0e0;
                font-family: 'Segoe UI', sans-serif;
            }}
            QWidget#qualityGateMainWidget QLabel {{
                background-color: transparent;
            }}
            QFrame#qualityGateBriefShell {{
                background-color: #202327;
                border: 1px solid #353b43;
                border-radius: 10px;
            }}
            QLabel#qualityGateSectionTitle {{
                color: #ffffff;
                font-size: 12px;
                font-weight: bold;
            }}
            QLabel#qualityGateSectionHint {{
                color: #95a0ab;
                font-size: 11px;
            }}
            QLabel#qualityGateFieldLabel {{
                color: #c7cdd4;
                font-size: 11px;
                font-weight: bold;
            }}
            QLabel#qualityGateMetricBadge {{
                color: {node_color.name()};
                background-color: rgba({badge_rgba}, 0.12);
                border: 1px solid rgba({badge_rgba}, 0.26);
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
        icon.setPixmap(qta.icon("fa5s.check-circle", color=node_color).pixmap(18, 18))
        header_layout.addWidget(icon)
        title_label = QLabel("Quality Gate")
        title_label.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {node_color.name()};")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: #3f3f3f; border: none; height: 1px;")
        main_layout.addWidget(line)

        briefing_surface = QFrame()
        briefing_surface.setObjectName("qualityGateBriefShell")
        briefing_surface.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        briefing_layout = QVBoxLayout(briefing_surface)
        briefing_layout.setContentsMargins(14, 12, 14, 12)
        briefing_layout.setSpacing(8)

        briefing_header = QHBoxLayout()
        briefing_header.setContentsMargins(0, 0, 0, 0)
        briefing_title = QLabel("Release Review")
        briefing_title.setObjectName("qualityGateSectionTitle")
        briefing_header.addWidget(briefing_title)
        briefing_header.addStretch()
        self.branch_label = QLabel("Current Branch")
        self.branch_label.setObjectName("qualityGateMetricBadge")
        briefing_header.addWidget(self.branch_label)
        briefing_layout.addLayout(briefing_header)

        goal_label = QLabel("Target Outcome")
        goal_label.setObjectName("qualityGateFieldLabel")
        briefing_layout.addWidget(goal_label)
        self.goal_input = QTextEdit()
        self.goal_input.setPlaceholderText("What should this branch be able to do when it is truly ready?")
        self.goal_input.setFixedHeight(90)
        self.goal_input.setStyleSheet("QTextEdit { font-size: 12px; }" + QUALITY_GATE_SCROLLBAR_STYLE)
        self.goal_input.textChanged.connect(self._on_goal_changed)
        briefing_layout.addWidget(self.goal_input)

        criteria_label = QLabel("Acceptance Criteria / Shipping Bar")
        criteria_label.setObjectName("qualityGateFieldLabel")
        briefing_layout.addWidget(criteria_label)
        self.criteria_input = QTextEdit()
        self.criteria_input.setPlaceholderText("What must be proven before this should be considered ready? Think tests, UX, correctness, evidence, polish, or constraints.")
        self.criteria_input.setFixedHeight(78)
        self.criteria_input.setStyleSheet("QTextEdit { font-size: 12px; }" + QUALITY_GATE_SCROLLBAR_STYLE)
        self.criteria_input.textChanged.connect(self._on_criteria_changed)
        briefing_layout.addWidget(self.criteria_input)

        controls_panel = QWidget()
        controls_layout = QHBoxLayout(controls_panel)
        controls_layout.setContentsMargins(0, 4, 0, 0)
        controls_layout.setSpacing(10)

        self.run_button = QPushButton("Run Quality Gate")
        self.run_button.setIcon(qta.icon("fa5s.check-circle", color=button_text_color))
        self.run_button.clicked.connect(lambda: self.review_requested.emit(self))
        controls_layout.addWidget(self.run_button)

        self.note_button = QPushButton("Create Summary Note")
        self.note_button.setIcon(qta.icon("fa5s.sticky-note", color=button_text_color))
        self.note_button.clicked.connect(lambda: self.note_requested.emit(self))
        self.note_button.setEnabled(False)
        controls_layout.addWidget(self.note_button)

        self.status_label = QLabel("Idle")
        self.status_label.setObjectName("qualityGateMetricBadge")
        controls_layout.addWidget(self.status_label)
        controls_layout.addStretch()
        status_hint = QLabel("Best used after a branch has meaningful implementation, research, or drafting work to inspect.")
        status_hint.setObjectName("qualityGateSectionHint")
        status_hint.setWordWrap(True)
        controls_layout.addWidget(status_hint, 1)
        briefing_layout.addWidget(controls_panel)

        main_layout.addWidget(briefing_surface)

        metrics_bar = QHBoxLayout()
        metrics_bar.setContentsMargins(2, 0, 2, 0)
        metrics_bar.setSpacing(8)

        self.verdict_label = QLabel("Verdict: Pending")
        self.verdict_label.setObjectName("qualityGateMetricBadge")
        metrics_bar.addWidget(self.verdict_label)

        self.score_label = QLabel("Readiness: --")
        self.score_label.setObjectName("qualityGateMetricBadge")
        metrics_bar.addWidget(self.score_label)

        self.fix_count_label = QLabel("Fix Paths: 0")
        self.fix_count_label.setObjectName("qualityGateMetricBadge")
        metrics_bar.addWidget(self.fix_count_label)
        metrics_bar.addStretch()
        main_layout.addLayout(metrics_bar)

        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setDocumentMode(True)

        review_panel = QWidget()
        review_layout = QVBoxLayout(review_panel)
        review_layout.setContentsMargins(0, 0, 0, 0)
        review_layout.setSpacing(8)

        review_subtitle = QLabel("This report focuses on readiness, blockers, missing evidence, and the shortest credible path to ship.")
        review_subtitle.setObjectName("qualityGateSectionHint")
        review_subtitle.setWordWrap(True)
        review_layout.addWidget(review_subtitle)

        self.review_display = QTextEdit()
        self.review_display.setReadOnly(True)
        self.review_display.setPlaceholderText("Run Quality Gate to generate a rich production-readiness review for this branch.")
        self.review_display.document().setDefaultStyleSheet("""
            h1, h2 { color: #ffffff; }
            p, li { color: #d6d6d6; font-family: 'Segoe UI', sans-serif; font-size: 12px; }
            code { background-color: #31353b; padding: 2px 4px; border-radius: 4px; color: #ffe07d; }
        """)
        self.review_display.setStyleSheet("""
            QTextEdit {
                font-size: 12px;
                background-color: transparent;
                border: none;
                padding: 6px 2px 2px 2px;
            }
        """ + QUALITY_GATE_SCROLLBAR_STYLE)
        review_layout.addWidget(self.review_display, stretch=1)

        fix_panel = QWidget()
        fix_layout = QVBoxLayout(fix_panel)
        fix_layout.setContentsMargins(0, 0, 0, 0)
        fix_layout.setSpacing(8)

        fix_header = QHBoxLayout()
        fix_header.setContentsMargins(0, 0, 0, 0)
        fix_title = QLabel("Launch the highest-value remediation or validation nodes directly from this list.")
        fix_title.setObjectName("qualityGateSectionHint")
        fix_title.setWordWrap(True)
        fix_header.addWidget(fix_title, 1)
        fix_header.addStretch()
        self.fix_count_header_label = QLabel("0")
        self.fix_count_header_label.setObjectName("qualityGateMetricBadge")
        fix_header.addWidget(self.fix_count_header_label)
        fix_layout.addLayout(fix_header)

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
        """ + QUALITY_GATE_SCROLLBAR_STYLE)

        self.recommendation_content = QWidget()
        self.recommendation_content.setStyleSheet("background: transparent;")
        self.recommendation_layout = QVBoxLayout(self.recommendation_content)
        self.recommendation_layout.setContentsMargins(0, 6, 0, 0)
        self.recommendation_layout.setSpacing(8)
        self.recommendation_layout.addWidget(self._build_empty_state())
        self.recommendation_layout.addStretch()
        self.recommendation_scroll.setWidget(self.recommendation_content)
        fix_layout.addWidget(self.recommendation_scroll)

        self.workspace_tabs.addTab(review_panel, qta.icon("fa5s.clipboard-list", color="#cccccc"), "Review")
        self.workspace_tabs.addTab(fix_panel, qta.icon("fa5s.tools", color="#cccccc"), "Fix Paths (0)")
        main_layout.addWidget(self.workspace_tabs, stretch=1)

        button_style = f"""
            QPushButton {{
                background-color: {node_color.name()};
                color: {button_text_color};
                border: none;
                border-radius: 8px;
                padding: 9px 16px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {node_color.lighter(108).name()};
            }}
            QPushButton:disabled {{
                background-color: #555555;
                color: #cccccc;
            }}
        """
        self.run_button.setStyleSheet(button_style)
        self.note_button.setStyleSheet(button_style)

        self.refresh_branch_context()
        self._apply_verdict_badge("pending")
        self.set_status("Idle")

    def _build_empty_state(self):
        label = QLabel("No fix paths yet. Run Quality Gate to generate a readiness review and the highest-value follow-up nodes.")
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

    def _on_criteria_changed(self):
        self.criteria = self.criteria_input.toPlainText()

    def get_goal(self):
        return self.goal_input.toPlainText()

    def seed_prompt(self, text):
        """Protocol method used by graphite_window_actions.instantiate_seeded_plugin."""
        self.goal_input.setPlainText(text)

    def get_criteria(self):
        return self.criteria_input.toPlainText()

    def get_review_payload(self):
        include_branch_context = bool(getattr(self, "include_branch_context", True))
        source_node = self.parent_node if include_branch_context and self.parent_node else self
        return build_quality_gate_payload(source_node, include_branch_context=include_branch_context)

    def refresh_branch_context(self):
        payload = self.get_review_payload()
        context_state = "On" if bool(getattr(self, "include_branch_context", True)) else "Off"
        label = f"{payload.get('label', 'Current Branch')} - Depth {payload.get('depth', 0)} - Context {context_state}"
        self.branch_label.setText(label)
        self.branch_label.setToolTip("\n".join(payload.get("node_labels", [])) or payload.get("label", "Current Branch"))

    def _apply_verdict_badge(self, verdict):
        verdict = (verdict or "pending").lower()
        if verdict == "ready":
            color = get_semantic_color("status_success")
            text = "Verdict: Ready"
        elif verdict == "blocked":
            color = get_semantic_color("status_error")
            text = "Verdict: Blocked"
        elif verdict == "needs_work":
            color = get_semantic_color("status_warning")
            text = "Verdict: Needs Work"
        else:
            color = QColor("#9aa3ad")
            text = "Verdict: Pending"

        self.verdict_label.setText(text)
        self.verdict_label.setStyleSheet(
            f"color: {color.name()}; background-color: rgba({color.red()}, {color.green()}, {color.blue()}, 0.1); "
            f"border: 1px solid rgba({color.red()}, {color.green()}, {color.blue()}, 0.22); border-radius: 10px; "
            "padding: 3px 8px; font-size: 11px; font-weight: bold;"
        )

    def _apply_score_badge(self):
        color = get_semantic_color("status_success") if self.readiness_score >= 85 else get_semantic_color("status_warning")
        if self.readiness_score < 55:
            color = get_semantic_color("status_error")
        self.score_label.setText(f"Readiness: {self.readiness_score}/100" if self.readiness_score else "Readiness: --")
        self.score_label.setStyleSheet(
            f"color: {color.name()}; background-color: rgba({color.red()}, {color.green()}, {color.blue()}, 0.1); "
            f"border: 1px solid rgba({color.red()}, {color.green()}, {color.blue()}, 0.22); border-radius: 10px; "
            "padding: 3px 8px; font-size: 11px; font-weight: bold;"
        )

    def _apply_fix_count_badge(self):
        color = QColor(get_current_palette().FRAME_COLORS["Yellow"]["color"])
        self.fix_count_label.setText(f"Fix Paths: {len(self.recommendations)}")
        self.fix_count_header_label.setText(str(len(self.recommendations)))
        style = (
            f"color: {color.name()}; background-color: rgba({color.red()}, {color.green()}, {color.blue()}, 0.12); "
            f"border: 1px solid rgba({color.red()}, {color.green()}, {color.blue()}, 0.24); border-radius: 10px; "
            "padding: 3px 8px; font-size: 11px; font-weight: bold;"
        )
        self.fix_count_label.setStyleSheet(style)
        self.fix_count_header_label.setStyleSheet(style)

    def set_running_state(self, is_running):
        if self.is_disposed:
            return

        self.run_button.setEnabled(not is_running)
        self.note_button.setEnabled(bool(self.note_summary.strip()) and not is_running)
        self.goal_input.setReadOnly(is_running)
        self.criteria_input.setReadOnly(is_running)
        self.run_button.setText("Reviewing..." if is_running else "Run Quality Gate")

        if is_running:
            self.set_status("Reviewing production readiness...")
        elif self.status.startswith("Error") or self.status in {"Ready to Ship", "Needs Work", "Blocked"}:
            return
        elif self.review_markdown.strip():
            self.set_status("Completed")
        else:
            self.set_status("Idle")

    def set_status(self, status_text):
        if self.is_disposed:
            return

        self.status = status_text
        self.status_label.setText(status_text)
        if "Reviewing" in status_text:
            color = get_semantic_color("status_info")
        elif "Ready" in status_text:
            color = get_semantic_color("status_success")
        elif "Needs Work" in status_text:
            color = get_semantic_color("status_warning")
        elif "Blocked" in status_text or "Error" in status_text:
            color = get_semantic_color("status_error")
        elif "Completed" in status_text:
            color = get_semantic_color("status_warning")
        else:
            color = QColor("#9aa3ad")

        self.status_label.setStyleSheet(
            f"color: {color.name()}; background-color: rgba({color.red()}, {color.green()}, {color.blue()}, 0.1); "
            f"border: 1px solid rgba({color.red()}, {color.green()}, {color.blue()}, 0.22); border-radius: 10px; "
            "padding: 3px 8px; font-size: 11px; font-weight: bold;"
        )

    def set_review(self, review):
        if self.is_disposed:
            return

        self.verdict = review.get("verdict", "needs_work")
        try:
            self.readiness_score = int(review.get("readiness_score", 0) or 0)
        except (TypeError, ValueError):
            self.readiness_score = 0
        self.review_markdown = review.get("review_markdown", "")
        self.note_summary = review.get("note_summary", "")
        self.recommendations = review.get("recommended_plugins", [])

        self.review_display.setMarkdown(self.review_markdown)
        self.workspace_tabs.setTabText(1, f"Fix Paths ({len(self.recommendations)})")
        self.note_button.setEnabled(bool(self.note_summary.strip()))

        self._apply_verdict_badge(self.verdict)
        self._apply_score_badge()
        self._apply_fix_count_badge()

        palette = get_current_palette()
        accent_color = palette.FRAME_COLORS["Yellow"]["color"]
        self._clear_recommendations()

        if not self.recommendations:
            self.recommendation_layout.addWidget(self._build_empty_state())
        else:
            for recommendation in self.recommendations:
                card = QualityGateRecommendationCard(recommendation, accent_color)
                card.add_requested.connect(lambda plugin, prompt, node=self: self.plugin_requested.emit(node, plugin, prompt))
                self.recommendation_layout.addWidget(card)
        self.recommendation_layout.addStretch()

        if self.verdict == "ready":
            self.set_status("Ready to Ship")
        elif self.verdict == "blocked":
            self.set_status("Blocked")
        else:
            self.set_status("Needs Work")

    def set_error(self, error_message):
        if self.is_disposed:
            return

        self.review_markdown = f"## Error\n\n{error_message}"
        self.note_summary = ""
        self.recommendations = []
        self.readiness_score = 0
        self.review_display.setMarkdown(self.review_markdown)
        self.note_button.setEnabled(False)
        self._apply_verdict_badge("blocked")
        self._apply_score_badge()
        self._apply_fix_count_badge()
        self.workspace_tabs.setTabText(1, "Fix Paths (0)")
        self._clear_recommendations()
        self.recommendation_layout.addWidget(self._build_empty_state())
        self.recommendation_layout.addStretch()
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
        node_color = QColor(palette.FRAME_COLORS["Yellow"]["color"])

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
            painter.drawText(QRectF(40, 0, self.width - 80, self.height), Qt.AlignmentFlag.AlignVCenter, "Quality Gate")
            qta.icon("fa5s.check-circle", color=node_color.name()).paint(painter, QRect(10, 10, 20, 20))
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

    def contextMenuEvent(self, event):
        menu = PluginNodeContextMenu(self)
        menu.exec(event.screenPos())

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
