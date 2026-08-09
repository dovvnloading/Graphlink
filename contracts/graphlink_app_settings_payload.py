"""The SPA settings topic's wire contract (Qt-removal plan R2.5d, extended R7.4a-c).

Was deliberately a SUBSET of graphlink_settings_payload.py::SettingsStatePayload
(General + Integrations fields only) - Ollama/Llama.cpp/API-provider pages
weren't implemented yet (see backend/settings.py's module docstring for
why). R7.4a added the API-provider fields for real, R7.4b added the Ollama
fields, and R7.4c now adds the Llama.cpp fields too - this closes every
field R2.5d originally deferred. Registered as its own codegen artifact
(topic "app-settings") so the generated validator doesn't collide with the
legacy island's own settings-state.ts.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ApiModelDescriptorPayload:
    """One entry of apiModelCatalog - graphlink_wire_schema.py has no bare
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
    showTokenCounter: bool
    enableSystemPrompt: bool
    notificationPreferences: dict[str, bool]
    githubTokenConfigured: bool
    # ADR-004 stage 4.4: True unless DPAPI is unavailable/failing on this
    # system, in which case every secret SettingsManager saves is being
    # written in plaintext - the Settings UI renders a persistent badge
    # when this is false (closes audit finding H12's silence).
    secretsEncryptedAtRest: bool
    # ADR-016 stage 16.1: General page - the log-level setting.
    logLevel: str
    # ADR-018 stage 18.4: General page - the auto-policy rung's setting
    # ("cheapest-capable" | "fastest" | "best-quality").
    autoModelPolicy: str
    # R7.4a: API-provider page.
    activeApiProvider: str
    viewingApiProvider: str
    apiBaseUrl: str
    apiKeyConfigured: dict[str, bool]
    # ADR-004 stage 4.4: "stored" | "environment" | "none" per provider -
    # surfaces api_provider.py's own env-var key fallback (previously
    # invisible to the user) without exposing the key's value.
    apiKeySource: dict[str, str]
    apiModels: dict[str, str]
    apiModelCatalog: list[ApiModelDescriptorPayload]
    apiCatalogStatus: str
    apiCatalogMessage: str
    geminiStaticModels: list[str]
    geminiStaticImageModels: list[str]
    # R7.4b: Ollama page. R8a: reasoning went from a 2-value Mode
    # (Thinking/Quick) to a graded 4-value Level (off/low/medium/high).
    ollamaReasoningLevel: str
    ollamaCurrentModel: str
    ollamaModelAssignments: dict[str, str]
    ollamaScannedModels: list[str]
    ollamaScanSummary: str
    ollamaScanStatus: str
    ollamaPullStatus: str
    ollamaNotice: str
    # R7.4c: Llama.cpp page. R8a: same Mode -> Level change as Ollama above.
    llamaCppReasoningLevel: str
    llamaCppChatModelPath: str
    llamaCppTitleModelPath: str
    llamaCppChatFormat: str
    llamaCppNCtx: int
    llamaCppNGpuLayers: int
    llamaCppNThreads: int
    llamaCppScannedModels: list[str]
    llamaCppScanSummary: str
    llamaCppScanStatus: str
    llamaCppNotice: str
    minCompatibleSchemaVersion: int | None = None
