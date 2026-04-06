import os
import webbrowser
import qtawesome as qta
from PySide6.QtCore import QPoint, QSize, Qt, QThread, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFormLayout,
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QRadioButton,
    QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget
)

import api_provider
import graphite_config as config
from graphite_agents import ModelPullWorkerThread
from graphite_styles import THEMES
from graphite_config import apply_theme, get_current_palette, set_current_model
from graphite_update import APP_VERSION, UPDATE_REPOSITORY_URL


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
                background-color: #2d2d2d;
                border: 1px solid #3f3f3f;
                border-radius: 9px;
            }}
            QListWidget#settingsComboPopupList {{
                background-color: transparent;
                color: #ffffff;
                border: none;
                outline: none;
                padding: 2px;
            }}
            QListWidget#settingsComboPopupList::item {{
                background-color: transparent;
                color: #ffffff;
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
                color: #ffffff;
            }}
            QListWidget#settingsComboPopupList::item:selected:hover {{
                background-color: {accent_color};
                color: #ffffff;
            }}
        """)

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
                self.list_widget.scrollToItem(current_item, QAbstractItemView.ScrollHint.PositionAtCenter)
        else:
            self.list_widget.clearSelection()

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
        self.list_widget.setFocus()

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


class OllamaSettingsWidget(QWidget):
    DEFAULT_MODELS = [
        'qwen2.5:7b-instruct', 'qwen3:8b', 'qwen3:14b', 'deepseek-r1:14b', 'phi3:14b', 'mistral:7b',
        'gpt-oss:20b', 'qwen3-vl:8b', 'deepseek-coder:6.7b', 'gemma3:4b', 'gemma3:12b'
    ]

    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.worker_thread = None
        self.scan_worker = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)

        info_label = QLabel("Configure the local chat model, the model used to name new chats, and the reasoning mode when using the Ollama provider.")
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #d4d4d4; margin-bottom: 15px;")
        layout.addWidget(info_label)
        
        form_layout = QFormLayout()
        form_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        reasoning_mode_label = QLabel("Reasoning Mode:")
        reasoning_mode_label.setStyleSheet("color: #ffffff; font-weight: bold;")
        
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
        saved_title_model = self.settings_manager.get_ollama_title_model()
        self.models = self._build_model_cache(saved_model, saved_title_model)

        self.current_model_label = QLabel(f"<b>{saved_model}</b>")
        self.current_model_label.setStyleSheet("color: #2ecc71;")
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
        self.scan_summary_label.setStyleSheet("color: #9fa6ad;")
        form_layout.addRow("", self.scan_summary_label)

        self.model_combo = SettingsComboBox()
        self.model_combo.addItems([""] + self.models)
        self.model_combo.currentTextChanged.connect(self.on_combo_change)
        form_layout.addRow("Scanned Model:", self.model_combo)

        self.model_input = QLineEdit()
        self.model_input.setPlaceholderText("e.g., llama3:latest")
        self.model_input.textChanged.connect(self.on_text_change)
        form_layout.addRow("Custom Model Name:", self.model_input)

        self.title_model_combo = SettingsComboBox()
        self.title_model_combo.setEditable(True)
        self.title_model_combo.addItems([""] + self.models)
        self.title_model_combo.setCurrentText(saved_title_model)
        form_layout.addRow("Chat Naming Model:", self.title_model_combo)
        
        layout.addLayout(form_layout)

        self.model_input.setText(saved_model)

        naming_help = QLabel("Used to name new chats. It starts with the active chat model, and you can override it independently.")
        naming_help.setWordWrap(True)
        naming_help.setStyleSheet("color: #9fa6ad; margin-top: 2px;")
        layout.addWidget(naming_help)

        scan_help = QLabel("System scan checks the local Ollama install/cache locations and stores the discovered list until you rescan. Folder scan lets you point directly at a custom models folder.")
        scan_help.setWordWrap(True)
        scan_help.setStyleSheet("color: #9fa6ad; margin-top: 4px;")
        layout.addWidget(scan_help)

        self.status_label = QLabel("Enter a model name to validate and set it.")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("statusLabel")
        self.status_label.setStyleSheet("color: #e67e22; min-height: 40px;")
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

    def on_theme_changed(self):
        palette = get_current_palette()
        selection_color = palette.SELECTION.name()
        selection_border = palette.SELECTION.darker(110).name()

        self.setStyleSheet(f"""
            QRadioButton {{
                color: #cccccc;
                font-size: 11px;
            }}
            QRadioButton::indicator {{
                width: 16px;
                height: 16px;
            }}
            QRadioButton::indicator:unchecked {{
                border: 1px solid #555;
                background-color: #3f3f3f;
                border-radius: 4px;
            }}
            QRadioButton::indicator:checked {{
                background-color: {selection_color};
                border: 1px solid {selection_border};
                image: url(C:/Users/Admin/source/repos/graphite_app/assets/check.png);
                border-radius: 4px;
            }}
        """)

    def _build_model_cache(self, *extra_models):
        cached_models = self.settings_manager.get_ollama_scanned_models()
        has_saved_scan = bool(
            self.settings_manager.get_ollama_model_scan_mode()
            or self.settings_manager.get_ollama_model_scan_path()
            or self.settings_manager.get_ollama_model_scan_locations()
        )
        base_models = cached_models if has_saved_scan else self.DEFAULT_MODELS
        combined_models = {
            str(model).strip()
            for model in [*base_models, *extra_models]
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
        current_title_text = self.title_model_combo.currentText().strip()

        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems([""] + self.models)
        if current_model_text in self.models:
            self.model_combo.setCurrentText(current_model_text)
        else:
            self.model_combo.setCurrentIndex(0)
        self.model_combo.blockSignals(False)

        self.title_model_combo.blockSignals(True)
        self.title_model_combo.clear()
        self.title_model_combo.addItems([""] + self.models)
        self.title_model_combo.setCurrentText(current_title_text)
        self.title_model_combo.blockSignals(False)

        self.scan_summary_label.setText(self._get_scan_summary_text())

    def _set_scan_buttons_enabled(self, enabled):
        self.system_scan_button.setEnabled(enabled)
        self.folder_scan_button.setEnabled(enabled)

    def scan_system_for_models(self):
        self._start_scan_worker()

    def scan_selected_folder(self):
        initial_directory = self.settings_manager.get_ollama_model_scan_path() or str(os.path.expanduser("~"))
        selected_directory = QFileDialog.getExistingDirectory(self, "Select Ollama Folder to Scan", initial_directory)
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
        self.status_label.setStyleSheet("color: #3498db; min-height: 40px;")

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
        self.models = self._build_model_cache(
            self.model_input.text().strip(),
            self.title_model_combo.currentText().strip(),
        )
        self._refresh_model_combos()
        self._set_scan_buttons_enabled(True)

        if models:
            self.status_label.setText(f"Found {len(models)} Ollama model(s). Saved this list for reuse until the next scan.")
            self.status_label.setStyleSheet("color: #2ecc71; min-height: 40px;")
        else:
            self.status_label.setText("Scan finished, but no Ollama models were found in the selected locations.")
            self.status_label.setStyleSheet("color: #e67e22; min-height: 40px;")

        self.scan_worker = None

    def handle_scan_error(self, error_message):
        self._set_scan_buttons_enabled(True)
        self.status_label.setText(f"Scan failed: {error_message}")
        self.status_label.setStyleSheet("color: #e74c3c; min-height: 40px;")
        self.scan_worker = None

    def save_settings(self):
        model_name = self.model_input.text().strip()
        if not model_name:
            QMessageBox.warning(self, "Warning", "Model name cannot be empty.")
            return
        
        reasoning_mode = "Thinking" if self.thinking_radio.isChecked() else "Quick"
        title_model_name = self.title_model_combo.currentText().strip() or model_name
        
        self.settings_manager.set_ollama_chat_model(model_name)
        self.settings_manager.set_ollama_title_model(title_model_name)
        self.settings_manager.set_ollama_reasoning_mode(reasoning_mode)
        set_current_model(model_name)

        main_window = self.window().parent()
        if main_window and hasattr(main_window, 'reinitialize_agent'):
            main_window.reinitialize_agent()

        self.current_model_label.setText(f"<b>{model_name}</b>")
        QMessageBox.information(self, "Saved", "Ollama settings have been saved and applied for the current session.")

    def on_combo_change(self, text):
        if not text: return
        self.model_input.textChanged.disconnect(self.on_text_change)
        self.model_input.setText(text)
        self.model_input.textChanged.connect(self.on_text_change)

    def on_text_change(self, text):
        self.model_combo.currentTextChanged.disconnect(self.on_combo_change)
        if text in self.models:
            self.model_combo.setCurrentText(text)
        else:
            self.model_combo.setCurrentIndex(0)
        self.model_combo.currentTextChanged.connect(self.on_combo_change)

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
        self.status_label.setStyleSheet("color: #3498db;")

    def handle_worker_finished(self, message, model_name):
        self.current_model_label.setText(f"<b>{model_name}</b>")
        self.status_label.setText(message)
        self.status_label.setStyleSheet("color: #2ecc71;")
        self.reset_button()
        QMessageBox.information(self, "Success", message)

    def handle_worker_error(self, error_message):
        self.status_label.setText(f"Error: {error_message}")
        self.status_label.setStyleSheet("color: #e74c3c;")
        self.reset_button()
        QMessageBox.warning(self, "Model Error", error_message)

    def reset_button(self):
        self.validate_button.setEnabled(True)
        self.validate_button.setText("Validate and Pull Model")


class ApiSettingsWidget(QWidget):
    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        
        layout.addWidget(QLabel("API Provider:", styleSheet="color: #ffffff; font-weight: bold;"))
        self.provider_combo = SettingsComboBox()
        self.provider_combo.addItems([config.API_PROVIDER_OPENAI, config.API_PROVIDER_GEMINI])
        self.provider_combo.currentTextChanged.connect(self._on_provider_changed)
        layout.addWidget(self.provider_combo)

        info = QLabel(
            "Configure your API endpoint.\n"
            "OpenAI-Compatible works with: OpenAI, LiteLLM, Anthropic, OpenRouter, etc.\n\n"
            "Choose different models for different tasks, including the chat naming model."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #d4d4d4; margin-bottom: 15px; margin-top: 10px;")
        layout.addWidget(info)

        self.base_url_label = QLabel("Base URL:")
        self.base_url_label.setStyleSheet("color: #ffffff; font-weight: bold;")
        layout.addWidget(self.base_url_label)
        self.base_url_input = QLineEdit(self.settings_manager.get_api_base_url(), placeholderText="https://api.openai.com/v1")
        layout.addWidget(self.base_url_input)

        layout.addWidget(QLabel("API Key:", styleSheet="color: #ffffff; font-weight: bold; margin-top: 10px;"))
        self.api_key_input = QLineEdit(echoMode=QLineEdit.Password, placeholderText="Enter your API key...")
        layout.addWidget(self.api_key_input)

        self.load_btn = QPushButton("Load Models from Endpoint")
        self.load_btn.clicked.connect(self.load_models_from_endpoint)
        layout.addWidget(self.load_btn)

        layout.addWidget(QLabel("Model Selection (per task):", styleSheet="color: #ffffff; font-weight: bold; margin-top: 15px;"))

        self.model_combos = {}
        layout.addWidget(QLabel("Chat Naming / Session Title:", styleSheet="color: #d4d4d4; margin-top: 8px;"))
        self.title_combo = SettingsComboBox(placeholder_text="Select model...")
        self.title_combo.setEditable(True)
        self.model_combos[config.TASK_TITLE] = self.title_combo
        layout.addWidget(self.title_combo)
        layout.addWidget(QLabel("Used when a new chat is named in API Endpoint mode.", styleSheet="color: #9fa6ad; margin-top: 2px;"))
        
        layout.addWidget(QLabel("Chat, Explain, Takeaways (main model):", styleSheet="color: #d4d4d4; margin-top: 8px;"))
        self.chat_combo = SettingsComboBox(placeholder_text="Select model...")
        self.chat_combo.setEditable(True)
        self.model_combos[config.TASK_CHAT] = self.chat_combo
        layout.addWidget(self.chat_combo)

        layout.addWidget(QLabel("Chart Generation (code-capable model):", styleSheet="color: #d4d4d4; margin-top: 8px;"))
        self.chart_combo = SettingsComboBox(placeholder_text="Select model...")
        self.chart_combo.setEditable(True)
        self.model_combos[config.TASK_CHART] = self.chart_combo
        layout.addWidget(self.chart_combo)

        layout.addWidget(QLabel("Image Generation:", styleSheet="color: #d4d4d4; margin-top: 8px;"))
        self.image_combo = SettingsComboBox(placeholder_text="Select image model...")
        self.image_combo.setEditable(True)
        self.model_combos[config.TASK_IMAGE_GEN] = self.image_combo
        layout.addWidget(self.image_combo)
        
        layout.addWidget(QLabel("Web Content Validation (fastest model, Gemini-only):", styleSheet="color: #d4d4d4; margin-top: 8px;"))
        self.web_validate_combo = SettingsComboBox(placeholder_text="Default: gemini-3.1-flash-lite-preview")
        self.web_validate_combo.setEditable(True)
        self.model_combos[config.TASK_WEB_VALIDATE] = self.web_validate_combo
        layout.addWidget(self.web_validate_combo)

        layout.addWidget(QLabel("Web Content Summarization:", styleSheet="color: #d4d4d4; margin-top: 8px;"))
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
        saved_models = self.settings_manager.get_api_models()
        for task, combo in self.model_combos.items():
            saved_model = saved_models.get(task, "")
            if saved_model:
                if combo.findText(saved_model) == -1:
                    combo.addItem(saved_model)
                combo.setCurrentText(saved_model)

    def _populate_models(self, models):
        for task, combo in self.model_combos.items():
            if task == config.TASK_WEB_VALIDATE and self.provider_combo.currentText() == config.API_PROVIDER_GEMINI:
                continue
            
            current = combo.currentText()
            combo.clear()
            combo.addItems(models)
            if current and combo.findText(current) != -1:
                combo.setCurrentText(current)
    
    def _on_provider_changed(self, provider_name):
        is_openai = (provider_name == config.API_PROVIDER_OPENAI)

        for task, combo in self.model_combos.items():
            combo.setEditable(is_openai or task == config.TASK_WEB_VALIDATE)
        
        self.base_url_label.setVisible(is_openai)
        self.base_url_input.setVisible(is_openai)
        self.load_btn.setVisible(is_openai)
        
        self.web_validate_combo.clear()
        self.web_validate_combo.addItems(api_provider.GEMINI_MODELS_STATIC)
        self.web_validate_combo.setEnabled(provider_name == config.API_PROVIDER_GEMINI)
        
        default_idx = self.web_validate_combo.findText("gemini-3.1-flash-lite-preview")
        if default_idx >= 0:
            self.web_validate_combo.setCurrentIndex(default_idx)

        self.image_combo.clear()
        if provider_name == config.API_PROVIDER_GEMINI:
            self.image_combo.addItems(api_provider.GEMINI_IMAGE_MODELS_STATIC)
            image_default_idx = self.image_combo.findText("gemini-2.5-flash-image")
            if image_default_idx >= 0:
                self.image_combo.setCurrentIndex(image_default_idx)

        if is_openai:
            self.api_key_input.setPlaceholderText("Enter your OpenAI-compatible API key...")
            self.api_key_input.setText(self.settings_manager.get_openai_key())
            self._populate_models([]) 
        else:
            self.api_key_input.setPlaceholderText("Enter your Google Gemini API key...")
            self.api_key_input.setText(self.settings_manager.get_gemini_key())
            for task, combo in self.model_combos.items():
                if task in (config.TASK_WEB_VALIDATE, config.TASK_IMAGE_GEN):
                    continue
                combo.clear()
                combo.addItems(api_provider.GEMINI_MODELS_STATIC)
                    
        self.restore_saved_models()

    def load_models_from_endpoint(self):
        provider = self.provider_combo.currentText()
        base_url = self.base_url_input.text().strip()
        api_key = self.api_key_input.text().strip()

        if provider == config.API_PROVIDER_OPENAI and not base_url:
            QMessageBox.warning(self, "Missing Information", "Please enter the Base URL for the OpenAI-compatible provider.")
            return
        if not api_key:
            QMessageBox.warning(self, "Missing Information", "Please enter the API Key.")
            return

        try:
            api_provider.initialize_api(provider, api_key, base_url if provider == config.API_PROVIDER_OPENAI else None)
            models = api_provider.get_available_models()
            
            if provider == config.API_PROVIDER_OPENAI:
                 self._populate_models(models)
            
            self.restore_saved_models()
            QMessageBox.information(self, "Models Loaded", f"Successfully loaded {len(models)} models!")
        except Exception as e:
            QMessageBox.critical(self, "Failed to Load Models", f"Could not fetch models from API:\n\n{str(e)}")

    def save_settings(self):
        provider = self.provider_combo.currentText()
        base_url = self.base_url_input.text().strip()
        api_key = self.api_key_input.text().strip()

        if not api_key:
            QMessageBox.warning(self, "Missing API Key", "Please enter your API Key.")
            return
            
        tasks_to_check =[t for t in self.model_combos.keys() if t != config.TASK_WEB_VALIDATE or provider == config.API_PROVIDER_GEMINI]
        for task_key in tasks_to_check:
            if not self.model_combos[task_key].currentText():
                QMessageBox.warning(self, "Missing Model Selection", f"Please select a model for task: {task_key}")
                return

        openai_key = api_key if provider == config.API_PROVIDER_OPENAI else self.settings_manager.get_openai_key()
        gemini_key = api_key if provider == config.API_PROVIDER_GEMINI else self.settings_manager.get_gemini_key()
        
        self.settings_manager.set_api_settings(provider, base_url, openai_key, gemini_key)

        models_dict = {}
        for task_key, combo in self.model_combos.items():
            if combo.currentText() or (task_key == config.TASK_WEB_VALIDATE and provider == config.API_PROVIDER_GEMINI):
                models_dict[task_key] = combo.currentText()
                api_provider.set_task_model(task_key, combo.currentText())
                
        self.settings_manager.set_api_models(models_dict)

        os.environ['GRAPHITE_API_PROVIDER'] = provider
        if provider == config.API_PROVIDER_OPENAI:
            os.environ['GRAPHITE_OPENAI_API_KEY'] = api_key
            os.environ['GRAPHITE_API_BASE'] = base_url
        else:
             os.environ['GRAPHITE_GEMINI_API_KEY'] = api_key

        try:
            if provider == config.API_PROVIDER_OPENAI:
                api_provider.initialize_api(provider, api_key, base_url)
            else:
                api_provider.initialize_api(provider, api_key)
        except Exception as e:
            QMessageBox.critical(self, "Initialization Error", f"Failed to initialize the API provider:\n\n{str(e)}")
            return
        
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
                if task != config.TASK_WEB_VALIDATE:
                    combo.clear()
            self.provider_combo.setCurrentText("OpenAI-Compatible")
            self._on_provider_changed("OpenAI-Compatible")
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
        intro.setStyleSheet("color: #d4d4d4;")
        layout.addWidget(intro)

        layout.addWidget(QLabel("GitHub Personal Access Token:", styleSheet="color: #ffffff; font-weight: bold; margin-top: 8px;"))
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
        hint.setStyleSheet("color: #9fa6ad; font-size: 11px;")
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
        
        self.desaturate_checkbox = QCheckBox("Enable Monochromatic Mode: Graphlink needs to be reset for this to be applied")
        self.desaturate_checkbox.setToolTip("Reduces color saturation for a grayscale-like appearance. The app needs reset for this to go into full effect.")
        self.desaturate_checkbox.setChecked(self.settings_manager.get_theme() == "mono")
        layout.addWidget(self.desaturate_checkbox)

        self.show_welcome_checkbox = QCheckBox("Show Welcome Screen on Startup")
        self.show_welcome_checkbox.setToolTip("If unchecked, the application will open directly to your last session.")
        self.show_welcome_checkbox.setChecked(self.settings_manager.get_show_welcome_screen())
        layout.addWidget(self.show_welcome_checkbox)

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
        notification_title.setStyleSheet("color: #ffffff; font-weight: bold; margin-top: 2px;")
        layout.addWidget(notification_title)

        notification_intro = QLabel(
            "Choose which banner flag types should appear. Turn off success banners if you want to hide the automatic chat-saved notice."
        )
        notification_intro.setWordWrap(True)
        notification_intro.setStyleSheet("color: #d4d4d4;")
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
        update_title.setStyleSheet("color: #ffffff; font-weight: bold; margin-top: 2px;")
        layout.addWidget(update_title)

        update_intro = QLabel(
            f"Current build: {APP_VERSION}. Graphlink can check a GitHub version signal on startup or whenever you request it."
        )
        update_intro.setWordWrap(True)
        update_intro.setStyleSheet("color: #d4d4d4;")
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
        self.update_timestamp_label.setStyleSheet("color: #8d8d8d; font-size: 11px;")
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
            "success": "#2ecc71",
            "warning": "#f5c04f",
            "error": "#e74c3c",
            "info": "#8fb7ff",
        }
        self.update_status_label.setText(status_message)
        self.update_status_label.setStyleSheet(
            f"color: {color_map.get(status_level, '#d4d4d4')}; font-size: 12px;"
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
        theme_name = "mono" if self.desaturate_checkbox.isChecked() else "dark"
        self.settings_manager.set_theme(theme_name)
        
        self.settings_manager.set_show_welcome_screen(self.show_welcome_checkbox.isChecked())
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
        self.setIcon(qta.icon(icon_name, color="#d9e1ea"))
        self.setIconSize(QSize(14, 14))


class SettingsDialog(QFrame):
    SECTION_DEFS = [
        ("General", "fa5s.sliders-h", "General app preferences, visuals, startup behavior, and assistant defaults."),
        ("Ollama (Local)", "fa5s.microchip", "Choose your local chat model, naming model, and reasoning mode."),
        ("API Endpoint", "fa5s.cloud", "Configure provider, keys, task models, and chat naming."),
        ("Integrations", "fa5s.plug", "Store optional tokens used by plugins such as GitHub-backed code review."),
    ]

    def __init__(self, settings_manager, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
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
        self.api_tab = ApiSettingsWidget(self.settings_manager)
        self.integrations_tab = IntegrationsSettingsWidget(self.settings_manager)

        self.section_widgets = {
            "General": self.appearance_tab,
            "Ollama (Local)": self.ollama_tab,
            "API Endpoint": self.api_tab,
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
        muted_text = "#8d8d8d"
        soft_text = "#bfc4ca"
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
                color: #ffffff;
            }}
            QPushButton#settingsCategoryButton:checked {{
                background-color: rgba(255, 255, 255, 0.06);
                border-color: rgba(255, 255, 255, 0.08);
                color: #ffffff;
            }}
            QLabel#settingsCategoryIcon {{
                background-color: {badge_gray};
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 14px;
            }}
            QLabel#settingsPaneTitle {{
                color: #f3f5f8;
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
                background-color: #2d2d2d;
                border: 1px solid #3f3f3f;
                color: #ffffff;
                padding: 5px;
                border-radius: 4px;
                font-family: 'Segoe UI', sans-serif;
                font-size: 12px;
            }}
            QComboBox#settingsComboBox:hover {{
                border-color: #4a4a4a;
            }}
            QComboBox#settingsComboBox:focus {{
                border-color: {accent};
            }}
            QComboBox#settingsComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 20px;
                border-left-width: 1px;
                border-left-color: #3f3f3f;
                border-left-style: solid;
                border-top-right-radius: 3px;
                border-bottom-right-radius: 3px;
                background-color: transparent;
            }}
            QComboBox#settingsComboBox::down-arrow {{
                image: url(C:/Users/Admin/source/repos/graphite_app/assets/down_arrow.png);
                width: 10px;
                height: 10px;
            }}
            QComboBox#settingsComboBox QLineEdit {{
                background-color: transparent;
                color: #ffffff;
                border: none;
                padding: 0;
                selection-background-color: #264f78;
                selection-color: #ffffff;
            }}
            QFrame#settingsFlyoutShell QMenu {{
                background-color: #2d2d2d;
                border: 1px solid #3f3f3f;
                border-radius: 4px;
                padding: 4px;
            }}
            QFrame#settingsFlyoutShell QMenu::item {{
                background-color: transparent;
                padding: 8px 24px 8px 24px;
                border-radius: 4px;
                color: #ffffff;
                font-family: 'Segoe UI', sans-serif;
                font-size: 12px;
            }}
            QFrame#settingsFlyoutShell QMenu::item:selected {{
                background-color: {accent};
                color: #ffffff;
            }}
            QFrame#settingsFlyoutShell QMenu::item:disabled {{
                color: #777777;
            }}
            QFrame#settingsFlyoutShell QMenu::separator {{
                height: 1px;
                background-color: #3f3f3f;
                margin: 4px 0px;
            }}
            QPushButton#settingsCloseButton {{
                background-color: rgba(255, 255, 255, 0.04);
                color: #f3f5f8;
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
        if mode_text == "Ollama (Local)":
            self.set_current_section("Ollama (Local)")
        elif mode_text == "API Endpoint":
            self.set_current_section("API Endpoint")
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
