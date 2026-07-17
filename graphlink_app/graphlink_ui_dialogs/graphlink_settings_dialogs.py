import os
import webbrowser
import qtawesome as qta
from PySide6.QtCore import QPoint, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFormLayout,
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QRadioButton,
    QScrollArea, QSizePolicy, QSpinBox, QStackedWidget, QVBoxLayout, QWidget
)

import api_provider
import graphlink_config as config
from graphlink_agents import ModelPullWorkerThread
from graphlink_styles import THEMES
from graphlink_config import apply_theme, get_current_palette, get_semantic_color, set_current_model
from graphlink_update import APP_VERSION, UPDATE_REPOSITORY_URL
from graphlink_paths import asset_url
from graphlink_model_catalog import AUTO_MODEL, INHERIT_MODEL, ModelAssignment


class SettingsComboPopup(QFrame):
    item_selected = Signal(int, str)
    popup_closed = Signal()

    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setObjectName("settingsComboPopupFrame")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 6)
        shadow.setColor(Qt.GlobalColor.black)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(8, 8, 8, 10)
        outer_layout.setSpacing(0)

        self.shell = QFrame()
        self.shell.setObjectName("settingsComboPopupShell")
        self.shell.setGraphicsEffect(shadow)
        outer_layout.addWidget(self.shell)

        shell_layout = QVBoxLayout(self.shell)
        shell_layout.setContentsMargins(4, 4, 4, 4)
        shell_layout.setSpacing(0)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search models...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._filter_items)
        self.search_input.returnPressed.connect(self._activate_current_item)
        shell_layout.addWidget(self.search_input)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("settingsComboPopupList")
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

    def apply_style(self, accent_color):
        self.setStyleSheet(f"""
            QFrame#settingsComboPopupFrame {{
                background-color: transparent;
                border: none;
            }}
            QFrame#settingsComboPopupShell {{
                background-color: #2D2D2D;
                border: 1px solid #3F3F3F;
                border-radius: 9px;
            }}
            QListWidget#settingsComboPopupList {{
                background-color: transparent;
                color: #FFFFFF;
                border: none;
                outline: none;
                padding: 2px;
            }}
            QListWidget#settingsComboPopupList::item {{
                background-color: transparent;
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                min-height: 26px;
                padding: 6px 10px;
            }}
            QListWidget#settingsComboPopupList::item:hover {{
                background-color: #383838;
            }}
            QListWidget#settingsComboPopupList::item:selected {{
                background-color: {accent_color};
                color: #FFFFFF;
            }}
            QListWidget#settingsComboPopupList::item:selected:hover {{
                background-color: {accent_color};
                color: #FFFFFF;
            }}
            QLineEdit {{
                background-color: #242424;
                color: #FFFFFF;
                border: 1px solid #4A4A4A;
                border-radius: 6px;
                padding: 6px 8px;
                margin: 2px 2px 6px 2px;
            }}
        """)

    def populate_from_combo(self, combo):
        current_index = combo.currentIndex()
        current_text = combo.currentText()

        self.list_widget.clear()
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
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
                self.list_widget.scrollToItem(current_item, QAbstractItemView.ScrollHint.PositionAtCenter)
        else:
            self.list_widget.clearSelection()

    def _filter_items(self, query):
        query = str(query or "").strip().lower()
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            item.setHidden(bool(query and query not in item.text().lower()))
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if not item.isHidden():
                self.list_widget.setCurrentItem(item)
                break

    def _activate_current_item(self):
        item = self.list_widget.currentItem()
        if item is not None and not item.isHidden():
            self._emit_item_selection(item)

    def show_for_combo(self, combo):
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

        visible_rows = min(max(self.list_widget.count(), 1), 8)
        popup_width = max(combo.width(), min(max_text_width + 56, 460))
        popup_height = (visible_rows * row_height) + 22
        self.resize(popup_width, popup_height)

        target_global = combo.mapToGlobal(QPoint(0, combo.height() + 4))
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
        self.search_input.setFocus()

    def _emit_item_selection(self, item):
        if item is None:
            return
        index = item.data(Qt.ItemDataRole.UserRole)
        self.item_selected.emit(index, item.text())

    def hideEvent(self, event):
        self.popup_closed.emit()
        super().hideEvent(event)


class SettingsComboBox(QComboBox):
    def __init__(self, parent=None, placeholder_text=None):
        super().__init__(parent)
        self.setObjectName("settingsComboBox")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(1)
        if placeholder_text:
            self.setPlaceholderText(placeholder_text)

        self._popup = SettingsComboPopup(self)
        self._popup.item_selected.connect(self._apply_popup_selection)
        self._popup.popup_closed.connect(self._handle_popup_closed)
        self._popup_closing = False

    def apply_popup_style(self, accent_color):
        self._popup.apply_style(accent_color)

    def showPopup(self):
        if not self.isEnabled() or self.count() == 0:
            return
        self._popup.show_for_combo(self)

    def hidePopup(self):
        if self._popup.isVisible():
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

    def wheelEvent(self, event):
        if self.hasFocus() or self._popup.isVisible():
            super().wheelEvent(event)
            return
        event.ignore()


class SettingsSpinBox(QSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
            return
        event.ignore()


class OllamaModelScanWorker(QThread):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, scan_path=None, parent=None):
        super().__init__(parent)
        self.scan_path = scan_path

    def run(self):
        try:
            results = api_provider.scan_local_ollama_models(self.scan_path)
            self.finished.emit(results)
        except Exception as exc:
            self.error.emit(str(exc))


class ApiModelLoadWorker(QThread):
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, provider, api_key, base_url=None, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url

    def run(self):
        try:
            # Discovery is deliberately isolated from the GUI thread.  It also
            # exercises the same provider initialization path used by Save, so a
            # successful catalog load is a useful connection check.
            api_provider.initialize_api(
                self.provider,
                self.api_key,
                self.base_url if self.provider == config.API_PROVIDER_OPENAI else None,
            )
            descriptors = api_provider.get_available_model_descriptors()
            self.finished.emit([
                {
                    "model_id": descriptor.model_id,
                    "provider": descriptor.provider,
                    "capabilities": sorted(descriptor.capabilities),
                    "ready": descriptor.ready,
                    "available": descriptor.available,
                }
                for descriptor in descriptors
            ])
        except Exception as exc:
            self.error.emit(str(exc))


class LlamaCppModelScanWorker(QThread):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, scan_path=None, parent=None):
        super().__init__(parent)
        self.scan_path = scan_path

    def run(self):
        try:
            results = api_provider.scan_local_llama_cpp_models(self.scan_path)
            self.finished.emit(results)
        except Exception as exc:
            self.error.emit(str(exc))


def _settings_file_dialog_parent(widget):
    window = widget.window() if widget else None
    if window is not None:
        parent_widget = window.parentWidget() if hasattr(window, "parentWidget") else None
        if parent_widget is not None:
            return parent_widget
        return window
    return widget


