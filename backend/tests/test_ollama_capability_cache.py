"""api_provider's Ollama model-capability cache and its invalidation
(Qt-removal plan R7.3).

R7.3 gap: api_provider.py is a confirmed Qt-free survivor module backend/
imports directly (agents.py:72, response_parsing.py:25) and _get_ollama_
capabilities (api_provider.py:658) is exercised on every real chat dispatch
through Ollama to decide whether to send image/audio content - live
production logic, not legacy-only code. Before this file, its own cache-
invalidation rules (a real historical bug fix per the module's own docstring:
the capability cache never expired, so a model that gained a capability via
a fresh pull mid-session kept whatever answer was cached the first time it
was seen) had zero backend/tests coverage. Ported from graphlink_app/tests/
test_ollama_capability_cache_invalidation.py's TestInvalidateOllamaCapability
Cache class - its sibling TestModelPullWorkerThreadInvalidatesCacheOnSuccess
class is NOT ported: ModelPullWorkerThread (graphlink_agents_tools.py) is a
QThread subclass with no backend/ equivalent yet - there is no model-pull
mechanism in the new stack at all until R7.4 builds the deferred Ollama
settings page, so there is nothing yet for that half to regress.
"""

from unittest.mock import patch

import api_provider


def test_invalidate_ollama_capability_cache_clears_a_specific_model_entry(monkeypatch):
    monkeypatch.setattr(api_provider, "_OLLAMA_CAPABILITY_CACHE", {"model-a": {"vision"}, "model-b": {"audio"}})

    api_provider.invalidate_ollama_capability_cache("model-a")

    assert "model-a" not in api_provider._OLLAMA_CAPABILITY_CACHE
    assert "model-b" in api_provider._OLLAMA_CAPABILITY_CACHE


def test_invalidate_ollama_capability_cache_model_name_lookup_is_case_and_whitespace_insensitive(monkeypatch):
    monkeypatch.setattr(api_provider, "_OLLAMA_CAPABILITY_CACHE", {"gemma4:e4b": {"audio"}})

    api_provider.invalidate_ollama_capability_cache("  Gemma4:E4B  ")

    assert api_provider._OLLAMA_CAPABILITY_CACHE == {}


def test_invalidate_ollama_capability_cache_clearing_an_uncached_model_is_a_safe_no_op(monkeypatch):
    monkeypatch.setattr(api_provider, "_OLLAMA_CAPABILITY_CACHE", {"model-a": {"vision"}})

    api_provider.invalidate_ollama_capability_cache("never-cached-model")

    assert api_provider._OLLAMA_CAPABILITY_CACHE == {"model-a": {"vision"}}


def test_invalidate_ollama_capability_cache_no_argument_clears_the_entire_cache(monkeypatch):
    monkeypatch.setattr(api_provider, "_OLLAMA_CAPABILITY_CACHE", {"model-a": {"vision"}, "model-b": {"audio"}})

    api_provider.invalidate_ollama_capability_cache()

    assert api_provider._OLLAMA_CAPABILITY_CACHE == {}


def test_next_lookup_after_invalidation_re_fetches_from_ollama_show(monkeypatch):
    monkeypatch.setattr(api_provider, "_OLLAMA_CAPABILITY_CACHE", {"model-a": {"vision"}})

    with patch("api_provider.ollama.show", return_value={"capabilities": ["audio"]}) as mock_show:
        api_provider.invalidate_ollama_capability_cache("model-a")
        result = api_provider._get_ollama_capabilities("model-a")

    mock_show.assert_called_once()
    assert result == {"audio"}


def test_a_lookup_with_no_invalidation_never_touches_ollama_show_a_second_time(monkeypatch):
    # The other half of the contract the test above proves: a REPEAT lookup
    # with no invalidation in between must serve the cached answer, not
    # re-fetch - this is the entire reason the cache exists.
    monkeypatch.setattr(api_provider, "_OLLAMA_CAPABILITY_CACHE", {})

    with patch("api_provider.ollama.show", return_value={"capabilities": ["vision"]}) as mock_show:
        first = api_provider._get_ollama_capabilities("model-a")
        second = api_provider._get_ollama_capabilities("model-a")

    mock_show.assert_called_once()
    assert first == second == {"vision"}


def test_a_transient_probe_failure_is_not_cached_so_the_next_call_retries(monkeypatch):
    """review-fix: a failed probe (daemon unreachable, a brief restart)
    used to be cached as None forever - ollama_supports_tools/embedding
    map None to False, so one bad moment permanently and silently blocked
    the Builder with a false "model does not support tool calling" error
    even after the daemon came back up. Same negative-caching bug class
    as crawl_etiquette's robots.txt fix."""
    monkeypatch.setattr(api_provider, "_OLLAMA_CAPABILITY_CACHE", {})

    with patch("api_provider.ollama.show", side_effect=ConnectionError("daemon unreachable")):
        first = api_provider._get_ollama_capabilities("model-a")

    assert first is None
    assert "model-a" not in api_provider._OLLAMA_CAPABILITY_CACHE, (
        "a transient probe exception must not poison the cache with a "
        "permanent negative result"
    )

    with patch("api_provider.ollama.show", return_value={"capabilities": ["tools"]}) as mock_show_2:
        second = api_provider._get_ollama_capabilities("model-a")

    mock_show_2.assert_called_once()
    assert second == {"tools"}, "the next call must retry the probe fresh, not serve a cached failure"
