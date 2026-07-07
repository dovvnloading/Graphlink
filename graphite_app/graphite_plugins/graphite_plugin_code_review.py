import ast
import base64
import json
import re
from pathlib import Path
from urllib.parse import quote

import requests
import qtawesome as qta
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsObject,
    QGraphicsProxyWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import api_provider
import graphite_config as config
from graphite_canvas_items import HoverAnimationMixin
from graphite_config import get_current_palette, get_semantic_color
from graphite_connections import ConnectionItem
from graphite_plugin_context_menu import PluginNodeContextMenu


class CodeReviewComboPopup(QFrame):
    item_selected = Signal(int, str)
    popup_closed = Signal()

    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setObjectName("codeReviewComboPopupFrame")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._owner_combo = None
        self._close_monitor_active = False

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self.shell = QFrame()
        self.shell.setObjectName("codeReviewComboPopupShell")
        outer_layout.addWidget(self.shell)

        shell_layout = QVBoxLayout(self.shell)
        shell_layout.setContentsMargins(4, 4, 4, 4)
        shell_layout.setSpacing(0)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("codeReviewComboPopupList")
        self.list_widget.setFrameShape(QFrame.Shape.NoFrame)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.list_widget.setSpacing(2)
        self.list_widget.setMouseTracking(True)
        self.list_widget.itemClicked.connect(self._emit_item_selection)
        self.list_widget.itemActivated.connect(self._emit_item_selection)
        shell_layout.addWidget(self.list_widget)

        self.apply_style()

    def apply_style(self, accent_color="#2d6fa3"):
        self.setStyleSheet(
            f"""
            QFrame#codeReviewComboPopupFrame {{
                background-color: #1f2327;
                border: 1px solid #353b43;
                border-radius: 10px;
            }}
            QFrame#codeReviewComboPopupShell {{
                background: transparent;
                border: none;
            }}
            QListWidget#codeReviewComboPopupList {{
                background: transparent;
                color: #ffffff;
                border: none;
                outline: none;
                padding: 2px;
            }}
            QListWidget#codeReviewComboPopupList::item {{
                background: transparent;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                min-height: 26px;
                padding: 6px 10px;
            }}
            QListWidget#codeReviewComboPopupList::item:hover {{
                background-color: #2a3037;
            }}
            QListWidget#codeReviewComboPopupList::item:selected {{
                background-color: {accent_color};
                color: #ffffff;
            }}
            QListWidget#codeReviewComboPopupList::item:selected:hover {{
                background-color: {accent_color};
                color: #ffffff;
            }}
            """
        )

    def populate_from_combo(self, combo):
        current_index = combo.currentIndex()
        current_text = combo.currentText()

        self.list_widget.clear()
        for index in range(combo.count()):
            text = combo.itemText(index)
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.list_widget.addItem(item)

        if current_index < 0 and current_text:
            current_index = combo.findText(current_text)

        if 0 <= current_index < self.list_widget.count():
            self.list_widget.setCurrentRow(current_index)
            current_item = self.list_widget.item(current_index)
            if current_item is not None:
                self.list_widget.scrollToItem(
                    current_item,
                    QAbstractItemView.ScrollHint.PositionAtCenter,
                )
        else:
            self.list_widget.clearSelection()

    def _screen_anchor_rect(self, combo):
        host_widget = combo.window()
        proxy = None
        if host_widget is not None and hasattr(host_widget, "graphicsProxyWidget"):
            proxy = host_widget.graphicsProxyWidget()

        if proxy is not None and proxy.scene() is not None and proxy.scene().views():
            view = proxy.scene().views()[0]
            top_left_host = combo.mapTo(host_widget, QPoint(0, 0))
            top_right_host = combo.mapTo(host_widget, QPoint(combo.width(), 0))
            bottom_left_host = combo.mapTo(host_widget, QPoint(0, combo.height()))

            top_left_scene = proxy.mapToScene(QPointF(top_left_host))
            top_right_scene = proxy.mapToScene(QPointF(top_right_host))
            bottom_left_scene = proxy.mapToScene(QPointF(bottom_left_host))

            top_left_view = view.mapFromScene(top_left_scene)
            top_right_view = view.mapFromScene(top_right_scene)
            bottom_left_view = view.mapFromScene(bottom_left_scene)

            top_left_global = view.viewport().mapToGlobal(top_left_view)
            width = max(1, abs(top_right_view.x() - top_left_view.x()))
            height = max(1, abs(bottom_left_view.y() - top_left_view.y()))
            top_level_window = view.viewport().window()
            if top_level_window is None:
                view_window_attr = getattr(view, "window", None)
                if callable(view_window_attr):
                    top_level_window = view_window_attr()
                else:
                    top_level_window = view_window_attr
            return QRect(top_left_global, QSize(width, height)), top_level_window

        top_left_global = combo.mapToGlobal(QPoint(0, 0))
        return QRect(top_left_global, combo.size()), combo.window()

    def show_for_combo(self, combo):
        self._owner_combo = combo
        anchor_rect, top_level_window = self._screen_anchor_rect(combo)
        if top_level_window is not None and self.parentWidget() is not top_level_window:
            self.setParent(top_level_window, self.windowFlags())

        self.populate_from_combo(combo)
        if self.list_widget.count() == 0:
            return

        font_metrics = combo.fontMetrics()
        max_text_width = 0
        for index in range(combo.count()):
            max_text_width = max(max_text_width, font_metrics.horizontalAdvance(combo.itemText(index)))

        row_height = self.list_widget.sizeHintForRow(0)
        if row_height <= 0:
            row_height = 34

        visible_rows = min(max(self.list_widget.count(), 1), 10)
        popup_width = max(anchor_rect.width(), min(max_text_width + 56, 560))
        popup_height = (visible_rows * row_height) + 22
        self.resize(popup_width, popup_height)

        target_global = anchor_rect.bottomLeft() + QPoint(0, 4)
        screen = QGuiApplication.screenAt(target_global) or QGuiApplication.primaryScreen()
        available_geometry = screen.availableGeometry() if screen else None

        x = target_global.x()
        y = target_global.y()

        if available_geometry is not None:
            if x + self.width() > available_geometry.right() - 12:
                x = available_geometry.right() - self.width() - 12

            if y + self.height() > available_geometry.bottom() - 12:
                above_global = combo.mapToGlobal(QPoint(0, -(self.height() + 4)))
                y = max(available_geometry.top() + 12, above_global.y())

            x = max(available_geometry.left() + 12, x)
            y = max(available_geometry.top() + 12, y)

        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self.list_widget.setFocus()
        QTimer.singleShot(0, self._start_close_monitor)

    def _emit_item_selection(self, item):
        if item is None:
            return
        index = item.data(Qt.ItemDataRole.UserRole)
        self.item_selected.emit(index, item.text())

    def _start_close_monitor(self):
        if not self.isVisible() or self._close_monitor_active:
            return
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            self._close_monitor_active = True

    def eventFilter(self, watched, event):
        if not self.isVisible():
            return False

        if event.type() not in {QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick}:
            return False

        global_pos = event.globalPosition().toPoint()
        if self.frameGeometry().contains(global_pos):
            return False

        if self._owner_combo is not None:
            combo_anchor_rect, _ = self._screen_anchor_rect(self._owner_combo)
            if combo_anchor_rect.contains(global_pos):
                self.hide()
                return True

        self.hide()
        return False

    def hideEvent(self, event):
        if self._close_monitor_active:
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
            self._close_monitor_active = False
        self.popup_closed.emit()
        super().hideEvent(event)

    def focusOutEvent(self, event):
        self.hide()
        super().focusOutEvent(event)


class CodeReviewPopupComboBox(QComboBox):
    about_to_show_popup = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._popup = CodeReviewComboPopup()
        self._popup.item_selected.connect(self._apply_popup_selection)
        self._popup.popup_closed.connect(self._handle_popup_closed)
        self._popup_closing = False
        self.destroyed.connect(self._cleanup_popup)

    def apply_popup_style(self, accent_color):
        self._popup.apply_style(accent_color)

    def showPopup(self):
        self.about_to_show_popup.emit()
        if self._popup is not None and self._popup.isVisible():
            self.hidePopup()
            return
        if not self.isEnabled() or self.count() == 0:
            return
        self._popup.show_for_combo(self)

    def hidePopup(self):
        if self._popup is not None and self._popup.isVisible():
            self._popup_closing = True
            self._popup.hide()
            self._popup_closing = False
        super().hidePopup()

    def _apply_popup_selection(self, index, text):
        if 0 <= index < self.count():
            self.setCurrentIndex(index)
        else:
            self.setCurrentText(text)
        self.hidePopup()
        self.setFocus()

    def _handle_popup_closed(self):
        if not self._popup_closing:
            super().hidePopup()

    def _cleanup_popup(self):
        if self._popup is not None:
            self._popup.hide()
            self._popup.deleteLater()
            self._popup = None


