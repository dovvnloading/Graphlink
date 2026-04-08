from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QToolBar,
    QToolButton, QLineEdit, QPushButton, QMessageBox, QSizePolicy, QLabel, QComboBox,
    QFileDialog
)
from PySide6.QtCore import Qt, QSize, QPointF, QTimer, QEvent
from PySide6.QtGui import QKeySequence, QGuiApplication, QCursor, QShortcut, QIcon, QColor
import qtawesome as qta
import os
import tempfile
from datetime import datetime

from graphite_widgets import PinOverlay, SearchOverlay, TokenCounterWidget, TokenEstimator, ChatInputTextEdit
from graphite_ui_components import NotificationBanner, DocumentViewerPanel
from graphite_canvas_items import Note, Frame, Container
from graphite_node import ChatNode, CodeNode, ThinkingNode
from graphite_pycoder import PyCoderNode
from graphite_plugin_code_sandbox import CodeSandboxNode
from graphite_web import WebNode
from graphite_conversation_node import ConversationNode
from graphite_reasoning import ReasoningNode
from graphite_html_view import HtmlViewNode
from graphite_plugin_artifact import ArtifactNode
from graphite_plugin_workflow import WorkflowNode
from graphite_plugin_graph_diff import GraphDiffNode
from graphite_plugin_quality_gate import QualityGateNode
from graphite_plugin_code_review import CodeReviewNode
from graphite_plugin_gitlink import GitlinkNode

from graphite_library_dialog import ChatLibraryDialog
from graphite_system_dialogs import HelpDialog, AboutDialog
from graphite_settings_dialogs import SettingsDialog

from graphite_session import ChatSessionManager
from graphite_command_palette import CommandManager
from graphite_plugin_portal import PluginPortal
from graphite_plugin_picker import PluginFlyoutPanel
from graphite_agents import ChatAgent
from graphite_audio import (
    AudioValidationError,
    SUPPORTED_AUDIO_EXTENSIONS,
    format_duration,
    inspect_audio_file,
)
from graphite_file_handler import FileHandler
import graphite_config as config
import api_provider
from graphite_config import get_current_palette, get_neutral_button_colors, get_semantic_color

from graphite_prompts import BASE_SYSTEM_PROMPT, THINKING_INSTRUCTIONS_PROMPT
from graphite_window_actions import WindowActionsMixin
from graphite_window_navigation import WindowNavigationMixin
from graphite_update import APP_VERSION, UpdateCheckWorker

