"""Read and validated write intents for the exact-model-ID pricing table.

Separate from register_settings_general_intents to respect the register*
function-length ratchet; all changes republish consumers of effective prices.
"""

from __future__ import annotations

import asyncio
import math

from backend.api._settings_shared import run_locked
from backend.events import SessionBus
from graphlink_settings_store import SettingsManager


def _validated_pricing_overrides(raw: object) -> dict[str, dict[str, float]]:
    """Reject a malformed write in full, without silently dropping entries."""
    if not isinstance(raw, dict):
        raise ValueError("Model pricing must be a table keyed by model ID.")
    normalized: dict[str, dict[str, float]] = {}
    for model_id, prices in raw.items():
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("Model ID must be non-empty.")
        key = model_id.strip().lower()
        if key in normalized:
            raise ValueError(f"Duplicate model ID: {model_id}")
        if not isinstance(prices, dict) or set(prices) != {"input", "output"}:
            raise ValueError(f"Model {model_id}: input and output prices are required.")
        values = []
        for name in ("input", "output"):
            value = prices[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"Model {model_id}: {name} price must be a finite non-negative number.")
            try:
                price = float(value)
            except (ValueError, OverflowError):
                raise ValueError(f"Model {model_id}: {name} price must be a finite non-negative number.") from None
            if not math.isfinite(price) or price < 0:
                raise ValueError(f"Model {model_id}: {name} price must be a finite non-negative number.")
            values.append(price)
        normalized[key] = {"input": values[0], "output": values[1]}
    return normalized


def register_settings_pricing_intents(bus: SessionBus, manager: SettingsManager) -> None:
    async def get_pricing_overrides():
        return manager.get_pricing_overrides()

    async def set_pricing_overrides(overrides: object):
        validated = _validated_pricing_overrides(overrides)
        await asyncio.to_thread(run_locked, manager.set_pricing_overrides, validated)
        await bus.publish("app-settings")
        # Recalculate scene reply badges and the current composer token counter.
        for topic in ("scene", "token-counter"):
            if bus.has_topic(topic):
                await bus.publish(topic)
        return manager.get_pricing_overrides()

    bus.register_intent("app-settings", "getPricingOverrides", get_pricing_overrides)
    bus.register_intent("app-settings", "setPricingOverrides", set_pricing_overrides)
