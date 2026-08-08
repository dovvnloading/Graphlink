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

`stream()` (ADR-006 stage 6.5b) is real SSE streaming: chat.completions
.create(stream=True) with incremental deltas on the "text" channel and
OpenAI-compatible servers' `reasoning_content`/`reasoning` extras on the
"reasoning" channel. Exception translation stays with the caller, same as
every provider in this package.
"""

from __future__ import annotations

import base64
import json
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
    ToolCall,
    normalize_usage,
)

# OpenAI's input_audio accepts exactly these container formats today. mpga is
# MPEG-1 audio (mp3-family bytes); .mpeg is deliberately ABSENT - it
# conventionally names an MPEG program/video stream, and the attachment
# classifier stages it as audio (mutagen reads the audio track's duration
# from the container), so mapping it to "mp3" would ship program-stream
# bytes labeled mp3 and reintroduce the provider-side 400 this conversion
# exists to eliminate (6.3 adversarial review, proven with a real ffmpeg
# .mpeg fixture). It routes to the actionable error below instead.
_OPENAI_AUDIO_FORMATS = {"wav": "wav", "mp3": "mp3", "mpga": "mp3"}


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
        # ADR-007 stage 7.1: the two tool-turn roles ChatRequest's docstring
        # adds, translated to OpenAI's native shape - checked before the
        # list-content branch below since neither ever carries part-list
        # content. OpenAI's tool_call.function.arguments is a JSON STRING on
        # the wire (unlike Ollama's already-parsed dict), so the app's
        # parsed dict is re-serialized here, mirroring how api_provider.
        # _prepare_ollama_messages handles the same two roles for Ollama.
        role = message.get("role")
        if role == "tool":
            prepared.append({
                "role": "tool",
                "tool_call_id": message.get("tool_call_id", ""),
                "content": str(message.get("content") or ""),
            })
            continue
        tool_calls = message.get("tool_calls")
        if tool_calls:
            prepared.append({
                "role": "assistant",
                "content": message.get("content") or None,
                "tool_calls": [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(call["arguments"]),
                        },
                    }
                    for call in tool_calls
                ],
            })
            continue

        content = message.get("content")
        if not isinstance(content, list):
            prepared.append(message)
            continue
        parts = []
        for part in content:
            if not isinstance(part, dict):
                # Same defensive posture as _prepare_ollama_messages' own
                # str(part) fallback - a non-dict part (legacy/hand-edited
                # session data) becomes text instead of an AttributeError.
                parts.append({"type": "text", "text": str(part)})
                continue
            part_type = part.get("type")
            if part_type == "text":
                parts.append({"type": "text", "text": str(part.get("text", ""))})
            elif part_type == "image_bytes":
                data = part.get("data") or b""
                if not data:
                    continue  # an empty data URI is malformed; drop the corrupt part
                encoded = base64.b64encode(data).decode("ascii")
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{_sniff_image_mime(data)};base64,{encoded}"},
                })
            elif part_type == "audio_file":
                path = str(part.get("path", ""))
                audio_format = _OPENAI_AUDIO_FORMATS.get(Path(path).suffix.lstrip(".").lower())
                if audio_format is None:
                    # Worded to NOT contain the "audio input" fragment
                    # _translate_chat_exception's audio-enrichment branch
                    # matches on - this message is already complete and
                    # actionable; the generic suffix would contradict it.
                    raise RuntimeError(
                        "OpenAI-compatible endpoints accept WAV and MP3 audio files.\n\n"
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
        self.last_usage: dict | None = None  # ADR-006 stage 6.8 - see complete()
        self.capabilities = ProviderCapabilities(
            streaming=True,  # 6.5b: real SSE via chat.completions.create(stream=True)
            reasoning=openai_supports_reasoning(model),
            vision=True,   # C4: image_url parts now real
            audio=True,    # C4: input_audio parts now real (wav/mp3)
            # Derived from the CLIENT, not asserted: "OpenAI-compatible"
            # covers llama-server/LM Studio/proxies with no images API at
            # all. Probing the client keeps the capability honest instead of
            # promising an affordance that would error at call time (6.3
            # adversarial review - generate_image's own hasattr guard is a
            # separate mechanism this flag was wrongly claiming to mirror).
            image_generation=callable(
                getattr(getattr(client, "images", None), "generate", None)
            ),
            # ADR-007 stage 7.1: unlike Ollama's per-model probe, OpenAI's
            # current model families all support native tool use
            # unconditionally (base.py's own ProviderCapabilities.tools
            # comment) - no capability call needed.
            tools=True,
            # ADR-007 stage 7.3: native response_format:{"type":"json_schema"}
            # structured outputs - see backend/structured_output.py's own
            # _native_kwargs_for_active_provider comment for the verified
            # SDK shape.
            structured_output=True,
        )

    # ADR-007 stage 7.1: deliberately NOT given tool-call support, matching
    # OllamaProvider.complete()'s own exclusion comment - complete() returns
    # a bare str with no channel for ToolCall events, and its callers never
    # supply request.tools. A future caller passing tools here has them
    # silently ignored (no `tools` kwarg built below).

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
        # ADR-006 stage 6.8: response.usage is in hand here - captured on a
        # side attribute (complete() returns a bare str by protocol). Note
        # api_provider.chat() deliberately does not surface API-mode blocking
        # usage today (the chat UI streams everywhere since 6.5b).
        response_usage = getattr(response, "usage", None)
        self.last_usage = normalize_usage(
            getattr(response_usage, "prompt_tokens", None),
            getattr(response_usage, "completion_tokens", None),
        )
        return response.choices[0].message.content

    def stream(self, request: ChatRequest, cancel: CancelToken) -> Iterator[ProviderEvent]:
        # ADR-006 stage 6.5b: real SSE streaming, same request prep as
        # complete() (C4 message conversion, reasoning kwargs gated on
        # TASK_CHAT), same cancellation pattern as OllamaProvider.stream():
        # check at the top of every iteration, close the live HTTP stream
        # BEFORE raising, close again (idempotently) in the finally.
        _raise_if_cancelled(cancel.event)
        kwargs = {k: v for k, v in request.extra_kwargs.items() if k != "cancellation_event"}
        kwargs.pop("stream", None)  # ours to set - a passthrough kwarg can't turn it back off
        if request.task == config.TASK_CHAT:
            kwargs.update(openai_reasoning_kwargs(self.model_id, self.reasoning_level))
        if request.tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in request.tools
            ]

        content_parts: list[str] = []
        # ADR-007 stage 7.1: OpenAI streams each tool call's arguments as
        # JSON TEXT FRAGMENTS keyed by the call's `index` (never by `id` -
        # only the first delta for a given call carries one), unlike
        # Ollama's whole-object delivery - buffered here and parsed once
        # complete, so ToolCall.arguments is always an already-parsed dict.
        tool_call_buffers: dict[int, dict] = {}
        prepared_messages = prepare_openai_messages(request.messages)
        # ADR-006 stage 6.8: ask for the final usage chunk. Some
        # OpenAI-compatible servers (older vLLM/LM Studio builds) reject
        # stream_options outright (TypeError from a narrower fake/wrapper, or
        # a BadRequest naming the param) - retry ONCE without it and degrade
        # to no usage rather than failing the request.
        try:
            stream = self.client.chat.completions.create(
                model=self.model_id,
                messages=prepared_messages,
                stream=True,
                stream_options={"include_usage": True},
                **kwargs,
            )
        except Exception as exc:
            if "stream_options" not in str(exc) and not isinstance(exc, TypeError):
                raise
            stream = self.client.chat.completions.create(
                model=self.model_id,
                messages=prepared_messages,
                stream=True,
                **kwargs,
            )
        usage = None
        try:
            for chunk in stream:
                if cancel.is_set():
                    stream.close()  # closes the SDK Stream's underlying HTTP response
                _raise_if_cancelled(cancel.event)  # raises if just closed above

                # ADR-006 stage 6.8: with include_usage, the final chunk (or
                # a usage-only empty-choices chunk) carries real counts.
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    usage = normalize_usage(
                        getattr(chunk_usage, "prompt_tokens", None),
                        getattr(chunk_usage, "completion_tokens", None),
                    ) or usage

                # Some OpenAI-compatible servers send usage-only/keep-alive
                # chunks with an empty choices list - skip, don't IndexError.
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = choices[0].delta
                if delta is None:
                    continue
                delta_content = getattr(delta, "content", None) or ""
                if delta_content:
                    content_parts.append(delta_content)
                    yield ProviderEvent("text", delta_content)
                # ChoiceDelta has no typed reasoning field, but the model
                # allows extras - OpenAI-compatible servers (vLLM,
                # llama-server, LM Studio, ...) put thinking deltas under
                # reasoning_content / reasoning.
                delta_reasoning = (
                    getattr(delta, "reasoning_content", None)
                    or getattr(delta, "reasoning", None)
                    or ""
                )
                if delta_reasoning:
                    yield ProviderEvent("reasoning", delta_reasoning)
                # ADR-007 stage 7.1: each delta carries one fragment of one
                # tool call, addressed by `index` - `id`/`function.name`
                # arrive whole on that call's first delta (the `or` below
                # preserves them against later empty-string deltas),
                # `function.arguments` accumulates char-by-char.
                for tc in getattr(delta, "tool_calls", None) or ():
                    buf = tool_call_buffers.setdefault(
                        tc.index, {"id": "", "name": "", "arguments": ""}
                    )
                    buf["id"] = getattr(tc, "id", None) or buf["id"]
                    function = getattr(tc, "function", None)
                    if function is not None:
                        buf["name"] = getattr(function, "name", None) or buf["name"]
                        buf["arguments"] += getattr(function, "arguments", None) or ""
        finally:
            stream.close()  # idempotent on an already-closed/exhausted stream

        if tool_call_buffers:
            # ADR-007 stage 7.1: mirrors OllamaProvider.stream()'s own
            # tool_calls branch - a pure tool-call turn is a complete,
            # legitimate outcome, so `done` carries whatever lead-in text
            # streamed (often none) rather than being treated as an error.
            for index in sorted(tool_call_buffers):
                buf = tool_call_buffers[index]
                yield ProviderEvent(
                    "tool_call",
                    tool_call=ToolCall(
                        id=buf["id"] or f"call_{index}",
                        name=buf["name"],
                        arguments=json.loads(buf["arguments"]) if buf["arguments"] else {},
                    ),
                )
            yield ProviderEvent("done", "".join(content_parts), usage=usage)
            return

        # Deliberate parity with complete(), which returns message.content
        # untouched (no <think> composition anywhere on the OpenAI path
        # today): the final text is the raw concatenated content deltas.
        yield ProviderEvent("done", "".join(content_parts), usage=usage)
