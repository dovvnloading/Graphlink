"""Tests for api_provider.chat_stream() (Qt-removal R4.4: true token streaming).

chat_stream() is an additive, wholly new function alongside chat() - it must never
change chat()'s own behavior (see test_import_chain.py / test_no_qt_anywhere.py, run
unmodified as part of the same increment's verification: chat_stream/on_chunk add zero
new import edges).

This file covers exactly the two backend-only items from the R4.4 design spec's test
plan (section 6) that fall entirely inside api_provider.py:

1. Fallback path unchanged for non-streaming providers - every provider/local-type this
   increment doesn't cover (API mode, or a non-Ollama local provider) degenerates to one
   blocking chat() call plus exactly one synthetic on_chunk(full_text, False) call, and
   returns the exact value chat() alone would for the same fixture.
6. Reset-on-retry - driving the real chat_stream against a monkeypatched ollama.chat
   that raises ReasoningWithoutAnswerError-triggering content on attempt 1 and real
   content on attempt 2: on_chunk("", True) fires exactly once between attempts, and all
   deltas after that reset concatenate to attempt 2's raw content.

(Items 2-5, 7-9 of the spec's test list live in backend/tests/test_agents.py and are out
of scope for this file - this file only exercises the provider layer.)
"""

import sys
import threading
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import api_provider
import graphlink_task_config as config


def _set_ollama_model(monkeypatch, model_name="test-model"):
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, model_name)
    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_OLLAMA)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    # Same convention as test_ollama_reasoning_retry.py: keep the retry-backoff path
    # fast/deterministic in tests that exercise more than one attempt.
    monkeypatch.setattr(api_provider.time, "sleep", lambda seconds: None)


class _FakeStream:
    """Stand-in for the generator ollama.chat(..., stream=True) returns.

    Real ollama.chat(stream=True) returns a plain Python generator (see
    ollama/_client.py's Client._request -> inner(), which `yield`s), so `.close()` is
    always available on it for free - chat_stream's `finally: stream.close()` relies on
    exactly that. This fake exposes the same iterate+close() shape explicitly, and
    records whether close() was ever invoked so tests can assert on it.
    """

    def __init__(self, chunks):
        self._iter = iter(chunks)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._iter)

    def close(self):
        self.closed = True


class TestFallbackPathUnchangedForNonStreamingProviders:
    """Spec section 6 item 1."""

    @staticmethod
    def _fixture_response():
        return {"message": {"content": "The full non-streamed answer.", "role": "assistant"}}

    def test_api_mode_falls_back_to_one_chat_call_and_one_synthetic_chunk(self, monkeypatch):
        monkeypatch.setattr(api_provider, "USE_API_MODE", True)
        fixture = self._fixture_response()
        received = []

        with patch("api_provider.chat", return_value=fixture) as mock_chat:
            result = api_provider.chat_stream(
                task=config.TASK_CHAT,
                messages=[{"role": "user", "content": "hi"}],
                on_chunk=lambda delta, reset: received.append((delta, reset)),
            )

        mock_chat.assert_called_once_with(config.TASK_CHAT, [{"role": "user", "content": "hi"}])
        assert result == fixture
        assert received == [("The full non-streamed answer.", False)]

    def test_non_ollama_local_provider_falls_back_identically(self, monkeypatch):
        monkeypatch.setattr(api_provider, "USE_API_MODE", False)
        monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_LLAMACPP)
        fixture = self._fixture_response()
        received = []

        with patch("api_provider.chat", return_value=fixture) as mock_chat:
            result = api_provider.chat_stream(
                task=config.TASK_CHAT,
                messages=[{"role": "user", "content": "hi"}],
                on_chunk=lambda delta, reset: received.append((delta, reset)),
            )

        assert mock_chat.call_count == 1
        assert result == fixture
        assert received == [("The full non-streamed answer.", False)]

    def test_fallback_return_value_is_identical_to_calling_chat_directly(self, monkeypatch):
        # Proves chat_stream's fallback returns the exact same value chat() alone would
        # produce for the identical fixture, not merely "a dict shaped like it".
        monkeypatch.setattr(api_provider, "USE_API_MODE", True)
        fixture = self._fixture_response()

        with patch("api_provider.chat", return_value=fixture):
            direct_result = api_provider.chat(task=config.TASK_CHAT, messages=[{"role": "user", "content": "hi"}])
        with patch("api_provider.chat", return_value=fixture):
            streamed_result = api_provider.chat_stream(
                task=config.TASK_CHAT,
                messages=[{"role": "user", "content": "hi"}],
                on_chunk=lambda delta, reset: None,
            )

        assert streamed_result == direct_result

    def test_cancellation_kwarg_still_flows_through_to_the_underlying_chat_call(self, monkeypatch):
        monkeypatch.setattr(api_provider, "USE_API_MODE", True)
        cancel_event = threading.Event()
        fixture = self._fixture_response()

        with patch("api_provider.chat", return_value=fixture) as mock_chat:
            api_provider.chat_stream(
                task=config.TASK_CHAT,
                messages=[{"role": "user", "content": "hi"}],
                on_chunk=lambda delta, reset: None,
                cancellation_event=cancel_event,
            )

        assert mock_chat.call_args.kwargs.get("cancellation_event") is cancel_event


