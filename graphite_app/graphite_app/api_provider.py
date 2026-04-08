import base64
import inspect
import json
import os
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import ollama
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    requests = None
    REQUESTS_AVAILABLE = False

import graphite_config as config
from graphite_audio import guess_audio_mime_type


USE_API_MODE = False
API_PROVIDER_TYPE = None
API_CLIENT = None
API_KEY = None
API_BASE_URL = None
LOCAL_PROVIDER_TYPE = config.LOCAL_PROVIDER_OLLAMA
API_MODELS = {
    config.TASK_TITLE: None,
    config.TASK_CHAT: None,
    config.TASK_CHART: None,
    config.TASK_IMAGE_GEN: None,
    config.TASK_WEB_VALIDATE: None,
    config.TASK_WEB_SUMMARIZE: None,
}
LLAMA_CPP_SETTINGS = {
    "chat_model_path": "",
    "title_model_path": "",
    "reasoning_mode": "Quick",
    "chat_format": "",
    "n_ctx": 4096,
    "n_gpu_layers": 0,
    "n_threads": 0,
}
_LLAMA_CPP_CLIENT_CACHE = {}
_LLAMA_CPP_CLIENT_LOCK = threading.RLock()

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
_OLLAMA_CAPABILITY_CACHE = {}
_KNOWN_OLLAMA_AUDIO_MODEL_FAMILIES = {"gemma4"}
_THINK_TAG_PATTERN = re.compile(r"<(think|thinking)>\s*(.*?)\s*</\1>", re.DOTALL | re.IGNORECASE)
_THINK_CLOSING_ONLY_PATTERN = re.compile(r"</(think|thinking)>", re.IGNORECASE)
_FALLBACK_REASONING_PATTERN = re.compile(
    r"--- REASONING ---\s*(.*?)\s*--- END REASONING ---",
    re.DOTALL | re.IGNORECASE,
)
_HARMONY_ANALYSIS_PREFIX_PATTERN = re.compile(
    r"^\s*<\|channel\|>analysis<\|message\|>\s*",
    re.IGNORECASE,
)
_HARMONY_FINAL_MARKER_PATTERN = re.compile(
    r"<\|start\|>assistant<\|channel\|>(?:final|final json)<\|message\|>\s*",
    re.IGNORECASE,
)
_HARMONY_END_MARKER_PATTERN = re.compile(r"<\|end\|>\s*", re.IGNORECASE)


class RequestCancelledError(RuntimeError):
    """Raised when the user cancels an in-flight model request."""


def _normalize_ollama_models_root(path_value: str | None) -> Path | None:
    normalized = str(path_value or "").strip()
    if not normalized:
        return None

    candidate = Path(normalized).expanduser()
    if candidate.name.lower() == "manifests":
        return candidate
    if candidate.name.lower() == "models":
        return candidate / "manifests"
    return candidate / "models" / "manifests"


def _iter_existing_ollama_manifest_roots() -> list[Path]:
    candidate_roots: list[Path] = []
    env_models_root = os.environ.get("OLLAMA_MODELS")
    local_app_data = os.environ.get("LOCALAPPDATA")
    program_data = os.environ.get("PROGRAMDATA")

    for raw_path in (
        env_models_root,
        Path.home() / ".ollama",
        Path.home() / ".ollama" / "models",
        local_app_data and Path(local_app_data) / "Ollama",
        local_app_data and Path(local_app_data) / "Ollama" / "models",
        program_data and Path(program_data) / "Ollama",
        program_data and Path(program_data) / "Ollama" / "models",
    ):
        manifests_root = _normalize_ollama_models_root(raw_path)
        if manifests_root and manifests_root.is_dir():
            candidate_roots.append(manifests_root)

    unique_roots: list[Path] = []
    seen_roots: set[str] = set()
    for root in candidate_roots:
        resolved = str(root.resolve()).lower()
        if resolved in seen_roots:
            continue
        seen_roots.add(resolved)
        unique_roots.append(root)
    return unique_roots


def _discover_manifest_roots_in_folder(scan_path: str) -> list[Path]:
    root_path = Path(scan_path).expanduser()
    if not root_path.exists():
        raise RuntimeError(f"Scan folder does not exist: {scan_path}")
    if not root_path.is_dir():
        raise RuntimeError(f"Scan folder is not a directory: {scan_path}")

    direct_candidates = [
        root_path,
        root_path / "manifests",
        root_path / "models" / "manifests",
    ]
    manifest_roots: list[Path] = []
    seen_roots: set[str] = set()

    for candidate in direct_candidates:
        manifests_root = _normalize_ollama_models_root(candidate)
        if manifests_root and manifests_root.is_dir():
            resolved = str(manifests_root.resolve()).lower()
            if resolved not in seen_roots:
                seen_roots.add(resolved)
                manifest_roots.append(manifests_root)

    for current_root, dir_names, _ in os.walk(root_path):
        current_name = os.path.basename(current_root).lower()
        parent_name = os.path.basename(os.path.dirname(current_root)).lower()
        if current_name == "blobs":
            dir_names[:] = []
            continue
        if current_name == "manifests" and parent_name == "models":
            manifests_root = Path(current_root)
            resolved = str(manifests_root.resolve()).lower()
            if resolved not in seen_roots:
                seen_roots.add(resolved)
                manifest_roots.append(manifests_root)
            dir_names[:] = []

    return manifest_roots


