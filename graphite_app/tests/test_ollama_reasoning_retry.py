"""Tests for api_provider.chat()'s Ollama retry-on-reasoning-without-answer behavior.

Some local reasoning-capable models occasionally return chain-of-thought "thinking"
text but an empty final `content` - often just sampling variance (the model didn't
finish "thinking" within its own budget that particular attempt), not a persistent
configuration problem. Previously this raised immediately on the first occurrence; now
the identical request is retried up to 3 total attempts before surfacing an error, and
only the terminal failure (all attempts exhausted) is raised to the caller.

A short backoff now runs between attempts (see doc/ARCHITECTURE_REVIEW_FINDINGS.md #36 -
previously attempts were re-sent with no delay at all). Every test here patches
api_provider.time.sleep to a no-op so the suite doesn't actually pause for real -
test_backoff_between_attempts specifically asserts the backoff is invoked.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import api_provider
import graphite_config as config


def _set_ollama_model(monkeypatch, model_name="test-model"):
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, model_name)
    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_OLLAMA)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    # Keep the retry-backoff tests fast and deterministic - real timing is covered by
    # test_backoff_between_attempts asserting time.sleep was actually called.
    monkeypatch.setattr(api_provider.time, "sleep", lambda seconds: None)


def _reasoning_only_response():
    return {"message": {"content": "", "thinking": "some internal reasoning", "role": "assistant"}}


def _real_answer_response(text="The final answer."):
    return {"message": {"content": text, "role": "assistant"}}


class TestOllamaReasoningRetry:
    def test_succeeds_on_the_second_attempt_after_one_reasoning_only_response(self, monkeypatch):
        _set_ollama_model(monkeypatch)
        with patch(
            "api_provider.ollama.chat",
            side_effect=[_reasoning_only_response(), _real_answer_response("Got there.")],
        ) as mock_chat:
            result = api_provider.chat(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}])

        assert result["message"]["content"] == "Got there."
        assert mock_chat.call_count == 2

    def test_succeeds_on_the_third_and_final_attempt(self, monkeypatch):
        _set_ollama_model(monkeypatch)
        with patch(
            "api_provider.ollama.chat",
            side_effect=[
                _reasoning_only_response(),
                _reasoning_only_response(),
                _real_answer_response("Third time's the charm."),
            ],
        ) as mock_chat:
            result = api_provider.chat(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}])

        assert result["message"]["content"] == "Third time's the charm."
        assert mock_chat.call_count == 3

    def test_raises_after_exhausting_all_three_attempts(self, monkeypatch):
        _set_ollama_model(monkeypatch)
        with patch(
            "api_provider.ollama.chat",
            side_effect=[_reasoning_only_response(), _reasoning_only_response(), _reasoning_only_response()],
        ) as mock_chat:
            with pytest.raises(RuntimeError, match="Ollama returned reasoning but no final answer"):
                api_provider.chat(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}])

        assert mock_chat.call_count == 3

    def test_first_attempt_success_does_not_retry(self, monkeypatch):
        _set_ollama_model(monkeypatch)
        with patch("api_provider.ollama.chat", side_effect=[_real_answer_response("Immediate answer.")]) as mock_chat:
            result = api_provider.chat(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}])

        assert result["message"]["content"] == "Immediate answer."
        assert mock_chat.call_count == 1

    def test_a_truly_empty_response_is_not_retried(self, monkeypatch):
        # An empty content AND no reasoning/thinking text at all is a different failure
        # mode (ReasoningWithoutAnswerError vs. plain RuntimeError) - this one is not
        # retried, since there's no reasoning-budget-timing story to explain a
        # transient fix on retry.
        _set_ollama_model(monkeypatch)
        with patch(
            "api_provider.ollama.chat",
            side_effect=[{"message": {"content": "", "role": "assistant"}}],
        ) as mock_chat:
            with pytest.raises(RuntimeError, match="returned an empty response"):
                api_provider.chat(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}])

        assert mock_chat.call_count == 1


class TestRetryBackoff:
    def test_backoff_runs_between_attempts_but_not_before_the_first(self, monkeypatch):
        _set_ollama_model(monkeypatch)
        with patch("api_provider.time.sleep") as mock_sleep, patch(
            "api_provider.ollama.chat",
            side_effect=[
                _reasoning_only_response(),
                _reasoning_only_response(),
                _real_answer_response("Third time's the charm."),
            ],
        ):
            api_provider.chat(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}])

        # 3 attempts -> backoff before attempt 2 and before attempt 3, none before attempt 1.
        assert mock_sleep.call_count == 2
        for call in mock_sleep.call_args_list:
            assert call.args[0] == api_provider._OLLAMA_REASONING_RETRY_BACKOFF_SECONDS

    def test_no_backoff_when_the_first_attempt_succeeds(self, monkeypatch):
        _set_ollama_model(monkeypatch)
        with patch("api_provider.time.sleep") as mock_sleep, patch(
            "api_provider.ollama.chat", side_effect=[_real_answer_response("Immediate answer.")]
        ):
            api_provider.chat(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}])

        mock_sleep.assert_not_called()

    def test_cancellation_during_backoff_is_honored(self, monkeypatch):
        import threading

        _set_ollama_model(monkeypatch)
        cancel_event = threading.Event()

        def _sleep_then_cancel(seconds):
            cancel_event.set()

        with patch("api_provider.time.sleep", side_effect=_sleep_then_cancel), patch(
            "api_provider.ollama.chat",
            side_effect=[_reasoning_only_response(), _real_answer_response("Should not get here.")],
        ) as mock_chat:
            with pytest.raises(api_provider.RequestCancelledError):
                api_provider.chat(
                    task=config.TASK_CHAT,
                    messages=[{"role": "user", "content": "hi"}],
                    cancellation_event=cancel_event,
                )

        # Cancellation is checked immediately after the backoff sleep, before the
        # second ollama.chat() call is made.
        assert mock_chat.call_count == 1
