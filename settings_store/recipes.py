"""RecipesOps - the user's saved Builder recipes (ADR-008 stage 8.6) for
SettingsManager: named plans that seed a build (goal + step titles + default
mode).

A MIXIN, not a standalone class: every method operates on the composing
class's own state (self.state) - it is composed exactly once, by
graphlink_settings_store.py's `class SettingsManager(PersistenceOps, ...,
RecipesOps, ...)`.

Method bodies are relocated VERBATIM from graphlink_settings_store.py; only
the class wrapper is new.
"""

from __future__ import annotations


class RecipesOps:

    @staticmethod
    def _normalize_recipe(entry) -> "dict | None":
        """One recipe's canonical shape - shared by get/set so a round trip
        is always well-formed (the get_mcp_servers posture exactly)."""
        if not isinstance(entry, dict):
            return None
        name = str(entry.get("name", "")).strip()
        goal = str(entry.get("goal", "")).strip()
        if not name or not goal:
            return None
        steps = [str(s).strip() for s in (entry.get("steps") or []) if str(s).strip()]
        mode = str(entry.get("mode") or "copilot")
        return {
            "name": name,
            "description": str(entry.get("description", "")),
            "goal": goal,
            "steps": steps[:20],
            "mode": mode if mode in ("copilot", "autopilot") else "copilot",
        }

    def get_recipes(self) -> list:
        """ADR-008 stage 8.6: the user's saved Builder recipes - named plans
        that seed a build (goal + step titles + default mode). Plain
        JSON-safe dicts, malformed entries dropped, same store posture as
        get_mcp_servers directly above. Built-in recipes are NOT stored
        here - backend/builder.py owns those as constants and merges them
        read-only at list time."""
        raw = self.state.get("builder_recipes", [])
        if not isinstance(raw, list):
            return []
        recipes = []
        for entry in raw:
            normalized = self._normalize_recipe(entry)
            if normalized is not None:
                recipes.append(normalized)
        return recipes

    def set_recipes(self, recipes: list) -> None:
        """Replaces the WHOLE recipe list - the set_mcp_servers posture: a
        small user-managed collection edited as a unit."""
        normalized = []
        for entry in (recipes or []):
            candidate = self._normalize_recipe(entry)
            if candidate is not None:
                normalized.append(candidate)
        self.state["builder_recipes"] = normalized
        self._save_state()
