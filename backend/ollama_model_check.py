"""Small, cancellable, report-only Ollama performance and tool-call probe.

The first chat request loads the model and provides Ollama's own duration
counters. The second asks for a harmless tool call but NEVER executes it.
Neither request touches Graphlink's model assignments or saved settings.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import ollama


_TOOL_NAME = "graphlink_model_check"
_TOOL = {
    "type": "function",
    "function": {
        "name": _TOOL_NAME,
        "description": "A harmless capability probe. Graphlink will not execute this tool.",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    },
}


def _field(value: object, name: str) -> object:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _nonnegative_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


async def probe_ollama_model(model_name: str) -> dict[str, float | bool | str | None]:
    """Time a short chat, observe but never execute a tool call, then release HTTP resources.

    Keep the checked model available for five minutes, not permanently.
    /api/ps can then report its newly loaded state to the inventory card.
    """
    client = ollama.AsyncClient(timeout=180.0)
    try:
        response = await client.chat(
            model=model_name,
            messages=[{"role": "user", "content": "In one short sentence, what is two plus two?"}],
            options={"num_predict": 32, "temperature": 0},
            keep_alive="5m",
        )
        load_ns = _nonnegative_number(_field(response, "load_duration"))
        eval_ns = _nonnegative_number(_field(response, "eval_duration"))
        eval_count = _nonnegative_number(_field(response, "eval_count"))
        speed = (
            eval_count * 1_000_000_000 / eval_ns
            if eval_count is not None and eval_ns is not None and eval_ns > 0 else None
        )
        report: dict[str, float | bool | str | None] = {
            "loadSeconds": round(load_ns / 1_000_000_000, 2)
            if load_ns is not None and load_ns <= 1e18 else None,
            "tokensPerSecond": round(speed, 1) if speed is not None and math.isfinite(speed) else None,
            "toolCallingWorks": None,
            "message": "",
        }
        try:
            probe = await client.chat(
                model=model_name,
                messages=[{"role": "user", "content": "Call graphlink_model_check with value 'ok'."}],
                tools=[_TOOL],
                options={"num_predict": 64, "temperature": 0},
                keep_alive="5m",
            )
            calls = _field(_field(probe, "message"), "tool_calls")
            if not isinstance(calls, (list, tuple)):
                calls = []
            works = any(_field(_field(call, "function"), "name") == _TOOL_NAME for call in calls[:32])
            report["toolCallingWorks"] = works
            if not works:
                report["message"] = "The model did not return the requested tool call."
        except Exception as exc:  # noqa: BLE001 - a failed tool request is not a negative result
            # A rejected request does not prove the model lacks tool-calling support.
            report["toolCallingWorks"] = None
            detail = str(exc).strip()[:240] or "Ollama did not respond to the tool request"
            report["message"] = f"Could not run tool probe: {detail}"
        return report
    finally:
        # AsyncClient owns an httpx connection pool. Cancellation must close
        # it even when the first request or the tool probe is still pending.
        await client.close()