class TestResetOnRetryPreservesTheReasoningRetryLoop:
    """Spec section 6 item 6."""

    def test_reset_emitted_once_between_attempts_and_post_reset_deltas_match_attempt_two(self, monkeypatch):
        _set_ollama_model(monkeypatch)

        # Attempt 1: entirely wrapped in <think>...</think> with nothing else -
        # split_reasoning_and_content yields reasoning but an empty visible answer,
        # which _compose_reasoned_response turns into ReasoningWithoutAnswerError.
        attempt_one_chunks = [
            {"message": {"content": "<think>some reasoning"}, "done": False},
            {"message": {"content": " only</think>"}, "done": True},
        ]
        # Attempt 2: a plain final answer, no reasoning wrapper - succeeds.
        attempt_two_chunks = [
            {"message": {"content": "This is "}, "done": False},
            {"message": {"content": "the final real answer."}, "done": True},
        ]
        attempt_two_raw_content = "This is the final real answer."

        events = []  # (delta, reset) in call order

        with patch(
            "api_provider.ollama.chat",
            side_effect=[_FakeStream(attempt_one_chunks), _FakeStream(attempt_two_chunks)],
        ) as mock_chat:
            result = api_provider.chat_stream(
                task=config.TASK_CHAT,
                messages=[{"role": "user", "content": "hi"}],
                on_chunk=lambda delta, reset: events.append((delta, reset)),
            )

        assert mock_chat.call_count == 2
        assert result["message"]["content"] == attempt_two_raw_content

        reset_events = [e for e in events if e[1] is True]
        assert len(reset_events) == 1
        assert reset_events[0] == ("", True)

        reset_index = events.index(("", True))
        deltas_before_reset = [delta for delta, reset in events[:reset_index] if not reset]
        deltas_after_reset = [delta for delta, reset in events[reset_index + 1:] if not reset]

        assert "".join(deltas_before_reset) == "<think>some reasoning only</think>"
        assert "".join(deltas_after_reset) == attempt_two_raw_content

    def test_succeeds_immediately_with_no_reset_when_the_first_attempt_has_a_real_answer(self, monkeypatch):
        _set_ollama_model(monkeypatch)
        chunks = [
            {"message": {"content": "Immediate "}, "done": False},
            {"message": {"content": "answer."}, "done": True},
        ]
        events = []

        with patch("api_provider.ollama.chat", side_effect=[_FakeStream(chunks)]) as mock_chat:
            result = api_provider.chat_stream(
                task=config.TASK_CHAT,
                messages=[{"role": "user", "content": "hi"}],
                on_chunk=lambda delta, reset: events.append((delta, reset)),
            )

        assert mock_chat.call_count == 1
        assert result["message"]["content"] == "Immediate answer."
        assert all(reset is False for _, reset in events)
        assert "".join(delta for delta, _ in events) == "Immediate answer."

    def test_raises_after_exhausting_all_three_attempts_with_two_resets(self, monkeypatch):
        # Parity with test_ollama_reasoning_retry.py's
        # test_raises_after_exhausting_all_three_attempts, but through the streaming path:
        # 3 attempts all reasoning-only -> RuntimeError, with a reset event before
        # attempt 2 and before attempt 3 (2 total), never before attempt 1.
        _set_ollama_model(monkeypatch)
        reasoning_only_chunks = [
            {"message": {"content": "<think>still thinking</think>"}, "done": True},
        ]
        events = []

        with patch(
            "api_provider.ollama.chat",
            side_effect=[
                _FakeStream(list(reasoning_only_chunks)),
                _FakeStream(list(reasoning_only_chunks)),
                _FakeStream(list(reasoning_only_chunks)),
            ],
        ) as mock_chat:
            with pytest.raises(RuntimeError, match="Ollama returned reasoning but no final answer"):
                api_provider.chat_stream(
                    task=config.TASK_CHAT,
                    messages=[{"role": "user", "content": "hi"}],
                    on_chunk=lambda delta, reset: events.append((delta, reset)),
                )

        assert mock_chat.call_count == 3
        reset_events = [e for e in events if e[1] is True]
        assert reset_events == [("", True), ("", True)]


class _CancellableFakeStream:
    """Like _FakeStream, but flips `cancel_event` after yielding a fixed
    number of chunks - drives the REAL per-chunk cancel-check-and-close
    ordering inside chat_stream's own loop (`if cancel_event.is_set():
    stream.close()` then `_raise_if_cancelled(...)`), rather than a fake
    chat_stream that reimplements cancellation itself."""

    def __init__(self, chunks, cancel_event, cancel_after):
        self._chunks = list(chunks)
        self._cancel_event = cancel_event
        self._cancel_after = cancel_after
        self._yielded = 0
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        if self._yielded >= len(self._chunks):
            raise StopIteration
        part = self._chunks[self._yielded]
        self._yielded += 1
        if self._yielded == self._cancel_after:
            self._cancel_event.set()
        return part

    def close(self):
        self.closed = True


