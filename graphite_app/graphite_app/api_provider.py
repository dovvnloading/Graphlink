import base64
import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlparse

import ollama
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    requests = None
    REQUESTS_AVAILABLE = False

import graphite_config as config
from graphite_audio import (
    AudioTranscriptionError,
    format_duration,
    guess_audio_mime_type,
    transcribe_audio_file,
)


USE_API_MODE = False
API_PROVIDER_TYPE = None
API_CLIENT = None
API_KEY = None
API_BASE_URL = None
API_MODELS = {
    config.TASK_TITLE: None,
    config.TASK_CHAT: None,
    config.TASK_CHART: None,
    config.TASK_IMAGE_GEN: None,
    config.TASK_WEB_VALIDATE: None,
    config.TASK_WEB_SUMMARIZE: None,
}

GEMINI_MODELS_STATIC = sorted([
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
])

GEMINI_IMAGE_MODELS_STATIC = sorted([
    "gemini-2.5-flash-image",
    "gemini-3.1-flash-image-preview",
])

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"
MAX_TRANSCRIPT_FALLBACK_TOKENS = 6000


def _prepare_ollama_messages(messages: list) -> list:
    processed_messages = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            text_parts = []
            image_parts = []
            for part in content:
                if part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
                elif part.get("type") == "image_bytes":
                    image_data = part.get("data")
                    if image_data:
                        image_parts.append(image_data)

            new_msg = {
                "role": msg["role"],
                "content": "\n".join(part for part in text_parts if part),
            }
            if image_parts:
                new_msg["images"] = image_parts
            processed_messages.append(new_msg)
        else:
            processed_messages.append(msg)
    return processed_messages


def _decode_base64_image(image_data: str) -> bytes:
    try:
        return base64.b64decode(image_data)
    except Exception as exc:
        raise RuntimeError(f"Failed to decode generated image payload: {exc}") from exc


def _extract_openai_image_bytes(response) -> bytes:
    data_items = getattr(response, "data", None)
    if not data_items:
        raise RuntimeError("Image endpoint returned no image payload.")

    first_item = data_items[0]
    b64_json = getattr(first_item, "b64_json", None)
    if not b64_json and isinstance(first_item, dict):
        b64_json = first_item.get("b64_json")
    if b64_json:
        return _decode_base64_image(b64_json)

    image_url = getattr(first_item, "url", None)
    if not image_url and isinstance(first_item, dict):
        image_url = first_item.get("url")
    if image_url:
        try:
            with urllib.request.urlopen(image_url, timeout=120) as resp:
                return resp.read()
        except Exception as exc:
            raise RuntimeError(
                f"Image endpoint returned a URL, but the image download failed: {exc}"
            ) from exc

    raise RuntimeError("Image endpoint response did not include b64_json or a URL.")


def _extract_gemini_image_bytes(payload: dict) -> bytes:
    for candidate in payload.get("candidates", []):
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            inline_data = part.get("inline_data") or part.get("inlineData")
            if inline_data and inline_data.get("data"):
                return _decode_base64_image(inline_data["data"])

    prompt_feedback = payload.get("promptFeedback", {})
    block_reason = prompt_feedback.get("blockReason") or prompt_feedback.get("block_reason")
    if block_reason:
        raise RuntimeError(f"Gemini blocked the image request: {block_reason}")

    model_text = []
    for candidate in payload.get("candidates", []):
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            if part.get("text"):
                model_text.append(part["text"])
    if model_text:
        raise RuntimeError(
            f"Gemini returned text instead of image data: {' '.join(model_text).strip()}"
        )

    raise RuntimeError("Gemini did not return image data.")


def _get_gemini_api_key() -> str:
    api_key = API_KEY or os.environ.get("GRAPHITE_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Gemini API key not configured. Open Settings and save your Gemini API key.")
    return api_key


def _is_local_base_url(base_url: str | None) -> bool:
    if not base_url:
        return False

    parsed = urlparse(base_url)
    hostname = (parsed.hostname or "").lower()
    return hostname in {"localhost", "127.0.0.1", "::1"}


