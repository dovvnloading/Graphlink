"""ADR-006 stage 6.3: OpenAIProvider - the OpenAI-compatible port, closing C4.

Faithful port of api_provider.chat()'s OpenAI branch (client.chat.completions
.create with per-model reasoning kwargs applied only for TASK_CHAT), plus the
one piece of genuinely NEW behavior this stage adds anywhere: multimodal
message prep. The old branch passed the app's message shape RAW to the SDK,
so image/audio attachments silently reached OpenAI-compatible endpoints as
unusable python-repr'd dicts (audit finding C4). prepare_messages now
converts the app's content parts to the OpenAI content-part format:

- {"type": "text", ...}        -> unchanged (already the OpenAI shape);
- {"type": "image_bytes", ...} -> {"type": "image_url"} with a base64 data
  URI, mime sniffed from the bytes' own magic numbers (attachments carry raw
  bytes, no mime - see backend/attachments.py's content_part comment);
- {"type": "audio_file", ...}  -> {"type": "input_audio"} with base64 data
  and the format OpenAI's API accepts (wav/mp3). Other audio containers get
  a clear, actionable error instead of a provider-side 400 - a stated
  capability boundary, not a silent drop.

`stream()` is the transitional single-"done" shape (matching chat_stream's
existing documented non-Ollama fallback of exactly one full-text chunk);
stage 6.4 replaces it with real SSE streaming. Exception translation stays
with the caller, same as every provider in this package.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Iterator

import graphlink_task_config as config
from api_provider import (
    _raise_if_cancelled,
    _read_attachment_bytes,
    openai_reasoning_kwargs,
    openai_supports_reasoning,
)
from backend.providers.base import (
    CancelToken,
    ChatRequest,
    ProviderCapabilities,
    ProviderEvent,
)

# OpenAI's input_audio accepts exactly these container formats today.
_OPENAI_AUDIO_FORMATS = {"wav": "wav", "mp3": "mp3", "mpga": "mp3", "mpeg": "mp3"}


def _sniff_image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    # The attachment classifier only stages png/jpg/jpeg/webp as images
    # (backend/attachments.py), so this default is a corrupted-file edge, not
    # a real format gap - png keeps the request well-formed either way.
    return "image/png"


def prepare_openai_messages(messages: list) -> list:
    """C4: the app's wire shape -> OpenAI content parts. Plain-string
    content and already-flat messages pass through untouched, so text-only
    requests are byte-identical to the pre-port behavior."""
    prepared = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            prepared.append(message)
            continue
        parts = []
        for part in content:
            part_type = part.get("type")
            if part_type == "text":
                parts.append({"type": "text", "text": str(part.get("text", ""))})
            elif part_type == "image_bytes":
                data = part.get("data") or b""
                encoded = base64.b64encode(data).decode("ascii")
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{_sniff_image_mime(data)};base64,{encoded}"},
                })
            elif part_type == "audio_file":
                path = str(part.get("path", ""))
                audio_format = _OPENAI_AUDIO_FORMATS.get(Path(path).suffix.lstrip(".").lower())
                if audio_format is None:
                    raise RuntimeError(
                        "OpenAI-compatible audio input supports WAV and MP3 files.\n\n"
                        f"'{Path(path).name}' is a different container - convert it, or use "
                        "Ollama or Gemini for this attachment."
                    )
                data = _read_attachment_bytes(path, "audio")
                parts.append({
                    "type": "input_audio",
                    "input_audio": {
                        "data": base64.b64encode(data).decode("ascii"),
                        "format": audio_format,
                    },
                })
            else:
                # Unknown part kinds pass through untouched rather than being
                # silently dropped - the provider's own 400 names the problem.
                parts.append(part)
        prepared.append({**message, "content": parts})
    return prepared


class OpenAIProvider:
    def __init__(self, *, client, model: str, reasoning_level: str = "off"):
        self.client = client
        self.model_id = model
        self.reasoning_level = reasoning_level
        self.capabilities = ProviderCapabilities(
            streaming=False,  # 6.4 flips this with real SSE
            reasoning=openai_supports_reasoning(model),
            vision=True,   # C4: image_url parts now real
            audio=True,    # C4: input_audio parts now real (wav/mp3)
            image_generation=True,  # endpoint-dependent; the images API branch guards at call time
        )

    def complete(self, request: ChatRequest, cancel: CancelToken) -> str:
        kwargs = {k: v for k, v in request.extra_kwargs.items() if k != "cancellation_event"}
        if request.task == config.TASK_CHAT:
            kwargs.update(openai_reasoning_kwargs(self.model_id, self.reasoning_level))
        response = self.client.chat.completions.create(
            model=self.model_id,
            messages=prepare_openai_messages(request.messages),
            **kwargs,
        )
        _raise_if_cancelled(cancel.event)
        return response.choices[0].message.content

    def stream(self, request: ChatRequest, cancel: CancelToken) -> Iterator[ProviderEvent]:
        yield ProviderEvent("done", self.complete(request, cancel))
