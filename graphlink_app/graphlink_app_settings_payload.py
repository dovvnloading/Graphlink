"""The SPA settings topic's wire contract (Qt-removal plan R2.5d).

Deliberately a SUBSET of graphlink_settings_payload.py::SettingsStatePayload
(General + Integrations fields only) rather than the legacy island's full
~30-field surface: Ollama/Llama.cpp/API-provider pages aren't implemented
yet (see backend/settings.py's module docstring for why), so their fields
would be dead weight here - the same "only what the SPA actually needs"
rationale as every other R2.3-R2.5 app-* payload. Registered as its own
codegen artifact (topic "app-settings") so the generated validator doesn't
collide with the legacy island's own settings-state.ts.
"""

from __future__ import annotations

from dataclasses import dataclass


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
    minCompatibleSchemaVersion: int | None = None