def _extract_model_name_from_manifest_path(manifest_path: Path, manifests_root: Path) -> str | None:
    try:
        relative_parts = manifest_path.relative_to(manifests_root).parts
    except ValueError:
        return None

    if len(relative_parts) < 3:
        return None

    repository_parts = list(relative_parts[1:-1])
    if repository_parts and repository_parts[0].lower() == "library":
        repository_parts = repository_parts[1:]
    if not repository_parts:
        return None

    tag = relative_parts[-1].strip()
    if not tag:
        return None

    repository_name = "/".join(part.strip() for part in repository_parts if part.strip())
    if not repository_name:
        return None

    return f"{repository_name}:{tag}"


def _collect_models_from_manifest_root(manifests_root: Path) -> list[str]:
    discovered_models: set[str] = set()
    for current_root, dir_names, file_names in os.walk(manifests_root):
        dir_names[:] = [dir_name for dir_name in dir_names if dir_name.lower() != "blobs"]
        for file_name in file_names:
            manifest_path = Path(current_root) / file_name
            model_name = _extract_model_name_from_manifest_path(manifest_path, manifests_root)
            if model_name:
                discovered_models.add(model_name)
    return sorted(discovered_models, key=str.lower)


def _list_models_from_running_ollama() -> list[str]:
    try:
        response = ollama.list()
    except Exception:
        return []

    raw_models = _extract_response_field(response, "models", [])
    discovered_models: set[str] = set()
    for raw_model in raw_models or []:
        model_name = _extract_response_field(raw_model, "model") or _extract_response_field(raw_model, "name")
        normalized = str(model_name or "").strip()
        if normalized:
            discovered_models.add(normalized)
    return sorted(discovered_models, key=str.lower)


def scan_local_ollama_models(scan_path: str | None = None) -> dict:
    if scan_path:
        manifest_roots = _discover_manifest_roots_in_folder(scan_path)
        scan_mode = "folder"
        scan_root = str(Path(scan_path).expanduser().resolve())
        running_models: list[str] = []
    else:
        manifest_roots = _iter_existing_ollama_manifest_roots()
        scan_mode = "system"
        scan_root = ""
        running_models = _list_models_from_running_ollama()

    discovered_models: set[str] = set(running_models)
    scanned_locations: list[str] = []

    for manifests_root in manifest_roots:
        discovered_models.update(_collect_models_from_manifest_root(manifests_root))
        scanned_locations.append(str(manifests_root.resolve()))

    return {
        "models": sorted(discovered_models, key=str.lower),
        "scan_mode": scan_mode,
        "scan_path": scan_root,
        "locations": sorted(set(scanned_locations), key=str.lower),
    }


def _normalize_llama_cpp_scan_root(path_value: str | None) -> Path | None:
    normalized = str(path_value or "").strip()
    if not normalized:
        return None

    candidate = Path(normalized).expanduser()
    if candidate.is_file():
        return candidate.parent
    return candidate


def _iter_existing_llama_cpp_scan_roots() -> list[Path]:
    local_app_data = os.environ.get("LOCALAPPDATA")
    candidate_roots = [
        os.environ.get("LLAMA_CPP_MODELS"),
        Path.home() / "models",
        Path.home() / "llama.cpp",
        Path.home() / "llama.cpp" / "models",
        Path.home() / "Downloads",
        Path.home() / "Documents",
        Path.home() / "Desktop",
        Path.home() / ".cache" / "lm-studio" / "models",
        local_app_data and Path(local_app_data) / "llama.cpp" / "models",
    ]

    unique_roots: list[Path] = []
    seen_roots: set[str] = set()
    for raw_path in candidate_roots:
        root = _normalize_llama_cpp_scan_root(raw_path)
        if not root or not root.is_dir():
            continue
        resolved = str(root.resolve()).lower()
        if resolved in seen_roots:
            continue
        seen_roots.add(resolved)
        unique_roots.append(root)
    return unique_roots


def _collect_gguf_files_from_root(root_path: Path) -> list[str]:
    discovered_models: set[str] = set()
    skip_directories = {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        "node_modules",
        "venv",
        ".venv",
    }

    for current_root, dir_names, file_names in os.walk(root_path):
        dir_names[:] = [
            dir_name for dir_name in dir_names
            if dir_name.lower() not in skip_directories
        ]
        for file_name in file_names:
            if not file_name.lower().endswith(".gguf"):
                continue
            model_path = Path(current_root) / file_name
            discovered_models.add(str(model_path.resolve()))

    return sorted(discovered_models, key=str.lower)


def scan_local_llama_cpp_models(scan_path: str | None = None) -> dict:
    if scan_path:
        root = _normalize_llama_cpp_scan_root(scan_path)
        if not root or not root.exists():
            raise RuntimeError(f"Scan folder does not exist: {scan_path}")
        if not root.is_dir():
            raise RuntimeError(f"Scan folder is not a directory: {scan_path}")
        scan_roots = [root]
        scan_mode = "folder"
        scan_root = str(root.resolve())
    else:
        scan_roots = _iter_existing_llama_cpp_scan_roots()
        scan_mode = "system"
        scan_root = ""

    discovered_models: set[str] = set()
    scanned_locations: list[str] = []

    for root in scan_roots:
        discovered_models.update(_collect_gguf_files_from_root(root))
        scanned_locations.append(str(root.resolve()))

    return {
        "models": sorted(discovered_models, key=str.lower),
        "scan_mode": scan_mode,
        "scan_path": scan_root,
        "locations": sorted(set(scanned_locations), key=str.lower),
    }