CODE_REVIEW_SCROLLBAR_STYLE = """
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
"""

REVIEW_CATEGORY_WEIGHTS = {
    "correctness": 24,
    "reliability": 16,
    "security": 14,
    "maintainability": 14,
    "readability": 10,
    "testing": 10,
    "performance": 6,
    "architecture": 6,
}

REVIEW_CATEGORY_LABELS = {
    "correctness": "Correctness",
    "reliability": "Reliability",
    "security": "Security",
    "maintainability": "Maintainability",
    "readability": "Readability",
    "testing": "Testing",
    "performance": "Performance",
    "architecture": "Architecture",
}

SEVERITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}

TEXT_FILE_EXCLUSION_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp", ".pdf",
    ".zip", ".tar", ".gz", ".7z", ".rar", ".mp3", ".wav", ".ogg", ".mp4", ".mov",
    ".avi", ".webm", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".exe", ".dll",
    ".so", ".dylib", ".class", ".jar", ".pyc", ".pyd", ".bin", ".dat", ".db",
)

CODE_REVIEW_METRIC_MARKDOWN = """## Deterministic Review Metric

This plugin uses a fixed, repeatable rubric before the model is allowed to grade the file.

### Preflight Gate

1. Confirm the source is present, readable, and large enough to review.
2. Identify the file's likely language, runtime, and execution boundary.
3. Note whether the review sees the full file or a truncated excerpt.
4. Identify external assumptions: imports, environment variables, network calls, filesystem access, framework hooks.
5. Decide whether there is enough evidence to score each category fairly. If not, mark the gap instead of guessing.

### Required Inspection Sequence

1. Trace the happy-path control flow from input to output.
2. Check edge cases, null/empty states, and failure branches.
3. Inspect error handling, retries, cleanup, and state consistency.
4. Inspect secrets, auth, injection risk, unsafe execution, and trust boundaries.
5. Inspect data contracts, side effects, and dependency assumptions.
6. Inspect readability, cohesion, naming, duplication, and complexity.
7. Inspect tests, observability, and how the code could be validated.
8. Inspect performance hotspots only where the visible code suggests a real risk.
9. Separate high-confidence errors from lower-confidence review findings.
10. Produce scores from the fixed weights below instead of ad hoc scoring.

### Weighted Scorecard

- Correctness: 24%
- Reliability: 16%
- Security: 14%
- Maintainability: 14%
- Readability: 10%
- Testing: 10%
- Performance: 6%
- Architecture: 6%

### Verdict Gates

- `Strong`: weighted score >= 78, no critical errors, no high-severity findings.
- `Needs Revision`: weighted score 60-77, or at least one high-confidence error, or at least one high-severity finding.
- `Not Ready`: weighted score < 60, or at least one critical error.

### Output Contract

- Overview: short executive review of what matters most.
- Review Findings: evidence-backed issues ordered by severity.
- Errors Found: only high-confidence bugs / faults / security defects.
- Code Quality Report: deterministic weighted score plus release risk.
"""


def _clean_text(value, limit=None):
    text = str(value or "").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if limit and len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _clamp_score(value, default=70):
    try:
        numeric = int(round(float(value)))
    except (TypeError, ValueError):
        numeric = default
    return max(0, min(100, numeric))


def _severity_key(value):
    severity = _clean_text(value, limit=20).lower()
    return severity if severity in SEVERITY_ORDER else "medium"


def _titleize_key(value):
    cleaned = re.sub(r"[_-]+", " ", _clean_text(value, limit=80)).strip()
    return cleaned.title() if cleaned else "General"


def _looks_like_python(source_state, source_text):
    path = (
        source_state.get("path")
        or source_state.get("local_path")
        or source_state.get("label")
        or ""
    ).lower()
    if path.endswith(".py"):
        return True
    tokens = ("def ", "class ", "import ", "from ", "async def ")
    return sum(1 for token in tokens if token in source_text) >= 2


def _decode_text_bytes(raw_bytes):
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def _prepare_numbered_source(source_text, max_chars=40000):
    lines = source_text.splitlines() or [source_text]
    total_lines = len(lines)
    visible_lines = []
    current_length = 0

    for index, line in enumerate(lines, start=1):
        numbered_line = f"{index:04d}: {line}"
        projected = current_length + len(numbered_line) + 1
        if visible_lines and projected > max_chars:
            break
        visible_lines.append(numbered_line)
        current_length = projected

    truncated = len(visible_lines) < total_lines
    return "\n".join(visible_lines), truncated, total_lines, len(visible_lines)


def _is_reviewable_repo_path(path_text):
    lowered = path_text.lower()
    return not lowered.endswith(TEXT_FILE_EXCLUSION_SUFFIXES)


def _source_origin_label(source_state):
    origin = source_state.get("origin", "")
    if origin == "github":
        repo = source_state.get("repo", "")
        branch = source_state.get("branch", "")
        file_path = source_state.get("path", "")
        parts = [part for part in (repo, branch, file_path) if part]
        return f"GitHub: {' / '.join(parts)}" if parts else "GitHub file"
    if origin == "local":
        return f"Local File: {source_state.get('local_path', '') or source_state.get('label', 'Loaded file')}"
    if origin == "manual":
        return "Manual / pasted source"
    return "No source selected"


def _compact_label_text(text, limit=34):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _source_scope_summary(payload):
    source_state = payload.get("source_state", {})
    context_text = _clean_text(payload.get("review_context", ""), limit=400)
    numbered_source = payload.get("source_for_model", "")
    summary_lines = [
        f"- Source: {_source_origin_label(source_state)}",
        f"- Total lines loaded: {payload.get('total_lines', 0)}",
        f"- Visible lines reviewed by the model: {payload.get('visible_lines', 0)}",
        f"- Full file visible to model: {'No' if payload.get('source_truncated') else 'Yes'}",
    ]
    if context_text:
        summary_lines.append(f"- Review context: {context_text}")
    if source_state.get("edited"):
        summary_lines.append("- Loaded source was manually edited inside the plugin before review.")
    if not numbered_source.strip():
        summary_lines.append("- Source excerpt: unavailable")
    return "\n".join(summary_lines)


