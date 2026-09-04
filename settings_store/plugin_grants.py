"""PluginGrantsOps - install-time consent grants for discovered third-party
plugins (ADR-014 stage 14.4) for SettingsManager.

A MIXIN, not a standalone class: every method operates on the composing
class's own state (self.state) - it is composed exactly once, by
graphlink_settings_store.py's `class SettingsManager(PersistenceOps, ...,
PluginGrantsOps, ...)`.

Method bodies are relocated VERBATIM from graphlink_settings_store.py; only
the class wrapper is new.
"""

from __future__ import annotations

from settings_store._composed import SettingsManagerParts


class PluginGrantsOps(SettingsManagerParts):

    def get_plugin_grants(self) -> dict:
        """ADR-014 stage 14.4: install-time consent grants for discovered
        THIRD-PARTY/demo-mechanism plugins - built-in plugins never consult
        this at all (see backend/plugins.py's own _execute_discovered_plugin
        for the enforcement point). Keyed by plugin_id -> bool. A plugin_id
        with no entry here reads as NOT granted via the caller's own
        `.get(plugin_id, False)` (this method does not synthesize that
        default itself - it returns exactly what is persisted, same posture
        as get_mcp_servers above returning exactly the persisted, validated
        server list rather than padding in every possible name). Malformed
        entries (a non-string key, an empty key) are dropped rather than
        raised on, same fail-soft posture as every other collection getter
        in this class."""
        raw = self.state.get("plugin_grants", {})
        if not isinstance(raw, dict):
            return {}
        grants = {}
        for plugin_id, granted in raw.items():
            key = str(plugin_id).strip()
            if not key:
                continue
            grants[key] = bool(granted)
        return grants

    def set_plugin_grant(self, plugin_id: str, granted: bool) -> None:
        """Sets ONE plugin's grant, leaving every other plugin's grant
        untouched - deliberately NOT set_mcp_servers' whole-collection-
        replace posture (that fits an add/remove/edit-a-server list editor;
        this fits a single Settings checkbox toggling exactly one plugin's
        consent at a time, ADR-014 stage 14.4's own "install-time consent"
        model - decided once, ahead of time, per plugin, not a live per-call
        approval prompt)."""
        key = str(plugin_id or "").strip()
        if not key:
            return
        grants = self.get_plugin_grants()
        grants[key] = bool(granted)
        self.state["plugin_grants"] = grants
        self._save_state()
