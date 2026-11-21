"""Small, cancellable, report-only Ollama performance and tool-call probe.

The first chat request loads the model and provides Ollama's own duration
counters. The second asks for a harmless tool call but NEVER executes it.
Neither request touches Graphlink's model assignments or saved settings.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import ollama

from provider_runtime.reasoning import ollama_think_kwarg


_TOOL_NAME = "graphlink_model_check"
_TOOL = {
    "type": "function",
    "function": {
        "name": _TOOL_NAME,
        "description": "Record the value for this harmless diagnostic. Call this function with value ok; it is never executed.",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
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
    """Measure one reply and request (but never execute) a native tool call.

    Reasoning models can spend a small generation budget entirely on
    thinking. Use the same per-family Ollama think setting as the workspace
    agent's Quick mode, and allow a bounded retry before calling the result
    inconclusive. keep_alive=0 unloads the model after every request.
    """
    client = ollama.AsyncClient(timeout=180.0)
    try:
        response = await client.chat(
            model=model_name,
            messages=[{"role": "user", "content": "In one short sentence, what is two plus two?"}],
            options={"num_predict": 32, "temperature": 0},
            keep_alive=0,
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
        # OllamaProvider._prepared sends exactly this native function schema,
        # rather than asking for a textual/JSON imitation of a function call.
        # There is no tool execution or Graphlink registry involved.
        messages = [
            {"role": "system", "content": (
                "This is a native function-calling test. Call graphlink_model_check "
                "with value ok using the provided tool. Do not answer in text, "
                "write JSON in the message, or explain the action."
            )},
            {"role": "user", "content": 'Call graphlink_model_check with {"value":"ok"}.'},
        ]
        think = ollama_think_kwarg(model_name, "off")
        request: dict = {
            "model": model_name,
            "messages": messages,
            "tools": [_TOOL],
            "options": {"num_predict": 256, "temperature": 0},
            "keep_alive": 0,
        }
        if think is not None:
            request["think"] = think

        saw_thinking = False
        try:
            for attempt in range(2):
                if attempt:
                    # Some templates still produce reasoning-only output even
                    # with think=False. Give the second attempt more room and
                    # repeat the native-function instruction, without feeding
                    # untrusted model text back into the prompt.
                    request["options"] = {"num_predict": 512, "temperature": 0}
                    request["messages"] = [
                        *messages,
                        {"role": "user", "content": (
                            "The test requires a native tool call, not a written answer. "
                            "Call graphlink_model_check now."
                        )},
                    ]
                probe = await client.chat(**request)
                message = _field(probe, "message")
                calls = _field(message, "tool_calls")
                if isinstance(calls, (list, tuple)) and any(
                    _field(_field(call, "function"), "name") == _TOOL_NAME for call in calls[:32]
                ):
                    report["toolCallingWorks"] = True
                    return report
                saw_thinking = saw_thinking or bool(
                    str(_field(message, "thinking") or "").strip()
                    or "<think>" in str(_field(message, "content") or "").lower()
                )
            reason = "reasoning output without a matching tool call" if saw_thinking else "no matching native tool call"
            report["message"] = (
                f"Tool probe inconclusive: {reason} after two attempts. "
                "This does not prove the model lacks tool calling."
            )
        except Exception as exc:  # noqa: BLE001 - request failure is not a negative capability result
            detail = str(exc).strip()[:240] or "Ollama did not respond to the tool request"
            report["message"] = (
                f"Could not run tool probe (inconclusive): {detail}. "
                "This does not prove the model lacks tool calling."
            )
        return report
    finally:
        # Cancellation must close the SDK's httpx pool even if a probe is pending.
        await client.close()