class CodeReviewAnalyzer:
    SYSTEM_PROMPT = f"""
You are Graphlink's Code Review Agent.

Your job is to produce a disciplined, repeatable single-file code review.
You must use the exact checklist and weighted scoring model below instead of inventing a new rubric each time.

{CODE_REVIEW_METRIC_MARKDOWN}

Rules:
1. Be evidence-driven. Do not invent dependencies, tests, runtime behavior, or unseen files.
2. Separate high-confidence errors from broader review findings.
3. High-confidence errors must be concrete faults such as syntax problems, likely runtime failures, security defects, or clearly broken logic.
4. Review findings can include maintainability, readability, testing, or architectural concerns, but still require visible evidence.
5. If the source is truncated, only review what is visible and explicitly mention the visibility limit.
6. Avoid low-value stylistic nitpicks unless they materially affect readability, safety, maintainability, or correctness.
7. Output valid JSON only. No markdown fences, no commentary outside the JSON object.

Return exactly this shape:
{{
  "title": "Short review title",
  "overview": "2-4 sentence executive summary",
  "confidence": "high",
  "preflight_checks": [
    {{
      "check": "Source completeness",
      "status": "pass",
      "details": "What was verified before scoring"
    }}
  ],
  "review_findings": [
    {{
      "severity": "medium",
      "category": "maintainability",
      "title": "Short finding title",
      "evidence": "Visible code evidence only",
      "impact": "Why this matters",
      "recommendation": "Concrete improvement"
    }}
  ],
  "errors_found": [
    {{
      "severity": "high",
      "kind": "runtime",
      "title": "Short error title",
      "evidence": "Visible code evidence only",
      "fix": "Concrete remediation"
    }}
  ],
  "category_scores": {{
    "correctness": 80,
    "reliability": 78,
    "security": 86,
    "maintainability": 74,
    "readability": 81,
    "testing": 62,
    "performance": 76,
    "architecture": 73
  }},
  "quality_summary": "Short synthesis that aligns with the findings and scores"
}}
"""

    def _extract_json(self, raw_text):
        code_block_match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", raw_text, re.IGNORECASE)
        if code_block_match:
            return code_block_match.group(1).strip()

        object_match = re.search(r"(\{[\s\S]*\})", raw_text)
        if object_match:
            return object_match.group(1).strip()
        return raw_text.strip()

    def _normalize_preflight(self, payload, checks):
        default_checks = [
            {
                "check": "Source loaded",
                "status": "pass" if payload.get("source_text", "").strip() else "fail",
                "details": "The plugin has source text to inspect." if payload.get("source_text", "").strip() else "No source text was supplied.",
            },
            {
                "check": "Language/runtime identified",
                "status": "pass" if payload.get("source_state", {}).get("label") or payload.get("source_text", "").strip() else "warn",
                "details": "The reviewer can infer likely runtime context from the file path or visible syntax.",
            },
            {
                "check": "Visibility limit assessed",
                "status": "warn" if payload.get("source_truncated") else "pass",
                "details": "The review only sees a truncated excerpt." if payload.get("source_truncated") else "The full file is available to the reviewer.",
            },
            {
                "check": "External assumptions noted",
                "status": "pass",
                "details": "Imports, filesystem access, network calls, and framework assumptions must be treated as explicit review surface.",
            },
            {
                "check": "Scoring evidence threshold",
                "status": "pass" if payload.get("visible_lines", 0) >= 5 else "warn",
                "details": "There is enough visible source to score the file with reasonable confidence." if payload.get("visible_lines", 0) >= 5 else "The file is sparse, so scoring confidence is lower.",
            },
        ]

        normalized = []
        for item in checks or []:
            if not isinstance(item, dict):
                continue
            status = _clean_text(item.get("status"), limit=20).lower()
            if status not in {"pass", "warn", "fail"}:
                status = "warn"
            normalized.append({
                "check": _clean_text(item.get("check"), limit=120) or "Unnamed preflight check",
                "status": status,
                "details": _clean_text(item.get("details"), limit=280) or "No details supplied.",
            })

        if len(normalized) < 5:
            for default_item in default_checks:
                if not any(existing["check"] == default_item["check"] for existing in normalized):
                    normalized.append(default_item)
        return normalized[:8]

    def _normalize_findings(self, findings, is_error_list=False):
        normalized = []
        for item in findings or []:
            if not isinstance(item, dict):
                continue
            severity = _severity_key(item.get("severity"))
            title = _clean_text(item.get("title"), limit=120)
            evidence = _clean_text(item.get("evidence"), limit=420)
            if not title or not evidence:
                continue

            normalized_item = {
                "severity": severity,
                "title": title,
                "evidence": evidence,
            }

            if is_error_list:
                normalized_item["kind"] = _titleize_key(item.get("kind") or item.get("category") or "runtime")
                normalized_item["fix"] = _clean_text(item.get("fix"), limit=320) or "Address the visible root cause and re-run validation."
            else:
                normalized_item["category"] = _titleize_key(item.get("category") or "general")
                normalized_item["impact"] = _clean_text(item.get("impact"), limit=320) or "This issue reduces confidence in the file's quality or safety."
                normalized_item["recommendation"] = _clean_text(item.get("recommendation"), limit=320) or "Tighten the implementation and add verification for this path."

            normalized.append(normalized_item)

        normalized.sort(key=lambda item: (SEVERITY_ORDER.get(item["severity"], 5), item["title"]))
        return normalized[:10]

    def _normalize_scores(self, parsed_scores):
        scores = {}
        for key in REVIEW_CATEGORY_WEIGHTS:
            scores[key] = _clamp_score((parsed_scores or {}).get(key), default=72)
        return scores

    def _compute_weighted_score(self, category_scores):
        weighted_total = 0.0
        for key, weight in REVIEW_CATEGORY_WEIGHTS.items():
            weighted_total += category_scores[key] * (weight / 100.0)
        return int(round(weighted_total))

    def _derive_verdict(self, overall_score, findings, errors):
        critical_errors = sum(1 for item in errors if item["severity"] == "critical")
        high_errors = sum(1 for item in errors if item["severity"] == "high")
        high_findings = sum(1 for item in findings if item["severity"] in {"critical", "high"})

        if critical_errors > 0 or overall_score < 60:
            verdict = "not_ready"
        elif high_errors > 0 or high_findings > 0 or overall_score < 78:
            verdict = "needs_revision"
        else:
            verdict = "strong"

        if critical_errors > 0 or overall_score < 60:
            risk = "high"
        elif high_errors > 0 or overall_score < 78:
            risk = "medium"
        else:
            risk = "low"
        return verdict, risk

    def _build_overview_markdown(self, normalized, payload):
        preflight_lines = []
        for item in normalized["preflight_checks"]:
            status = item["status"].upper()
            preflight_lines.append(f"- `{status}` {item['check']}: {item['details']}")

        return "\n".join([
            "## Review Overview",
            "",
            normalized["overview"],
            "",
            "### Review Scope",
            _source_scope_summary(payload),
            "",
            "### Preflight Checklist",
            *preflight_lines,
        ])

    def _build_findings_markdown(self, normalized):
        findings = normalized["review_findings"]
        if not findings:
            return "\n".join([
                "## Review Findings",
                "",
                "No additional evidence-backed review findings were identified beyond the high-confidence errors list.",
            ])

        lines = ["## Review Findings", ""]
        for index, finding in enumerate(findings, start=1):
            lines.extend([
                f"### {index}. [{finding['severity'].upper()}] {finding['title']}",
                f"- Category: {finding['category']}",
                f"- Evidence: {finding['evidence']}",
                f"- Impact: {finding['impact']}",
                f"- Recommendation: {finding['recommendation']}",
                "",
            ])
        return "\n".join(lines).rstrip()

    def _build_errors_markdown(self, normalized):
        errors = normalized["errors_found"]
        if not errors:
            return "\n".join([
                "## Errors Found",
                "",
                "No high-confidence errors were identified from the visible source.",
            ])

        lines = ["## Errors Found", ""]
        for index, error in enumerate(errors, start=1):
            lines.extend([
                f"### {index}. [{error['severity'].upper()}] {error['title']}",
                f"- Kind: {error['kind']}",
                f"- Evidence: {error['evidence']}",
                f"- Fix: {error['fix']}",
                "",
            ])
        return "\n".join(lines).rstrip()

    def _build_quality_markdown(self, normalized):
        score_lines = []
        for key in REVIEW_CATEGORY_WEIGHTS:
            score_lines.append(
                f"- {REVIEW_CATEGORY_LABELS[key]} ({REVIEW_CATEGORY_WEIGHTS[key]}%): {normalized['category_scores'][key]}/100"
            )

        verdict_label = normalized["verdict"].replace("_", " ").title()
        confidence_label = normalized["confidence"].title()
        risk_label = normalized["risk_level"].title()

        return "\n".join([
            "## Code Quality Report",
            "",
            f"- Deterministic weighted score: {normalized['quality_score']}/100",
            f"- Verdict: {verdict_label}",
            f"- Confidence: {confidence_label}",
            f"- Release risk: {risk_label}",
            "",
            "### Weighted Scorecard",
            *score_lines,
            "",
            "### Summary",
            normalized["quality_summary"],
            "",
            "### Verdict Logic",
            "- `Strong`: score >= 78, no critical errors, no high-severity findings.",
            "- `Needs Revision`: score 60-77, or any high-confidence error, or any high-severity finding.",
            "- `Not Ready`: score < 60, or any critical error.",
        ])

    def _build_combined_markdown(self, overview_markdown, findings_markdown, errors_markdown, quality_markdown):
        return "\n\n".join([
            overview_markdown,
            findings_markdown,
            errors_markdown,
            quality_markdown,
        ])

    def _build_quality_summary(self, normalized):
        findings_count = len(normalized["review_findings"])
        errors_count = len(normalized["errors_found"])
        strongest_category = max(normalized["category_scores"], key=lambda key: normalized["category_scores"][key])
        weakest_category = min(normalized["category_scores"], key=lambda key: normalized["category_scores"][key])

        summary = _clean_text(normalized.get("quality_summary"), limit=420)
        if summary:
            return summary

        return (
            f"The file scores strongest in {REVIEW_CATEGORY_LABELS[strongest_category].lower()} "
            f"and weakest in {REVIEW_CATEGORY_LABELS[weakest_category].lower()}. "
            f"The review surfaced {findings_count} broader findings and {errors_count} high-confidence errors."
        )

    def _fallback_review(self, payload, exception_text=None):
        source_text = payload.get("source_text", "")
        source_state = payload.get("source_state", {})
        findings = []
        errors = []
        scores = {key: 82 for key in REVIEW_CATEGORY_WEIGHTS}

        def add_finding(severity, category, title, evidence, impact, recommendation):
            findings.append({
                "severity": severity,
                "category": category,
                "title": title,
                "evidence": evidence,
                "impact": impact,
                "recommendation": recommendation,
            })

        def add_error(severity, kind, title, evidence, fix):
            errors.append({
                "severity": severity,
                "kind": kind,
                "title": title,
                "evidence": evidence,
                "fix": fix,
            })

        if _looks_like_python(source_state, source_text):
            try:
                ast.parse(source_text)
            except SyntaxError as exc:
                evidence = f"Python parser raised a syntax error near line {exc.lineno}: {exc.msg}."
                add_error(
                    "critical",
                    "Syntax",
                    "Python syntax error prevents execution",
                    evidence,
                    "Fix the syntax error before running or reviewing downstream behavior.",
                )
                scores["correctness"] = min(scores["correctness"], 25)
                scores["reliability"] = min(scores["reliability"], 30)
                scores["maintainability"] = min(scores["maintainability"], 38)

        if re.search(r"(api[_-]?key|secret|token|password)\s*=\s*['\"][^'\"]+['\"]", source_text, re.IGNORECASE):
            add_error(
                "high",
                "Security",
                "Hard-coded secret-like value detected",
                "The file appears to assign a literal value to a secret-like variable name.",
                "Move the value to secure configuration or environment-based secret management.",
            )
            scores["security"] = min(scores["security"], 35)
            scores["maintainability"] = min(scores["maintainability"], 55)

        if re.search(r"\b(eval|exec)\s*\(", source_text):
            add_finding(
                "high",
                "security",
                "Dynamic code execution increases risk",
                "The file calls `eval(...)` or `exec(...)` directly.",
                "Dynamic execution expands injection and debugging risk.",
                "Replace dynamic execution with explicit parsing or a constrained execution strategy.",
            )
            scores["security"] = min(scores["security"], 40)

        if re.search(r"subprocess\.(Popen|run)\(.*shell\s*=\s*True", source_text, re.IGNORECASE | re.DOTALL) or "os.system(" in source_text:
            add_finding(
                "high",
                "security",
                "Shell execution path requires strict input control",
                "The file invokes a shell command path from code.",
                "Shell execution becomes dangerous if any untrusted input reaches the command.",
                "Prefer argument lists, validate inputs, and avoid shell invocation when possible.",
            )
            scores["security"] = min(scores["security"], 45)

        if re.search(r"except\s*:\s*\n", source_text):
            add_finding(
                "medium",
                "reliability",
                "Bare exception handler hides root causes",
                "The file contains a bare `except:` block.",
                "Bare exception handling can swallow unrelated failures and make debugging harder.",
                "Catch only expected exception types and log or re-raise unexpected ones.",
            )
            scores["reliability"] = min(scores["reliability"], 60)

        if re.search(r"except\s+Exception\s*:\s*pass", source_text):
            add_error(
                "high",
                "Reliability",
                "Exception is silently discarded",
                "The file uses `except Exception: pass`, which hides execution failures.",
                "Handle the exception explicitly or surface the failure so the caller can react.",
            )
            scores["reliability"] = min(scores["reliability"], 42)

        if re.search(r"\b(TODO|FIXME)\b", source_text):
            add_finding(
                "low",
                "maintainability",
                "Outstanding TODO or FIXME markers remain in the file",
                "The visible source still contains TODO/FIXME markers.",
                "Open TODO markers often indicate unfinished edge cases or deferred cleanup.",
                "Either resolve the pending work or convert the note into a tracked issue with clear ownership.",
            )
            scores["maintainability"] = min(scores["maintainability"], 72)

        if re.search(r"\b(print|console\.log)\s*\(", source_text):
            add_finding(
                "low",
                "readability",
                "Debug logging remains in the file",
                "The visible source includes raw debug logging calls.",
                "Ad hoc logging can add noise and make production behavior harder to reason about.",
                "Replace debug prints with structured logging or remove them before release.",
            )
            scores["readability"] = min(scores["readability"], 74)

        long_line_count = sum(1 for line in source_text.splitlines() if len(line) > 140)
        if long_line_count >= 5:
            add_finding(
                "low",
                "readability",
                "Several lines exceed a maintainable width",
                f"The file contains {long_line_count} lines longer than 140 characters.",
                "Very long lines usually hide complexity and make review and debugging slower.",
                "Break long expressions into named steps or helper functions.",
            )
            scores["readability"] = min(scores["readability"], 70)

        if payload.get("source_truncated"):
            scores["architecture"] = min(scores["architecture"], 74)
            scores["testing"] = min(scores["testing"], 74)

        if not findings and not errors:
            overview = "The visible file is structurally clean in this heuristic pass, with no immediately obvious high-confidence defects. A full model-driven review should still be preferred for architectural nuance and testability judgment."
        else:
            overview = "The fallback review identified concrete issues in the visible source. The most important next step is to address the highest-severity items before relying on the file in a production path."

        if exception_text:
            overview += f" A heuristic fallback was used because the model review could not be completed cleanly: {_clean_text(exception_text, limit=120)}."

        return {
            "title": "Code Review",
            "overview": overview,
            "confidence": "low" if exception_text else "medium",
            "preflight_checks": self._normalize_preflight(payload, []),
            "review_findings": findings,
            "errors_found": errors,
            "category_scores": scores,
            "quality_summary": "",
        }

    def _normalize_response(self, parsed, payload):
        if not isinstance(parsed, dict):
            parsed = {}

        normalized = {
            "title": _clean_text(parsed.get("title"), limit=120) or "Code Review",
            "overview": _clean_text(parsed.get("overview"), limit=600) or "The file was reviewed against a fixed engineering quality rubric.",
            "confidence": _clean_text(parsed.get("confidence"), limit=20).lower() or "medium",
            "preflight_checks": self._normalize_preflight(payload, parsed.get("preflight_checks", [])),
            "review_findings": self._normalize_findings(parsed.get("review_findings", [])),
            "errors_found": self._normalize_findings(parsed.get("errors_found", []), is_error_list=True),
            "category_scores": self._normalize_scores(parsed.get("category_scores", {})),
            "quality_summary": _clean_text(parsed.get("quality_summary"), limit=420),
        }

        if normalized["confidence"] not in {"low", "medium", "high"}:
            normalized["confidence"] = "medium"

        normalized["quality_score"] = self._compute_weighted_score(normalized["category_scores"])
        normalized["verdict"], normalized["risk_level"] = self._derive_verdict(
            normalized["quality_score"],
            normalized["review_findings"],
            normalized["errors_found"],
        )
        normalized["quality_summary"] = self._build_quality_summary(normalized)
        normalized["finding_count"] = len(normalized["review_findings"])
        normalized["error_count"] = len(normalized["errors_found"])
        normalized["metric_markdown"] = CODE_REVIEW_METRIC_MARKDOWN
        normalized["overview_markdown"] = self._build_overview_markdown(normalized, payload)
        normalized["findings_markdown"] = self._build_findings_markdown(normalized)
        normalized["errors_markdown"] = self._build_errors_markdown(normalized)
        normalized["quality_report_markdown"] = self._build_quality_markdown(normalized)
        normalized["review_markdown"] = self._build_combined_markdown(
            normalized["overview_markdown"],
            normalized["findings_markdown"],
            normalized["errors_markdown"],
            normalized["quality_report_markdown"],
        )
        return normalized

    def get_response(self, payload):
        user_prompt = "\n".join([
            "Review the following source file using the deterministic code review metric.",
            "",
            _source_scope_summary(payload),
            "",
            "### Source For Review",
            payload.get("source_for_model", "") or "[No source loaded]",
        ])

        try:
            response = api_provider.chat(
                task=config.TASK_CHAT,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )
            raw_text = response["message"]["content"]
            parsed = json.loads(self._extract_json(raw_text))
        except Exception as exc:
            parsed = self._fallback_review(payload, str(exc))
        return self._normalize_response(parsed, payload)


