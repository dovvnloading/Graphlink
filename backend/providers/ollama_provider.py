"""ADR-006 stage 6.1: OllamaProvider - the first real port onto the Provider
protocol.

This is a FAITHFUL PORT of api_provider.py's twin Ollama branches (chat()'s
and chat_stream()'s), deduplicating the two ~70-line blocks into one class
while preserving every documented invariant of that code:

- message prep flattens images AND audio bytes into ollama's `images` field
  (_prepare_ollama_messages - Gemma 4 audio rides the multimodal field);
- the audio capability gate runs before any network call;
- the `think` kwarg and the reasoning-budget system hint apply ONLY when
  task == TASK_CHAT, with the per-family string-vs-bool mapping
  (ollama_think_kwarg) unchanged;
- the 3-attempt ReasoningWithoutAnswerError retry loop, with the same backoff
  constant, cancel checks on BOTH sides of the sleep, and (streaming only) a
  "reset" event before a discarded attempt's replacement deltas start;
- `thinking` deltas are a separate event channel, never mixed into "text";
- the final text is composed as "<think>...</think>\\n{answer}" via
  _compose_reasoned_response - backend/response_parsing.py depends on that
  exact shape - with the native `thinking` field and embedded <think> text
  deduplicated (_append_unique_text_segment);
- mid-stream cancellation closes the live HTTP stream (stream.close()
  unwinds the ollama client's context manager) then raises
  RequestCancelledError, which must escape untranslated;
- "reasoning but no answer after 3 attempts" and "genuinely empty response"
  remain DISTINCT failures with their exact existing messages.

The helpers are imported from api_provider rather than duplicated: they are
provider-agnostic text utilities plus Ollama-specific prep that api_provider
still needs for capability probing; they migrate into this package when the
last consumer outside it does (stage 6.3+). Exception TRANSLATION stays in
api_provider._translate_chat_exception at the call sites - this class raises
raw errors, per the protocol's documented contract.

`complete()` is a transitional extra beyond the protocol: chat()'s Ollama
branch is non-streaming today (one blocking ollama.chat call), and switching
it to streamed consumption would change network behavior in a stage whose
job is the seam, not the semantics. Stage 6.4 (universal streaming) deletes
it.
"""

from __future__ import annotations

import time
from typing import Iterator

import ollama

import graphlink_task_config as config
from api_provider import (
    _OLLAMA_REASONING_RETRY_BACKOFF_SECONDS,
    ReasoningWithoutAnswerError,
    _append_system_hint,
    _append_unique_text_segment,
    _assert_ollama_audio_support,
    _compose_reasoned_response,
    _is_ollama_bool_reasoning_model,
    _prepare_ollama_messages,
    _raise_if_cancelled,
    ollama_supports_embedding,
    ollama_supports_tools,
    ollama_think_kwarg,
    reasoning_budget_hint,
    split_reasoning_and_content,
)
from backend.providers.base import (
    CancelToken,
    ChatRequest,
    ProviderCapabilities,
    ProviderEvent,
    ToolCall,
    normalize_usage,
)

_MAX_REASONING_ATTEMPTS = 3


