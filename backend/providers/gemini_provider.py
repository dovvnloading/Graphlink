"""ADR-006 stage 6.3: GeminiProvider - faithful port of chat()'s Gemini
branch (raw REST :generateContent, no SDK). Preserves exactly: content prep
with media upload via _prepare_gemini_contents (which returns the uploaded
file names), thinkingConfig gated on TASK_CHAT, the message-size-derived
timeout, and - load-bearing - the finally-block cleanup that deletes every
uploaded file even when the generation call fails.

stream() (ADR-006 stage 6.5b) streams the same request through
:streamGenerateContent?alt=sse via _gemini_stream_sse (the streaming
sibling of _gemini_post_json): parts without "thought": true are "text"
events, parts with it (thinkingConfig.includeThoughts) are "reasoning"
events, and the uploaded-files cleanup finally runs on EVERY exit path,
including mid-stream cancellation.
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
    _gemini_stream_sse,
    _prepare_gemini_contents,
    _raise_if_cancelled,
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
            streaming=True,  # 6.5b: real SSE via :streamGenerateContent?alt=sse
            reasoning=gemini_supports_reasoning(model),
            vision=True,
            audio=True,   # media rides the Files API upload path in _prepare_gemini_contents
            image_generation=True,
        )

    def _request_body(self, request: ChatRequest, system_prompt, gemini_contents) -> dict:
        """The shared body build of both paths: passthrough kwargs as
        generationConfig, thinkingConfig gated on TASK_CHAT."""
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
        return request_body

    def complete(self, request: ChatRequest, cancel: CancelToken) -> str:
        system_prompt, gemini_contents, uploaded_files = _prepare_gemini_contents(
            request.messages, cancel_event=cancel.event, api_key=self.api_key
        )
        request_body = self._request_body(request, system_prompt, gemini_contents)

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
        # ADR-006 stage 6.5b: same prep and body as complete(), streamed over
        # SSE. The uploaded-files finally wraps EVERYTHING network-facing -
        # a mid-stream cancel or consumer abandonment (GeneratorExit at any
        # yield below) still deletes every uploaded file, exactly the
        # invariant complete()'s own finally pins.
        _raise_if_cancelled(cancel.event)
        system_prompt, gemini_contents, uploaded_files = _prepare_gemini_contents(
            request.messages, cancel_event=cancel.event, api_key=self.api_key
        )
        try:
            request_body = self._request_body(request, system_prompt, gemini_contents)
            text_parts: list[str] = []
            sse = _gemini_stream_sse(
                f"{GEMINI_BASE_URL}/v1beta/models/{self.model_id}:streamGenerateContent?alt=sse",
                request_body,
                timeout=_calculate_gemini_timeout(request.messages),
                cancel_event=cancel.event,
                api_key=self.api_key,
            )
            try:
                for payload in sse:
                    if cancel.is_set():
                        sse.close()  # tears down the live urllib response
                    _raise_if_cancelled(cancel.event)  # raises if just closed above

                    # 6.5b review (MEDIUM): a mid-stream error frame
                    # ({"error": {"code": ..., "message": ...}}) matches
                    # neither promptFeedback nor candidates - skipping it
                    # would return the partial text as a complete response.
                    # Raise with the same message extraction _gemini_post_json
                    # applies to that exact payload shape.
                    error_info = payload.get("error")
                    if error_info:
                        message = (
                            error_info.get("message") if isinstance(error_info, dict) else None
                        )
                        raise RuntimeError(message or str(error_info))

                    # Same safety-filter contract as _extract_gemini_text.
                    prompt_feedback = payload.get("promptFeedback", {}) or {}
                    block_reason = (
                        prompt_feedback.get("blockReason") or prompt_feedback.get("block_reason")
                    )
                    if block_reason:
                        raise RuntimeError(
                            f"The response was blocked by Google's Safety Filters ({block_reason})."
                        )
                    for candidate in payload.get("candidates", []) or []:
                        content = candidate.get("content", {}) or {}
                        for part in content.get("parts", []) or []:
                            text = part.get("text")
                            if not text:
                                continue
                            text_parts.append(text)
                            # thinkingConfig.includeThoughts marks thought
                            # parts with "thought": true - they stream on the
                            # reasoning channel, but their text STAYS in the
                            # final concatenation below.
                            if part.get("thought") is True:
                                yield ProviderEvent("reasoning", text)
                            else:
                                yield ProviderEvent("text", text)
            finally:
                sse.close()  # idempotent on an already-closed generator

            # Deliberate parity with complete(): _extract_gemini_text
            # concatenates EVERY text part, thought or not, and never calls
            # _compose_reasoned_response - Gemini has no <think> composition
            # today, and giving the streamed path one would be a silent
            # behavior change. Revisit when Gemini gets composition.
            final = "".join(text_parts).strip()
            if not final:
                raise RuntimeError("Gemini returned an empty response.")
        finally:
            for file_name in uploaded_files:
                _gemini_delete_file(file_name, api_key=self.api_key)

        yield ProviderEvent("done", final)