class CodeReviewWorkerThread(QThread):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, payload):
        super().__init__()
        self.payload = payload
        self.agent = CodeReviewAnalyzer()
        self._is_running = True

    def run(self):
        try:
            if not self._is_running:
                return
            result = self.agent.get_response(self.payload)
            if self._is_running:
                self.finished.emit(result)
        except Exception as exc:
            if self._is_running:
                self.error.emit(str(exc))
        finally:
            self._is_running = False

    def stop(self):
        self._is_running = False


class CodeReviewConnectionItem(ConnectionItem):
    def paint(self, painter, option, widget=None):
        if not (self.start_node and self.end_node):
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = get_current_palette()
        node_color = QColor(palette.FRAME_COLORS["Blue"]["color"])
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


class CodeReviewNode(QGraphicsObject, HoverAnimationMixin):
    review_requested = Signal(object)

    NODE_WIDTH = 820
    NODE_HEIGHT = 760
    COLLAPSED_WIDTH = 260
    COLLAPSED_HEIGHT = 40
    CONNECTION_DOT_RADIUS = 5
    CONNECTION_DOT_OFFSET = 0

    def __init__(self, parent_node, settings_manager=None, parent=None):
        super().__init__(parent)
        HoverAnimationMixin.__init__(self)
        self.parent_node = parent_node
        self.settings_manager = settings_manager
        self.children = []
        self.is_user = False
        self.conversation_history = []
        self.status = "Idle"
        self.quality_score = 0
        self.verdict = "pending"
        self.risk_level = "unknown"
        self.finding_count = 0
        self.error_count = 0
        self.review_context = ""
        self.review_markdown = ""
        self.review_data = {}
        self.source_state = {
            "origin": "",
            "label": "",
            "repo": "",
            "branch": "",
            "path": "",
            "local_path": "",
            "edited": False,
        }
        self.is_search_match = False
        self.hovered = False
        self.is_collapsed = False
        self.width = self.NODE_WIDTH
        self.height = self.NODE_HEIGHT
        self.worker_thread = None
        self.is_disposed = False
        self._suppress_source_state_updates = False

        self.setFlags(
            QGraphicsObject.GraphicsItemFlag.ItemIsMovable
            | QGraphicsObject.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsObject.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setZValue(0)
        self.collapse_button_rect = QRectF()

        self.proxy = QGraphicsProxyWidget(self)
        self.proxy_widget = QWidget()
        self.proxy_widget.setObjectName("codeReviewMainWidget")
        self.proxy_widget.setFixedSize(self.width, self.height)
        self.proxy_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.proxy_widget.setStyleSheet(
            """
            QWidget#codeReviewMainWidget {
                background-color: transparent;
                color: #e0e0e0;
            }
            QWidget#codeReviewMainWidget QLabel {
                background-color: transparent;
            }
            """
        )
        self.proxy.setWidget(self.proxy_widget)

        self._build_ui()
        self._apply_verdict_badge("pending")
        self._apply_score_badge()
        self._apply_count_badges()
        self.refresh_github_state()
        self._update_source_status()
        self.set_status("Idle")

    def _build_ui(self):
        palette = get_current_palette()
        node_color = QColor(palette.FRAME_COLORS["Blue"]["color"])
        badge_rgba = f"{node_color.red()}, {node_color.green()}, {node_color.blue()}"
        button_text_color = "#ffffff"

        self.proxy_widget.setStyleSheet(f"""
            QWidget#codeReviewMainWidget {{
                background-color: transparent;
                color: #e0e0e0;
                font-family: 'Segoe UI', sans-serif;
            }}
            QWidget#codeReviewMainWidget QLabel {{
                background-color: transparent;
            }}
            QFrame#codeReviewBriefShell,
            QFrame#codeReviewSectionCard {{
                background-color: #202327;
                border: 1px solid #353b43;
                border-radius: 10px;
            }}
            QLabel#codeReviewSectionHint {{
                color: #95a0ab;
                font-size: 11px;
            }}
            QLabel#codeReviewFieldLabel {{
                color: #c7cdd4;
                font-size: 11px;
                font-weight: bold;
            }}
            QLabel#codeReviewMetricBadge {{
                color: {node_color.name()};
                background-color: rgba({badge_rgba}, 0.12);
                border: 1px solid rgba({badge_rgba}, 0.26);
                border-radius: 10px;
                padding: 3px 8px;
                font-size: 11px;
                font-weight: bold;
            }}
            QComboBox, QLineEdit, QTextEdit, QPlainTextEdit {{
                background-color: #15181b;
                border: 1px solid #2f353d;
                color: #d7dce2;
                border-radius: 7px;
                padding: 8px;
                selection-background-color: #264f78;
                font-family: 'Segoe UI', sans-serif;
            }}
            QComboBox:focus, QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
                border: 1px solid {node_color.name()};
            }}
            QComboBox QAbstractItemView {{
                background-color: #1a1d20;
                color: #ffffff;
                selection-background-color: #2d6fa3;
                border: 1px solid #2b3138;
            }}
            QTabWidget::pane {{
                border: 1px solid #3a4048;
                background: #1d2024;
                border-radius: 8px;
            }}
            QTabBar::tab {{
                background: #25282d;
                color: #97a1ab;
                padding: 7px 12px;
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
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            QScrollArea > QWidget > QWidget {{
                background: transparent;
            }}
        """)

        root_layout = QVBoxLayout(self.proxy_widget)
        root_layout.setContentsMargins(15, 15, 15, 15)
        root_layout.setSpacing(10)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(4, 0, 4, 0)
        header_layout.setSpacing(8)
        icon = QLabel()
        icon.setPixmap(qta.icon("fa5s.search", color=node_color).pixmap(18, 18))
        header_layout.addWidget(icon)
        title = QLabel("Code Review Agent")
        title.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {node_color.name()};")
        header_layout.addWidget(title)
        header_layout.addStretch()
        root_layout.addLayout(header_layout)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: #3f3f3f; border: none; height: 1px;")
        root_layout.addWidget(line)

        intro_card = QFrame()
        intro_card.setObjectName("codeReviewBriefShell")
        intro_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        intro_layout = QVBoxLayout(intro_card)
        intro_layout.setContentsMargins(14, 12, 14, 12)
        intro_layout.setSpacing(8)

        controls_row = QHBoxLayout()
        controls_row.setContentsMargins(0, 0, 0, 0)
        controls_row.setSpacing(10)

        self.run_button = QPushButton("Run Code Review")
        self.run_button.setIcon(qta.icon("fa5s.search", color=button_text_color))
        self.run_button.clicked.connect(lambda: self.review_requested.emit(self))
        controls_row.addWidget(self.run_button)

        self.status_label = QLabel("Idle")
        self.status_label.setObjectName("codeReviewMetricBadge")
        controls_row.addWidget(self.status_label)
        controls_row.addStretch()
        intro_layout.addLayout(controls_row)

        root_layout.addWidget(intro_card)

        metrics_row = QHBoxLayout()
        metrics_row.setContentsMargins(2, 0, 2, 0)
        metrics_row.setSpacing(8)

        self.verdict_label = QLabel("Verdict: Pending")
        self.verdict_label.setObjectName("codeReviewMetricBadge")
        metrics_row.addWidget(self.verdict_label)

        self.score_label = QLabel("Quality: --")
        self.score_label.setObjectName("codeReviewMetricBadge")
        metrics_row.addWidget(self.score_label)

        self.findings_label = QLabel("Findings: 0")
        self.findings_label.setObjectName("codeReviewMetricBadge")
        metrics_row.addWidget(self.findings_label)

        self.errors_label = QLabel("Errors: 0")
        self.errors_label.setObjectName("codeReviewMetricBadge")

        self.risk_label = QLabel("Risk: --")
        self.risk_label.setObjectName("codeReviewMetricBadge")
        metrics_row.addWidget(self.risk_label)
        metrics_row.addStretch()

        root_layout.addLayout(metrics_row)

        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setDocumentMode(True)
        root_layout.addWidget(self.workspace_tabs, stretch=1)

        source_tab = QWidget()
        source_layout = QVBoxLayout(source_tab)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.setSpacing(8)

        source_hint = QLabel("Use Load to choose the file, Context for review goals, and Source to inspect or paste the exact text that will be reviewed.")
        source_hint.setObjectName("codeReviewSectionHint")
        source_hint.setWordWrap(True)
        source_layout.addWidget(source_hint)

        self.source_tabs = QTabWidget()
        self.source_tabs.setDocumentMode(True)
        source_layout.addWidget(self.source_tabs, stretch=1)

        load_panel = QWidget()
        load_panel_layout = QVBoxLayout(load_panel)
        load_panel_layout.setContentsMargins(0, 0, 0, 0)
        load_panel_layout.setSpacing(8)

        load_scroll = QScrollArea()
        load_scroll.setWidgetResizable(True)
        load_scroll.setFrameShape(QFrame.Shape.NoFrame)
        load_scroll.setStyleSheet(CODE_REVIEW_SCROLLBAR_STYLE)
        load_panel_layout.addWidget(load_scroll)

        load_scroll_content = QWidget()
        load_scroll.setWidget(load_scroll_content)
        load_scroll_layout = QVBoxLayout(load_scroll_content)
        load_scroll_layout.setContentsMargins(0, 0, 0, 0)
        load_scroll_layout.setSpacing(8)

        self.source_picker_tabs = QTabWidget()
        self.source_picker_tabs.setDocumentMode(True)
        load_scroll_layout.addWidget(self.source_picker_tabs)
        load_scroll_layout.addStretch()

        github_panel = QWidget()
        github_layout = QVBoxLayout(github_panel)
        github_layout.setContentsMargins(0, 0, 0, 0)
        github_layout.setSpacing(8)

        github_card = QFrame()
        github_card.setObjectName("codeReviewSectionCard")
        github_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        github_card_layout = QVBoxLayout(github_card)
        github_card_layout.setContentsMargins(12, 12, 12, 12)
        github_card_layout.setSpacing(8)

        github_title = QLabel("GitHub Source")
        github_title.setStyleSheet("color: #ffffff; font-weight: bold;")
        github_card_layout.addWidget(github_title)

        self.github_state_label = QLabel("")
        self.github_state_label.setObjectName("codeReviewSectionHint")
        self.github_state_label.setWordWrap(True)
        github_card_layout.addWidget(self.github_state_label)

        repo_row = QHBoxLayout()
        repo_row.setContentsMargins(0, 0, 0, 0)
        repo_row.setSpacing(8)
        self.load_repos_button = QPushButton("Load Repo List")
        self.load_repos_button.setIcon(qta.icon("fa5s.sync-alt", color=button_text_color))
        self.load_repos_button.clicked.connect(self.load_github_repositories)
        repo_row.addWidget(self.load_repos_button)

        self.repo_combo = CodeReviewPopupComboBox()
        self.repo_combo.setEditable(False)
        self.repo_combo.setMinimumWidth(200)
        self.repo_combo.apply_popup_style("#2d6fa3")
        self.repo_combo.about_to_show_popup.connect(self._ensure_github_repositories_loaded)
        self.repo_combo.currentTextChanged.connect(self._on_repo_selected)
        repo_row.addWidget(self.repo_combo, stretch=1)
        github_card_layout.addLayout(repo_row)

        repo_label = QLabel("Repository")
        repo_label.setObjectName("codeReviewFieldLabel")
        github_card_layout.addWidget(repo_label)
        self.repo_input = QLineEdit()
        self.repo_input.setPlaceholderText("owner/repo")
        github_card_layout.addWidget(self.repo_input)

        branch_row = QHBoxLayout()
        branch_row.setContentsMargins(0, 0, 0, 0)
        branch_row.setSpacing(8)
        self.branch_input = QLineEdit()
        self.branch_input.setPlaceholderText("Branch (leave blank for repo default)")
        branch_row.addWidget(self.branch_input)

        self.refresh_files_button = QPushButton("Load File List")
        self.refresh_files_button.setIcon(qta.icon("fa5s.folder-open", color=button_text_color))
        self.refresh_files_button.clicked.connect(self.load_github_file_list)
        branch_row.addWidget(self.refresh_files_button)
        github_card_layout.addLayout(branch_row)

        file_label = QLabel("Repository File")
        file_label.setObjectName("codeReviewFieldLabel")
        github_card_layout.addWidget(file_label)
        file_row = QHBoxLayout()
        file_row.setContentsMargins(0, 0, 0, 0)
        file_row.setSpacing(8)
        self.file_combo = CodeReviewPopupComboBox()
        self.file_combo.setEditable(True)
        self.file_combo.setMaxVisibleItems(20)
        self.file_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.file_combo.setMinimumWidth(220)
        self.file_combo.apply_popup_style("#2d6fa3")
        self.file_combo.lineEdit().setPlaceholderText("Repository file path")
        file_row.addWidget(self.file_combo, stretch=1)

        self.load_github_file_button = QPushButton("Load File")
        self.load_github_file_button.setIcon(qta.icon("fa5s.download", color=button_text_color))
        self.load_github_file_button.clicked.connect(self.load_selected_github_file)
        file_row.addWidget(self.load_github_file_button)
        github_card_layout.addLayout(file_row)
        github_layout.addWidget(github_card)

        local_panel = QWidget()
        local_panel_layout = QVBoxLayout(local_panel)
        local_panel_layout.setContentsMargins(0, 0, 0, 0)
        local_panel_layout.setSpacing(8)

        local_card = QFrame()
        local_card.setObjectName("codeReviewSectionCard")
        local_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        local_layout = QVBoxLayout(local_card)
        local_layout.setContentsMargins(12, 12, 12, 12)
        local_layout.setSpacing(8)

        local_title = QLabel("Local Source")
        local_title.setStyleSheet("color: #ffffff; font-weight: bold;")
        local_layout.addWidget(local_title)

        local_hint = QLabel("Browse to a file on disk, or paste code directly in the Source panel if you want a quick manual review.")
        local_hint.setObjectName("codeReviewSectionHint")
        local_hint.setWordWrap(True)
        local_layout.addWidget(local_hint)

        self.local_path_input = QLineEdit()
        self.local_path_input.setPlaceholderText("Local file path")
        local_layout.addWidget(self.local_path_input)

        local_row = QHBoxLayout()
        local_row.setContentsMargins(0, 0, 0, 0)
        local_row.setSpacing(8)

        self.browse_button = QPushButton("Browse")
        self.browse_button.setIcon(qta.icon("fa5s.folder-open", color=button_text_color))
        self.browse_button.clicked.connect(self.browse_local_file)
        local_row.addWidget(self.browse_button)

        self.load_local_button = QPushButton("Load Local File")
        self.load_local_button.setIcon(qta.icon("fa5s.file-import", color=button_text_color))
        self.load_local_button.clicked.connect(self.load_local_file)
        local_row.addWidget(self.load_local_button)
        local_row.addStretch()
        local_layout.addLayout(local_row)
        local_panel_layout.addWidget(local_card)

        self.source_picker_tabs.addTab(github_panel, qta.icon("fa5s.code-branch", color="#cccccc"), "GitHub")
        self.source_picker_tabs.addTab(local_panel, qta.icon("fa5s.folder-open", color="#cccccc"), "Local")

        context_panel = QWidget()
        context_layout = QVBoxLayout(context_panel)
        context_layout.setContentsMargins(0, 0, 0, 0)
        context_layout.setSpacing(8)

        context_hint = QLabel("Optional review context helps the agent judge the file against the right intent, risks, and constraints.")
        context_hint.setObjectName("codeReviewSectionHint")
        context_hint.setWordWrap(True)
        context_layout.addWidget(context_hint)

        context_label = QLabel("Review Context")
        context_label.setObjectName("codeReviewFieldLabel")
        context_layout.addWidget(context_label)

        self.context_input = QTextEdit()
        self.context_input.setPlaceholderText("What should this file do, what matters most, and what would make you uneasy about shipping it?")
        self.context_input.setFixedHeight(140)
        self.context_input.setStyleSheet("QTextEdit { font-size: 12px; }" + CODE_REVIEW_SCROLLBAR_STYLE)
        self.context_input.textChanged.connect(self._on_context_changed)
        context_layout.addWidget(self.context_input)
        context_layout.addStretch()

        source_panel = QWidget()
        source_panel_layout = QVBoxLayout(source_panel)
        source_panel_layout.setContentsMargins(0, 0, 0, 0)
        source_panel_layout.setSpacing(8)

        source_header = QHBoxLayout()
        source_header.setContentsMargins(0, 0, 0, 0)
        source_header.setSpacing(8)

        source_title = QLabel("Source Under Review")
        source_title.setStyleSheet("color: #ffffff; font-weight: bold;")
        source_header.addWidget(source_title)
        source_header.addStretch()

        self.source_status_label = QLabel("")
        self.source_status_label.setObjectName("codeReviewSectionHint")
        source_header.addWidget(self.source_status_label)

        self.clear_source_button = QPushButton("Clear")
        self.clear_source_button.setIcon(qta.icon("fa5s.eraser", color=button_text_color))
        self.clear_source_button.clicked.connect(self.clear_source)
        source_header.addWidget(self.clear_source_button)
        source_panel_layout.addLayout(source_header)

        self.source_editor = QPlainTextEdit()
        self.source_editor.setPlaceholderText("Paste code here, or load a file from GitHub or your local machine.")
        self.source_editor.setFont(QFont("Consolas", 10))
        self.source_editor.textChanged.connect(self._on_source_changed)
        self.source_editor.setStyleSheet("QPlainTextEdit { font-family: 'Consolas'; }" + CODE_REVIEW_SCROLLBAR_STYLE)
        source_panel_layout.addWidget(self.source_editor, stretch=1)

        self.source_tabs.addTab(load_panel, qta.icon("fa5s.file-import", color="#cccccc"), "Load")
        self.source_tabs.addTab(context_panel, qta.icon("fa5s.comment", color="#cccccc"), "Context")
        self.source_tabs.addTab(source_panel, qta.icon("fa5s.file-code", color="#cccccc"), "Source")

        self.workspace_tabs.addTab(source_tab, qta.icon("fa5s.file-code", color="#cccccc"), "Source")

        self.overview_display = self._create_markdown_display("Run the review to generate the executive overview.")
        self.findings_display = self._create_markdown_display("Review findings will appear here after the analysis runs.")
        self.errors_display = self._create_markdown_display("High-confidence errors will appear here after the analysis runs.")
        self.quality_display = self._create_markdown_display("The weighted quality report will appear here after the analysis runs.")
        self.metric_display = self._create_markdown_display("")
        self.metric_display.setMarkdown(CODE_REVIEW_METRIC_MARKDOWN)

        self.workspace_tabs.addTab(self.overview_display, qta.icon("fa5s.clipboard", color="#cccccc"), "Overview")
        self.workspace_tabs.addTab(self.findings_display, qta.icon("fa5s.list-ul", color="#cccccc"), "Findings (0)")
        self.workspace_tabs.addTab(self.errors_display, qta.icon("fa5s.bug", color="#cccccc"), "Errors (0)")
        self.workspace_tabs.addTab(self.quality_display, qta.icon("fa5s.chart-line", color="#cccccc"), "Quality")
        self.workspace_tabs.addTab(self.metric_display, qta.icon("fa5s.ruler-combined", color="#cccccc"), "Rubric")

        button_style = f"""
            QPushButton {{
                background-color: {node_color.name()};
                color: {button_text_color};
                border: none;
                border-radius: 8px;
                padding: 9px 14px;
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
        for button in (
            self.run_button,
            self.load_repos_button,
            self.refresh_files_button,
            self.load_github_file_button,
            self.browse_button,
            self.load_local_button,
            self.clear_source_button,
        ):
            button.setStyleSheet(button_style)

    def _create_markdown_display(self, placeholder_text):
        display = QTextEdit()
        display.setReadOnly(True)
        display.setPlaceholderText(placeholder_text)
        display.document().setDefaultStyleSheet(
            """
            h1, h2, h3 { color: #ffffff; }
            p, li { color: #d6d6d6; font-family: 'Segoe UI', sans-serif; font-size: 12px; }
            code { background-color: #31353b; padding: 2px 4px; border-radius: 4px; color: #ffe07d; }
            """
        )
        display.setStyleSheet(
            "QTextEdit { font-size: 12px; background-color: #121417; border: 1px solid #2b3138; border-radius: 10px; padding: 6px; }"
            + CODE_REVIEW_SCROLLBAR_STYLE
        )
        return display

    def _get_github_token(self):
        if self.settings_manager:
            return self.settings_manager.get_github_token().strip()
        return ""

    def _github_headers(self):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = self._get_github_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _github_request(self, url, params=None):
        response = requests.get(url, headers=self._github_headers(), params=params or {}, timeout=25)
        if response.status_code >= 400:
            try:
                payload = response.json()
                message = payload.get("message") or response.reason
            except ValueError:
                message = response.text or response.reason
            if response.status_code == 404:
                raise RuntimeError("GitHub resource not found. Check the repository, branch, and file path.")
            if response.status_code == 401:
                raise RuntimeError("GitHub rejected the saved token. Update it in Settings > Integrations.")
            if response.status_code == 403 and "rate limit" in message.lower():
                raise RuntimeError("GitHub API rate limit reached. Add a token or try again later.")
            raise RuntimeError(message)
        return response.json()

    def refresh_github_state(self):
        token = self._get_github_token()
        if token:
            detail_text = "GitHub token detected. Private repositories and authenticated repo listing are enabled."
            self.github_state_label.setText("GitHub token ready. Private repos enabled.")
        else:
            detail_text = "No GitHub token saved. Repo list loading is disabled, but public repositories still work when you enter owner/repo manually."
            self.github_state_label.setText("No GitHub token saved. Public repos still work.")
        self.github_state_label.setToolTip(detail_text)

    def _on_repo_selected(self, repo_name):
        if repo_name:
            self.repo_input.setText(repo_name)
            self.file_combo.clear()

    def _ensure_github_repositories_loaded(self):
        if self.repo_combo.count() == 0:
            self.load_github_repositories()

    def _on_context_changed(self):
        self.review_context = self.context_input.toPlainText()

    def _on_source_changed(self):
        if self._suppress_source_state_updates:
            return
        source_text = self.source_editor.toPlainText()
        if source_text.strip():
            if not self.source_state.get("origin"):
                self.source_state["origin"] = "manual"
                self.source_state["label"] = "Manual / pasted source"
            else:
                self.source_state["edited"] = True
        elif not self._suppress_source_state_updates:
            self.source_state = {
                "origin": "",
                "label": "",
                "repo": "",
                "branch": "",
                "path": "",
                "local_path": "",
                "edited": False,
            }
        self._update_source_status()

    def _update_source_status(self):
        source_text = self.source_editor.toPlainText()
        line_count = len(source_text.splitlines()) if source_text else 0
        char_count = len(source_text)
        origin_label = _source_origin_label(self.source_state)
        if char_count == 0:
            summary_text = "No source loaded yet."
            detail_text = summary_text
        else:
            detail_text = f"{origin_label} | {line_count} lines, {char_count} chars"
            summary_text = f"{origin_label} | {line_count} lines"
        self.source_status_label.setText(_compact_label_text(summary_text, limit=44))
        self.source_status_label.setToolTip(detail_text)

    def browse_local_file(self):
        path, _ = QFileDialog.getOpenFileName(None, "Choose a file to review")
        if path:
            self.local_path_input.setText(path)

    def load_local_file(self):
        path_text = self.local_path_input.text().strip()
        if not path_text:
            self.set_status("Error: Select a local file first.")
            return

        path = Path(path_text)
        if not path.exists() or not path.is_file():
            self.set_status("Error: The selected local file could not be found.")
            return

        try:
            raw_bytes = path.read_bytes()
            source_text = _decode_text_bytes(raw_bytes)
        except Exception as exc:
            self.set_status(f"Error: {exc}")
            return

        self._set_source_text(
            source_text,
            {
                "origin": "local",
                "label": path.name,
                "repo": "",
                "branch": "",
                "path": "",
                "local_path": str(path),
                "edited": False,
            },
        )
        self.set_status("Loaded local file.")

    def clear_source(self):
        self._set_source_text("", {
            "origin": "",
            "label": "",
            "repo": "",
            "branch": "",
            "path": "",
            "local_path": "",
            "edited": False,
        })
        self.local_path_input.clear()
        self.file_combo.clear()
        self.set_status("Source cleared.")

    def load_github_repositories(self):
        self.refresh_github_state()
        if not self._get_github_token():
            self.set_status("Error: Save a GitHub token in Settings > Integrations to load your private repo list.")
            return

        try:
            repos = []
            page = 1
            while True:
                page_payload = self._github_request(
                    "https://api.github.com/user/repos",
                    params={
                        "per_page": 100,
                        "page": page,
                        "sort": "updated",
                        "visibility": "all",
                        "affiliation": "owner,collaborator,organization_member",
                    },
                )
                if not page_payload:
                    break
                repos.extend(item.get("full_name", "") for item in page_payload if item.get("full_name"))
                if len(page_payload) < 100 or page >= 5:
                    break
                page += 1

            self.repo_combo.clear()
            self.repo_combo.addItems(sorted(set(repos), key=str.lower))
            if self.repo_combo.count() == 0:
                self.set_status("GitHub returned no accessible repositories for this token.")
            else:
                self.set_status(f"Loaded {self.repo_combo.count()} repositories from GitHub.")
        except Exception as exc:
            self.set_status(f"Error: {exc}")

    def _resolve_repo_and_branch(self):
        repo_name = self.repo_input.text().strip() or self.repo_combo.currentText().strip()
        if not repo_name or "/" not in repo_name:
            raise RuntimeError("Enter a repository as `owner/repo`.")

        repo_payload = self._github_request(f"https://api.github.com/repos/{repo_name}")
        default_branch = repo_payload.get("default_branch", "")
        branch_name = self.branch_input.text().strip() or default_branch
        if not branch_name:
            raise RuntimeError("GitHub did not provide a default branch for this repository.")

        self.repo_input.setText(repo_name)
        self.branch_input.setText(branch_name)
        return repo_name, branch_name

    def load_github_file_list(self):
        self.refresh_github_state()
        try:
            repo_name, branch_name = self._resolve_repo_and_branch()
            tree_payload = self._github_request(
                f"https://api.github.com/repos/{repo_name}/git/trees/{quote(branch_name, safe='')}",
                params={"recursive": 1},
            )
            tree_items = tree_payload.get("tree", [])
            paths = [
                item.get("path", "")
                for item in tree_items
                if item.get("type") == "blob" and item.get("path") and _is_reviewable_repo_path(item.get("path", ""))
            ]
            limited_paths = sorted(paths)[:5000]
            current_text = self.file_combo.currentText().strip()
            self.file_combo.clear()
            self.file_combo.addItems(limited_paths)
            if current_text:
                self.file_combo.setCurrentText(current_text)

            status_text = f"Loaded {len(limited_paths)} file paths from {repo_name}@{branch_name}."
            if tree_payload.get("truncated"):
                status_text += " GitHub reported the tree as truncated."
            self.set_status(status_text)
        except Exception as exc:
            self.set_status(f"Error: {exc}")

    def load_selected_github_file(self):
        self.refresh_github_state()
        try:
            repo_name, branch_name = self._resolve_repo_and_branch()
            file_path = self.file_combo.currentText().strip()
            if not file_path:
                raise RuntimeError("Choose or type a repository file path first.")

            content_payload = self._github_request(
                f"https://api.github.com/repos/{repo_name}/contents/{quote(file_path, safe='/')}",
                params={"ref": branch_name},
            )
            if isinstance(content_payload, list):
                raise RuntimeError("The selected path resolves to a directory, not a file.")

            if content_payload.get("encoding") == "base64" and content_payload.get("content"):
                source_text = _decode_text_bytes(base64.b64decode(content_payload["content"]))
            elif content_payload.get("download_url"):
                download_response = requests.get(content_payload["download_url"], timeout=25)
                download_response.raise_for_status()
                source_text = download_response.text
            else:
                raise RuntimeError("GitHub did not return file contents for this path.")

            self._set_source_text(
                source_text,
                {
                    "origin": "github",
                    "label": Path(file_path).name or file_path,
                    "repo": repo_name,
                    "branch": branch_name,
                    "path": file_path,
                    "local_path": "",
                    "edited": False,
                },
            )
            self.set_status("Loaded GitHub file.")
        except requests.HTTPError as exc:
            self.set_status(f"Error: {exc}")
        except Exception as exc:
            self.set_status(f"Error: {exc}")

    def _set_source_text(self, source_text, source_state):
        self._suppress_source_state_updates = True
        self.source_editor.setPlainText(source_text)
        self.source_state = dict(source_state)
        self._suppress_source_state_updates = False

        self.repo_input.setText(self.source_state.get("repo", ""))
        self.branch_input.setText(self.source_state.get("branch", ""))
        self.local_path_input.setText(self.source_state.get("local_path", ""))
        if self.source_state.get("path"):
            self.file_combo.setCurrentText(self.source_state["path"])
        else:
            self.file_combo.setCurrentText("")
        self._update_source_status()
        if hasattr(self, "workspace_tabs"):
            self.workspace_tabs.setCurrentIndex(0)
        if hasattr(self, "source_tabs"):
            self.source_tabs.setCurrentIndex(2 if source_text.strip() else 0)

    def get_review_context(self):
        return self.context_input.toPlainText()

    def build_review_payload(self):
        source_text = self.source_editor.toPlainText()
        source_for_model, truncated, total_lines, visible_lines = _prepare_numbered_source(source_text)
        return {
            "source_text": source_text,
            "source_for_model": source_for_model,
            "source_truncated": truncated,
            "source_state": dict(self.source_state),
            "review_context": self.get_review_context(),
            "total_lines": total_lines,
            "visible_lines": visible_lines,
        }

    def _apply_verdict_badge(self, verdict):
        verdict = (verdict or "pending").lower()
        if verdict == "strong":
            color = get_semantic_color("status_success")
            text = "Verdict: Strong"
        elif verdict == "needs_revision":
            color = get_semantic_color("status_warning")
            text = "Verdict: Needs Revision"
        elif verdict == "not_ready":
            color = get_semantic_color("status_error")
            text = "Verdict: Not Ready"
        else:
            color = QColor("#9aa3ad")
            text = "Verdict: Pending"

        self.verdict_label.setText(text)
        self.verdict_label.setStyleSheet(
            f"color: {color.name()}; background-color: rgba({color.red()}, {color.green()}, {color.blue()}, 0.1); "
            f"border: 1px solid rgba({color.red()}, {color.green()}, {color.blue()}, 0.24); border-radius: 10px; "
            "padding: 3px 8px; font-size: 11px; font-weight: bold;"
        )

    def _apply_score_badge(self):
        if self.quality_score >= 78:
            color = get_semantic_color("status_success")
        elif self.quality_score >= 60:
            color = get_semantic_color("status_warning")
        else:
            color = get_semantic_color("status_error")

        text = f"Quality: {self.quality_score}/100" if self.quality_score else "Quality: --"
        self.score_label.setText(text)
        self.score_label.setStyleSheet(
            f"color: {color.name()}; background-color: rgba({color.red()}, {color.green()}, {color.blue()}, 0.1); "
            f"border: 1px solid rgba({color.red()}, {color.green()}, {color.blue()}, 0.24); border-radius: 10px; "
            "padding: 3px 8px; font-size: 11px; font-weight: bold;"
        )

    def _apply_count_badges(self):
        palette = get_current_palette()
        info_color = QColor(palette.FRAME_COLORS["Blue"]["color"])
        error_color = get_semantic_color("status_error")
        risk_color = {
            "low": get_semantic_color("status_success"),
            "medium": get_semantic_color("status_warning"),
            "high": get_semantic_color("status_error"),
        }.get(self.risk_level, QColor("#9aa3ad"))

        for label, value, color in (
            (self.findings_label, f"Findings: {self.finding_count}", info_color),
            (self.errors_label, f"Errors: {self.error_count}", error_color if self.error_count else info_color),
            (self.risk_label, f"Risk: {self.risk_level.title() if self.risk_level else '--'}", risk_color),
        ):
            label.setText(value)
            label.setStyleSheet(
                f"color: {color.name()}; background-color: rgba({color.red()}, {color.green()}, {color.blue()}, 0.1); "
                f"border: 1px solid rgba({color.red()}, {color.green()}, {color.blue()}, 0.24); border-radius: 10px; "
                "padding: 3px 8px; font-size: 11px; font-weight: bold;"
            )

    def set_running_state(self, is_running):
        if self.is_disposed:
            return

        for widget in (
            self.run_button,
            self.load_repos_button,
            self.refresh_files_button,
            self.load_github_file_button,
            self.browse_button,
            self.load_local_button,
            self.clear_source_button,
        ):
            widget.setEnabled(not is_running)

        self.source_editor.setReadOnly(is_running)
        self.context_input.setReadOnly(is_running)
        self.run_button.setText("Reviewing..." if is_running else "Run Code Review")

        if is_running:
            self.set_status("Reviewing source...")
        elif self.status.startswith("Error"):
            return
        elif self.review_markdown.strip():
            self.set_status("Review complete.")
        else:
            self.set_status("Idle")

    def set_status(self, status_text):
        if self.is_disposed:
            return

        self.status = status_text
        self.status_label.setText(_compact_label_text(status_text, limit=26))
        self.status_label.setToolTip(status_text)
        if "Reviewing" in status_text:
            color = get_semantic_color("status_info")
        elif "Error" in status_text:
            color = get_semantic_color("status_error")
        elif "complete" in status_text.lower():
            color = get_semantic_color("status_success")
        else:
            color = QColor("#9aa3ad")

        self.status_label.setStyleSheet(
            f"color: {color.name()}; background-color: rgba({color.red()}, {color.green()}, {color.blue()}, 0.1); "
            f"border: 1px solid rgba({color.red()}, {color.green()}, {color.blue()}, 0.24); border-radius: 10px; "
            "padding: 3px 8px; font-size: 11px; font-weight: bold;"
        )

    def set_review(self, review):
        if self.is_disposed:
            return

        self.review_data = dict(review or {})
        self.review_markdown = self.review_data.get("review_markdown", "")
        self.quality_score = int(self.review_data.get("quality_score", 0) or 0)
        self.verdict = self.review_data.get("verdict", "pending")
        self.risk_level = self.review_data.get("risk_level", "unknown")
        self.finding_count = int(self.review_data.get("finding_count", 0) or 0)
        self.error_count = int(self.review_data.get("error_count", 0) or 0)

        self.overview_display.setMarkdown(self.review_data.get("overview_markdown", ""))
        self.findings_display.setMarkdown(self.review_data.get("findings_markdown", ""))
        self.errors_display.setMarkdown(self.review_data.get("errors_markdown", ""))
        self.quality_display.setMarkdown(self.review_data.get("quality_report_markdown", ""))
        self.metric_display.setMarkdown(self.review_data.get("metric_markdown", CODE_REVIEW_METRIC_MARKDOWN))

        self.workspace_tabs.setTabText(2, f"Findings ({self.finding_count})")
        self.workspace_tabs.setTabText(3, f"Errors ({self.error_count})")

        self._apply_verdict_badge(self.verdict)
        self._apply_score_badge()
        self._apply_count_badges()
        self.set_status("Review complete.")

    def set_error(self, error_message):
        if self.is_disposed:
            return

        self.review_data = {}
        self.review_markdown = f"## Error\n\n{error_message}"
        self.quality_score = 0
        self.verdict = "not_ready"
        self.risk_level = "high"
        self.finding_count = 0
        self.error_count = 0

        self.overview_display.setMarkdown(self.review_markdown)
        self.findings_display.setMarkdown("## Review Findings\n\nNo findings available because the review did not complete.")
        self.errors_display.setMarkdown("## Errors Found\n\nThe review itself failed before issue extraction could complete.")
        self.quality_display.setMarkdown("## Code Quality Report\n\nA deterministic quality report could not be generated because the review failed.")
        self.metric_display.setMarkdown(CODE_REVIEW_METRIC_MARKDOWN)
        self.workspace_tabs.setTabText(2, "Findings (0)")
        self.workspace_tabs.setTabText(3, "Errors (0)")

        self._apply_verdict_badge("not_ready")
        self._apply_score_badge()
        self._apply_count_badges()
        self.set_status(f"Error: {error_message}")

    def set_collapsed(self, collapsed):
        if self.is_collapsed == collapsed:
            return

        self.prepareGeometryChange()
        self.is_collapsed = collapsed
        if collapsed:
            self.width = self.COLLAPSED_WIDTH
            self.height = self.COLLAPSED_HEIGHT
            self.proxy.setVisible(False)
        else:
            self.width = self.NODE_WIDTH
            self.height = self.NODE_HEIGHT
            self.proxy.setVisible(True)
            self.proxy_widget.setFixedSize(self.width, self.height)
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
        node_color = QColor(palette.FRAME_COLORS["Blue"]["color"])

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
            painter.drawText(QRectF(42, 0, self.width - 84, self.height), Qt.AlignmentFlag.AlignVCenter, "Code Review Agent")
            qta.icon("fa5s.search", color=node_color.name()).paint(painter, QRect(12, 10, 20, 20))
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
