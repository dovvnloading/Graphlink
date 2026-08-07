"""ADR-006 stage 6.3: LlamaCppProvider - faithful port of chat()'s llama.cpp
branch. Preserves exactly: the up-front media rejection
(_assert_llama_cpp_message_support - llama.cpp is text-only here, with the
actionable "use Ollama or Gemini" message), per-task message prep against
the FROZEN settings dict the caller snapshotted, the cached client lookup
(_get_llama_cpp_client - a heavyweight in-process model load, cached in
api_provider under its own lock), kwargs filtered to what
create_chat_completion actually accepts, and text extraction. stream() is
the transitional single-"done" shape; local token streaming lands in 6.4
via the threaded-generator bridge the ADR names for this provider.
"""

from __future__ import annotations

from typing import Iterator

from api_provider import (
    _assert_llama_cpp_message_support,
    _extract_llama_cpp_text,
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
            streaming=False,
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
        yield ProviderEvent("done", self.complete(request, cancel))