class OllamaProvider:
    """One configured (model, reasoning_level) pair. Stateless beyond its
    construction arguments - the caller captures both from its own provider
    snapshot, so a mid-request settings change can't half-apply here any more
    than it could in the branch this ports."""

    def __init__(self, *, model: str, reasoning_level: str = "off", context_window: int | None = None):
        self.model_id = model
        self.reasoning_level = reasoning_level
        # ADR-006 stage 6.8 review fix (HIGH): the context window to ask the
        # daemon to SERVE (options.num_ctx) - the SAME number the caller
        # budgets trim_history with (api_provider._ollama_effective_context_
        # window), so the daemon can never silently truncate front-first
        # below our budget. None (older direct constructions) omits the
        # option entirely, preserving pre-fix request shapes.
        self.context_window = context_window
        # ADR-006 stage 6.8: usage from the last complete() call (see the
        # comment there); None until a blocking call reports counts.
        self.last_usage: dict | None = None
        # Derived WITHOUT a network round-trip: reasoning support here means
        # "this family takes a think kwarg" (a pure string-family check, the
        # same one the request path applies). vision/audio are True because
        # the REQUEST PATH genuinely sends both (_prepare_ollama_messages
        # flattens image and audio bytes into ollama's images field) - the
        # per-MODEL audio gate (_assert_ollama_audio_support's probed
        # capability cache) still enforces at request time, exactly as
        # before; a False here would wrongly tell a future consumer to
        # disable attachments for vision-capable Ollama models (6.3
        # adversarial review finding on the first draft's under-claim).
        # ADR-007 stage 7.1: unlike vision/audio (True unconditionally - see
        # the comment above), tools IS a genuine per-model probe - see
        # ollama_supports_tools' own docstring for why the two cases differ.
        self.capabilities = ProviderCapabilities(
            streaming=True,
            reasoning=ollama_think_kwarg(model, "high") is not None,
            vision=True,
            audio=True,
            tools=ollama_supports_tools(model),
            # ADR-007 stage 7.3: unlike tools (a genuine per-model probe),
            # Ollama's `format` accepting a raw JSON Schema dict is a
            # request-shape feature of the client/server protocol itself,
            # not a per-model chat-template capability - True
            # unconditionally, matching vision/audio's own reasoning above.
            structured_output=True,
            # ADR-017 stage 17.3: a genuine per-model probe, like tools -
            # see ollama_supports_embedding's own docstring for why a CHAT
            # model must not claim this just because SOME Ollama model
            # supports it.
            embedding=ollama_supports_embedding(model),
        )

    # -- shared request prep --------------------------------------------------

    def _prepared(self, request: ChatRequest) -> tuple[list, dict]:
        _assert_ollama_audio_support(self.model_id, request.messages)
        messages = _prepare_ollama_messages(request.messages)
        kwargs = {k: v for k, v in request.extra_kwargs.items() if k != "cancellation_event"}
        if request.tools:
            # ADR-007 stage 7.1: Ollama's native shape mirrors OpenAI's -
            # {"type":"function","function":{"name","description",
            # "parameters"}} - `parameters` is the ToolSpec's own JSON
            # Schema object, passed through untouched (Ollama does not
            # narrow it to an OpenAPI subset the way Gemini's REST API
            # does).
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": spec.input_schema,
                    },
                }
                for spec in request.tools
            ]
        if self.context_window:
            # Served context == budgeted context (see __init__). A caller's
            # own explicit options.num_ctx passthrough still wins.
            options = dict(kwargs.get("options") or {})
            options.setdefault("num_ctx", self.context_window)
            kwargs["options"] = options
        if request.task == config.TASK_CHAT:
            think_value = ollama_think_kwarg(self.model_id, self.reasoning_level)
            if think_value is not None:
                kwargs["think"] = think_value
            if _is_ollama_bool_reasoning_model(self.model_id) and self.reasoning_level != "off":
                messages = _append_system_hint(
                    messages, reasoning_budget_hint(self.reasoning_level)
                )
        return messages, kwargs

    @staticmethod
    def _compose(raw_content: str, thinking: str) -> str:
        """The shared tail of both branches: split embedded reasoning out of
        the raw content, merge it with the native thinking channel without
        duplication, and re-wrap. Raises ReasoningWithoutAnswerError for the
        retryable reasoning-only case, RuntimeError for genuinely empty."""
        embedded_reasoning, visible_content = split_reasoning_and_content(raw_content)
        reasoning_parts: list[str] = []
        reasoning_seen: set[str] = set()
        _append_unique_text_segment(reasoning_parts, thinking, reasoning_seen)
        _append_unique_text_segment(reasoning_parts, embedded_reasoning, reasoning_seen)
        return _compose_reasoned_response(
            visible_content, "\n\n".join(reasoning_parts).strip(), "Ollama"
        )

    @staticmethod
    def _exhausted_retries_error() -> RuntimeError:
        return RuntimeError(
            f"Ollama returned reasoning but no final answer after {_MAX_REASONING_ATTEMPTS} attempts. "
            "Retry in Quick mode or choose a different chat format/model."
        )

    @staticmethod
    def _extract_tool_calls(message) -> tuple[ToolCall, ...]:
        """ADR-007 stage 7.1: Ollama delivers tool calls whole - never
        incrementally, so there is nothing to accumulate across chunks, in
        contrast to OpenAI/Anthropic's per-delta buffers. Ollama's own
        ToolCall has NO id field (see api_provider.ollama_supports_tools'
        own docstring), so a stable per-turn id is synthesized from the
        call's position - stable because Ollama's tool_calls list, once
        delivered, is not re-ordered or re-delivered within the same turn."""
        raw_calls = message.get("tool_calls") or []
        return tuple(
            ToolCall(
                id=f"call_{index}",
                name=raw["function"]["name"],
                arguments=dict(raw["function"]["arguments"]),
            )
            for index, raw in enumerate(raw_calls)
        )

    # -- the protocol ---------------------------------------------------------

    def stream(self, request: ChatRequest, cancel: CancelToken) -> Iterator[ProviderEvent]:
        _raise_if_cancelled(cancel.event)
        messages, kwargs = self._prepared(request)

        last_reasoning_error: ReasoningWithoutAnswerError | None = None
        for attempt in range(_MAX_REASONING_ATTEMPTS):
            if attempt > 0:
                _raise_if_cancelled(cancel.event)
                time.sleep(_OLLAMA_REASONING_RETRY_BACKOFF_SECONDS)
                _raise_if_cancelled(cancel.event)
                yield ProviderEvent("reset")

            content_parts: list[str] = []
            thinking_parts: list[str] = []
            tool_calls: tuple[ToolCall, ...] = ()
            usage = None
            stream = ollama.chat(model=self.model_id, messages=messages, stream=True, **kwargs)
            try:
                for part in stream:
                    if cancel.is_set():
                        stream.close()  # unwinds ollama/_client.py's `with self._client.stream(...)`
                    _raise_if_cancelled(cancel.event)  # raises if just closed above

                    delta_content = part["message"].get("content") or ""
                    if delta_content:
                        content_parts.append(delta_content)
                        yield ProviderEvent("text", delta_content)
                    delta_thinking = part["message"].get("thinking") or ""
                    if delta_thinking:
                        thinking_parts.append(delta_thinking)
                        yield ProviderEvent("reasoning", delta_thinking)
                    # ADR-007 stage 7.1: a tool-calling chunk is ALSO the
                    # terminal chunk for this attempt (Ollama never streams
                    # more content after tool_calls appear), so this check
                    # sits ahead of the `done` check below rather than
                    # inside it - `done` may not even be set the same chunk.
                    if part["message"].get("tool_calls"):
                        tool_calls = self._extract_tool_calls(part["message"])
                        break
                    if part.get("done"):
                        # ADR-006 stage 6.8: the terminal chunk carries
                        # Ollama's real token counts.
                        usage = normalize_usage(
                            part.get("prompt_eval_count"), part.get("eval_count")
                        )
                        break
            finally:
                stream.close()  # idempotent on an already-exhausted generator

            if tool_calls:
                # ADR-007 stage 7.1: a pure tool-call turn is a legitimate,
                # complete outcome - it must NOT enter the reasoning-retry
                # loop below (that loop exists for "thinking but no visible
                # ANSWER", not for "the model chose to call a tool instead
                # of answering yet"). Any lead-in text before the call still
                # streamed above as ordinary "text" events.
                for call in tool_calls:
                    yield ProviderEvent("tool_call", tool_call=call)
                yield ProviderEvent("done", "".join(content_parts))
                return

            try:
                final = self._compose("".join(content_parts), "".join(thinking_parts))
            except ReasoningWithoutAnswerError as exc:
                last_reasoning_error = exc
                continue
            yield ProviderEvent("done", final, usage=usage)
            return
        raise self._exhausted_retries_error() from last_reasoning_error

    # -- transitional non-streaming path (dies in stage 6.4) ------------------
    #
    # ADR-007 stage 7.1: deliberately NOT given tool-call support. complete()
    # returns a bare str with no channel for ToolCall events to ride, and its
    # only remaining callers (api_provider.chat()'s Ollama branch) never
    # supply request.tools - the tool-use loop this stage introduces is
    # built exclusively on stream()/chat_stream(), matching the ADR's own
    # framing of tool calls as a streamed concept. If a future caller ever
    # passes tools here, they are silently ignored (no `tools` kwarg is
    # built below) rather than raising - consistent with `complete()`'s
    # existing transitional-scope posture elsewhere in this class.

    def complete(self, request: ChatRequest, cancel: CancelToken) -> str:
        _raise_if_cancelled(cancel.event)
        messages, kwargs = self._prepared(request)

        last_reasoning_error: ReasoningWithoutAnswerError | None = None
        for attempt in range(_MAX_REASONING_ATTEMPTS):
            if attempt > 0:
                _raise_if_cancelled(cancel.event)
                time.sleep(_OLLAMA_REASONING_RETRY_BACKOFF_SECONDS)
                _raise_if_cancelled(cancel.event)

            response = ollama.chat(model=self.model_id, messages=messages, **kwargs)
            _raise_if_cancelled(cancel.event)
            # ADR-006 stage 6.8: complete() returns a bare str by protocol, so
            # the usage from the raw response rides a side attribute the
            # blocking chat() branch reads right after this call returns.
            self.last_usage = normalize_usage(
                response.get("prompt_eval_count"), response.get("eval_count")
            )

            try:
                return self._compose(
                    response["message"].get("content", ""),
                    response["message"].get("thinking") or "",
                )
            except ReasoningWithoutAnswerError as exc:
                last_reasoning_error = exc
                continue
        raise self._exhausted_retries_error() from last_reasoning_error

    # -- embeddings (ADR-017 stage 17.3) ---------------------------------------

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Batch embedding via Ollama's own `/api/embed` (ollama.embed()) -
        one call for the whole batch rather than one per text, matching
        the endpoint's own designed usage (it accepts a list `input`
        natively). Ollama returns vectors in the SAME order as `input` (its
        own documented contract) - trusted here rather than re-verified,
        the same caller-trusts-server posture every other provider method
        in this class already takes. Returns `[]` for an empty `texts`
        WITHOUT a network call - an empty batch is a valid no-op, not
        worth a round trip."""
        if not texts:
            return []
        response = ollama.embed(model=self.model_id, input=list(texts))
        return [list(vector) for vector in response["embeddings"]]
