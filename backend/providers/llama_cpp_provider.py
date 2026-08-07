"""ADR-006 stage 6.3: LlamaCppProvider - faithful port of chat()'s llama.cpp
branch. Preserves exactly: the up-front media rejection
(_assert_llama_cpp_message_support - llama.cpp is text-only here, with the
actionable "use Ollama or Gemini" message), per-task message prep against
the FROZEN settings dict the caller snapshotted, the cached client lookup
(_get_llama_cpp_client - a heavyweight in-process model load, cached in
api_provider under its own lock), kwargs filtered to what
create_chat_completion actually accepts, and text extraction.

stream() (ADR-006 stage 6.5b) is real local token streaming:
create_chat_completion(stream=True) returns a sync generator of OpenAI-
chunk-shaped dicts, consumed with cooperative-only cancellation (there is
no HTTP stream to tear down - inference runs in-process) and composed at
"done" through the same _extract_llama_cpp_text the blocking path uses,
so streamed and blocking runs produce identical final text.
"""

from __future__ import annotations

from typing import Iterator

from api_provider import (
    _assert_llama_cpp_message_support,
    _extract_llama_cpp_text,
    _extract_response_field,
    _filter_kwargs_for_callable,
    _get_llama_cpp_client,
    _prepare_llama_cpp_kwargs,
    _prepare_llama_cpp_messages,
    _raise_if_cancelled,
    llama_cpp_supports_reasoning,
)
from backend.providers.base import (
    CancelToken,
    ChatRequest,
    ProviderCapabilities,
    ProviderEvent,
)


class LlamaCppProvider:
    def __init__(self, *, settings: dict):
        # The whole settings dict (paths, n_ctx, chat_format, reasoning
        # level) is this provider's configuration - captured from the
        # caller's snapshot, same immutability contract as every provider
        # in this package.
        self.settings = settings
        self.capabilities = ProviderCapabilities(
            streaming=True,  # 6.5b: real local token streaming via create_chat_completion(stream=True)
            reasoning=llama_cpp_supports_reasoning(str(settings.get("chat_model_path", ""))),
            vision=False,  # _assert_llama_cpp_message_support rejects media up front
            audio=False,
            image_generation=False,
        )

    def complete(self, request: ChatRequest, cancel: CancelToken) -> str:
        _assert_llama_cpp_message_support(request.messages)
        llama_messages = _prepare_llama_cpp_messages(request.messages, request.task, self.settings)
        client = _get_llama_cpp_client(request.task, self.settings)
        kwargs = {k: v for k, v in request.extra_kwargs.items() if k != "cancellation_event"}
        llama_kwargs = _filter_kwargs_for_callable(
            client.create_chat_completion,
            _prepare_llama_cpp_kwargs(kwargs, self.settings),
        )
        response = client.create_chat_completion(messages=llama_messages, **llama_kwargs)
        _raise_if_cancelled(cancel.event)
        return _extract_llama_cpp_text(response)

    def stream(self, request: ChatRequest, cancel: CancelToken) -> Iterator[ProviderEvent]:
        # ADR-006 stage 6.5b: same prep pipeline as complete(), streamed.
        _raise_if_cancelled(cancel.event)
        _assert_llama_cpp_message_support(request.messages)
        llama_messages = _prepare_llama_cpp_messages(request.messages, request.task, self.settings)
        client = _get_llama_cpp_client(request.task, self.settings)
        kwargs = {k: v for k, v in request.extra_kwargs.items() if k != "cancellation_event"}
        llama_kwargs = _filter_kwargs_for_callable(
            client.create_chat_completion,
            _prepare_llama_cpp_kwargs(kwargs, self.settings),
        )
        # stream=True is passed OUTSIDE the filtered kwargs, explicitly:
        # _filter_kwargs_for_callable keeps only names the callable's own
        # signature declares, so routing it through the filter would let a
        # wrapped/faked create_chat_completion without a spelled-out `stream`
        # parameter silently degrade this into a blocking call.
        llama_kwargs.pop("stream", None)
        chunks = client.create_chat_completion(
            messages=llama_messages, stream=True, **llama_kwargs
        )

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        try:
            for chunk in chunks:
                # Cooperative-only cancellation: no live HTTP stream to close
                # (inference runs in-process); the finally below still closes
                # the generator so the shared cached client is never left
                # with a partially-consumed completion.
                _raise_if_cancelled(cancel.event)

                choices = _extract_response_field(chunk, "choices", []) or []
                if not choices:
                    continue
                delta = _extract_response_field(choices[0], "delta", {}) or {}
                delta_content = _extract_response_field(delta, "content") or ""
                if delta_content:
                    content_parts.append(delta_content)
                    yield ProviderEvent("text", delta_content)
                # The same wide net _extract_llama_cpp_text casts for
                # thinking output, at the delta level.
                delta_reasoning = (
                    _extract_response_field(delta, "reasoning")
                    or _extract_response_field(delta, "reasoning_content")
                    or _extract_response_field(delta, "thinking")
                    or ""
                )
                if delta_reasoning:
                    reasoning_parts.append(delta_reasoning)
                    yield ProviderEvent("reasoning", delta_reasoning)
        finally:
            close = getattr(chunks, "close", None)
            if callable(close):
                close()

        # Streamed and blocking paths compose through the SAME extraction:
        # a synthetic blocking-shaped response through _extract_llama_cpp_text
        # gives identical <think> composition, reasoning-without-answer
        # detection, and empty-response error as complete() for the same data.
        yield ProviderEvent("done", _extract_llama_cpp_text({
            "choices": [{"message": {
                "content": "".join(content_parts),
                "reasoning": "".join(reasoning_parts),
            }}],
        }))
