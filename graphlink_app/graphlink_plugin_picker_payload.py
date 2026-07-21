"""The plugin-picker island's outbound wire contract (Phase 6 increment 3).

Absorbs graphlink_plugins/graphlink_plugin_picker.py's PluginFlyoutPanel
(deleted this increment) - the category-rail + plugin-list flyout opened by
the toolbar's "Plugins" button.

THIS IS A WIRE FORMAT, NOT A DOMAIN MODEL - see graphlink_composer_payload.py
for the fuller rationale. `PluginPortal.get_plugin_categories()`'s own
per-plugin dict carries a `callback` key holding a live bound Python method -
not serializable and meaningless in a web/JSON context, so it's stripped
entirely; `name` is kept as the round-trip identifier `executePlugin(name)`
sends back, exactly what `execute_plugin(plugin_name)` already expects.
Category/plugin `icon` fields (qtawesome icon-name strings like
"fa5s.compass") are deliberately dropped - not carried over at all - the
same choice already made for About/Help in Phase 4: a qtawesome name only
resolves to a real glyph via Python's own qta.icon() at Qt-widget render
time, and isn't portable to a web island without a new icon-library
dependency this migration has never needed elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PluginEntry:
    name: str
    description: str


@dataclass
class PluginCategory:
    name: str
    description: str
    plugins: list[PluginEntry]


@dataclass
class PluginPickerStatePayload:
    """The complete published snapshot, including the envelope fields
    IslandBridge.publish() adds to every island's payload."""

    schemaVersion: int
    revision: int
    categories: list[PluginCategory]
    # See ComposerStatePayload's identical field for the full negotiation
    # rationale; optional for the same reason (models a sender predating this
    # field, not today's - IslandBridge.publish() always emits it).
    minCompatibleSchemaVersion: int | None = None
