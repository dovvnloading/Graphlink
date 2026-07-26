"""Settings dialog: General + Integrations + API-provider + Ollama +
Llama.cpp pages (Qt-removal plan R2.5d, extended R7.4a, R7.4b, R7.4c).

Unlike composer.py/plugins.py this is a genuine REUSE, not a
reimplementation: SettingsManager (graphlink_licensing.py) and its own
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
pick_ollama_scan_folder below.

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
"""

from __future__ import annotations

import asyncio
import os
import threading
from typing import Any, Callable

import ollama

import api_provider
import graphlink_task_config as config
from graphlink_licensing import SettingsManager
from graphlink_model_catalog import AUTO_MODEL, INHERIT_MODEL

from backend import native_dialogs
from backend.events import SessionBus
from backend.notifications import NotificationState

# The six per-task model-assignment slots the API-provider page exposes,
# in the same order the legacy widget built its combo boxes - not load-
# bearing (payload is a dict), but keeps save_api_configuration's required-
# task walk in a stable, readable order.
_API_TASK_KEYS = (
    config.TASK_TITLE,
    config.TASK_CHAT,
    config.TASK_CHART,
    config.TASK_IMAGE_GEN,
    config.TASK_WEB_VALIDATE,
    config.TASK_WEB_SUMMARIZE,
)

# The three providers the page can actually display. Used to bound the
# per-provider catalog-state dict: without it, that dict is keyed by a
# raw client-supplied string and grows without limit.
_KNOWN_API_PROVIDERS = (
    config.API_PROVIDER_OPENAI,
    config.API_PROVIDER_ANTHROPIC,
    config.API_PROVIDER_GEMINI,
)

# Ollama's five per-task model-assignment slots - task_image_gen is
# deliberately absent (Ollama has no image-generation path, matching
# legacy: OLLAMA_TASKS in graphlink_settings_bridge.py has the same 5).
_OLLAMA_TASK_KEYS = (
    config.TASK_CHAT,
    config.TASK_TITLE,
    config.TASK_CHART,
    config.TASK_WEB_VALIDATE,
    config.TASK_WEB_SUMMARIZE,
)
_OLLAMA_REASONING_MODES = ("Thinking", "Quick")

# Same 2-mode set, distinct constant deliberately (not reused) - Llama.cpp
# and Ollama have entirely separate SettingsManager fields/api_provider
# global state for reasoning mode, so keeping the constants separate avoids
# an accidental coupling if one provider's valid-mode set ever diverges.
_LLAMA_CPP_REASONING_MODES = ("Thinking", "Quick")

# Every persistence-touching mutation runs in a worker thread
# (asyncio.to_thread) so SettingsManager._save_state's json-dump + fsync +
# atomic-replace never stalls the event loop (and with it, every session's
# WS traffic). The manager's mutations are unsynchronized read-modify-writes
# on shared state, so this lock restores the serialization that running them
# on the single-threaded loop used to provide for free. Module-level rather
# than per-manager: a real process has exactly one manager, and the
# throwaway managers tests create can share it harmlessly.
_manager_lock = threading.Lock()


def _apply(mutation: Callable[..., None], *args: Any) -> None:
    with _manager_lock:
        mutation(*args)


def _locked_llama_cpp_settings(manager: SettingsManager) -> dict[str, Any]:
    # Adversarial-review finding: SettingsManager.get_llama_cpp_settings()
    # does 7 separate unsynchronized dict reads - calling it outside
    # _manager_lock let a concurrent setLlamaCppNCtx/NGpuLayers/NThreads/
    # ChatFormat call (each correctly _apply-locked) interleave mid-read,
    # so a live-reapply could combine e.g. a brand-new n_ctx with a stale
    # chat_format. Doesn't corrupt the persisted file (this is a read, never
    # written back), but it's exactly the class of race this file's own
    # comments already claim to guard against elsewhere.
    with _manager_lock:
        return manager.get_llama_cpp_settings()


async def _republish_composer_reasoning(bus: SessionBus) -> None:
    """Push a fresh composer snapshot after a reasoning-mode change here.

    The composer's own Reasoning control displays the SAME persisted value
    these intents edit (backend/composer.py derives it rather than keeping a
    private copy, matching legacy). It therefore has to be told to rebuild,
    or the Settings dialog and the composer disagree until some unrelated
    composer event happens to republish. Guarded because a focused
    test_settings.py bus registers no composer topic.
    """
    if bus.has_topic("app-composer"):
        await bus.publish("app-composer")


async def apply_ollama_reasoning_mode(manager: SettingsManager, mode: str) -> None:
    """Persist `mode` and, if Ollama is still the live provider, re-apply it to
    api_provider's module state so the very next chat()/chat_stream() call
    picks it up. `mode` MUST already be "Thinking" or "Quick" - this function
    does not re-validate it (every caller's own normalization is total, so
    there is no path that could hand it anything else). Checked-and-applied
    inside the SAME asyncio.to_thread hop as a deliberate race-preemption:
    see set_ollama_reasoning_mode's inline comment for why. Shared by this
    file's own setOllamaReasoningMode intent (Settings-dialog Ollama page)
    and backend/composer.py's setReasoningLevel intent (composer's own
    quick-access popover) so both surfaces apply a change identically."""
    await asyncio.to_thread(_apply, manager.set_ollama_reasoning_mode, mode)

    def _reapply_if_ollama_is_still_the_live_provider() -> None:
        if api_provider.is_local_ollama_mode():
            api_provider.initialize_local_provider(config.LOCAL_PROVIDER_OLLAMA, {"reasoning_mode": mode})

    await asyncio.to_thread(_reapply_if_ollama_is_still_the_live_provider)


