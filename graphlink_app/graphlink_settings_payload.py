"""The settings island's outbound wire contract, as typed Python dataclasses.

THIS IS A WIRE FORMAT, NOT A DOMAIN MODEL - see graphlink_composer_payload.py
for the fuller rationale. Grown incrementally, one page at a time, per the
recorded Phase 3 increment sequence in
doc/FRONTEND_WEB_MIGRATION_MASTER_PLAN.md: increment 2 shipped
activeSection alone (shell/navigation); increment 3 added the
General/Appearance page; increment 4 (this) adds the Integrations page -
the first page with a real secret, and deliberately write-only: the
payload only ever states WHETHER a token is configured, never the token
value itself. Each remaining page's own fields land in its own later
increment rather than being stubbed speculatively here.

Field names are camelCase to match the JSON keys
SettingsBridge._build_state_payload() emits and
web_ui/src/lib/bridge-core/generated/settings-state.ts mirrors.

Cross-checked against a live SettingsBridge snapshot by
tests/test_settings_payload_schema.py.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SettingsStatePayload:
    """The complete published snapshot, including the envelope fields
    IslandBridge.publish() adds to every island's payload."""

    schemaVersion: int
    revision: int
    activeSection: str

    # General/Appearance page (increment 3) - mirrors
    # AppearanceSettingsWidget's fields exactly, minus the two that need a
    # real window callback (Check for Updates / Open Repository), deferred
    # to increment 8 alongside the rest of the duck-typed-callback wiring.
    theme: str
    showTokenCounter: bool
    enableSystemPrompt: bool
    notificationPreferences: dict[str, bool]
    updateNotificationsEnabled: bool
    updateStatusMessage: str
    updateStatusLevel: str
    updateLastCheckedAt: str
    updateAvailable: bool
    # Added increment 8, alongside the real checkForUpdates()/
    # openRepository() intents these two fields support.
    updateLatestVersion: str
    updateCheckInProgress: bool

    # Integrations page (increment 4) - write-only by design: this bridge
    # never emits the actual GitHub token, only whether one is configured.
    # See graphlink_settings_bridge.py's module docstring and
    # tests/test_settings_bridge_secrets.py for the invariant this protects.
    githubTokenConfigured: bool

    # API page (increment 5) - the 3 provider keys are write-only, same
    # shape as githubTokenConfigured. apiTaskModels/apiAvailableModels are
    # scoped to whichever provider is currently selected; switching
    # apiProvider republishes both from SettingsManager's own
    # per-provider-keyed storage (get_api_models/get_api_model_catalog).
    apiProvider: str
    apiBaseUrl: str
    openaiKeyConfigured: bool
    anthropicKeyConfigured: bool
    geminiKeyConfigured: bool
    apiTaskModels: dict[str, str]
    apiAvailableModels: list[str]
    # idle|running|done|error - a faithful binary port of ApiModelLoadWorker's
    # existing finished/error signals (Phase 3's Section C design decision),
    # not real progress.
    apiLoadStatus: str

    # Ollama page (increment 6). ollamaModelAssignments flattens
    # SettingsManager's {mode, model_id} dict-of-dicts into one string per
    # task - "inherit"|"auto"|"<explicit model id>" - a strictly equivalent,
    # simpler wire shape (same idea as apiTaskModels' flat dict). An
    # explicit value not present in ollamaScannedModels is preserved
    # verbatim, never dropped - the "unavailable-model preservation"
    # behavior the Phase 3 checklist names.
    ollamaReasoningMode: str
    ollamaCurrentModel: str
    ollamaModelAssignments: dict[str, str]
    ollamaScannedModels: list[str]
    ollamaScanSummary: str
    # idle|running|done|error, one faithful binary status port per worker
    # (Phase 3 Section C) - scan (OllamaModelScanWorker) and pull
    # (ModelPullWorkerThread) are two independent operations with two
    # independent statuses.
    ollamaScanStatus: str
    ollamaPullStatus: str

    # LlamaCpp page (increment 7) - mechanically parallel to Ollama's scan
    # half. llamaCppChatModelPath/llamaCppTitleModelPath are STAGED, not yet
    # persisted - set by the native file pickers, committed to
    # SettingsManager only by saveLlamaCppSettings() (validated: file
    # exists, .gguf extension), mirroring the original widget's own
    # Browse-fills-the-field / Save-persists-and-validates split - the one
    # LlamaCpp field that couldn't become a live-apply intent like every
    # other field on this island, since a mid-typing path can't be
    # meaningfully validated.
    llamaCppReasoningMode: str
    llamaCppChatModelPath: str
    llamaCppTitleModelPath: str
    llamaCppChatFormat: str
    llamaCppNCtx: int
    llamaCppNGpuLayers: int
    llamaCppNThreads: int
    llamaCppScannedModels: list[str]
    llamaCppScanSummary: str
    llamaCppScanStatus: str

    # Shared across pages, not API-specific: a transient message for an
    # intent that was rejected (e.g. failed provider init) or a stale
    # operation. Modeled directly on CommandPaletteBridge's identical
    # `notice` field - same shape, same "JS renders it, never round-trips
    # it back" contract - reused rather than inventing a second
    # error-channel shape, per the Phase 3 design panel's own synthesis.
    notice: str | None = None

    # See ComposerStatePayload's identical field for the full negotiation
    # rationale; optional for the same reason (models a sender predating this
    # field, not today's - IslandBridge.publish() always emits it).
    minCompatibleSchemaVersion: int | None = None
