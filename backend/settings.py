"""Settings dialog: General + Integrations + API-provider + Ollama +
Llama.cpp pages (Qt-removal plan R2.5d, extended R7.4a, R7.4b, R7.4c).

Unlike composer.py/plugins.py this is a genuine REUSE, not a
reimplementation: SettingsManager (graphlink_settings_store.py) and its own
imports (graphlink_secrets, graphlink_model_catalog) carry zero PySide6
coupling, confirmed via a runtime sys.modules check in
test_settings_never_imports_qt below. api_provider.py,
graphlink_task_config.py, and graphlink_model_catalog.py are confirmed
Qt-free repo-root survivor modules (Qt-removal plan R7.2); ollama is a
hard, always-installed dependency (pyproject.toml), not the optional
llama-cpp-python extra.

Scope (doc/QT_REMOVAL_PLAN.md R2.5d, R7.4a-c): the General/Appearance page,
the Integrations page (GitHub token, write-only), the API-provider page
(OpenAI-Compatible/Anthropic/Gemini), the Ollama page (reasoning mode,
system model scan, per-task model assignment, model pull), and now the
Llama.cpp page (reasoning mode, runtime tunables, GGUF model scan/browse,
chat/naming model paths) are all real here - this closes every settings
page R2.5d originally deferred. The update-check pair
(checkForUpdates/openRepository) still needs a native browser-open
capability that doesn't exist yet in graphlink_desktop.py and is out of
this file's scope (a separate R7.5 gap, not a settings page).

R7.4c's own scope was the LAST genuinely NEW capability gap this whole
settings surface needed: a native OS file/folder picker, since llama.cpp's
C++ bindings and Ollama's manifest walker both need a real on-disk PATH
string, not file bytes (a plain HTML <input type="file"> only ever gives
bytes) - see backend/native_dialogs.py's own docstring for the mechanism
(pywebview's webview.windows list, populated at create_window() time with
zero plumbing changes needed in graphlink_desktop.py). Building it also
retroactively un-defers the Ollama page's own "Scan Folder..." button,
which was deliberately left disabled pending exactly this capability - see
backend/api/intents_settings_ollama.py's pick_ollama_scan_folder.

The API-provider page deliberately does NOT replicate one piece of legacy
behavior: the old QWidget pre-filled its API-key field with the saved
plaintext key on every provider switch (safe there only because the
widget lives in-process, with no serialization boundary). Serializing a
plaintext key into this topic's WS payload would be a real regression
from the write-only pattern already established for the GitHub token
below (githubTokenConfigured: bool) - so this page follows that same
precedent instead: apiKeyConfigured is a per-provider bool, the key input
always starts blank, and saving requires retyping the full key. This is a
deliberate security improvement over legacy parity, not an oversight -
called out explicitly per the "never redefine done" discipline.

SettingsManager owns ONE shared ~/.graphlink/session.dat file for the whole
app (the same file the legacy Qt app read/wrote). register_settings takes
an already-constructed SettingsManager - created ONCE in
backend/app.py's create_app() and shared across every session - rather
than one per SessionBus, which would each open/mutate the same file
independently and stomp on each other's in-memory copy.

ADR-002 stage 2.7 relocated register_settings' own former ~901-line
closure set, page by page, into backend/api/intents_settings_*.py (see
backend/api/_settings_shared.py's own docstring for why a separate leaf
module, not this file, had to hold the pieces those modules and this one
both need). register_settings is now a thin orchestrator: it builds the
one shared per-session SettingsSessionState every page's intents read and
write, registers the single "app-settings" topic (the wire-payload
builder below spans all 4 pages), then calls each
register_settings_*_intents function in turn.
"""

from __future__ import annotations

from typing import Any

import api_provider
import graphlink_task_config as config
from graphlink_settings_store import SettingsManager
from graphlink_model_catalog import AUTO_MODEL

from backend.api._settings_shared import (
    OLLAMA_TASK_KEYS,
    SettingsSessionState,
    apply_anthropic_reasoning_level,
    apply_gemini_reasoning_level,
    apply_llama_cpp_reasoning_level,
    apply_ollama_chat_model,
    apply_ollama_reasoning_level,
    apply_openai_reasoning_level,
    run_locked,
)
from backend.api.intents_settings_api_provider import register_settings_api_provider_intents
from backend.api.intents_settings_general import register_settings_general_intents
from backend.api.intents_settings_llama_cpp import register_settings_llama_cpp_intents
from backend.api.intents_settings_ollama import register_settings_ollama_intents
from backend.events import SessionBus
from backend.notifications import NotificationState

# Re-exported verbatim: backend/composer.py's own setReasoningLevel/setModel
# intents import all seven of these directly (`from backend.settings import
# ...`) - their real implementation now lives in backend/api/_settings_shared.py
# (see that module's own docstring for why), but this file stays their
# public home so composer.py needs only a name update (_apply -> run_locked,
# the private-cross-import rename this split applies everywhere else too),
# not a new import path.
__all__ = [
    "apply_anthropic_reasoning_level",
    "apply_gemini_reasoning_level",
    "apply_llama_cpp_reasoning_level",
    "apply_ollama_chat_model",
    "apply_ollama_reasoning_level",
    "apply_openai_reasoning_level",
    "register_settings",
    "run_locked",
    "settings_payload",
]


