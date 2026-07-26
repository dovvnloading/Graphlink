"""Settings dialog: General + Integrations + API-provider pages
(Qt-removal plan R2.5d, extended R7.4a).

Unlike composer.py/plugins.py this is a genuine REUSE, not a
reimplementation: SettingsManager (graphlink_licensing.py) and its own
imports (graphlink_secrets, graphlink_model_catalog) carry zero PySide6
coupling, confirmed via a runtime sys.modules check in
test_settings_never_imports_qt below. api_provider.py and
graphlink_task_config.py (this file's two new R7.4a imports) are
confirmed Qt-free repo-root survivor modules (Qt-removal plan R7.2).

Scope (doc/QT_REMOVAL_PLAN.md R2.5d, R7.4a): the General/Appearance page
(theme, token-counter visibility, system-prompt toggle, notification
preferences), the Integrations page (GitHub token, write-only), and now
the API-provider page (OpenAI-Compatible/Anthropic/Gemini endpoint
config, per-task model assignment, live catalog refresh) are real here.
Ollama/Llama.cpp pages remain deferred - R7.4b/R7.4c - and the
update-check pair (checkForUpdates/openRepository) needs a native
browser-open capability that doesn't exist yet in graphlink_desktop.py.
The SPA renders those sections disabled with an explicit "lands in R7.4b/
R7.4c" label rather than faking them.

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
import threading
from typing import Any, Callable

import api_provider
import graphlink_task_config as config
from graphlink_licensing import SettingsManager

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


def _redact(text: str, secret: str) -> str:
    # Post-review fix: some HTTP client libraries embed request parameters
    # (including a rejected API key) directly in exception text - the
    # legacy bridge this page replaces redacted for exactly this reason
    # (graphlink_settings_bridge.py: str(exc).replace(api_key, '***')).
    # Applied before any exception text reaches a WS-broadcast message, so
    # the write-only-key guarantee holds on the failure path too, not just
    # the happy path.
    if not secret:
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
        # Gemini has no live catalog endpoint wired (legacy never showed the
        # Load button for it either) - a stale/misbehaving client asking
        # anyway gets a clean error rather than a confusing initialize_api
        # call with the wrong semantics.
        if provider not in (config.API_PROVIDER_OPENAI, config.API_PROVIDER_ANTHROPIC):
            api_catalog_state[provider] = {"status": "error", "message": f"{provider} does not support catalog refresh."}
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
                str(api_key),
                str(base_url) if provider == config.API_PROVIDER_OPENAI else None,
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
            message = _redact(str(exc), api_key)
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
        await asyncio.to_thread(_apply, manager.reset_api_settings)
        if notifications is not None:
            notifications.show("All API settings have been cleared.", "success")
            await bus.publish("notification")
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
