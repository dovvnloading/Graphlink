"""CloudProviderSettingsOps - cloud/API provider settings for SettingsManager:
the current mode toggle, the active API provider/base URL, provider API keys
(decrypted on read), per-provider reasoning levels for the three cloud
providers, and the per-provider model/catalog selections.

Named cloud_provider.py/CloudProviderSettingsOps rather than
api_provider.py/ApiProviderSettingsOps deliberately - the real top-level
api_provider.py module already owns that name, and importing this module
alongside it under a colliding name would be a namespace collision waiting
to happen.

A MIXIN, not a standalone class: every method operates on the composing
class's own state (self.state) and shared constants (self.REASONING_LEVELS)
- it is composed exactly once, by graphlink_settings_store.py's `class
SettingsManager(PersistenceOps, ..., CloudProviderSettingsOps, ...)`.
set_api_settings also reaches self._protect_and_track (PersistenceOps) and
compares against the KEEP_EXISTING_SECRET sentinel, which stays defined at
the top level in graphlink_settings_store.py since real callers pass it in
directly.

Method bodies are relocated VERBATIM from graphlink_settings_store.py; only
the class wrapper is new.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from backend.fal_integration import media_urls, model_path, request_path

from settings_store._composed import SettingsManagerParts

import graphlink_secrets
from graphlink_cloud_providers import CLOUD_PROVIDERS, DEFAULT_CLOUD_PROVIDER
from graphlink_task_config import API_PROVIDER_OPENAI, MODE_API_ENDPOINT, MODE_OLLAMA_LOCAL
from graphlink_model_catalog import normalize_model_id


class CloudProviderSettingsOps(SettingsManagerParts):

    def get_anthropic_reasoning_level(self):
        return self.state.get("anthropic_reasoning_level", "off")

    def set_anthropic_reasoning_level(self, level: str):
        if level in self.REASONING_LEVELS:
            self.state['anthropic_reasoning_level'] = level
            self._save_state()

    def get_gemini_reasoning_level(self):
        return self.state.get("gemini_reasoning_level", "off")

    def set_gemini_reasoning_level(self, level: str):
        if level in self.REASONING_LEVELS:
            self.state['gemini_reasoning_level'] = level
            self._save_state()

    def get_openai_reasoning_level(self):
        return self.state.get("openai_reasoning_level", "off")

    def set_openai_reasoning_level(self, level: str):
        if level in self.REASONING_LEVELS:
            self.state['openai_reasoning_level'] = level
            self._save_state()

    def get_current_mode(self):
        return self.state.get("current_mode", "Ollama (Local)")

    def set_current_mode(self, mode: str):
        self.state["current_mode"] = mode
        self._save_state()

    def get_api_provider(self):
        return self.state.get("api_provider", "OpenAI-Compatible")

    def get_api_base_url(self, provider: str | None = None) -> str:
        provider = provider or self.get_api_provider()
        profile = CLOUD_PROVIDERS.get(provider)
        if profile is None:  # older/unrecognized profile, preserved on disk
            return str(self.state.get("api_base_url", "https://api.openai.com/v1"))
        if not profile.editable_endpoint:
            return profile.base_url
        endpoints = self.state.get("api_base_urls_by_provider", {})
        if isinstance(endpoints, dict) and provider in endpoints:
            return str(endpoints[provider] or "")
        if provider == API_PROVIDER_OPENAI:
            # Older versions wrote one global URL. A native-provider save could
            # overwrite it with "" while leaving a custom key behind. A lost
            # binding requires re-entry, never a guess at the key's destination.
            if self.get_api_provider() == provider or self.state.get(profile.secret_field):
                return str(self.state.get("api_base_url") or "")
        return profile.base_url

    def get_provider_key(self, provider: str) -> str:
        return graphlink_secrets.unprotect(self.state.get(CLOUD_PROVIDERS[provider].secret_field, ""))

    def save_cloud_profile(self, provider: str, endpoint: str, entered_key: str, models: dict[str, str]) -> None:
        """Commit one complete connection, leaving sibling secret blobs untouched."""
        profile = CLOUD_PROVIDERS[provider]
        state = dict(self.state)
        urls = dict(state.get("api_base_urls_by_provider") or {})
        # Record the old binding before overwriting the legacy active-url slot.
        urls.setdefault(API_PROVIDER_OPENAI, self.get_api_base_url(API_PROVIDER_OPENAI))
        urls[provider] = endpoint
        profiles = dict(state.get("api_models_by_provider") or {})
        profiles[provider] = dict(models)
        state.update(api_provider=provider, api_base_url=endpoint, api_base_urls_by_provider=urls,
                     api_models=dict(models), api_models_by_provider=profiles, current_mode=MODE_API_ENDPOINT)
        changed_url = self.get_api_base_url(provider).rstrip("/") != endpoint
        if entered_key:
            state[profile.secret_field] = self._protect_and_track(entered_key)
        elif changed_url:
            # A keyless local service replaced a credential-bound custom URL.
            # Do not accidentally bind the old key to that service on restart.
            state.pop(profile.secret_field, None)
        if changed_url and not self.get_catalog_endpoint(provider):
            # An older cache had no endpoint identity. Do not carry it across
            # the first URL change after upgrading.
            catalogs = dict(state.get("api_model_catalog_by_provider") or {})
            catalogs.pop(provider, None)
            state["api_model_catalog_by_provider"] = catalogs
        self._save_state(state, raise_on_error=True)
        self.state = state

    def get_openai_key(self):
        return graphlink_secrets.unprotect(self.state.get("openai_api_key", ""))

    def get_anthropic_key(self):
        return graphlink_secrets.unprotect(self.state.get("anthropic_api_key", ""))

    def get_gemini_key(self):
        return graphlink_secrets.unprotect(self.state.get("gemini_api_key", ""))

    def get_github_token(self):
        return graphlink_secrets.unprotect(self.state.get("github_access_token", ""))

    def get_fal_api_key_source(self) -> str:
        # Do not serialize the key into any WebSocket settings snapshot.
        if self.state.get("fal_api_key"):
            return "stored"
        if os.environ.get("GRAPHLINK_FAL_API_KEY") or os.environ.get("FAL_KEY"):
            return "environment"
        return "none"

    def get_fal_api_key(self) -> str:
        saved = self.state.get("fal_api_key", "")
        if saved:
            return graphlink_secrets.unprotect(saved)
        return os.environ.get("GRAPHLINK_FAL_API_KEY") or os.environ.get("FAL_KEY") or ""

    def set_fal_api_key(self, key: str) -> None:
        if not isinstance(key, str):
            raise ValueError("fal API key must be a string.")
        self.state["fal_api_key"] = self._protect_and_track(key.strip())
        self._save_state()

    def get_fal_media_favorites(self) -> list[str]:
        """Saved model favorites, separate from Fal's account-side asset favorites."""
        raw = self.state.get("fal_media_favorites", [])
        if not isinstance(raw, list):
            return []
        result = []
        for value in raw[:500]:
            try:
                path = model_path(value)
            except Exception:
                continue
            if path not in result:
                result.append(path)
        return result

    def set_fal_media_favorite(self, model_id: str, enabled: bool) -> None:
        """Idempotent setter: safe against a replayed WebSocket intent."""
        path = model_path(model_id)
        if not isinstance(enabled, bool):
            raise ValueError("favorite setting must be boolean")
        favorites = self.get_fal_media_favorites()
        if enabled:
            if path not in favorites:
                if len(favorites) >= 500:
                    raise ValueError("Maximum of 500 favorite models reached.")
                favorites.append(path)
        else:
            favorites = [entry for entry in favorites if entry != path]
        self.state["fal_media_favorites"] = favorites
        self._save_state()

    def get_fal_jobs(self) -> list[dict]:
        # Bounded metadata only. Inputs and the API key are never stored in
        # this ledger. Full responses are capped per job for session.dat.
        jobs = self.state.get("fal_jobs", [])
        return [dict(item) for item in jobs if isinstance(item, dict)][:40] if isinstance(jobs, list) else []

    def upsert_fal_job(self, model_id: str, request_id: str, status: str,
                       result=None) -> None:
        model_path(model_id)
        request_path(model_id, request_id)
        now = datetime.now(timezone.utc).isoformat()
        jobs = self.get_fal_jobs()
        job = next((item for item in jobs if item.get("requestId") == request_id), None)
        if job is None:
            job = {
                "requestId": request_id, "modelId": model_id,
                "createdAt": now, "resultJson": "", "mediaUrls": [],
            }
            jobs.insert(0, job)
        elif job.get("modelId") != model_id:
            raise ValueError("fal request ID is already associated with a different model.")
        job["status"] = str(status)[:80]
        job["updatedAt"] = now
        if result is not None:
            job["mediaUrls"] = media_urls(result)
            try:
                serialized = json.dumps(result, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError):
                serialized = json.dumps({"notice": "Non-JSON response; inspect the remote result."})
            job["resultJson"] = serialized[:32000] + ("\n[truncated]" if len(serialized) > 32000 else "")
        self.state["fal_jobs"] = jobs[:40]
        self._save_state()

    def get_api_models(self, provider: str | None = None):
        provider = provider or self.get_api_provider()
        profiles = self.state.get("api_models_by_provider", {})
        if isinstance(profiles, dict):
            return dict(profiles.get(provider, {}) or {})
        return dict(self.state.get("api_models", {}) or {})

    def begin_api_catalog_refresh(self, provider: str) -> object:
        # Shared across sessions. Identity prevents an old request writing over
        # a new refresh or restoring a catalog after Reset has returned.
        ticket = object()
        self._api_catalog_requests[provider] = ticket
        return ticket

    def is_current_api_catalog_request(self, provider: str, ticket: object) -> bool:
        return self._api_catalog_requests.get(provider) is ticket

    def get_catalog_endpoint(self, provider: str) -> str:
        endpoints = self.state.get("api_catalog_endpoints", {})
        if isinstance(endpoints, dict) and provider in endpoints:
            return str(endpoints[provider] or "")
        return ""

    def get_api_model_catalog(self, provider: str | None = None, *, include_draft: bool = False):
        """Offer only models from the saved endpoint to the routing runtime."""
        provider = provider or self.get_api_provider()
        bound = self.get_catalog_endpoint(provider)
        if bound and not include_draft and bound.rstrip("/") != self.get_api_base_url(provider).rstrip("/"):
            return []
        catalogs = self.state.get("api_model_catalog_by_provider", {})
        raw_models = catalogs.get(provider, []) if isinstance(catalogs, dict) else []
        if not isinstance(raw_models, list):
            return []

        normalized = []
        seen = set()
        for raw_model in raw_models:
            if isinstance(raw_model, dict):
                model_id = str(raw_model.get("model_id") or raw_model.get("id") or "").strip()
                descriptor = dict(raw_model)
            else:
                model_id = str(raw_model or "").strip()
                descriptor = {}
            if not model_id or model_id.lower() in seen:
                continue
            seen.add(model_id.lower())
            descriptor.update(
                {
                    "model_id": model_id,
                    "provider": str(descriptor.get("provider") or provider),
                    "ready": bool(descriptor.get("ready", True)),
                    "available": bool(descriptor.get("available", True)),
                    "capabilities": sorted(
                        {str(item).strip() for item in descriptor.get("capabilities", []) if str(item).strip()}
                    ),
                }
            )
            normalized.append(descriptor)
        return normalized

    def set_api_settings(
        self,
        provider: str,
        base_url: str,
        openai_key: str,
        anthropic_key: str,
        gemini_key: str,
    ):
        endpoints = dict(self.state.get("api_base_urls_by_provider") or {})
        endpoints.setdefault(API_PROVIDER_OPENAI, self.get_api_base_url(API_PROVIDER_OPENAI))
        if provider in CLOUD_PROVIDERS and CLOUD_PROVIDERS[provider].editable_endpoint:
            endpoints[provider] = base_url
        self.state["api_base_urls_by_provider"] = endpoints
        self.state["api_provider"] = provider
        self.state["api_base_url"] = base_url
        # KEEP_EXISTING_SECRET leaves the stored value untouched - no decrypt,
        # no re-encrypt, no write. See that sentinel's own comment for the
        # sibling-key destruction this prevents; an ordinary "" still clears.
        from graphlink_settings_store import KEEP_EXISTING_SECRET

        for state_key, value in (
            ("openai_api_key", openai_key),
            ("anthropic_api_key", anthropic_key),
            ("gemini_api_key", gemini_key),
        ):
            if value is KEEP_EXISTING_SECRET:
                continue
            self.state[state_key] = self._protect_and_track(value)
        self._save_state()

    def set_api_models(self, models_dict: dict, provider: str | None = None):
        provider = provider or self.get_api_provider()
        normalized = {
            str(task): normalize_model_id(model)
            for task, model in (models_dict or {}).items()
            if normalize_model_id(model)
        }
        profiles = self.state.get("api_models_by_provider", {})
        if not isinstance(profiles, dict):
            profiles = {}
        profiles[provider] = normalized
        self.state["api_models_by_provider"] = profiles
        self.state["api_models"] = normalized
        self._save_state()

    def set_api_model_catalog(
        self, models: list[dict] | list[str], provider: str | None = None, *, endpoint: str | None = None,
    ):
        """Persist a normalized, non-secret snapshot of a provider model catalog."""
        provider = provider or self.get_api_provider()
        normalized = []
        seen = set()
        for raw_model in models or []:
            if isinstance(raw_model, dict):
                model_id = str(raw_model.get("model_id") or raw_model.get("id") or "").strip()
                descriptor = dict(raw_model)
            else:
                model_id = str(raw_model or "").strip()
                descriptor = {}
            if not model_id or model_id.lower() in seen:
                continue
            seen.add(model_id.lower())
            normalized.append(
                {
                    "model_id": model_id,
                    "provider": str(descriptor.get("provider") or provider),
                    "ready": bool(descriptor.get("ready", True)),
                    "available": bool(descriptor.get("available", True)),
                    "capabilities": sorted(
                        {str(item).strip() for item in descriptor.get("capabilities", []) if str(item).strip()}
                    ),
                }
            )
        catalogs = dict(self.state.get("api_model_catalog_by_provider") or {})
        catalogs[provider] = normalized
        endpoints = dict(self.state.get("api_catalog_endpoints") or {})
        endpoints[provider] = self.get_api_base_url(provider) if endpoint is None else endpoint
        state = dict(self.state, api_model_catalog_by_provider=catalogs, api_catalog_endpoints=endpoints)
        self._save_state(state, raise_on_error=True)
        self.state = state

    def set_github_token(self, token: str):
        self.state["github_access_token"] = self._protect_and_track(token)
        self._save_state()

    def reset_api_settings(self):
        state = dict(self.state)
        for profile in CLOUD_PROVIDERS.values():
            state.pop(profile.secret_field, None)
        if state.get("current_mode") == MODE_API_ENDPOINT:
            state["current_mode"] = MODE_OLLAMA_LOCAL
        state.update(api_provider=DEFAULT_CLOUD_PROVIDER, api_base_url=CLOUD_PROVIDERS[DEFAULT_CLOUD_PROVIDER].base_url,
                     api_base_urls_by_provider={}, api_models={}, api_models_by_provider={}, api_model_catalog_by_provider={},
                     api_catalog_endpoints={})
        self._save_state(state, raise_on_error=True)
        self.state = state
        self._api_catalog_requests.clear()