def _read_attachment_bytes(file_path: str, attachment_kind: str) -> bytes:
    resolved_path = os.path.abspath(file_path or "")
    attachment_name = os.path.basename(resolved_path or file_path or "")
    if not resolved_path or not os.path.isfile(resolved_path):
        raise RuntimeError(
            f"Attached {attachment_kind} file is no longer available: "
            f"{attachment_name or '[missing file]'}"
        )

    try:
        with open(resolved_path, "rb") as source_file:
            return source_file.read()
    except OSError as exc:
        raise RuntimeError(
            f"Failed to read attached {attachment_kind} file '{attachment_name}': {exc}"
        ) from exc


def _extract_response_field(payload, field_name: str, default=None):
    if payload is None:
        return default
    if isinstance(payload, dict):
        return payload.get(field_name, default)
    if hasattr(payload, field_name):
        return getattr(payload, field_name)
    try:
        return payload[field_name]
    except Exception:
        return default


def _append_unique_text_segment(parts: list[str], text, seen: set[str]):
    normalized = str(text or "").strip()
    if not normalized:
        return

    key = re.sub(r"\s+", " ", normalized).strip().lower()
    if not key or key in seen:
        return

    seen.add(key)
    parts.append(normalized)


def _strip_leading_harmony_tokens(text: str) -> str:
    remaining = str(text or "")
    while True:
        updated = _HARMONY_FINAL_MARKER_PATTERN.sub("", remaining, count=1)
        updated = _HARMONY_END_MARKER_PATTERN.sub("", updated, count=1)
        updated = updated.lstrip()
        if updated == remaining:
            return updated
        remaining = updated


def _split_harmony_reasoning_block(text: str) -> tuple[str, str] | None:
    raw_text = str(text or "")
    prefix_match = _HARMONY_ANALYSIS_PREFIX_PATTERN.match(raw_text)
    if not prefix_match:
        return None

    remaining = raw_text[prefix_match.end():]
    final_match = _HARMONY_FINAL_MARKER_PATTERN.search(remaining)
    end_match = _HARMONY_END_MARKER_PATTERN.search(remaining)

    if final_match and (not end_match or final_match.start() <= end_match.start()):
        reasoning_text = remaining[:final_match.start()].strip()
        answer_text = remaining[final_match.end():].strip()
        return reasoning_text, answer_text

    if end_match:
        reasoning_text = remaining[:end_match.start()].strip()
        answer_text = _strip_leading_harmony_tokens(remaining[end_match.end():]).strip()
        return reasoning_text, answer_text

    return remaining.strip(), ""


def _split_closing_only_think_block(text: str) -> tuple[str, str] | None:
    raw_text = str(text or "")
    closing_match = _THINK_CLOSING_ONLY_PATTERN.search(raw_text)
    if not closing_match:
        return None

    prefix = raw_text[:closing_match.start()]
    if re.search(r"<(think|thinking)>", prefix, re.IGNORECASE):
        return None

    reasoning_text = prefix.strip()
    answer_text = raw_text[closing_match.end():].strip()
    return reasoning_text, answer_text


def split_reasoning_and_content(text: str) -> tuple[str, str]:
    remaining_text = str(text or "").strip()
    if not remaining_text:
        return "", ""

    reasoning_parts: list[str] = []
    reasoning_seen: set[str] = set()

    while True:
        changed = False

        think_match = _THINK_TAG_PATTERN.search(remaining_text)
        if think_match:
            _append_unique_text_segment(reasoning_parts, think_match.group(2), reasoning_seen)
            remaining_text = (
                f"{remaining_text[:think_match.start()]}\n{remaining_text[think_match.end():]}"
            ).strip()
            changed = True

        fallback_match = _FALLBACK_REASONING_PATTERN.search(remaining_text)
        if fallback_match:
            _append_unique_text_segment(reasoning_parts, fallback_match.group(1), reasoning_seen)
            remaining_text = (
                f"{remaining_text[:fallback_match.start()]}\n{remaining_text[fallback_match.end():]}"
            ).strip()
            changed = True

        closing_only_split = _split_closing_only_think_block(remaining_text)
        if closing_only_split:
            closing_reasoning, closing_answer = closing_only_split
            _append_unique_text_segment(reasoning_parts, closing_reasoning, reasoning_seen)
            remaining_text = closing_answer.strip()
            changed = True

        harmony_split = _split_harmony_reasoning_block(remaining_text)
        if harmony_split:
            harmony_reasoning, harmony_answer = harmony_split
            _append_unique_text_segment(reasoning_parts, harmony_reasoning, reasoning_seen)
            remaining_text = harmony_answer.strip()
            changed = True

        if not changed:
            break

    remaining_text = _strip_leading_harmony_tokens(remaining_text).strip()
    reasoning_text = "\n\n".join(reasoning_parts).strip()
    return reasoning_text, remaining_text


def _compose_reasoned_response(answer_text: str, reasoning_text: str, provider_name: str) -> str:
    normalized_answer = str(answer_text or "").strip()
    normalized_reasoning = str(reasoning_text or "").strip()

    if normalized_answer:
        if normalized_reasoning:
            return f"<think>{normalized_reasoning}</think>\n{normalized_answer}"
        return normalized_answer

    if normalized_reasoning:
        raise RuntimeError(
            f"{provider_name} returned reasoning but no final answer. "
            "Retry in Quick mode or choose a different chat format/model."
        )

    raise RuntimeError(f"{provider_name} returned an empty response.")


def _normalize_ollama_capabilities(capabilities) -> set[str]:
    if not capabilities:
        return set()
    if isinstance(capabilities, str):
        return {capabilities.lower()}
    return {
        str(capability).strip().lower()
        for capability in capabilities
        if str(capability).strip()
    }


