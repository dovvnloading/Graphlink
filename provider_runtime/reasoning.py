"""Reasoning-level mapping: per-provider translation of the app's unified
off/low/medium/high reasoning level into provider-specific request kwargs.

Function bodies are relocated VERBATIM from api_provider.py; only the
patch-seam rewrites below are new. Any name that lives in api_provider's
module namespace (module globals, sibling helpers, constants, and the
`ollama`/`urllib`/`requests` module bindings) is accessed late-bound as
`_mod.<name>` through an in-body deferred `import api_provider as _mod`,
NEVER via a module-top import here: a top-level `from api_provider import X`
would be a circular import (api_provider imports this module at ITS top)
AND would freeze the name at import time, making the test suite's
`monkeypatch.setattr(api_provider, "X", ...)` patches invisible to these
functions. The deferred-import-then-attribute pattern resolves the name on
api_provider at call time, so those patch seams keep working with zero test
changes. api_provider.py re-exports every name below, so every existing
`api_provider.<name>` caller and patch site is unchanged.
"""

from __future__ import annotations

import re


def normalize_reasoning_level(value: str | None) -> str:
    """Canonicalizes to one of REASONING_LEVELS, defaulting to "off" for
    anything unrecognized - the safe direction to fail in for a parameter
    that can affect cost/latency on a paid API: a garbled persisted value
    must never silently escalate to expensive reasoning."""
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _mod.REASONING_LEVELS else "off"


def _is_ollama_gpt_oss_model(model_name: str) -> bool:
    return "gpt-oss" in str(model_name or "").lower()


def _is_ollama_bool_reasoning_model(model_name: str) -> bool:
    normalized = str(model_name or "").lower()
    return any(token in normalized for token in ("qwen3", "deepseek", "qwq"))


def ollama_think_kwarg(model_name: str, level: str) -> bool | str | None:
    """The exact value for ollama_kwargs["think"], or None when this model
    has no reasoning control Ollama exposes at all (the kwarg should be
    omitted entirely - the same behavior this app had for any non-
    reasoning model before this change).

    gpt-oss REQUIRES a string level - Ollama's own issue tracker documents
    real bugs when a bool or "minimal" is sent instead (ollama/ollama
    #12004, #11766), so "off" (not a supported rung for a model that
    always reasons at some level) maps to the cheapest real one, "low",
    rather than attempting to disable it outright.

    qwen3/deepseek/qwq only expose a plain on/off think bool via Ollama's
    own /api/chat - there is no numeric or string-graded knob for these on
    Ollama specifically (Qwen's own native cloud API does support a
    numeric thinking_budget, but that is a different API this app does
    not call). See reasoning_budget_hint below for how low/medium/high
    still differ meaningfully for these models despite the bool ceiling."""
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    normalized = str(model_name or "").lower()
    if _mod._is_ollama_gpt_oss_model(normalized):
        return {"off": "low", "low": "low", "medium": "medium", "high": "high"}[level]
    if _mod._is_ollama_bool_reasoning_model(normalized):
        return level != "off"
    return None


def reasoning_budget_hint(level: str) -> str | None:
    """A real, if soft, SECOND axis of control for models whose only
    native lever (Ollama's think bool, Llama.cpp's /think directive) is a
    plain on/off switch: a natural-language nudge on how much reasoning to
    do, layered on TOP of enabling thinking, not a replacement for it.
    This is prompt-based guidance, not an API-enforced cap - honestly
    weaker than Anthropic's budget_tokens or Gemini's thinkingBudget - but
    a genuine difference in model behavior, not three identical "on"
    states wearing different labels."""
    return {
        "low": "Keep your internal reasoning brief - a few short steps, then answer.",
        "medium": None,
        "high": "Reason thoroughly through the problem and verify your answer before responding.",
    }.get(level)


def _append_system_hint(messages: list, hint: str | None) -> list:
    """Appends `hint` as its OWN leading system-role message. Deliberately
    separate from _inject_qwen_thinking_instruction below (which prepends
    INTO the first existing system message as a model-specific chat-
    template directive) - this is a generic additive instruction, not a
    template convention, so it gets its own message rather than risking
    interference with content already sitting in the real system prompt."""
    if not hint:
        return messages
    return [{"role": "system", "content": hint}, *messages]


