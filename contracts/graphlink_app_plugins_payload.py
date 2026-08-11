"""The SPA plugins topic's wire contract (Qt-removal plan R2.5).

Field-for-field the same shape as graphlink_plugin_picker_payload.py's
PluginPickerStatePayload (icon already dropped there too), registered as a
distinct codegen artifact so the SPA's validator is generated from this
independent Qt-free source rather than importing anything Qt-coupled.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AppPluginEntryPayload:
    name: str
    description: str


@dataclass
class AppPluginCategoryPayload:
    name: str
    description: str
    plugins: list[AppPluginEntryPayload]


@dataclass
class AppPluginGrantPayload:
    """ADR-014 stage 14.4: one row per DISTINCT non-built-in discovered
    plugin_id - see backend/plugins.py's own _plugin_grants_payload for the
    exact "one row per plugin, not per picker entry, built-ins never
    appear here" construction rule. `scopes` is the plugin's own
    self-reported [scopes].grants manifest declaration (read-only in the
    Settings UI, matching McpServerConfigPayload.scopes' own read-only
    posture there); `granted` is the ONE thing a Settings checkbox actually
    writes back, via the new setPluginGrant intent."""

    pluginId: str
    name: str
    scopes: list[str]
    granted: bool


@dataclass
class AppPluginsStatePayload:
    schemaVersion: int
    revision: int
    categories: list[AppPluginCategoryPayload]
    # ADR-014 stage 14.4: the Settings > Plugins page's own data - see
    # AppPluginGrantPayload's own docstring.
    grants: list[AppPluginGrantPayload]
    minCompatibleSchemaVersion: int | None = None
