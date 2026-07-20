from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QToolBar,
    QToolButton, QLineEdit, QPushButton, QMessageBox, QSizePolicy, QLabel, QComboBox,
    QFileDialog
)
from PySide6.QtCore import Qt, QSize, QPoint, QRect, QPointF, QTimer, QEvent
from PySide6.QtGui import QKeySequence, QGuiApplication, QCursor, QShortcut, QIcon
import qtawesome as qta
import logging
import os
import tempfile
from datetime import datetime

from graphlink_widgets import PinOverlay, SearchOverlay
from graphlink_token_estimator import TokenEstimator
from graphlink_token_counter_bridge import TokenCounterBridge
from graphlink_document_viewer_web import DocumentViewerWebHost
from graphlink_notification_web import NotificationWebHost
from graphlink_command_palette_web import CommandPaletteWebHost
from graphlink_composer_web import ComposerWebHost
from graphlink_web_island_host import AcceleratorForwardingFilter, WebIslandHost
from graphlink_canvas_items import Note, Frame, Container
from graphlink_node import ChatNode, CodeNode, ThinkingNode
from graphlink_pycoder import PyCoderNode
from graphlink_plugins.graphlink_plugin_code_sandbox import CodeSandboxNode
from graphlink_web import WebNode
from graphlink_conversation_node import ConversationNode
from graphlink_html_view import HtmlViewNode
from graphlink_plugins.graphlink_plugin_artifact import ArtifactNode
from graphlink_plugins.graphlink_plugin_gitlink import GitlinkNode

from graphlink_library_dialog import ChatLibraryDialog
from graphlink_about_web import AboutWebHost
from graphlink_help_web import HelpWebHost
from graphlink_settings_web import SettingsWebHost

from graphlink_session import ChatSessionManager
from graphlink_command_palette import CommandManager
from graphlink_plugins.graphlink_plugin_portal import PluginPortal
from graphlink_plugins.graphlink_plugin_picker import PluginFlyoutPanel
from graphlink_agents import ChatAgent
from graphlink_audio import (
    AudioValidationError,
    SUPPORTED_AUDIO_EXTENSIONS,
    format_duration,
    inspect_audio_file,
)
from graphlink_file_handler import FileHandler
import graphlink_config as config
import api_provider
from graphlink_config import get_current_palette

from graphlink_prompts import BASE_SYSTEM_PROMPT, THINKING_INSTRUCTIONS_PROMPT
from graphlink_window_actions import WindowActionsMixin
from graphlink_window_navigation import WindowNavigationMixin
from graphlink_update import APP_VERSION, UpdateCheckWorker
from graphlink_paths import asset_path
from graphlink_crash import mark_clean_exit
from graphlink_composer import ComposerController
from graphlink_composer_bridge import COMPOSER_MIN_HEIGHT
from graphlink_navigation_pins import NavigationPinsController
from graphlink_composer_popups import (
    ComposerContextPopup,
    ComposerPickerPopup,
    composer_picker_position,
)


logger = logging.getLogger(__name__)


from graphlink_utility import UtilityOperationController

