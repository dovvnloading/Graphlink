"""ADR-002 stage 2.7: Settings dialog - API-provider page
(OpenAI-Compatible/Anthropic/Gemini).

Relocated VERBATIM from backend/settings.py's former register_settings
(closures at its former lines 506-752) - pure code motion, no behavior
change.

The API-provider page deliberately does NOT replicate one piece of legacy
behavior: the old QWidget pre-filled its API-key field with the saved
plaintext key on every provider switch (safe there only because the
widget lives in-process, with no serialization boundary). Serializing a
plaintext key into this topic's WS payload would be a real regression
from the write-only pattern already established for the GitHub token
(backend/api/intents_settings_general.py) - so this page follows that
same precedent instead: apiKeyConfigured is a per-provider bool, the key
input always starts blank, and saving requires retyping the full key.
This is a deliberate security improvement over legacy parity, not an
oversight - called out explicitly per the "never redefine done"
discipline.
"""

from __future__ import annotations

import asyncio

import api_provider
import graphlink_task_config as config
from graphlink_model_catalog import ModelDescriptor, sort_descriptors

from backend.api._settings_shared import (
    KNOWN_API_PROVIDERS,
    API_TASK_KEYS,
    SettingsSessionState,
    redact_secret,
    run_locked,
)
from backend.events import SessionBus
from backend.notifications import NotificationState
from graphlink_settings_store import SettingsManager


def register_settings_api_provider_intents(
    bus: SessionBus,
    manager: SettingsManager,
    notifications: NotificationState | None,
    state: SettingsSessionState,
) -> None:
    async def set_viewing_api_provider(provider: str):
        state.viewing_api_provider = str(provider)
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
            # slot: the wire-payload builder surfaces the slot for the
            # viewed provider only, so a slot for an unknown string could
            # never be read - it would just grow this dict without bound on
            # a key the client controls.
            if provider in KNOWN_API_PROVIDERS:
                state.api_catalog_state[provider] = {
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
            state.api_catalog_state[provider] = {
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
            state.api_catalog_state[provider] = {"status": "error", "message": "Please enter the API Key."}
            await bus.publish("app-settings")
            return

        state.api_catalog_state[provider] = {
            "status": "loading",
            "message": "Contacting the provider... you can keep editing other settings.",
        }
        await bus.publish("app-settings")

        try:
            # ADR-006 stage 6.5: a read-only catalog fetch through a
            # throwaway client (api_provider.list_models_for_config), never
            # initialize_api - refreshing a Settings dropdown used to
            # silently repoint the process's LIVE provider (and with
            # per-session runtimes, would have repointed every session's
            # default). Still exercises the same client-construction path
            # Save uses, so a successful load doubles as a connection check,
            # matching legacy's ApiModelLoadWorker. This also retires the
            # post-hoc `API_PROVIDER_TYPE != provider` consistency check
            # that used to live here: the listing no longer touches live
            # state, so there is no half-swapped-globals race left to
            # detect - the result is inherently about the (provider, key,
            # base_url) triple THIS call was given.
            model_ids = await asyncio.to_thread(
                api_provider.list_models_for_config,
                provider,
                key,
                base_url if provider == config.API_PROVIDER_OPENAI else None,
            )
            # Same descriptor shape get_available_model_descriptors builds
            # for the live provider, keyed to the REQUESTED provider.
            descriptors = sort_descriptors(
                ModelDescriptor(
                    model_id=str(model_id).strip(),
                    provider=provider,
                    ready=True,
                    available=True,
                    source="endpoint",
                )
                for model_id in model_ids
                if str(model_id).strip()
            )
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
            message = redact_secret(str(exc), key)
            state.api_catalog_state[provider] = {
                "status": "error",
                "message": f"Catalog refresh failed: {message}\nSaved/custom model IDs remain usable if the endpoint supports them.",
            }
            await bus.publish("app-settings")
            return

        await asyncio.to_thread(run_locked, manager.set_api_model_catalog, normalized, provider)
        state.api_catalog_state[provider] = {
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

        # ADR-006 stage 6.5 (H6): TASK_IMAGE_GEN is optional for EVERY
        # provider, not just Anthropic - image generation is capability-gated
        # at call time (generate_image raises an actionable "No image
        # generation model configured" error), so a text-only
        # OpenAI-compatible endpoint (vLLM, LM Studio, llama-server) saves
        # cleanly. Mirrors ProviderRuntime.is_configured's required set.
        required_tasks = [task for task in API_TASK_KEYS if task != config.TASK_IMAGE_GEN]
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
                notifications.show(f"Failed to initialize the API provider: {redact_secret(str(exc), api_key)}", "error")
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
            # are now read HERE, inside the manager lock (via run_locked),
            # not on the event loop before this thread was dispatched. The
            # old read-before-await-then-write-after ordering was a
            # lost-update race: two saveApiConfiguration calls for
            # different providers (e.g. two browser tabs on the same
            # default session) could interleave so that the second call's
            # stale pre-read of the first call's key got written back over
            # the first call's own, freshly-persisted value. Reading and
            # writing inside the same locked critical section makes this
            # atomic with respect to every other run_locked-guarded
            # mutation.
            openai_key = api_key if provider == config.API_PROVIDER_OPENAI else manager.get_openai_key()
            anthropic_key = api_key if provider == config.API_PROVIDER_ANTHROPIC else manager.get_anthropic_key()
            gemini_key = api_key if provider == config.API_PROVIDER_GEMINI else manager.get_gemini_key()
            manager.set_api_settings(provider, base_url, openai_key, anthropic_key, gemini_key)
            manager.set_api_models(normalized_models, provider)
            for task, model_id in normalized_models.items():
                api_provider.set_task_model(task, model_id)
            # ADR-006 stage 6.5: a successful save just flipped the LIVE
            # provider to this API endpoint (initialize_api above), but the
            # persisted mode never followed - a restart silently booted back
            # into whatever mode was last persisted. Persist the mode the
            # save actually put the app in.
            manager.set_current_mode(config.MODE_API_ENDPOINT)

        await asyncio.to_thread(run_locked, _persist)
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
            for known in KNOWN_API_PROVIDERS:
                manager.set_api_model_catalog([], known)

        await asyncio.to_thread(run_locked, _reset)
        # Transient per-provider status/message is UI state, not manager
        # state, so it has to be cleared here too - otherwise the previous
        # "Catalog refreshed - N model(s)" success banner survives a reset
        # that just deleted that very catalog.
        state.api_catalog_state.clear()
        # Legacy's reset_settings snapped the provider dropdown back to
        # OpenAI-Compatible; reset_api_settings restores exactly that as
        # the persisted provider, so the viewed provider follows it.
        state.viewing_api_provider = manager.get_api_provider()
        if notifications is not None:
            notifications.show("All API settings have been cleared.", "success")
            await bus.publish("notification")
        await bus.publish("app-settings")

    bus.register_intent("app-settings", "setViewingApiProvider", set_viewing_api_provider)
    bus.register_intent("app-settings", "loadApiModels", load_api_models)
    bus.register_intent("app-settings", "saveApiConfiguration", save_api_configuration)
    bus.register_intent("app-settings", "resetApiSettings", reset_api_settings)