class TestCancelMidStream:
    """Spec section 6 item 3, driven against the REAL chat_stream (not a
    fake chat_stream) - proves the exact per-chunk ordering: stream.close()
    is called, no further chunks are consumed/forwarded after the cancel
    point, and RequestCancelledError propagates (not swallowed, not
    translated into a different exception)."""

    def test_cancel_mid_stream_closes_the_generator_and_raises_request_cancelled(self, monkeypatch):
        _set_ollama_model(monkeypatch)
        cancel_event = threading.Event()
        chunks = [
            {"message": {"content": "one "}, "done": False},
            {"message": {"content": "two "}, "done": False},  # cancel_event flips right after this
            {"message": {"content": "three "}, "done": False},
            {"message": {"content": "four"}, "done": True},
        ]
        fake_stream = _CancellableFakeStream(chunks, cancel_event, cancel_after=2)
        received = []

        with patch("api_provider.ollama.chat", return_value=fake_stream):
            with pytest.raises(api_provider.RequestCancelledError):
                api_provider.chat_stream(
                    task=config.TASK_CHAT,
                    messages=[{"role": "user", "content": "hi"}],
                    on_chunk=lambda delta, reset: received.append(delta),
                    cancellation_event=cancel_event,
                )

        assert fake_stream.closed, "cancel must close the live ollama generator"
        # The cancel_event flips INSIDE the generator's own __next__, before
        # control returns to chat_stream's loop body - so even the chunk
        # that coincides with the flip is caught by the cancel check before
        # its content is ever forwarded to on_chunk. Only "one " (yielded
        # strictly before the flip) gets through; "two "/"three "/"four"
        # never reach on_chunk - tight cancellation, nothing trickles past
        # the cancel point.
        assert received == ["one "]

    def test_cancel_checked_before_the_stream_is_first_touched(self, monkeypatch):
        # _raise_if_cancelled(cancel_event) at the top of chat_stream's
        # Ollama branch must fire even if the event was already set before
        # any chunk ever arrives - ollama.chat itself must never be called.
        _set_ollama_model(monkeypatch)
        cancel_event = threading.Event()
        cancel_event.set()

        with patch("api_provider.ollama.chat") as mock_chat:
            with pytest.raises(api_provider.RequestCancelledError):
                api_provider.chat_stream(
                    task=config.TASK_CHAT,
                    messages=[{"role": "user", "content": "hi"}],
                    on_chunk=lambda delta, reset: None,
                    cancellation_event=cancel_event,
                )

        mock_chat.assert_not_called()


class TestErrorTranslationMatchesChat:
    """Regression for an adversarial-review finding: chat_stream's live
    Ollama branch must translate raw connection/timeout/etc. exceptions into
    the exact same friendly, actionable messages chat() already does for its
    own Ollama branch - not let raw provider/network exception text
    propagate on what is now the ONLY code path the Composer send surface
    uses."""

    def test_connection_refused_gets_the_same_friendly_message_chat_would_raise(self, monkeypatch):
        _set_ollama_model(monkeypatch)

        with patch("api_provider.ollama.chat", side_effect=ConnectionError("Connection refused")):
            with pytest.raises(ConnectionError, match="Failed to connect to local Ollama server"):
                api_provider.chat_stream(
                    task=config.TASK_CHAT,
                    messages=[{"role": "user", "content": "hi"}],
                    on_chunk=lambda delta, reset: None,
                )

    def test_timeout_gets_the_same_friendly_message_chat_would_raise(self, monkeypatch):
        _set_ollama_model(monkeypatch)

        with patch("api_provider.ollama.chat", side_effect=TimeoutError("Read timed out")):
            with pytest.raises(TimeoutError, match="The model request timed out"):
                api_provider.chat_stream(
                    task=config.TASK_CHAT,
                    messages=[{"role": "user", "content": "hi"}],
                    on_chunk=lambda delta, reset: None,
                )

    def test_request_cancelled_error_passes_through_untranslated(self, monkeypatch):
        # The translation helper's own first check (isinstance RequestCancelledError)
        # must re-raise unchanged, never wrap it as some other exception type.
        _set_ollama_model(monkeypatch)

        with patch("api_provider.ollama.chat", side_effect=api_provider.RequestCancelledError("cancelled")):
            with pytest.raises(api_provider.RequestCancelledError):
                api_provider.chat_stream(
                    task=config.TASK_CHAT,
                    messages=[{"role": "user", "content": "hi"}],
                    on_chunk=lambda delta, reset: None,
                )
