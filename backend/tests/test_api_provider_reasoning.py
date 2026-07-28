"""Tests for api_provider.py's R8a graded-reasoning-level mapping functions.

api_provider.py itself has no dedicated test file elsewhere in the repo -
its provider-dispatch logic is only exercised indirectly through
backend/composer.py and backend/settings.py's own tests. The functions
covered here are pure (no network/SDK calls, no module-global state), so
they're unit-tested directly rather than only through those indirect paths.
"""

import api_provider


# -- normalize_reasoning_level -----------------------------------------------


def test_normalize_reasoning_level_accepts_the_four_real_values():
    for level in ("off", "low", "medium", "high"):
        assert api_provider.normalize_reasoning_level(level) == level


def test_normalize_reasoning_level_is_case_and_whitespace_insensitive():
    assert api_provider.normalize_reasoning_level(" HIGH ") == "high"
    assert api_provider.normalize_reasoning_level("Low") == "low"


def test_normalize_reasoning_level_falls_back_to_off_for_garbage():
    assert api_provider.normalize_reasoning_level("banana") == "off"
    assert api_provider.normalize_reasoning_level(None) == "off"
    assert api_provider.normalize_reasoning_level("") == "off"


# -- Ollama: think kwarg + budget hint ---------------------------------------


def test_ollama_think_kwarg_is_a_bool_for_qwen3_deepseek_qwq():
    for model in ("qwen3:8b", "deepseek-r1:32b", "qwq:32b", "QWEN3:LATEST"):
        assert api_provider.ollama_think_kwarg(model, "off") is False
        assert api_provider.ollama_think_kwarg(model, "low") is True
        assert api_provider.ollama_think_kwarg(model, "medium") is True
        assert api_provider.ollama_think_kwarg(model, "high") is True


def test_ollama_think_kwarg_is_a_string_for_gpt_oss():
    assert api_provider.ollama_think_kwarg("gpt-oss:20b", "low") == "low"
    assert api_provider.ollama_think_kwarg("gpt-oss:20b", "medium") == "medium"
    assert api_provider.ollama_think_kwarg("gpt-oss:20b", "high") == "high"
    # gpt-oss cannot fully disable reasoning - "off" maps to the cheapest
    # real rung instead of a bool/omitted kwarg.
    assert api_provider.ollama_think_kwarg("gpt-oss:20b", "off") == "low"


def test_ollama_think_kwarg_is_none_for_a_non_reasoning_model():
    assert api_provider.ollama_think_kwarg("llama3.1:8b", "high") is None
    assert api_provider.ollama_think_kwarg("", "high") is None


def test_reasoning_budget_hint_differs_by_level_and_is_none_for_medium():
    assert "brief" in api_provider.reasoning_budget_hint("low").lower()
    assert api_provider.reasoning_budget_hint("medium") is None
    assert "thorough" in api_provider.reasoning_budget_hint("high").lower()
    assert api_provider.reasoning_budget_hint("off") is None


def test_ollama_supports_reasoning_matches_the_same_model_families():
    assert api_provider.ollama_supports_reasoning("qwen3:8b") is True
    assert api_provider.ollama_supports_reasoning("gpt-oss:20b") is True
    assert api_provider.ollama_supports_reasoning("llama3.1:8b") is False


# -- Llama.cpp --------------------------------------------------------------


def test_llama_cpp_supports_reasoning_matches_qwen_and_qwq_gguf_paths():
    assert api_provider.llama_cpp_supports_reasoning("C:/models/qwen3-8b.gguf") is True
    assert api_provider.llama_cpp_supports_reasoning("C:/models/qwq-32b.gguf") is True
    assert api_provider.llama_cpp_supports_reasoning("C:/models/llama-3.1-8b.gguf") is False
    assert api_provider.llama_cpp_supports_reasoning("") is False


# -- Anthropic: budget_tokens vs effort, model-version detection -------------


def test_anthropic_reasoning_kwargs_off_sends_nothing():
    assert api_provider.anthropic_reasoning_kwargs("claude-sonnet-4-5", "off", 4096) == {}


def test_anthropic_reasoning_kwargs_uses_budget_tokens_for_older_models():
    result = api_provider.anthropic_reasoning_kwargs("claude-sonnet-4-5", "low", 4096)
    assert result["thinking"] == {"type": "enabled", "budget_tokens": 2000}
    assert "effort" not in result


def test_anthropic_reasoning_kwargs_bumps_max_tokens_when_budget_would_not_fit():
    # budget_tokens must stay strictly under max_tokens - a small default
    # (4096) can't hold a 16000-token "high" budget, so max_tokens must rise.
    result = api_provider.anthropic_reasoning_kwargs("claude-sonnet-4-5", "high", 4096)
    assert result["thinking"]["budget_tokens"] == 16000
    assert result["max_tokens"] > result["thinking"]["budget_tokens"]


def test_anthropic_reasoning_kwargs_does_not_bump_max_tokens_when_already_large_enough():
    result = api_provider.anthropic_reasoning_kwargs("claude-sonnet-4-5", "low", 50000)
    assert "max_tokens" not in result


