"""ADR-006 stage 6.3: GeminiProvider - faithful port of chat()'s Gemini
branch (raw REST :generateContent, no SDK). Preserves exactly: content prep
with media upload via _prepare_gemini_contents (which returns the uploaded
file names), thinkingConfig gated on TASK_CHAT, the message-size-derived
timeout, and - load-bearing - the finally-block cleanup that deletes every
uploaded file even when the generation call fails. stream() is the
transitional single-"done" shape until 6.4.
"""

from __future__ import annotations

from typing import Iterator

import graphlink_task_config as config
from api_provider import (
    GEMINI_BASE_URL,
    _calculate_gemini_timeout,
    _extract_gemini_text,
    _gemini_delete_file,
    _gemini_post_json,
    _prepare_gemini_contents,
    gemini_supports_reasoning,
    gemini_thinking_config,
)
from backend.providers.base import (
    CancelToken,
    ChatRequest,
    ProviderCapabilities,
    ProviderEvent,
)


class GeminiProvider:
    def __init__(self, *, api_key: str, model: str, reasoning_level: str = "off"):
        self.api_key = api_key
        self.model_id = model
        self.reasoning_level = reasoning_level
        self.capabilities = ProviderCapabilities(
            streaming=False,
            reasoning=gemini_supports_reasoning(model),
            vision=True,
            audio=True,   # media rides the Files API upload path in _prepare_gemini_contents
            image_generation=True,
        )

    def complete(self, request: ChatRequest, cancel: CancelToken) -> str:
        system_prompt, gemini_contents, uploaded_files = _prepare_gemini_contents(
            request.messages, cancel_event=cancel.event, api_key=self.api_key
        )
        kwargs = {k: v for k, v in request.extra_kwargs.items() if k != "cancellation_event"}
        generation_config = dict(kwargs) if kwargs else {}
        if request.task == config.TASK_CHAT:
            thinking_config = gemini_thinking_config(self.model_id, self.reasoning_level)
            if thinking_config is not None:
                generation_config["thinkingConfig"] = thinking_config
        request_body = {"contents": gemini_contents}
        if system_prompt:
            request_body["system_instruction"] = {"parts": [{"text": str(system_prompt)}]}
        if generation_config:
            request_body["generationConfig"] = generation_config

        try:
            payload = _gemini_post_json(
                f"{GEMINI_BASE_URL}/v1beta/models/{self.model_id}:generateContent",
                request_body,
                timeout=_calculate_gemini_timeout(request.messages),
                cancel_event=cancel.event,
                api_key=self.api_key,
            )
        finally:
            for file_name in uploaded_files:
                _gemini_delete_file(file_name, api_key=self.api_key)

        return _extract_gemini_text(payload)

    def stream(self, request: ChatRequest, cancel: CancelToken) -> Iterator[ProviderEvent]:
        yield ProviderEvent("done", self.complete(request, cancel))
