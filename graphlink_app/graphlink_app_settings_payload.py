"""The SPA settings topic's wire contract (Qt-removal plan R2.5d, extended R7.4a).

Was deliberately a SUBSET of graphlink_settings_payload.py::SettingsStatePayload
(General + Integrations fields only) - Ollama/Llama.cpp/API-provider pages
weren't implemented yet (see backend/settings.py's module docstring for
why). R7.4a adds the API-provider fields for real; Ollama/Llama.cpp remain
deferred (R7.4b/R7.4c) so their fields would still be dead weight here -
the same "only what the SPA actually needs" rationale as every other
R2.3-R2.5 app-* payload. Registered as its own codegen artifact (topic
"app-settings") so the generated validator doesn't collide with the legacy
island's own settings-state.ts.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ApiModelDescriptorPayload:
    """One entry of apiModelCatalog - graphlink_island_schema.py has no bare
    `dict` support by design (a future-drift guard), so this mirrors the
    normalized descriptor shape backend/settings.py's load_api_models
    already builds (model_id/provider/capabilities/ready/available) as a
    real nested dataclass instead."""

    modelId: str
    provider: str
    capabilities: list[str]
    ready: bool
    available: bool


@dataclass
class AppSettingsStatePayload:
    schemaVersion: int
    revision: int
    activeSection: str
    theme: str
    showTokenCounter: bool
    enableSystemPrompt: bool
    notificationPreferences: dict[str, bool]
    githubTokenConfigured: bool
    # R7.4a: API-provider page.
    activeApiProvider: str
    viewingApiProvider: str
    apiBaseUrl: str
    apiKeyConfigured: dict[str, bool]
    apiModels: dict[str, str]
    apiModelCatalog: list[ApiModelDescriptorPayload]
    apiCatalogStatus: str
    apiCatalogMessage: str
    geminiStaticModels: list[str]
    geminiStaticImageModels: list[str]
    minCompatibleSchemaVersion: int | None = None