def _api_model_catalog_for_wire(manager: SettingsManager, provider: str) -> list[dict[str, Any]]:
    # SettingsManager.get_api_model_catalog is a shared, general-purpose
    # method (also used by graphlink_settings_bridge.py) and always returns
    # snake_case model_id keys - its own internal convention, not this
    # payload's. Every other field in this wire contract is camelCase (JS
    # convention), so this maps at the boundary rather than either bending
    # SettingsManager's shared shape or breaking the payload's own
    # convention - the mismatch would otherwise be silently rejected by the
    # generated validator's additionalProperties: false.
    return [
        {
            "modelId": str(entry.get("model_id", "")),
            "provider": str(entry.get("provider", provider)),
            "capabilities": list(entry.get("capabilities", [])),
            "ready": bool(entry.get("ready", True)),
            "available": bool(entry.get("available", True)),
        }
        for entry in manager.get_api_model_catalog(provider)
    ]


def _flatten_ollama_assignment(assignment: Any) -> str:
    # Wire representation collapses {"mode": ..., "model_id": ...} to a
    # single string ("inherit"/"auto"/an explicit model id) - matches the
    # legacy bridge's own _flatten_ollama_assignment exactly, including
    # preserving an explicit model id verbatim even if it is no longer in
    # the scanned-models list (the field always shows whatever is actually
    # persisted, never silently drops it).
    mode = assignment.get("mode", AUTO_MODEL) if isinstance(assignment, dict) else AUTO_MODEL
    if mode == "explicit":
        return assignment.get("model_id") or AUTO_MODEL
    return mode


def _ollama_model_assignments_for_wire(manager: SettingsManager) -> dict[str, str]:
    raw = manager.get_ollama_model_assignments()
    return {task: _flatten_ollama_assignment(raw.get(task, {})) for task in OLLAMA_TASK_KEYS}


def _ollama_scan_summary(manager: SettingsManager) -> str:
    scan_mode = manager.get_ollama_model_scan_mode()
    scan_path = manager.get_ollama_model_scan_path()
    cached_models = manager.get_ollama_scanned_models()
    has_saved_scan = bool(scan_mode or scan_path or manager.get_ollama_model_scan_locations())
    if not has_saved_scan:
        return "No saved scan yet. Run a system scan or choose a folder to build the local model list."
    if not cached_models:
        return "The last scan is saved, but it did not find any Ollama models."
    if scan_mode == "folder" and scan_path:
        return f"Using saved scan from folder: {scan_path}"
    if scan_mode == "system":
        return "Using saved system scan results from local Ollama locations."
    return "Using saved scanned model list."


def _llama_cpp_scan_summary(manager: SettingsManager) -> str:
    scan_mode = manager.get_llama_cpp_model_scan_mode()
    scan_path = manager.get_llama_cpp_model_scan_path()
    cached_models = manager.get_llama_cpp_scanned_models()
    has_saved_scan = bool(scan_mode or scan_path or manager.get_llama_cpp_model_scan_locations())
    if not has_saved_scan:
        return "No saved GGUF scan yet. Run a system scan or choose a folder to build the local model list."
    if not cached_models:
        return "The last GGUF scan is saved, but it did not find any models."
    if scan_mode == "folder" and scan_path:
        return f"Using saved scan from folder: {scan_path}"
    if scan_mode == "system":
        return "Using saved system scan results from local GGUF locations."
    return "Using saved scanned GGUF model list."


def settings_payload(manager: SettingsManager) -> dict[str, Any]:
    return {
        "showTokenCounter": manager.get_show_token_counter(),
        "enableSystemPrompt": manager.get_enable_system_prompt(),
        "notificationPreferences": manager.get_notification_preferences(),
        "githubTokenConfigured": bool(manager.get_github_token()),
    }


# Catalog-refresh feedback (mirrors the legacy discovery_status_label) is
# transient UI state, not a SettingsManager field - errors here are
# non-blocking background feedback, not a save failure (see
# backend/api/intents_settings_api_provider.py's load_api_models; contrast
# with save_api_configuration's notification-banner errors, matching
# legacy's own QMessageBox split). Module-level (not per-session) because
# it is a pure default value, never mutated in place.
_DEFAULT_API_CATALOG_STATE = {"status": "idle", "message": "Model catalog has not been refreshed yet."}


def _catalog_state_for(state: SettingsSessionState, provider: str) -> dict[str, str]:
    # Post-review fix (kept from the pre-split code): keyed by provider (not
    # one flat pair of cells) - a load_api_models call for provider A
    # resolving after the user has switched viewingApiProvider to B must not
    # paint A's status/message onto B's page, and B's own Load button must
    # not appear disabled just because A is still in flight. This also makes
    # the legacy widget's explicit "discard if the user switched providers
    # mid-flight" guard unnecessary here: each provider's outcome lands in
    # its own slot, so a switch-back to A later still shows A's real,
    # correct result rather than nothing.
    return state.api_catalog_state.get(provider, _DEFAULT_API_CATALOG_STATE)