class ChatWindow(QMainWindow, WindowActionsMixin, WindowNavigationMixin):
    def __init__(self, settings_manager):
        super().__init__()
        from graphite_view import ChatView
        
        self.settings_manager = settings_manager
        self.file_handler = FileHandler()
        self.setAcceptDrops(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint | Qt.WindowType.WindowCloseButtonHint)
        self.setGeometry(100, 100, 1200, 800)
        self.library_dialog = None
        self.settings_panel = None
        self.help_panel = None
        self._initial_show_complete = False
        self._overlay_update_pending = False
        self._startup_update_check_ran = False
        self.update_check_worker = None
        self._update_check_status_target = None
        self._update_check_manual = False

        icon_path = r"C:\Users\Admin\source\repos\graphite_app\assets\graphite.ico"
        self.setWindowIcon(QIcon(str(icon_path)))

        self.session_manager = ChatSessionManager(self)
        self.plugin_portal = PluginPortal(self)
        self.update_title_bar()
        self.reinitialize_agent()

        self.chat_thread = None
        self.takeaway_thread = None
        self.explainer_thread = None
        self.chart_thread = None
        self.group_summary_thread = None
        self.image_gen_thread = None
        self.code_exec_thread = None
        self.pycoder_agent_thread = None
        self.pycoder_exec_thread = None
        self.sandbox_thread = None
        self.web_worker_thread = None
        self.conversation_node_thread = None
        self.reasoning_thread = None
        self.artifact_thread = None
        self.workflow_thread = None
        self.graph_diff_thread = None
        self.quality_gate_thread = None
        self.code_review_thread = None
        self.gitlink_thread = None
        self._main_request_active = False
        self._main_request_cancel_pending = False
        self._main_request_cancel_callback = None

        self.container = QWidget()
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        content_widget = QWidget()
        content_layout = QHBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.doc_viewer_panel = DocumentViewerPanel(self)
        self.doc_viewer_panel.close_requested.connect(self.hide_document_view)
        self.doc_viewer_panel.setVisible(False)
        content_layout.addWidget(self.doc_viewer_panel)

        self.chat_view = ChatView(self)
        self.chat_view.setAcceptDrops(True)
        self.chat_view.installEventFilter(self)
        self.chat_view.viewport().installEventFilter(self)
        content_layout.addWidget(self.chat_view)

        self.notification_banner = NotificationBanner(self.chat_view)

        self.pin_overlay = PinOverlay(self.chat_view, self)
        self.pin_overlay.closed.connect(self._handle_pin_overlay_closed)
        self.pin_overlay.setVisible(False)

        self.token_estimator = TokenEstimator()
        self.total_session_tokens = 0
        self.token_counter_widget = TokenCounterWidget(self.chat_view)
        self.token_counter_widget.setVisible(self.settings_manager.get_show_token_counter())

        self.toolbar = QToolBar()
        container_layout.addWidget(self.toolbar)

        library_btn = QToolButton(); library_btn.setText("Library"); library_btn.setObjectName("actionButton")
        library_btn.clicked.connect(self.show_library); self.toolbar.addWidget(library_btn)
        save_btn = QToolButton(); save_btn.setText("Save"); save_btn.setObjectName("actionButton")
        save_btn.clicked.connect(self.save_chat); self.toolbar.addWidget(save_btn)
        self.toolbar.addSeparator()
        self.setup_toolbar(self.toolbar)

        container_layout.addWidget(content_widget)

        self.input_widget = QWidget()
        self.input_widget.setObjectName("chatInputRow")
        self.input_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.input_widget.setStyleSheet("""
            QWidget#chatInputRow {
                background: transparent;
                border: none;
            }
        """)
        input_layout = QHBoxLayout(self.input_widget)
        input_layout.setContentsMargins(8, 10, 8, 10)
        input_layout.setSpacing(8)
        
        self.pending_attachments = []
        self.attach_file_btn = QPushButton()
        self.attach_file_btn.setIcon(qta.icon('fa5s.paperclip', color='#cccccc'))
        self.attach_file_btn.setFixedSize(40, 40)
        self.attach_file_btn.clicked.connect(self.attach_file)

        self.message_input = ChatInputTextEdit()
        self.message_input.setPlaceholderText("Type your message...")
        self.message_input.sendRequested.connect(self.send_message)
        self.message_input.largePasteDetected.connect(self._handle_large_paste_from_input)
        self.message_input.filesDropped.connect(self._handle_input_files_dropped)
        self.message_input.textDropped.connect(self._handle_input_text_dropped)
        self.message_input.attachmentRemoved.connect(self._handle_attachment_pill_removed)
        self.message_input.composerHeightChanged.connect(self._sync_footer_height)
        
        self.send_button = QPushButton(); self.send_button.setFixedSize(40, 40)
        input_layout.addWidget(self.attach_file_btn); input_layout.addWidget(self.message_input); input_layout.addWidget(self.send_button)
        input_layout.setAlignment(self.attach_file_btn, Qt.AlignmentFlag.AlignBottom)
        input_layout.setAlignment(self.send_button, Qt.AlignmentFlag.AlignBottom)
        
        self.bottom_container = QWidget()
        self.bottom_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.bottom_container.setMinimumHeight(68)
        bottom_layout = QVBoxLayout(self.bottom_container)
        bottom_layout.setContentsMargins(0,0,0,0); bottom_layout.setSpacing(0); bottom_layout.addWidget(self.input_widget)
        container_layout.addWidget(self.bottom_container)

        self.setCentralWidget(self.container)
        self._update_themed_styles()
        self.send_button.clicked.connect(self._handle_send_button_click)
        self._sync_footer_height()

        self.current_node = None
        self.loading_animation = None
        self.pending_response_preview = None
        self.search_overlay = None
        self.search_results = []
        self.current_search_index = -1

        self.command_manager = CommandManager()
        self._setup_commands()
        self.plugin_picker = PluginFlyoutPanel(self.plugin_portal, self)
        self.plugin_picker.pluginSelected.connect(self._handle_plugin_picker_selection)

        self.new_chat_shortcut = QShortcut(QKeySequence("Ctrl+T"), self); self.new_chat_shortcut.activated.connect(self.new_chat)
        self.library_shortcut = QShortcut(QKeySequence("Ctrl+L"), self); self.library_shortcut.activated.connect(self.show_library)
        self.save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self); self.save_shortcut.activated.connect(self.save_chat)
        self.command_palette_shortcut = QShortcut(QKeySequence("Ctrl+K"), self); self.command_palette_shortcut.activated.connect(self.show_command_palette)
        self.search_shortcut = QShortcut(QKeySequence("Ctrl+F"), self); self.search_shortcut.activated.connect(self.show_search_overlay)
        self.frame_shortcut = QShortcut(QKeySequence("Ctrl+G"), self); self.frame_shortcut.activated.connect(self.chat_view.scene().createFrame)
        self.container_shortcut = QShortcut(QKeySequence("Ctrl+Shift+G"), self); self.container_shortcut.activated.connect(self.chat_view.scene().createContainer)

        self.nav_up_shortcut = QShortcut(QKeySequence("Ctrl+Up"), self); self.nav_up_shortcut.activated.connect(self._navigate_up)
        self.nav_down_shortcut = QShortcut(QKeySequence("Ctrl+Down"), self); self.nav_down_shortcut.activated.connect(self._navigate_down)
        self.nav_left_shortcut = QShortcut(QKeySequence("Ctrl+Left"), self); self.nav_left_shortcut.activated.connect(self._navigate_left)
        self.nav_right_shortcut = QShortcut(QKeySequence("Ctrl+Right"), self); self.nav_right_shortcut.activated.connect(self._navigate_right)

        screen = QGuiApplication.primaryScreen().geometry()
        size = self.geometry(); self.move(int((screen.width() - size.width()) / 2), int((screen.height() - size.height()) / 2))
        
    def reinitialize_agent(self):
        current_prompt = self._get_current_system_prompt()
        self.agent = ChatAgent("Graphlink Assistant", current_prompt)

    def _get_current_system_prompt(self):
        if not self.settings_manager.get_enable_system_prompt():
            return ""
        current_mode = self.settings_manager.get_current_mode()
        if current_mode == config.MODE_LLAMACPP_LOCAL:
            reasoning_mode = self.settings_manager.get_llama_cpp_reasoning_mode()
        else:
            reasoning_mode = self.settings_manager.get_ollama_reasoning_mode()
        if reasoning_mode == "Thinking":
            return THINKING_INSTRUCTIONS_PROMPT + BASE_SYSTEM_PROMPT
        else:
            return BASE_SYSTEM_PROMPT

    def _update_themed_styles(self):
        palette = get_current_palette()
        button_colors = get_neutral_button_colors()
        if self._main_request_active:
            stop_accent = get_semantic_color("status_error")
            stop_background = self._blend_button_color(button_colors["background"], stop_accent, 0.18)
            stop_hover = self._blend_button_color(button_colors["hover"], stop_accent, 0.24)
            stop_pressed = self._blend_button_color(button_colors["pressed"], stop_accent, 0.16)
            stop_border = self._blend_button_color(button_colors["border"], stop_accent, 0.30)
            stop_icon = button_colors["muted_icon"] if self._main_request_cancel_pending else QColor("#ffffff")
            self.send_button.setIcon(qta.icon('fa5s.stop', color=stop_icon.name()))
            self.send_button.setToolTip("Cancelling..." if self._main_request_cancel_pending else "Cancel response")
            background = stop_background
            hover = stop_hover
            pressed = stop_pressed
            border = stop_border
        else:
            self.send_button.setIcon(qta.icon('fa5s.paper-plane', color=button_colors["icon"].name()))
            self.send_button.setToolTip("Send message")
            background = button_colors["background"]
            hover = button_colors["hover"]
            pressed = button_colors["pressed"]
            border = button_colors["border"]
        if hasattr(self, 'plugins_button'):
            self.plugins_button.setIcon(qta.icon("fa5s.chevron-down", color=palette.SELECTION.lighter(160).name()))
        if hasattr(self, 'plugin_picker'):
            self.plugin_picker.refresh()
        if hasattr(self, 'pin_overlay'):
            self.pin_overlay.on_theme_changed()
        self.send_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {background.name()};
                border: 1px solid {border.name()};
                border-radius: 20px;
                padding: 10px;
            }}
            QPushButton:hover {{
                background-color: {hover.name()};
                border-color: {hover.lighter(112).name()};
            }}
            QPushButton:pressed {{
                background-color: {pressed.name()};
                border-color: {border.darker(105).name()};
            }}
            QPushButton:disabled {{
                background-color: {background.darker(108).name()};
                border-color: {border.darker(112).name()};
            }}
        """)
        if hasattr(self, 'message_input'):
            self.message_input.on_theme_changed()
        self._refresh_attachment_button()

    def _blend_button_color(self, base, accent, ratio):
        base_color = QColor(base)
        accent_color = QColor(accent)
        mix = max(0.0, min(1.0, float(ratio)))
        return QColor(
            round(base_color.red() + (accent_color.red() - base_color.red()) * mix),
            round(base_color.green() + (accent_color.green() - base_color.green()) * mix),
            round(base_color.blue() + (accent_color.blue() - base_color.blue()) * mix),
        )

    def _handle_send_button_click(self):
        if self._main_request_active:
            if self._main_request_cancel_callback and not self._main_request_cancel_pending:
                self._main_request_cancel_pending = True
                self.send_button.setEnabled(False)
                self._update_themed_styles()
                self._main_request_cancel_callback()
            return
        self.send_message()

    def _set_main_request_state(self, *, active: bool, cancel_callback=None, cancel_pending: bool = False):
        self._main_request_active = active
        self._main_request_cancel_pending = cancel_pending if active else False
        self._main_request_cancel_callback = cancel_callback if active else None
        if active:
            self.send_button.setEnabled(not self._main_request_cancel_pending)
            self.message_input.setEnabled(False)
            self.attach_file_btn.setEnabled(False)
        else:
            self.send_button.setEnabled(True)
        self._update_themed_styles()

    def on_theme_changed(self):
        self._update_themed_styles()
        if self.chat_view and self.chat_view.scene():
            self.chat_view.scene().update()
            self.chat_view.viewport().update()

    def on_settings_changed(self):
        self.token_counter_widget.setVisible(self.settings_manager.get_show_token_counter())
        self._update_overlay_positions()
        self.reinitialize_agent()

    def start_with_prompt(self, prompt: str):
        if prompt:
            self.message_input.setText(prompt); QTimer.singleShot(100, self.send_message)

    def _handle_pin_overlay_closed(self):
        pass

    def showEvent(self, event):
        super().showEvent(event)
        if not self._initial_show_complete:
            self._update_overlay_positions()
            self._initial_show_complete = True
        self._schedule_startup_update_check()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_overlay_positions()

    def eventFilter(self, watched, event):
        chat_view = getattr(self, 'chat_view', None)
        viewport = chat_view.viewport() if chat_view else None
        if watched in (chat_view, viewport):
            if event.type() in {
                QEvent.Type.Resize,
                QEvent.Type.Move,
                QEvent.Type.Show,
                QEvent.Type.LayoutRequest,
            }:
                self._schedule_overlay_update()
        return super().eventFilter(watched, event)

    def _schedule_overlay_update(self):
        if self._overlay_update_pending:
            return
        self._overlay_update_pending = True
        QTimer.singleShot(0, self._flush_overlay_update)

    def _flush_overlay_update(self):
        self._overlay_update_pending = False
        self._update_overlay_positions()

    def _update_overlay_positions(self):
        search_overlay = getattr(self, 'search_overlay', None)
        token_counter_widget = getattr(self, 'token_counter_widget', None)
        notification_banner = getattr(self, 'notification_banner', None)
        padding = 10
        viewport = self.chat_view.viewport()

        if notification_banner and notification_banner.isVisible():
            notification_banner.update_position()
        if search_overlay and search_overlay.isVisible():
            search_overlay.move(viewport.width() - search_overlay.width() - padding, padding)
        if token_counter_widget and token_counter_widget.isVisible():
            token_y = viewport.height() - token_counter_widget.height() - padding
            token_counter_widget.move(padding, max(padding, token_y))

    def _schedule_startup_update_check(self):
        if self._startup_update_check_ran:
            return
        if not self.settings_manager.get_update_notifications_enabled():
            return
        self._startup_update_check_ran = True
        QTimer.singleShot(900, lambda: self.check_for_updates(manual=False))

    def check_for_updates(self, manual=False, status_target=None):
        if self.update_check_worker and self.update_check_worker.isRunning():
            if status_target is not None:
                self._update_check_status_target = status_target
            if manual:
                self.notification_banner.show_message("An update check is already running.", 3000, "info")
            if status_target and hasattr(status_target, "set_update_check_in_progress"):
                status_target.set_update_check_in_progress(True)
            return

        self._update_check_manual = bool(manual)
        self._update_check_status_target = status_target
        if status_target and hasattr(status_target, "set_update_check_in_progress"):
            status_target.set_update_check_in_progress(True)

        self.update_check_worker = UpdateCheckWorker(APP_VERSION, self)
        self.update_check_worker.finished_check.connect(self._handle_update_check_result)
        self.update_check_worker.finished.connect(self._cleanup_update_check_worker)
        self.update_check_worker.start()

    def _handle_update_check_result(self, result):
        self.settings_manager.record_update_check_result(result)

        status_target = self._update_check_status_target
        if status_target and hasattr(status_target, "refresh_update_status"):
            status_target.refresh_update_status()
        if status_target and hasattr(status_target, "set_update_check_in_progress"):
            status_target.set_update_check_in_progress(False)

        if self.settings_panel and hasattr(self.settings_panel, "appearance_tab"):
            self.settings_panel.appearance_tab.refresh_update_status()

        should_notify = self._update_check_manual or result.get("update_available") or not result.get("success", True)
        if should_notify:
            duration_ms = 7000 if result.get("update_available") else 5000
            self.notification_banner.show_message(result.get("message", "Update check finished."), duration_ms, result.get("level", "info"))

        self._update_check_status_target = None
        self._update_check_manual = False

    def _cleanup_update_check_worker(self):
        worker = self.update_check_worker
        self.update_check_worker = None
        if worker is not None:
            worker.deleteLater()

    def _iter_shutdown_threads(self):
        for attr_name, label in (
            ("chat_thread", "active chat request"),
            ("takeaway_thread", "takeaway generation"),
            ("explainer_thread", "explanation generation"),
            ("chart_thread", "chart generation"),
            ("group_summary_thread", "group summary generation"),
            ("image_gen_thread", "image generation"),
            ("code_exec_thread", "PyCoder code execution"),
            ("pycoder_agent_thread", "PyCoder analysis"),
            ("pycoder_exec_thread", "PyCoder workflow"),
            ("sandbox_thread", "code sandbox execution"),
            ("web_worker_thread", "web research"),
            ("conversation_node_thread", "conversation node request"),
            ("reasoning_thread", "reasoning workflow"),
            ("artifact_thread", "artifact workflow"),
            ("workflow_thread", "workflow generation"),
            ("graph_diff_thread", "graph diff"),
            ("quality_gate_thread", "quality gate"),
            ("code_review_thread", "code review"),
            ("gitlink_thread", "Gitlink proposal"),
            ("update_check_worker", "update check"),
        ):
            worker = getattr(self, attr_name, None)
            if worker is not None:
                yield attr_name, label, worker

        save_thread = getattr(getattr(self, "session_manager", None), "save_thread", None)
        if save_thread is not None:
            yield "session_manager.save_thread", "background save", save_thread

    def _request_thread_shutdown(self, worker):
        if worker is None:
            return

        for method_name in ("cancel", "stop"):
            method = getattr(worker, method_name, None)
            if callable(method):
                try:
                    method()
                except Exception:
                    pass
                return

        request_interruption = getattr(worker, "requestInterruption", None)
        if callable(request_interruption):
            try:
                request_interruption()
            except Exception:
                pass

    def _shutdown_background_threads(self, timeout_ms=3000):
        still_running = []
        for attr_name, label, worker in self._iter_shutdown_threads():
            if not hasattr(worker, "isRunning") or not worker.isRunning():
                continue

            self._request_thread_shutdown(worker)
            if worker.wait(timeout_ms):
                if attr_name != "session_manager.save_thread" and getattr(self, attr_name, None) is worker:
                    setattr(self, attr_name, None)
                continue

            still_running.append(label)

        return still_running

    def closeEvent(self, event):
        still_running = self._shutdown_background_threads()
        session_manager = getattr(self, "session_manager", None)
        save_shutdown_ok = session_manager.shutdown() if session_manager else True

        if not save_shutdown_ok and "background save" not in still_running:
            still_running.append("background save")

        if still_running:
            task_list = "\n".join(f"- {label}" for label in still_running)
            QMessageBox.information(
                self,
                "Background Work Still Running",
                "Please wait for these background tasks to finish before closing:\n\n"
                f"{task_list}",
            )
            event.ignore()
            return

        super().closeEvent(event)

    def _sync_footer_height(self, *_):
        if not hasattr(self, 'input_widget') or not hasattr(self, 'bottom_container'):
            return

        layout = self.input_widget.layout()
        margins = layout.contentsMargins() if layout else None
        top_margin = margins.top() if margins else 0
        bottom_margin = margins.bottom() if margins else 0
        composer_height = 0
        if hasattr(self, 'message_input'):
            composer_height = max(self.message_input.height(), self.message_input.sizeHint().height())
        row_height = max(40, composer_height) + top_margin + bottom_margin
        target_height = max(72, row_height)

        self.input_widget.setFixedHeight(target_height)
        self.bottom_container.setFixedHeight(target_height)
        self.input_widget.updateGeometry()
        self.bottom_container.updateGeometry()
        if hasattr(self, 'container'):
            self.container.updateGeometry()
            if self.container.layout():
                self.container.layout().activate()
        self._schedule_overlay_update()
        
    def show_search_overlay(self):
        if not self.search_overlay:
            self.search_overlay = SearchOverlay(self.chat_view)
            self.search_overlay.textChanged.connect(self._handle_search_changed)
            self.search_overlay.findNext.connect(self._find_next_match)
            self.search_overlay.findPrevious.connect(self._find_previous_match)
            self.search_overlay.closed.connect(self._close_search)
        self.search_overlay.search_input.clear(); self.search_overlay.show(); self.search_overlay.raise_(); self.search_overlay.focus_input(); self._update_overlay_positions()

    def _close_search(self):
        if self.search_overlay: self.search_overlay.hide()
        self.chat_view.scene().update_search_highlight([]); self.search_results = []; self.current_search_index = -1; self._update_overlay_positions()

    def _handle_search_changed(self, text: str):
        scene = self.chat_view.scene()
        if not text: self.search_results = []; self.current_search_index = -1; scene.update_search_highlight([])
        else: self.search_results = scene.find_items(text); self.current_search_index = -1; scene.update_search_highlight(self.search_results)
        self.search_overlay.update_results_label(0, len(self.search_results))

    def _find_next_match(self):
        if not self.search_results: return
        self.current_search_index = (self.current_search_index + 1) % len(self.search_results); self._focus_on_current_match()

    def _find_previous_match(self):
        if not self.search_results: return
        self.current_search_index = (self.current_search_index - 1 + len(self.search_results)) % len(self.search_results); self._focus_on_current_match()
        
    def _focus_on_current_match(self):
        if not (0 <= self.current_search_index < len(self.search_results)): return
        target_node = self.search_results[self.current_search_index]
        self.chat_view.scene().clearSelection(); target_node.setSelected(True); self.chat_view.centerOn(target_node); self.search_overlay.update_results_label(self.current_search_index + 1, len(self.search_results))

    def show_library(self):
        if self.library_dialog and self.library_dialog.isVisible():
            self.library_dialog.raise_()
            self.library_dialog.activateWindow()
            return

        self.library_dialog = ChatLibraryDialog(self.session_manager, self)
        self.library_dialog.destroyed.connect(lambda *_: setattr(self, "library_dialog", None))
        self.library_dialog.show_centered()
        
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
             if self.pending_attachments: self.clear_attachment()
        elif event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.key() == Qt.Key.Key_N:
                view_pos = self.chat_view.mapFromGlobal(QCursor.pos()); scene_pos = self.chat_view.mapToScene(view_pos); self.chat_view.scene().add_note(scene_pos)
        elif event.key() == Qt.Key.Key_Delete: self.chat_view.scene().deleteSelectedItems()
        else: super().keyPressEvent(event)
        
    def save_chat(self):
        self.session_manager.save_current_chat()
        self.notification_banner.show_message("Chat saved in background.", 3000, "success")

    def should_show_notification(self, msg_type):
        if not hasattr(self, "settings_manager") or self.settings_manager is None:
            return True
        return self.settings_manager.get_notification_type_enabled(msg_type)

    def setup_toolbar(self, toolbar):
        toolbar.setIconSize(QSize(20, 20)); toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        toolbar.setStyleSheet("QToolBar { spacing: 4px; padding: 4px; } QToolButton { color: white; background: transparent; border: none; border-radius: 4px; padding: 6px; margin: 2px; font-size: 12px; } QToolButton:hover { background: rgba(255, 255, 255, 0.1); }")
        self.pins_btn = QToolButton(); self.pins_btn.setText("Pins"); self.pins_btn.clicked.connect(self.toggle_pin_overlay); toolbar.addWidget(self.pins_btn)
        organize_btn = QToolButton(); organize_btn.setText("Organize"); organize_btn.setObjectName("actionButton"); organize_btn.clicked.connect(lambda: self.chat_view.scene().organize_nodes()); toolbar.addWidget(organize_btn)
        toolbar.addSeparator()
        zoom_in_btn = QToolButton(); zoom_in_btn.setText("Zoom In"); zoom_in_btn.clicked.connect(lambda: self.chat_view.scale(1.1, 1.1)); toolbar.addWidget(zoom_in_btn)
        zoom_out_btn = QToolButton(); zoom_out_btn.setText("Zoom Out"); zoom_out_btn.clicked.connect(lambda: self.chat_view.scale(0.9, 0.9)); toolbar.addWidget(zoom_out_btn)
        toolbar.addSeparator()
        reset_btn = QToolButton(); reset_btn.setText("Reset"); reset_btn.clicked.connect(self.chat_view.reset_zoom); toolbar.addWidget(reset_btn)
        fit_btn = QToolButton(); fit_btn.setText("Fit All"); fit_btn.clicked.connect(self.chat_view.fit_all); toolbar.addWidget(fit_btn)
        toggle_overlays_btn = QToolButton(); toggle_overlays_btn.setText("Controls"); toggle_overlays_btn.setCheckable(True); toggle_overlays_btn.toggled.connect(self.chat_view.toggle_overlays_visibility); toolbar.addWidget(toggle_overlays_btn)

        self.plugins_button = QToolButton()
        self.plugins_button.setText("Plugins")
        self.plugins_button.setObjectName("actionButton")
        self.plugins_button.setPopupMode(QToolButton.ToolButtonPopupMode.DelayedPopup)
        self.plugins_button.setIcon(qta.icon("fa5s.chevron-down", color="#9aa6b2"))
        self.plugins_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.plugins_button.clicked.connect(self._toggle_plugin_picker)
        toolbar.addWidget(self.plugins_button)
        
        spacer = QWidget(); spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred); toolbar.addWidget(spacer)
        
        self.mode_combo = QComboBox()
        self.mode_combo.addItem(config.MODE_OLLAMA_LOCAL, config.LOCAL_PROVIDER_OLLAMA)
        self.mode_combo.addItem(config.MODE_LLAMACPP_LOCAL, config.LOCAL_PROVIDER_LLAMACPP)
        self.mode_combo.addItem(config.MODE_API_ENDPOINT, config.API_PROVIDER_OPENAI)
        self.mode_combo.setMinimumWidth(150)
        
        current_mode = self.settings_manager.get_current_mode()
        idx = self.mode_combo.findText(current_mode)
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)
            
        self.mode_combo.currentIndexChanged.connect(self.on_mode_changed)
        toolbar.addWidget(self.mode_combo)
        
        self.settings_btn = QToolButton(); self.settings_btn.setText("Settings"); self.settings_btn.clicked.connect(self.show_settings); toolbar.addWidget(self.settings_btn)
        about_btn = QToolButton(); about_btn.setText("About"); about_btn.clicked.connect(self.show_about_dialog); toolbar.addWidget(about_btn)
        self.help_btn = QToolButton(); self.help_btn.setText("Help"); self.help_btn.setObjectName("helpButton"); self.help_btn.clicked.connect(self.show_help); toolbar.addWidget(self.help_btn)
        
        self._initialize_saved_mode_on_startup()

    def _toggle_plugin_picker(self):
        if self.plugin_picker.isVisible():
            self.plugin_picker.close()
            return
        self.plugin_picker.show_for_anchor(self.plugins_button)

    def _handle_plugin_picker_selection(self, plugin_name):
        self.plugin_portal.execute_plugin(plugin_name)

    def toggle_pin_overlay(self, checked=False):
        if self.pin_overlay.isVisible():
            self.pin_overlay.raise_()
            self.pin_overlay.activateWindow()
            return
        self.pin_overlay.show_for_anchor(self.pins_btn)

    def update_title_bar(self):
        title = "Graphlink"
        if self.session_manager and self.session_manager.current_chat_id:
            chat_info = self.session_manager.db.load_chat(self.session_manager.current_chat_id)
            if chat_info and 'title' in chat_info: title = f"Graphlink - {chat_info['title']}"
        self.setWindowTitle(title)

    def show_about_dialog(self): AboutDialog(self).exec()
    def show_help(self):
        if self.help_panel and self.help_panel.isVisible():
            self.help_panel.close()
            return

        if not self.help_panel:
            self.help_panel = HelpDialog(self)

        self.help_panel.show_for_anchor(self.help_btn if hasattr(self, 'help_btn') else self)

    def _build_document_section(self, title, body):
        text = str(body or "").strip()
        if not text:
            return ""
        return f"## {title}\n\n{text}"

    def _build_code_block(self, code_text, language=""):
        text = str(code_text or "").rstrip()
        if not text:
            return ""
        return f"```{language}\n{text}\n```"

    def _join_document_sections(self, *sections):
        active_sections = [section for section in sections if section and section.strip()]
        return "\n\n---\n\n".join(active_sections)

    def _history_to_markdown(self, history):
        rows = []
        for index, message in enumerate(history or [], start=1):
            role = str(message.get("role", "assistant")).replace("_", " ").title()
            content = str(message.get("content", "")).strip()
            if not content:
                continue
            rows.append(f"### {index}. {role}\n\n{content}")
        return "\n\n".join(rows)

    def _truncate_document_text(self, text, limit=12000):
        value = str(text or "").strip()
        if len(value) <= limit:
            return value
        truncated_count = len(value) - limit
        return f"{value[:limit].rstrip()}\n\n...[truncated {truncated_count} chars]"

    def _extract_document_view_content(self, node):
        if isinstance(node, ChatNode):
            return node.text

        if isinstance(node, CodeNode):
            language = getattr(node, "language", "")
            return self._build_document_section("Code", self._build_code_block(getattr(node, "code", ""), language))

        if isinstance(node, ThinkingNode):
            return self._build_document_section("Reasoning", getattr(node, "thinking_text", ""))

        if isinstance(node, ConversationNode):
            return self._build_document_section("Conversation Transcript", self._history_to_markdown(getattr(node, "conversation_history", [])))

        if isinstance(node, ReasoningNode):
            return self._join_document_sections(
                self._build_document_section("Prompt", getattr(node, "prompt", "")),
                self._build_document_section("Reasoning Trace", getattr(node, "thought_process", "")),
            )

        if isinstance(node, WebNode):
            sources = getattr(node, "sources", []) or []
            source_lines = "\n".join(f"- [{url}]({url})" for url in sources if str(url).strip())
            return self._join_document_sections(
                self._build_document_section("Query", getattr(node, "query", "")),
                self._build_document_section("Summary", getattr(node, "summary", "")),
                self._build_document_section("Sources", source_lines),
            )

        if isinstance(node, PyCoderNode):
            terminal = node.output_display.toPlainText() if hasattr(node, "output_display") else ""
            analysis = node.ai_analysis_display.toPlainText() if hasattr(node, "ai_analysis_display") else ""
            return self._join_document_sections(
                self._build_document_section("Task Prompt", node.get_prompt() if hasattr(node, "get_prompt") else ""),
                self._build_document_section("Code", self._build_code_block(node.get_code() if hasattr(node, "get_code") else "", "python")),
                self._build_document_section("Terminal Output", self._build_code_block(terminal)),
                self._build_document_section("Analysis", analysis),
            )

        if isinstance(node, CodeSandboxNode):
            terminal = node.output_display.toPlainText() if hasattr(node, "output_display") else ""
            analysis = node.ai_analysis_display.toPlainText() if hasattr(node, "ai_analysis_display") else ""
            return self._join_document_sections(
                self._build_document_section("Task Brief", node.get_prompt() if hasattr(node, "get_prompt") else ""),
                self._build_document_section("Requirements", self._build_code_block(node.get_requirements() if hasattr(node, "get_requirements") else "")),
                self._build_document_section("Code", self._build_code_block(node.get_code() if hasattr(node, "get_code") else "", "python")),
                self._build_document_section("Terminal Output", self._build_code_block(terminal)),
                self._build_document_section("Review", analysis),
            )

        if isinstance(node, HtmlViewNode):
            html_source = node.get_html_content() if hasattr(node, "get_html_content") else ""
            return self._build_document_section("HTML Source", self._build_code_block(html_source, "html"))

        if isinstance(node, ArtifactNode):
            transcript = self._history_to_markdown(getattr(node, "local_history", []))
            return self._join_document_sections(
                self._build_document_section("Artifact", node.get_artifact_content() if hasattr(node, "get_artifact_content") else ""),
                self._build_document_section("Drafting Transcript", transcript),
            )

        if isinstance(node, WorkflowNode):
            recommendations = []
            for item in getattr(node, "recommendations", []) or []:
                plugin = str(item.get("plugin", "Plugin")).strip()
                why = str(item.get("why", "")).strip()
                priority = str(item.get("priority", "")).strip()
                prompt = str(item.get("starter_prompt", "")).strip()
                summary = f"- **{plugin}**"
                if priority:
                    summary += f" ({priority})"
                if why:
                    summary += f": {why}"
                if prompt:
                    summary += f"\n  Starter Prompt: {prompt}"
                recommendations.append(summary)
            return self._join_document_sections(
                self._build_document_section("Goal", node.get_goal() if hasattr(node, "get_goal") else ""),
                self._build_document_section("Constraints", node.get_constraints() if hasattr(node, "get_constraints") else ""),
                self._build_document_section("Workflow Blueprint", getattr(node, "blueprint_markdown", "")),
                self._build_document_section("Recommended Plugins", "\n".join(recommendations)),
            )

        if isinstance(node, GraphDiffNode):
            return self._join_document_sections(
                self._build_document_section("Branch Comparison", getattr(node, "comparison_markdown", "")),
                self._build_document_section("Summary Note", getattr(node, "note_summary", "")),
            )

        if isinstance(node, QualityGateNode):
            recommendations = []
            for item in getattr(node, "recommendations", []) or []:
                plugin = str(item.get("plugin", "Plugin")).strip()
                why = str(item.get("why", "")).strip()
                starter_prompt = str(item.get("starter_prompt", "")).strip()
                summary = f"- **{plugin}**"
                if why:
                    summary += f": {why}"
                if starter_prompt:
                    summary += f"\n  Starter Prompt: {starter_prompt}"
                recommendations.append(summary)
            return self._join_document_sections(
                self._build_document_section("Goal", node.get_goal() if hasattr(node, "get_goal") else ""),
                self._build_document_section("Acceptance Criteria", node.get_criteria() if hasattr(node, "get_criteria") else ""),
                self._build_document_section("Quality Review", getattr(node, "review_markdown", "")),
                self._build_document_section("Recommended Plugins", "\n".join(recommendations)),
                self._build_document_section("Summary Note", getattr(node, "note_summary", "")),
            )

        if isinstance(node, CodeReviewNode):
            source_text = node.source_editor.toPlainText() if hasattr(node, "source_editor") else ""
            return self._join_document_sections(
                self._build_document_section("Review Context", node.get_review_context() if hasattr(node, "get_review_context") else ""),
                self._build_document_section("Code Review", getattr(node, "review_markdown", "")),
                self._build_document_section("Source Snapshot", self._build_code_block(source_text)),
            )

        if isinstance(node, GitlinkNode):
            context_xml = self._truncate_document_text(getattr(node, "context_xml", ""))
            return self._join_document_sections(
                self._build_document_section("Task Prompt", node.get_task_prompt() if hasattr(node, "get_task_prompt") else ""),
                self._build_document_section("Proposal", getattr(node, "proposal_markdown", "")),
                self._build_document_section("Patch Preview", self._build_code_block(getattr(node, "preview_text", ""))),
                self._build_document_section("Context XML (truncated)", self._build_code_block(context_xml, "xml")),
            )

        return ""

    def show_document_view(self, node):
        document_text = self._extract_document_view_content(node)
        if not str(document_text or "").strip():
            self.notification_banner.show_message("No document view content is available for this node yet.", 3000, "info")
            return
        self.doc_viewer_panel.set_document_content(document_text)
        self.doc_viewer_panel.setVisible(True)

    def hide_document_view(self): self.doc_viewer_panel.setVisible(False)

    def show_settings(self):
        if self.settings_panel and self.settings_panel.isVisible():
            self.settings_panel.close()
            return

        if not self.settings_panel:
            self.settings_panel = SettingsDialog(self.settings_manager, self)

        self.settings_panel.set_current_section_by_mode(self.mode_combo.currentText())
        self.settings_panel.show_for_anchor(self.settings_btn)

    def _set_mode_combo_silently(self, mode_text):
        if not hasattr(self, "mode_combo"):
            return
        target_index = self.mode_combo.findText(mode_text)
        if target_index < 0:
            return
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentIndex(target_index)
        self.mode_combo.blockSignals(False)

    def _initialize_mode(self, mode_text, *, show_dialogs):
        if mode_text == config.MODE_OLLAMA_LOCAL:
            api_provider.initialize_local_provider(config.LOCAL_PROVIDER_OLLAMA)
            self.settings_btn.setEnabled(True)
            return True

        if mode_text == config.MODE_LLAMACPP_LOCAL:
            api_provider.initialize_local_provider(
                config.LOCAL_PROVIDER_LLAMACPP,
                self.settings_manager.get_llama_cpp_settings(),
                preload_model=False,
            )
            self.settings_btn.setEnabled(True)
            return True

        if mode_text == config.MODE_API_ENDPOINT:
            provider = self.settings_manager.get_api_provider()
            base_url = self.settings_manager.get_api_base_url()

            saved_models = self.settings_manager.get_api_models()
            for task, model_name in saved_models.items():
                api_provider.set_task_model(task, model_name)

            if provider == config.API_PROVIDER_OPENAI:
                api_key = self.settings_manager.get_openai_key()
                api_provider.initialize_api(provider, api_key, base_url)
            elif provider == config.API_PROVIDER_ANTHROPIC:
                api_key = self.settings_manager.get_anthropic_key()
                api_provider.initialize_api(provider, api_key)
            else:
                api_key = self.settings_manager.get_gemini_key()
                api_provider.initialize_api(provider, api_key)
            self.settings_btn.setEnabled(True)
            return True

        if show_dialogs:
            QMessageBox.warning(
                self,
                "Unknown Mode",
                f"Graphlink does not recognize the saved mode '{mode_text}'.",
            )
        return False
    
    def on_mode_changed(self, index):
        previous_mode = self.settings_manager.get_current_mode()
        mode_text = self.mode_combo.itemText(index)
        try:
            self._initialize_mode(mode_text, show_dialogs=True)
            self.settings_manager.set_current_mode(mode_text)
            self.reinitialize_agent()
            if mode_text == config.MODE_LLAMACPP_LOCAL:
                self.notification_banner.show_message(
                    "Llama.cpp is configured. The GGUF will load on the first request instead of blocking startup or mode switching.",
                    5000,
                    "info",
                )
        except Exception as e:
            if previous_mode and previous_mode != mode_text:
                self._set_mode_combo_silently(previous_mode)
                self.settings_manager.set_current_mode(previous_mode)
                try:
                    self._initialize_mode(previous_mode, show_dialogs=False)
                    self.reinitialize_agent()
                except Exception:
                    pass
            title = (
                "Llama.cpp Configuration Required"
                if mode_text == config.MODE_LLAMACPP_LOCAL
                else "API Configuration Required"
                if mode_text == config.MODE_API_ENDPOINT
                else "Ollama Initialization Error"
            )
            QMessageBox.warning(
                self,
                title,
                f"{mode_text} could not be initialized:\n\n{str(e)}"
            )

    def _initialize_saved_mode_on_startup(self):
        mode_text = self.mode_combo.currentText()
        try:
            self._initialize_mode(mode_text, show_dialogs=False)
            self.settings_manager.set_current_mode(mode_text)
        except Exception:
            fallback_mode = config.MODE_OLLAMA_LOCAL
            self._set_mode_combo_silently(fallback_mode)
            self.settings_manager.set_current_mode(fallback_mode)
            api_provider.initialize_local_provider(config.LOCAL_PROVIDER_OLLAMA)
        self.reinitialize_agent()

    def setCurrentNode(self, node):
        self.current_node = node; text_content = ""
        if isinstance(node, ChatNode): text_content = node.text if node.text else "[Attachment/Content Node]"
        elif isinstance(node, PyCoderNode): text_content = "Py-Coder Analysis"
        elif isinstance(node, CodeSandboxNode): text_content = "Execution Sandbox"
        elif isinstance(node, WebNode): text_content = "Web Search Node"
        elif isinstance(node, ConversationNode): text_content = "Conversation"
        elif isinstance(node, ReasoningNode): text_content = "Reasoning Node"
        elif isinstance(node, HtmlViewNode): text_content = "HTML Renderer"
        elif isinstance(node, WorkflowNode): text_content = "Workflow Architect"
        elif isinstance(node, GraphDiffNode): text_content = "Branch Lens"
        elif isinstance(node, QualityGateNode): text_content = "Quality Gate"
        elif isinstance(node, CodeReviewNode): text_content = "Code Review Agent"
        elif isinstance(node, GitlinkNode): text_content = "Gitlink"
        elif isinstance(node, Note) and node.is_summary_note: self.message_input.setPlaceholderText("Cannot respond to a summary note."); return
        if text_content: self.message_input.setPlaceholderText(f"Responding to: {text_content[:30]}...")
        else: self.message_input.setPlaceholderText("Type your message...")
        
    def attach_file(self):
        supported_images = "*.png *.jpg *.jpeg *.webp"
        supported_audio = " ".join(f"*{ext}" for ext in sorted(SUPPORTED_AUDIO_EXTENSIONS))
        supported_docs = " ".join(f"*{ext}" for ext in sorted(self.file_handler.SUPPORTED_EXTENSIONS))
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Files",
            "",
            (
                f"Common Attachments ({supported_images} {supported_audio} {supported_docs});;"
                f"Image Files ({supported_images});;"
                f"Audio Files ({supported_audio});;"
                f"Readable Files (*.*)"
            ),
        )
        if file_paths:
            self.stage_dropped_files(file_paths)

    def stage_dropped_file(self, file_path):
        self.stage_dropped_files([file_path])

    def stage_dropped_files(self, file_paths):
        staged_count = 0
        rejected_files = []

        for file_path in file_paths:
            stage_result, stage_reason = self._stage_attachment_file(file_path)
            if stage_result == "added":
                staged_count += 1
            elif stage_result == "rejected":
                rejected_files.append((os.path.basename(file_path) or file_path, stage_reason))

        if staged_count:
            noun = "file" if staged_count == 1 else "files"
            self.notification_banner.show_message(f"Attached {staged_count} {noun}.", 3000, "success")

        if rejected_files:
            preview_lines = []
            for file_name, reason in rejected_files[:3]:
                preview_lines.append(f"{file_name}: {reason or 'Unsupported attachment'}")
            rejected_preview = "\n".join(preview_lines)
            if len(rejected_files) > 3:
                rejected_preview += "\n..."
            self.notification_banner.show_message(
                f"Some attachments could not be staged:\n{rejected_preview}",
                9000,
                "warning",
            )

    def _stage_attachment_file(self, file_path, is_temp=False, display_name=None):
        if not file_path or not os.path.isfile(file_path):
            return "rejected", "File not found."

        normalized_path = os.path.abspath(file_path)
        if any(item['path'] == normalized_path for item in self.pending_attachments):
            return "duplicate", "Already attached."

        file_extension = os.path.splitext(normalized_path)[1].lower()
        image_extensions = {'.png', '.jpg', '.jpeg', '.webp'}

        if file_extension in image_extensions:
            attachment_kind = 'image'
        elif file_extension in SUPPORTED_AUDIO_EXTENSIONS:
            attachment_kind = 'audio'
        elif self.file_handler.can_read_file(normalized_path):
            attachment_kind = 'document'
        else:
            return "rejected", "Unsupported file type."

        attachment_item = {
            'path': normalized_path,
            'kind': attachment_kind,
            'name': display_name or os.path.basename(normalized_path),
            'is_temp': bool(is_temp),
            'byte_size': os.path.getsize(normalized_path),
        }

        if attachment_kind == 'document':
            doc_content, error = self.file_handler.read_file(normalized_path)
            if error:
                return "rejected", error
            attachment_item['content'] = doc_content
            attachment_item['token_count'] = self.token_estimator.count_tokens(doc_content)
            attachment_item['line_count'] = doc_content.count("\n") + 1 if doc_content else 0
            attachment_item['context_label'] = self._describe_document_attachment(attachment_item, doc_content)
        elif attachment_kind == 'audio':
            try:
                audio_info = inspect_audio_file(normalized_path)
            except AudioValidationError as exc:
                return "rejected", str(exc)
            attachment_item['mime_type'] = audio_info['mime_type']
            attachment_item['duration_seconds'] = audio_info['duration_seconds']
            attachment_item['context_label'] = f"Audio | {format_duration(audio_info['duration_seconds'])}"
        else:
            attachment_item['context_label'] = 'Vision'

        self.pending_attachments.append(attachment_item)
        self._refresh_attachment_button()
        return "added", ""

    def _refresh_attachment_button(self):
        if not hasattr(self, 'attach_file_btn'):
            return

        if not self.pending_attachments:
            self.attach_file_btn.setIcon(qta.icon('fa5s.paperclip', color='#cccccc'))
            self.attach_file_btn.setToolTip("Attach images, audio, or readable files")
            if hasattr(self, 'message_input'):
                self.message_input.set_context_items([])
            return

        palette = get_current_palette()
        attachment_kinds = {item['kind'] for item in self.pending_attachments}
        if attachment_kinds == {'image'}:
            icon_name = 'fa5s.image'
        elif attachment_kinds == {'audio'}:
            icon_name = 'fa5s.music'
        elif attachment_kinds == {'document'}:
            icon_name = 'fa5s.file-alt'
        else:
            icon_name = 'fa5s.paperclip'

        tooltip_lines = ["Ready to send:"]
        for item in self.pending_attachments[:6]:
            tooltip_lines.append(f"- {item['name']}")
        remaining_count = len(self.pending_attachments) - 6
        if remaining_count > 0:
            tooltip_lines.append(f"- and {remaining_count} more")
        tooltip_lines.append("")
        tooltip_lines.append("Press Esc to clear staged attachments.")

        self.attach_file_btn.setIcon(qta.icon(icon_name, color=palette.SELECTION.name()))
        self.attach_file_btn.setToolTip("\n".join(tooltip_lines))
        if hasattr(self, 'message_input'):
            self.message_input.set_context_items(self.pending_attachments)

    def clear_attachment(self):
        for item in self.pending_attachments:
            if not item.get('is_temp'):
                continue
            temp_path = item.get('path')
            if temp_path and os.path.isfile(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
        self.pending_attachments = []
        self._refresh_attachment_button()

    def _handle_large_paste_from_input(self, pasted_text):
        stage_result, _ = self._stage_large_paste_as_attachment(pasted_text)
        if stage_result == "added":
            self.notification_banner.show_message(
                "Large paste captured as an attachment. Add instructions and send.",
                4500,
                "success",
            )
            return

        self.message_input.insertPlainText(pasted_text)
        self.notification_banner.show_message(
            "Could not stage large paste as an attachment. Inserted into input instead.",
            6000,
            "warning",
        )

    def _stage_large_paste_as_attachment(self, pasted_text):
        if not pasted_text or not pasted_text.strip():
            return "rejected", "No text to attach."

        base_dir = os.path.join(tempfile.gettempdir(), "graphite_paste_attachments")
        try:
            os.makedirs(base_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            temp_file_path = os.path.join(base_dir, f"pasted_text_{timestamp}.txt")
            with open(temp_file_path, "w", encoding="utf-8", errors="ignore") as temp_file:
                temp_file.write(pasted_text)

            line_count = pasted_text.count("\n") + 1
            preview_name = f"Pasted Text ({line_count} lines).txt"
            return self._stage_attachment_file(temp_file_path, is_temp=True, display_name=preview_name)
        except OSError:
            return "rejected", "Could not create a temporary attachment file."

    def _handle_input_files_dropped(self, file_paths):
        self.stage_dropped_files(file_paths)
        self.message_input.setFocus()

    def _handle_input_text_dropped(self, dropped_text):
        stage_result, _ = self._stage_text_context_attachment(dropped_text)
        if stage_result == "added":
            self.notification_banner.show_message(
                "Context staged from drop. Add instructions and send.",
                4000,
                "success",
            )
            self.message_input.setFocus()
            return

        self.message_input.insertPlainText(dropped_text)
        self.notification_banner.show_message(
            "Could not stage dropped text as context. Inserted into input instead.",
            5000,
            "warning",
        )

    def _handle_attachment_pill_removed(self, attachment_path):
        if not attachment_path:
            return

        retained = []
        removed = None
        for item in self.pending_attachments:
            if removed is None and item.get('path') == attachment_path:
                removed = item
                continue
            retained.append(item)

        if removed and removed.get('is_temp') and os.path.isfile(removed['path']):
            try:
                os.remove(removed['path'])
            except OSError:
                pass

        self.pending_attachments = retained
        self._refresh_attachment_button()

    def _stage_text_context_attachment(self, dropped_text):
        if not dropped_text or not dropped_text.strip():
            return "rejected", "No text to attach."

        base_dir = os.path.join(tempfile.gettempdir(), "graphite_drop_attachments")
        try:
            os.makedirs(base_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            suffix = self._guess_text_drop_suffix(dropped_text)
            temp_file_path = os.path.join(base_dir, f"dropped_context_{timestamp}{suffix}")
            with open(temp_file_path, "w", encoding="utf-8", errors="ignore") as temp_file:
                temp_file.write(dropped_text)

            line_count = dropped_text.count("\n") + 1
            if self._looks_like_code_text(dropped_text, suffix):
                preview_name = f"Dropped Code ({line_count} lines){suffix}"
            else:
                preview_name = f"Dropped Text ({line_count} lines){suffix}"
            return self._stage_attachment_file(temp_file_path, is_temp=True, display_name=preview_name)
        except OSError:
            return "rejected", "Could not create a temporary attachment file."

    def _describe_document_attachment(self, attachment_item, content):
        file_name = (attachment_item.get('name') or "").lower()
        _, extension = os.path.splitext(file_name)

        if extension == '.pdf':
            return 'PDF'
        if extension == '.docx':
            return 'DOCX'
        if self._looks_like_code_text(content, extension):
            return 'Code'
        if extension in {'.md', '.mdx'}:
            return 'Markdown'
        return 'Text'

    def _looks_like_code_text(self, text, extension=""):
        code_extensions = {
            '.py', '.js', '.ts', '.tsx', '.jsx', '.java', '.cs', '.cpp', '.c',
            '.h', '.hpp', '.go', '.rs', '.php', '.rb', '.swift', '.kt',
            '.sql', '.sh', '.ps1', '.json', '.yaml', '.yml', '.xml', '.html', '.css'
        }
        if extension in code_extensions:
            return True

        if not text:
            return False

        code_markers = (
            'def ', 'class ', 'import ', 'from ', 'function ', 'const ', 'let ',
            'var ', '#include', 'public class', 'SELECT ', '<html', '{', '};'
        )
        marker_hits = sum(marker in text for marker in code_markers)
        newline_count = text.count('\n')
        return marker_hits >= 2 or (marker_hits >= 1 and newline_count >= 4)

    def _guess_text_drop_suffix(self, text):
        stripped = text.lstrip()
        if stripped.startswith('{') or stripped.startswith('['):
            return '.json'
        if stripped.startswith('<!DOCTYPE html') or stripped.startswith('<html'):
            return '.html'
        if stripped.startswith('SELECT ') or stripped.startswith('WITH '):
            return '.sql'
        if 'def ' in text and 'import ' in text:
            return '.py'
        if 'function ' in text or 'const ' in text or 'let ' in text:
            return '.js'
        return '.txt'

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            file_paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
            if file_paths:
                self.stage_dropped_files(file_paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def handle_error(self, error_message):
        self._set_main_request_state(active=False)
        self._clear_loading_animation()
        self._clear_pending_response_preview()
        self.notification_banner.show_message(f"An error occurred:\n{error_message}", 15000, "error")
        self.message_input.setEnabled(True); self.send_button.setEnabled(True); self.attach_file_btn.setEnabled(True); self.clear_attachment()
        
    def _get_single_selected_node(self):
        selected_items = self.chat_view.scene().selectedItems(); valid_types = (ChatNode, PyCoderNode, CodeSandboxNode, WebNode, ConversationNode, ReasoningNode, HtmlViewNode, WorkflowNode, GraphDiffNode, QualityGateNode, CodeReviewNode, GitlinkNode)
        if len(selected_items) == 1 and isinstance(selected_items[0], valid_types): return selected_items[0]
        return None

    def reset_token_counter(self, total_tokens=0):
        self.total_session_tokens = total_tokens; self.token_counter_widget.reset(); self.token_counter_widget.update_counts(total_tokens=self.total_session_tokens)

    def new_chat(self, parent_for_dialog=None):
        scene = self.chat_view.scene()
        if not scene.items() and not self.session_manager.current_chat_id: return True
        reply = QMessageBox.question(parent_for_dialog or self, 'New Chat', 'Start a new chat? Any unsaved changes will be lost.', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            if hasattr(self, 'pin_overlay') and self.pin_overlay: self.pin_overlay.clear_pins()
            self.session_manager.current_chat_id = None; scene.clear(); self.current_node = None; self.message_input.setPlaceholderText("Type your message..."); self.update_title_bar(); self.reset_token_counter(); return True
        return False