class OllamaSettingsWidget(QWidget):
    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.worker_thread = None
        self.scan_worker = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)

        info_label = QLabel("Configure the local chat model, the model used to name new chats, and the reasoning mode when using the Ollama provider.")
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #D4D4D4; margin-bottom: 15px;")
        layout.addWidget(info_label)
        
        form_layout = QFormLayout()
        form_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        reasoning_mode_label = QLabel("Reasoning Mode:")
        reasoning_mode_label.setStyleSheet("color: #FFFFFF; font-weight: bold;")
        
        self.thinking_radio = QRadioButton("Thinking Mode (Enable CoT)")
        self.thinking_radio.setToolTip("Instructs the model to provide a step-by-step reasoning process. Best for complex queries.")
        
        self.quick_radio = QRadioButton("Quick Mode (No CoT)")
        self.quick_radio.setToolTip("Instructs the model to provide a direct answer without showing its reasoning. Faster for simple queries.")
        
        self.reasoning_group = QButtonGroup(self)
        self.reasoning_group.addButton(self.thinking_radio)
        self.reasoning_group.addButton(self.quick_radio)

        reasoning_layout = QHBoxLayout()
        reasoning_layout.addWidget(self.thinking_radio)
        reasoning_layout.addWidget(self.quick_radio)
        reasoning_layout.addStretch()
        
        form_layout.addRow(reasoning_mode_label, reasoning_layout)

        saved_mode = self.settings_manager.get_ollama_reasoning_mode()
        if saved_mode == "Thinking":
            self.thinking_radio.setChecked(True)
        else:
            self.quick_radio.setChecked(True)

        saved_model = self.settings_manager.get_ollama_chat_model()
        self.saved_assignments = (
            self.settings_manager.get_ollama_model_assignments()
            if hasattr(self.settings_manager, "get_ollama_model_assignments")
            else {}
        )
        self.models = self._build_model_cache()

        self.current_model_label = QLabel(f"<b>{saved_model or 'Auto — no installed model selected yet'}</b>")
        self.current_model_label.setStyleSheet(f"color: {get_semantic_color('status_success').name()};")
        form_layout.addRow("Current Active Chat Model:", self.current_model_label)

        scan_controls = QHBoxLayout()
        self.system_scan_button = QPushButton("System Scan")
        self.system_scan_button.clicked.connect(self.scan_system_for_models)
        self.folder_scan_button = QPushButton("Scan Folder...")
        self.folder_scan_button.clicked.connect(self.scan_selected_folder)
        scan_controls.addWidget(self.system_scan_button)
        scan_controls.addWidget(self.folder_scan_button)
        scan_controls.addStretch()
        form_layout.addRow("Available Model Scan:", scan_controls)

        self.scan_summary_label = QLabel(self._get_scan_summary_text())
        self.scan_summary_label.setWordWrap(True)
        self.scan_summary_label.setStyleSheet("color: #A5A5A5;")
        form_layout.addRow("", self.scan_summary_label)

        # Create both synchronized controls before connecting either signal.
        # ``setCurrentText(saved_model)`` can emit immediately; connecting the
        # combo before ``model_input`` exists caused a startup-time AttributeError
        # and left the settings dialog in a partially initialized Qt state.
        self.model_input = QLineEdit()
        self.model_input.setPlaceholderText("Advanced model ID entry")
        self.model_input.setVisible(False)
        self.model_input.setText(saved_model)

        self.model_combo = SettingsComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setPlaceholderText("Select an installed model or enter a model ID")
        self.model_combo.addItems([""] + self.models)
        self.model_combo.blockSignals(True)
        self.model_combo.setCurrentText(saved_model)
        self.model_combo.blockSignals(False)
        self.model_combo.currentTextChanged.connect(self.on_combo_change)
        form_layout.addRow("Chat Model:", self.model_combo)

        self.model_input.textChanged.connect(self.on_text_change)
        form_layout.addRow("", self.model_input)

        self.title_model_combo = SettingsComboBox()
        self._configure_assignment_combo(self.title_model_combo, "task_title")
        form_layout.addRow("Chat Naming Model:", self.title_model_combo)

        self.chart_model_combo = SettingsComboBox()
        self._configure_assignment_combo(self.chart_model_combo, "task_chart")
        form_layout.addRow("Chart Generation Model:", self.chart_model_combo)

        self.web_validate_model_combo = SettingsComboBox()
        self._configure_assignment_combo(self.web_validate_model_combo, "task_web_validate")
        form_layout.addRow("Web Content Validation Model:", self.web_validate_model_combo)

        self.web_summarize_model_combo = SettingsComboBox()
        self._configure_assignment_combo(self.web_summarize_model_combo, "task_web_summarize")
        form_layout.addRow("Web Content Summarization Model:", self.web_summarize_model_combo)

        layout.addLayout(form_layout)

        naming_help = QLabel("Each task can inherit the chat model, choose Auto, or use an explicit installed/custom model. Missing models stay visible as unavailable instead of silently changing routes.")
        naming_help.setWordWrap(True)
        naming_help.setStyleSheet("color: #A5A5A5; margin-top: 2px;")
        layout.addWidget(naming_help)

        task_models_help = QLabel(
            "Auto chooses from models detected on this machine. Ollama model IDs are never assumed or downloaded implicitly; use Validate and Pull for a custom ID."
        )
        task_models_help.setWordWrap(True)
        task_models_help.setStyleSheet("color: #A5A5A5; margin-top: 2px;")
        layout.addWidget(task_models_help)

        scan_help = QLabel("System scan checks the local Ollama install/cache locations and stores the discovered list until you rescan. Folder scan lets you point directly at a custom models folder.")
        scan_help.setWordWrap(True)
        scan_help.setStyleSheet("color: #A5A5A5; margin-top: 4px;")
        layout.addWidget(scan_help)

        self.status_label = QLabel("Enter a model name to validate and set it.")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("statusLabel")
        self.status_label.setStyleSheet(f"color: {get_semantic_color('status_warning').name()}; min-height: 40px;")
        layout.addWidget(self.status_label)
        layout.addStretch()

        button_layout = QHBoxLayout()
        self.validate_button = QPushButton("Validate and Pull Model")
        self.validate_button.clicked.connect(self.validate_model)
        self.save_button = QPushButton("Save Settings")
        self.save_button.clicked.connect(self.save_settings)
        button_layout.addStretch()
        button_layout.addWidget(self.validate_button)
        button_layout.addWidget(self.save_button)
        layout.addLayout(button_layout)

        self.on_theme_changed()
        QTimer.singleShot(0, self.scan_system_for_models)

    def on_theme_changed(self):
        palette = get_current_palette()
        selection_color = palette.SELECTION.name()
        selection_border = palette.SELECTION.darker(110).name()

        self.setStyleSheet(f"""
            QRadioButton {{
                color: #CCCCCC;
                font-size: 11px;
            }}
            QRadioButton::indicator {{
                width: 16px;
                height: 16px;
            }}
            QRadioButton::indicator:unchecked {{
                border: 1px solid #555;
                background-color: #3F3F3F;
                border-radius: 4px;
            }}
            QRadioButton::indicator:checked {{
                background-color: {selection_color};
                border: 1px solid {selection_border};
                image: url({asset_url('check.png')});
                border-radius: 4px;
            }}
        """)

    def _configure_assignment_combo(self, combo, task):
        combo.setEditable(True)
        combo.clear()
        combo.addItem("Use chat model", INHERIT_MODEL)
        combo.addItem("Auto — choose a compatible installed model", AUTO_MODEL)
        for model in self.models:
            combo.addItem(model, model)
        assignment = ModelAssignment.from_value(self.saved_assignments.get(task, {}))
        if assignment.mode == "explicit" and assignment.model_id:
            combo.setCurrentText(assignment.model_id)
        else:
            target_mode = assignment.mode if assignment.mode in {INHERIT_MODEL, AUTO_MODEL} else INHERIT_MODEL
            index = combo.findData(target_mode)
            combo.setCurrentIndex(index if index >= 0 else 0)

    def _assignment_from_combo(self, combo, *, default_mode=INHERIT_MODEL):
        data = combo.currentData()
        text = combo.currentText().strip()
        if data in {INHERIT_MODEL, AUTO_MODEL}:
            return ModelAssignment(str(data))
        if text and text not in {"Use chat model", "Auto — choose a compatible installed model"}:
            return ModelAssignment("explicit", text)
        return ModelAssignment(default_mode)

    def _build_model_cache(self):
        cached_models = self.settings_manager.get_ollama_scanned_models()
        combined_models = {
            str(model).strip()
            for model in cached_models
            if str(model).strip()
        }
        return sorted(combined_models, key=str.lower)

    def _get_scan_summary_text(self):
        scan_mode = self.settings_manager.get_ollama_model_scan_mode()
        scan_path = self.settings_manager.get_ollama_model_scan_path()
        cached_models = self.settings_manager.get_ollama_scanned_models()
        has_saved_scan = bool(scan_mode or scan_path or self.settings_manager.get_ollama_model_scan_locations())
        if not has_saved_scan:
            return "No saved scan yet. Run a system scan or choose a folder to build the local model list."
        if not cached_models:
            return "The last scan is saved, but it did not find any Ollama models."
        if scan_mode == "folder" and scan_path:
            return f"Using saved scan from folder: {scan_path}"
        if scan_mode == "system":
            return "Using saved system scan results from local Ollama locations."
        return "Using saved scanned model list."

    def _refresh_model_combos(self):
        current_model_text = self.model_input.text().strip()

        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems([""] + self.models)
        self.model_combo.setEditable(True)
        self.model_combo.setPlaceholderText("Select an installed model or enter a model ID")
        if current_model_text in self.models:
            self.model_combo.setCurrentText(current_model_text)
        else:
            self.model_combo.setCurrentText(current_model_text)
        self.model_combo.blockSignals(False)

        for combo, task in (
            (self.title_model_combo, "task_title"),
            (self.chart_model_combo, "task_chart"),
            (self.web_validate_model_combo, "task_web_validate"),
            (self.web_summarize_model_combo, "task_web_summarize"),
        ):
            current_assignment = self._assignment_from_combo(combo)
            self.saved_assignments[task] = current_assignment.to_dict()
            combo.blockSignals(True)
            self._configure_assignment_combo(combo, task)
            if current_assignment.mode == "explicit":
                combo.setCurrentText(current_assignment.model_id)
            combo.blockSignals(False)

        self.scan_summary_label.setText(self._get_scan_summary_text())

    def _set_scan_buttons_enabled(self, enabled):
        self.system_scan_button.setEnabled(enabled)
        self.folder_scan_button.setEnabled(enabled)

    def scan_system_for_models(self):
        self._start_scan_worker()

    def scan_selected_folder(self):
        initial_directory = self.settings_manager.get_ollama_model_scan_path() or str(os.path.expanduser("~"))
        selected_directory = QFileDialog.getExistingDirectory(
            _settings_file_dialog_parent(self),
            "Select Ollama Folder to Scan",
            initial_directory,
        )
        if not selected_directory:
            return
        self._start_scan_worker(selected_directory)

    def _start_scan_worker(self, scan_path=None):
        if self.scan_worker and self.scan_worker.isRunning():
            return

        self._set_scan_buttons_enabled(False)
        if scan_path:
            self.status_label.setText(f"Scanning folder for Ollama models: {scan_path}")
        else:
            self.status_label.setText("Scanning local Ollama model locations...")
        self.status_label.setStyleSheet(f"color: {get_semantic_color('status_info').name()}; min-height: 40px;")

        self.scan_worker = OllamaModelScanWorker(scan_path, self)
        self.scan_worker.finished.connect(self.handle_scan_finished)
        self.scan_worker.error.connect(self.handle_scan_error)
        self.scan_worker.start()

    def handle_scan_finished(self, results):
        models = results.get("models", [])
        self.settings_manager.set_ollama_model_scan_cache(
            models,
            results.get("scan_mode", ""),
            results.get("scan_path", ""),
            results.get("locations", []),
        )
        self.models = self._build_model_cache()
        self._refresh_model_combos()
        config.sync_ollama_task_models(self.settings_manager)
        resolved_chat_model = config.OLLAMA_MODELS.get(config.TASK_CHAT, "")
        self.current_model_label.setText(
            f"<b>{resolved_chat_model or 'Auto — no compatible installed model found'}</b>"
        )
        self._set_scan_buttons_enabled(True)

        if models:
            self.status_label.setText(
                f"Found {len(models)} Ollama model(s). Auto routing is now ready; saved selections were preserved."
            )
            self.status_label.setStyleSheet(
                f"color: {get_semantic_color('status_success').name()}; min-height: 40px;"
            )
        else:
            self.status_label.setText("Scan finished, but no Ollama models were found in the selected locations.")
            self.status_label.setStyleSheet(
                f"color: {get_semantic_color('status_warning').name()}; min-height: 40px;"
            )

        self.scan_worker = None

    def handle_scan_error(self, error_message):
        self._set_scan_buttons_enabled(True)
        self.status_label.setText(f"Scan failed: {error_message}")
        self.status_label.setStyleSheet(f"color: {get_semantic_color('status_error').name()}; min-height: 40px;")
        self.scan_worker = None

    def save_settings(self):
        model_name = self.model_combo.currentText().strip() or self.model_input.text().strip()
        reasoning_mode = "Thinking" if self.thinking_radio.isChecked() else "Quick"
        assignments = {
            "task_chat": ModelAssignment("explicit", model_name).to_dict() if model_name else ModelAssignment(AUTO_MODEL).to_dict(),
            "task_title": self._assignment_from_combo(self.title_model_combo).to_dict(),
            "task_chart": self._assignment_from_combo(self.chart_model_combo).to_dict(),
            "task_web_validate": self._assignment_from_combo(self.web_validate_model_combo).to_dict(),
            "task_web_summarize": self._assignment_from_combo(self.web_summarize_model_combo).to_dict(),
        }

        if hasattr(self.settings_manager, "set_ollama_model_assignments"):
            self.settings_manager.set_ollama_model_assignments(assignments)
        else:
            self.settings_manager.set_ollama_chat_model(model_name)
        self.settings_manager.set_ollama_reasoning_mode(reasoning_mode)
        if model_name:
            set_current_model(model_name)
        config.sync_ollama_task_models(self.settings_manager)

        main_window = self.window().parent()
        if main_window and hasattr(main_window, 'reinitialize_agent'):
            main_window.reinitialize_agent()

        self.current_model_label.setText(f"<b>{model_name or 'Auto — waiting for a detected model'}</b>")
        self.status_label.setText(
            "Settings saved. Auto will resolve from the next successful Ollama discovery." if not model_name
            else "Settings saved and applied for the current session."
        )
        self.status_label.setStyleSheet(f"color: {get_semantic_color('status_success').name()}; min-height: 40px;")

    def on_combo_change(self, text):
        if not text or not hasattr(self, "model_input"):
            return
        self.model_input.blockSignals(True)
        self.model_input.setText(text)
        self.model_input.blockSignals(False)

    def on_text_change(self, text):
        if not hasattr(self, "model_combo"):
            return
        self.model_combo.blockSignals(True)
        if text in self.models:
            self.model_combo.setCurrentText(text)
        else:
            self.model_combo.setCurrentIndex(0)
        self.model_combo.blockSignals(False)

    def validate_model(self):
        model_name = self.model_input.text().strip()
        if not model_name:
            self.status_label.setText("Model name cannot be empty.")
            return

        self.validate_button.setEnabled(False)
        self.validate_button.setText("Validating...")
        
        self.worker_thread = ModelPullWorkerThread(model_name)
        self.worker_thread.status_update.connect(self.handle_status_update)
        self.worker_thread.finished.connect(self.handle_worker_finished)
        self.worker_thread.error.connect(self.handle_worker_error)
        self.worker_thread.start()

    def handle_status_update(self, message):
        self.status_label.setText(message)
        self.status_label.setStyleSheet(f"color: {get_semantic_color('status_info').name()};")

    def handle_worker_finished(self, message, model_name):
        self.current_model_label.setText(f"<b>{model_name}</b>")
        self.status_label.setText(message)
        self.status_label.setStyleSheet(f"color: {get_semantic_color('status_success').name()};")
        self.reset_button()
        QMessageBox.information(self, "Success", message)

    def handle_worker_error(self, error_message):
        self.status_label.setText(f"Error: {error_message}")
        self.status_label.setStyleSheet(f"color: {get_semantic_color('status_error').name()};")
        self.reset_button()
        QMessageBox.warning(self, "Model Error", error_message)

    def reset_button(self):
        self.validate_button.setEnabled(True)
        self.validate_button.setText("Validate and Pull Model")


