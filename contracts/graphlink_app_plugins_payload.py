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
class AppPluginsStatePayload:
    schemaVersion: int
    revision: int
    categories: list[AppPluginCategoryPayload]
    minCompatibleSchemaVersion: int | None = None