def _get_ollama_capabilities(model_name: str | None) -> set[str] | None:
    normalized_model = (model_name or "").strip()
    if not normalized_model:
        return None

    cache_key = normalized_model.lower()
    if cache_key in _OLLAMA_CAPABILITY_CACHE:
        return _OLLAMA_CAPABILITY_CACHE[cache_key]

    show_fn = getattr(ollama, "show", None)
    if not callable(show_fn):
        _OLLAMA_CAPABILITY_CACHE[cache_key] = None
        return None

    try:
        try:
            show_response = show_fn(normalized_model)
        except TypeError:
            show_response = show_fn(model=normalized_model)
    except Exception:
        _OLLAMA_CAPABILITY_CACHE[cache_key] = None
        return None

    raw_capabilities = _extract_response_field(show_response, "capabilities")
    if raw_capabilities is None:
        _OLLAMA_CAPABILITY_CACHE[cache_key] = None
        return None

    capabilities = _normalize_ollama_capabilities(raw_capabilities)
    _OLLAMA_CAPABILITY_CACHE[cache_key] = capabilities
    return capabilities


def _is_known_ollama_audio_model(model_name: str | None) -> bool:
    normalized_model = (model_name or "").strip().lower()
    if not normalized_model:
        return False
    family = normalized_model.split(":", 1)[0]
    return family in _KNOWN_OLLAMA_AUDIO_MODEL_FAMILIES


def _assert_ollama_audio_support(model_name: str, messages: list):
    if not _message_contains_audio(messages):
        return

    capabilities = _get_ollama_capabilities(model_name)
    if capabilities is None:
        return

    if "audio" in capabilities:
        return

    if _is_known_ollama_audio_model(model_name):
        return

    raise RuntimeError(
        f"The selected Ollama model '{model_name}' does not advertise audio input support.\n\n"
        "Try again with an audio-capable Ollama model such as gemma4:e4b."
    )


def _prepare_ollama_messages(messages: list) -> list:
    processed_messages = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            text_parts = []
            media_parts = []
            for part in content:
                if not isinstance(part, dict):
                    text_parts.append(str(part))
                    continue

                part_type = part.get("type")
                if part_type == "text":
                    text_parts.append(part.get("text", ""))
                elif part_type == "image_bytes":
                    image_data = part.get("data")
                    if image_data:
                        media_parts.append(image_data)
                elif part_type == "audio_file":
                    # Ollama's native Gemma 4 audio currently reuses the multimodal `images` field.
                    media_parts.append(
                        _read_attachment_bytes(part.get("path", ""), "audio")
                    )

            new_msg = {
                "role": msg["role"],
                "content": "\n".join(part for part in text_parts if part),
            }
            if media_parts:
                new_msg["images"] = media_parts
            processed_messages.append(new_msg)
        else:
            processed_messages.append(msg)
    return processed_messages


def _normalize_llama_cpp_settings(settings: dict | None = None) -> dict:
    raw_settings = settings or {}
    normalized = {
        "chat_model_path": str(raw_settings.get("chat_model_path", "")).strip(),
        "title_model_path": str(raw_settings.get("title_model_path", "")).strip(),
        "reasoning_mode": (
            "Thinking"
            if str(raw_settings.get("reasoning_mode", "Quick")).strip().lower() == "thinking"
            else "Quick"
        ),
        "chat_format": str(raw_settings.get("chat_format", "")).strip(),
        "n_ctx": max(256, int(raw_settings.get("n_ctx", 4096) or 4096)),
        "n_gpu_layers": int(raw_settings.get("n_gpu_layers", 0) or 0),
        "n_threads": max(0, int(raw_settings.get("n_threads", 0) or 0)),
    }
    return normalized


