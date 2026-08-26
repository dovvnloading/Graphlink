"""PricingSettingsOps - the user-editable local pricing override table
(ADR-016 stage 16.2) for SettingsManager.

A MIXIN, not a standalone class: every method operates on the composing
class's own state (self.state) - it is composed exactly once, by
graphlink_settings_store.py's `class SettingsManager(PersistenceOps, ...,
PricingSettingsOps)`.

Method bodies are relocated VERBATIM from graphlink_settings_store.py; only
the class wrapper is new.
"""

from __future__ import annotations


class PricingSettingsOps:

    def get_pricing_overrides(self) -> dict:
        """ADR-016 stage 16.2: user-editable local pricing table, keyed by
        EXACT model id (lowercased) -> {"input": usd_per_mtok, "output":
        usd_per_mtok} - see backend/token_counter.py's estimate_cost_usd for
        how this is consumed (checked before the built-in prefix table).
        Malformed entries are dropped, not raised on - same posture as
        get_mcp_servers above."""
        raw = self.state.get("pricing_overrides", {})
        if not isinstance(raw, dict):
            return {}
        overrides = {}
        for model_id, prices in raw.items():
            if not isinstance(prices, dict):
                continue
            key = str(model_id).strip().lower()
            if not key:
                continue
            try:
                input_price = float(prices.get("input", 0.0))
                output_price = float(prices.get("output", 0.0))
            except (TypeError, ValueError):
                continue
            if input_price < 0 or output_price < 0:
                continue
            overrides[key] = {"input": input_price, "output": output_price}
        return overrides

    def set_pricing_overrides(self, overrides: dict) -> None:
        """Replaces the WHOLE override table, same "replace the collection"
        posture as set_mcp_servers - a small, user-managed table edited as a
        unit. Validates the same way get_pricing_overrides reads back, so a
        round trip through set then get is always well-formed."""
        normalized = {}
        for model_id, prices in (overrides or {}).items():
            if not isinstance(prices, dict):
                continue
            key = str(model_id).strip().lower()
            if not key:
                continue
            try:
                input_price = float(prices.get("input", 0.0))
                output_price = float(prices.get("output", 0.0))
            except (TypeError, ValueError):
                continue
            if input_price < 0 or output_price < 0:
                continue
            normalized[key] = {"input": input_price, "output": output_price}
        self.state["pricing_overrides"] = normalized
        self._save_state()
