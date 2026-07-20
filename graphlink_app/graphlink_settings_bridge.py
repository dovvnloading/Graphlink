"""Desktop-side state bridge for the settings island.

Grown one page at a time per the recorded Phase 3 increment sequence in
doc/FRONTEND_WEB_MIGRATION_MASTER_PLAN.md. Increment 2 shipped
activeSection navigation alone; increment 3 added the General/Appearance
page (no secrets, no workers); increment 4 (this) adds the Integrations
page - the first page with a real secret, and therefore the first proof of
the write-only secrets protocol the Phase 3 scope note decided on.

WRITE-ONLY SECRETS, BY DESIGN: IslandBridge.publish() re-serializes the
FULL snapshot on every mutation, unconditionally - unlike the Qt widget
this replaces, which safely pre-fills the real decrypted token into a
masked QLineEdit because that value never leaves process memory as a
string. A QWebChannel payload is categorically different: it is
inspectable via Chromium DevTools. So this bridge never emits the token
itself, only githubTokenConfigured: bool - modeled on the composer's
existing id-not-path firewall (ComposerBridge._attachment_paths), which
solved the identical "don't let a sensitive value cross the wire" problem
for filesystem paths. setGithubToken() is write-only in the same sense a
password-change form is: JS sends a new value in, Python never echoes the
current one back out. tests/test_settings_bridge_secrets.py is the
contract test proving this holds across a full lifecycle, extending
test_secrets_at_rest.py's own "assert the literal secret is absent from
every serialized form" pattern to this bridge's publish() output instead
of session.dat.

Each field-level intent (setTheme/setShowTokenCounter/etc.) applies and
publishes immediately, one field at a time - a deliberate departure from
AppearanceSettingsWidget's single batched "Apply" button (see the Phase 3
session log for the reasoning): a persistent settings panel with instant
feedback fits a live snapshot-driven bridge better than a modal-style
commit step, and every existing bridge intent in this codebase (composer,
notification, command-palette) is already shaped as one small, immediately
effective call per concern, never a multi-field batch.

REAL-SHELL WIRING (increment 8): checkForUpdates()/openRepository() and
main_window.on_settings_changed()'s four side effects (token-counter
visibility, overlay repositioning, agent reinitialization, composer
provider status) all need a real ChatWindow reference this bridge didn't
have before increment 8 - it's now the optional `main_window` constructor
argument (None in every test and the mock bridge, where these calls are
simply no-ops). openRepository() turned out not to need main_window at
all once ground-truthed against the real legacy code: it's a plain
`webbrowser.open(...)` call, not QDesktopServices as an earlier scope note
assumed - ground-truthing found the discrepancy before it was carried
forward. refresh_update_status()/set_update_check_in_progress() are
duck-typed callbacks ChatWindow._handle_update_check_result()/
check_for_updates() call directly, matching the legacy widget's own two
method names exactly so the same call sites work against either.
"""

from __future__ import annotations

import json
import os
import webbrowser
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QApplication, QFileDialog

import api_provider
import graphlink_config as config
from graphlink_agents_tools import ModelPullWorkerThread
from graphlink_island_bridge import IslandBridge
from graphlink_licensing import SettingsManager
from graphlink_model_catalog import AUTO_MODEL, INHERIT_MODEL
from graphlink_settings_workers import ApiModelLoadWorker, LlamaCppModelScanWorker, OllamaModelScanWorker
from graphlink_styles import THEMES
from graphlink_update import UPDATE_REPOSITORY_URL

# The 5 settings sections, in rail order - identical vocabulary to
# SettingsDialog.SECTION_DEFS/set_current_section_by_mode so this bridge and
# the eventual native shell never need two names for the same section.
SECTION_NAMES = (
    "General",
    config.MODE_OLLAMA_LOCAL,
    config.MODE_LLAMACPP_LOCAL,
    config.MODE_API_ENDPOINT,
    "Integrations",
)

# Combo order on the original ApiSettingsWidget - preserved here since
# apiTaskModels is a plain dict and iteration order otherwise has no
# defined meaning.
API_TASKS = (
    config.TASK_TITLE,
    config.TASK_CHAT,
    config.TASK_CHART,
    config.TASK_IMAGE_GEN,
    config.TASK_WEB_VALIDATE,
    config.TASK_WEB_SUMMARIZE,
)

API_PROVIDERS = (config.API_PROVIDER_OPENAI, config.API_PROVIDER_ANTHROPIC, config.API_PROVIDER_GEMINI)

# Same set SettingsManager.OLLAMA_MODEL_TASKS already uses - task_chat is a
# uniform member of this set at the persistence layer even though the
# original widget's UI special-cases its display.
OLLAMA_TASKS = (
    config.TASK_CHAT,
    config.TASK_TITLE,
    config.TASK_CHART,
    config.TASK_WEB_VALIDATE,
    config.TASK_WEB_SUMMARIZE,
)

