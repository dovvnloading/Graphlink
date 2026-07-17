import base64
import difflib
import html
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import qtawesome as qta
import requests
from PySide6.QtCore import QRect, QRectF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
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
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from graphlink_config import canvas_font

from graphlink_canvas_items import HoverAnimationMixin
from graphlink_config import get_current_palette, get_semantic_color
from graphlink_connections import ConnectionItem
from graphlink_plugins.graphlink_plugin_context_menu import PluginNodeContextMenu
from graphlink_plugins.common.github_client import GitHubRestClient
from graphlink_plugins.gitlink.agent import (
    GitlinkAgent,
    _clean_text,
    _compact_label_text,
    _decode_text_bytes,
    _fingerprint_changes,
    _is_repo_text_path,
    _normalize_repo_path,
    _safe_local_target,
    _truncate_for_context,
    _xml_file_block,
)
from graphlink_plugins.common.combo import PopupComboBox


GITLINK_SCROLLBAR_STYLE = """
    QScrollBar:vertical {
        background: #1D1D1D;
        width: 10px;
        margin: 0px;
        border-radius: 5px;
    }
    QScrollBar::handle:vertical {
        background-color: #5A5A5A;
        min-height: 25px;
        border-radius: 5px;
    }
    QScrollBar::handle:vertical:hover {
        background-color: #717171;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0px;
        background: none;
    }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
        background: none;
    }
    QScrollBar:horizontal {
        background: #1D1D1D;
        height: 10px;
        margin: 0px;
        border-radius: 5px;
    }
    QScrollBar::handle:horizontal {
        background-color: #5A5A5A;
        min-width: 25px;
        border-radius: 5px;
    }
    QScrollBar::handle:horizontal:hover {
        background-color: #717171;
    }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
        width: 0px;
        background: none;
    }
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
        background: none;
    }
"""

IGNORED_LOCAL_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
}

MAX_CONTEXT_CHARS = 180000
MAX_MANIFEST_ENTRIES = 1200
MAX_REPO_PAGES = 5


# Write-gate state machine for GitlinkNode.apply_approved_changes: a proposal starts as
# DRAFT, becomes PREVIEWED once a change set is rendered for review, only becomes APPROVED
# inside the confirmation-dialog flow (stamped with a fingerprint of exactly what was shown),
# and only becomes APPLIED if that fingerprint still matches the change set at write time.
GITLINK_STATE_DRAFT = "draft"
GITLINK_STATE_PREVIEWED = "previewed"
GITLINK_STATE_APPROVED = "approved"
GITLINK_STATE_APPLIED = "applied"


class GitlinkWorkerThread(QThread):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, payload):
        super().__init__()
        self.payload = payload
        self.agent = GitlinkAgent()
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


class GitlinkConnectionItem(ConnectionItem):
    def paint(self, painter, option, widget=None):
        if not (self.start_node and self.end_node):
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = get_current_palette()
        node_color = QColor(palette.FRAME_COLORS["Green"]["color"])
        pen = QPen(node_color, 2, Qt.PenStyle.DashDotLine)
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


