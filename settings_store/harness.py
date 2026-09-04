"""HarnessSettingsOps - workspace-agent trusted directory grants
(PLAN-2026-08-24 H4) for SettingsManager.

A MIXIN, not a standalone class: every method operates on the composing
class's own state (self.state) - it is composed exactly once, by
graphlink_settings_store.py's `class SettingsManager(PersistenceOps, ...,
HarnessSettingsOps, ...)`.

Method bodies are relocated VERBATIM from graphlink_settings_store.py; only
the class wrapper is new.
"""

from __future__ import annotations

from settings_store._composed import SettingsManagerParts


class HarnessSettingsOps(SettingsManagerParts):

    def get_harness_trusted_dirs(self) -> list:
        """PLAN-2026-08-24 H4: directories the user has explicitly granted
        the workspace agent access to, each recorded when the user picked
        it through the native folder dialog (the pick IS the consent - the
        gitlink local-root precedent). Checked at RUN time, not only at
        pick time: a session file naming a directory that is not in this
        list degrades to the scratch workspace instead of silently
        operating on a folder this install's user never granted (a session
        file is untrusted input - it can be hand-edited or imported).
        Stored as normalized absolute path strings; malformed entries
        dropped, the get_mcp_servers posture."""
        raw = self.state.get("harness_trusted_dirs", [])
        if not isinstance(raw, list):
            return []
        return [str(entry) for entry in raw if isinstance(entry, str) and entry.strip()]

    def add_harness_trusted_dir(self, path: str) -> None:
        """Appends one grant, deduplicated - additive rather than the
        set_recipes whole-list-replace posture, because grants accumulate
        one picked folder at a time and a replace API would let one buggy
        caller silently revoke every earlier consent."""
        clean = str(path or "").strip()
        if not clean:
            return
        current = self.get_harness_trusted_dirs()
        if clean in current:
            return
        self.state["harness_trusted_dirs"] = current + [clean]
        self._save_state()