def test_anthropic_reasoning_kwargs_uses_effort_for_opus_4_7_and_later():
    result = api_provider.anthropic_reasoning_kwargs("claude-opus-4-7", "high", 4096)
    assert result == {"effort": "high"}


def test_anthropic_reasoning_kwargs_effort_model_detection_is_forward_looking():
    # Deliberately generous about FUTURE versions (4.8, 4.9, 5.x) per the
    # function's own docstring - not just the one version known today.
    assert api_provider._is_anthropic_effort_model("claude-opus-4-8") is True
    assert api_provider._is_anthropic_effort_model("claude-opus-5-1") is True
    assert api_provider._is_anthropic_effort_model("claude-opus-4-6") is False
    assert api_provider._is_anthropic_effort_model("claude-sonnet-4-5") is False


def test_anthropic_supports_reasoning_excludes_known_legacy_models():
    assert api_provider.anthropic_supports_reasoning("claude-sonnet-4-5") is True
    assert api_provider.anthropic_supports_reasoning("claude-opus-4-7") is True
    assert api_provider.anthropic_supports_reasoning("claude-3-haiku-20240307") is False
    assert api_provider.anthropic_supports_reasoning("claude-2.1") is False
    assert api_provider.anthropic_supports_reasoning("claude-instant-1.2") is False
    assert api_provider.anthropic_supports_reasoning("") is False
    # Haiku 3.5 is a distinct, newer model - the exclusion pattern must not
    # over-match it just because it also contains "haiku-3".
    assert api_provider.anthropic_supports_reasoning("claude-3-5-haiku-20241022") is True


# -- Gemini: thinkingBudget vs thinkingLevel, model-family detection ---------


def test_gemini_thinking_config_uses_thinking_budget_for_25_series():
    assert api_provider.gemini_thinking_config("gemini-2.5-pro", "off") == {
        "thinkingBudget": 0,
        "includeThoughts": False,
    }
    assert api_provider.gemini_thinking_config("gemini-2.5-flash", "high") == {
        "thinkingBudget": 24576,
        "includeThoughts": True,
    }


def test_gemini_thinking_config_uses_thinking_level_for_gemini_3():
    assert api_provider.gemini_thinking_config("gemini-3-flash-preview", "low") == {"thinkingLevel": "LOW"}
    assert api_provider.gemini_thinking_config("gemini-3.1-pro-preview", "off") == {"thinkingLevel": "MINIMAL"}
    # Gemini 3's thinkingLevel only defines 3 rungs - medium and high both
    # resolve to HIGH rather than inventing a value the API doesn't define.
    assert api_provider.gemini_thinking_config("gemini-3-flash-preview", "medium") == {"thinkingLevel": "HIGH"}
    assert api_provider.gemini_thinking_config("gemini-3-flash-preview", "high") == {"thinkingLevel": "HIGH"}


def test_gemini_thinking_config_is_none_for_a_non_capable_model():
    assert api_provider.gemini_thinking_config("gemini-2.0-flash", "high") is None
    assert api_provider.gemini_thinking_config("gemini-2.5-flash-image", "high") is None


def test_gemini_supports_reasoning_matches_25_and_3_series_only():
    assert api_provider.gemini_supports_reasoning("gemini-2.5-pro") is True
    assert api_provider.gemini_supports_reasoning("gemini-3-flash-preview") is True
    assert api_provider.gemini_supports_reasoning("gemini-2.0-flash") is False


# -- OpenAI-compatible: reasoning_effort, gated by model name ----------------


def test_openai_reasoning_kwargs_off_sends_nothing():
    assert api_provider.openai_reasoning_kwargs("o3-mini", "off") == {}


def test_openai_reasoning_kwargs_sends_reasoning_effort_for_known_reasoning_models():
    for model in ("o1-preview", "o3-mini", "o4-mini", "gpt-5.1-codex-max", "gpt-oss-120b"):
        assert api_provider.openai_reasoning_kwargs(model, "high") == {"reasoning_effort": "high"}


def test_openai_reasoning_kwargs_never_sends_minimal():
    # Deliberately never sent - real-world reports show "minimal" is
    # inconsistently supported/buggy across reasoning model versions.
    result = api_provider.openai_reasoning_kwargs("o1-preview", "off")
    assert result.get("reasoning_effort") != "minimal"


def test_openai_reasoning_kwargs_sends_nothing_for_an_unrecognized_model():
    # A self-hosted/Groq/vLLM endpoint serving a non-reasoning model must
    # never receive a parameter it might reject with a hard 400.
    assert api_provider.openai_reasoning_kwargs("llama-3.1-70b", "high") == {}
    assert api_provider.openai_reasoning_kwargs("gpt-4o", "high") == {}


def test_openai_supports_reasoning_matches_known_prefixes_only():
    assert api_provider.openai_supports_reasoning("o1-preview") is True
    assert api_provider.openai_supports_reasoning("gpt-5.1-codex-max") is True
    assert api_provider.openai_supports_reasoning("gpt-4o") is False
    assert api_provider.openai_supports_reasoning("llama-3.1-70b") is False