def _resolve_llama_cpp_thread_count(configured_threads: int) -> int:
    configured = int(configured_threads or 0)
    if configured > 0:
        return configured

    cpu_count = os.cpu_count() or 4
    if cpu_count <= 2:
        return 1
    if cpu_count <= 4:
        return max(1, cpu_count - 1)
    if cpu_count <= 8:
        return max(1, cpu_count - 2)
    return max(1, cpu_count - max(2, cpu_count // 4))


def _close_llama_cpp_clients():
    with _LLAMA_CPP_CLIENT_LOCK:
        clients = list(_LLAMA_CPP_CLIENT_CACHE.values())
        _LLAMA_CPP_CLIENT_CACHE.clear()

    for client in clients:
        close_fn = getattr(client, "close", None)
        if callable(close_fn):
            try:
                close_fn()
            except Exception:
                pass


def _load_llama_cpp_class():
    try:
        from llama_cpp import Llama
    except ImportError as exc:
        raise RuntimeError(
            "llama-cpp-python is required for Llama.cpp local mode.\n\n"
            "Install it with: pip install llama-cpp-python"
        ) from exc
    return Llama


def _get_llama_cpp_model_path(task: str) -> str:
    if task == config.TASK_TITLE:
        title_model_path = LLAMA_CPP_SETTINGS.get("title_model_path", "")
        if title_model_path:
            return title_model_path
    return LLAMA_CPP_SETTINGS.get("chat_model_path", "")


def _validate_llama_cpp_model_path(model_path: str, task: str):
    raw_model_path = str(model_path or "").strip()
    if not raw_model_path:
        task_name = "chat" if task != config.TASK_TITLE else "chat naming"
        raise RuntimeError(f"No Llama.cpp {task_name} model file is configured.")
    normalized_path = os.path.abspath(raw_model_path)
    if not os.path.isfile(normalized_path):
        raise RuntimeError(f"Llama.cpp model file was not found: {normalized_path}")
    if not normalized_path.lower().endswith(".gguf"):
        raise RuntimeError(
            "Llama.cpp local mode expects a GGUF model file.\n\n"
            f"Received: {normalized_path}"
        )


def _llama_cpp_contains_unsupported_media(messages: list) -> str | None:
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type", "")).strip().lower()
            if part_type == "audio_file":
                return "audio"
            if part_type == "image_bytes":
                return "image"
    return None


def _assert_llama_cpp_message_support(messages: list):
    unsupported_kind = _llama_cpp_contains_unsupported_media(messages)
    if not unsupported_kind:
        return

    raise RuntimeError(
        f"Llama.cpp local mode does not currently support {unsupported_kind} attachments in Graphlink.\n\n"
        "Use Ollama or Gemini for multimodal requests, or retry with text-only input."
    )


def _is_qwen_reasoning_model_path(model_path: str | None) -> bool:
    normalized_path = os.path.basename(str(model_path or "")).strip().lower()
    if not normalized_path:
        return False
    return any(token in normalized_path for token in ("qwen", "qwq"))


def _inject_qwen_thinking_instruction(messages: list, enable_thinking: bool) -> list:
    directive = "/think" if enable_thinking else "/no_think"
    processed_messages = [dict(message) for message in messages]

    for message in processed_messages:
        if message.get("role") != "system":
            continue

        current_content = str(message.get("content") or "").strip()
        lowered_content = current_content.lower()
        if "/think" in lowered_content or "/no_think" in lowered_content:
            return processed_messages

        message["content"] = f"{directive}\n{current_content}" if current_content else directive
        return processed_messages

    return [{"role": "system", "content": directive}, *processed_messages]


def _prepare_llama_cpp_messages(messages: list, task: str) -> list:
    normalized_messages = [dict(message) for message in messages]
    if task == config.TASK_CHAT and _is_qwen_reasoning_model_path(_get_llama_cpp_model_path(task)):
        enable_thinking = str(LLAMA_CPP_SETTINGS.get("reasoning_mode", "Quick")).strip().lower() == "thinking"
        normalized_messages = _inject_qwen_thinking_instruction(normalized_messages, enable_thinking)

    processed_messages = []
    for msg in normalized_messages:
        content = msg.get("content")
        if isinstance(content, list):
            text_parts = []
            for part in content:
                if not isinstance(part, dict):
                    text_parts.append(str(part))
                    continue
                if part.get("type") == "text":
                    text_parts.append(str(part.get("text", "")))
            processed_messages.append(
                {
                    "role": msg["role"],
                    "content": "\n".join(part for part in text_parts if part),
                }
            )
        else:
            processed_messages.append(
                {
                    "role": msg["role"],
                    "content": str(content or ""),
                }
            )
    return processed_messages


def _prepare_llama_cpp_kwargs(kwargs: dict) -> dict:
    prepared = dict(kwargs or {})
    if prepared.pop("format", None) == "json":
        prepared.setdefault("response_format", {"type": "json_object"})
    prepared.pop("response_mime_type", None)
    enable_thinking = str(LLAMA_CPP_SETTINGS.get("reasoning_mode", "Quick")).strip().lower() == "thinking"
    prepared.setdefault("enable_thinking", enable_thinking)
    chat_template_kwargs = prepared.get("chat_template_kwargs")
    if isinstance(chat_template_kwargs, dict):
        chat_template_kwargs = dict(chat_template_kwargs)
    else:
        chat_template_kwargs = {}
    chat_template_kwargs.setdefault("enable_thinking", enable_thinking)
    prepared["chat_template_kwargs"] = chat_template_kwargs
    return prepared


def _filter_kwargs_for_callable(callable_obj, kwargs: dict) -> dict:
    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return dict(kwargs or {})

    parameters = signature.parameters.values()
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters):
        return dict(kwargs or {})

    allowed_names = {
        parameter.name
        for parameter in parameters
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }
    return {
        key: value
        for key, value in (kwargs or {}).items()
        if key in allowed_names
    }


def _configure_llama_cpp_chat_handler(client):
    try:
        import llama_cpp.llama_chat_format as llama_chat_format
    except Exception:
        return

    base_handler = getattr(client, "_graphite_base_chat_handler", None)
    if base_handler is None:
        configured_handler = getattr(client, "chat_handler", None)
        if configured_handler is not None and not getattr(configured_handler, "_graphite_wrapped_handler", False):
            base_handler = configured_handler
        else:
            chat_handlers = getattr(client, "_chat_handlers", {}) or {}
            chat_format_name = getattr(client, "chat_format", None)
            if chat_format_name and chat_format_name in chat_handlers:
                base_handler = chat_handlers[chat_format_name]
            elif chat_format_name:
                try:
                    base_handler = llama_chat_format.get_chat_completion_handler(chat_format_name)
                except Exception:
                    base_handler = None

    if base_handler is None:
        return

    enable_thinking = str(LLAMA_CPP_SETTINGS.get("reasoning_mode", "Quick")).strip().lower() == "thinking"
    current_flag = getattr(client, "_graphite_enable_thinking", None)
    if current_flag == enable_thinking and getattr(getattr(client, "chat_handler", None), "_graphite_wrapped_handler", False):
        return

    def graphite_chat_handler(**call_kwargs):
        if "enable_thinking" not in call_kwargs:
            call_kwargs["enable_thinking"] = getattr(client, "_graphite_enable_thinking", False)
        return base_handler(**call_kwargs)

    graphite_chat_handler._graphite_wrapped_handler = True
    client._graphite_base_chat_handler = base_handler
    client._graphite_enable_thinking = enable_thinking
    client.chat_handler = graphite_chat_handler


def _flatten_llama_cpp_text(value) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        return value.strip()

    if isinstance(value, list):
        text_parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text_candidate = (
                    item.get("text")
                    or item.get("content")
                    or item.get("value")
                )
                flattened = _flatten_llama_cpp_text(text_candidate)
            else:
                flattened = _flatten_llama_cpp_text(item)

            if flattened:
                text_parts.append(flattened)
        return "\n".join(text_parts).strip()

    if isinstance(value, dict):
        for key in ("text", "content", "value", "reasoning_content", "reasoning", "thinking", "analysis", "message"):
            flattened = _flatten_llama_cpp_text(value.get(key))
            if flattened:
                return flattened
        return ""

    return str(value).strip()


def _extract_llama_cpp_text(response) -> str:
    choices = _extract_response_field(response, "choices", [])
    if not choices:
        raise RuntimeError("Llama.cpp returned no completion choices.")

    first_choice = choices[0]
    message = _extract_response_field(first_choice, "message", {})
    content = _extract_response_field(message, "content")
    answer_parts: list[str] = []
    reasoning_parts: list[str] = []
    answer_seen: set[str] = set()
    reasoning_seen: set[str] = set()

    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                flattened = _flatten_llama_cpp_text(part)
                extracted_reasoning, visible_text = split_reasoning_and_content(flattened)
                _append_unique_text_segment(reasoning_parts, extracted_reasoning, reasoning_seen)
                _append_unique_text_segment(answer_parts, visible_text, answer_seen)
                continue

            part_type = str(part.get("type", "")).strip().lower()
            if part_type in {"thinking", "think", "reasoning", "reasoning_content"}:
                part_text = _flatten_llama_cpp_text(
                    part.get("reasoning_content")
                    or part.get("reasoning")
                    or part.get("thinking")
                    or part.get("text")
                    or part.get("content")
                    or part.get("value")
                )
            else:
                part_text = _flatten_llama_cpp_text(
                    part.get("text")
                    or part.get("content")
                    or part.get("value")
                    or part.get("message")
                )
            if not part_text:
                continue

            if part_type in {"thinking", "think", "reasoning", "reasoning_content"}:
                extracted_reasoning, visible_text = split_reasoning_and_content(part_text)
                _append_unique_text_segment(
                    reasoning_parts,
                    extracted_reasoning or visible_text or part_text,
                    reasoning_seen,
                )
            else:
                extracted_reasoning, visible_text = split_reasoning_and_content(part_text)
                _append_unique_text_segment(reasoning_parts, extracted_reasoning, reasoning_seen)
                _append_unique_text_segment(answer_parts, visible_text, answer_seen)
    else:
        flattened_content = _flatten_llama_cpp_text(content)
        if flattened_content:
            extracted_reasoning, visible_text = split_reasoning_and_content(flattened_content)
            _append_unique_text_segment(reasoning_parts, extracted_reasoning, reasoning_seen)
            _append_unique_text_segment(answer_parts, visible_text, answer_seen)

    for reasoning_candidate in (
        _extract_response_field(message, "thinking"),
        _extract_response_field(message, "reasoning"),
        _extract_response_field(message, "reasoning_content"),
        _extract_response_field(first_choice, "thinking"),
        _extract_response_field(first_choice, "reasoning"),
        _extract_response_field(first_choice, "reasoning_content"),
    ):
        flattened = _flatten_llama_cpp_text(reasoning_candidate)
        if flattened:
            extracted_reasoning, visible_text = split_reasoning_and_content(flattened)
            _append_unique_text_segment(
                reasoning_parts,
                extracted_reasoning or visible_text or flattened,
                reasoning_seen,
            )

    for answer_candidate in (
        _extract_response_field(message, "text"),
        _extract_response_field(message, "response"),
        _extract_response_field(first_choice, "text"),
        _extract_response_field(first_choice, "response"),
    ):
        flattened = _flatten_llama_cpp_text(answer_candidate)
        if flattened:
            extracted_reasoning, visible_text = split_reasoning_and_content(flattened)
            _append_unique_text_segment(reasoning_parts, extracted_reasoning, reasoning_seen)
            _append_unique_text_segment(answer_parts, visible_text, answer_seen)

    answer_text = "\n\n".join(part for part in answer_parts if part).strip()
    reasoning_text = "\n\n".join(part for part in reasoning_parts if part).strip()
    return _compose_reasoned_response(answer_text, reasoning_text, "Llama.cpp")


def _get_llama_cpp_client(task: str):
    model_path = _get_llama_cpp_model_path(task)
    _validate_llama_cpp_model_path(model_path, task)

    normalized_path = os.path.abspath(model_path)
    resolved_n_threads = _resolve_llama_cpp_thread_count(
        int(LLAMA_CPP_SETTINGS.get("n_threads", 0) or 0)
    )
    cache_key = (
        normalized_path,
        LLAMA_CPP_SETTINGS.get("chat_format", ""),
        int(LLAMA_CPP_SETTINGS.get("n_ctx", 4096) or 4096),
        int(LLAMA_CPP_SETTINGS.get("n_gpu_layers", 0) or 0),
        resolved_n_threads,
    )

    with _LLAMA_CPP_CLIENT_LOCK:
        cached_client = _LLAMA_CPP_CLIENT_CACHE.get(cache_key)
        if cached_client is not None:
            _configure_llama_cpp_chat_handler(cached_client)
            return cached_client

        Llama = _load_llama_cpp_class()
        client_kwargs = {
            "model_path": normalized_path,
            "n_ctx": cache_key[2],
            "n_gpu_layers": cache_key[3],
            "verbose": False,
        }
        if cache_key[1]:
            client_kwargs["chat_format"] = cache_key[1]
        client_kwargs["n_threads"] = cache_key[4]

        try:
            client = Llama(**client_kwargs)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load the Llama.cpp model '{normalized_path}': {exc}"
            ) from exc

        _configure_llama_cpp_chat_handler(client)
        _LLAMA_CPP_CLIENT_CACHE[cache_key] = client
        return client


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


