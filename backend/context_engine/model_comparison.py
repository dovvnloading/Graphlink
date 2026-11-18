"""Read-only, configured model choices for a compare-on-send fan-out.

No network calls, no credentials on the wire, and no mutation of the active
model route. Exact (provider, model_id) pairs are checked again when sending.
"""
from __future__ import annotations

from pathlib import Path

import api_provider
import graphlink_task_config as config
from graphlink_cloud_providers import CLOUD_PROVIDERS
from graphlink_model_catalog import ModelRef


def available_comparison_models(settings_manager, runtime=None) -> list[dict[str, str]]:
    """List only models addressable by existing local or saved cloud profiles."""
    if settings_manager is None:
        return []

    state = (runtime if runtime is not None else api_provider.DEFAULT_RUNTIME).snapshot()
    result: dict[tuple[str, str], dict[str, str]] = {}

    def add(provider: str, model_id: str) -> None:
        model_id = str(model_id or "").strip()
        if model_id:
            result[(provider, model_id)] = {
                "provider": provider,
                "modelId": model_id,
                "label": f"{provider} · {model_id}",
            }

    scanned = settings_manager.get_ollama_scanned_models() or []
    for model in scanned:
        add(config.LOCAL_PROVIDER_OLLAMA, model)
    # Include the live assigned model when a scan cache has gone empty.
    add(config.LOCAL_PROVIDER_OLLAMA, state.ollama_models.get(config.TASK_CHAT, ""))

    # Only the two configured GGUF slots are runnable, not every scanned path.
    llama_settings = settings_manager.get_llama_cpp_settings()
    for key in ("chat_model_path", "title_model_path"):
        model_path = llama_settings.get(key) or state.llama_cpp_settings.get(key) or ""
        if model_path:
            add(config.LOCAL_PROVIDER_LLAMACPP, Path(model_path).name)

    for provider in CLOUD_PROVIDERS:
        endpoint = settings_manager.get_api_base_url(provider)
        live_profile = (
            state.use_api_mode and state.api_provider_type == provider and state.api_client is not None
        )
        has_credentials = (
            bool(settings_manager.get_provider_key(provider))
            or api_provider.env_api_key_configured(provider, endpoint)
            or (provider == config.API_PROVIDER_OPENAI and api_provider._is_local_base_url(endpoint))
        )
        if not (has_credentials or live_profile):
            continue
        for entry in settings_manager.get_api_model_catalog(provider) or []:
            if isinstance(entry, dict) and entry.get("ready", True) and entry.get("available", True):
                add(provider, entry.get("model_id", ""))
        add(provider, (settings_manager.get_api_models(provider) or {}).get(config.TASK_CHAT, ""))
        if live_profile:
            add(provider, state.api_models.get(config.TASK_CHAT, ""))

    return [result[key] for key in sorted(result, key=lambda item: (item[0].lower(), item[1].lower()))]


def validate_comparison_models(raw_models, settings_manager, runtime=None) -> list[ModelRef]:
    """Reject duplicate, unknown and over-limit routes before any graph mutation."""
    if not isinstance(raw_models, list) or not 2 <= len(raw_models) <= 4:
        raise ValueError("Choose two to four models for comparison.")
    valid = {
        (row["provider"], row["modelId"])
        for row in available_comparison_models(settings_manager, runtime)
    }
    refs: list[ModelRef] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_models:
        if not isinstance(item, dict):
            raise ValueError("Invalid comparison model selection.")
        provider = item.get("provider")
        model_id = item.get("modelId")
        if not isinstance(provider, str) or not isinstance(model_id, str):
            raise ValueError("Invalid comparison model selection.")
        key = (provider, model_id)
        if key not in valid or key in seen:
            raise ValueError(f"Comparison model is unavailable or duplicated: {provider} / {model_id}")
        seen.add(key)
        refs.append(ModelRef(provider=provider, model_id=model_id))
    return refs