def _is_anthropic_effort_model(model_id: str) -> bool:
    """True for Opus 4.7 and later, which use the newer `effort` parameter
    and REJECT the older thinking/budget_tokens shape outright (a real 400
    error, confirmed via Anthropic's own migration docs - these are not
    two names for the same mechanism). The pattern is deliberately
    generous about future versions (4.8, 4.9, 5.x, ...) since Anthropic's
    own docs describe this as the new direction, not a one-off; a model
    this doesn't recognize falls back to budget_tokens, which has been
    stable across a much wider range of models and is the safer default
    when a future model name is genuinely unrecognized."""
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    return bool(_mod._ANTHROPIC_EFFORT_MODEL_PATTERN.search(str(model_id or "")))


def anthropic_reasoning_kwargs(model_id: str, level: str, max_tokens: int) -> dict:
    """Kwargs to MERGE into an Anthropic request - empty for "off" (no
    thinking requested at all, the model's plain fast-path response).
    Also returns a raised `max_tokens` when the caller's own value would
    leave no room for the requested budget, so picking Low/Medium/High
    degrades to a clear, working response rather than a silent API
    rejection."""
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    if level == "off":
        return {}
    if _mod._is_anthropic_effort_model(model_id):
        # NESTED under output_config, not top-level. Verified directly
        # against the installed SDK (anthropic 0.116.0): messages.create has
        # no `effort` parameter and no **kwargs, but does take
        # `output_config`, whose OutputConfigParam declares
        # effort: Literal["low","medium","high","xhigh","max"] | None - so
        # the three levels passed here are already valid values, and only
        # the nesting was ever wrong.
        #
        # A flat {"effort": level} was silently useless on BOTH paths, which
        # is why nothing caught it: on the SDK path
        # _filter_kwargs_for_callable keeps only names the callable actually
        # declares, so the unknown key was dropped and the user's Low/Medium/
        # High selection simply never reached a paid API call - no error, no
        # signal; on the SDK-absent REST path the same key went out as a
        # top-level body field, which the API rejects with a 400 on every
        # request that has reasoning enabled.
        return {"output_config": {"effort": level}}
    budget = _mod._ANTHROPIC_BUDGET_TOKENS[level]
    result = {"thinking": {"type": "enabled", "budget_tokens": budget}}
    if max_tokens <= budget:
        result["max_tokens"] = budget + _mod._ANTHROPIC_THINKING_HEADROOM_TOKENS
    return result


def _is_gemini_3_model(model_id: str) -> bool:
    return bool(re.search(r"gemini-3", str(model_id or ""), re.IGNORECASE))


def _is_gemini_thinking_capable(model_id: str) -> bool:
    """2.5-series and 3-series document thinking configuration; 2.0-flash
    and the image-generation models do not, so the reasoning control
    should not be sent at all for those rather than silently including a
    parameter the model may ignore or reject."""
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    normalized = str(model_id or "").lower()
    if "-image" in normalized:
        return False
    if _mod._is_gemini_3_model(normalized):
        return True
    return "gemini-2.5" in normalized


def gemini_thinking_config(model_id: str, level: str) -> dict | None:
    """The `thinkingConfig` value to merge into generationConfig, or None
    for a model this app doesn't consider thinking-capable (the caller
    should omit the key entirely)."""
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    if not _mod._is_gemini_thinking_capable(model_id):
        return None
    if _mod._is_gemini_3_model(model_id):
        return {"thinkingLevel": _mod._GEMINI_THINKING_LEVEL[level]}
    return {"thinkingBudget": _mod._GEMINI_THINKING_BUDGET_TOKENS[level], "includeThoughts": level != "off"}


def _is_openai_reasoning_model(model_id: str) -> bool:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    normalized = str(model_id or "").strip().lower()
    return normalized.startswith(_mod._OPENAI_REASONING_MODEL_PREFIXES)


def openai_reasoning_kwargs(model_id: str, level: str) -> dict:
    """Empty for "off" (never sends reasoning_effort, deferring entirely
    to the model/server's own default) or for any model this app doesn't
    recognize as a reasoning model by name. Deliberately never sends
    "minimal" - real-world reports show it's inconsistently supported/
    buggy across reasoning model versions (see ollama/ollama#12004);
    "low" is the conservative floor instead."""
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    if level == "off" or not _mod._is_openai_reasoning_model(model_id):
        return {}
    return {"reasoning_effort": level}
