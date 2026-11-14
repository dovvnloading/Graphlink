"""Provider settings, model discovery and activation for the current session."""
from __future__ import annotations

import asyncio
import os

import api_provider
import graphlink_task_config as config
from graphlink_cloud_providers import CLOUD_PROVIDERS, CloudProvider
from graphlink_model_catalog import normalize_model_id
from urllib.parse import urlsplit
from graphlink_settings_store import SettingsManager

from backend.api._settings_shared import API_TASK_KEYS, SettingsSessionState, redact_secret, run_locked
from backend.events import SessionBus
from backend.notifications import MessageType, NotificationState


class _InvalidConnection(ValueError):
    """An actionable settings validation failure that can be shown to the user."""


def _connection_key(manager: SettingsManager, profile: CloudProvider, endpoint: str, entered_key: str) -> str:
    if entered_key:
        return entered_key
    same_endpoint = manager.get_api_base_url(profile.name).rstrip("/") == endpoint
    local = profile.name == config.API_PROVIDER_OPENAI and urlsplit(endpoint).hostname in {"localhost", "127.0.0.1", "::1"}
    # A proxy on loopback commonly accepts requests without authentication.
    # A credential bound to an old endpoint must not follow it onto loopback.
    if local and not same_endpoint:
        return ""
    stored = manager.get_provider_key(profile.name)
    if stored:
        if profile.editable_endpoint and not same_endpoint:
            raise _InvalidConnection("Enter the API Key to use a different Base URL.")
        return stored
    if profile.environment_key_allowed(endpoint):
        for name in profile.env_vars:
            if key := os.environ.get(name):
                return key
    if local:
        return ""
    raise _InvalidConnection("Please enter the API Key.")


def _assignments(profile: CloudProvider, raw: object) -> dict[str, str]:
    inputs = raw if isinstance(raw, dict) else {}
    models = {task: inputs[task].strip() for task in API_TASK_KEYS
              if isinstance(inputs.get(task), str) and inputs[task].strip()}
    if not models.get(config.TASK_CHAT):
        raise _InvalidConnection(f"Please select a model for task: {config.TASK_CHAT}")
    if not profile.supports_image_generation:
        models.pop(config.TASK_IMAGE_GEN, None)
    return models


def register_settings_api_provider_intents(
    bus: SessionBus,
    manager: SettingsManager,
    notifications: NotificationState | None,
    state: SettingsSessionState,
    provider_runtime: api_provider.ProviderRuntime | None = None,
) -> None:
    live = provider_runtime or api_provider.DEFAULT_RUNTIME

    async def notify(message: str, severity: MessageType):
        if notifications is not None:
            notifications.show(message, severity)
            await bus.publish("notification")

    async def publish_configuration():
        if bus.has_topic("app-composer"):
            await bus.publish("app-composer")
        await bus.publish("app-settings")

    async def set_viewing_api_provider(provider: str):
        if isinstance(provider, str) and provider in CLOUD_PROVIDERS:
            state.viewing_api_provider = provider
            await bus.publish("app-settings")

    async def load_api_models(provider: str, api_key: str, base_url: str = ""):
        profile = CLOUD_PROVIDERS.get(str(provider))
        if profile is None:
            return
        # A reset or a newer request invalidates this result, even if the older
        # network request finishes last. Only the newest request owns the status.
        loading = {"status": "loading", "message": "Contacting the provider…"}
        state.api_catalog_state[provider] = loading
        request = run_locked(manager.begin_api_catalog_refresh, provider)
        key = ""
        try:
            if not profile.supports_catalog:
                raise _InvalidConnection(f"{profile.label or provider} uses manually entered model IDs.")
            endpoint = profile.endpoint(base_url if isinstance(base_url, str) else "")
            entered_key = api_key.strip() if isinstance(api_key, str) else ""
            key = await asyncio.to_thread(run_locked, _connection_key, manager, profile, endpoint, entered_key)
            await bus.publish("app-settings")
            model_ids = await asyncio.to_thread(api_provider.list_models_for_config, provider, key,
                                                endpoint if profile.protocol == "openai" else None)
            models = sorted({model for m in model_ids if (model := normalize_model_id(m))}, key=str.casefold)

            def persist_if_current():
                if manager.is_current_api_catalog_request(provider, request):
                    manager.set_api_model_catalog(models, provider, endpoint=endpoint)
            await asyncio.to_thread(run_locked, persist_if_current)
            result = {"status": "success", "message": f"Catalog refreshed - {len(models)} model(s) available from {provider}."}
        except Exception as exc:
            result = {"status": "error", "message": redact_secret(str(exc), key)}
            if not isinstance(exc, ValueError):
                result["message"] = f"Catalog refresh failed: {result['message']}\nYou can still enter a model ID."
        if state.api_catalog_state.get(provider) is loading:
            if manager.is_current_api_catalog_request(provider, request):
                state.api_catalog_state[provider] = result
            else:
                state.api_catalog_state.pop(provider, None)
            await bus.publish("app-settings")

    async def save_api_configuration(provider: str, base_url: str, api_key: str, models_by_task: dict[str, str]):
        profile = CLOUD_PROVIDERS.get(str(provider))
        if profile is None:
            await notify("Choose a provider from the directory.", "warning")
            return
        key = ""
        try:
            endpoint = profile.endpoint(base_url if isinstance(base_url, str) else "")
            models = _assignments(profile, models_by_task)
            entered_key = api_key.strip() if isinstance(api_key, str) else ""

            def persist_and_activate():
                nonlocal key
                key = _connection_key(manager, profile, endpoint, entered_key)
                staged = api_provider.ProviderRuntime()
                staged.initialize_api(provider, key, endpoint if profile.protocol == "openai" else None, task_models=models)
                manager.save_cloud_profile(provider, endpoint, entered_key, models)
                live.activate_api(staged.snapshot())
            await asyncio.to_thread(run_locked, persist_and_activate)
        except ValueError as exc:
            await notify(redact_secret(str(exc), key), "warning")
            return
        except Exception as exc:
            await notify(f"Failed to save the API provider: {redact_secret(str(exc), key)}", "error")
            return
        await notify(f"API settings for {provider} have been saved.", "success")
        await publish_configuration()

    async def reset_api_settings():
        def reset():
            manager.reset_api_settings()
            live.clear_api_configuration()
        try:
            await asyncio.to_thread(run_locked, reset)
        except OSError:
            await notify("Could not save the reset. Check that the settings file is writable.", "error")
            return
        state.api_catalog_state.clear()
        state.viewing_api_provider = manager.get_api_provider()
        await notify("All API settings have been cleared.", "success")
        await publish_configuration()

    bus.register_intent("app-settings", "setViewingApiProvider", set_viewing_api_provider)
    bus.register_intent("app-settings", "loadApiModels", load_api_models)
    bus.register_intent("app-settings", "saveApiConfiguration", save_api_configuration)
    bus.register_intent("app-settings", "resetApiSettings", reset_api_settings)