class LlamaCppSettingsWidget(QWidget):
    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.scan_worker = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)

        info_label = QLabel(
            "Configure direct local GGUF model access through llama-cpp-python. "
            "This mode loads the selected GGUF file into the app directly instead of calling a local server "
            "or reusing Ollama's internal model store."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #D4D4D4; margin-bottom: 15px;")
        layout.addWidget(info_label)

        form_layout = QFormLayout()
        form_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        reasoning_mode_label = QLabel("Reasoning Mode:")
        reasoning_mode_label.setStyleSheet("color: #FFFFFF; font-weight: bold;")

        self.thinking_radio = QRadioButton("Thinking Mode (Enable CoT)")
        self.quick_radio = QRadioButton("Quick Mode (No CoT)")
        self.reasoning_group = QButtonGroup(self)
        self.reasoning_group.addButton(self.thinking_radio)
        self.reasoning_group.addButton(self.quick_radio)

        reasoning_layout = QHBoxLayout()
        reasoning_layout.addWidget(self.thinking_radio)
        reasoning_layout.addWidget(self.quick_radio)
        reasoning_layout.addStretch()
        form_layout.addRow(reasoning_mode_label, reasoning_layout)

        saved_reasoning_mode = self.settings_manager.get_llama_cpp_reasoning_mode()
        if saved_reasoning_mode == "Thinking":
            self.thinking_radio.setChecked(True)
        else:
            self.quick_radio.setChecked(True)

        saved_chat_model = self.settings_manager.get_llama_cpp_chat_model_path()
        saved_title_model = self.settings_manager.get_llama_cpp_title_model_override_path()
        self.models = self._build_model_cache(saved_chat_model, saved_title_model)

        self.current_model_label = QLabel()
        self.current_model_label.setStyleSheet(f"color: {get_semantic_color('status_success').name()};")
        form_layout.addRow("Current Active GGUF:", self.current_model_label)

        scan_controls = QHBoxLayout()
        self.system_scan_button = QPushButton("System Scan")
        self.system_scan_button.clicked.connect(self.scan_system_for_models)
        self.folder_scan_button = QPushButton("Scan Folder...")
        self.folder_scan_button.clicked.connect(self.scan_selected_folder)
        scan_controls.addWidget(self.system_scan_button)
        scan_controls.addWidget(self.folder_scan_button)
        scan_controls.addStretch()
        form_layout.addRow("Available GGUF Scan:", scan_controls)

        self.scan_summary_label = QLabel(self._get_scan_summary_text())
        self.scan_summary_label.setWordWrap(True)
        self.scan_summary_label.setStyleSheet("color: #A5A5A5;")
        form_layout.addRow("", self.scan_summary_label)

        self.chat_model_combo = SettingsComboBox()
        self.chat_model_combo.setMinimumWidth(0)
        self.chat_model_combo.addItems([""] + self.models)
        self.chat_model_combo.currentTextChanged.connect(self.on_chat_combo_change)
        form_layout.addRow("Scanned Chat Model:", self.chat_model_combo)

        self.chat_model_input = QLineEdit(saved_chat_model)
        self.chat_model_input.setMinimumWidth(0)
        self.chat_model_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.chat_model_input.textChanged.connect(self._update_current_model_label)
        self.chat_model_input.textChanged.connect(self.on_chat_text_change)
        browse_chat_button = QPushButton("Browse...")
        browse_chat_button.clicked.connect(self.browse_chat_model)
        chat_model_row = QHBoxLayout()
        chat_model_row.addWidget(self.chat_model_input, 1)
        chat_model_row.addWidget(browse_chat_button)
        form_layout.addRow("Chat Model File:", chat_model_row)

        self.title_model_combo = SettingsComboBox()
        self.title_model_combo.setMinimumWidth(0)
        self.title_model_combo.addItems([""] + self.models)
        self.title_model_combo.currentTextChanged.connect(self.on_title_combo_change)
        form_layout.addRow("Scanned Naming Model:", self.title_model_combo)

        self.title_model_input = QLineEdit(saved_title_model)
        self.title_model_input.setMinimumWidth(0)
        self.title_model_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.title_model_input.textChanged.connect(self.on_title_text_change)
        browse_title_button = QPushButton("Browse...")
        browse_title_button.clicked.connect(self.browse_title_model)
        title_model_row = QHBoxLayout()
        title_model_row.addWidget(self.title_model_input, 1)
        title_model_row.addWidget(browse_title_button)
        form_layout.addRow("Chat Naming File:", title_model_row)

        self.chat_format_input = QLineEdit(self.settings_manager.get_llama_cpp_chat_format())
        self.chat_format_input.setMinimumWidth(0)
        self.chat_format_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        form_layout.addRow("Chat Format Override:", self.chat_format_input)

        self.n_ctx_spin = SettingsSpinBox()
        self.n_ctx_spin.setRange(256, 131072)
        self.n_ctx_spin.setSingleStep(256)
        self.n_ctx_spin.setValue(self.settings_manager.get_llama_cpp_n_ctx())
        form_layout.addRow("Context Window:", self.n_ctx_spin)

        self.n_gpu_layers_spin = SettingsSpinBox()
        self.n_gpu_layers_spin.setRange(-1, 9999)
        self.n_gpu_layers_spin.setValue(self.settings_manager.get_llama_cpp_n_gpu_layers())
        self.n_gpu_layers_spin.setToolTip("Use -1 to offload as many layers as the backend can fit.")
        form_layout.addRow("GPU Layers:", self.n_gpu_layers_spin)

        self.n_threads_spin = SettingsSpinBox()
        self.n_threads_spin.setRange(0, 256)
        self.n_threads_spin.setSpecialValueText("Auto")
        self.n_threads_spin.setValue(self.settings_manager.get_llama_cpp_n_threads())
        self.n_threads_spin.setToolTip("Set 0 to use Graphlink's safe auto mode, which reserves some CPU headroom to keep the UI responsive.")
        form_layout.addRow("CPU Threads:", self.n_threads_spin)

        layout.addLayout(form_layout)

        help_label = QLabel(
            "Leave Chat Format Override blank to let the GGUF metadata decide. "
            "If a model ships without a usable chat template, enter a specific chat format manually. "
            "Graphlink also forwards the `enable_thinking` chat-template flag to templates that support it, "
            "including Qwen3.5 GGUFs that expose a thinking toggle."
        )
        help_label.setWordWrap(True)
        help_label.setStyleSheet("color: #A5A5A5; margin-top: 4px;")
        layout.addWidget(help_label)

        title_help = QLabel(
            "Chat Naming File is optional. Leave it empty to reuse the main chat model instead of loading a second GGUF."
        )
        title_help.setWordWrap(True)
        title_help.setStyleSheet("color: #A5A5A5; margin-top: 2px;")
        layout.addWidget(title_help)

        scan_help = QLabel(
            "System scan looks through common local model folders for `.gguf` files only. "
            "Folder scan lets you point straight at a custom model directory. Ollama-managed manifests and blobs "
            "are not valid llama.cpp model files here."
        )
        scan_help.setWordWrap(True)
        scan_help.setStyleSheet("color: #A5A5A5; margin-top: 2px;")
        layout.addWidget(scan_help)

        self.status_label = QLabel("Choose a GGUF file and save settings.")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(f"color: {get_semantic_color('status_warning').name()}; min-height: 40px;")
        layout.addWidget(self.status_label)
        layout.addStretch()

        button_layout = QHBoxLayout()
        self.save_button = QPushButton("Save Settings")
        self.save_button.clicked.connect(self.save_settings)
        button_layout.addStretch()
        button_layout.addWidget(self.save_button)
        layout.addLayout(button_layout)

        self._update_current_model_label(saved_chat_model)
        self._refresh_model_combos()

    def _restore_settings_panel(self):
        settings_dialog = self.window()
        dialog_parent = _settings_file_dialog_parent(self)
        if (
            settings_dialog is not None
            and hasattr(settings_dialog, "show_for_anchor")
            and not settings_dialog.isVisible()
            and dialog_parent is not None
            and hasattr(dialog_parent, "settings_btn")
        ):
            settings_dialog.set_current_section(config.MODE_LLAMACPP_LOCAL)
            settings_dialog.show_for_anchor(dialog_parent.settings_btn)

    def _build_model_cache(self, *extra_models):
        cached_models = self.settings_manager.get_llama_cpp_scanned_models()
        combined_models = {
            str(model).strip()
            for model in [*cached_models, *extra_models]
            if str(model).strip()
        }
        return sorted(combined_models, key=str.lower)

    def _get_scan_summary_text(self):
        scan_mode = self.settings_manager.get_llama_cpp_model_scan_mode()
        scan_path = self.settings_manager.get_llama_cpp_model_scan_path()
        cached_models = self.settings_manager.get_llama_cpp_scanned_models()
        has_saved_scan = bool(scan_mode or scan_path or self.settings_manager.get_llama_cpp_model_scan_locations())
        if not has_saved_scan:
            return "No saved GGUF scan yet. Run a system scan or choose a folder to build the local model list."
        if not cached_models:
            return "The last GGUF scan is saved, but it did not find any models."
        if scan_mode == "folder" and scan_path:
            return f"Using saved scan from folder: {scan_path}"
        if scan_mode == "system":
            return "Using saved system scan results from common local model folders."
        return "Using saved scanned GGUF model list."

    def _refresh_model_combos(self):
        current_chat_text = self.chat_model_input.text().strip()
        current_title_text = self.title_model_input.text().strip()

        self.chat_model_combo.blockSignals(True)
        self.chat_model_combo.clear()
        self.chat_model_combo.addItems([""] + self.models)
        if current_chat_text in self.models:
            self.chat_model_combo.setCurrentText(current_chat_text)
        else:
            self.chat_model_combo.setCurrentIndex(0)
        self.chat_model_combo.blockSignals(False)

        self.title_model_combo.blockSignals(True)
        self.title_model_combo.clear()
        self.title_model_combo.addItems([""] + self.models)
        if current_title_text in self.models:
            self.title_model_combo.setCurrentText(current_title_text)
        else:
            self.title_model_combo.setCurrentIndex(0)
        self.title_model_combo.blockSignals(False)

        self.scan_summary_label.setText(self._get_scan_summary_text())

    def _set_scan_buttons_enabled(self, enabled):
        self.system_scan_button.setEnabled(enabled)
        self.folder_scan_button.setEnabled(enabled)

    def _select_model_file(self, caption, initial_path):
        initial_location = initial_path or self.settings_manager.get_llama_cpp_model_scan_path() or str(os.path.expanduser("~"))
        dialog_parent = _settings_file_dialog_parent(self)
        dialog = QFileDialog(dialog_parent, caption, initial_location, "GGUF Models (*.gguf);;All Files (*.*)")
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        if dialog.exec() == QFileDialog.DialogCode.Accepted:
            selected_files = dialog.selectedFiles()
            self._restore_settings_panel()
            return selected_files[0] if selected_files else ""
        self._restore_settings_panel()
        return ""

    def scan_system_for_models(self):
        self._start_scan_worker()

    def scan_selected_folder(self):
        initial_directory = self.settings_manager.get_llama_cpp_model_scan_path() or str(os.path.expanduser("~"))
        selected_directory = QFileDialog.getExistingDirectory(
            _settings_file_dialog_parent(self),
            "Select Folder to Scan for GGUF Models",
            initial_directory,
        )
        self._restore_settings_panel()
        if not selected_directory:
            return
        self._start_scan_worker(selected_directory)

    def _start_scan_worker(self, scan_path=None):
        if self.scan_worker and self.scan_worker.isRunning():
            return

        self._set_scan_buttons_enabled(False)
        if scan_path:
            self._set_status(f"Scanning folder for GGUF models: {scan_path}", "info")
        else:
            self._set_status("Scanning common local folders for GGUF models...", "info")

        self.scan_worker = LlamaCppModelScanWorker(scan_path, self)
        self.scan_worker.finished.connect(self.handle_scan_finished)
        self.scan_worker.error.connect(self.handle_scan_error)
        self.scan_worker.start()

    def browse_chat_model(self):
        selected_path = self._select_model_file("Select Llama.cpp Chat Model", self.chat_model_input.text().strip())
        if selected_path:
            self.chat_model_input.setText(selected_path)

    def browse_title_model(self):
        initial_path = self.title_model_input.text().strip() or self.chat_model_input.text().strip()
        selected_path = self._select_model_file("Select Llama.cpp Chat Naming Model", initial_path)
        if selected_path:
            self.title_model_input.setText(selected_path)

    def handle_scan_finished(self, results):
        models = results.get("models", [])
        self.settings_manager.set_llama_cpp_model_scan_cache(
            models,
            results.get("scan_mode", ""),
            results.get("scan_path", ""),
            results.get("locations", []),
        )
        self.models = self._build_model_cache(
            self.chat_model_input.text().strip(),
            self.title_model_input.text().strip(),
        )
        self._refresh_model_combos()
        self._set_scan_buttons_enabled(True)

        truncated_suffix = (
            " Scan stopped early (very large folder tree) - results may be incomplete; "
            "point the scan at a narrower folder to see everything."
            if results.get("truncated")
            else ""
        )
        if models:
            self._set_status(f"Found {len(models)} GGUF model file(s). Saved this list for reuse until the next scan.{truncated_suffix}", "success")
        else:
            self._set_status(f"Scan finished, but no GGUF models were found in the selected locations.{truncated_suffix}", "warning")

        self.scan_worker = None

    def handle_scan_error(self, error_message):
        self._set_scan_buttons_enabled(True)
        self._set_status(f"Scan failed: {error_message}", "error")
        self.scan_worker = None

    def _set_status(self, message, tone):
        color = {
            "success": get_semantic_color("status_success").name(),
            "warning": get_semantic_color("status_warning").name(),
            "error": get_semantic_color("status_error").name(),
        }.get(tone, get_semantic_color("status_info").name())
        self.status_label.setText(message)
        self.status_label.setStyleSheet(f"color: {color}; min-height: 40px;")

    def _update_current_model_label(self, model_path):
        normalized_path = str(model_path or "").strip()
        if not normalized_path:
            self.current_model_label.setText("<b>No model selected</b>")
            self.current_model_label.setToolTip("")
            return

        self.current_model_label.setText(f"<b>{os.path.basename(normalized_path)}</b>")
        self.current_model_label.setToolTip(normalized_path)

    def on_chat_combo_change(self, text):
        self.chat_model_input.blockSignals(True)
        self.chat_model_input.setText(text)
        self.chat_model_input.blockSignals(False)
        self._update_current_model_label(text)

    def on_chat_text_change(self, text):
        self.chat_model_combo.blockSignals(True)
        if text in self.models:
            self.chat_model_combo.setCurrentText(text)
        else:
            self.chat_model_combo.setCurrentIndex(0)
        self.chat_model_combo.blockSignals(False)

    def on_title_combo_change(self, text):
        self.title_model_input.blockSignals(True)
        self.title_model_input.setText(text)
        self.title_model_input.blockSignals(False)

    def on_title_text_change(self, text):
        self.title_model_combo.blockSignals(True)
        if text in self.models:
            self.title_model_combo.setCurrentText(text)
        else:
            self.title_model_combo.setCurrentIndex(0)
        self.title_model_combo.blockSignals(False)

    def _collect_settings(self):
        chat_model_path = self.chat_model_input.text().strip()
        title_model_path = self.title_model_input.text().strip()
        chat_format = self.chat_format_input.text().strip()

        if not chat_model_path:
            raise ValueError("Chat Model File cannot be empty.")
        if not os.path.isfile(chat_model_path):
            raise ValueError(f"Chat model file was not found:\n{chat_model_path}")
        if not chat_model_path.lower().endswith(".gguf"):
            raise ValueError("Chat Model File must point to a `.gguf` file. Ollama-managed blob files are not supported here.")

        if title_model_path:
            if not os.path.isfile(title_model_path):
                raise ValueError(f"Chat naming model file was not found:\n{title_model_path}")
            if not title_model_path.lower().endswith(".gguf"):
                raise ValueError("Chat Naming File must point to a `.gguf` file. Ollama-managed blob files are not supported here.")

        return {
            "chat_model_path": chat_model_path,
            "title_model_path": title_model_path,
            "reasoning_mode": "Thinking" if self.thinking_radio.isChecked() else "Quick",
            "chat_format": chat_format,
            "n_ctx": self.n_ctx_spin.value(),
            "n_gpu_layers": self.n_gpu_layers_spin.value(),
            "n_threads": self.n_threads_spin.value(),
        }

    def save_settings(self):
        try:
            settings = self._collect_settings()
        except ValueError as exc:
            self._set_status(str(exc), "error")
            QMessageBox.warning(self, "Invalid Llama.cpp Settings", str(exc))
            return

        if self.settings_manager.get_current_mode() == config.MODE_LLAMACPP_LOCAL:
            try:
                api_provider.initialize_local_provider(
                    config.LOCAL_PROVIDER_LLAMACPP,
                    settings,
                    preload_model=False,
                )
            except Exception as exc:
                self._set_status(f"Invalid Llama.cpp configuration: {exc}", "error")
                QMessageBox.critical(self, "Llama.cpp Configuration Error", str(exc))
                return

        self.settings_manager.set_llama_cpp_chat_model_path(settings["chat_model_path"])
        self.settings_manager.set_llama_cpp_title_model_path(settings["title_model_path"])
        self.settings_manager.set_llama_cpp_reasoning_mode(settings["reasoning_mode"])
        self.settings_manager.set_llama_cpp_runtime(
            n_ctx=settings["n_ctx"],
            n_gpu_layers=settings["n_gpu_layers"],
            n_threads=settings["n_threads"],
            chat_format=settings["chat_format"],
        )

        self._update_current_model_label(settings["chat_model_path"])
        main_window = self.window().parent()
        if main_window and hasattr(main_window, 'reinitialize_agent'):
            main_window.reinitialize_agent()

        self._set_status(
            "Llama.cpp settings have been saved. The GGUF will load on the first request instead of blocking the UI.",
            "success",
        )
        QMessageBox.information(
            self,
            "Saved",
            "Llama.cpp settings have been saved.\n\n"
            "The model will load on the first request instead of during Save Settings.",
        )


class ApiSettingsWidget(QWidget):
    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.api_worker = None
        self.api_worker_provider = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        
        layout.addWidget(QLabel("API Provider:", styleSheet="color: #FFFFFF; font-weight: bold;"))
        self.provider_combo = SettingsComboBox()
        self.provider_combo.addItems([
            config.API_PROVIDER_OPENAI,
            config.API_PROVIDER_ANTHROPIC,
            config.API_PROVIDER_GEMINI,
        ])
        self.provider_combo.currentTextChanged.connect(self._on_provider_changed)
        layout.addWidget(self.provider_combo)

        self.info_label = QLabel(
            "Configure your API endpoint.\n"
            "OpenAI-Compatible works with: OpenAI, LiteLLM, OpenRouter, LM Studio, and similar endpoints.\n"
            "Anthropic Claude uses Anthropic's native API.\n\n"
            "Choose different models for different tasks, including the chat naming model."
        )
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet("color: #D4D4D4; margin-bottom: 15px; margin-top: 10px;")
        layout.addWidget(self.info_label)

        self.base_url_label = QLabel("Base URL:")
        self.base_url_label.setStyleSheet("color: #FFFFFF; font-weight: bold;")
        layout.addWidget(self.base_url_label)
        self.base_url_input = QLineEdit(self.settings_manager.get_api_base_url(), placeholderText="https://api.openai.com/v1")
        layout.addWidget(self.base_url_input)

        layout.addWidget(QLabel("API Key:", styleSheet="color: #FFFFFF; font-weight: bold; margin-top: 10px;"))
        self.api_key_input = QLineEdit(echoMode=QLineEdit.Password, placeholderText="Enter your API key...")
        layout.addWidget(self.api_key_input)

        self.load_btn = QPushButton("Load Available Models")
        self.load_btn.clicked.connect(self.load_models_from_endpoint)
        layout.addWidget(self.load_btn)
        self.discovery_status_label = QLabel("Model catalog has not been refreshed yet.")
        self.discovery_status_label.setWordWrap(True)
        self.discovery_status_label.setStyleSheet("color: #A5A5A5;")
        layout.addWidget(self.discovery_status_label)

        layout.addWidget(QLabel("Model Selection (per task):", styleSheet="color: #FFFFFF; font-weight: bold; margin-top: 15px;"))

        self.model_combos = {}
        layout.addWidget(QLabel("Chat Naming / Session Title:", styleSheet="color: #D4D4D4; margin-top: 8px;"))
        self.title_combo = SettingsComboBox(placeholder_text="Select model...")
        self.title_combo.setEditable(True)
        self.model_combos[config.TASK_TITLE] = self.title_combo
        layout.addWidget(self.title_combo)
        layout.addWidget(QLabel("Used when a new chat is named in API Endpoint mode.", styleSheet="color: #A5A5A5; margin-top: 2px;"))
        
        layout.addWidget(QLabel("Chat, Explain, Takeaways (main model):", styleSheet="color: #D4D4D4; margin-top: 8px;"))
        self.chat_combo = SettingsComboBox(placeholder_text="Select model...")
        self.chat_combo.setEditable(True)
        self.model_combos[config.TASK_CHAT] = self.chat_combo
        layout.addWidget(self.chat_combo)

        layout.addWidget(QLabel("Chart Generation (code-capable model):", styleSheet="color: #D4D4D4; margin-top: 8px;"))
        self.chart_combo = SettingsComboBox(placeholder_text="Select model...")
        self.chart_combo.setEditable(True)
        self.model_combos[config.TASK_CHART] = self.chart_combo
        layout.addWidget(self.chart_combo)

        layout.addWidget(QLabel("Image Generation:", styleSheet="color: #D4D4D4; margin-top: 8px;"))
        self.image_combo = SettingsComboBox(placeholder_text="Select image model...")
        self.image_combo.setEditable(True)
        self.model_combos[config.TASK_IMAGE_GEN] = self.image_combo
        layout.addWidget(self.image_combo)
        self.image_help_label = QLabel("Select an image model if your provider supports image generation.")
        self.image_help_label.setWordWrap(True)
        self.image_help_label.setStyleSheet("color: #A5A5A5; margin-top: 2px;")
        layout.addWidget(self.image_help_label)
        
        layout.addWidget(QLabel("Web Content Validation:", styleSheet="color: #D4D4D4; margin-top: 8px;"))
        self.web_validate_combo = SettingsComboBox(placeholder_text="Select a validation model...")
        self.web_validate_combo.setEditable(True)
        self.model_combos[config.TASK_WEB_VALIDATE] = self.web_validate_combo
        layout.addWidget(self.web_validate_combo)

        layout.addWidget(QLabel("Web Content Summarization:", styleSheet="color: #D4D4D4; margin-top: 8px;"))
        self.web_summarize_combo = SettingsComboBox(placeholder_text="Select model...")
        self.web_summarize_combo.setEditable(True)
        self.model_combos[config.TASK_WEB_SUMMARIZE] = self.web_summarize_combo
        layout.addWidget(self.web_summarize_combo)

        layout.addStretch()

        button_layout = QHBoxLayout()
        self.reset_button = QPushButton("Reset API Settings")
        self.reset_button.clicked.connect(self.reset_settings)
        button_layout.addWidget(self.reset_button)
        button_layout.addStretch()
        
        self.save_button = QPushButton("Save Configuration")
        self.save_button.clicked.connect(self.save_settings)
        button_layout.addWidget(self.save_button)
        layout.addLayout(button_layout)

        saved_provider = self.settings_manager.get_api_provider()
        self.provider_combo.setCurrentText(saved_provider)
        self._on_provider_changed(saved_provider)
        self.restore_saved_models()

    def restore_saved_models(self):
        saved_models = self.settings_manager.get_api_models(self.provider_combo.currentText())
        provider = self.provider_combo.currentText()
        for task, combo in self.model_combos.items():
            if provider == config.API_PROVIDER_ANTHROPIC and task == config.TASK_IMAGE_GEN:
                continue
            saved_model = saved_models.get(task, "")
            if saved_model:
                if combo.findText(saved_model) == -1:
                    combo.addItem(saved_model)
                combo.setCurrentText(saved_model)

    def _populate_models(self, models, skip_tasks=None):
        skip_tasks = set(skip_tasks or ())
        for task, combo in self.model_combos.items():
            if task in skip_tasks:
                continue
            
            current = combo.currentText()
            combo.clear()
            combo.addItems(models)
            if current and combo.findText(current) != -1:
                combo.setCurrentText(current)

    def _required_tasks_for_provider(self, provider_name):
        required_tasks = list(self.model_combos.keys())
        if provider_name == config.API_PROVIDER_ANTHROPIC:
            required_tasks.remove(config.TASK_IMAGE_GEN)
        return required_tasks

    def _configure_anthropic_image_state(self):
        self.image_combo.clear()
        self.image_combo.setEnabled(False)
        self.image_combo.setEditable(False)
        self.image_help_label.setText("Anthropic Claude does not support image generation in Graphlink yet.")

    def _configure_supported_image_state(self, provider_name):
        self.image_combo.setEnabled(True)
        self.image_combo.setEditable(provider_name == config.API_PROVIDER_OPENAI)
        if provider_name == config.API_PROVIDER_GEMINI:
            self.image_help_label.setText("Select a Gemini image model for image generation.")
        else:
            self.image_help_label.setText("Select an image model if your provider supports image generation.")
    
    def _on_provider_changed(self, provider_name):
        is_openai = (provider_name == config.API_PROVIDER_OPENAI)
        is_anthropic = (provider_name == config.API_PROVIDER_ANTHROPIC)

        for task, combo in self.model_combos.items():
            combo.setEditable((is_openai or is_anthropic) and task != config.TASK_IMAGE_GEN)
            if task == config.TASK_WEB_VALIDATE:
                combo.setEditable(True)
        
        self.base_url_label.setVisible(is_openai)
        self.base_url_input.setVisible(is_openai)
        self.load_btn.setVisible(is_openai or is_anthropic)
        
        self.web_validate_combo.clear()
        self.web_validate_combo.setEnabled(True)

        if is_openai:
            self.api_key_input.setPlaceholderText("Enter your OpenAI-compatible API key...")
            self.api_key_input.setText(self.settings_manager.get_openai_key())
            self._populate_models([])
            self._configure_supported_image_state(provider_name)
        elif is_anthropic:
            self.api_key_input.setPlaceholderText("Enter your Anthropic API key...")
            self.api_key_input.setText(self.settings_manager.get_anthropic_key())
            self._populate_models([], skip_tasks={config.TASK_IMAGE_GEN})
            self._configure_anthropic_image_state()
        else:
            self.api_key_input.setPlaceholderText("Enter your Google Gemini API key...")
            self.api_key_input.setText(self.settings_manager.get_gemini_key())
            for task, combo in self.model_combos.items():
                if task in (config.TASK_WEB_VALIDATE, config.TASK_IMAGE_GEN):
                    continue
                combo.clear()
                combo.addItems(api_provider.GEMINI_MODELS_STATIC)
            self.web_validate_combo.addItems(api_provider.GEMINI_MODELS_STATIC)

            self.image_combo.clear()
            self.image_combo.addItems(api_provider.GEMINI_IMAGE_MODELS_STATIC)
            self._configure_supported_image_state(provider_name)

        self.discovery_status_label.setText(
            "Choose Refresh to load the provider's current catalog. Saved IDs remain available as unverified custom selections."
        )
        self.restore_saved_models()

    def load_models_from_endpoint(self):
        if self.api_worker and self.api_worker.isRunning():
            return
        provider = self.provider_combo.currentText()
        base_url = self.base_url_input.text().strip()
        api_key = self.api_key_input.text().strip()

        if provider == config.API_PROVIDER_OPENAI and not base_url:
            QMessageBox.warning(self, "Missing Information", "Please enter the Base URL for the OpenAI-compatible provider.")
            return
        if not api_key:
            QMessageBox.warning(self, "Missing Information", "Please enter the API Key.")
            return

        self.load_btn.setEnabled(False)
        self.load_btn.setText("Loading catalog…")
        self.discovery_status_label.setText("Contacting the provider… You can keep editing other settings.")
        self.discovery_status_label.setStyleSheet(f"color: {get_semantic_color('status_info').name()};")
        self.api_worker = ApiModelLoadWorker(
            provider,
            api_key,
            base_url if provider == config.API_PROVIDER_OPENAI else None,
            self,
        )
        self.api_worker_provider = provider
        self.api_worker.finished.connect(self.handle_models_loaded)
        self.api_worker.error.connect(self.handle_models_load_error)
        self.api_worker.finished.connect(self._clear_api_worker)
        self.api_worker.error.connect(self._clear_api_worker)
        self.api_worker.start()

    def handle_models_loaded(self, descriptors):
        if self.api_worker_provider != self.provider_combo.currentText():
            return
        models = [item.get("model_id", "") for item in descriptors if item.get("model_id")]
        provider = self.provider_combo.currentText()
        if hasattr(self.settings_manager, "set_api_model_catalog"):
            self.settings_manager.set_api_model_catalog(descriptors, provider)
        if provider == config.API_PROVIDER_OPENAI:
            self._populate_models(models)
        elif provider == config.API_PROVIDER_ANTHROPIC:
            self._populate_models(models, skip_tasks={config.TASK_IMAGE_GEN})
        else:
            self._populate_models(models, skip_tasks={config.TASK_IMAGE_GEN})
            self.image_combo.clear()
            self.image_combo.addItems(api_provider.GEMINI_IMAGE_MODELS_STATIC)
        self.restore_saved_models()
        self.discovery_status_label.setText(f"Catalog refreshed — {len(models)} model(s) available from {provider}.")
        self.discovery_status_label.setStyleSheet(f"color: {get_semantic_color('status_success').name()};")

    def handle_models_load_error(self, error_message):
        if self.api_worker_provider != self.provider_combo.currentText():
            return
        self.discovery_status_label.setText(
            f"Catalog refresh failed: {error_message}\nSaved/custom model IDs remain usable if the endpoint supports them."
        )
        self.discovery_status_label.setStyleSheet(f"color: {get_semantic_color('status_warning').name()};")

    def _clear_api_worker(self, *_args):
        self.load_btn.setEnabled(True)
        self.load_btn.setText("Refresh Available Models")
        self.api_worker = None
        self.api_worker_provider = None

    def save_settings(self):
        provider = self.provider_combo.currentText()
        base_url = self.base_url_input.text().strip()
        api_key = self.api_key_input.text().strip()

        if not api_key:
            QMessageBox.warning(self, "Missing API Key", "Please enter your API Key.")
            return
            
        tasks_to_check = self._required_tasks_for_provider(provider)
        for task_key in tasks_to_check:
            if not self.model_combos[task_key].currentText():
                QMessageBox.warning(self, "Missing Model Selection", f"Please select a model for task: {task_key}")
                return

        openai_key = api_key if provider == config.API_PROVIDER_OPENAI else self.settings_manager.get_openai_key()
        anthropic_key = api_key if provider == config.API_PROVIDER_ANTHROPIC else self.settings_manager.get_anthropic_key()
        gemini_key = api_key if provider == config.API_PROVIDER_GEMINI else self.settings_manager.get_gemini_key()
        
        models_dict = dict(self.settings_manager.get_api_models(provider))
        for task_key, combo in self.model_combos.items():
            if provider == config.API_PROVIDER_ANTHROPIC and task_key == config.TASK_IMAGE_GEN:
                continue
            if combo.currentText():
                models_dict[task_key] = combo.currentText()
                api_provider.set_task_model(task_key, combo.currentText())
                
        try:
            if provider == config.API_PROVIDER_OPENAI:
                api_provider.initialize_api(provider, api_key, base_url)
            else:
                api_provider.initialize_api(provider, api_key)
        except Exception as e:
            QMessageBox.critical(self, "Initialization Error", f"Failed to initialize the API provider:\n\n{str(e)}")
            return

        # Commit only after provider initialization succeeds. A rejected key or
        # endpoint must not overwrite the last known-good settings profile.
        self.settings_manager.set_api_settings(provider, base_url, openai_key, anthropic_key, gemini_key)
        self.settings_manager.set_api_models(models_dict, provider)

        os.environ['GRAPHLINK_API_PROVIDER'] = provider
        if provider == config.API_PROVIDER_OPENAI:
            os.environ['GRAPHLINK_OPENAI_API_KEY'] = api_key
            os.environ['GRAPHLINK_API_BASE'] = base_url
        elif provider == config.API_PROVIDER_ANTHROPIC:
            os.environ['GRAPHLINK_ANTHROPIC_API_KEY'] = api_key
        else:
            os.environ['GRAPHLINK_GEMINI_API_KEY'] = api_key
        
        QMessageBox.information(self, "Configuration Saved", f"API settings for {provider} have been saved.")

    def reset_settings(self):
        reply = QMessageBox.question(
            self, "Confirm Reset", 
            "Are you sure you want to clear all saved API keys and model configurations? This cannot be undone.", 
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.settings_manager.reset_api_settings()
            self.api_key_input.clear()
            self.base_url_input.setText("https://api.openai.com/v1")
            for task, combo in self.model_combos.items():
                combo.clear()
            self.provider_combo.setCurrentText(config.API_PROVIDER_OPENAI)
            self._on_provider_changed(config.API_PROVIDER_OPENAI)
            QMessageBox.information(self, "Reset Successful", "All API settings have been cleared.")


class IntegrationsSettingsWidget(QWidget):
    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        intro = QLabel(
            "Store optional external-service tokens used by specialized plugins.\n\n"
            "The Code Review plugin uses a GitHub personal access token to load your private repositories "
            "and your authenticated repo list. If you leave this empty, the plugin still works with public repositories."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #D4D4D4;")
        layout.addWidget(intro)

        layout.addWidget(QLabel("GitHub Personal Access Token:", styleSheet="color: #FFFFFF; font-weight: bold; margin-top: 8px;"))
        self.github_token_input = QLineEdit(
            self.settings_manager.get_github_token(),
            placeholderText="ghp_... or fine-grained token",
        )
        self.github_token_input.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.github_token_input)

        hint = QLabel(
            "Recommended scopes: read-only repository access. The token is stored locally in "
            "`~/.graphlink/session.dat` and is not required for public repositories."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #A5A5A5; font-size: 11px;")
        layout.addWidget(hint)

        layout.addStretch()

        button_row = QHBoxLayout()
        self.clear_button = QPushButton("Clear Token")
        self.clear_button.clicked.connect(self.clear_token)
        button_row.addWidget(self.clear_button)
        button_row.addStretch()

        self.save_button = QPushButton("Save Integrations")
        self.save_button.clicked.connect(self.save_settings)
        button_row.addWidget(self.save_button)
        layout.addLayout(button_row)

    def save_settings(self):
        token = self.github_token_input.text().strip()
        self.settings_manager.set_github_token(token)
        QMessageBox.information(self, "Integrations Saved", "GitHub integration settings have been saved.")

    def clear_token(self):
        self.github_token_input.clear()
        self.settings_manager.set_github_token("")
        QMessageBox.information(self, "Token Cleared", "The saved GitHub token has been removed.")


class AppearanceSettingsWidget(QWidget):
    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        
        theme_row = QHBoxLayout()
        theme_label = QLabel("Theme")
        theme_label.setStyleSheet("color: #8D8D8D; font-weight: bold;")
        theme_row.addWidget(theme_label)
        theme_row.addStretch()

        self.theme_combo = SettingsComboBox()
        self.theme_options = [
            ("dark", "Dark"),
            ("muted", "Muted"),
            ("mono", "Monochrome"),
        ]
        for theme_name, theme_label_text in self.theme_options:
            self.theme_combo.addItem(theme_label_text, theme_name)

        saved_theme = self.settings_manager.get_theme()
        theme_values = [item for item, _ in self.theme_options]
        if saved_theme in theme_values:
            self.theme_combo.setCurrentIndex(theme_values.index(saved_theme))
        else:
            self.theme_combo.setCurrentIndex(0)
        self.theme_combo.setToolTip("Pick a visual preset: dark is default, muted reduces saturation, mono is grayscale.")
        theme_row.addWidget(self.theme_combo)
        layout.addLayout(theme_row)

        theme_help = QLabel("Theme changes apply immediately when you click Apply.")
        theme_help.setWordWrap(True)
        theme_help.setStyleSheet("color: #A1A1A1; font-size: 11px;")
        layout.addWidget(theme_help)

        self.show_token_counter_checkbox = QCheckBox("Show Token Counter Overlay")
        self.show_token_counter_checkbox.setToolTip("Displays an overlay with token usage for the current session.")
        self.show_token_counter_checkbox.setChecked(self.settings_manager.get_show_token_counter())
        layout.addWidget(self.show_token_counter_checkbox)

        self.enable_system_prompt_checkbox = QCheckBox("Enable Assistant System Prompt")
        self.enable_system_prompt_checkbox.setToolTip(
            "When disabled, Graphlink sends your messages without a system-role prompt."
        )
        self.enable_system_prompt_checkbox.setChecked(self.settings_manager.get_enable_system_prompt())
        layout.addWidget(self.enable_system_prompt_checkbox)

        notification_divider = QFrame()
        notification_divider.setFrameShape(QFrame.Shape.HLine)
        notification_divider.setStyleSheet("background-color: rgba(255, 255, 255, 0.08); max-height: 1px; margin: 12px 0;")
        layout.addWidget(notification_divider)

        notification_title = QLabel("Notifications")
        notification_title.setStyleSheet("color: #FFFFFF; font-weight: bold; margin-top: 2px;")
        layout.addWidget(notification_title)

        notification_intro = QLabel(
            "Choose which banner flag types should appear. Turn off success banners if you want to hide the automatic chat-saved notice."
        )
        notification_intro.setWordWrap(True)
        notification_intro.setStyleSheet("color: #D4D4D4;")
        layout.addWidget(notification_intro)

        notification_preferences = self.settings_manager.get_notification_preferences()
        self.notification_type_checkboxes = {}
        for notification_type, label in (
            ("info", "Show Info Banners"),
            ("success", "Show Success Banners"),
            ("warning", "Show Warning Banners"),
            ("error", "Show Error Banners"),
        ):
            checkbox = QCheckBox(label)
            checkbox.setChecked(notification_preferences.get(notification_type, True))
            layout.addWidget(checkbox)
            self.notification_type_checkboxes[notification_type] = checkbox

        update_divider = QFrame()
        update_divider.setFrameShape(QFrame.Shape.HLine)
        update_divider.setStyleSheet("background-color: rgba(255, 255, 255, 0.08); max-height: 1px; margin: 12px 0;")
        layout.addWidget(update_divider)

        update_title = QLabel("Updates")
        update_title.setStyleSheet("color: #FFFFFF; font-weight: bold; margin-top: 2px;")
        layout.addWidget(update_title)

        update_intro = QLabel(
            f"Current build: {APP_VERSION}. Graphlink can check a GitHub version signal on startup or whenever you request it."
        )
        update_intro.setWordWrap(True)
        update_intro.setStyleSheet("color: #D4D4D4;")
        layout.addWidget(update_intro)

        self.enable_update_notifications_checkbox = QCheckBox("Enable Update Notifications on Startup")
        self.enable_update_notifications_checkbox.setToolTip(
            "When enabled, Graphlink checks the GitHub update signal after the app opens."
        )
        self.enable_update_notifications_checkbox.setChecked(
            self.settings_manager.get_update_notifications_enabled()
        )
        layout.addWidget(self.enable_update_notifications_checkbox)

        self.update_status_label = QLabel()
        self.update_status_label.setWordWrap(True)
        self.update_status_label.setMinimumHeight(44)
        layout.addWidget(self.update_status_label)

        self.update_timestamp_label = QLabel()
        self.update_timestamp_label.setWordWrap(True)
        self.update_timestamp_label.setStyleSheet("color: #8D8D8D; font-size: 11px;")
        layout.addWidget(self.update_timestamp_label)

        update_button_row = QHBoxLayout()
        self.check_updates_button = QPushButton("Check for Updates")
        self.check_updates_button.clicked.connect(self.check_for_updates)
        update_button_row.addWidget(self.check_updates_button)

        self.open_repo_button = QPushButton("Open Repository")
        self.open_repo_button.clicked.connect(lambda: webbrowser.open(UPDATE_REPOSITORY_URL))
        update_button_row.addWidget(self.open_repo_button)
        update_button_row.addStretch()
        layout.addLayout(update_button_row)
        
        layout.addStretch()

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.apply_button = QPushButton("Apply")
        self.apply_button.clicked.connect(self.apply_settings)
        button_layout.addWidget(self.apply_button)
        layout.addLayout(button_layout)
        self.refresh_update_status()

    def refresh_update_status(self):
        status_message = self.settings_manager.get_update_status_message()
        status_level = self.settings_manager.get_update_status_level()
        color_map = {
            "success": get_semantic_color("status_success").name(),
            "warning": get_semantic_color("status_warning").name(),
            "error": get_semantic_color("status_error").name(),
            "info": get_semantic_color("status_info").name(),
        }
        self.update_status_label.setText(status_message)
        self.update_status_label.setStyleSheet(
            f"color: {color_map.get(status_level, '#D4D4D4')}; font-size: 12px;"
        )

        checked_at = self.settings_manager.get_update_last_checked_at()
        latest_version = self.settings_manager.get_update_latest_version()
        details = []
        if latest_version:
            details.append(f"GitHub signal: {latest_version}")
        if checked_at:
            details.append(f"Last checked: {checked_at}")
        if not details:
            details.append("No update check has run yet.")
        self.update_timestamp_label.setText(" | ".join(details))

    def set_update_check_in_progress(self, in_progress: bool):
        self.check_updates_button.setEnabled(not in_progress)
        self.check_updates_button.setText("Checking..." if in_progress else "Check for Updates")

    def check_for_updates(self):
        main_window = self.window().parent()
        if main_window and hasattr(main_window, "check_for_updates"):
            self.set_update_check_in_progress(True)
            main_window.check_for_updates(manual=True, status_target=self)
            return
        self.set_update_check_in_progress(False)
        QMessageBox.warning(self, "Update Check Unavailable", "The main window is not available for update checks.")

    def apply_settings(self):
        theme_name = self.theme_combo.currentData() or "dark"
        if theme_name not in {name for name, _ in self.theme_options}:
            theme_name = "dark"
        self.settings_manager.set_theme(theme_name)
        
        self.settings_manager.set_show_token_counter(self.show_token_counter_checkbox.isChecked())
        self.settings_manager.set_enable_system_prompt(self.enable_system_prompt_checkbox.isChecked())
        self.settings_manager.set_notification_preferences({
            notification_type: checkbox.isChecked()
            for notification_type, checkbox in self.notification_type_checkboxes.items()
        })
        self.settings_manager.set_update_notifications_enabled(
            self.enable_update_notifications_checkbox.isChecked()
        )
        self.refresh_update_status()

        app = QApplication.instance()
        apply_theme(app, theme_name)
        
        main_window = self.window().parent()
        if main_window and hasattr(main_window, 'on_settings_changed'):
            main_window.on_settings_changed()

        QMessageBox.information(self, "Settings Applied", "General settings have been saved.")


class SettingsCategoryButton(QPushButton):
    def __init__(self, section_name, icon_name, parent=None):
        super().__init__(section_name, parent)
        self.section_name = section_name
        self.setObjectName("settingsCategoryButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)
        self.setMinimumHeight(42)
        self.setIcon(qta.icon(icon_name, color="#E0E0E0"))
        self.setIconSize(QSize(14, 14))


class SettingsDialog(QFrame):
    SECTION_DEFS = [
        ("General", "fa5s.sliders-h", "General app preferences, visuals, and assistant defaults."),
        (config.MODE_OLLAMA_LOCAL, "fa5s.microchip", "Choose your local chat model, naming model, and reasoning mode."),
        (config.MODE_LLAMACPP_LOCAL, "fa5s.hdd", "Load a local GGUF through llama-cpp-python and tune its runtime."),
        (config.MODE_API_ENDPOINT, "fa5s.cloud", "Configure provider, keys, task models, and chat naming."),
        ("Integrations", "fa5s.plug", "Store optional tokens used by plugins such as GitHub-backed code review."),
    ]

    def __init__(self, settings_manager, parent=None):
        # Use a persistent tool window instead of a popup so the settings panel
        # stays open during scans, message boxes, and incidental outside clicks.
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.settings_manager = settings_manager
        self.category_buttons = {}
        self.current_section_name = None
        self.setObjectName("settingsFlyoutPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(820, 560)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 10)
        shadow.setColor(Qt.GlobalColor.black)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(12, 12, 12, 14)
        outer_layout.setSpacing(0)

        self.shell = QFrame()
        self.shell.setObjectName("settingsFlyoutShell")
        self.shell.setGraphicsEffect(shadow)
        outer_layout.addWidget(self.shell)

        root_layout = QHBoxLayout(self.shell)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(0)

        rail = QWidget()
        rail.setObjectName("settingsCategoryRail")
        rail.setFixedWidth(190)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(12, 12, 12, 12)
        rail_layout.setSpacing(8)

        eyebrow = QLabel("Settings")
        eyebrow.setObjectName("settingsSectionLabel")
        rail_layout.addWidget(eyebrow)

        rail_intro = QLabel("Pick a section instead of digging through one giant wall of controls.")
        rail_intro.setObjectName("settingsRailIntro")
        rail_intro.setWordWrap(True)
        rail_layout.addWidget(rail_intro)

        self.category_button_column = QVBoxLayout()
        self.category_button_column.setContentsMargins(0, 6, 0, 0)
        self.category_button_column.setSpacing(6)
        rail_layout.addLayout(self.category_button_column)
        rail_layout.addStretch(1)

        divider = QFrame()
        divider.setObjectName("settingsFlyoutDivider")
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFrameShadow(QFrame.Shadow.Plain)
        divider.setLineWidth(1)

        content_panel = QWidget()
        content_panel.setObjectName("settingsPane")
        content_layout = QVBoxLayout(content_panel)
        content_layout.setContentsMargins(14, 12, 14, 12)
        content_layout.setSpacing(10)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(10)

        self.section_icon = QLabel()
        self.section_icon.setObjectName("settingsCategoryIcon")
        self.section_icon.setFixedSize(28, 28)
        self.section_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_row.addWidget(self.section_icon, 0, Qt.AlignmentFlag.AlignTop)

        header_text_column = QVBoxLayout()
        header_text_column.setContentsMargins(0, 0, 0, 0)
        header_text_column.setSpacing(2)

        self.header_title = QLabel("Settings")
        self.header_title.setObjectName("settingsPaneTitle")
        header_text_column.addWidget(self.header_title)

        self.header_body = QLabel("")
        self.header_body.setObjectName("settingsPaneMeta")
        self.header_body.setWordWrap(True)
        header_text_column.addWidget(self.header_body)
        header_row.addLayout(header_text_column, 1)

        self.close_button = QPushButton("Close")
        self.close_button.setObjectName("settingsCloseButton")
        self.close_button.clicked.connect(self.close)
        header_row.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        content_layout.addLayout(header_row)

        self.stack = QStackedWidget()
        self.stack.setObjectName("settingsStack")
        content_layout.addWidget(self.stack, 1)

        self.appearance_tab = AppearanceSettingsWidget(self.settings_manager)
        self.ollama_tab = OllamaSettingsWidget(self.settings_manager)
        self.llama_cpp_tab = LlamaCppSettingsWidget(self.settings_manager)
        self.api_tab = ApiSettingsWidget(self.settings_manager)
        self.integrations_tab = IntegrationsSettingsWidget(self.settings_manager)

        self.section_widgets = {
            "General": self.appearance_tab,
            config.MODE_OLLAMA_LOCAL: self.ollama_tab,
            config.MODE_LLAMACPP_LOCAL: self.llama_cpp_tab,
            config.MODE_API_ENDPOINT: self.api_tab,
            "Integrations": self.integrations_tab,
        }
        self.section_pages = {}

        for section_name, _, _ in self.SECTION_DEFS:
            page = self._build_scroll_page(self.section_widgets[section_name])
            self.section_pages[section_name] = page
            self.stack.addWidget(page)

        root_layout.addWidget(rail)
        root_layout.addWidget(divider)
        root_layout.addWidget(content_panel, 1)

        self._build_category_buttons()
        self._apply_panel_styles()
        self.set_current_section("General")

    def _build_scroll_page(self, content_widget):
        scroll_area = QScrollArea()
        scroll_area.setObjectName("settingsScrollArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        container = QWidget()
        container.setObjectName("settingsScrollContent")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 2, 0)
        layout.setSpacing(0)
        layout.addWidget(content_widget)
        layout.addStretch(1)
        scroll_area.setWidget(container)
        return scroll_area

    def _build_category_buttons(self):
        while self.category_button_column.count():
            item = self.category_button_column.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self.category_buttons.clear()
        for section_name, icon_name, _ in self.SECTION_DEFS:
            button = SettingsCategoryButton(section_name, icon_name, self)
            button.clicked.connect(lambda checked=False, name=section_name: self.set_current_section(name))
            self.category_button_column.addWidget(button)
            self.category_buttons[section_name] = button

        self.category_button_column.addStretch(1)

    def _apply_panel_styles(self):
        palette = get_current_palette()
        accent = palette.SELECTION.name()
        panel_gray = "rgba(42, 42, 42, 248)"
        line_gray = "rgba(255, 255, 255, 0.08)"
        muted_text = "#8D8D8D"
        soft_text = "#C3C3C3"
        hover_gray = "rgba(255, 255, 255, 0.055)"
        badge_gray = "rgba(255, 255, 255, 0.025)"

        self.setStyleSheet(f"""
            QFrame#settingsFlyoutPanel {{
                background-color: transparent;
                border: none;
            }}
            QFrame#settingsFlyoutShell {{
                background-color: {panel_gray};
                border: 1px solid {line_gray};
                border-radius: 14px;
            }}
            /* Keep text rows explicitly transparent. The app theme applies a dark
               background to every QWidget subclass, so using shorthand `background`
               or unsupported selectors here brings the dark bars back under titles,
               labels, and checkbox rows inside the flyout. */
            QFrame#settingsFlyoutShell QLabel,
            QFrame#settingsFlyoutShell QCheckBox,
            QFrame#settingsFlyoutShell QRadioButton,
            QWidget#settingsCategoryRail,
            QWidget#settingsPane,
            QWidget#settingsScrollContent,
            QScrollArea#settingsScrollArea > QWidget > QWidget {{
                background-color: transparent;
            }}
            QFrame#settingsFlyoutDivider {{
                background-color: rgba(255, 255, 255, 0.06);
                border: none;
                margin-top: 10px;
                margin-bottom: 10px;
            }}
            QLabel#settingsSectionLabel {{
                color: {muted_text};
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.14em;
                text-transform: uppercase;
                background-color: transparent;
            }}
            QLabel#settingsRailIntro {{
                color: {muted_text};
                font-size: 11px;
                line-height: 1.35em;
                background-color: transparent;
                padding: 0 2px 4px 2px;
            }}
            QPushButton#settingsCategoryButton {{
                background-color: transparent;
                color: {soft_text};
                border: 1px solid transparent;
                border-radius: 8px;
                padding: 10px 12px;
                text-align: left;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton#settingsCategoryButton:hover {{
                background-color: {hover_gray};
                border-color: rgba(255, 255, 255, 0.05);
                color: #FFFFFF;
            }}
            QPushButton#settingsCategoryButton:checked {{
                background-color: rgba(255, 255, 255, 0.06);
                border-color: rgba(255, 255, 255, 0.08);
                color: #FFFFFF;
            }}
            QLabel#settingsCategoryIcon {{
                background-color: {badge_gray};
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 14px;
            }}
            QLabel#settingsPaneTitle {{
                color: #F5F5F5;
                font-size: 15px;
                font-weight: 700;
                background-color: transparent;
            }}
            QLabel#settingsPaneMeta {{
                color: {muted_text};
                font-size: 11px;
                background-color: transparent;
            }}
            QScrollArea#settingsScrollArea, QWidget#settingsScrollContent, QStackedWidget#settingsStack {{
                background-color: transparent;
                border: none;
            }}
            QScrollArea#settingsScrollArea > QWidget > QWidget {{
                background-color: transparent;
            }}
            QComboBox#settingsComboBox {{
                background-color: #2D2D2D;
                border: 1px solid #3F3F3F;
                color: #FFFFFF;
                padding: 5px;
                border-radius: 4px;
                font-family: 'Segoe UI', sans-serif;
                font-size: 12px;
            }}
            QComboBox#settingsComboBox:hover {{
                border-color: #4A4A4A;
            }}
            QComboBox#settingsComboBox:focus {{
                border-color: {accent};
            }}
            QComboBox#settingsComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 20px;
                border-left-width: 1px;
                border-left-color: #3F3F3F;
                border-left-style: solid;
                border-top-right-radius: 3px;
                border-bottom-right-radius: 3px;
                background-color: transparent;
            }}
            QComboBox#settingsComboBox::down-arrow {{
                image: url({asset_url('down_arrow.png')});
                width: 10px;
                height: 10px;
            }}
            QComboBox#settingsComboBox QLineEdit {{
                background-color: transparent;
                color: #FFFFFF;
                border: none;
                padding: 0;
                selection-background-color: #494949;
                selection-color: #FFFFFF;
            }}
            QFrame#settingsFlyoutShell QMenu {{
                background-color: #2D2D2D;
                border: 1px solid #3F3F3F;
                border-radius: 4px;
                padding: 4px;
            }}
            QFrame#settingsFlyoutShell QMenu::item {{
                background-color: transparent;
                padding: 8px 24px 8px 24px;
                border-radius: 4px;
                color: #FFFFFF;
                font-family: 'Segoe UI', sans-serif;
                font-size: 12px;
            }}
            QFrame#settingsFlyoutShell QMenu::item:selected {{
                background-color: {accent};
                color: #FFFFFF;
            }}
            QFrame#settingsFlyoutShell QMenu::item:disabled {{
                color: #777777;
            }}
            QFrame#settingsFlyoutShell QMenu::separator {{
                height: 1px;
                background-color: #3F3F3F;
                margin: 4px 0px;
            }}
            QPushButton#settingsCloseButton {{
                background-color: rgba(255, 255, 255, 0.04);
                color: #F5F5F5;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                padding: 8px 14px;
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton#settingsCloseButton:hover {{
                background-color: rgba(255, 255, 255, 0.08);
            }}
        """)

        self._accent_color = accent
        for combo in self.findChildren(SettingsComboBox):
            combo.apply_popup_style(accent)

    def set_current_section(self, section_name):
        if section_name not in self.section_pages:
            return

        self.current_section_name = section_name
        for name, button in self.category_buttons.items():
            button.setChecked(name == section_name)

        icon_name = next(icon for name, icon, _ in self.SECTION_DEFS if name == section_name)
        description = next(text for name, _, text in self.SECTION_DEFS if name == section_name)

        self.header_title.setText(section_name)
        self.header_body.setText(description)
        self.section_icon.setPixmap(qta.icon(icon_name, color=self._accent_color).pixmap(14, 14))
        self.stack.setCurrentWidget(self.section_pages[section_name])

    def set_current_section_by_mode(self, mode_text):
        if mode_text == config.MODE_OLLAMA_LOCAL:
            self.set_current_section(config.MODE_OLLAMA_LOCAL)
        elif mode_text == config.MODE_LLAMACPP_LOCAL:
            self.set_current_section(config.MODE_LLAMACPP_LOCAL)
        elif mode_text == config.MODE_API_ENDPOINT:
            self.set_current_section(config.MODE_API_ENDPOINT)
        else:
            self.set_current_section("General")

    def show_for_anchor(self, anchor_widget):
        self._apply_panel_styles()
        self.resize(820, 560)

        target_global = anchor_widget.mapToGlobal(QPoint(anchor_widget.width() - self.width(), anchor_widget.height() + 6))
        screen = QGuiApplication.screenAt(target_global) or QGuiApplication.primaryScreen()
        available_geometry = screen.availableGeometry() if screen else None

        x = target_global.x()
        y = target_global.y()

        if available_geometry is not None:
            max_x = available_geometry.right() - self.width() - 12
            max_y = available_geometry.bottom() - self.height() - 12
            x = max(available_geometry.left() + 12, min(x, max_x))
            y = max(available_geometry.top() + 12, min(y, max_y))

        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    def on_theme_changed(self):
        self._apply_panel_styles()

    def _iter_running_workers(self):
        for label, worker in (
            ("Ollama model validation", getattr(self.ollama_tab, "worker_thread", None)),
            ("Ollama model scan", getattr(self.ollama_tab, "scan_worker", None)),
            ("Llama.cpp model scan", getattr(self.llama_cpp_tab, "scan_worker", None)),
        ):
            if worker is not None:
                yield label, worker

    def _request_worker_shutdown(self, worker):
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

    def closeEvent(self, event):
        still_running = []
        for label, worker in self._iter_running_workers():
            if not hasattr(worker, "isRunning") or not worker.isRunning():
                continue

            self._request_worker_shutdown(worker)
            if not worker.wait(3000):
                still_running.append(label)

        if still_running:
            worker_list = "\n".join(f"- {label}" for label in still_running)
            QMessageBox.information(
                self,
                "Background Work Still Running",
                "Please wait for these settings tasks to finish before closing:\n\n"
                f"{worker_list}",
            )
            event.ignore()
            return

        super().closeEvent(event)
