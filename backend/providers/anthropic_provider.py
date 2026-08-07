"""ADR-006 stage 6.3: AnthropicProvider - faithful port of chat()'s
Anthropic branch, including its SDK-or-REST duality: initialize_api installs
either a real anthropic SDK client or a dict sentinel when the SDK isn't
installed, and this port preserves the exact same runtime dispatch
(messages.create when callable, _anthropic_post_json otherwise). Message
prep, media handling (_prepare_anthropic_messages already converts image
parts via _anthropic_content_block_from_part), reasoning gating on
TASK_CHAT, and text extraction all come from the same api_provider helpers
the branch used - behavior byte-identical. stream() is the transitional
single-"done" shape until 6.4.
"""

from __future__ import annotations

from typing import Iterator

import graphlink_task_config as config
from api_provider import (
    _anthropic_post_json,
    _extract_anthropic_text,
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
)

_ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"


class AnthropicProvider:
    def __init__(self, *, client, api_key: str, model: str, reasoning_level: str = "off"):
        self.client = client
        self.api_key = api_key
        self.model_id = model
        self.reasoning_level = reasoning_level
        self.capabilities = ProviderCapabilities(
            streaming=False,
            reasoning=anthropic_supports_reasoning(model),
            vision=True,   # _prepare_anthropic_messages converts image parts
            audio=False,   # Anthropic has no audio input; parts would be rejected upstream
            image_generation=False,  # generate_image's branch raises the explicit "not yet" error
        )

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
            request_kwargs["system"] = system_prompt

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
        return _extract_anthropic_text(response)

    def stream(self, request: ChatRequest, cancel: CancelToken) -> Iterator[ProviderEvent]:
        yield ProviderEvent("done", self.complete(request, cancel))