def _raise_if_cancelled(cancel_event=None):
    if cancel_event is not None and getattr(cancel_event, "is_set", None) and cancel_event.is_set():
        raise RequestCancelledError("Request cancelled.")


def _gemini_headers(api_key: str, extra_headers: dict | None = None) -> dict:
    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    return headers


def _gemini_post_json(endpoint: str, body: dict, timeout: int = 120, cancel_event=None) -> dict:
    _raise_if_cancelled(cancel_event)
    api_key = _get_gemini_api_key()
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers=_gemini_headers(api_key),
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        _raise_if_cancelled(cancel_event)
        return payload
    except urllib.error.HTTPError as exc:
        error_payload = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(error_payload)
            message = parsed.get("error", {}).get("message") or error_payload
        except json.JSONDecodeError:
            message = error_payload
        raise RuntimeError(message) from exc


def _gemini_upload_file(
    file_path: str,
    mime_type: str,
    display_name: str | None = None,
    cancel_event=None,
) -> dict:
    _raise_if_cancelled(cancel_event)
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
        _raise_if_cancelled(cancel_event)
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
        _raise_if_cancelled(cancel_event)
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
                _raise_if_cancelled(cancel_event)
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


def _gemini_part_from_content(part: dict, uploaded_files: list, cancel_event=None) -> dict | None:
    _raise_if_cancelled(cancel_event)
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
        upload_info = _gemini_upload_file(
            audio_path,
            mime_type,
            part.get("name"),
            cancel_event=cancel_event,
        )
        uploaded_files.append(upload_info.get("name"))
        return {
            "file_data": {
                "mime_type": upload_info["mime_type"],
                "file_uri": upload_info["uri"],
            }
        }

    return None