class GitlinkNode(QGraphicsObject, HoverAnimationMixin):
    gitlink_requested = Signal(object)

    NODE_WIDTH = 860
    NODE_HEIGHT = 780
    COLLAPSED_WIDTH = 250
    COLLAPSED_HEIGHT = 40
    CONNECTION_DOT_RADIUS = 5
    CONNECTION_DOT_OFFSET = 0

    def __init__(self, parent_node, settings_manager=None, parent=None):
        super().__init__(parent)
        HoverAnimationMixin.__init__(self)
        self.parent_node = parent_node
        self.settings_manager = settings_manager
        self._github_client = GitHubRestClient(settings_manager)
        self.children = []
        self.is_user = False
        self.conversation_history = []
        self.status = "Idle"
        self.task_prompt = ""
        self.context_xml = ""
        self.context_stats = {}
        self.context_summary = ""
        self.proposal_data = {}
        self.proposal_markdown = ""
        self.preview_text = ""
        self.pending_changes = []
        self.change_state = GITLINK_STATE_DRAFT
        self._approved_fingerprint = None
        self.repo_file_entries = []
        self.repo_file_paths = []
        self.selected_paths = []
        self.last_context_paths = []
        self.repo_state = {
            "repo": "",
            "branch": "",
            "scope_mode": "selected",
            "local_root": "",
            "imported_root": "",
        }
        self.is_search_match = False
        self.hovered = False
        self.is_collapsed = False
        self.width = self.NODE_WIDTH
        self.height = self.NODE_HEIGHT
        self.worker_thread = None
        self.is_disposed = False
        self._suppress_ui_updates = False
        self._context_dirty = True

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
        self.proxy_widget.setObjectName("gitlinkMainWidget")
        self.proxy_widget.setFixedSize(self.width, self.height)
        self.proxy_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.proxy.setWidget(self.proxy_widget)
        self.proxy.setPos(0, 0)

        self._build_ui()
        self.refresh_github_state()
        self._update_badges()
        self._update_file_selection_label()
        self.set_status("Idle")

    def _build_ui(self):
        palette = get_current_palette()
        node_color = QColor(palette.FRAME_COLORS["Green"]["color"])
        badge_rgba = f"{node_color.red()}, {node_color.green()}, {node_color.blue()}"

        self.proxy_widget.setStyleSheet(
            f"""
            QWidget#gitlinkMainWidget {{
                background-color: transparent;
                color: #E0E0E0;
                font-family: 'Segoe UI', sans-serif;
            }}
            QWidget#gitlinkMainWidget QLabel {{
                background-color: transparent;
            }}
            QFrame#gitlinkHeaderCard,
            QFrame#gitlinkSectionCard {{
                background-color: #232323;
                border: 1px solid #3A3A3A;
                border-radius: 10px;
            }}
            QLabel#gitlinkTitle {{
                color: #FFFFFF;
                font-size: 17px;
                font-weight: bold;
            }}
            QLabel#gitlinkHint {{
                color: #A5A5A5;
                font-size: 11px;
            }}
            QLabel#gitlinkFieldLabel {{
                color: #D6D6D6;
                font-size: 11px;
                font-weight: bold;
            }}
            QLabel#gitlinkBadge {{
                color: #F3F3F3;
                background-color: rgba({badge_rgba}, 0.14);
                border: 1px solid rgba({badge_rgba}, 0.28);
                border-radius: 10px;
                padding: 3px 8px;
                font-size: 11px;
                font-weight: bold;
            }}
            QLineEdit,
            QComboBox,
            QListWidget,
            QPlainTextEdit,
            QTextEdit {{
                background-color: #1A1A1A;
                color: #F3F3F3;
                border: 1px solid #3A3A3A;
                border-radius: 8px;
                padding: 6px 8px;
                selection-background-color: {node_color.name()};
            }}
            QLineEdit:focus,
            QComboBox:focus,
            QListWidget:focus,
            QPlainTextEdit:focus,
            QTextEdit:focus {{
                border-color: {node_color.name()};
            }}
            QPushButton {{
                background-color: #303030;
                color: #FFFFFF;
                border: 1px solid #474747;
                border-radius: 8px;
                padding: 7px 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #3E3E3E;
                border-color: {node_color.name()};
            }}
            QPushButton:pressed {{
                background-color: #2E2E2E;
            }}
            QPushButton:disabled {{
                background-color: #262626;
                color: #848484;
                border-color: #353535;
            }}
            QTabWidget::pane {{
                border: 1px solid #3F3F3F;
                background: #202020;
                border-radius: 8px;
            }}
            QTabBar::tab {{
                background: #282828;
                color: #A0A0A0;
                padding: 7px 12px;
                border: 1px solid #3F3F3F;
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                margin-right: 2px;
                font-weight: bold;
            }}
            QTabBar::tab:selected {{
                background: #202020;
                color: #FFFFFF;
                border-top: 2px solid {node_color.name()};
                border-bottom: 1px solid #202020;
            }}
            QListWidget::item {{
                padding: 4px 6px;
                border-radius: 6px;
            }}
            QListWidget::item:selected {{
                background: rgba({badge_rgba}, 0.22);
                color: #FFFFFF;
            }}
            """
        )

        root_layout = QVBoxLayout(self.proxy_widget)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(10)

        header_card = QFrame()
        header_card.setObjectName("gitlinkHeaderCard")
        header_card_layout = QVBoxLayout(header_card)
        header_card_layout.setContentsMargins(14, 14, 14, 14)
        header_card_layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)

        title_label = QLabel("Gitlink")
        title_label.setObjectName("gitlinkTitle")
        title_row.addWidget(title_label)
        title_row.addStretch()

        self.repo_badge = QLabel("Repo: none")
        self.repo_badge.setObjectName("gitlinkBadge")
        title_row.addWidget(self.repo_badge)

        self.scope_badge = QLabel("Scope: Selected")
        self.scope_badge.setObjectName("gitlinkBadge")
        title_row.addWidget(self.scope_badge)

        self.change_badge = QLabel("Changes: 0")
        self.change_badge.setObjectName("gitlinkBadge")
        title_row.addWidget(self.change_badge)
        header_card_layout.addLayout(title_row)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)

        self.status_label = QLabel("Idle")
        self.status_label.setObjectName("gitlinkBadge")
        status_row.addWidget(self.status_label)

        self.file_selection_label = QLabel("Files selected: 0")
        self.file_selection_label.setObjectName("gitlinkBadge")
        status_row.addWidget(self.file_selection_label)
        status_row.addStretch()
        header_card_layout.addLayout(status_row)
        root_layout.addWidget(header_card)

        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setDocumentMode(True)
        root_layout.addWidget(self.workspace_tabs, stretch=1)

        setup_tab = QWidget()
        setup_layout = QVBoxLayout(setup_tab)
        setup_layout.setContentsMargins(0, 0, 0, 0)
        setup_layout.setSpacing(8)

        setup_scroll = QScrollArea()
        setup_scroll.setWidgetResizable(True)
        setup_scroll.setFrameShape(QFrame.Shape.NoFrame)
        setup_scroll.setStyleSheet(GITLINK_SCROLLBAR_STYLE)
        setup_layout.addWidget(setup_scroll)

        setup_content = QWidget()
        setup_scroll.setWidget(setup_content)
        setup_content_layout = QVBoxLayout(setup_content)
        setup_content_layout.setContentsMargins(0, 0, 0, 0)
        setup_content_layout.setSpacing(8)

        repo_card = QFrame()
        repo_card.setObjectName("gitlinkSectionCard")
        repo_layout = QVBoxLayout(repo_card)
        repo_layout.setContentsMargins(12, 12, 12, 12)
        repo_layout.setSpacing(8)

        repo_title = QLabel("GitHub Source")
        repo_title.setStyleSheet("color: #FFFFFF; font-weight: bold;")
        repo_layout.addWidget(repo_title)

        self.github_state_label = QLabel("")
        self.github_state_label.setObjectName("gitlinkHint")
        self.github_state_label.setWordWrap(True)
        repo_layout.addWidget(self.github_state_label)

        repo_picker_row = QHBoxLayout()
        repo_picker_row.setContentsMargins(0, 0, 0, 0)
        repo_picker_row.setSpacing(8)

        self.load_repos_button = QPushButton("Load Repo List")
        self.load_repos_button.setIcon(qta.icon("fa5s.sync-alt", color="#FFFFFF"))
        self.load_repos_button.clicked.connect(self.load_github_repositories)
        repo_picker_row.addWidget(self.load_repos_button)

        self.repo_combo = PopupComboBox()
        self.repo_combo.setEditable(False)
        self.repo_combo.setMinimumWidth(220)
        self.repo_combo.apply_popup_style(node_color.name())
        self.repo_combo.about_to_show_popup.connect(self._ensure_github_repositories_loaded)
        self.repo_combo.currentTextChanged.connect(self._on_repo_selected)
        repo_picker_row.addWidget(self.repo_combo, stretch=1)
        repo_layout.addLayout(repo_picker_row)

        repo_label = QLabel("Repository")
        repo_label.setObjectName("gitlinkFieldLabel")
        repo_layout.addWidget(repo_label)

        self.repo_input = QLineEdit()
        self.repo_input.setPlaceholderText("owner/repo")
        self.repo_input.textChanged.connect(self._on_repo_details_changed)
        repo_layout.addWidget(self.repo_input)

        branch_row = QHBoxLayout()
        branch_row.setContentsMargins(0, 0, 0, 0)
        branch_row.setSpacing(8)

        self.branch_input = QLineEdit()
        self.branch_input.setPlaceholderText("Branch (blank = default)")
        self.branch_input.textChanged.connect(self._on_repo_details_changed)
        branch_row.addWidget(self.branch_input, stretch=1)

        self.load_tree_button = QPushButton("Load File Tree")
        self.load_tree_button.setIcon(qta.icon("fa5s.sitemap", color="#FFFFFF"))
        self.load_tree_button.clicked.connect(self.load_repository_tree)
        branch_row.addWidget(self.load_tree_button)
        repo_layout.addLayout(branch_row)

        setup_content_layout.addWidget(repo_card)

        scope_card = QFrame()
        scope_card.setObjectName("gitlinkSectionCard")
        scope_layout = QVBoxLayout(scope_card)
        scope_layout.setContentsMargins(12, 12, 12, 12)
        scope_layout.setSpacing(8)

        scope_title = QLabel("Access Scope")
        scope_title.setStyleSheet("color: #FFFFFF; font-weight: bold;")
        scope_layout.addWidget(scope_title)

        scope_hint = QLabel(
            "Pick full repo access for a broad stitched snapshot, or choose one or more files for a tighter context window."
        )
        scope_hint.setObjectName("gitlinkHint")
        scope_hint.setWordWrap(True)
        scope_layout.addWidget(scope_hint)

        scope_row = QHBoxLayout()
        scope_row.setContentsMargins(0, 0, 0, 0)
        scope_row.setSpacing(8)

        self.scope_combo = QComboBox()
        self.scope_combo.addItem("Selected Files", "selected")
        self.scope_combo.addItem("Full Repo Access", "full")
        self.scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        scope_row.addWidget(self.scope_combo, stretch=1)

        self.file_filter_input = QLineEdit()
        self.file_filter_input.setPlaceholderText("Filter files")
        self.file_filter_input.textChanged.connect(self._on_file_filter_changed)
        scope_row.addWidget(self.file_filter_input, stretch=1)
        scope_layout.addLayout(scope_row)

        file_action_row = QHBoxLayout()
        file_action_row.setContentsMargins(0, 0, 0, 0)
        file_action_row.setSpacing(8)

        self.select_visible_button = QPushButton("Select Visible")
        self.select_visible_button.clicked.connect(self._select_visible_files)
        file_action_row.addWidget(self.select_visible_button)

        self.clear_selection_button = QPushButton("Clear Selection")
        self.clear_selection_button.clicked.connect(self._clear_file_selection)
        file_action_row.addWidget(self.clear_selection_button)
        file_action_row.addStretch()
        scope_layout.addLayout(file_action_row)

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.setAlternatingRowColors(False)
        self.file_list.itemSelectionChanged.connect(self._on_file_selection_changed)
        self.file_list.setMinimumHeight(200)
        self.file_list.setStyleSheet(GITLINK_SCROLLBAR_STYLE)
        scope_layout.addWidget(self.file_list)

        setup_content_layout.addWidget(scope_card)

        workspace_card = QFrame()
        workspace_card.setObjectName("gitlinkSectionCard")
        workspace_layout = QVBoxLayout(workspace_card)
        workspace_layout.setContentsMargins(12, 12, 12, 12)
        workspace_layout.setSpacing(8)

        workspace_title = QLabel("Writable Checkout")
        workspace_title.setStyleSheet("color: #FFFFFF; font-weight: bold;")
        workspace_layout.addWidget(workspace_title)

        workspace_hint = QLabel(
            "Approved changes are only written to a local repo path. You can point Gitlink at an existing checkout or import a local snapshot first."
        )
        workspace_hint.setObjectName("gitlinkHint")
        workspace_hint.setWordWrap(True)
        workspace_layout.addWidget(workspace_hint)

        local_label = QLabel("Local Repo Path")
        local_label.setObjectName("gitlinkFieldLabel")
        workspace_layout.addWidget(local_label)

        self.local_root_input = QLineEdit()
        self.local_root_input.setPlaceholderText("Optional local checkout path")
        self.local_root_input.textChanged.connect(self._on_local_root_changed)
        workspace_layout.addWidget(self.local_root_input)

        workspace_button_row = QHBoxLayout()
        workspace_button_row.setContentsMargins(0, 0, 0, 0)
        workspace_button_row.setSpacing(8)

        self.browse_root_button = QPushButton("Browse")
        self.browse_root_button.setIcon(qta.icon("fa5s.folder-open", color="#FFFFFF"))
        self.browse_root_button.clicked.connect(self.browse_local_root)
        workspace_button_row.addWidget(self.browse_root_button)

        self.import_repo_button = QPushButton("Import Repo Snapshot")
        self.import_repo_button.setIcon(qta.icon("fa5s.download", color="#FFFFFF"))
        self.import_repo_button.clicked.connect(self.import_repository_snapshot)
        workspace_button_row.addWidget(self.import_repo_button)
        workspace_button_row.addStretch()
        workspace_layout.addLayout(workspace_button_row)

        setup_content_layout.addWidget(workspace_card)

        task_card = QFrame()
        task_card.setObjectName("gitlinkSectionCard")
        task_layout = QVBoxLayout(task_card)
        task_layout.setContentsMargins(12, 12, 12, 12)
        task_layout.setSpacing(8)

        task_title = QLabel("Task Prompt")
        task_title.setStyleSheet("color: #FFFFFF; font-weight: bold;")
        task_layout.addWidget(task_title)

        task_hint = QLabel(
            "Describe the code change you want. Gitlink will combine the prompt with the XML repo snapshot and return a previewable file set."
        )
        task_hint.setObjectName("gitlinkHint")
        task_hint.setWordWrap(True)
        task_layout.addWidget(task_hint)

        self.task_input = QPlainTextEdit()
        self.task_input.setPlaceholderText("Example: Add a Gitlink plugin node that loads repos, stitches XML context, and prepares user-approved writes.")
        self.task_input.setMinimumHeight(130)
        self.task_input.textChanged.connect(self._on_task_changed)
        task_layout.addWidget(self.task_input)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)

        self.build_context_button = QPushButton("Build XML Context")
        self.build_context_button.setIcon(qta.icon("fa5s.file-code", color="#FFFFFF"))
        self.build_context_button.clicked.connect(self.build_context_preview)
        button_row.addWidget(self.build_context_button)

        self.run_button = QPushButton("Generate Change Set")
        self.run_button.setIcon(qta.icon("fa5s.magic", color="#FFFFFF"))
        self.run_button.clicked.connect(self._request_gitlink_run)
        button_row.addWidget(self.run_button)

        self.apply_button = QPushButton("Apply Approved Changes")
        self.apply_button.setIcon(qta.icon("fa5s.save", color="#FFFFFF"))
        self.apply_button.clicked.connect(self.apply_approved_changes)
        self.apply_button.setEnabled(False)
        button_row.addWidget(self.apply_button)
        task_layout.addLayout(button_row)

        setup_content_layout.addWidget(task_card)
        setup_content_layout.addStretch()

        self.context_editor = QPlainTextEdit()
        self.context_editor.setReadOnly(True)
        self.context_editor.setStyleSheet(GITLINK_SCROLLBAR_STYLE)

        self.proposal_display = QTextEdit()
        self.proposal_display.setReadOnly(True)
        self.proposal_display.setStyleSheet(GITLINK_SCROLLBAR_STYLE)

        self.preview_editor = QPlainTextEdit()
        self.preview_editor.setReadOnly(True)
        self.preview_editor.setStyleSheet(GITLINK_SCROLLBAR_STYLE)

        self.workspace_tabs.addTab(setup_tab, qta.icon("fa5s.link", color="#CCCCCC"), "Setup")
        self.workspace_tabs.addTab(self.context_editor, qta.icon("fa5s.file-code", color="#CCCCCC"), "Context XML")
        self.workspace_tabs.addTab(self.proposal_display, qta.icon("fa5s.tasks", color="#CCCCCC"), "Proposal")
        self.workspace_tabs.addTab(self.preview_editor, qta.icon("fa5s.columns", color="#CCCCCC"), "Preview")

    def _get_github_token(self):
        # Delegates to the shared GitHubRestClient (doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md
        # section 1.6/4.2) - kept as a thin wrapper so every existing call site in this
        # file (load_github_repositories, _resolve_repo_and_branch, etc.) is unchanged.
        return self._github_client.get_token()

    def _github_headers(self):
        return self._github_client.build_headers()

    def _github_request(self, url, params=None, *, expect_json=True, timeout=25):
        return self._github_client.request(url, params, expect_json=expect_json, timeout=timeout)

    def refresh_github_state(self):
        token = self._get_github_token()
        if token:
            detail_text = "GitHub token detected. Private repositories and authenticated repo listing are enabled."
            self.github_state_label.setText("GitHub token ready. Private repos enabled.")
        else:
            detail_text = "No GitHub token saved. Public repos still work when you enter owner/repo manually."
            self.github_state_label.setText("No GitHub token saved. Public repos still work.")
        self.github_state_label.setToolTip(detail_text)

    def _on_repo_selected(self, repo_name):
        if self._suppress_ui_updates:
            return
        if repo_name:
            self.repo_input.setText(repo_name)
            self._clear_repository_tree(keep_status=True)

    def _ensure_github_repositories_loaded(self):
        if self.repo_combo.count() == 0:
            self.load_github_repositories()

    def _on_repo_details_changed(self):
        if self._suppress_ui_updates:
            return
        self.repo_state["repo"] = self.repo_input.text().strip()
        self.repo_state["branch"] = self.branch_input.text().strip()
        self._mark_context_dirty()
        self._update_badges()

    def _on_local_root_changed(self):
        if self._suppress_ui_updates:
            return
        self.repo_state["local_root"] = self.local_root_input.text().strip()
        self._mark_context_dirty()

    def _on_scope_changed(self):
        if self._suppress_ui_updates:
            return
        self.repo_state["scope_mode"] = self.scope_combo.currentData() or "selected"
        self._mark_context_dirty()
        self._update_badges()

    def _on_file_selection_changed(self):
        if self._suppress_ui_updates:
            return
        self.selected_paths = self.get_selected_paths()
        self._update_file_selection_label()
        self._mark_context_dirty()

    def _on_file_filter_changed(self):
        filter_text = self.file_filter_input.text().strip().lower()
        for index in range(self.file_list.count()):
            item = self.file_list.item(index)
            item_text = item.data(Qt.ItemDataRole.UserRole) or item.text()
            item.setHidden(bool(filter_text) and filter_text not in item_text.lower())

    def _on_task_changed(self):
        if self._suppress_ui_updates:
            return
        self.task_prompt = self.task_input.toPlainText()

    def seed_prompt(self, text):
        """Protocol method used by graphlink_window_actions.instantiate_seeded_plugin."""
        self.task_input.setPlainText(text)
        self._on_task_changed()

    def _mark_context_dirty(self):
        self._context_dirty = True
        self.context_summary = ""
        if self.pending_changes:
            self.clear_proposal(keep_context=True)

    def _update_badges(self):
        repo_text = self.repo_input.text().strip()
        branch_text = self.branch_input.text().strip()
        if repo_text:
            repo_label = repo_text if not branch_text else f"{repo_text}@{branch_text}"
        else:
            repo_label = "none"
        self.repo_badge.setText(f"Repo: {_compact_label_text(repo_label, limit=32)}")
        scope_label = "Full Repo" if (self.scope_combo.currentData() == "full") else "Selected"
        self.scope_badge.setText(f"Scope: {scope_label}")
        self.change_badge.setText(f"Changes: {len(self.pending_changes)}")

    def _update_file_selection_label(self):
        selected_count = len(self.get_selected_paths())
        total_count = len(self.repo_file_paths)
        label_text = f"Files selected: {selected_count}"
        if total_count:
            label_text += f" / {total_count}"
        self.file_selection_label.setText(label_text)

    def load_github_repositories(self):
        self.refresh_github_state()
        if not self._get_github_token():
            self.set_status("Error: Save a GitHub token in Settings > Integrations to load your repo list.")
            return

        try:
            repos = []
            page = 1
            while page <= MAX_REPO_PAGES:
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
                if len(page_payload) < 100:
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

        self._suppress_ui_updates = True
        self.repo_input.setText(repo_name)
        self.branch_input.setText(branch_name)
        self._suppress_ui_updates = False
        self.repo_state["repo"] = repo_name
        self.repo_state["branch"] = branch_name
        self._update_badges()
        return repo_name, branch_name

    def _clear_repository_tree(self, keep_status=False):
        self.repo_file_entries = []
        self.repo_file_paths = []
        self.selected_paths = []
        self.file_list.clear()
        self._update_file_selection_label()
        if not keep_status:
            self.set_status("Repository file tree cleared.")

    def load_repository_tree(self):
        self.refresh_github_state()
        try:
            repo_name, branch_name = self._resolve_repo_and_branch()
            tree_payload = self._github_request(
                f"https://api.github.com/repos/{repo_name}/git/trees/{quote(branch_name, safe='')}",
                params={"recursive": 1},
            )
            tree_items = tree_payload.get("tree", [])
            file_entries = []
            for item in tree_items:
                path_text = item.get("path", "")
                if item.get("type") != "blob" or not path_text or not _is_repo_text_path(path_text):
                    continue
                file_entries.append({
                    "path": path_text,
                    "sha": item.get("sha", ""),
                    "size": item.get("size", 0),
                })

            self.repo_file_entries = sorted(file_entries, key=lambda entry: entry["path"].lower())
            self.repo_file_paths = [entry["path"] for entry in self.repo_file_entries]
            self._populate_file_list(self.repo_file_paths)

            status_text = f"Loaded {len(self.repo_file_paths)} text file paths from {repo_name}@{branch_name}."
            if tree_payload.get("truncated"):
                status_text += " GitHub reported the tree as truncated."
            self.set_status(status_text)
            self._mark_context_dirty()
        except Exception as exc:
            self.set_status(f"Error: {exc}")

    def _populate_file_list(self, paths):
        previous_selection = set(self.get_selected_paths())
        self._suppress_ui_updates = True
        self.file_list.clear()
        for path_text in paths:
            item = QListWidgetItem(path_text)
            item.setData(Qt.ItemDataRole.UserRole, path_text)
            self.file_list.addItem(item)
            if path_text in previous_selection or path_text in self.selected_paths:
                item.setSelected(True)
        self._suppress_ui_updates = False
        self._on_file_filter_changed()
        self.selected_paths = self.get_selected_paths()
        self._update_file_selection_label()

    def _select_visible_files(self):
        self._suppress_ui_updates = True
        for index in range(self.file_list.count()):
            item = self.file_list.item(index)
            if not item.isHidden():
                item.setSelected(True)
        self._suppress_ui_updates = False
        self._on_file_selection_changed()

    def _clear_file_selection(self):
        self.file_list.clearSelection()
        self._on_file_selection_changed()

    def get_selected_paths(self):
        selected_paths = []
        for item in self.file_list.selectedItems():
            try:
                selected_paths.append(_normalize_repo_path(item.data(Qt.ItemDataRole.UserRole) or item.text()))
            except RuntimeError:
                continue
        return sorted(set(selected_paths), key=str.lower)

    def browse_local_root(self):
        initial_dir = self.local_root_input.text().strip() or str(Path.home())
        chosen_dir = QFileDialog.getExistingDirectory(None, "Select Local Repository Root", initial_dir)
        if chosen_dir:
            self.local_root_input.setText(chosen_dir)
            self.repo_state["local_root"] = chosen_dir
            self._mark_context_dirty()

    def _default_import_root(self, repo_name, branch_name):
        safe_repo = repo_name.replace("/", "__")
        safe_branch = branch_name.replace("/", "__")
        return Path.home() / ".graphlink" / "gitlink_repos" / safe_repo / safe_branch

    def _download_repository_snapshot(self, repo_name, branch_name, target_root):
        if target_root.exists() and any(target_root.iterdir()):
            return target_root

        target_root.parent.mkdir(parents=True, exist_ok=True)
        archive_bytes = self._github_request(
            f"https://api.github.com/repos/{repo_name}/zipball/{quote(branch_name, safe='')}",
            expect_json=False,
            timeout=60,
        )

        with tempfile.TemporaryDirectory(prefix="gitlink_import_") as temp_dir:
            temp_path = Path(temp_dir)
            archive_path = temp_path / "repo.zip"
            extract_root = temp_path / "extract"
            extract_root.mkdir(parents=True, exist_ok=True)
            archive_path.write_bytes(archive_bytes)

            with zipfile.ZipFile(archive_path) as archive:
                archive.extractall(extract_root)

            extracted_dirs = [item for item in extract_root.iterdir() if item.is_dir()]
            extracted_root = extracted_dirs[0] if extracted_dirs else extract_root

            if target_root.exists():
                return target_root

            shutil.move(str(extracted_root), str(target_root))

        return target_root

    def _ensure_repository_snapshot(self):
        repo_name, branch_name = self._resolve_repo_and_branch()
        local_root_text = self.local_root_input.text().strip()
        if local_root_text:
            root_path = Path(local_root_text).expanduser()
            if root_path.exists():
                return root_path
            raise RuntimeError("The selected local repo path does not exist.")

        imported_root = self.repo_state.get("imported_root", "")
        if imported_root:
            imported_path = Path(imported_root)
            if imported_path.exists():
                self._suppress_ui_updates = True
                self.local_root_input.setText(str(imported_path))
                self._suppress_ui_updates = False
                self.repo_state["local_root"] = str(imported_path)
                return imported_path

        target_root = self._default_import_root(repo_name, branch_name)
        target_path = self._download_repository_snapshot(repo_name, branch_name, target_root)
        self.repo_state["imported_root"] = str(target_path)
        self.repo_state["local_root"] = str(target_path)
        self._suppress_ui_updates = True
        self.local_root_input.setText(str(target_path))
        self._suppress_ui_updates = False
        return target_path

    def import_repository_snapshot(self):
        try:
            snapshot_root = self._ensure_repository_snapshot()
            self.set_status(f"Repo snapshot ready at {snapshot_root}.")
            self._mark_context_dirty()
        except Exception as exc:
            self.set_status(f"Error: {exc}")

    def _read_local_repo_file(self, local_root, repo_path):
        file_path = _safe_local_target(local_root, repo_path)
        if not file_path.exists():
            raise RuntimeError(f"Local checkout is missing `{repo_path}`.")
        if file_path.is_dir():
            raise RuntimeError(f"`{repo_path}` resolves to a directory, not a file.")
        return _decode_text_bytes(file_path.read_bytes())

    def _fetch_github_file_text(self, repo_name, branch_name, repo_path):
        content_payload = self._github_request(
            f"https://api.github.com/repos/{repo_name}/contents/{quote(repo_path, safe='/')}",
            params={"ref": branch_name},
        )
        if isinstance(content_payload, list):
            raise RuntimeError(f"`{repo_path}` resolves to a directory, not a file.")

        if content_payload.get("encoding") == "base64" and content_payload.get("content"):
            return _decode_text_bytes(base64.b64decode(content_payload["content"]))

        download_url = content_payload.get("download_url")
        if download_url:
            response = requests.get(download_url, timeout=25)
            response.raise_for_status()
            return response.text

        raise RuntimeError(f"GitHub did not return file contents for `{repo_path}`.")

    def _scan_local_repo_paths(self, local_root):
        root_path = Path(local_root).expanduser()
        if not root_path.exists():
            raise RuntimeError("The selected local repo path does not exist.")

        collected = []
        for file_path in root_path.rglob("*"):
            if not file_path.is_file():
                continue
            relative = file_path.relative_to(root_path).as_posix()
            if any(part in IGNORED_LOCAL_DIR_NAMES for part in PurePosixPath(relative).parts):
                continue
            if not _is_repo_text_path(relative):
                continue
            collected.append(relative)
        return sorted(collected, key=str.lower)

    def _resolve_scope_paths(self, local_root=None):
        scope_mode = self.scope_combo.currentData() or "selected"
        if scope_mode == "selected":
            selected_paths = self.get_selected_paths()
            if not selected_paths:
                raise RuntimeError("Select one or more files or switch to Full Repo Access.")
            return selected_paths

        if self.repo_file_paths:
            return list(self.repo_file_paths)
        if local_root:
            return self._scan_local_repo_paths(local_root)
        raise RuntimeError("Load the file tree first so Gitlink knows which repository files to stitch together.")

    def build_context_bundle(self):
        repo_name, branch_name = self._resolve_repo_and_branch()
        local_root_text = self.local_root_input.text().strip()
        local_root = Path(local_root_text).expanduser() if local_root_text else None
        if local_root and not local_root.exists():
            raise RuntimeError("The selected local repo path does not exist.")

        if (self.scope_combo.currentData() or "selected") == "full" and local_root is None:
            local_root = self._ensure_repository_snapshot()

        scope_paths = self._resolve_scope_paths(local_root=local_root)
        records = []
        included_file_count = 0
        omitted_for_budget = 0
        load_errors = 0

        for repo_path in scope_paths:
            normalized_path = _normalize_repo_path(repo_path)
            source_origin = "github"
            try:
                if local_root is not None:
                    source_text = self._read_local_repo_file(local_root, normalized_path)
                    source_origin = "local"
                else:
                    source_text = self._fetch_github_file_text(repo_name, branch_name, normalized_path)
                visible_text, source_truncated = _truncate_for_context(source_text)
                records.append({
                    "path": normalized_path,
                    "source": source_origin,
                    "content": visible_text,
                    "original_chars": len(source_text),
                    "source_truncated": source_truncated,
                    "included": False,
                })
            except Exception as exc:
                load_errors += 1
                records.append({
                    "path": normalized_path,
                    "source": source_origin,
                    "error": _clean_text(exc, limit=180) or "Unknown file load error.",
                })

        current_chars = 0
        file_blocks = []
        for record in records:
            if record.get("error"):
                continue
            candidate_block = _xml_file_block(
                record["path"],
                record["content"],
                truncated=record.get("source_truncated", False),
                original_chars=record.get("original_chars", 0),
            )
            if file_blocks and (current_chars + len(candidate_block) > MAX_CONTEXT_CHARS):
                omitted_for_budget += 1
                continue
            record["included"] = True
            file_blocks.append(candidate_block)
            current_chars += len(candidate_block)
            included_file_count += 1

        manifest_lines = []
        for index, record in enumerate(records):
            if index >= MAX_MANIFEST_ENTRIES:
                break
            attrs = [
                f'path="{html.escape(record["path"], quote=True)}"',
                f'source="{html.escape(record.get("source", "unknown"), quote=True)}"',
            ]
            if record.get("error"):
                attrs.append(f'error="{html.escape(record["error"], quote=True)}"')
            else:
                attrs.append(f'included="{str(bool(record.get("included"))).lower()}"')
                attrs.append(f'chars="{max(0, int(record.get("original_chars", 0)))}"')
                attrs.append(f'truncated="{str(bool(record.get("source_truncated"))).lower()}"')
                if not record.get("included"):
                    attrs.append('omitted="true"')
                    attrs.append('reason="context_budget"')
            manifest_lines.append(f"    <file {' '.join(attrs)} />")

        manifest_omitted = max(0, len(records) - MAX_MANIFEST_ENTRIES)
        if manifest_omitted:
            manifest_lines.append(f'    <more count="{manifest_omitted}" reason="manifest_budget" />')

        scope_mode = self.scope_combo.currentData() or "selected"
        scope_label = "full_repo" if scope_mode == "full" else "selected_files"
        xml_parts = [
            f'<gitlink_context repository="{html.escape(repo_name, quote=True)}" branch="{html.escape(branch_name, quote=True)}" scope="{scope_label}">',
            f"  <summary scanned_files=\"{len(records)}\" loaded_files=\"{len(records) - load_errors}\" included_files=\"{included_file_count}\" load_errors=\"{load_errors}\" context_omissions=\"{omitted_for_budget}\" />",
            "  <manifest>",
            *manifest_lines,
            "  </manifest>",
            "  <files>",
            *file_blocks,
            "  </files>",
            "</gitlink_context>",
        ]
        context_xml = "\n".join(xml_parts)

        source_root = str(local_root) if local_root is not None else "github"
        summary_parts = [
            f"Scanned {len(records)} files",
            f"loaded {len(records) - load_errors}",
            f"included {included_file_count}",
        ]
        if omitted_for_budget:
            summary_parts.append(f"omitted {omitted_for_budget} for context budget")
        if load_errors:
            summary_parts.append(f"hit {load_errors} load errors")
        summary_parts.append(f"source={source_root}")
        context_summary = ", ".join(summary_parts) + "."

        self.context_xml = context_xml
        self.context_stats = {
            "scanned_files": len(records),
            "loaded_files": len(records) - load_errors,
            "included_files": included_file_count,
            "load_errors": load_errors,
            "context_omissions": omitted_for_budget,
            "source_root": source_root,
            "summary": context_summary,
        }
        self.context_summary = context_summary
        self.last_context_paths = [record["path"] for record in records if record.get("included")]
        self.context_editor.setPlainText(context_xml)
        self.workspace_tabs.setCurrentIndex(1)
        self._context_dirty = False
        self._update_badges()
        return context_xml

    def build_context_preview(self):
        try:
            self.build_context_bundle()
            self.set_status("XML context ready.")
        except Exception as exc:
            self.set_status(f"Error: {exc}")

    def get_task_prompt(self):
        return self.task_input.toPlainText()

    def build_change_request(self):
        if not self.context_xml.strip():
            raise RuntimeError("Build the XML context before generating a change set.")
        return {
            "repo": self.repo_state.get("repo") or self.repo_input.text().strip(),
            "branch": self.repo_state.get("branch") or self.branch_input.text().strip(),
            "scope_label": "Full Repo Access" if (self.scope_combo.currentData() == "full") else "Selected Files",
            "task_prompt": self.get_task_prompt(),
            "context_xml": self.context_xml,
            "context_summary": self.context_summary,
        }

    def _request_gitlink_run(self):
        if not self.get_task_prompt().strip():
            self.set_error("Describe the code change you want before generating a change set.")
            return

        try:
            if self._context_dirty or not self.context_xml.strip():
                self.build_context_bundle()
            self.gitlink_requested.emit(self)
        except Exception as exc:
            self.set_error(str(exc))

    def _build_proposal_markdown(self, proposal):
        repo_name = self.repo_state.get("repo") or self.repo_input.text().strip()
        branch_name = self.repo_state.get("branch") or self.branch_input.text().strip()
        summary = _clean_text(proposal.get("summary"), limit=500)
        rationale = _clean_text(proposal.get("rationale"), limit=1200)
        notes = proposal.get("notes", []) if isinstance(proposal.get("notes"), list) else []
        write_intent = proposal.get("write_intent", "blocked")
        files = proposal.get("files", []) if isinstance(proposal.get("files"), list) else []

        lines = [
            "## Gitlink Proposal",
            "",
            f"- Repository: {repo_name or 'Unknown repo'}",
            f"- Branch: {branch_name or 'Unknown branch'}",
            f"- Intent: {write_intent.replace('_', ' ').title()}",
            f"- Files Returned: {len(files)}",
            "",
            "### Summary",
            summary or "No summary returned.",
            "",
            "### Rationale",
            rationale or "No rationale returned.",
        ]

        if notes:
            lines.extend(["", "### Notes"])
            lines.extend(f"- {note}" for note in notes)

        if files:
            lines.extend(["", "### Proposed File Writes"])
            for file_item in files:
                lines.append(
                    f"- `{file_item.get('path', '')}` [{file_item.get('operation', 'update')}] - {file_item.get('reason', 'No reason supplied.')}"
                )

        return "\n".join(lines)

    def _read_original_text_for_preview(self, repo_path):
        local_root_text = self.local_root_input.text().strip()
        if local_root_text:
            try:
                return self._read_local_repo_file(local_root_text, repo_path)
            except Exception:
                pass

        repo_name = self.repo_state.get("repo") or self.repo_input.text().strip()
        branch_name = self.repo_state.get("branch") or self.branch_input.text().strip()
        if repo_name and branch_name:
            try:
                return self._fetch_github_file_text(repo_name, branch_name, repo_path)
            except Exception:
                pass
        return ""

    def _build_preview_text(self, files):
        preview_parts = []
        for file_item in files:
            path_text = file_item.get("path", "")
            operation = file_item.get("operation", "update")
            original_text = self._read_original_text_for_preview(path_text)
            proposed_text = file_item.get("content", "") if operation in {"update", "create"} else ""

            if operation == "create":
                diff_lines = list(
                    difflib.unified_diff(
                        [],
                        proposed_text.splitlines(),
                        fromfile=f"a/{path_text}",
                        tofile=f"b/{path_text}",
                        lineterm="",
                    )
                )
            elif operation == "delete":
                diff_lines = list(
                    difflib.unified_diff(
                        original_text.splitlines(),
                        [],
                        fromfile=f"a/{path_text}",
                        tofile=f"b/{path_text}",
                        lineterm="",
                    )
                )
            else:
                diff_lines = list(
                    difflib.unified_diff(
                        original_text.splitlines(),
                        proposed_text.splitlines(),
                        fromfile=f"a/{path_text}",
                        tofile=f"b/{path_text}",
                        lineterm="",
                    )
                )

            preview_parts.append(f"### {path_text} [{operation}]\n")
            if diff_lines:
                preview_parts.append("\n".join(diff_lines))
            else:
                preview_parts.append("No textual diff available.")
            preview_parts.append("")

        return "\n".join(preview_parts).strip()

    def clear_proposal(self, keep_context=False):
        self.proposal_data = {}
        self.proposal_markdown = ""
        self.preview_text = ""
        self.pending_changes = []
        self.change_state = GITLINK_STATE_DRAFT
        self._approved_fingerprint = None
        self.proposal_display.clear()
        self.preview_editor.clear()
        self.apply_button.setEnabled(False)
        self.change_badge.setText("Changes: 0")
        if not keep_context and not self._context_dirty:
            self.context_xml = ""
            self.context_editor.clear()
            self.context_summary = ""

    def set_proposal(self, proposal, preview_override=None):
        if self.is_disposed:
            return

        self.proposal_data = dict(proposal or {})
        self.pending_changes = list(self.proposal_data.get("files", []) or [])
        self.change_state = GITLINK_STATE_PREVIEWED if self.pending_changes else GITLINK_STATE_DRAFT
        self._approved_fingerprint = None
        self.proposal_markdown = self._build_proposal_markdown(self.proposal_data)
        self.preview_text = preview_override if preview_override is not None else self._build_preview_text(self.pending_changes)

        self.proposal_display.setMarkdown(self.proposal_markdown)
        self.preview_editor.setPlainText(self.preview_text)
        self.apply_button.setEnabled(bool(self.pending_changes))
        self.workspace_tabs.setCurrentIndex(2 if self.proposal_markdown else 0)
        self._update_badges()
        self.set_status("Change set ready for review.")

    def restore_saved_state(self, *, repo_state=None, repo_file_paths=None, selected_paths=None, task_prompt="", context_xml="", context_stats=None, proposal_data=None, preview_text=""):
        self._suppress_ui_updates = True
        repo_state = dict(repo_state or {})
        self.repo_state.update(repo_state)
        self.repo_input.setText(self.repo_state.get("repo", ""))
        self.branch_input.setText(self.repo_state.get("branch", ""))
        self.local_root_input.setText(self.repo_state.get("local_root", ""))

        scope_mode = self.repo_state.get("scope_mode", "selected")
        scope_index = self.scope_combo.findData(scope_mode)
        if scope_index >= 0:
            self.scope_combo.setCurrentIndex(scope_index)

        self.task_input.setPlainText(task_prompt or "")
        self.task_prompt = task_prompt or ""
        self.repo_file_paths = list(repo_file_paths or [])
        self.repo_file_entries = [{"path": path_text, "sha": "", "size": 0} for path_text in self.repo_file_paths]
        normalized_selected = []
        for path_text in (selected_paths or []):
            try:
                normalized_selected.append(_normalize_repo_path(path_text))
            except RuntimeError:
                continue
        self.selected_paths = sorted(set(normalized_selected), key=str.lower)
        self._populate_file_list(self.repo_file_paths)
        self.context_xml = context_xml or ""
        self.context_stats = dict(context_stats or {})
        self.context_summary = _clean_text(self.context_stats.get("summary"), limit=300)
        self.context_editor.setPlainText(self.context_xml)
        self._context_dirty = not bool(self.context_xml.strip())
        self._suppress_ui_updates = False

        if proposal_data:
            self.set_proposal(proposal_data, preview_override=preview_text or "")
        else:
            self.clear_proposal(keep_context=True)

        self._update_badges()
        self._update_file_selection_label()

    def apply_approved_changes(self):
        if not self.pending_changes:
            self.set_error("There is no approved change set to write.")
            return

        local_root_text = self.local_root_input.text().strip()
        if not local_root_text:
            self.set_error("Select or import a local repository path before applying changes.")
            return

        local_root = Path(local_root_text).expanduser()
        if not local_root.exists():
            self.set_error("The selected local repository path does not exist.")
            return

        # Fingerprint the exact change set being shown before the confirmation dialog opens,
        # then re-verify it right before writing. If a background result (e.g. a re-run
        # completing) mutated pending_changes while the dialog was open, this refuses to
        # write a change set the user never actually saw or approved.
        shown_fingerprint = _fingerprint_changes(self.pending_changes)

        reply = QMessageBox.question(
            None,
            "Apply Gitlink Changes",
            f"Write {len(self.pending_changes)} file changes into:\n{local_root}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            self.set_status("Write cancelled.")
            return

        self.change_state = GITLINK_STATE_APPROVED
        self._approved_fingerprint = shown_fingerprint

        current_fingerprint = _fingerprint_changes(self.pending_changes)
        if self.change_state != GITLINK_STATE_APPROVED or current_fingerprint != self._approved_fingerprint:
            self.change_state = GITLINK_STATE_PREVIEWED
            self._approved_fingerprint = None
            self.set_error("The proposed change set changed after approval. Review it again before applying.")
            return

        written_files = 0
        try:
            for file_item in self.pending_changes:
                path_text = file_item.get("path", "")
                operation = file_item.get("operation", "update")
                target_path = _safe_local_target(local_root, path_text)

                if operation == "delete":
                    if target_path.exists():
                        target_path.unlink()
                        written_files += 1
                    continue

                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_text(file_item.get("content", ""), encoding="utf-8")
                written_files += 1

            self.change_state = GITLINK_STATE_APPLIED
            self.set_status(f"Applied {written_files} file changes.")
        except Exception as exc:
            self.change_state = GITLINK_STATE_PREVIEWED
            self._approved_fingerprint = None
            self.set_error(f"Failed to write approved changes: {exc}")

    def set_running_state(self, is_running):
        for widget in (
            self.load_repos_button,
            self.repo_combo,
            self.repo_input,
            self.branch_input,
            self.load_tree_button,
            self.scope_combo,
            self.file_filter_input,
            self.select_visible_button,
            self.clear_selection_button,
            self.file_list,
            self.local_root_input,
            self.browse_root_button,
            self.import_repo_button,
            self.build_context_button,
            self.run_button,
        ):
            widget.setEnabled(not is_running)

        self.task_input.setReadOnly(is_running)
        self.apply_button.setEnabled(bool(self.pending_changes) and not is_running)
        self.run_button.setText("Working..." if is_running else "Generate Change Set")

        if is_running:
            self.set_status("Preparing change set...")
        elif self.status.startswith("Error"):
            return
        elif self.pending_changes:
            self.set_status("Change set ready for review.")
        else:
            self.set_status("Idle")

    def set_status(self, status_text):
        if self.is_disposed:
            return

        self.status = status_text
        self.status_label.setText(_compact_label_text(status_text, limit=26))
        self.status_label.setToolTip(status_text)

        if "Preparing" in status_text or "Importing" in status_text:
            color = get_semantic_color("status_info")
        elif "Error" in status_text:
            color = get_semantic_color("status_error")
        elif "ready" in status_text.lower() or "applied" in status_text.lower():
            color = get_semantic_color("status_success")
        else:
            color = QColor("#A2A2A2")

        self.status_label.setStyleSheet(
            f"color: {color.name()}; background-color: rgba({color.red()}, {color.green()}, {color.blue()}, 0.1); "
            f"border: 1px solid rgba({color.red()}, {color.green()}, {color.blue()}, 0.24); border-radius: 10px; "
            "padding: 3px 8px; font-size: 11px; font-weight: bold;"
        )

    def set_error(self, error_message):
        if self.is_disposed:
            return

        self.proposal_data = {}
        self.pending_changes = []
        self.proposal_markdown = f"## Error\n\n{error_message}"
        self.preview_text = ""
        self.proposal_display.setMarkdown(self.proposal_markdown)
        self.preview_editor.setPlainText("")
        self.apply_button.setEnabled(False)
        self._update_badges()
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

        try:
            self.task_input.textChanged.disconnect(self._on_task_changed)
        except (TypeError, RuntimeError):
            pass

    def boundingRect(self):
        padding = self.CONNECTION_DOT_OFFSET + self.CONNECTION_DOT_RADIUS
        return QRectF(-padding, 0, self.width + 2 * padding, self.height)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = get_current_palette()
        node_color = QColor(palette.FRAME_COLORS["Green"]["color"])

        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width, self.height, 10, 10)
        painter.setBrush(QColor("#2D2D2D"))

        pen = QPen(node_color, 1.5)
        if self.isSelected():
            pen = QPen(palette.SELECTION, 2)
        elif self.is_search_match:
            pen = QPen(get_semantic_color("search_highlight"), 2)
        elif self.hovered:
            pen = QPen(QColor("#FFFFFF"), 2)

        painter.setPen(pen)
        painter.drawPath(path)

        dot_color = node_color if not (self.isSelected() or self.hovered) else pen.color().lighter(110)
        painter.setBrush(dot_color)
        painter.setPen(Qt.PenStyle.NoPen)

        dot_rect_left = QRectF(
            -self.CONNECTION_DOT_RADIUS,
            (self.height / 2) - self.CONNECTION_DOT_RADIUS,
            self.CONNECTION_DOT_RADIUS * 2,
            self.CONNECTION_DOT_RADIUS * 2,
        )
        painter.drawPie(dot_rect_left, 90 * 16, -180 * 16)

        dot_rect_right = QRectF(
            self.width - self.CONNECTION_DOT_RADIUS,
            (self.height / 2) - self.CONNECTION_DOT_RADIUS,
            self.CONNECTION_DOT_RADIUS * 2,
            self.CONNECTION_DOT_RADIUS * 2,
        )
        painter.drawPie(dot_rect_right, 90 * 16, 180 * 16)

        if self.is_collapsed:
            painter.setPen(QColor("#FFFFFF"))
            painter.setFont(canvas_font(self.scene(), weight=QFont.Weight.Bold))
            painter.drawText(QRectF(42, 0, self.width - 84, self.height), Qt.AlignmentFlag.AlignVCenter, "Gitlink")
            qta.icon("fa5s.link", color=node_color.name()).paint(painter, QRect(12, 10, 20, 20))
            self.collapse_button_rect = QRectF(self.width - 35, 5, 30, 30)
            qta.icon(
                "fa5s.expand-arrows-alt",
                color="#FFFFFF" if self.hovered else "#888888",
            ).paint(painter, QRect(int(self.width - 30), 10, 20, 20))
        else:
            if self.hovered:
                self.collapse_button_rect = QRectF(self.width - 35, 5, 30, 30)
                painter.setBrush(QColor(255, 255, 255, 30))
                painter.setPen(QColor(255, 255, 255, 150))
                painter.drawRoundedRect(self.collapse_button_rect.adjusted(6, 6, -6, -6), 4, 4)
                painter.setPen(QPen(QColor("#FFFFFF"), 2))
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
