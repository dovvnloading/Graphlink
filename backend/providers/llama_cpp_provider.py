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
    llama_cpp_inference_lock,
    llama_cpp_supports_reasoning,
)
from backend.providers.base import (
    CancelToken,
    ChatRequest,
    ProviderCapabilities,
    ProviderEvent,
    normalize_usage,
)


class LlamaCppProvider:
    def __init__(self, *, settings: dict):
        # The whole settings dict (paths, n_ctx, chat_format, reasoning
        # level) is this provider's configuration - captured from the
        # caller's snapshot, same immutability contract as every provider
        # in this package.
        self.settings = settings
        self.last_usage: dict | None = None  # ADR-006 stage 6.8 - see complete()
        self.capabilities = ProviderCapabilities(
            streaming=True,  # 6.5b: real local token streaming via create_chat_completion(stream=True)
            reasoning=llama_cpp_supports_reasoning(str(settings.get("chat_model_path", ""))),
            vision=False,  # _assert_llama_cpp_message_support rejects media up front
            audio=False,
            image_generation=False,
            # ADR-007 stage 7.3: create_chat_completion's response_format=
            # {"type":"json_object","schema":...} compiles a GBNF grammar
            # server-side (verified against the installed llama_cpp
            # package's own ChatCompletionRequestResponseFormat shape) -
            # unlike tools (deliberately out of ADR-007 stage 7.1's scope
            # for this provider), structured output needs no per-model
            # capability probe.
            structured_output=True,
            # ADR-017 stage 17.3: out of this stage's scope for llama.cpp
            # (ADR-017 doc's own stage-17.3 row names Ollama + API
            # providers, matching ADR-007 stage 7.1's own precedent for
            # leaving llama.cpp out of a new capability's first pass).
            embedding=False,
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
        # Serialized against every other generation on this same cached Llama
        # instance - it is one native context with mutable KV state, and two
        # concurrent decodes corrupt output or crash the process. See
        # api_provider.llama_cpp_inference_lock.
        with llama_cpp_inference_lock(client):
            response = client.create_chat_completion(messages=llama_messages, **llama_kwargs)
        _raise_if_cancelled(cancel.event)
        # ADR-006 stage 6.8: llama.cpp's blocking response carries an
        # OpenAI-shaped usage dict; captured on a side attribute for
        # api_provider.chat()'s llama.cpp branch to surface.
        response_usage = _extract_response_field(response, "usage", None) or {}
        self.last_usage = normalize_usage(
            _extract_response_field(response_usage, "prompt_tokens", None),
            _extract_response_field(response_usage, "completion_tokens", None),
        )
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

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        # Held across the ENTIRE stream, not just the call that opens it: a
        # llama.cpp generation advances its one native context incrementally
        # as chunks are pulled, so releasing after the opening call would let
        # a second decode interleave mid-reply on the same KV cache. Released
        # in the outer finally below - which is precisely why this must be a
        # plain Lock: if a caller abandons this generator, that finally runs
        # during generator finalization, possibly on another thread, and an
        # RLock would refuse the release. See api_provider's own
        # llama_cpp_inference_lock.
        inference_lock = llama_cpp_inference_lock(client)
        inference_lock.acquire()
        try:
            chunks = client.create_chat_completion(
                messages=llama_messages, stream=True, **llama_kwargs
            )
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
        finally:
            inference_lock.release()

        # Streamed and blocking paths compose through the SAME extraction:
        # a synthetic blocking-shaped response through _extract_llama_cpp_text
        # gives identical <think> composition, reasoning-without-answer
        # detection, and empty-response error as complete() for the same data.
        # ADR-006 stage 6.8: llama.cpp stream chunks do not reliably carry
        # usage (llama-cpp-python emits no usage on delta chunks) - the done
        # event's usage stays None deliberately; the token counter falls back
        # to its estimator for this provider's streamed replies.
        yield ProviderEvent("done", _extract_llama_cpp_text({
            "choices": [{"message": {
                "content": "".join(content_parts),
                "reasoning": "".join(reasoning_parts),
            }}],
        }))