def _guess_image_mime_type(image_data: bytes) -> str:
    if image_data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_data.startswith(b"RIFF") and image_data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _audio_attachment_xml(part: dict, transcript: str) -> str:
    name = _escape_xml(part.get("name") or os.path.basename(part.get("path", "")) or "audio")
    mime_type = _escape_xml(part.get("mime_type") or guess_audio_mime_type(part.get("path", "")))
    duration_text = _escape_xml(format_duration(part.get("duration_seconds")))
    transcript_cdata = transcript.replace("]]>", "]]]]><![CDATA[>")
    return (
        f'<audio_attachment name="{name}" mime_type="{mime_type}" duration="{duration_text}" '
        'mode="transcript_fallback">\n'
        f'<![CDATA[\n{transcript_cdata}\n]]>\n'
        "</audio_attachment>"
    )


def _escape_xml(value) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def _iter_audio_parts(messages: list):
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "audio_file":
                yield part


def _message_contains_audio(messages: list) -> bool:
    return any(True for _ in _iter_audio_parts(messages))


def _native_audio_supported(task: str, model_name: str | None) -> bool:
    if task != config.TASK_CHAT:
        return False
    if not USE_API_MODE:
        return False
    if API_PROVIDER_TYPE != config.API_PROVIDER_GEMINI:
        return False
    if not model_name:
        return False
    lowered = model_name.lower()
    return lowered.startswith("gemini-") and "image" not in lowered


def _preprocess_audio_for_provider(messages: list, *, native_audio_enabled: bool):
    if native_audio_enabled:
        return

    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue

        rewritten_parts = []
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "audio_file":
                rewritten_parts.append(part)
                continue

            transcript = (part.get("transcript_text") or "").strip()
            if not transcript:
                transcript = transcribe_audio_file(part.get("path", ""))
                part["transcript_text"] = transcript

            transcript_tokens = _estimate_tokens(transcript)
            if transcript_tokens > MAX_TRANSCRIPT_FALLBACK_TOKENS:
                raise RuntimeError(
                    "This audio is too long for transcript-based handling with the selected model. "
                    "Try a shorter clip, or switch to a Gemini model that supports native audio."
                )

            rewritten_parts.append({
                "type": "text",
                "text": _audio_attachment_xml(part, transcript),
            })

        message["content"] = rewritten_parts


def _gemini_headers(api_key: str, extra_headers: dict | None = None) -> dict:
    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    return headers


def _gemini_post_json(endpoint: str, body: dict, timeout: int = 120) -> dict:
    api_key = _get_gemini_api_key()
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers=_gemini_headers(api_key),
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_payload = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(error_payload)
            message = parsed.get("error", {}).get("message") or error_payload
        except json.JSONDecodeError:
            message = error_payload
        raise RuntimeError(message) from exc