async def apply_llama_cpp_reasoning_mode(manager: SettingsManager, mode: str) -> str | None:
    """Same contract as apply_ollama_reasoning_mode, for Llama.cpp. Returns a
    human-readable failure message if the live re-apply fails (the mode is
    ALREADY persisted regardless - only the live effect is delayed to the
    next mode switch/restart), or None on success / when Llama.cpp is not the
    active provider. Returns rather than writes a notice directly: this
    file's own setLlamaCppReasoningMode intent writes the message into its
    page-local llama_notice cell; backend/composer.py's intent has no such
    cell and surfaces it through the shared NotificationState banner
    instead - that decision belongs to each caller, not to this function."""
    await asyncio.to_thread(_apply, manager.set_llama_cpp_reasoning_mode, mode)

    def _reapply_if_llama_cpp_is_still_the_live_provider() -> None:
        if api_provider.is_local_llama_cpp_mode():
            settings = _locked_llama_cpp_settings(manager)
            settings["reasoning_mode"] = mode
            api_provider.initialize_local_provider(config.LOCAL_PROVIDER_LLAMACPP, settings)

    try:
        await asyncio.to_thread(_reapply_if_llama_cpp_is_still_the_live_provider)
    except Exception as exc:  # noqa: BLE001 - no secret in this path (a local file path, not a credential)
        return f"Reasoning mode saved, but could not be applied to the live model: {exc}"
    return None


def _redact(text: str, secret: Any) -> str:
    # Post-review fix: some HTTP client libraries embed request parameters
    # (including a rejected API key) directly in exception text - the
    # legacy bridge this page replaces redacted for exactly this reason
    # (graphlink_settings_bridge.py: str(exc).replace(api_key, '***')).
    # Applied before any exception text reaches a WS-broadcast message, so
    # the write-only-key guarantee holds on the failure path too, not just
    # the happy path.
    #
    # Second-pass fix: the isinstance check is load-bearing, not defensive
    # noise. Intent arguments arrive as raw JSON off the wire with no
    # coercion anywhere in the dispatch path, and both call sites are
    # inside `except` blocks - where a TypeError from str.replace would
    # NOT be caught by its own `try` and would escape the handler, leaving
    # the catalog pinned at "loading" (which disables the Load button) for
    # the life of the process.
    if not isinstance(secret, str) or not secret:
        return text
    return text.replace(secret, "***")


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
    return {task: _flatten_ollama_assignment(raw.get(task, {})) for task in _OLLAMA_TASK_KEYS}


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
        "theme": manager.get_theme(),
        "showTokenCounter": manager.get_show_token_counter(),
        "enableSystemPrompt": manager.get_enable_system_prompt(),
        "notificationPreferences": manager.get_notification_preferences(),
        "githubTokenConfigured": bool(manager.get_github_token()),
    }


