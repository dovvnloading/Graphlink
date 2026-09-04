"""The llama.cpp runtime: settings normalization, client construction and
caching, message/kwargs preparation, and response-text extraction.

Function bodies are relocated VERBATIM from api_provider.py; only the
patch-seam rewrites below are new. Any name that lives in api_provider's
module namespace (module globals, sibling helpers, constants, and the
`ollama`/`urllib`/`requests` module bindings) is accessed late-bound as
`_mod.<name>` through an in-body deferred `import api_provider as _mod`,
NEVER via a module-top import here: a top-level `from api_provider import X`
would be a circular import (api_provider imports this module at ITS top)
AND would freeze the name at import time, making the test suite's
`monkeypatch.setattr(api_provider, "X", ...)` patches invisible to these
functions. The deferred-import-then-attribute pattern resolves the name on
api_provider at call time, so those patch seams keep working with zero test
changes. api_provider.py re-exports every name below, so every existing
`api_provider.<name>` caller and patch site is unchanged.
"""

from __future__ import annotations

import inspect
import os
import threading


def _normalize_llama_cpp_settings(settings: dict | None = None) -> dict:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    raw_settings = settings or {}
    normalized = {
        "chat_model_path": str(raw_settings.get("chat_model_path", "")).strip(),
        "title_model_path": str(raw_settings.get("title_model_path", "")).strip(),
        "reasoning_level": _mod.normalize_reasoning_level(raw_settings.get("reasoning_level", "high")),
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
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    with _mod._LLAMA_CPP_CLIENT_LOCK:
        clients = list(_mod._LLAMA_CPP_CLIENT_CACHE.values())
        _mod._LLAMA_CPP_CLIENT_CACHE.clear()

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


def _get_llama_cpp_model_path(task: str, settings: dict | None = None) -> str:
    # `settings` lets chat() thread its per-request snapshot through (#9); callers
    # without one (is_configured, title generation) keep reading the live global.
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    active_settings = settings if settings is not None else _mod.LLAMA_CPP_SETTINGS
    if task == _mod.config.TASK_TITLE:
        title_model_path = active_settings.get("title_model_path", "")
        if title_model_path:
            return title_model_path
    return active_settings.get("chat_model_path", "")


def _validate_llama_cpp_model_path(model_path: str | None, task: str):
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    raw_model_path = str(model_path or "").strip()
    if not raw_model_path:
        task_name = "chat" if task != _mod.config.TASK_TITLE else "chat naming"
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
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    unsupported_kind = _mod._llama_cpp_contains_unsupported_media(messages)
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


def _prepare_llama_cpp_messages(messages: list, task: str, settings: dict | None = None) -> list:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    active_settings = settings if settings is not None else _mod.LLAMA_CPP_SETTINGS
    normalized_messages = [dict(message) for message in messages]
    if task == _mod.config.TASK_CHAT and _mod._is_qwen_reasoning_model_path(_mod._get_llama_cpp_model_path(task, active_settings)):
        level = _mod.normalize_reasoning_level(active_settings.get("reasoning_level", "high"))
        normalized_messages = _mod._inject_qwen_thinking_instruction(normalized_messages, level != "off")
        # A second, real axis of control on top of the /think directive -
        # see reasoning_budget_hint's own docstring: Qwen/QwQ's chat
        # template only exposes on/off via /think //no_think, so low/
        # medium/high still need this to differ meaningfully once thinking
        # is enabled.
        if level != "off":
            normalized_messages = _mod._append_system_hint(normalized_messages, _mod.reasoning_budget_hint(level))

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


def _prepare_llama_cpp_kwargs(kwargs: dict, settings: dict | None = None) -> dict:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    active_settings = settings if settings is not None else _mod.LLAMA_CPP_SETTINGS
    prepared = dict(kwargs or {})
    if prepared.pop("format", None) == "json":
        prepared.setdefault("response_format", {"type": "json_object"})
    prepared.pop("response_mime_type", None)
    enable_thinking = _mod.normalize_reasoning_level(active_settings.get("reasoning_level", "high")) != "off"
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


def _configure_llama_cpp_chat_handler(client, settings: dict | None = None):
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    active_settings = settings if settings is not None else _mod.LLAMA_CPP_SETTINGS
    try:
        import llama_cpp.llama_chat_format as llama_chat_format
    except Exception:
        return

    base_handler = getattr(client, "_graphlink_base_chat_handler", None)
    if base_handler is None:
        configured_handler = getattr(client, "chat_handler", None)
        if configured_handler is not None and not getattr(configured_handler, "_graphlink_wrapped_handler", False):
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

    enable_thinking = _mod.normalize_reasoning_level(active_settings.get("reasoning_level", "high")) != "off"
    current_flag = getattr(client, "_graphlink_enable_thinking", None)
    if current_flag == enable_thinking and getattr(getattr(client, "chat_handler", None), "_graphlink_wrapped_handler", False):
        return

    def graphlink_chat_handler(**call_kwargs):
        if "enable_thinking" not in call_kwargs:
            call_kwargs["enable_thinking"] = getattr(client, "_graphlink_enable_thinking", False)
        return base_handler(**call_kwargs)

    # setattr, not plain attribute assignment: the marker is an ad-hoc
    # attribute on a function object, declared on no type. Both readers
    # above already use getattr(..., False), so setattr is the symmetric
    # write side of the same convention.
    setattr(graphlink_chat_handler, "_graphlink_wrapped_handler", True)
    client._graphlink_base_chat_handler = base_handler
    client._graphlink_enable_thinking = enable_thinking
    client.chat_handler = graphlink_chat_handler


def _flatten_llama_cpp_text(value) -> str:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
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
                flattened = _mod._flatten_llama_cpp_text(text_candidate)
            else:
                flattened = _mod._flatten_llama_cpp_text(item)

            if flattened:
                text_parts.append(flattened)
        return "\n".join(text_parts).strip()

    if isinstance(value, dict):
        for key in ("text", "content", "value", "reasoning_content", "reasoning", "thinking", "analysis", "message"):
            flattened = _mod._flatten_llama_cpp_text(value.get(key))
            if flattened:
                return flattened
        return ""

    return str(value).strip()


def _extract_llama_cpp_text(response) -> str:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    choices = _mod._extract_response_field(response, "choices", [])
    if not choices:
        raise RuntimeError("Llama.cpp returned no completion choices.")

    first_choice = choices[0]
    message = _mod._extract_response_field(first_choice, "message", {})
    content = _mod._extract_response_field(message, "content")
    answer_parts: list[str] = []
    reasoning_parts: list[str] = []
    answer_seen: set[str] = set()
    reasoning_seen: set[str] = set()

    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                flattened = _mod._flatten_llama_cpp_text(part)
                extracted_reasoning, visible_text = _mod.split_reasoning_and_content(flattened)
                _mod._append_unique_text_segment(reasoning_parts, extracted_reasoning, reasoning_seen)
                _mod._append_unique_text_segment(answer_parts, visible_text, answer_seen)
                continue

            part_type = str(part.get("type", "")).strip().lower()
            if part_type in {"thinking", "think", "reasoning", "reasoning_content"}:
                part_text = _mod._flatten_llama_cpp_text(
                    part.get("reasoning_content")
                    or part.get("reasoning")
                    or part.get("thinking")
                    or part.get("text")
                    or part.get("content")
                    or part.get("value")
                )
            else:
                part_text = _mod._flatten_llama_cpp_text(
                    part.get("text")
                    or part.get("content")
                    or part.get("value")
                    or part.get("message")
                )
            if not part_text:
                continue

            if part_type in {"thinking", "think", "reasoning", "reasoning_content"}:
                extracted_reasoning, visible_text = _mod.split_reasoning_and_content(part_text)
                _mod._append_unique_text_segment(
                    reasoning_parts,
                    extracted_reasoning or visible_text or part_text,
                    reasoning_seen,
                )
            else:
                extracted_reasoning, visible_text = _mod.split_reasoning_and_content(part_text)
                _mod._append_unique_text_segment(reasoning_parts, extracted_reasoning, reasoning_seen)
                _mod._append_unique_text_segment(answer_parts, visible_text, answer_seen)
    else:
        flattened_content = _mod._flatten_llama_cpp_text(content)
        if flattened_content:
            extracted_reasoning, visible_text = _mod.split_reasoning_and_content(flattened_content)
            _mod._append_unique_text_segment(reasoning_parts, extracted_reasoning, reasoning_seen)
            _mod._append_unique_text_segment(answer_parts, visible_text, answer_seen)

    for reasoning_candidate in (
        _mod._extract_response_field(message, "thinking"),
        _mod._extract_response_field(message, "reasoning"),
        _mod._extract_response_field(message, "reasoning_content"),
        _mod._extract_response_field(first_choice, "thinking"),
        _mod._extract_response_field(first_choice, "reasoning"),
        _mod._extract_response_field(first_choice, "reasoning_content"),
    ):
        flattened = _mod._flatten_llama_cpp_text(reasoning_candidate)
        if flattened:
            extracted_reasoning, visible_text = _mod.split_reasoning_and_content(flattened)
            _mod._append_unique_text_segment(
                reasoning_parts,
                extracted_reasoning or visible_text or flattened,
                reasoning_seen,
            )

    for answer_candidate in (
        _mod._extract_response_field(message, "text"),
        _mod._extract_response_field(message, "response"),
        _mod._extract_response_field(first_choice, "text"),
        _mod._extract_response_field(first_choice, "response"),
    ):
        flattened = _mod._flatten_llama_cpp_text(answer_candidate)
        if flattened:
            extracted_reasoning, visible_text = _mod.split_reasoning_and_content(flattened)
            _mod._append_unique_text_segment(reasoning_parts, extracted_reasoning, reasoning_seen)
            _mod._append_unique_text_segment(answer_parts, visible_text, answer_seen)

    answer_text = "\n\n".join(part for part in answer_parts if part).strip()
    reasoning_text = "\n\n".join(part for part in reasoning_parts if part).strip()
    return _mod._compose_reasoned_response(answer_text, reasoning_text, "Llama.cpp")


def _get_llama_cpp_client(task: str, settings: dict | None = None):
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    active_settings = settings if settings is not None else _mod.LLAMA_CPP_SETTINGS
    model_path = _mod._get_llama_cpp_model_path(task, active_settings)
    _mod._validate_llama_cpp_model_path(model_path, task)

    normalized_path = os.path.abspath(model_path)
    resolved_n_threads = _mod._resolve_llama_cpp_thread_count(
        int(active_settings.get("n_threads", 0) or 0)
    )
    cache_key = (
        normalized_path,
        active_settings.get("chat_format", ""),
        int(active_settings.get("n_ctx", 4096) or 4096),
        int(active_settings.get("n_gpu_layers", 0) or 0),
        resolved_n_threads,
    )

    with _mod._LLAMA_CPP_CLIENT_LOCK:
        cached_client = _mod._LLAMA_CPP_CLIENT_CACHE.get(cache_key)
        if cached_client is not None:
            _mod._configure_llama_cpp_chat_handler(cached_client, active_settings)
            return cached_client

        Llama = _mod._load_llama_cpp_class()
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

        _mod._configure_llama_cpp_chat_handler(client, active_settings)
        # This instance's own inference lock, attached once at construction so
        # every caller that later resolves to this same cached client
        # serializes its generations against the same object - see
        # llama_cpp_inference_lock's own docstring.
        try:
            client._graphlink_inference_lock = threading.Lock()
        except Exception:
            # An exotic Llama build that refuses attribute assignment falls
            # back to the module-wide lock rather than losing the guard.
            pass
        _mod._LLAMA_CPP_CLIENT_CACHE[cache_key] = client
        return client