def _gemini_upload_file(file_path: str, mime_type: str, display_name: str | None = None) -> dict:
    api_key = _get_gemini_api_key()
    resolved_path = os.path.abspath(file_path)
    file_size = os.path.getsize(resolved_path)
    upload_start = urllib.request.Request(
        f"{GEMINI_BASE_URL}/upload/v1beta/files",
        data=json.dumps({"file": {"display_name": display_name or os.path.basename(resolved_path)}}).encode("utf-8"),
        headers=_gemini_headers(api_key, {
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(file_size),
            "X-Goog-Upload-Header-Content-Type": mime_type,
        }),
        method="POST",
    )

    try:
        with urllib.request.urlopen(upload_start, timeout=300) as response:
            upload_url = response.headers.get("X-Goog-Upload-URL")
    except urllib.error.HTTPError as exc:
        error_payload = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini file upload initialization failed: {error_payload}") from exc

    if not upload_url:
        raise RuntimeError("Gemini file upload did not return an upload URL.")

    if REQUESTS_AVAILABLE:
        with open(resolved_path, "rb") as source_file:
            response = requests.post(
                upload_url,
                headers={
                    "Content-Length": str(file_size),
                    "X-Goog-Upload-Offset": "0",
                    "X-Goog-Upload-Command": "upload, finalize",
                },
                data=source_file,
                timeout=1800,
            )
        if not response.ok:
            raise RuntimeError(f"Gemini file upload failed: {response.text}")
        payload = response.json()
    else:
        with open(resolved_path, "rb") as source_file:
            upload_request = urllib.request.Request(
                upload_url,
                data=source_file.read(),
                headers={
                    "Content-Length": str(file_size),
                    "X-Goog-Upload-Offset": "0",
                    "X-Goog-Upload-Command": "upload, finalize",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(upload_request, timeout=1800) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                error_payload = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Gemini file upload failed: {error_payload}") from exc

    file_info = payload.get("file", {})
    file_uri = file_info.get("uri")
    file_name = file_info.get("name")
    resolved_mime = file_info.get("mimeType") or file_info.get("mime_type") or mime_type

    if not file_uri or not file_name:
        raise RuntimeError("Gemini file upload succeeded, but the file metadata was incomplete.")

    return {
        "name": file_name,
        "uri": file_uri,
        "mime_type": resolved_mime,
    }


def _gemini_delete_file(file_name: str):
    if not file_name:
        return

    api_key = _get_gemini_api_key()
    resource_name = file_name if str(file_name).startswith("files/") else f"files/{file_name}"
    delete_request = urllib.request.Request(
        f"{GEMINI_BASE_URL}/v1beta/{resource_name}",
        headers={"x-goog-api-key": api_key},
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(delete_request, timeout=60):
            return
    except Exception:
        return


def _gemini_part_from_content(part: dict, uploaded_files: list) -> dict | None:
    part_type = part.get("type")
    if part_type == "text":
        return {"text": part.get("text", "")}

    if part_type == "image_bytes":
        image_data = part.get("data")
        if image_data:
            return {
                "inline_data": {
                    "mime_type": _guess_image_mime_type(image_data),
                    "data": base64.b64encode(image_data).decode("utf-8"),
                }
            }
        return None

    if part_type == "audio_file":
        audio_path = part.get("path")
        if not audio_path or not os.path.isfile(audio_path):
            raise RuntimeError(
                f"Attached audio file is no longer available: {part.get('name') or audio_path or '[missing file]'}"
            )

        mime_type = part.get("mime_type") or guess_audio_mime_type(audio_path)
        upload_info = _gemini_upload_file(audio_path, mime_type, part.get("name"))
        uploaded_files.append(upload_info.get("name"))
        return {
            "file_data": {
                "mime_type": upload_info["mime_type"],
                "file_uri": upload_info["uri"],
            }
        }

    return None


def _prepare_gemini_contents(messages: list) -> tuple[str | None, list, list]:
    system_prompt = None
    contents = []
    uploaded_files = []

    for msg in messages:
        role_name = msg.get("role")
        if role_name == "system":
            system_prompt = msg.get("content")
            continue

        role = "model" if role_name == "assistant" else "user"
        content = msg.get("content")
        parts = []

        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    parts.append({"text": str(part)})
                    continue
                gemini_part = _gemini_part_from_content(part, uploaded_files)
                if gemini_part is not None:
                    parts.append(gemini_part)
        else:
            parts.append({"text": str(content)})

        if not parts:
            continue

        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].extend(parts)
            continue

        contents.append({
            "role": role,
            "parts": parts,
        })

    return system_prompt, contents, uploaded_files


def _extract_gemini_text(payload: dict) -> str:
    prompt_feedback = payload.get("promptFeedback", {})
    block_reason = prompt_feedback.get("blockReason") or prompt_feedback.get("block_reason")
    if block_reason:
        raise RuntimeError(f"The response was blocked by Google's Safety Filters ({block_reason}).")

    text_parts = []
    for candidate in payload.get("candidates", []):
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            text = part.get("text")
            if text:
                text_parts.append(text)

    response_text = "".join(text_parts).strip()
    if not response_text:
        raise RuntimeError("Gemini returned an empty response.")
    return response_text


def _calculate_gemini_timeout(messages: list) -> int:
    max_audio_duration = 0
    for part in _iter_audio_parts(messages):
        max_audio_duration = max(max_audio_duration, int(part.get("duration_seconds") or 0))

    if max_audio_duration <= 0:
        return 180
    return min(1800, max(300, 180 + max_audio_duration // 2))


def generate_image(prompt: str, size: str = "1024x1024") -> bytes:
    if not prompt or not prompt.strip():
        raise ValueError("Image prompt cannot be empty.")

    if not USE_API_MODE:
        raise RuntimeError("Image generation is only available in API Endpoint mode.")

    if not API_CLIENT:
        raise RuntimeError("API client not initialized. Configure API settings first.")

    api_model = API_MODELS.get(config.TASK_IMAGE_GEN)
    if not api_model and API_PROVIDER_TYPE == config.API_PROVIDER_GEMINI:
        api_model = "gemini-2.5-flash-image"
    if not api_model:
        raise RuntimeError(
            "No image generation model configured.\n"
            "Please select one in API Settings."
        )

    try:
        if API_PROVIDER_TYPE == config.API_PROVIDER_OPENAI:
            if not hasattr(API_CLIENT, "images") or not hasattr(API_CLIENT.images, "generate"):
                raise RuntimeError("The configured OpenAI-compatible client does not expose an images.generate API.")

            response = API_CLIENT.images.generate(
                model=api_model,
                prompt=prompt,
                size=size,
            )
            return _extract_openai_image_bytes(response)

        if API_PROVIDER_TYPE == config.API_PROVIDER_GEMINI:
            payload = _gemini_post_json(
                f"{GEMINI_BASE_URL}/v1beta/models/{api_model}:generateContent",
                {
                    "contents": [{
                        "parts": [{"text": prompt}],
                    }],
                    "generationConfig": {
                        "responseModalities": ["IMAGE"],
                    },
                },
                timeout=120,
            )
            return _extract_gemini_image_bytes(payload)

        raise RuntimeError(f"Unsupported API provider: {API_PROVIDER_TYPE}")
    except Exception as exc:
        error_str = str(exc).lower()
        if "429" in error_str or "quota" in error_str or "resourceexhausted" in error_str:
            raise RuntimeError(
                "Image generation quota exceeded.\n\n"
                "Please use a lower-cost image model or verify billing is enabled for the selected provider."
            ) from exc
        raise


def chat(task: str, messages: list, **kwargs) -> dict:
    try:
        if not USE_API_MODE:
            model = config.OLLAMA_MODELS.get(task)
            if not model:
                raise ValueError(f"No Ollama model configured for task: {task}")

            _preprocess_audio_for_provider(messages, native_audio_enabled=False)
            ollama_messages = _prepare_ollama_messages(messages)

            ollama_kwargs = kwargs.copy()
            if task == config.TASK_CHAT and ("qwen3" in model.lower() or "deepseek" in model.lower()):
                ollama_kwargs["think"] = True

            response = ollama.chat(model=model, messages=ollama_messages, **ollama_kwargs)

            full_response_content = response["message"].get("content", "")
            reasoning = response["message"].get("thinking")

            if reasoning:
                full_response_content = f"<think>{reasoning}</think>\n{full_response_content}"

            return {
                "message": {
                    "content": full_response_content,
                    "role": "assistant",
                }
            }

        if not API_CLIENT:
            raise RuntimeError("API client not initialized. Configure API settings first.")

        if task == config.TASK_WEB_VALIDATE and API_PROVIDER_TYPE == config.API_PROVIDER_GEMINI:
            api_model = API_MODELS.get(task) or "gemini-3.1-flash-lite-preview"
        else:
            api_model = API_MODELS.get(task)

        if not api_model:
            raise RuntimeError(
                f"No API model configured for task '{task}'.\n"
                "Please configure models in API Settings."
            )

        native_audio_enabled = _native_audio_supported(task, api_model)
        _preprocess_audio_for_provider(messages, native_audio_enabled=native_audio_enabled)

        if API_PROVIDER_TYPE == config.API_PROVIDER_OPENAI:
            response = API_CLIENT.chat.completions.create(
                model=api_model,
                messages=messages,
                **kwargs,
            )
            return {
                "message": {
                    "content": response.choices[0].message.content,
                    "role": "assistant",
                }
            }

        if API_PROVIDER_TYPE == config.API_PROVIDER_GEMINI:
            system_prompt, gemini_contents, uploaded_files = _prepare_gemini_contents(messages)
            request_body = {
                "contents": gemini_contents,
            }
            if system_prompt:
                request_body["system_instruction"] = {
                    "parts": [{"text": str(system_prompt)}],
                }
            if kwargs:
                request_body["generationConfig"] = kwargs

            try:
                payload = _gemini_post_json(
                    f"{GEMINI_BASE_URL}/v1beta/models/{api_model}:generateContent",
                    request_body,
                    timeout=_calculate_gemini_timeout(messages),
                )
            finally:
                for file_name in uploaded_files:
                    _gemini_delete_file(file_name)

            return {
                "message": {
                    "content": _extract_gemini_text(payload),
                    "role": "assistant",
                }
            }

        raise RuntimeError(f"Unsupported API provider: {API_PROVIDER_TYPE}")

    except AudioTranscriptionError as exc:
        raise RuntimeError(
            f"{exc}\n\n"
            "Please try again, verify the audio contains spoken content, or switch to a Gemini model for native audio handling."
        ) from exc
    except Exception as exc:
        error_str = str(exc).lower()

        if "timed out" in error_str or "timeout" in error_str:
            if _message_contains_audio(messages):
                raise TimeoutError(
                    "The request timed out while processing audio.\n\n"
                    "Please try again. If this keeps happening, use a shorter clip or switch providers."
                ) from exc
            raise TimeoutError(
                "The model request timed out.\n\n"
                "Please try again or choose a faster model."
            ) from exc

        if "429" in error_str or "quota" in error_str or "resourceexhausted" in error_str:
            if API_PROVIDER_TYPE == config.API_PROVIDER_OPENAI:
                raise RuntimeError(
                    "OpenAI-compatible API quota exceeded or rate limited.\n\n"
                    "Please verify billing, rate limits, and the selected model for your endpoint."
                ) from exc
            raise RuntimeError(
                "Google Gemini API Quota Exceeded.\n\n"
                "Note: Google does not offer a free tier for their 'Pro' models. "
                "Please switch your default task models to a 'Flash' model in the API Settings, "
                "or link a billing account in Google AI Studio."
            ) from exc

        if (
            "connection refused" in error_str
            or "connecterror" in error_str
            or "connection error" in error_str
            or "all connection attempts failed" in error_str
        ):
            if not USE_API_MODE:
                raise ConnectionError(
                    "Failed to connect to local Ollama server. Please ensure the Ollama app is running and accessible."
                ) from exc
            raise ConnectionError(
                "Failed to connect to the API endpoint. Please verify your Base URL in settings and your network connection.\n\n"
                f"Details: {exc}"
            ) from exc

        raise


def initialize_api(provider: str, api_key: str, base_url: str = None):
    global API_PROVIDER_TYPE, API_CLIENT, API_KEY, API_BASE_URL
    API_PROVIDER_TYPE = provider
    API_KEY = api_key
    API_BASE_URL = base_url

    if provider == config.API_PROVIDER_OPENAI:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai package required. Install dependencies with: pip install -r requirements.txt"
            ) from exc

        if not base_url:
            base_url = "https://api.openai.com/v1"

        if not api_key:
            if _is_local_base_url(base_url):
                api_key = "dummy-key-for-local"
                API_KEY = api_key
            else:
                raise RuntimeError("OpenAI-compatible API key not configured. Open Settings and save your API key.")

        API_CLIENT = OpenAI(api_key=api_key, base_url=base_url)

    elif provider == config.API_PROVIDER_GEMINI:
        if not (api_key or os.environ.get("GRAPHITE_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")):
            raise RuntimeError("Gemini API key not configured. Open Settings and save your Gemini API key.")
        API_CLIENT = {"provider": config.API_PROVIDER_GEMINI}
    else:
        raise ValueError(f"Unknown API provider: {provider}")

    return API_CLIENT


def get_available_models():
    if not API_CLIENT:
        raise RuntimeError("API client not initialized")

    try:
        if API_PROVIDER_TYPE == config.API_PROVIDER_OPENAI:
            models = API_CLIENT.models.list()
            return sorted([model.id for model in models.data])
        if API_PROVIDER_TYPE == config.API_PROVIDER_GEMINI:
            return GEMINI_MODELS_STATIC
        return []
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch models from endpoint: {exc}") from exc


def set_mode(use_api: bool):
    global USE_API_MODE
    USE_API_MODE = use_api


def set_task_model(task: str, api_model: str):
    if task in API_MODELS:
        API_MODELS[task] = api_model


def get_task_models() -> dict:
    return API_MODELS.copy()


def get_mode() -> str:
    return "API" if USE_API_MODE else "Ollama"


def is_configured() -> bool:
    return API_CLIENT is not None and all(API_MODELS.values())