class ChatWindow(QMainWindow, WindowActionsMixin, WindowNavigationMixin):
    def __init__(self, settings_manager):
        super().__init__()
        from graphlink_view import ChatView
        
        self.settings_manager = settings_manager
        self.file_handler = FileHandler()
        self.setAcceptDrops(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint | Qt.WindowType.WindowCloseButtonHint)
        self.setGeometry(100, 100, 1200, 800)
        self.library_dialog = None
        self.settings_panel = None
        self.help_panel = None
        self.about_panel = None
        self._initial_show_complete = False
        self._overlay_update_pending = False
        self._composer_picker = None
        self._composer_context_popup = None
        self._startup_update_check_ran = False
        self.update_check_worker = None
        self._update_check_status_target = None
        self._update_check_manual = False

        self.setWindowIcon(QIcon(str(asset_path("graphlink.ico"))))

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
        self.pycoder_agent_thread = None
        self.conversation_node_thread = None
        self._main_request_active = False
        self._main_request_cancel_pending = False
        self._main_request_cancel_callback = None
        self.composer_controller = ComposerController(self)
        self.utility_operation_controller = UtilityOperationController(self)
        self.utility_threads = {}

        self.container = QWidget()
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        # The composer is a window overlay. It must not be parented to the
        # QGraphicsView viewport, otherwise graph panning/scrolling and the
        # viewport's clipping region can move or crop the composer.
        self.composer_overlay_parent = self.container

        content_widget = QWidget()
        content_layout = QHBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # DocumentViewerWebHost already starts hidden (setVisible(False) in
        # its own __init__) and its in-DOM Close button reaches
        # setVisible(False) directly via the bridge's close() intent - no
        # close_requested signal to connect, unlike the legacy QWidget.
        self.doc_viewer_panel = DocumentViewerWebHost(self)
        content_layout.addWidget(self.doc_viewer_panel)

        self.chat_view = ChatView(self)
        self.chat_view.setAcceptDrops(True)
        self.chat_view.installEventFilter(self)
        self.chat_view.viewport().installEventFilter(self)
        content_layout.addWidget(self.chat_view)
        self.navigation_pins_controller = NavigationPinsController(self.chat_view.scene(), self.chat_view)

        # Notifications share the window overlay parent with the composer so
        # their z-order is meaningful across the graph/content hierarchy.
        # Keeping the banner under ChatView would leave it trapped below the
        # fixed composer even when NotificationWebHost.raise_() is called.
        self.notification_banner = NotificationWebHost(self, parent=self.composer_overlay_parent)

        self.pin_overlay = PinOverlay(self.chat_view, self, controller=self.navigation_pins_controller)
        self.pin_overlay.closed.connect(self._handle_pin_overlay_closed)
        self.pin_overlay.setVisible(False)

        self.token_estimator = TokenEstimator()
        self.total_session_tokens = 0
        self.token_counter_bridge = TokenCounterBridge()
        self.token_counter_widget = WebIslandHost(
            bridge=self.token_counter_bridge,
            asset_dir_name="token-counter",
            bridge_object_name="tokenCounterBridge",
            unavailable_message="Token counter unavailable: QtWebEngine failed to initialize.",
            parent=self.chat_view,
        )
        self.token_counter_widget.setFixedSize(150, 90)
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

        self.pending_attachments = []
        self.composer = ComposerWebHost(
            self,
            self.composer_controller,
            self.composer_overlay_parent,
        )
        self.message_input = self.composer
        self.message_input.composerHeightChanged.connect(self._sync_footer_height)

        self.composer.setVisible(True)
        self.composer.raise_()

        self.setCentralWidget(self.container)
        self._update_themed_styles()
        self.composer_controller.stateChanged.connect(self._handle_composer_state_changed)
        self._sync_footer_height()

        self.current_node = None
        self.loading_animation = None
        self.pending_response_preview = None
        self.search_overlay = None
        self.search_results = []
        self.current_search_index = -1

        self.command_manager = CommandManager()
        self._setup_commands()
        # Not embedded in normal layout, same as notification_banner - a free
        # child of composer_overlay_parent, shown/raised/positioned directly.
        # It is meant to be the topmost overlay when open (see
        # CommandPaletteWebHost.update_position()'s docstring), so it shares
        # notification's parent for the same z-order reason documented above.
        self.command_palette_host = CommandPaletteWebHost(
            self.command_manager, parent=self.composer_overlay_parent
        )
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

        # Keeps the 9 workspace-level shortcuts above (all but Ctrl+S and
        # Ctrl+K - the palette's own summon key, exempted for real once this
        # island shipped a real text input to test the tension against; see
        # AcceleratorForwardingFilter's docstring) from firing while an
        # island's own text input has DOM focus. Application-wide, not
        # per-shortcut: covers every current and future QShortcut with one
        # mechanism instead of per-shortcut bookkeeping.
        self._accelerator_filter = AcceleratorForwardingFilter(self)
        QApplication.instance().installEventFilter(self._accelerator_filter)

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
        if hasattr(self, 'plugins_button'):
            self.plugins_button.setIcon(qta.icon("fa5s.chevron-down", color=palette.SELECTION.lighter(160).name()))
        if hasattr(self, 'plugin_picker'):
            self.plugin_picker.refresh()
        if hasattr(self, 'pin_overlay'):
            self.pin_overlay.on_theme_changed()
        if hasattr(self, 'message_input'):
            self.message_input.on_theme_changed()
        self._refresh_attachment_button()

    def _set_main_request_state(self, *, active: bool, cancel_callback=None, cancel_pending: bool = False):
        self._main_request_active = active
        self._main_request_cancel_pending = cancel_pending if active else False
        self._main_request_cancel_callback = cancel_callback if active else None
        if active:
            self.message_input.set_editor_enabled(False)
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
        self._refresh_composer_provider_status()

    def _refresh_composer_provider_status(self):
        if not hasattr(self, 'composer'):
            return
        mode = self.settings_manager.get_current_mode() or "Active provider route"
        if mode == config.MODE_API_ENDPOINT:
            provider = self.settings_manager.get_api_provider() or "Cloud API"
            label = f"Cloud · {provider}"
        elif mode == config.MODE_LLAMACPP_LOCAL:
            label = "Local · llama.cpp"
        elif mode == config.MODE_OLLAMA_LOCAL:
            label = "Local · Ollama"
        else:
            label = str(mode)
        self.composer.set_provider_status(label, f"Requests follow the active mode: {mode}")

    def start_with_prompt(self, prompt: str):
        if prompt:
            self.message_input.setText(prompt); QTimer.singleShot(100, self.send_message)

    def _handle_pin_overlay_closed(self):
        if hasattr(self, "pins_btn"):
            self.pins_btn.setChecked(False)

    def edit_navigation_pin(self, pin):
        """Open the shared navigation-pin editor for a canvas marker."""
        if hasattr(self, "pin_overlay"):
            self.pin_overlay.edit_pin(pin)

    def show_navigation_pin_context_menu(self, pin, global_pos):
        if hasattr(self, "pin_overlay"):
            self.pin_overlay.show_pin_context_menu(pin, global_pos)

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
        command_palette_host = getattr(self, 'command_palette_host', None)
        padding = 10
        viewport = self.chat_view.viewport()

        if notification_banner and notification_banner.isVisible():
            notification_banner.update_position()
        if command_palette_host and command_palette_host.isVisible():
            command_palette_host.update_position()
        if search_overlay and search_overlay.isVisible():
            search_overlay.move(viewport.width() - search_overlay.width() - padding, padding)
        if token_counter_widget and token_counter_widget.isVisible():
            token_y = viewport.height() - token_counter_widget.height() - padding
            token_counter_widget.move(padding, max(padding, token_y))

        # ChatView stacks search_overlay/control_widget/grid_control/font_control/
        # minimap_widget in its own _update_overlay_positions(), which accounts for
        # search_overlay's current visibility when placing the rest of that stack.
        # Without this, toggling search while the Controls panel is already open left
        # the panel at its old Y (calculated before search became visible) while the
        # search bar always renders at the top - the two would render on top of each
        # other until something else (e.g. a resize) happened to recompute the stack.
        if self.chat_view:
            self.chat_view._update_overlay_positions()

        pin_overlay = getattr(self, "pin_overlay", None)
        if pin_overlay and pin_overlay.isVisible():
            pin_overlay.reposition()

        self._update_composer_overlay()

    def _update_composer_overlay(self):
        """Keep the composer as a centered, floating surface over the graph."""
        composer = getattr(self, "composer", None)
        chat_view = getattr(self, "chat_view", None)
        if composer is None or chat_view is None:
            return

        viewport = chat_view.viewport()
        if viewport is None or viewport.width() <= 0 or viewport.height() <= 0:
            return

        horizontal_margin = 16
        target_width = min(820, max(0, viewport.width() - horizontal_margin * 2))
        if target_width <= 0:
            composer.hide()
            return

        composer.show()
        composer.setFixedWidth(target_width)

        composer.setFixedHeight(max(COMPOSER_MIN_HEIGHT, composer.height()))

        overlay_parent = getattr(self, "composer_overlay_parent", None)
        viewport_origin = (
            viewport.mapTo(overlay_parent, QPoint(0, 0))
            if overlay_parent is not None
            else QPoint(0, 0)
        )
        target_x = viewport_origin.x() + max(0, (viewport.width() - target_width) // 2)
        bottom_inset = 18
        if (
            token_counter_widget := getattr(self, "token_counter_widget", None)
        ) is not None and token_counter_widget.isVisible():
            if target_x < token_counter_widget.width() + horizontal_margin:
                bottom_inset += token_counter_widget.height() + 12

        target_y = viewport_origin.y() + max(
            12,
            viewport.height() - composer.height() - bottom_inset,
        )
        composer.move(target_x, target_y)
        composer.raise_()

        # The composer sits above graph controls, but transient notifications
        # are a higher-priority surface and must remain readable.  Reassert
        # their ordering here because composer repositioning can happen after
        # a notification is shown (resize, draft growth, or viewport layout).
        notification_banner = getattr(self, "notification_banner", None)
        if notification_banner is not None and notification_banner.isVisible():
            notification_banner.raise_()

        # The command palette is meant to be the topmost overlay whenever
        # open - reassert above both composer and notification for the same
        # reason as the notification reassert just above.
        command_palette_host = getattr(self, "command_palette_host", None)
        if command_palette_host is not None and command_palette_host.isVisible():
            command_palette_host.raise_()

        self._position_composer_picker()
        self._position_composer_context_popup()

    def _position_composer_popup(self, popup):
        composer = getattr(self, "composer", None)
        chat_view = getattr(self, "chat_view", None)
        if popup is None or composer is None or chat_view is None:
            return

        viewport = chat_view.viewport()
        viewport_origin = viewport.mapToGlobal(QPoint(0, 0))
        viewport_rect = QRect(viewport_origin, viewport.size())
        composer_origin = composer.mapToGlobal(QPoint(0, 0))
        composer_rect = QRect(composer_origin, composer.size())
        popup.move(composer_picker_position(viewport_rect, composer_rect, popup.size()))

    def _position_composer_picker(self):
        """Keep the native model/reasoning picker above the graph."""
        self._position_composer_popup(getattr(self, "_composer_picker", None))

    def _position_composer_context_popup(self):
        """Keep context review outside the clipped QWebEngine document."""
        self._position_composer_popup(getattr(self, "_composer_context_popup", None))

    def _clear_composer_picker(self, picker=None):
        if picker is None or picker is getattr(self, "_composer_picker", None):
            self._composer_picker = None

    def _close_composer_picker(self):
        picker = getattr(self, "_composer_picker", None)
        self._composer_picker = None
        if picker is not None:
            picker.close()
            picker.deleteLater()

    def _clear_composer_context_popup(self, popup=None):
        if popup is None or popup is getattr(self, "_composer_context_popup", None):
            self._composer_context_popup = None

    def _close_composer_context_popup(self):
        popup = getattr(self, "_composer_context_popup", None)
        self._composer_context_popup = None
        if popup is not None:
            popup.close()
            popup.deleteLater()

    def open_composer_model_picker(self, kind="model"):
        """Open a native model/reasoning picker requested by the React composer."""
        bridge = getattr(getattr(self, "composer", None), "bridge", None)
        if bridge is None:
            return

        self._close_composer_context_popup()

        requested_kind = "reasoning" if kind == "reasoning" else "model"
        current = getattr(self, "_composer_picker", None)
        if current is not None:
            if current.kind == requested_kind and current.isVisible():
                self._close_composer_picker()
                return
            self._close_composer_picker()

        picker = ComposerPickerPopup(requested_kind, bridge.route_snapshot(), self)
        picker.modelSelected.connect(bridge.selectModel)
        picker.reasoningSelected.connect(bridge.setReasoningLevel)
        picker.settingsRequested.connect(self.show_settings)
        picker.destroyed.connect(lambda: self._clear_composer_picker(picker))
        self._composer_picker = picker
        picker.adjustSize()
        self._position_composer_picker()
        picker.show()
        self._position_composer_picker()
        picker.raise_()
        picker.activateWindow()

    def open_composer_context_popup(self, context):
        """Open context review as a native window-level surface."""
        bridge = getattr(getattr(self, "composer", None), "bridge", None)
        if bridge is None:
            return

        current = getattr(self, "_composer_context_popup", None)
        if current is not None and current.isVisible():
            self._close_composer_context_popup()
            return

        self._close_composer_picker()
        popup = ComposerContextPopup(context, self)
        popup.contextItemRemoved.connect(bridge.removeContextItem)
        popup.destroyed.connect(lambda: self._clear_composer_context_popup(popup))
        self._composer_context_popup = popup
        popup.adjustSize()
        self._position_composer_context_popup()
        popup.show()
        self._position_composer_context_popup()
        popup.raise_()
        popup.activateWindow()

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

        if self.settings_panel is not None:
            self.settings_panel.bridge.refresh_update_status()

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
        # Artifact/Gitlink/Code Sandbox/PyCoder nodes each keep their running worker on
        # their own node.worker_thread - multiple nodes of the same plugin type can run
        # concurrently, so there is no single shared main_window attribute to check for
        # them anymore.
        for attr_name, label in (
            ("chat_thread", "active chat request"),
            ("takeaway_thread", "takeaway generation"),
            ("explainer_thread", "explanation generation"),
            ("chart_thread", "chart generation"),
            ("group_summary_thread", "group summary generation"),
            ("image_gen_thread", "image generation"),
            ("pycoder_agent_thread", "PyCoder analysis"),
            ("conversation_node_thread", "conversation node request"),
            ("update_check_worker", "update check"),
        ):
            worker = getattr(self, attr_name, None)
            if worker is not None:
                yield label, worker, (lambda name=attr_name: setattr(self, name, None))

        for operation_id, worker in list(getattr(self, "utility_threads", {}).items()):
            yield "canvas utility operation", worker, (lambda op=operation_id: self.utility_threads.pop(op, None))

        chat_view = getattr(self, "chat_view", None)
        scene = chat_view.scene() if chat_view is not None else None
        if scene is not None:
            for node_list_name, label in (
                ("code_sandbox_nodes", "code sandbox execution"),
                ("artifact_nodes", "artifact workflow"),
                ("pycoder_nodes", "PyCoder execution"),
                ("gitlink_nodes", "Gitlink proposal"),
                ("web_nodes", "web research"),
            ):
                for node in list(getattr(scene, node_list_name, [])):
                    worker = getattr(node, "worker_thread", None)
                    if worker is not None:
                        yield label, worker, (lambda n=node: setattr(n, "worker_thread", None))

        save_thread = getattr(getattr(self, "session_manager", None), "save_thread", None)
        if save_thread is not None:
            yield "background save", save_thread, None

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
        for label, worker, clear_ref in self._iter_shutdown_threads():
            if not hasattr(worker, "isRunning") or not worker.isRunning():
                continue

            self._request_thread_shutdown(worker)
            if worker.wait(timeout_ms):
                if clear_ref is not None:
                    clear_ref()
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

        self._close_composer_picker()
        self._close_composer_context_popup()
        prepare_composer_shutdown = getattr(
            getattr(self, "composer", None),
            "prepare_for_shutdown",
            None,
        )
        if callable(prepare_composer_shutdown):
            prepare_composer_shutdown()
        # Symmetric with installEventFilter in __init__ - matches every other
        # install/removeEventFilter pair in this codebase (e.g.
        # graphlink_composer_popups.py, graphlink_context_menu.py). Not
        # reachable as a live bug today (ChatWindow is constructed exactly
        # once per process), but a still-live QApplication instance keeps a
        # removed window's filter installed forever otherwise - real risk the
        # moment a future multi-window feature reconstructs ChatWindow.
        accelerator_filter = getattr(self, "_accelerator_filter", None)
        if accelerator_filter is not None:
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(accelerator_filter)
        mark_clean_exit()
        super().closeEvent(event)

    def show_previous_crash_notice(self):
        """Called by main() when graphlink_crash.previous_run_crashed() found the
        running.lock sentinel still present at startup - the prior run didn't reach
        closeEvent's clean-exit path."""
        self.notification_banner.show_message(
            "Graphlink didn't shut down cleanly last time. If it crashed, a report was "
            "saved under ~/.graphlink/crash/.",
            8000,
            "warning",
        )

    def _sync_footer_height(self, *_):
        if not hasattr(self, "composer"):
            return
        self._schedule_overlay_update()

    def _handle_composer_state_changed(self, state, message):
        if not hasattr(self, 'composer'):
            return
        active_states = {"preparing", "uploading", "waiting", "generating", "finalizing"}
        self.composer.set_request_state(state in active_states, self._main_request_cancel_pending, message)
        
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
             if self.cancel_latest_utility_operation():
                 event.accept()
             elif self.pending_attachments: self.clear_attachment()
        elif event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.key() == Qt.Key.Key_N:
                view_pos = self.chat_view.mapFromGlobal(QCursor.pos()); scene_pos = self.chat_view.mapToScene(view_pos); self.chat_view.scene().add_note(scene_pos)
        elif event.key() == Qt.Key.Key_Delete: self.chat_view.scene().deleteSelectedItems()
        else: super().keyPressEvent(event)
        
    def save_chat(self):
        if self.session_manager.save_current_chat():
            self.notification_banner.show_message("Chat saved in background.", 3000, "success")

    def should_show_notification(self, msg_type):
        if not hasattr(self, "settings_manager") or self.settings_manager is None:
            return True
        return self.settings_manager.get_notification_type_enabled(msg_type)

    def setup_toolbar(self, toolbar):
        toolbar.setIconSize(QSize(20, 20)); toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        toolbar.setStyleSheet("QToolBar { spacing: 4px; padding: 4px; } QToolButton { color: white; background: transparent; border: none; border-radius: 4px; padding: 6px; margin: 2px; font-size: 12px; } QToolButton:hover { background: rgba(255, 255, 255, 0.1); }")
        self.pins_btn = QToolButton(); self.pins_btn.setText("Pins"); self.pins_btn.setCheckable(True); self.pins_btn.setToolTip("Show navigation pins"); self.pins_btn.clicked.connect(self.toggle_pin_overlay); toolbar.addWidget(self.pins_btn)
        organize_btn = QToolButton(); organize_btn.setText("Organize"); organize_btn.setObjectName("actionButton"); organize_btn.clicked.connect(lambda: self.chat_view.scene().organize_nodes()); toolbar.addWidget(organize_btn)
        toolbar.addSeparator()
        zoom_in_btn = QToolButton(); zoom_in_btn.setText("Zoom In"); zoom_in_btn.clicked.connect(lambda: self.chat_view.zoom_by(1.1)); toolbar.addWidget(zoom_in_btn)
        zoom_out_btn = QToolButton(); zoom_out_btn.setText("Zoom Out"); zoom_out_btn.clicked.connect(lambda: self.chat_view.zoom_by(0.9)); toolbar.addWidget(zoom_out_btn)
        toolbar.addSeparator()
        reset_btn = QToolButton(); reset_btn.setText("Reset"); reset_btn.clicked.connect(self.chat_view.reset_zoom); toolbar.addWidget(reset_btn)
        fit_btn = QToolButton(); fit_btn.setText("Fit All"); fit_btn.clicked.connect(self.chat_view.fit_all); toolbar.addWidget(fit_btn)
        toggle_overlays_btn = QToolButton(); toggle_overlays_btn.setText("Controls"); toggle_overlays_btn.setCheckable(True); toggle_overlays_btn.toggled.connect(self.chat_view.toggle_overlays_visibility); toolbar.addWidget(toggle_overlays_btn)

        self.plugins_button = QToolButton()
        self.plugins_button.setText("Plugins")
        self.plugins_button.setObjectName("actionButton")
        self.plugins_button.setPopupMode(QToolButton.ToolButtonPopupMode.DelayedPopup)
        self.plugins_button.setIcon(qta.icon("fa5s.chevron-down", color="#A4A4A4"))
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
            self.pin_overlay.close()
            return
        self.pin_overlay.show_for_anchor(self.pins_btn)

    def update_title_bar(self):
        title = "Graphlink"
        if self.session_manager and self.session_manager.current_chat_id:
            chat_title = self.session_manager.db.get_chat_title(self.session_manager.current_chat_id)
            if chat_title: title = f"Graphlink - {chat_title}"
        self.setWindowTitle(title)

    def show_about_dialog(self):
        # Cached once and toggled, replacing the legacy AboutDialog(self).exec()
        # (modal, constructed fresh every call) - see graphlink_about_web.py's
        # module docstring for the modal->non-modal rationale.
        if self.about_panel and self.about_panel.isVisible():
            self.about_panel.close()
            return
        if not self.about_panel:
            self.about_panel = AboutWebHost(self)
        self.about_panel.show_centered_over_parent()

    def show_help(self):
        if self.help_panel and self.help_panel.isVisible():
            self.help_panel.close()
            return

        if not self.help_panel:
            self.help_panel = HelpWebHost(self)

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
            # Unconditional since Phase 3 increment 10 deleted the legacy
            # SettingsDialog and its renderer flag (pre-deletion code is at
            # the legacy-settings-final git tag).
            self.settings_panel = SettingsWebHost(self.settings_manager, main_window=self, parent=self)

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
            api_provider.initialize_local_provider(
                config.LOCAL_PROVIDER_OLLAMA,
                {"reasoning_mode": self.settings_manager.get_ollama_reasoning_mode()},
            )
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

            saved_models = self.settings_manager.get_api_models(provider)
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

        # Switching modes calls api_provider.initialize_* which swaps the provider
        # globals (USE_API_MODE, API_CLIENT, API_KEY, ...) that a running chat request
        # is reading from a worker thread - the request could execute against a
        # half-swapped provider. Block the switch while a main request is in flight
        # instead of racing it.
        if self._main_request_active and mode_text != previous_mode:
            self._set_mode_combo_silently(previous_mode)
            self.notification_banner.show_message(
                "Can't switch modes while a response is being generated. "
                "Cancel the request or wait for it to finish, then switch.",
                6000,
                "warning",
            )
            return

        try:
            self._initialize_mode(mode_text, show_dialogs=True)
            self.settings_manager.set_current_mode(mode_text)
            self.reinitialize_agent()
            self._refresh_composer_provider_status()
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
        self._refresh_composer_provider_status()

    def setCurrentNode(self, node):
        self.current_node = node; text_content = ""
        if hasattr(self, 'composer'):
            self.composer.set_context_anchor(node)
        if isinstance(node, ChatNode): text_content = node.text if node.text else "[Attachment/Content Node]"
        elif isinstance(node, PyCoderNode): text_content = "Py-Coder Analysis"
        elif isinstance(node, CodeSandboxNode): text_content = "Execution Sandbox"
        elif isinstance(node, WebNode): text_content = "Web Research Node"
        elif isinstance(node, ConversationNode): text_content = "Conversation"
        elif isinstance(node, HtmlViewNode): text_content = "HTML Renderer"
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
        if hasattr(self, 'message_input'):
            self.message_input.set_context_items(self.pending_attachments)
        if hasattr(self, 'composer_controller'):
            self.composer_controller.set_attachments(self.pending_attachments)

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
        stage_result, stage_reason = self._stage_large_paste_as_attachment(pasted_text)
        if stage_result == "added":
            self.notification_banner.show_message(
                "Attached pasted text as context.",
                3000,
                "success",
            )
        elif stage_result == "rejected":
            self.notification_banner.show_message(
                stage_reason or "The pasted text could not be attached.",
                5000,
                "warning",
            )
        self.message_input.setFocus()

    def _stage_large_paste_as_attachment(self, pasted_text):
        if not pasted_text or not pasted_text.strip():
            return "rejected", "No text to attach."

        base_dir = os.path.join(tempfile.gettempdir(), "graphlink_paste_attachments")
        try:
            os.makedirs(base_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            suffix = self._guess_text_drop_suffix(pasted_text)
            temp_file_path = os.path.join(base_dir, f"pasted_text_{timestamp}{suffix}")
            # errors="replace" (not "ignore") - unencodable characters (e.g. an
            # unpaired surrogate from a malformed Windows clipboard/drop payload) become
            # a visible replacement character instead of silently vanishing from the
            # saved attachment with no trace.
            with open(temp_file_path, "w", encoding="utf-8", errors="replace") as temp_file:
                temp_file.write(pasted_text)

            line_count = pasted_text.count("\n") + 1
            kind_label = "Pasted Code" if self._looks_like_code_text(pasted_text, suffix) else "Pasted Text"
            preview_name = f"{kind_label} ({line_count} lines){suffix}"
            return self._stage_attachment_file(temp_file_path, is_temp=True, display_name=preview_name)
        except OSError:
            return "rejected", "Could not create a temporary attachment file."

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

        base_dir = os.path.join(tempfile.gettempdir(), "graphlink_drop_attachments")
        try:
            os.makedirs(base_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            suffix = self._guess_text_drop_suffix(dropped_text)
            temp_file_path = os.path.join(base_dir, f"dropped_context_{timestamp}{suffix}")
            # errors="replace" (not "ignore") - unencodable characters (e.g. an
            # unpaired surrogate from a malformed Windows clipboard/drop payload) become
            # a visible replacement character instead of silently vanishing from the
            # saved attachment with no trace.
            with open(temp_file_path, "w", encoding="utf-8", errors="replace") as temp_file:
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
        self.message_input.setEnabled(True)
        
    def _get_single_selected_node(self):
        selected_items = self.chat_view.scene().selectedItems(); valid_types = (ChatNode, PyCoderNode, CodeSandboxNode, WebNode, ConversationNode, HtmlViewNode, GitlinkNode)
        if len(selected_items) == 1 and isinstance(selected_items[0], valid_types): return selected_items[0]
        return None

    def reset_token_counter(self, total_tokens=0):
        self.total_session_tokens = total_tokens; self.token_counter_bridge.reset(); self.token_counter_bridge.update_counts(total_tokens=self.total_session_tokens)

    def new_chat(self, parent_for_dialog=None):
        scene = self.chat_view.scene()
        if not scene.items() and not self.session_manager.current_chat_id: return True
        reply = QMessageBox.question(parent_for_dialog or self, 'New Chat', 'Start a new chat? Any unsaved changes will be lost.', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            if self._main_request_active and self.chat_thread is not None:
                self._cancel_main_chat_request(self.chat_thread)
                self.chat_thread = None
                self._set_main_request_state(active=False)
                self._clear_loading_animation()
                self._clear_pending_response_preview()
            if hasattr(self, 'pin_overlay') and self.pin_overlay: self.pin_overlay.clear_pins()
            self.session_manager.mark_context_switch(); self.session_manager.current_chat_id = None; scene.clear(); self.current_node = None; self.message_input.clear(); self.clear_attachment(); self.message_input.set_context_anchor(None); self.message_input.setPlaceholderText("Type your message..."); self.update_title_bar(); self.reset_token_counter(); return True
        return False
