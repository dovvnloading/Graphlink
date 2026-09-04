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

from settings_store._composed import SettingsManagerParts

import graphlink_secrets
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

    def get_api_base_url(self):
        return self.state.get("api_base_url", "https://api.openai.com/v1")

    def get_openai_key(self):
        return graphlink_secrets.unprotect(self.state.get("openai_api_key", ""))

    def get_anthropic_key(self):
        return graphlink_secrets.unprotect(self.state.get("anthropic_api_key", ""))

    def get_gemini_key(self):
        return graphlink_secrets.unprotect(self.state.get("gemini_api_key", ""))

    def get_github_token(self):
        return graphlink_secrets.unprotect(self.state.get("github_access_token", ""))

    def get_api_models(self, provider: str | None = None):
        provider = provider or self.get_api_provider()
        profiles = self.state.get("api_models_by_provider", {})
        if isinstance(profiles, dict):
            return dict(profiles.get(provider, {}) or {})
        return dict(self.state.get("api_models", {}) or {})

    def get_api_model_catalog(self, provider: str | None = None):
        """Return the last successful provider catalog refresh for the UI."""
        provider = provider or self.get_api_provider()
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

    def set_api_model_catalog(self, models: list[dict] | list[str], provider: str | None = None):
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
        catalogs = self.state.get("api_model_catalog_by_provider", {})
        if not isinstance(catalogs, dict):
            catalogs = {}
        catalogs[provider] = normalized
        self.state["api_model_catalog_by_provider"] = catalogs
        self._save_state()

    def set_github_token(self, token: str):
        self.state["github_access_token"] = self._protect_and_track(token)
        self._save_state()

    def reset_api_settings(self):
        self.state["api_provider"] = "OpenAI-Compatible"
        self.state["api_base_url"] = "https://api.openai.com/v1"
        self.state["openai_api_key"] = ""
        self.state["anthropic_api_key"] = ""
        self.state["gemini_api_key"] = ""
        self.state["api_models"] = {}
        self.state["api_models_by_provider"] = {}
        self._save_state()