REASONING_MODES = ("Thinking", "Quick")


class SettingsBridge(IslandBridge, QObject):
    stateChanged = Signal(str)

    def __init__(self, settings_manager: SettingsManager, main_window=None, parent=None):
        QObject.__init__(self, parent)
        IslandBridge.__init__(self)
        self.settings_manager = settings_manager
        # Duck-typed reference to ChatWindow, exactly like the legacy
        # AppearanceSettingsWidget's own self.window().parent() reach-through
        # (there's no Qt signal connecting this bridge to ChatWindow to
        # reuse) - None in every test/mock-bridge context, where these three
        # side effects simply don't fire. See checkForUpdates()/
        # _notify_main_window_settings_changed() below.
        self._main_window = main_window
        self._active_section = SECTION_NAMES[0]
        self._api_provider = settings_manager.get_api_provider()
        if self._api_provider not in API_PROVIDERS:
            self._api_provider = config.API_PROVIDER_OPENAI
        self._api_load_status = "idle"
        self._notice: str | None = None
        self._api_worker: ApiModelLoadWorker | None = None
        self._api_worker_provider: str | None = None
        self._ollama_scan_status = "idle"
        self._ollama_scan_worker: OllamaModelScanWorker | None = None
        self._ollama_pull_status = "idle"
        self._ollama_pull_worker: ModelPullWorkerThread | None = None
        self._llama_chat_model_path = settings_manager.get_llama_cpp_chat_model_path()
        self._llama_title_model_path = settings_manager.get_llama_cpp_title_model_override_path()
        self._llama_scan_status = "idle"
        self._llama_scan_worker: LlamaCppModelScanWorker | None = None
        self._update_check_in_progress = False

    def _transport_send(self, payload_json: str) -> None:
        self.stateChanged.emit(payload_json)

    def _api_available_models(self) -> list[str]:
        if self._api_provider == config.API_PROVIDER_GEMINI:
            return list(api_provider.GEMINI_MODELS_STATIC)
        catalog = self.settings_manager.get_api_model_catalog(self._api_provider)
        return [entry["model_id"] for entry in catalog if entry.get("model_id")]

    def _api_image_models(self) -> list[str]:
        """Gemini's image-generation task takes a DIFFERENT curated list than
        its chat models (GEMINI_IMAGE_MODELS_STATIC), exactly as the legacy
        ApiSettingsWidget did (it made the image combo a non-editable dropdown
        of these two models). Every other provider's image field shares the
        general apiAvailableModels list, so this returns [] for them and the
        React side falls back to the shared datalist. Without this, Gemini's
        Image Generation field would suggest chat models that silently break
        image generation."""
        if self._api_provider == config.API_PROVIDER_GEMINI:
            return list(api_provider.GEMINI_IMAGE_MODELS_STATIC)
        return []

    @staticmethod
    def _flatten_ollama_assignment(assignment: dict) -> str:
        mode = assignment.get("mode", AUTO_MODEL)
        if mode == "explicit":
            return assignment.get("model_id") or AUTO_MODEL
        return mode

    def _ollama_model_assignments(self) -> dict[str, str]:
        raw = self.settings_manager.get_ollama_model_assignments()
        return {task: self._flatten_ollama_assignment(raw.get(task, {})) for task in OLLAMA_TASKS}

    def _ollama_scan_summary(self) -> str:
        sm = self.settings_manager
        scan_mode = sm.get_ollama_model_scan_mode()
        scan_path = sm.get_ollama_model_scan_path()
        cached_models = sm.get_ollama_scanned_models()
        has_saved_scan = bool(scan_mode or scan_path or sm.get_ollama_model_scan_locations())
        if not has_saved_scan:
            return "No saved scan yet. Run a system scan or choose a folder to build the local model list."
        if not cached_models:
            return "The last scan is saved, but it did not find any Ollama models."
        if scan_mode == "folder" and scan_path:
            return f"Using saved scan from folder: {scan_path}"
        if scan_mode == "system":
            return "Using saved system scan results from local Ollama locations."
        return "Using saved scanned model list."

    def _llama_scan_summary(self) -> str:
        sm = self.settings_manager
        scan_mode = sm.get_llama_cpp_model_scan_mode()
        scan_path = sm.get_llama_cpp_model_scan_path()
        cached_models = sm.get_llama_cpp_scanned_models()
        has_saved_scan = bool(scan_mode or scan_path or sm.get_llama_cpp_model_scan_locations())
        if not has_saved_scan:
            return "No saved GGUF scan yet. Run a system scan or choose a folder to build the local model list."
        if not cached_models:
            return "The last GGUF scan is saved, but it did not find any models."
        if scan_mode == "folder" and scan_path:
            return f"Using saved scan from folder: {scan_path}"
        if scan_mode == "system":
            return "Using saved system scan results from common local model folders."
        return "Using saved scanned GGUF model list."

    def _build_state_payload(self) -> dict[str, Any]:
        sm = self.settings_manager
        return {
            "activeSection": self._active_section,
            "theme": sm.get_theme(),
            "showTokenCounter": sm.get_show_token_counter(),
            "enableSystemPrompt": sm.get_enable_system_prompt(),
            "notificationPreferences": sm.get_notification_preferences(),
            "updateNotificationsEnabled": sm.get_update_notifications_enabled(),
            "updateStatusMessage": sm.get_update_status_message(),
            "updateStatusLevel": sm.get_update_status_level(),
            "updateLastCheckedAt": sm.get_update_last_checked_at(),
            "updateAvailable": sm.get_update_available(),
            "updateLatestVersion": sm.get_update_latest_version(),
            "updateCheckInProgress": self._update_check_in_progress,
            "githubTokenConfigured": bool(sm.get_github_token()),
            "apiProvider": self._api_provider,
            "apiBaseUrl": sm.get_api_base_url(),
            "openaiKeyConfigured": bool(sm.get_openai_key()),
            "anthropicKeyConfigured": bool(sm.get_anthropic_key()),
            "geminiKeyConfigured": bool(sm.get_gemini_key()),
            "apiTaskModels": dict(sm.get_api_models(self._api_provider)),
            "apiAvailableModels": self._api_available_models(),
            "apiImageModels": self._api_image_models(),
            "apiLoadStatus": self._api_load_status,
            "ollamaReasoningMode": sm.get_ollama_reasoning_mode(),
            "ollamaCurrentModel": config.OLLAMA_MODELS.get(config.TASK_CHAT, ""),
            "ollamaModelAssignments": self._ollama_model_assignments(),
            "ollamaScannedModels": sm.get_ollama_scanned_models(),
            "ollamaScanSummary": self._ollama_scan_summary(),
            "ollamaScanStatus": self._ollama_scan_status,
            "ollamaPullStatus": self._ollama_pull_status,
            "llamaCppReasoningMode": sm.get_llama_cpp_reasoning_mode(),
            "llamaCppChatModelPath": self._llama_chat_model_path,
            "llamaCppTitleModelPath": self._llama_title_model_path,
            "llamaCppChatFormat": sm.get_llama_cpp_chat_format(),
            "llamaCppNCtx": sm.get_llama_cpp_n_ctx(),
            "llamaCppNGpuLayers": sm.get_llama_cpp_n_gpu_layers(),
            "llamaCppNThreads": sm.get_llama_cpp_n_threads(),
            "llamaCppScannedModels": sm.get_llama_cpp_scanned_models(),
            "llamaCppScanSummary": self._llama_scan_summary(),
            "llamaCppScanStatus": self._llama_scan_status,
            "notice": self._notice,
        }

    @Slot()
    def ready(self):
        self.publish()

    @Slot(str)
    def setActiveSection(self, section: str):
        """Navigate the rail. Unrecognized section names are ignored, not
        raised - a boundary intent from JS must never crash the bridge on a
        bad string, matching CommandPaletteBridge.executeCommand's own
        tolerance of a stale/invalid id."""
        if section not in SECTION_NAMES or section == self._active_section:
            return
        self._active_section = section
        self.publish()

    def set_active_section(self, section: str):
        """Python-side equivalent of setActiveSection, for a future native
        shell to deep-link into (mirrors set_current_section_by_mode)."""
        self.setActiveSection(section)

    def _notify_main_window_settings_changed(self):
        """Explicit replacement for AppearanceSettingsWidget.apply_settings()'s
        duck-typed `self.window().parent().on_settings_changed()` reach-
        through - there is no Qt signal connecting this bridge to ChatWindow
        to reuse instead. The legacy widget called this once per batched
        Apply click, covering all 5 General/Appearance fields at once;
        since every field here applies live instead, this fires once per
        field-level intent rather than once per Apply click - the same
        four side effects (token-counter visibility, overlay
        repositioning, agent reinitialization, composer provider status),
        just triggered more often and more promptly. A no-op wherever
        main_window is None (every test and the mock bridge)."""
        if self._main_window is not None and hasattr(self._main_window, "on_settings_changed"):
            self._main_window.on_settings_changed()

    def _reinitialize_main_window_agent(self):
        """Targeted equivalent of the legacy Ollama/LlamaCpp save's own
        main_window.reinitialize_agent() call. The reasoning mode (for
        whichever local provider is currently active) feeds
        ChatWindow._get_current_system_prompt(), which is baked into the
        agent at construction - so a reasoning-mode change is inert in the
        running session until the agent is rebuilt. Narrower than
        _notify_main_window_settings_changed() on purpose: a reasoning-mode
        toggle should not also refresh token-counter visibility / overlay
        positions / composer status, matching exactly what the legacy save
        did (reinitialize_agent only). A no-op wherever main_window is None
        (every test and the mock bridge)."""
        if self._main_window is not None and hasattr(self._main_window, "reinitialize_agent"):
            self._main_window.reinitialize_agent()

    @Slot(str)
    def setTheme(self, theme_name: str):
        """Persist the theme, restyle the running app, and publish this
        island's own updated snapshot. apply_theme()'s theme_changed_all()
        call (Phase 3 increment 2) separately republishes every OTHER
        registered island host - but only once this bridge itself is
        wrapped in a registered WebIslandHost (increment 8); calling
        publish() directly here keeps this bridge's own state correct
        regardless of that wiring, and is harmless once it's also reached a
        second time via theme_changed_all(). Unrecognized theme names are
        ignored rather than raised, same boundary-tolerance convention as
        setActiveSection."""
        if theme_name not in THEMES:
            return
        self.settings_manager.set_theme(theme_name)
        config.apply_theme(QApplication.instance(), theme_name)
        self._notify_main_window_settings_changed()
        self.publish()

    @Slot(bool)
    def setShowTokenCounter(self, enabled: bool):
        self.settings_manager.set_show_token_counter(enabled)
        self._notify_main_window_settings_changed()
        self.publish()

    @Slot(bool)
    def setEnableSystemPrompt(self, enabled: bool):
        self.settings_manager.set_enable_system_prompt(enabled)
        self._notify_main_window_settings_changed()
        self.publish()

    @Slot(str, bool)
    def setNotificationPreference(self, notification_type: str, enabled: bool):
        if notification_type not in SettingsManager.NOTIFICATION_TYPES:
            return
        self.settings_manager.set_notification_preferences({notification_type: enabled})
        self._notify_main_window_settings_changed()
        self.publish()

    @Slot(bool)
    def setUpdateNotificationsEnabled(self, enabled: bool):
        self.settings_manager.set_update_notifications_enabled(enabled)
        self._notify_main_window_settings_changed()
        self.publish()

    @Slot()
    def checkForUpdates(self):
        """Explicit replacement for AppearanceSettingsWidget.check_for_updates()'s
        duck-typed reach-through. main_window.check_for_updates() itself
        calls status_target.set_update_check_in_progress(True) (duck-typed,
        implemented below) before starting UpdateCheckWorker - this bridge
        never starts that QThread directly, since ChatWindow is the only
        thing that owns it."""
        if self._main_window is not None and hasattr(self._main_window, "check_for_updates"):
            self._main_window.check_for_updates(manual=True, status_target=self)
            return
        self._notice = "The main window is not available for update checks."
        self.publish()

    @Slot()
    def openRepository(self):
        webbrowser.open(UPDATE_REPOSITORY_URL)

    def refresh_update_status(self):
        """Duck-typed callback ChatWindow._handle_update_check_result() calls
        once UpdateCheckWorker finishes - every update-status field is
        already read live from settings_manager in _build_state_payload(),
        so republishing is all that's needed once record_update_check_result()
        has persisted the new values."""
        self.publish()

    def set_update_check_in_progress(self, in_progress: bool):
        """Duck-typed callback ChatWindow.check_for_updates() calls directly
        (status_target.set_update_check_in_progress(...)), matching the
        legacy widget's own method of the same name."""
        self._update_check_in_progress = bool(in_progress)
        self.publish()

    @Slot(str)
    def setGithubToken(self, token: str):
        """Write-only: persists the token but never echoes it back over the
        bridge - only githubTokenConfigured's boolean changes in the next
        snapshot. Matches the original widget's own .strip() before save."""
        self.settings_manager.set_github_token(token.strip())
        self.publish()

    @Slot()
    def clearGithubToken(self):
        self.settings_manager.set_github_token("")
        self.publish()

    @Slot(str)
    def setApiProvider(self, provider: str):
        """Switch which provider's key/base-url/task-models/catalog the
        payload reflects. Does not persist anything by itself - matches
        the original widget's own provider_combo, which only takes effect
        for real once Save Configuration commits it."""
        if provider not in API_PROVIDERS or provider == self._api_provider:
            return
        self._api_provider = provider
        self._api_load_status = "idle"
        self._notice = None
        self.publish()

    @Slot(str)
    def saveApiConfiguration(self, config_json: str):
        """Atomic, all-or-nothing - the one deliberate exception to every
        other intent on this bridge applying live, one field at a time.
        Several correlated values (provider, base URL, key, per-task
        models) must commit together, with a real ordering constraint:
        provider init must succeed BEFORE anything is persisted, so a
        rejected key can never overwrite the last known-good profile -
        ApiSettingsWidget.save_settings()'s own comment states this
        exactly, and this port preserves it verbatim. A single JSON-string
        argument is also the one deliberate exception to "primitive Slot
        args only," the shape every other intent in this codebase follows
        - splitting this into several separate calls would lose the
        atomicity this specific save needs.
        """
        try:
            payload = json.loads(config_json)
        except (TypeError, ValueError):
            self._notice = "Malformed configuration payload."
            self.publish()
            return
        if not isinstance(payload, dict):
            self._notice = "Malformed configuration payload."
            self.publish()
            return

        provider = payload.get("provider")
        base_url = str(payload.get("baseUrl") or "").strip()
        api_key = str(payload.get("apiKey") or "").strip()
        task_models = payload.get("taskModels")
        if not isinstance(task_models, dict):
            task_models = {}

        if provider not in API_PROVIDERS:
            self._notice = "Unrecognized provider."
            self.publish()
            return
        if provider == config.API_PROVIDER_OPENAI and not base_url:
            self._notice = "Please enter the Base URL for the OpenAI-compatible provider."
            self.publish()
            return
        if not api_key:
            self._notice = "Please enter your API Key."
            self.publish()
            return

        required_tasks = [
            task for task in API_TASKS
            if not (provider == config.API_PROVIDER_ANTHROPIC and task == config.TASK_IMAGE_GEN)
        ]
        for task in required_tasks:
            if not str(task_models.get(task) or "").strip():
                self._notice = f"Please select a model for task: {task}"
                self.publish()
                return

        try:
            if provider == config.API_PROVIDER_OPENAI:
                api_provider.initialize_api(provider, api_key, base_url)
            else:
                api_provider.initialize_api(provider, api_key)
        except Exception as exc:
            # Found by tests/test_settings_bridge_secrets.py: some HTTP
            # client libraries embed request parameters (including the key
            # just rejected) directly in their exception text. That was
            # harmless in the original widget (a transient native
            # QMessageBox), but this notice gets published in a
            # DevTools-inspectable wire snapshot - redact the raw key out
            # of the message before it ever reaches _build_state_payload().
            self._notice = f"Failed to initialize the API provider: {str(exc).replace(api_key, '***')}"
            self.publish()
            return

        # Commit only after provider initialization succeeds - see the
        # docstring above.
        sm = self.settings_manager
        openai_key = api_key if provider == config.API_PROVIDER_OPENAI else sm.get_openai_key()
        anthropic_key = api_key if provider == config.API_PROVIDER_ANTHROPIC else sm.get_anthropic_key()
        gemini_key = api_key if provider == config.API_PROVIDER_GEMINI else sm.get_gemini_key()
        sm.set_api_settings(provider, base_url, openai_key, anthropic_key, gemini_key)

        models_dict = dict(sm.get_api_models(provider))
        for task in required_tasks:
            model_id = str(task_models.get(task) or "").strip()
            if model_id:
                models_dict[task] = model_id
                api_provider.set_task_model(task, model_id)
        sm.set_api_models(models_dict, provider)

        os.environ["GRAPHLINK_API_PROVIDER"] = provider
        if provider == config.API_PROVIDER_OPENAI:
            os.environ["GRAPHLINK_OPENAI_API_KEY"] = api_key
            os.environ["GRAPHLINK_API_BASE"] = base_url
        elif provider == config.API_PROVIDER_ANTHROPIC:
            os.environ["GRAPHLINK_ANTHROPIC_API_KEY"] = api_key
        else:
            os.environ["GRAPHLINK_GEMINI_API_KEY"] = api_key

        self._api_provider = provider
        self._notice = None
        self.publish()

    @Slot(str)
    def loadAvailableModels(self, api_key: str):
        """Fetch the current provider's live model catalog. Takes the
        just-typed key directly (JS passes whatever is currently in the
        field, matching the original Load button's own behavior of using
        the live-typed value even before Save) rather than reading a
        stored key - this bridge never stores a plaintext key it could
        read back out anyway.

        A faithful binary status port (Phase 3 Section C design decision):
        idle|running|done|error, wired off ApiModelLoadWorker's existing
        finished/error signals - no real progress, no cancellation. The
        worker is owned directly by this bridge (parented to self) since
        it needs no window reference, unlike Check-for-Updates/Open
        Repository above.
        """
        if self._api_worker is not None and self._api_worker.isRunning():
            return
        provider = self._api_provider
        api_key = api_key.strip()
        base_url = self.settings_manager.get_api_base_url().strip()
        if provider == config.API_PROVIDER_OPENAI and not base_url:
            self._notice = "Please enter the Base URL for the OpenAI-compatible provider."
            self.publish()
            return
        if not api_key:
            self._notice = "Please enter the API Key."
            self.publish()
            return

        self._api_load_status = "running"
        self._notice = None
        self.publish()

        self._api_worker_provider = provider
        worker = ApiModelLoadWorker(
            provider,
            api_key,
            base_url if provider == config.API_PROVIDER_OPENAI else None,
            self,
        )
        self._api_worker = worker
        worker.finished.connect(self._handle_models_loaded)
        worker.error.connect(self._handle_models_load_error)
        worker.start()

    def _handle_models_loaded(self, descriptors: list[dict]):
        stale = self._api_worker_provider != self._api_provider
        self._api_worker = None
        self._api_worker_provider = None
        if stale:
            # A provider switch mid-flight discards this result, matching
            # handle_models_loaded's own guard - but the load indicator
            # still clears, matching _clear_api_worker's unconditional
            # button re-enable.
            self._api_load_status = "idle"
            self.publish()
            return
        self.settings_manager.set_api_model_catalog(descriptors, self._api_provider)
        self._api_load_status = "done"
        self._notice = None
        self.publish()

    def _handle_models_load_error(self, error_message: str):
        stale = self._api_worker_provider != self._api_provider
        # Same redaction as saveApiConfiguration's except-block, and for the
        # identical reason: ApiModelLoadWorker.run() also calls
        # api_provider.initialize_api() with this exact key, so its
        # exception text can carry the same leak risk. Read the key off the
        # worker BEFORE clearing the reference below.
        worker_api_key = self._api_worker.api_key if self._api_worker is not None else ""
        self._api_worker = None
        self._api_worker_provider = None
        if stale:
            self._api_load_status = "idle"
            self.publish()
            return
        self._api_load_status = "error"
        self._notice = f"Catalog refresh failed: {error_message.replace(worker_api_key, '***') if worker_api_key else error_message}"
        self.publish()

    @Slot()
    def resetApiSettings(self):
        self.settings_manager.reset_api_settings()
        self._api_provider = config.API_PROVIDER_OPENAI
        self._api_load_status = "idle"
        self._notice = None
        self.publish()

    @Slot(str)
    def setOllamaReasoningMode(self, mode: str):
        if mode not in REASONING_MODES:
            return
        self.settings_manager.set_ollama_reasoning_mode(mode)
        # Reasoning mode feeds the agent's system prompt, so it must take
        # effect in the running session immediately - the legacy Ollama save
        # called reinitialize_agent() for exactly this reason. Without it the
        # new mode is persisted but inert until an app restart.
        self._reinitialize_main_window_agent()
        self.publish()

    @Slot(str, str)
    def setOllamaModelAssignment(self, task: str, value: str):
        """Live, per-task apply - same departure from the original's
        Save-button batching as every other intent on this island. value is
        the flat wire representation ("inherit"/"auto"/an explicit model
        id, possibly one not present in ollamaScannedModels - preserved
        verbatim rather than dropped, matching the original editable
        combo's behavior)."""
        if task not in OLLAMA_TASKS:
            return
        value = value.strip()
        if not value or value in (INHERIT_MODEL, AUTO_MODEL):
            assignment = {"mode": value or AUTO_MODEL, "model_id": ""}
        else:
            assignment = {"mode": "explicit", "model_id": value}

        assignments = self.settings_manager.get_ollama_model_assignments()
        assignments[task] = assignment
        self.settings_manager.set_ollama_model_assignments(assignments)
        config.sync_ollama_task_models(self.settings_manager)
        if task == config.TASK_CHAT and assignment["mode"] == "explicit":
            config.set_current_model(assignment["model_id"])
        self.publish()

    @Slot()
    def scanOllamaSystem(self):
        self._start_ollama_scan(None)

    @Slot()
    def pickOllamaScanFolder(self):
        """Native picker, matching the Phase 3 checklist's pickFolder
        intent shape: synchronous, fire-and-forget - results arrive via the
        same scan-worker status port scanOllamaSystem() already uses, not a
        separate return value."""
        initial_directory = self.settings_manager.get_ollama_model_scan_path() or os.path.expanduser("~")
        selected_directory = QFileDialog.getExistingDirectory(None, "Select Ollama Folder to Scan", initial_directory)
        if not selected_directory:
            return
        self._start_ollama_scan(selected_directory)

    def _start_ollama_scan(self, scan_path: str | None):
        if self._ollama_scan_worker is not None and self._ollama_scan_worker.isRunning():
            return
        self._ollama_scan_status = "running"
        self._notice = None
        self.publish()

        worker = OllamaModelScanWorker(scan_path, self)
        self._ollama_scan_worker = worker
        worker.finished.connect(self._handle_ollama_scan_finished)
        worker.error.connect(self._handle_ollama_scan_error)
        worker.start()

    def _handle_ollama_scan_finished(self, results: dict):
        self._ollama_scan_worker = None
        models = results.get("models", [])
        self.settings_manager.set_ollama_model_scan_cache(
            models,
            results.get("scan_mode", ""),
            results.get("scan_path", ""),
            results.get("locations", []),
        )
        config.sync_ollama_task_models(self.settings_manager)
        self._ollama_scan_status = "done"
        self.publish()

    def _handle_ollama_scan_error(self, error_message: str):
        self._ollama_scan_worker = None
        self._ollama_scan_status = "error"
        self._notice = f"Scan failed: {error_message}"
        self.publish()

    @Slot(str)
    def pullOllamaModel(self, model_name: str):
        model_name = model_name.strip()
        if not model_name:
            self._notice = "Model name cannot be empty."
            self.publish()
            return
        if self._ollama_pull_worker is not None and self._ollama_pull_worker.isRunning():
            return

        self._ollama_pull_status = "running"
        self._notice = None
        self.publish()

        worker = ModelPullWorkerThread(model_name)
        worker.setParent(self)
        self._ollama_pull_worker = worker
        worker.finished.connect(self._handle_ollama_pull_finished)
        worker.error.connect(self._handle_ollama_pull_error)
        worker.start()

    def _handle_ollama_pull_finished(self, message: str, model_name: str):
        self._ollama_pull_worker = None
        self._ollama_pull_status = "done"
        self._notice = None
        config.set_current_model(model_name)
        self.publish()

    def _handle_ollama_pull_error(self, error_message: str):
        self._ollama_pull_worker = None
        self._ollama_pull_status = "error"
        self._notice = error_message
        self.publish()

    @Slot(str)
    def setLlamaCppReasoningMode(self, mode: str):
        if mode not in REASONING_MODES:
            return
        self.settings_manager.set_llama_cpp_reasoning_mode(mode)
        # Same reasoning as setOllamaReasoningMode: when Llama.cpp is the
        # active provider its reasoning mode feeds _get_current_system_prompt,
        # so the running agent must be rebuilt for the change to take effect
        # live (the legacy Llama.cpp save called reinitialize_agent too).
        self._reinitialize_main_window_agent()
        self.publish()

    @Slot(str)
    def setLlamaCppChatFormat(self, chat_format: str):
        self.settings_manager.set_llama_cpp_chat_format(chat_format)
        self.publish()

    def _set_llama_cpp_runtime(self, **overrides):
        sm = self.settings_manager
        current = {
            "n_ctx": sm.get_llama_cpp_n_ctx(),
            "n_gpu_layers": sm.get_llama_cpp_n_gpu_layers(),
            "n_threads": sm.get_llama_cpp_n_threads(),
            "chat_format": sm.get_llama_cpp_chat_format(),
        }
        current.update(overrides)
        sm.set_llama_cpp_runtime(**current)
        self.publish()

    @Slot(int)
    def setLlamaCppNCtx(self, n_ctx: int):
        self._set_llama_cpp_runtime(n_ctx=n_ctx)

    @Slot(int)
    def setLlamaCppNGpuLayers(self, n_gpu_layers: int):
        self._set_llama_cpp_runtime(n_gpu_layers=n_gpu_layers)

    @Slot(int)
    def setLlamaCppNThreads(self, n_threads: int):
        self._set_llama_cpp_runtime(n_threads=n_threads)

    @Slot()
    def pickLlamaCppChatModelFile(self):
        """Native picker - stages the path only, matching the original
        widget's Browse button (which just fills the QLineEdit; nothing
        persists until Save). See the payload's own field docstring."""
        selected = self._pick_gguf_file("Select Llama.cpp Chat Model", self._llama_chat_model_path)
        if selected:
            self._llama_chat_model_path = selected
            self.publish()

    @Slot()
    def pickLlamaCppTitleModelFile(self):
        initial = self._llama_title_model_path or self._llama_chat_model_path
        selected = self._pick_gguf_file("Select Llama.cpp Chat Naming Model", initial)
        if selected:
            self._llama_title_model_path = selected
            self.publish()

    @Slot(str)
    def setLlamaCppChatModelPath(self, path: str):
        """Stage a chat-model path chosen from the scanned-models dropdown -
        the non-native-dialog counterpart to pickLlamaCppChatModelFile,
        mirroring the legacy widget's "Scanned Chat Model" combo whose
        selection filled the chat-model field (on_chat_combo_change). Staged
        only, exactly like the picker; nothing persists until
        saveLlamaCppSettings validates it. An empty string clears the staged
        path, matching the legacy combo's empty first entry."""
        self._llama_chat_model_path = path.strip()
        self.publish()

    @Slot(str)
    def setLlamaCppTitleModelPath(self, path: str):
        """Staged counterpart of pickLlamaCppTitleModelFile for the scanned
        naming-model dropdown - see setLlamaCppChatModelPath."""
        self._llama_title_model_path = path.strip()
        self.publish()

    def _pick_gguf_file(self, caption: str, initial_path: str) -> str:
        initial_location = initial_path or self.settings_manager.get_llama_cpp_model_scan_path() or os.path.expanduser("~")
        selected_files, _ = QFileDialog.getOpenFileName(
            None, caption, initial_location, "GGUF Models (*.gguf);;All Files (*.*)"
        )
        return selected_files

    @Slot()
    def scanLlamaCppSystem(self):
        self._start_llama_scan(None)

    @Slot()
    def pickLlamaCppScanFolder(self):
        initial_directory = self.settings_manager.get_llama_cpp_model_scan_path() or os.path.expanduser("~")
        selected_directory = QFileDialog.getExistingDirectory(None, "Select Folder to Scan for GGUF Models", initial_directory)
        if not selected_directory:
            return
        self._start_llama_scan(selected_directory)

    def _start_llama_scan(self, scan_path: str | None):
        if self._llama_scan_worker is not None and self._llama_scan_worker.isRunning():
            return
        self._llama_scan_status = "running"
        self._notice = None
        self.publish()

        worker = LlamaCppModelScanWorker(scan_path, self)
        self._llama_scan_worker = worker
        worker.finished.connect(self._handle_llama_scan_finished)
        worker.error.connect(self._handle_llama_scan_error)
        worker.start()

    def _handle_llama_scan_finished(self, results: dict):
        self._llama_scan_worker = None
        models = results.get("models", [])
        self.settings_manager.set_llama_cpp_model_scan_cache(
            models,
            results.get("scan_mode", ""),
            results.get("scan_path", ""),
            results.get("locations", []),
        )
        self._llama_scan_status = "done"
        self.publish()

    def _handle_llama_scan_error(self, error_message: str):
        self._llama_scan_worker = None
        self._llama_scan_status = "error"
        self._notice = f"Scan failed: {error_message}"
        self.publish()

    @Slot()
    def saveLlamaCppSettings(self):
        """Validates the staged model paths (file exists, .gguf extension)
        and, if the app is currently in Llama.cpp mode, requires
        api_provider.initialize_local_provider() to succeed BEFORE
        persisting - the identical "commit only after init succeeds"
        ordering saveApiConfiguration() already implements, ported here for
        the same reason: a rejected/invalid GGUF must not overwrite the
        last known-good configuration."""
        chat_path = self._llama_chat_model_path.strip()
        title_path = self._llama_title_model_path.strip()

        if not chat_path:
            self._notice = "Chat Model File cannot be empty."
            self.publish()
            return
        if not os.path.isfile(chat_path):
            self._notice = f"Chat model file was not found: {chat_path}"
            self.publish()
            return
        if not chat_path.lower().endswith(".gguf"):
            self._notice = "Chat Model File must point to a .gguf file."
            self.publish()
            return
        if title_path:
            if not os.path.isfile(title_path):
                self._notice = f"Chat naming model file was not found: {title_path}"
                self.publish()
                return
            if not title_path.lower().endswith(".gguf"):
                self._notice = "Chat Naming File must point to a .gguf file."
                self.publish()
                return

        if self.settings_manager.get_current_mode() == config.MODE_LLAMACPP_LOCAL:
            settings = {
                "chat_model_path": chat_path,
                "title_model_path": title_path,
                "reasoning_mode": self.settings_manager.get_llama_cpp_reasoning_mode(),
                "chat_format": self.settings_manager.get_llama_cpp_chat_format(),
                "n_ctx": self.settings_manager.get_llama_cpp_n_ctx(),
                "n_gpu_layers": self.settings_manager.get_llama_cpp_n_gpu_layers(),
                "n_threads": self.settings_manager.get_llama_cpp_n_threads(),
            }
            try:
                api_provider.initialize_local_provider(config.LOCAL_PROVIDER_LLAMACPP, settings, preload_model=False)
            except Exception as exc:
                self._notice = f"Invalid Llama.cpp configuration: {exc}"
                self.publish()
                return

        self.settings_manager.set_llama_cpp_chat_model_path(chat_path)
        self.settings_manager.set_llama_cpp_title_model_path(title_path)
        self._notice = None
        self.publish()
