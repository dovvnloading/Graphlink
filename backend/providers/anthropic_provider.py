"""ADR-006 stage 6.3: AnthropicProvider - faithful port of chat()'s
Anthropic branch, including its SDK-or-REST duality: initialize_api installs
either a real anthropic SDK client or a dict sentinel when the SDK isn't
installed, and this port preserves the exact same runtime dispatch
(messages.create when callable, _anthropic_post_json otherwise). Message
prep, media handling (_prepare_anthropic_messages already converts image
parts via _anthropic_content_block_from_part), reasoning gating on
TASK_CHAT, and text extraction all come from the same api_provider helpers
the branch used - behavior byte-identical.

stream() (ADR-006 stage 6.5b) is real streaming over BOTH transports:
messages.create(stream=True) on the SDK path - create's raw event shape IS
the REST SSE wire shape, unlike the messages.stream() helper's typed
context manager - and _anthropic_stream_sse (the streaming sibling of
_anthropic_post_json) on the dict-sentinel REST fallback, so one
translation loop serves both. text_delta -> "text", thinking_delta ->
"reasoning", and the final "done" composes via _compose_reasoned_response
exactly as _extract_anthropic_text does for the blocking path.
"""

from __future__ import annotations

import json
from typing import Iterator

import graphlink_task_config as config
from api_provider import (
    _anthropic_post_json,
    _anthropic_stream_sse,
    _compose_reasoned_response,
    _extract_anthropic_text,
    _extract_response_field,
    _filter_kwargs_for_callable,
    _prepare_anthropic_kwargs,
    _prepare_anthropic_messages,
    _raise_if_cancelled,
    anthropic_supports_reasoning,
)
from backend.providers.base import (
    CancelToken,
    ChatRequest,
    ProviderCapabilities,
    ProviderEvent,
    ToolCall,
    normalize_usage,
)

_ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"


def _system_blocks_with_cache_control(system_prompt: str) -> list[dict]:
    """ADR-006 stage 6.7: send the system prompt as a content-block list
    with cache_control instead of a bare string, so Anthropic caches the
    (stable, per-conversation-identical) system prompt across turns.
    Both transports pass this through unchanged: `system` is a declared
    param on the SDK's messages.create (survives _filter_kwargs_for_
    callable), and the REST fallback serializes request kwargs wholesale."""
    return [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]