def _prepare_gemini_contents(messages: list, cancel_event=None) -> tuple[str | None, list, list]:
    system_prompt = None
    contents = []
    uploaded_files = []

    for msg in messages:
        _raise_if_cancelled(cancel_event)
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
                gemini_part = _gemini_part_from_content(part, uploaded_files, cancel_event=cancel_event)
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
    cancel_event = kwargs.pop("cancellation_event", None)

    try:
        _raise_if_cancelled(cancel_event)
        if not USE_API_MODE:
            if LOCAL_PROVIDER_TYPE == config.LOCAL_PROVIDER_OLLAMA:
                model = config.OLLAMA_MODELS.get(task)
                if not model:
                    raise ValueError(f"No Ollama model configured for task: {task}")

                _assert_ollama_audio_support(model, messages)
                ollama_messages = _prepare_ollama_messages(messages)

                ollama_kwargs = kwargs.copy()
                if task == config.TASK_CHAT and ("qwen3" in model.lower() or "deepseek" in model.lower()):
                    ollama_kwargs["think"] = True

                response = ollama.chat(model=model, messages=ollama_messages, **ollama_kwargs)
                _raise_if_cancelled(cancel_event)

                raw_response_content = response["message"].get("content", "")
                embedded_reasoning, visible_response_content = split_reasoning_and_content(raw_response_content)
                reasoning_parts: list[str] = []
                reasoning_seen: set[str] = set()
                _append_unique_text_segment(reasoning_parts, response["message"].get("thinking"), reasoning_seen)
                _append_unique_text_segment(reasoning_parts, embedded_reasoning, reasoning_seen)
                full_response_content = _compose_reasoned_response(
                    visible_response_content,
                    "\n\n".join(reasoning_parts).strip(),
                    "Ollama",
                )

                return {
                    "message": {
                        "content": full_response_content,
                        "role": "assistant",
                    }
                }

            if LOCAL_PROVIDER_TYPE == config.LOCAL_PROVIDER_LLAMACPP:
                _assert_llama_cpp_message_support(messages)
                llama_messages = _prepare_llama_cpp_messages(messages, task)
                client = _get_llama_cpp_client(task)
                llama_kwargs = _filter_kwargs_for_callable(
                    client.create_chat_completion,
                    _prepare_llama_cpp_kwargs(kwargs),
                )
                response = client.create_chat_completion(messages=llama_messages, **llama_kwargs)
                _raise_if_cancelled(cancel_event)
                return {
                    "message": {
                        "content": _extract_llama_cpp_text(response),
                        "role": "assistant",
                    }
                }

            raise RuntimeError(f"Unsupported local provider: {LOCAL_PROVIDER_TYPE}")

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

        if API_PROVIDER_TYPE == config.API_PROVIDER_OPENAI:
            response = API_CLIENT.chat.completions.create(
                model=api_model,
                messages=messages,
                **kwargs,
            )
            _raise_if_cancelled(cancel_event)
            return {
                "message": {
                    "content": response.choices[0].message.content,
                    "role": "assistant",
                }
            }

        if API_PROVIDER_TYPE == config.API_PROVIDER_GEMINI:
            system_prompt, gemini_contents, uploaded_files = _prepare_gemini_contents(
                messages,
                cancel_event=cancel_event,
            )
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
                    cancel_event=cancel_event,
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

    except Exception as exc:
        if isinstance(exc, RequestCancelledError):
            raise

        error_str = str(exc).lower()

        if "timed out" in error_str or "timeout" in error_str:
            if _message_contains_audio(messages):
                raise TimeoutError(
                    "The request timed out while processing audio.\n\n"
                    "Please try again. If this keeps happening, use a shorter clip or switch to an audio-capable Gemini or Ollama model."
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

        if _message_contains_audio(messages):
            audio_error_fragments = (
                "audio input",
                "audio support",
                "unsupported audio",
                "input_audio",
                "modality",
                "capabilit",
                "transcription",
                "decode audio",
            )
            if any(fragment in error_str for fragment in audio_error_fragments):
                raise RuntimeError(
                    f"{exc}\n\n"
                    "Please try again with an audio-capable model, or retry after confirming the file opens correctly."
                ) from exc

        raise


def initialize_api(provider: str, api_key: str, base_url: str = None):
    global USE_API_MODE, API_PROVIDER_TYPE, API_CLIENT, API_KEY, API_BASE_URL

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
            else:
                raise RuntimeError("OpenAI-compatible API key not configured. Open Settings and save your API key.")

        client = OpenAI(api_key=api_key, base_url=base_url)

    elif provider == config.API_PROVIDER_GEMINI:
        if not (api_key or os.environ.get("GRAPHITE_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")):
            raise RuntimeError("Gemini API key not configured. Open Settings and save your Gemini API key.")
        client = {"provider": config.API_PROVIDER_GEMINI}
    else:
        raise ValueError(f"Unknown API provider: {provider}")

    USE_API_MODE = True
    API_PROVIDER_TYPE = provider
    API_CLIENT = client
    API_KEY = api_key
    API_BASE_URL = base_url
    return API_CLIENT


def initialize_local_provider(
    provider: str,
    settings: dict | None = None,
    *,
    preload_model: bool = False,
):
    global USE_API_MODE, LOCAL_PROVIDER_TYPE, API_PROVIDER_TYPE, API_CLIENT, API_KEY, API_BASE_URL, LLAMA_CPP_SETTINGS

    if provider == config.LOCAL_PROVIDER_OLLAMA:
        normalized_settings = _normalize_llama_cpp_settings()
        USE_API_MODE = False
        LOCAL_PROVIDER_TYPE = provider
        API_PROVIDER_TYPE = None
        API_CLIENT = None
        API_KEY = None
        API_BASE_URL = None
        LLAMA_CPP_SETTINGS = normalized_settings
        return {"provider": provider}

    if provider == config.LOCAL_PROVIDER_LLAMACPP:
        normalized_settings = _normalize_llama_cpp_settings(settings)
        _validate_llama_cpp_model_path(
            normalized_settings.get("chat_model_path"),
            config.TASK_CHAT,
        )
        if normalized_settings.get("title_model_path"):
            _validate_llama_cpp_model_path(normalized_settings["title_model_path"], config.TASK_TITLE)

        previous_state = (
            USE_API_MODE,
            LOCAL_PROVIDER_TYPE,
            API_PROVIDER_TYPE,
            API_CLIENT,
            API_KEY,
            API_BASE_URL,
            LLAMA_CPP_SETTINGS,
        )

        try:
            USE_API_MODE = False
            LOCAL_PROVIDER_TYPE = provider
            API_PROVIDER_TYPE = None
            API_CLIENT = None
            API_KEY = None
            API_BASE_URL = None
            LLAMA_CPP_SETTINGS = normalized_settings
            if preload_model:
                _get_llama_cpp_client(config.TASK_CHAT)
        except Exception:
            (
                USE_API_MODE,
                LOCAL_PROVIDER_TYPE,
                API_PROVIDER_TYPE,
                API_CLIENT,
                API_KEY,
                API_BASE_URL,
                LLAMA_CPP_SETTINGS,
            ) = previous_state
            raise

        return {
            "provider": provider,
            "model_path": _get_llama_cpp_model_path(config.TASK_CHAT),
            "preloaded": bool(preload_model),
        }

    raise ValueError(f"Unknown local provider: {provider}")


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


def is_api_mode() -> bool:
    return USE_API_MODE


def is_local_ollama_mode() -> bool:
    return not USE_API_MODE and LOCAL_PROVIDER_TYPE == config.LOCAL_PROVIDER_OLLAMA


def is_local_llama_cpp_mode() -> bool:
    return not USE_API_MODE and LOCAL_PROVIDER_TYPE == config.LOCAL_PROVIDER_LLAMACPP


def get_mode() -> str:
    if USE_API_MODE:
        return "API"
    return LOCAL_PROVIDER_TYPE


def is_configured() -> bool:
    if USE_API_MODE:
        return API_CLIENT is not None and all(API_MODELS.values())
    if LOCAL_PROVIDER_TYPE == config.LOCAL_PROVIDER_OLLAMA:
        return bool(config.OLLAMA_MODELS.get(config.TASK_CHAT))
    if LOCAL_PROVIDER_TYPE == config.LOCAL_PROVIDER_LLAMACPP:
        return bool(_get_llama_cpp_model_path(config.TASK_CHAT))
    return False