def _build_settings_payload(manager: SettingsManager, state: SettingsSessionState) -> dict[str, Any]:
    payload = settings_payload(manager)
    payload["activeSection"] = state.active_section
    viewing_provider = state.viewing_api_provider
    payload["activeApiProvider"] = manager.get_api_provider()
    payload["viewingApiProvider"] = viewing_provider
    payload["apiBaseUrl"] = manager.get_api_base_url()
    payload["apiKeyConfigured"] = {
        "openai": bool(manager.get_openai_key()),
        "anthropic": bool(manager.get_anthropic_key()),
        "gemini": bool(manager.get_gemini_key()),
    }
    payload["apiModels"] = manager.get_api_models(viewing_provider)
    payload["apiModelCatalog"] = _api_model_catalog_for_wire(manager, viewing_provider)
    catalog_state = _catalog_state_for(state, viewing_provider)
    payload["apiCatalogStatus"] = catalog_state["status"]
    payload["apiCatalogMessage"] = catalog_state["message"]
    # Gemini has no live catalog-refresh endpoint wired here (matching
    # legacy: load_btn was never shown for Gemini) - its model choices
    # are this fixed, hand-maintained list. Sourced from api_provider.py
    # rather than duplicated in TypeScript so there is one place to
    # update when the list changes.
    payload["geminiStaticModels"] = list(api_provider.GEMINI_MODELS_STATIC)
    payload["geminiStaticImageModels"] = list(api_provider.GEMINI_IMAGE_MODELS_STATIC)

    payload["ollamaReasoningLevel"] = manager.get_ollama_reasoning_level()
    payload["ollamaCurrentModel"] = config.OLLAMA_MODELS.get(config.TASK_CHAT, "")
    payload["ollamaModelAssignments"] = _ollama_model_assignments_for_wire(manager)
    payload["ollamaScannedModels"] = manager.get_ollama_scanned_models()
    payload["ollamaScanSummary"] = _ollama_scan_summary(manager)
    payload["ollamaScanStatus"] = state.ollama_scan_status
    payload["ollamaPullStatus"] = state.ollama_pull_status
    payload["ollamaNotice"] = state.ollama_notice

    payload["llamaCppReasoningLevel"] = manager.get_llama_cpp_reasoning_level()
    # Staged (session-local), NOT manager.get_llama_cpp_chat_model_path()
    # - the field must show the in-progress draft, not the last-saved
    # value, exactly like the API-provider page's draftBaseUrl/
    # draftModels never reading back from the payload once edited.
    payload["llamaCppChatModelPath"] = state.llama_staged_chat_path
    payload["llamaCppTitleModelPath"] = state.llama_staged_title_path
    payload["llamaCppChatFormat"] = manager.get_llama_cpp_chat_format()
    payload["llamaCppNCtx"] = manager.get_llama_cpp_n_ctx()
    payload["llamaCppNGpuLayers"] = manager.get_llama_cpp_n_gpu_layers()
    payload["llamaCppNThreads"] = manager.get_llama_cpp_n_threads()
    payload["llamaCppScannedModels"] = manager.get_llama_cpp_scanned_models()
    payload["llamaCppScanSummary"] = _llama_cpp_scan_summary(manager)
    payload["llamaCppScanStatus"] = state.llama_scan_status
    payload["llamaCppNotice"] = state.llama_notice
    return payload


def register_settings(
    bus: SessionBus, manager: SettingsManager, notifications: NotificationState | None = None
) -> None:
    # notifications is optional only so the ~11 pre-R7.4a tests in
    # backend/tests/test_settings.py that call register_settings(bus,
    # manager) keep working unchanged - the real (and only) production call
    # site, backend/app.py's _configure_session, always passes a real one.
    #
    # activeSection is session-local UI navigation, not SettingsManager
    # state - the legacy bridge didn't persist it either (the dialog always
    # opened on General). viewingApiProvider is likewise session-local: the
    # legacy widget let a user free-browse a different provider's key/models
    # via its dropdown without persisting anything until Save - starts on
    # whichever provider is currently saved/active, matching the dialog's
    # legacy open state. llama_staged_*_path seed from the manager too,
    # matching legacy's own __init__ (a fresh session opens showing whatever
    # was last saved).
    state = SettingsSessionState(
        viewing_api_provider=manager.get_api_provider(),
        llama_staged_chat_path=manager.get_llama_cpp_chat_model_path(),
        llama_staged_title_path=manager.get_llama_cpp_title_model_override_path(),
    )

    bus.register_topic("app-settings", lambda: _build_settings_payload(manager, state))

    register_settings_general_intents(bus, manager, state)
    register_settings_api_provider_intents(bus, manager, notifications, state)
    register_settings_ollama_intents(bus, manager, state)
    register_settings_llama_cpp_intents(bus, manager, state)