class AnthropicProvider:
    def __init__(self, *, client, api_key: str, model: str, reasoning_level: str = "off"):
        self.client = client
        self.api_key = api_key
        self.model_id = model
        self.reasoning_level = reasoning_level
        self.last_usage: dict | None = None  # ADR-006 stage 6.8 - see complete()
        self.capabilities = ProviderCapabilities(
            streaming=True,  # 6.5b: real streaming on both the SDK and REST transports
            reasoning=anthropic_supports_reasoning(model),
            vision=True,   # _prepare_anthropic_messages converts image parts
            audio=False,   # Anthropic has no audio input API; False is what
                           # keeps a future capability consumer from offering it
            image_generation=False,  # generate_image's branch raises the explicit "not yet" error
            # ADR-007 stage 7.1: current Claude model families all support
            # native tool use unconditionally (base.py's own
            # ProviderCapabilities.tools comment) - no capability call needed.
            tools=True,
        )

    # ADR-007 stage 7.1: deliberately NOT given tool-call support, matching
    # every other provider's complete() - it returns a bare str with no
    # channel for ToolCall events, and its callers never supply
    # request.tools. A future caller passing tools here has them silently
    # ignored (no `tools` kwarg built below).

    def complete(self, request: ChatRequest, cancel: CancelToken) -> str:
        system_prompt, anthropic_messages = _prepare_anthropic_messages(
            request.messages, cancel_event=cancel.event
        )
        reasoning_level = self.reasoning_level if request.task == config.TASK_CHAT else "off"
        kwargs = {k: v for k, v in request.extra_kwargs.items() if k != "cancellation_event"}
        request_kwargs = {
            "model": self.model_id,
            "messages": anthropic_messages,
            **_prepare_anthropic_kwargs(request.task, kwargs, self.model_id, reasoning_level),
        }
        if system_prompt:
            request_kwargs["system"] = _system_blocks_with_cache_control(system_prompt)

        create_callable = getattr(getattr(self.client, "messages", None), "create", None)
        if callable(create_callable):
            filtered_kwargs = _filter_kwargs_for_callable(create_callable, request_kwargs)
            response = create_callable(**filtered_kwargs)
        else:
            response = _anthropic_post_json(
                _ANTHROPIC_MESSAGES_URL,
                request_kwargs,
                timeout=180,
                cancel_event=cancel.event,
                api_key=self.api_key,
            )
        _raise_if_cancelled(cancel.event)
        # ADR-006 stage 6.8: blocking usage captured on a side attribute
        # (complete() returns a bare str by protocol); api_provider.chat()
        # deliberately does not surface API-mode blocking usage today (the
        # chat UI streams everywhere since 6.5b).
        response_usage = _extract_response_field(response, "usage", None)
        if response_usage is not None:
            self.last_usage = normalize_usage(
                _extract_response_field(response_usage, "input_tokens", None),
                _extract_response_field(response_usage, "output_tokens", None),
            )
        return _extract_anthropic_text(response)

    def stream(self, request: ChatRequest, cancel: CancelToken) -> Iterator[ProviderEvent]:
        # ADR-006 stage 6.5b: same request prep as complete(), then one event
        # loop over whichever transport the client duck-types to. Events are
        # read through _extract_response_field because the SDK path yields
        # typed objects while the REST path yields dicts - same wire shape,
        # different containers.
        _raise_if_cancelled(cancel.event)
        system_prompt, anthropic_messages = _prepare_anthropic_messages(
            request.messages, cancel_event=cancel.event
        )
        reasoning_level = self.reasoning_level if request.task == config.TASK_CHAT else "off"
        kwargs = {k: v for k, v in request.extra_kwargs.items() if k != "cancellation_event"}
        request_kwargs = {
            "model": self.model_id,
            "messages": anthropic_messages,
            **_prepare_anthropic_kwargs(request.task, kwargs, self.model_id, reasoning_level),
        }
        if system_prompt:
            request_kwargs["system"] = _system_blocks_with_cache_control(system_prompt)
        if request.tools:
            # ADR-007 stage 7.1: Anthropic's native tool shape - {"name",
            # "description", "input_schema"} - is the ToolSpec's own three
            # fields verbatim, no wrapper object unlike OpenAI/Ollama's
            # {"type":"function","function":{...}}.
            request_kwargs["tools"] = [
                {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
                for tool in request.tools
            ]

        create_callable = getattr(getattr(self.client, "messages", None), "create", None)
        if callable(create_callable):
            # `stream` is a declared parameter on the real SDK's
            # messages.create, but it is passed OUTSIDE the filtered kwargs
            # anyway so a wrapped/faked create with a narrower signature can
            # never silently drop it and degrade to a blocking call.
            filtered_kwargs = _filter_kwargs_for_callable(create_callable, request_kwargs)
            filtered_kwargs.pop("stream", None)
            event_stream = create_callable(stream=True, **filtered_kwargs)
        else:
            event_stream = _anthropic_stream_sse(
                _ANTHROPIC_MESSAGES_URL,
                request_kwargs,
                timeout=180,
                cancel_event=cancel.event,
                api_key=self.api_key,
            )

        answer_parts: list[str] = []
        thinking_parts: list[str] = []
        # ADR-007 stage 7.1: Anthropic streams a tool call as
        # content_block_start (type "tool_use", carrying id/name) followed
        # by zero or more content_block_delta "input_json_delta" events
        # (partial_json fragments) - buffered per block INDEX, same shape
        # as OpenAI's per-index buffering, then parsed once complete.
        tool_call_buffers: dict = {}
        saw_message_stop = False
        # ADR-006 stage 6.8: input tokens arrive on message_start
        # (message.usage.input_tokens), output tokens on message_delta
        # (usage.output_tokens, cumulative - last one wins). Read through
        # _extract_response_field like every other event field, so both the
        # SDK's typed objects and the REST dicts work.
        prompt_tokens = None
        completion_tokens = None
        try:
            for event in event_stream:
                if cancel.is_set():
                    event_stream.close()  # SDK Stream and generator alike expose close()
                _raise_if_cancelled(cancel.event)  # raises if just closed above

                event_type = str(_extract_response_field(event, "type", "")).strip().lower()
                if event_type == "error":
                    # 6.5b review (HIGH): the streaming API can send an error
                    # event on a 200 stream (overloaded_error, ...) and then
                    # close - dropping it would compose a truncated answer as
                    # if complete. Raise with the API's own type+message, the
                    # same posture _anthropic_post_json takes for error
                    # payloads, so translation treats both paths identically.
                    error_info = _extract_response_field(event, "error", {})
                    error_type = str(_extract_response_field(error_info, "type", "") or "").strip()
                    error_message = str(
                        _extract_response_field(error_info, "message", "") or ""
                    ).strip() or "Anthropic returned an error mid-stream."
                    raise RuntimeError(
                        f"{error_type}: {error_message}" if error_type else error_message
                    )
                if event_type == "content_block_delta":
                    delta = _extract_response_field(event, "delta", {})
                    delta_type = str(_extract_response_field(delta, "type", "")).strip().lower()
                    if delta_type == "text_delta":
                        text = str(_extract_response_field(delta, "text", "") or "")
                        if text:
                            answer_parts.append(text)
                            yield ProviderEvent("text", text)
                    elif delta_type == "thinking_delta":
                        thinking = str(_extract_response_field(delta, "thinking", "") or "")
                        if thinking:
                            thinking_parts.append(thinking)
                            yield ProviderEvent("reasoning", thinking)
                    elif delta_type == "input_json_delta":
                        index = _extract_response_field(event, "index", None)
                        buf = tool_call_buffers.get(index)
                        if buf is not None:
                            buf["json"] += str(_extract_response_field(delta, "partial_json", "") or "")
                elif event_type == "content_block_start":
                    content_block = _extract_response_field(event, "content_block", None)
                    block_type = str(_extract_response_field(content_block, "type", "") or "").strip().lower()
                    if block_type == "tool_use":
                        tool_call_buffers[_extract_response_field(event, "index", None)] = {
                            "id": str(_extract_response_field(content_block, "id", "") or ""),
                            "name": str(_extract_response_field(content_block, "name", "") or ""),
                            "json": "",
                        }
                elif event_type == "message_start":
                    message = _extract_response_field(event, "message", {})
                    start_usage = _extract_response_field(message, "usage", None)
                    if start_usage is not None:
                        prompt_tokens = _extract_response_field(start_usage, "input_tokens", None)
                elif event_type == "message_delta":
                    delta_usage = _extract_response_field(event, "usage", None)
                    if delta_usage is not None:
                        completion_tokens = _extract_response_field(delta_usage, "output_tokens", None)
                elif event_type == "message_stop":
                    saw_message_stop = True
                    break
        finally:
            event_stream.close()  # idempotent on an already-closed/exhausted stream

        # 6.5b review: a successful Anthropic stream ALWAYS ends with
        # message_stop, on both transports. An iterator that just stops
        # without one (proxy truncation, silent connection close) delivered
        # a fragment - composing it would present a truncated answer as
        # complete.
        if not saw_message_stop:
            raise RuntimeError(
                "Anthropic stream ended unexpectedly before completion. Please try again."
            )

        if tool_call_buffers:
            # ADR-007 stage 7.1: a pure tool-call turn is a legitimate,
            # complete outcome - it must NOT go through
            # _compose_reasoned_response below, which raises for "thinking
            # but no visible answer" (the exact, legitimate shape of a
            # tool-call turn whose model reasoned before calling instead of
            # answering), mirroring OllamaProvider.stream()'s own
            # short-circuit ahead of its equivalent _compose() call.
            for index in sorted(tool_call_buffers, key=str):
                buf = tool_call_buffers[index]
                yield ProviderEvent(
                    "tool_call",
                    tool_call=ToolCall(
                        id=buf["id"],
                        name=buf["name"],
                        arguments=json.loads(buf["json"]) if buf["json"] else {},
                    ),
                )
            yield ProviderEvent("done", "".join(answer_parts).strip())
            return

        # The same composition contract as _extract_anthropic_text gives the
        # blocking path: answer + thinking through _compose_reasoned_response,
        # so streamed and blocking runs produce identical final text (and the
        # same reasoning-without-answer / empty-response failures).
        yield ProviderEvent(
            "done",
            _compose_reasoned_response(
                "".join(answer_parts).strip(),
                "".join(thinking_parts).strip(),
                "Anthropic Claude",
            ),
            usage=normalize_usage(prompt_tokens, completion_tokens),
        )