def register_settings(
    bus: SessionBus, manager: SettingsManager, notifications: NotificationState | None = None
) -> None:
    # notifications is optional only so the ~11 pre-R7.4a tests in
    # backend/tests/test_settings.py that call register_settings(bus,
    # manager) keep working unchanged - the real (and only) production call
    # site, backend/app.py's _configure_session, always passes a real one.
    # activeSection is session-local UI navigation, not SettingsManager
    # state - the legacy bridge didn't persist it either (the dialog always
    # opened on General). One mutable cell closed over by the builder and
    # its own intent, matching this field's single purpose.
    active_section = {"value": "general"}

    # viewingApiProvider is likewise session-local: the legacy widget let a
    # user free-browse a different provider's key/models via its dropdown
    # without persisting anything until Save - starts on whichever provider
    # is currently saved/active, matching the dialog's legacy open state.
    viewing_api_provider = {"value": manager.get_api_provider()}
    # Catalog-refresh feedback (mirrors the legacy discovery_status_label)
    # is transient UI state, not a SettingsManager field - errors here are
    # non-blocking background feedback, not a save failure (see
    # load_api_models below; contrast with save_api_configuration's
    # notification-banner errors, matching legacy's own QMessageBox split).
    #
    # Post-review fix: keyed by provider (not one flat pair of cells) - a
    # load_api_models call for provider A resolving after the user has
    # switched viewingApiProvider to B must not paint A's status/message
    # onto B's page, and B's own Load button must not appear disabled just
    # because A is still in flight. This also makes the legacy widget's
    # explicit "discard if the user switched providers mid-flight" guard
    # unnecessary here: each provider's outcome lands in its own slot, so a
    # switch-back to A later still shows A's real, correct result rather
    # than nothing.
    _default_catalog_state = {"status": "idle", "message": "Model catalog has not been refreshed yet."}
    api_catalog_state: dict[str, dict[str, str]] = {}

    def _catalog_state_for(provider: str) -> dict[str, str]:
        return api_catalog_state.get(provider, _default_catalog_state)

    # Ollama has exactly one page (unlike API-provider's 3), so a flat pair
    # of cells - not one keyed by anything client-supplied - carries no
    # analog of the per-provider bug the API-provider page's catalog state
    # had to be fixed for. scan and pull deliberately share one notice
    # field, matching the legacy bridge's own single self._notice exactly
    # (not a new gap: legacy never isolated scan-vs-pull messages either).
    ollama_scan_status = {"value": "idle"}
    ollama_pull_status = {"value": "idle"}
    ollama_notice = {"value": ""}

    # Llama.cpp mirrors Ollama's flat-cells shape (still exactly one page,
    # still no per-provider keying needed) plus two EXTRA cells with no
    # Ollama analog: the staged chat/title GGUF paths. Legacy's own bridge
    # kept these in-memory on the bridge instance and only persisted them
    # on an explicit saveLlamaCppSettings() call - Browse/select just
    # updates the staged draft, exactly like the API-provider page's own
    # draftApiKey/draftBaseUrl/draftModels (a local edit buffer, not
    # write-through) rather than Ollama's immediate-persist intents. Seeded
    # from the manager's already-persisted value so a fresh session opens
    # showing whatever was last saved, matching legacy's own __init__.
    llama_scan_status = {"value": "idle"}
    llama_notice = {"value": ""}
    llama_staged_chat_path = {"value": manager.get_llama_cpp_chat_model_path()}
    llama_staged_title_path = {"value": manager.get_llama_cpp_title_model_override_path()}

    def build_payload() -> dict[str, Any]:
        payload = settings_payload(manager)
        payload["activeSection"] = active_section["value"]
        viewing_provider = viewing_api_provider["value"]
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
        catalog_state = _catalog_state_for(viewing_provider)
        payload["apiCatalogStatus"] = catalog_state["status"]
        payload["apiCatalogMessage"] = catalog_state["message"]
        # Gemini has no live catalog-refresh endpoint wired here (matching
        # legacy: load_btn was never shown for Gemini) - its model choices
        # are this fixed, hand-maintained list. Sourced from api_provider.py
        # rather than duplicated in TypeScript so there is one place to
        # update when the list changes.
        payload["geminiStaticModels"] = list(api_provider.GEMINI_MODELS_STATIC)
        payload["geminiStaticImageModels"] = list(api_provider.GEMINI_IMAGE_MODELS_STATIC)

        payload["ollamaReasoningMode"] = manager.get_ollama_reasoning_mode()
        payload["ollamaCurrentModel"] = config.OLLAMA_MODELS.get(config.TASK_CHAT, "")
        payload["ollamaModelAssignments"] = _ollama_model_assignments_for_wire(manager)
        payload["ollamaScannedModels"] = manager.get_ollama_scanned_models()
        payload["ollamaScanSummary"] = _ollama_scan_summary(manager)
        payload["ollamaScanStatus"] = ollama_scan_status["value"]
        payload["ollamaPullStatus"] = ollama_pull_status["value"]
        payload["ollamaNotice"] = ollama_notice["value"]

        payload["llamaCppReasoningMode"] = manager.get_llama_cpp_reasoning_mode()
        # Staged (session-local), NOT manager.get_llama_cpp_chat_model_path()
        # - the field must show the in-progress draft, not the last-saved
        # value, exactly like the API-provider page's draftBaseUrl/
        # draftModels never reading back from the payload once edited.
        payload["llamaCppChatModelPath"] = llama_staged_chat_path["value"]
        payload["llamaCppTitleModelPath"] = llama_staged_title_path["value"]
        payload["llamaCppChatFormat"] = manager.get_llama_cpp_chat_format()
        payload["llamaCppNCtx"] = manager.get_llama_cpp_n_ctx()
        payload["llamaCppNGpuLayers"] = manager.get_llama_cpp_n_gpu_layers()
        payload["llamaCppNThreads"] = manager.get_llama_cpp_n_threads()
        payload["llamaCppScannedModels"] = manager.get_llama_cpp_scanned_models()
        payload["llamaCppScanSummary"] = _llama_cpp_scan_summary(manager)
        payload["llamaCppScanStatus"] = llama_scan_status["value"]
        payload["llamaCppNotice"] = llama_notice["value"]
        return payload

    bus.register_topic("app-settings", build_payload)

    async def set_active_section(section: str):
        active_section["value"] = str(section)
        await bus.publish("app-settings")

    # The topic builder (read path) stays on the loop, unlocked: field reads
    # are GIL-atomic, and every mutation republishes on completion, so a
    # snapshot that races a write is immediately superseded by a settled one.

    async def set_theme(theme: str):
        await asyncio.to_thread(_apply, manager.set_theme, str(theme))
        await bus.publish("app-settings")

    async def set_show_token_counter(enabled: bool):
        await asyncio.to_thread(_apply, manager.set_show_token_counter, bool(enabled))
        await bus.publish("app-settings")

    async def set_enable_system_prompt(enabled: bool):
        await asyncio.to_thread(_apply, manager.set_enable_system_prompt, bool(enabled))
        await bus.publish("app-settings")

    async def set_notification_preference(notification_type: str, enabled: bool):
        await asyncio.to_thread(
            _apply, manager.set_notification_preferences, {str(notification_type): bool(enabled)}
        )
        await bus.publish("app-settings")

    async def set_github_token(token: str):
        await asyncio.to_thread(_apply, manager.set_github_token, str(token))
        await bus.publish("app-settings")

    async def clear_github_token():
        await asyncio.to_thread(_apply, manager.set_github_token, "")
        await bus.publish("app-settings")

    async def set_viewing_api_provider(provider: str):
        viewing_api_provider["value"] = str(provider)
        await bus.publish("app-settings")

    async def load_api_models(provider: str, api_key: str, base_url: str = ""):
        # Second-pass fix: coerce every wire argument BEFORE it is used for
        # anything - `provider` in particular is used as a dict key below,
        # and an unhashable JSON value (array/object) would raise TypeError
        # straight out of the handler. Intent args reach here as raw JSON
        # with no validation anywhere in the dispatch path.
        provider = str(provider)
        base_url = str(base_url).strip()

        # Gemini has no live catalog endpoint wired (legacy never showed the
        # Load button for it either) - a stale/misbehaving client asking
        # anyway gets a clean error rather than a confusing initialize_api
        # call with the wrong semantics.
        if provider not in (config.API_PROVIDER_OPENAI, config.API_PROVIDER_ANTHROPIC):
            # Only a provider the UI can actually VIEW gets a stored status
            # slot: build_payload surfaces the slot for the viewed provider
            # only, so a slot for an unknown string could never be read -
            # it would just grow this dict without bound on a key the client
            # controls.
            if provider in _KNOWN_API_PROVIDERS:
                api_catalog_state[provider] = {
                    "status": "error",
                    "message": f"{provider} does not support catalog refresh.",
                }
                await bus.publish("app-settings")
            return

        if provider == config.API_PROVIDER_OPENAI and not base_url:
            # Restores a guard both legacy predecessors had. Without it an
            # empty Base URL silently falls through to api_provider's own
            # "https://api.openai.com/v1" default, sending a key meant for a
            # self-hosted proxy to OpenAI's public API instead.
            api_catalog_state[provider] = {
                "status": "error",
                "message": "Please enter the Base URL for the OpenAI-compatible provider.",
            }
            await bus.publish("app-settings")
            return

        # This page deliberately never sends the saved key to the client
        # (see the module docstring), so a user who already has a key saved
        # opens the dialog with a blank field. Legacy pre-filled the field
        # and therefore always had a key to test with; the write-only port
        # has to make up for that here instead, or Load is simply unusable
        # for every already-configured user. Falling back server-side keeps
        # the plaintext key off the wire entirely.
        key = str(api_key).strip()
        if not key:
            key = (
                manager.get_openai_key()
                if provider == config.API_PROVIDER_OPENAI
                else manager.get_anthropic_key()
            )
        if not key:
            api_catalog_state[provider] = {"status": "error", "message": "Please enter the API Key."}
            await bus.publish("app-settings")
            return

        api_catalog_state[provider] = {
            "status": "loading",
            "message": "Contacting the provider... you can keep editing other settings.",
        }
        await bus.publish("app-settings")

        try:
            # Discovery deliberately exercises the same provider-init path
            # Save uses, so a successful catalog load doubles as a
            # connection check - matching legacy's ApiModelLoadWorker.
            await asyncio.to_thread(
                api_provider.initialize_api,
                provider,
                key,
                base_url if provider == config.API_PROVIDER_OPENAI else None,
            )
            descriptors = await asyncio.to_thread(api_provider.get_available_model_descriptors)
            # Post-review fix: api_provider's provider globals are process-
            # wide (not per-request), and initialize_api/get_available_
            # model_descriptors are two separate asyncio.to_thread hops with
            # an await gap between them - a concurrent loadApiModels/
            # saveApiConfiguration call for a DIFFERENT provider landing in
            # that gap would repoint the globals before the descriptors read
            # runs, so descriptors could describe the wrong provider. This
            # is the same class of race api_provider.py's own
            # _snapshot_provider_state() exists to prevent for chat()/
            # generate_image() - get_available_model_descriptors() has no
            # snapshot-based equivalent, so the narrowest fix available at
            # this call site is a post-hoc consistency check: if the global
            # provider type no longer matches what THIS call just
            # requested, treat the result as aborted rather than silently
            # persisting/reporting it under the wrong provider's name.
            if api_provider.API_PROVIDER_TYPE != provider:
                api_catalog_state[provider] = {
                    "status": "error",
                    "message": "Catalog refresh aborted - another request changed the active provider. Try again.",
                }
                await bus.publish("app-settings")
                return
            normalized = [
                {
                    "model_id": descriptor.model_id,
                    "provider": descriptor.provider,
                    "capabilities": sorted(descriptor.capabilities),
                    "ready": descriptor.ready,
                    "available": descriptor.available,
                }
                for descriptor in descriptors
            ]
        except Exception as exc:  # noqa: BLE001 - redacted, then surfaced, matching legacy's error label
            # Redact the EFFECTIVE key (which may be the stored one picked
            # up by the fallback above), not the submitted argument - the
            # effective key is what actually reached the provider SDK and
            # so is what can come back embedded in its exception text.
            message = _redact(str(exc), key)
            api_catalog_state[provider] = {
                "status": "error",
                "message": f"Catalog refresh failed: {message}\nSaved/custom model IDs remain usable if the endpoint supports them.",
            }
            await bus.publish("app-settings")
            return

        await asyncio.to_thread(_apply, manager.set_api_model_catalog, normalized, provider)
        api_catalog_state[provider] = {
            "status": "success",
            "message": f"Catalog refreshed - {len(normalized)} model(s) available from {provider}.",
        }
        await bus.publish("app-settings")

    async def save_api_configuration(provider: str, base_url: str, api_key: str, models_by_task: dict[str, str]):
        provider = str(provider)
        base_url = str(base_url).strip()
        api_key = str(api_key).strip()
        # Post-review fix: a non-dict 4th argument (e.g. a WS client sending
        # null) used to raise AttributeError out of the required-task loop
        # below, uncaught, bypassing this intent's own validation-notice
        # path entirely in favor of a generic "intent failed" WS error.
        # Coercing to {} here routes it back through the normal "please
        # select a model" warning instead.
        if not isinstance(models_by_task, dict):
            models_by_task = {}

        if provider == config.API_PROVIDER_OPENAI and not base_url:
            # Same guard as load_api_models, and the more important of the
            # two: an empty base_url persisted here is read straight back
            # by SettingsManager.get_api_base_url (whose own default only
            # applies to a MISSING key, not a stored ""), so every later
            # provider bootstrap silently re-points the saved key at
            # api.openai.com.
            if notifications is not None:
                notifications.show("Please enter the Base URL for the OpenAI-compatible provider.", "warning")
                await bus.publish("notification")
            return

        if not api_key:
            if notifications is not None:
                notifications.show("Please enter your API Key.", "warning")
                await bus.publish("notification")
            return

        required_tasks = [
            task for task in _API_TASK_KEYS if not (provider == config.API_PROVIDER_ANTHROPIC and task == config.TASK_IMAGE_GEN)
        ]
        for task in required_tasks:
            if not str(models_by_task.get(task, "")).strip():
                if notifications is not None:
                    notifications.show(f"Please select a model for task: {task}", "warning")
                    await bus.publish("notification")
                return

        try:
            await asyncio.to_thread(
                api_provider.initialize_api,
                provider,
                api_key,
                base_url if provider == config.API_PROVIDER_OPENAI else None,
            )
        except Exception as exc:  # noqa: BLE001 - redacted, then surfaced, matching legacy's error dialog
            if notifications is not None:
                notifications.show(f"Failed to initialize the API provider: {_redact(str(exc), api_key)}", "error")
                await bus.publish("notification")
            return

        # Commit only after provider initialization succeeds - a rejected
        # key or endpoint must not overwrite the last known-good profile
        # (matching legacy save_settings's own ordering exactly).
        normalized_models = {
            str(task): str(model_id).strip()
            for task, model_id in (models_by_task or {}).items()
            if str(model_id).strip()
        }

        def _persist() -> None:
            # Post-review fix: the other two providers' "keep as-is" keys
            # are now read HERE, inside _manager_lock (via _apply), not on
            # the event loop before this thread was dispatched. The old
            # read-before-await-then-write-after ordering was a lost-update
            # race: two saveApiConfiguration calls for different providers
            # (e.g. two browser tabs on the same default session) could
            # interleave so that the second call's stale pre-read of the
            # first call's key got written back over the first call's own,
            # freshly-persisted value. Reading and writing inside the same
            # locked critical section makes this atomic with respect to
            # every other _apply()-guarded mutation.
            openai_key = api_key if provider == config.API_PROVIDER_OPENAI else manager.get_openai_key()
            anthropic_key = api_key if provider == config.API_PROVIDER_ANTHROPIC else manager.get_anthropic_key()
            gemini_key = api_key if provider == config.API_PROVIDER_GEMINI else manager.get_gemini_key()
            manager.set_api_settings(provider, base_url, openai_key, anthropic_key, gemini_key)
            manager.set_api_models(normalized_models, provider)
            for task, model_id in normalized_models.items():
                api_provider.set_task_model(task, model_id)

        await asyncio.to_thread(_apply, _persist)
        if notifications is not None:
            notifications.show(f"API settings for {provider} have been saved.", "success")
            await bus.publish("notification")
        await bus.publish("app-settings")

    async def reset_api_settings():
        def _reset() -> None:
            manager.reset_api_settings()
            # SettingsManager.reset_api_settings intentionally leaves
            # api_model_catalog_by_provider alone (it is shared with the
            # legacy Qt bridge), but "All API settings have been cleared"
            # has to mean it - otherwise the page keeps offering the old
            # provider's model catalog after the reset. Clearing through
            # the existing public setter rather than editing the shared
            # legacy module.
            for known in _KNOWN_API_PROVIDERS:
                manager.set_api_model_catalog([], known)

        await asyncio.to_thread(_apply, _reset)
        # Transient per-provider status/message is UI state, not manager
        # state, so it has to be cleared here too - otherwise the previous
        # "Catalog refreshed - N model(s)" success banner survives a reset
        # that just deleted that very catalog.
        api_catalog_state.clear()
        # Legacy's reset_settings snapped the provider dropdown back to
        # OpenAI-Compatible; reset_api_settings restores exactly that as
        # the persisted provider, so the viewed provider follows it.
        viewing_api_provider["value"] = manager.get_api_provider()
        if notifications is not None:
            notifications.show("All API settings have been cleared.", "success")
            await bus.publish("notification")
        await bus.publish("app-settings")

    async def set_ollama_reasoning_mode(mode: str):
        # Checked and applied inside the SAME to_thread hop, not split across
        # an await boundary: a concurrent mode switch (the toolbar's provider
        # selector) landing in a gap between a separate check and a later
        # apply could otherwise force the live provider back to Ollama after
        # the user had already switched away - the same class of
        # stale-check-then-await race the R7.4a audit found and fixed
        # elsewhere in this file.
        # Legacy-parity note (corrected after adversarial review): this live
        # re-apply is NOT what legacy's own settings dialog did.
        # graphlink_settings_bridge.py's setOllamaReasoningMode called
        # _reinitialize_main_window_agent() unconditionally, which just
        # rebuilds ChatAgent - it never touched the live
        # OLLAMA_REASONING_MODE global api_provider.py's chat dispatch
        # actually reads. Legacy's ONLY path that ever updated that global
        # was a completely separate control - the composer toolbar's
        # setReasoningLevel - which the Settings dialog was never wired to.
        # So in real legacy usage, changing reasoning mode via Settings
        # persisted the value but left the live think= kwarg stale until the
        # next provider-mode switch or restart. This re-apply is a genuine
        # fix for that gap, not a port of existing legacy behavior - gated on
        # is_local_ollama_mode() so it can't be the mechanism that forces a
        # user who already switched to a different provider back onto
        # Ollama.
        #
        # R7.5d: this apply/re-apply sequence is now shared with
        # backend/composer.py's own setReasoningLevel intent - see
        # apply_ollama_reasoning_mode's own docstring above.
        mode = str(mode)
        if mode not in _OLLAMA_REASONING_MODES:
            return
        await apply_ollama_reasoning_mode(manager, mode)
        await bus.publish("app-settings")
        await _republish_composer_reasoning(bus)

    async def set_ollama_model_assignment(task: str, value: str):
        # Coerced before use, matching every intent in this file post the
        # R7.4a audit: task ends up as a dict key below, and a client can
        # send any JSON type over the wire with zero validation anywhere
        # in the dispatch path.
        task = str(task)
        value = str(value).strip()
        if task not in _OLLAMA_TASK_KEYS:
            return
        if task == config.TASK_CHAT and value == INHERIT_MODEL:
            # Adversarial-review finding: task_chat has no "inherit"
            # concept - it IS the base chat model, and its <select> in
            # SettingsDialog.tsx never renders an "inherit" option (task !==
            # "task_chat" guards it there). Without this normalization, a
            # stray or hand-edited "inherit" for this one task would persist
            # fine but leave the frontend's <select> showing a value with no
            # matching <option> - falling back here to auto instead, which
            # the UI CAN represent.
            value = AUTO_MODEL
        if not value or value in (INHERIT_MODEL, AUTO_MODEL):
            assignment = {"mode": value or AUTO_MODEL, "model_id": ""}
        else:
            assignment = {"mode": "explicit", "model_id": value}

        def _persist() -> None:
            # Read-modify-write, all inside this one _apply-locked closure -
            # not read-before-await-then-write-after. Splitting those across
            # an await boundary is exactly the R7.4a save_api_configuration
            # race: a concurrent assignment change for a DIFFERENT task
            # (two browser tabs on the same session) landing in the gap
            # would get its own freshly-persisted change silently reverted
            # by this call's stale pre-read of the whole assignments dict.
            assignments = manager.get_ollama_model_assignments()
            assignments[task] = assignment
            manager.set_ollama_model_assignments(assignments)
            config.sync_ollama_task_models(manager)
            if task == config.TASK_CHAT and assignment["mode"] == "explicit":
                config.set_current_model(assignment["model_id"])

        await asyncio.to_thread(_apply, _persist)
        await bus.publish("app-settings")

    async def _run_ollama_scan(scan_path: str | None) -> None:
        # Extracted (R7.4c) from what used to be scan_ollama_system's own
        # monolithic body, unchanged in behavior - factored out so
        # pick_ollama_scan_folder (below) can share it with a real path
        # instead of duplicating the scan/persist/report sequence.
        ollama_scan_status["value"] = "running"
        ollama_notice["value"] = ""
        await bus.publish("app-settings")

        try:
            # scan_local_ollama_models never raises for "Ollama not
            # running" - that's reported via the returned server_reachable/
            # server_error fields (which legacy's own worker/bridge never
            # surfaced either; matched here, not newly dropped). Only a
            # genuine, unexpected exception (e.g. a filesystem error
            # walking a manifest folder) reaches this except.
            results = await asyncio.to_thread(api_provider.scan_local_ollama_models, scan_path)
        except Exception as exc:  # noqa: BLE001 - no secret in this path to redact
            ollama_scan_status["value"] = "error"
            ollama_notice["value"] = f"Scan failed: {exc}"
            await bus.publish("app-settings")
            return

        def _persist() -> None:
            manager.set_ollama_model_scan_cache(
                results.get("models", []),
                results.get("scan_mode", ""),
                results.get("scan_path", ""),
                results.get("locations", []),
            )
            config.sync_ollama_task_models(manager)

        try:
            # Adversarial-review finding: set_ollama_model_scan_cache does a
            # real disk write (SettingsManager._save_state - json dump +
            # fsync + atomic replace), which CAN fail (locked file,
            # permission denied, disk full). Before this fix, a failure here
            # propagated uncaught out of the intent handler - the WS layer
            # logs and survives it, but ollama_scan_status["value"] was left
            # at "running" forever, so the "already running" reentrancy
            # guard at the top of this function would silently no-op every
            # future scan for the rest of the session. Legacy never had this
            # failure mode: its reentrancy check was tied to the QThread
            # object, nulled at the start of its finished-handler regardless
            # of whether persistence succeeded.
            await asyncio.to_thread(_apply, _persist)
        except Exception as exc:  # noqa: BLE001 - no secret in this path to redact
            ollama_scan_status["value"] = "error"
            ollama_notice["value"] = f"Scan failed: {exc}"
            await bus.publish("app-settings")
            return
        ollama_scan_status["value"] = "done"
        await bus.publish("app-settings")

    async def scan_ollama_system():
        # Check-then-set with no await between them - safe under asyncio's
        # single-threaded event loop (unlike the two genuinely async-gapped
        # races above), matching legacy's own isRunning()-guarded no-op.
        if ollama_scan_status["value"] == "running":
            return
        await _run_ollama_scan(None)

    async def pick_ollama_scan_folder():
        # R7.4c: the retroactive un-defer of this page's own "Scan
        # Folder..." button, now that native_dialogs exists. Matches
        # legacy's pickOllamaScanFolder(): a cancelled dialog is a quiet
        # no-op, not an error. The reentrancy guard is set BEFORE the
        # (blocking, potentially long-lived-until-the-user-decides) native
        # dialog opens, not after - two near-simultaneous clicks must not
        # both reach a native file-dialog call.
        if ollama_scan_status["value"] == "running":
            return
        ollama_scan_status["value"] = "running"
        await bus.publish("app-settings")
        directory = manager.get_ollama_model_scan_path() or os.path.expanduser("~")
        try:
            # Adversarial-review finding: the native dialog call itself can
            # raise (a per-platform GTK/COM/file-type-parsing failure inside
            # pywebview's create_file_dialog - confirmed reachable via its
            # own source, not theoretical). Uncaught, this would strand the
            # reentrancy gate at "running" forever - the exact class of bug
            # already fixed twice in this file for the SCAN/PERSIST steps,
            # reintroduced here via the dialog call that precedes them.
            folder = await native_dialogs.pick_folder(directory=directory)
        except Exception as exc:  # noqa: BLE001 - a local folder path, not a credential
            ollama_scan_status["value"] = "error"
            ollama_notice["value"] = f"Could not open the folder picker: {exc}"
            await bus.publish("app-settings")
            return
        if not folder:
            ollama_scan_status["value"] = "idle"
            await bus.publish("app-settings")
            return
        await _run_ollama_scan(folder)

    async def pull_ollama_model(model_name: str):
        model_name = str(model_name).strip()
        if not model_name:
            ollama_notice["value"] = "Model name cannot be empty."
            await bus.publish("app-settings")
            return
        if ollama_pull_status["value"] == "running":
            return
        ollama_pull_status["value"] = "running"
        ollama_notice["value"] = ""
        await bus.publish("app-settings")

        try:
            await asyncio.to_thread(ollama.pull, model_name)
        except Exception as exc:  # noqa: BLE001 - mapped to a friendly message, matching ModelPullWorkerThread's own 3-way mapping; no secret here to redact (a model name is not a credential)
            text = str(exc).lower()
            if "not found" in text:
                message = f"Model '{model_name}' was not found on the Ollama hub."
            elif "connection refused" in text:
                message = "Could not connect to Ollama. Is Ollama running?"
            else:
                message = f"An unexpected error occurred: {exc}"
            ollama_pull_status["value"] = "error"
            ollama_notice["value"] = message
            await bus.publish("app-settings")
            return

        def _persist() -> None:
            api_provider.invalidate_ollama_capability_cache(model_name)
            config.set_current_model(model_name)

        try:
            # Belt-and-suspenders for the same reentrancy-gate hazard fixed
            # in scan_ollama_system's own _persist call above - unlike that
            # one, neither invalidate_ollama_capability_cache nor
            # set_current_model do disk I/O today (both are in-memory-only
            # global mutations), so this has no realistic failure mode right
            # now, but a stranded "running" gate is bad enough - and cheap
            # enough to guard against - that this doesn't wait for one of
            # them to grow a fallible path first.
            await asyncio.to_thread(_apply, _persist)
        except Exception as exc:  # noqa: BLE001 - no secret here to redact (a model name is not a credential)
            ollama_pull_status["value"] = "error"
            ollama_notice["value"] = f"An unexpected error occurred: {exc}"
            await bus.publish("app-settings")
            return
        ollama_pull_status["value"] = "done"
        ollama_notice["value"] = ""
        await bus.publish("app-settings")

    async def set_llama_cpp_reasoning_mode(mode: str):
        # Same shape as set_ollama_reasoning_mode's own live re-apply: gated
        # on is_local_llama_cpp_mode(), checked and applied inside the SAME
        # to_thread hop so a concurrent provider-mode switch can't be
        # clobbered back to Llama.cpp. Unlike Ollama's version, this
        # genuinely CAN fail: initialize_local_provider's Llama.cpp branch
        # re-validates chat_model_path (must still be a real, existing .gguf
        # file) every time it runs - if that file was deleted/moved from
        # under an already-active session since Llama.cpp was last
        # activated, this raises. The mode is already persisted regardless,
        # so a failure here only means it takes effect on the next mode
        # switch/restart instead of immediately - not a lost setting.
        #
        # R7.5d: this apply/re-apply sequence is now shared with
        # backend/composer.py's own setReasoningLevel intent - see
        # apply_llama_cpp_reasoning_mode's own docstring above.
        mode = str(mode)
        if mode not in _LLAMA_CPP_REASONING_MODES:
            return
        failure = await apply_llama_cpp_reasoning_mode(manager, mode)
        if failure is not None:
            llama_notice["value"] = failure
        # NOTE: on success, do NOT clear llama_notice to "" - this preserves
        # the exact pre-existing behavior (today's code never clears it on
        # success either); do not "improve" this as a drive-by fix in this
        # increment.
        await bus.publish("app-settings")
        await _republish_composer_reasoning(bus)

    async def set_llama_cpp_chat_format(chat_format: str):
        await asyncio.to_thread(_apply, manager.set_llama_cpp_chat_format, str(chat_format))
        await bus.publish("app-settings")

    def _set_llama_cpp_runtime_field(field: str, value: int) -> None:
        # Read-modify-write entirely inside this one _apply-locked closure -
        # SettingsManager.set_llama_cpp_runtime requires all 4 kwargs every
        # call (no partial-update variant), so the "current" read must
        # happen in the SAME locked critical section as the write, not
        # before scheduling it - exactly the class of race the R7.4a
        # save_api_configuration bug and R7.4b's model-assignment test both
        # already covered for other read-modify-writes in this file.
        current = manager.get_llama_cpp_settings()
        current[field] = value
        manager.set_llama_cpp_runtime(
            n_ctx=current["n_ctx"],
            n_gpu_layers=current["n_gpu_layers"],
            n_threads=current["n_threads"],
            chat_format=current["chat_format"],
        )

    async def set_llama_cpp_n_ctx(n_ctx: int):
        try:
            n_ctx = int(n_ctx)
        except (TypeError, ValueError):
            return
        await asyncio.to_thread(_apply, _set_llama_cpp_runtime_field, "n_ctx", n_ctx)
        await bus.publish("app-settings")

    async def set_llama_cpp_n_gpu_layers(n_gpu_layers: int):
        try:
            n_gpu_layers = int(n_gpu_layers)
        except (TypeError, ValueError):
            return
        await asyncio.to_thread(_apply, _set_llama_cpp_runtime_field, "n_gpu_layers", n_gpu_layers)
        await bus.publish("app-settings")

    async def set_llama_cpp_n_threads(n_threads: int):
        try:
            n_threads = int(n_threads)
        except (TypeError, ValueError):
            return
        await asyncio.to_thread(_apply, _set_llama_cpp_runtime_field, "n_threads", n_threads)
        await bus.publish("app-settings")

    def _initial_gguf_directory(staged_path: str) -> str:
        # Matches legacy's own _pick_gguf_file: prefer the staged path's
        # own directory (so re-browsing starts where the current selection
        # already lives), else a saved scan path, else home - never leaves
        # it to whatever the OS defaults to.
        if staged_path:
            directory = os.path.dirname(staged_path)
            if directory:
                return directory
        return manager.get_llama_cpp_model_scan_path() or os.path.expanduser("~")

    async def pick_llama_cpp_chat_model_file():
        # Stages only - matches legacy's pickLlamaCppChatModelFile(), which
        # never persists until the user clicks Save. A cancelled dialog
        # (path is None) is a quiet no-op.
        directory = _initial_gguf_directory(llama_staged_chat_path["value"])
        path = await native_dialogs.pick_file(
            file_types=("GGUF files (*.gguf)", "All Files (*.*)"), directory=directory
        )
        if path:
            llama_staged_chat_path["value"] = path
            await bus.publish("app-settings")

    async def pick_llama_cpp_title_model_file():
        # Legacy's own initial-dir fallback order for the TITLE picker
        # specifically: the staged title path, else the staged CHAT path
        # (not the title one) - matches _pick_gguf_file's caller passing
        # `self._llama_title_model_path or self._llama_chat_model_path`.
        directory = _initial_gguf_directory(
            llama_staged_title_path["value"] or llama_staged_chat_path["value"]
        )
        path = await native_dialogs.pick_file(
            file_types=("GGUF files (*.gguf)", "All Files (*.*)"), directory=directory
        )
        if path:
            llama_staged_title_path["value"] = path
            await bus.publish("app-settings")

    async def set_llama_cpp_chat_model_path(path: str):
        # The non-native counterpart (selecting from the scanned-models
        # dropdown) - also stages only, matching pick_llama_cpp_chat_model_file.
        llama_staged_chat_path["value"] = str(path).strip()
        await bus.publish("app-settings")

    async def set_llama_cpp_title_model_path(path: str):
        llama_staged_title_path["value"] = str(path).strip()
        await bus.publish("app-settings")

    async def _run_llama_cpp_scan(scan_path: str | None) -> None:
        llama_scan_status["value"] = "running"
        llama_notice["value"] = ""
        await bus.publish("app-settings")

        try:
            # Unlike scan_local_ollama_models, this DOES raise for a real,
            # reachable failure: an explicit scan_path that doesn't exist or
            # isn't a directory (api_provider.py's own scan_local_llama_cpp_models).
            results = await asyncio.to_thread(api_provider.scan_local_llama_cpp_models, scan_path)
        except Exception as exc:  # noqa: BLE001 - no secret in this path (a local folder path, not a credential)
            llama_scan_status["value"] = "error"
            llama_notice["value"] = f"Scan failed: {exc}"
            await bus.publish("app-settings")
            return

        def _persist() -> None:
            manager.set_llama_cpp_model_scan_cache(
                results.get("models", []),
                results.get("scan_mode", ""),
                results.get("scan_path", ""),
                results.get("locations", []),
            )

        try:
            # Same reentrancy-gate-recovery fix as scan_ollama_system's own
            # persist step - a disk-write failure here must not strand this
            # scan status at "running" forever.
            await asyncio.to_thread(_apply, _persist)
        except Exception as exc:  # noqa: BLE001 - no secret in this path
            llama_scan_status["value"] = "error"
            llama_notice["value"] = f"Scan failed: {exc}"
            await bus.publish("app-settings")
            return

        llama_scan_status["value"] = "done"
        if results.get("truncated"):
            # A deliberate small improvement over legacy (never surfaced
            # this): the scan collector is bounded (50k directories / 30s -
            # see api_provider.py's _GGUF_SCAN_MAX_DIRECTORIES/_MAX_SECONDS)
            # specifically because the default system-wide roots include
            # the user's whole Downloads/Documents/Desktop trees, which can
            # be huge. Silently reporting an incomplete scan as complete
            # would be misleading; this doesn't change any persisted field.
            llama_notice["value"] = "Scan stopped early (too many folders or took too long) - results may be incomplete."
        await bus.publish("app-settings")

    async def scan_llama_cpp_system():
        if llama_scan_status["value"] == "running":
            return
        await _run_llama_cpp_scan(None)

    async def pick_llama_cpp_scan_folder():
        if llama_scan_status["value"] == "running":
            return
        llama_scan_status["value"] = "running"
        await bus.publish("app-settings")
        directory = manager.get_llama_cpp_model_scan_path() or os.path.expanduser("~")
        try:
            # Same reentrancy-gate hazard fixed above for pick_ollama_scan_folder.
            folder = await native_dialogs.pick_folder(directory=directory)
        except Exception as exc:  # noqa: BLE001 - a local folder path, not a credential
            llama_scan_status["value"] = "error"
            llama_notice["value"] = f"Could not open the folder picker: {exc}"
            await bus.publish("app-settings")
            return
        if not folder:
            llama_scan_status["value"] = "idle"
            await bus.publish("app-settings")
            return
        await _run_llama_cpp_scan(folder)

    async def save_llama_cpp_settings():
        # Sequencing matches legacy's saveLlamaCppSettings() exactly:
        # (1) validate the staged paths locally - chat is required, title
        # is optional but validated if non-empty; (2) if Llama.cpp is the
        # CURRENTLY LIVE provider, re-initialize it with the new settings,
        # aborting without persisting anything on failure (a real, useful
        # abort: this is what catches "that .gguf file doesn't actually
        # exist" before it's saved); (3) only then persist.
        chat_path = llama_staged_chat_path["value"].strip()
        title_path = llama_staged_title_path["value"].strip()

        # Legacy-parity fix: restores the 5 distinct legacy error messages
        # (graphlink_settings_bridge.py's saveLlamaCppSettings) instead of 2
        # generic ones - a user gets the ACTUAL problem (empty vs. not-found
        # vs. wrong-extension), not just "must be a real .gguf file" for all
        # three.
        if not chat_path:
            llama_notice["value"] = "Chat Model File cannot be empty."
            await bus.publish("app-settings")
            return
        if not os.path.isfile(chat_path):
            llama_notice["value"] = f"Chat model file was not found: {chat_path}"
            await bus.publish("app-settings")
            return
        if not chat_path.lower().endswith(".gguf"):
            llama_notice["value"] = "Chat Model File must point to a .gguf file."
            await bus.publish("app-settings")
            return
        if title_path:
            if not os.path.isfile(title_path):
                llama_notice["value"] = f"Chat naming model file was not found: {title_path}"
                await bus.publish("app-settings")
                return
            if not title_path.lower().endswith(".gguf"):
                llama_notice["value"] = "Chat Naming File must point to a .gguf file."
                await bus.publish("app-settings")
                return

        def _maybe_reapply_live() -> None:
            # Checked and applied inside the SAME to_thread hop - the same
            # race-preemption shape as set_llama_cpp_reasoning_mode's own
            # live re-apply above.
            if api_provider.is_local_llama_cpp_mode():
                settings = _locked_llama_cpp_settings(manager)
                settings["chat_model_path"] = chat_path
                settings["title_model_path"] = title_path
                api_provider.initialize_local_provider(config.LOCAL_PROVIDER_LLAMACPP, settings)

        try:
            await asyncio.to_thread(_maybe_reapply_live)
        except Exception as exc:  # noqa: BLE001 - no secret in this path (local file paths, not credentials)
            llama_notice["value"] = f"Invalid Llama.cpp configuration: {exc}"
            await bus.publish("app-settings")
            return

        def _persist() -> None:
            manager.set_llama_cpp_chat_model_path(chat_path)
            manager.set_llama_cpp_title_model_path(title_path)

        await asyncio.to_thread(_apply, _persist)
        llama_notice["value"] = ""
        await bus.publish("app-settings")

    bus.register_intent("app-settings", "setActiveSection", set_active_section)
    bus.register_intent("app-settings", "setTheme", set_theme)
    bus.register_intent("app-settings", "setShowTokenCounter", set_show_token_counter)
    bus.register_intent("app-settings", "setEnableSystemPrompt", set_enable_system_prompt)
    bus.register_intent("app-settings", "setNotificationPreference", set_notification_preference)
    bus.register_intent("app-settings", "setGithubToken", set_github_token)
    bus.register_intent("app-settings", "clearGithubToken", clear_github_token)
    bus.register_intent("app-settings", "setViewingApiProvider", set_viewing_api_provider)
    bus.register_intent("app-settings", "loadApiModels", load_api_models)
    bus.register_intent("app-settings", "saveApiConfiguration", save_api_configuration)
    bus.register_intent("app-settings", "resetApiSettings", reset_api_settings)
    bus.register_intent("app-settings", "setOllamaReasoningMode", set_ollama_reasoning_mode)
    bus.register_intent("app-settings", "setOllamaModelAssignment", set_ollama_model_assignment)
    bus.register_intent("app-settings", "scanOllamaSystem", scan_ollama_system)
    bus.register_intent("app-settings", "pullOllamaModel", pull_ollama_model)
    bus.register_intent("app-settings", "pickOllamaScanFolder", pick_ollama_scan_folder)
    bus.register_intent("app-settings", "setLlamaCppReasoningMode", set_llama_cpp_reasoning_mode)
    bus.register_intent("app-settings", "setLlamaCppChatFormat", set_llama_cpp_chat_format)
    bus.register_intent("app-settings", "setLlamaCppNCtx", set_llama_cpp_n_ctx)
    bus.register_intent("app-settings", "setLlamaCppNGpuLayers", set_llama_cpp_n_gpu_layers)
    bus.register_intent("app-settings", "setLlamaCppNThreads", set_llama_cpp_n_threads)
    bus.register_intent("app-settings", "pickLlamaCppChatModelFile", pick_llama_cpp_chat_model_file)
    bus.register_intent("app-settings", "pickLlamaCppTitleModelFile", pick_llama_cpp_title_model_file)
    bus.register_intent("app-settings", "setLlamaCppChatModelPath", set_llama_cpp_chat_model_path)
    bus.register_intent("app-settings", "setLlamaCppTitleModelPath", set_llama_cpp_title_model_path)
    bus.register_intent("app-settings", "scanLlamaCppSystem", scan_llama_cpp_system)
    bus.register_intent("app-settings", "pickLlamaCppScanFolder", pick_llama_cpp_scan_folder)
    bus.register_intent("app-settings", "saveLlamaCppSettings", save_llama_cpp_settings)
