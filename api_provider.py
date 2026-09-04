import base64
import inspect
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, NamedTuple, NoReturn
from urllib.parse import urlparse

import ollama
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    # The optional-dependency shape: the name is a module when the import
    # works and None when it does not, which no annotation expresses.
    requests = None  # type: ignore[assignment]
    REQUESTS_AVAILABLE = False

# Qt-removal plan R4.1: import the Qt-free split, not graphlink_config -
# this module must be importable from backend/ without PySide6 loading.
import graphlink_task_config as config
from graphlink_audio import guess_audio_mime_type
from graphlink_model_catalog import FALLBACK_ENABLED_TASKS, ModelDescriptor, ModelRef, ollama_descriptor, sort_descriptors

# Phase 4d god-file decomposition (provider_runtime/): module-top re-exports
# of every relocated name. NOT circular: the submodules never import
# api_provider at module top (they late-bind it in function bodies - see
# their module docstrings), so this direction is safe. These re-exports
# keep every existing `api_provider.<name>` caller and every
# `monkeypatch.setattr(api_provider, ...)` patch seam working unchanged.
# F401 is per-file-ignored for this facade in pyproject.toml (the
# backend/canvas.py precedent).
from provider_runtime.ollama_scan import (
    _normalize_ollama_models_root,
    _iter_existing_ollama_manifest_roots,
    _discover_manifest_roots_in_folder,
    _extract_model_name_from_manifest_path,
    _collect_models_from_manifest_root,
    _list_model_descriptors_from_running_ollama,
    scan_local_ollama_models,
)
from provider_runtime.llama_cpp_scan import (
    _normalize_llama_cpp_scan_root,
    _iter_existing_llama_cpp_scan_roots,
    _collect_gguf_files_from_root,
    scan_local_llama_cpp_models,
)
from provider_runtime.text_parsing import (
    _extract_response_field,
    _append_unique_text_segment,
    _strip_leading_harmony_tokens,
    _split_harmony_reasoning_block,
    _split_closing_only_think_block,
    split_reasoning_and_content,
    ReasoningWithoutAnswerError,
    _compose_reasoned_response,
)
from provider_runtime.reasoning import (
    normalize_reasoning_level,
    _is_ollama_gpt_oss_model,
    _is_ollama_bool_reasoning_model,
    ollama_think_kwarg,
    reasoning_budget_hint,
    _append_system_hint,
    _is_anthropic_effort_model,
    anthropic_reasoning_kwargs,
    _is_gemini_3_model,
    _is_gemini_thinking_capable,
    gemini_thinking_config,
    _is_openai_reasoning_model,
    openai_reasoning_kwargs,
)
from provider_runtime.llama_cpp_runtime import (
    _normalize_llama_cpp_settings,
    _resolve_llama_cpp_thread_count,
    _close_llama_cpp_clients,
    _load_llama_cpp_class,
    _get_llama_cpp_model_path,
    _validate_llama_cpp_model_path,
    _llama_cpp_contains_unsupported_media,
    _assert_llama_cpp_message_support,
    _is_qwen_reasoning_model_path,
    _inject_qwen_thinking_instruction,
    _prepare_llama_cpp_messages,
    _prepare_llama_cpp_kwargs,
    _filter_kwargs_for_callable,
    _configure_llama_cpp_chat_handler,
    _flatten_llama_cpp_text,
    _extract_llama_cpp_text,
    _get_llama_cpp_client,
)
from provider_runtime.media import (
    _read_attachment_bytes,
    _decode_base64_image,
    _extract_openai_image_bytes,
    _guess_image_mime_type,
    _iter_audio_parts,
    _message_contains_audio,
    _stringify_message_content,
)
from provider_runtime.anthropic_transport import (
    _anthropic_headers,
    _anthropic_get_json,
    _attach_http_error_metadata,
    _anthropic_post_json,
    _anthropic_stream_sse,
    _anthropic_content_block_from_part,
    _prepare_anthropic_messages,
    _prepare_anthropic_kwargs,
    _extract_anthropic_text,
    _raise_if_cancelled,
)
from provider_runtime.gemini_transport import (
    _gemini_headers,
    _gemini_post_json,
    _gemini_stream_sse,
    _gemini_upload_file,
    _gemini_delete_file,
    _gemini_part_from_content,
    _prepare_gemini_contents,
    _extract_gemini_text,
    _calculate_gemini_timeout,
    _extract_gemini_image_bytes,
)


# The ollama package ships its module-level helpers (ollama.chat/embed/list/
# show) as bound methods of ONE module-level Client, and that client is built
# with `timeout=None` - i.e. no socket timeout of any kind. Every other
# provider in this file is bounded (the OpenAI/Anthropic SDKs default to
# 600s; the hand-rolled Anthropic/Gemini REST calls pass explicit timeouts),
# so Ollama was the one path that could block a worker thread FOREVER.
#
# Why that is worse than it sounds: a daemon that accepts the TCP connection
# but never answers (a GPU hang or a stuck model load - a real, known Ollama
# failure mode) parks the calling thread on a socket read that never
# returns. Cancellation cannot help, because the cancel event is only polled
# between streamed chunks / after the call returns, and the dispatch watchdog
# (asyncio.wait_for) only stops WAITING - the worker keeps its
# asyncio.to_thread pool slot. Enough of those and the shared executor is
# exhausted and every to_thread in the app - including all settings
# mutations - queues forever: the whole backend soft-hangs.
#
# Configured on the existing shared client rather than by constructing our
# own: every call site keeps calling `ollama.chat(...)` exactly as before,
# and the test suite's monkeypatching of those module attributes keeps
# working. READ_TIMEOUT is deliberately generous and sits ABOVE the dispatch
# watchdog, so this is a backstop against a genuinely wedged daemon, not
# something that can cut a legitimately slow local generation short (for a
# streaming call httpx applies it per-chunk-read, i.e. to the GAP between
# tokens, not to the whole reply).
_OLLAMA_CONNECT_TIMEOUT_SECONDS = 10.0
_OLLAMA_READ_TIMEOUT_SECONDS = 600.0


def _configure_ollama_client_timeout() -> None:
    """Bound the shared ollama client's socket timeouts. Best-effort: this
    reaches into the package's own internals (the bound method's __self__
    and its httpx client), so a future ollama release that reshapes either
    must degrade to the previous no-timeout behavior rather than breaking
    import of this whole module."""
    try:
        import httpx

        client = getattr(ollama.chat, "__self__", None)
        inner = getattr(client, "_client", None)
        if inner is None:
            return
        inner.timeout = httpx.Timeout(
            connect=_OLLAMA_CONNECT_TIMEOUT_SECONDS,
            read=_OLLAMA_READ_TIMEOUT_SECONDS,
            write=60.0,
            pool=_OLLAMA_CONNECT_TIMEOUT_SECONDS,
        )
    except Exception:  # pragma: no cover - defensive, see docstring
        pass


_configure_ollama_client_timeout()


USE_API_MODE = False
API_PROVIDER_TYPE = None
API_CLIENT = None
API_KEY = None
API_BASE_URL = None
LOCAL_PROVIDER_TYPE = config.LOCAL_PROVIDER_OLLAMA

# R8a: reasoning is now a graded level, not a bool "mode" - see
# REASONING_LEVELS' own docstring below for the full mapping story. Local
# providers default to "high" (the old "Thinking" default - local compute
# is free to the user, so thorough-by-default is the right starting
# point); cloud providers default to "off" (extended thinking on a paid
# API is an opt-in cost/latency tradeoff, never a silent default).
OLLAMA_REASONING_LEVEL = "high"
ANTHROPIC_REASONING_LEVEL = "off"
GEMINI_REASONING_LEVEL = "off"
OPENAI_REASONING_LEVEL = "off"
# Per-task model id, or None for a task with nothing configured yet.
API_MODELS: dict[str, str | None] = {
    config.TASK_TITLE: None,
    config.TASK_CHAT: None,
    config.TASK_CHART: None,
    config.TASK_IMAGE_GEN: None,
    config.TASK_WEB_VALIDATE: None,
    config.TASK_WEB_SUMMARIZE: None,
}
# Mixed value types (paths, ints, a chat format string), so an inferred
# dict[str, object] would make every .get() unusable at its use site.
LLAMA_CPP_SETTINGS: dict[str, Any] = {
    "chat_model_path": "",
    "title_model_path": "",
    "reasoning_level": "high",
    "chat_format": "",
    "n_ctx": 4096,
    "n_gpu_layers": 0,
    "n_threads": 0,
}
# Keyed by llama_cpp_runtime's 5-tuple of (path, chat_format, n_ctx,
# n_gpu_layers, n_threads); the values are Llama handles, whose type is
# only importable when llama-cpp-python is installed.
_LLAMA_CPP_CLIENT_CACHE: dict[tuple[str, Any, int, int, int], Any] = {}
_LLAMA_CPP_CLIENT_LOCK = threading.RLock()

# _LLAMA_CPP_CLIENT_LOCK above guards only the CACHE (lookup/creation). It
# does NOT guard INFERENCE, and those are genuinely different critical
# sections: one cached Llama instance is handed to every caller that resolves
# to the same model, and llama-cpp-python's Llama is not thread-safe (it
# carries mutable per-sequence state - n_tokens and the KV cache - and
# releases the GIL inside llama_decode). RunRegistry.is_busy is per-kind
# per-session, so a streaming chat reply and a chart/note generation in the
# same session, or two sessions at once, legitimately run concurrently on
# separate worker threads - and before this lock they could interleave two
# generations on ONE native context, which corrupts output or dies in native
# code and takes the whole backend process with it.
#
# A plain Lock, not an RLock, and deliberately so: a stream() generator holds
# this across its whole consumption and releases it in a finally, which -
# if the caller abandons the generator - runs during garbage collection,
# potentially on a DIFFERENT thread. RLock refuses a release from any thread
# but its owner (RuntimeError); a plain Lock permits it, which is exactly the
# behavior this ownership pattern needs.
_LLAMA_CPP_SHARED_INFERENCE_LOCK = threading.Lock()


def llama_cpp_inference_lock(client):
    """The inference lock for one cached Llama instance - see
    _LLAMA_CPP_SHARED_INFERENCE_LOCK's own comment for why inference needs a
    lock separate from the cache lock.

    Per-CLIENT rather than global, so two different models (separate native
    contexts, no shared state) still run concurrently; only calls sharing one
    instance serialize. A client with no attached lock - a monkeypatched fake
    in a test, or an instance built before this existed - falls back to the
    module-wide lock, which is over-strict but never unsafe."""
    lock = getattr(client, "_graphlink_inference_lock", None)
    return lock if lock is not None else _LLAMA_CPP_SHARED_INFERENCE_LOCK

# Guards the provider globals above. Mutators (initialize_api,
# initialize_local_provider, set_task_model) write under this lock;
# chat()/generate_image() take one consistent snapshot under it at request entry and
# route the whole request through that snapshot. Previously a mode switch during an
# in-flight request could interleave with the request's many separate global reads,
# executing it against a half-swapped provider (e.g. the new provider type with the
# old client/key). The module globals stay authoritative (and monkeypatchable) - the
# snapshot is a per-request view.
_PROVIDER_STATE_LOCK = threading.Lock()


class _ProviderSnapshot(NamedTuple):
    use_api_mode: bool
    api_provider_type: str | None
    api_client: object
    api_key: str | None
    api_base_url: str | None
    local_provider_type: str
    api_models: dict
    llama_cpp_settings: dict
    ollama_reasoning_level: str
    anthropic_reasoning_level: str
    gemini_reasoning_level: str
    openai_reasoning_level: str
    # ADR-006 stage 6.5 (H6): the Ollama per-task model table, copied UNDER
    # the provider lock at snapshot time. chat()/chat_stream() previously
    # read config.OLLAMA_MODELS live AFTER taking their snapshot - a
    # concurrent model-assignment change (or even an app-composer republish,
    # which used to sync the table on the read path) could swap the model
    # between the snapshot and the provider construction. Trailing field
    # with a default so existing positional constructions stay valid.
    ollama_models: dict = {}


def _snapshot_provider_state() -> _ProviderSnapshot:
    with _PROVIDER_STATE_LOCK:
        return _ProviderSnapshot(
            use_api_mode=USE_API_MODE,
            api_provider_type=API_PROVIDER_TYPE,
            api_client=API_CLIENT,
            api_key=API_KEY,
            api_base_url=API_BASE_URL,
            local_provider_type=LOCAL_PROVIDER_TYPE,
            api_models=dict(API_MODELS),
            llama_cpp_settings=dict(LLAMA_CPP_SETTINGS),
            ollama_reasoning_level=OLLAMA_REASONING_LEVEL,
            anthropic_reasoning_level=ANTHROPIC_REASONING_LEVEL,
            gemini_reasoning_level=GEMINI_REASONING_LEVEL,
            openai_reasoning_level=OPENAI_REASONING_LEVEL,
            ollama_models=dict(config.OLLAMA_MODELS),
        )


def sync_ollama_models(settings_manager=None):
    """ADR-006 stage 6.5 (H6): the ONLY sanctioned writer entry point for
    config.OLLAMA_MODELS/CURRENT_MODEL - takes the provider lock so the
    table can never change between a request snapshot's copy of it and the
    rest of that snapshot. config.sync_ollama_task_models itself stays in
    graphlink_task_config (it owns the persistence semantics); this wrapper
    owns the locking, which that module cannot (it would be a circular
    import)."""
    with _PROVIDER_STATE_LOCK:
        return config.sync_ollama_task_models(settings_manager)


def set_current_ollama_model(model: str) -> None:
    """Locked twin of config.set_current_model - see sync_ollama_models."""
    with _PROVIDER_STATE_LOCK:
        config.set_current_model(model)


class ProviderRuntime:
    """ADR-006 stage 6.5: one session's complete provider configuration.

    Instances constructed directly hold their OWN state - two sessions can
    hold different providers/models/reasoning levels concurrently. The
    module-level DEFAULT_RUNTIME below is the one exception: it PROXIES the
    legacy module globals, which stay authoritative (and monkeypatchable -
    the entire existing test suite patches them) for the default session.
    Every mutator and every snapshot goes through _read_all/_write, which is
    the only thing the module-backed subclass overrides.

    A request captures `snapshot()` once at entry and routes the whole
    request through it - the same mid-request-swap immunity the module
    globals' _ProviderSnapshot always provided, now including the Ollama
    model table (H6)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = {
            "use_api_mode": False,
            "api_provider_type": None,
            "api_client": None,
            "api_key": None,
            "api_base_url": None,
            "local_provider_type": config.LOCAL_PROVIDER_OLLAMA,
            "api_models": dict.fromkeys(API_MODELS),
            "llama_cpp_settings": _normalize_llama_cpp_settings(),
            "ollama_reasoning_level": "high",
            "anthropic_reasoning_level": "off",
            "gemini_reasoning_level": "off",
            "openai_reasoning_level": "off",
            "ollama_models": {},
        }

    @classmethod
    def from_snapshot(cls, snapshot: "_ProviderSnapshot") -> "ProviderRuntime":
        """Seed a fresh per-session runtime from an existing configuration -
        how a non-default session starts out matching the default one before
        diverging."""
        runtime = cls()
        runtime._write(**snapshot._asdict())
        return runtime

    # -- state access (the ONLY methods the module-backed subclass overrides)

    def _read_all(self) -> dict:
        with self._lock:
            state = dict(self._state)
            state["api_models"] = dict(state["api_models"])
            state["llama_cpp_settings"] = dict(state["llama_cpp_settings"])
            state["ollama_models"] = dict(state["ollama_models"])
            return state

    def _write(self, **updates) -> dict:
        """Apply updates under the lock; returns the PREVIOUS values of the
        updated keys (initialize_local_provider's llama.cpp rollback needs
        the capture and the write to be one atomic step)."""
        with self._lock:
            previous = {key: self._state[key] for key in updates}
            self._state.update(updates)
            return previous

    def set_task_model(self, task: str, api_model: str) -> None:
        with self._lock:
            if task in self._state["api_models"]:
                self._state["api_models"][task] = api_model

    def set_ollama_models(self, models: dict) -> None:
        """Per-session twin of sync_ollama_models: replaces this runtime's
        own Ollama task table (a plain dict write - per-session runtimes do
        not share config.OLLAMA_MODELS)."""
        self._write(ollama_models=dict(models))

    # -- the shared configuration logic ---------------------------------------

    def snapshot(self) -> _ProviderSnapshot:
        return _ProviderSnapshot(**self._read_all())

    def initialize_api(self, provider: str, api_key: str, base_url: str | None = None):
        client, api_key, base_url = _build_api_client(provider, api_key, base_url)
        self._write(
            use_api_mode=True,
            api_provider_type=provider,
            api_client=client,
            api_key=api_key,
            api_base_url=base_url,
        )
        return client

    def initialize_local_provider(
        self, provider: str, settings: dict | None = None, *, preload_model: bool = False
    ):
        if provider == config.LOCAL_PROVIDER_OLLAMA:
            normalized_settings = _normalize_llama_cpp_settings()
            updates = dict(
                use_api_mode=False,
                local_provider_type=provider,
                api_provider_type=None,
                api_client=None,
                api_key=None,
                api_base_url=None,
                llama_cpp_settings=normalized_settings,
            )
            requested_reasoning = (settings or {}).get("reasoning_level")
            if requested_reasoning:
                updates["ollama_reasoning_level"] = normalize_reasoning_level(requested_reasoning)
            self._write(**updates)
            return {"provider": provider}

        if provider == config.LOCAL_PROVIDER_LLAMACPP:
            normalized_settings = _normalize_llama_cpp_settings(settings)
            _validate_llama_cpp_model_path(
                normalized_settings.get("chat_model_path"),
                config.TASK_CHAT,
            )
            if normalized_settings.get("title_model_path"):
                _validate_llama_cpp_model_path(normalized_settings["title_model_path"], config.TASK_TITLE)

            # ADR-006 stage 6.5 review fix (LOW): preload BEFORE writing state,
            # not write-then-rollback-on-failure. The (potentially slow,
            # multi-GB) preload deliberately happens outside the state lock
            # so it never blocks other requests' snapshots - but a snapshot
            # taken in that window used to see the NEW, not-yet-validated
            # settings, which from_snapshot() can now copy into a per-session
            # runtime that never gets corrected if the preload then fails and
            # this runtime rolls back. Preloading first means a write only
            # ever commits a value already known-good, so there is nothing to
            # roll back and nothing transient for a concurrent snapshot to
            # capture.
            if preload_model:
                _get_llama_cpp_client(config.TASK_CHAT, normalized_settings)

            self._write(
                use_api_mode=False,
                local_provider_type=provider,
                api_provider_type=None,
                api_client=None,
                api_key=None,
                api_base_url=None,
                llama_cpp_settings=normalized_settings,
            )

            return {
                "provider": provider,
                "model_path": _get_llama_cpp_model_path(config.TASK_CHAT, normalized_settings),
                "preloaded": bool(preload_model),
            }

        raise ValueError(f"Unknown local provider: {provider}")

    def set_ollama_reasoning_level(self, level: str) -> None:
        self._write(ollama_reasoning_level=normalize_reasoning_level(level))

    def set_anthropic_reasoning_level(self, level: str) -> None:
        self._write(anthropic_reasoning_level=normalize_reasoning_level(level))

    def set_gemini_reasoning_level(self, level: str) -> None:
        self._write(gemini_reasoning_level=normalize_reasoning_level(level))

    def set_openai_reasoning_level(self, level: str) -> None:
        self._write(openai_reasoning_level=normalize_reasoning_level(level))

    def is_api_mode(self) -> bool:
        return self.snapshot().use_api_mode

    def is_local_ollama_mode(self) -> bool:
        state = self.snapshot()
        return not state.use_api_mode and state.local_provider_type == config.LOCAL_PROVIDER_OLLAMA

    def is_local_llama_cpp_mode(self) -> bool:
        state = self.snapshot()
        return not state.use_api_mode and state.local_provider_type == config.LOCAL_PROVIDER_LLAMACPP

    def is_configured(self) -> bool:
        state = self.snapshot()
        if state.use_api_mode:
            # ADR-006 stage 6.5 (H6): TASK_IMAGE_GEN is deliberately ABSENT
            # for EVERY provider, not just Anthropic - image generation is
            # capability-gated at call time (generate_image's own explicit
            # no-model/no-images-API errors), so a text-only OpenAI-compatible
            # endpoint (vLLM, LM Studio, llama-server) counts as configured.
            required_tasks = (
                config.TASK_TITLE,
                config.TASK_CHAT,
                config.TASK_CHART,
                config.TASK_WEB_VALIDATE,
                config.TASK_WEB_SUMMARIZE,
            )
            return state.api_client is not None and all(
                state.api_models.get(task_key) for task_key in required_tasks
            )
        if state.local_provider_type == config.LOCAL_PROVIDER_OLLAMA:
            return bool(state.ollama_models.get(config.TASK_CHAT))
        if state.local_provider_type == config.LOCAL_PROVIDER_LLAMACPP:
            return bool(_get_llama_cpp_model_path(config.TASK_CHAT, state.llama_cpp_settings))
        return False

    def context_window(self, task: str) -> int:
        """ADR-006 stage 6.6: the active chat model's context window in
        tokens, for `task`'s configured model under THIS runtime's current
        snapshot. Three sources, in honesty order:

        - llama.cpp mode: the configured n_ctx - exact truth, it IS the
          allocated context.
        - Ollama mode: "<arch>.context_length" from a cached ollama.show()
          lookup (_get_ollama_context_window); falls back to the
          conservative default when the server/metadata is unavailable.
        - API mode: the documented per-family table (_KNOWN_CONTEXT_WINDOWS,
          matched by model-id prefix - same name-heuristic posture as
          anthropic_supports_reasoning); unknown ids get the conservative
          default, preserving pre-6.6 behavior for unrecognized endpoints.
        """
        state = self.snapshot()
        if not state.use_api_mode:
            if state.local_provider_type == config.LOCAL_PROVIDER_LLAMACPP:
                try:
                    n_ctx = int(state.llama_cpp_settings.get("n_ctx") or 0)
                except (TypeError, ValueError):
                    n_ctx = 0
                return n_ctx if n_ctx > 0 else _DEFAULT_CONTEXT_WINDOW
            if state.local_provider_type == config.LOCAL_PROVIDER_OLLAMA:
                return _ollama_effective_context_window(state.ollama_models.get(task))
            return _DEFAULT_CONTEXT_WINDOW
        return known_context_window(state.api_models.get(task))


class _ModuleBackedProviderRuntime(ProviderRuntime):
    """The default session's runtime: state lives in the module globals
    above (guarded by _PROVIDER_STATE_LOCK), which stay authoritative and
    monkeypatchable - `patch.object(api_provider, "USE_API_MODE", ...)`
    keeps working exactly as before. Only the two state-access primitives
    differ; every piece of configuration LOGIC is inherited."""

    _GLOBAL_NAMES = {
        "use_api_mode": "USE_API_MODE",
        "api_provider_type": "API_PROVIDER_TYPE",
        "api_client": "API_CLIENT",
        "api_key": "API_KEY",
        "api_base_url": "API_BASE_URL",
        "local_provider_type": "LOCAL_PROVIDER_TYPE",
        "llama_cpp_settings": "LLAMA_CPP_SETTINGS",
        "ollama_reasoning_level": "OLLAMA_REASONING_LEVEL",
        "anthropic_reasoning_level": "ANTHROPIC_REASONING_LEVEL",
        "gemini_reasoning_level": "GEMINI_REASONING_LEVEL",
        "openai_reasoning_level": "OPENAI_REASONING_LEVEL",
    }

    def __init__(self):
        # Deliberately NO super().__init__() - the module IS the state.
        pass

    def _read_all(self) -> dict:
        return _snapshot_provider_state()._asdict()

    def _write(self, **updates) -> dict:
        module_globals = globals()
        with _PROVIDER_STATE_LOCK:
            previous: dict[str, Any] = {}
            for key, value in updates.items():
                if key == "api_models":
                    previous[key] = dict(API_MODELS)
                    API_MODELS.clear()
                    API_MODELS.update(value)
                elif key == "ollama_models":
                    previous[key] = dict(config.OLLAMA_MODELS)
                    config.OLLAMA_MODELS.clear()
                    config.OLLAMA_MODELS.update(value)
                else:
                    previous[key] = module_globals[self._GLOBAL_NAMES[key]]
                    module_globals[self._GLOBAL_NAMES[key]] = value
            return previous

    def set_task_model(self, task: str, api_model: str) -> None:
        with _PROVIDER_STATE_LOCK:
            if task in API_MODELS:
                API_MODELS[task] = api_model


# The default session's runtime - the one every module-level function below
# delegates to, and the one backend/app.py hands to the default session.
DEFAULT_RUNTIME = _ModuleBackedProviderRuntime()

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
ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models?limit=1000"
ANTHROPIC_DEFAULT_MAX_TOKENS = {
    config.TASK_TITLE: 128,
    config.TASK_WEB_VALIDATE: 64,
    config.TASK_CHAT: 4096,
    config.TASK_CHART: 4096,
    config.TASK_WEB_SUMMARIZE: 4096,
}
# model name (lowercased) -> the capability set show() reported, or None
# for a model the daemon does not know. A failed PROBE is deliberately not
# cached - see _get_ollama_capabilities.
_OLLAMA_CAPABILITY_CACHE: dict[str, set[str] | None] = {}
# ADR-006 stage 6.6: context windows extracted from the same `ollama.show()`
# call the capability cache uses, cached under the same key discipline and
# invalidated by the same invalidate_ollama_capability_cache() entry point.
_OLLAMA_CONTEXT_WINDOW_CACHE: dict[str, int | None] = {}

# ADR-006 stage 6.6: API-mode context windows, matched by model-id prefix.
# Same posture as anthropic_supports_reasoning below: a documented
# name-based heuristic, not an API lookup - providers expose no context-
# window endpoint. First matching prefix wins (ordered, longest-first where
# prefixes overlap). Unknown models (including unrecognized OpenAI-
# compatible endpoints) fall back to _DEFAULT_CONTEXT_WINDOW, preserving
# the pre-6.6 8k budget for anything we cannot vouch for.
_KNOWN_CONTEXT_WINDOWS = (
    ("claude-", 200_000),      # claude-3/3.5/4+ families all document 200k
    ("gemini-", 1_048_576),    # gemini-2.0-flash / 2.5-pro/flash / 3* all 1M
    ("gpt-4.1", 1_047_576),    # gpt-4.1 family documents ~1M
    ("gpt-4o", 128_000),
    ("gpt-5", 128_000),        # conservative floor for the family
    ("o1", 128_000),
    ("o3", 128_000),
    ("o4", 128_000),
)
_DEFAULT_CONTEXT_WINDOW = 8_192
# ADR-006 stage 6.8 review fix (HIGH): what we ask the Ollama daemon to
# SERVE (options.num_ctx) when the Modelfile has no explicit num_ctx. The
# trained max from model_info is NOT a safe default to request - a 131k
# num_ctx on llama3.1 allocates a KV cache that OOMs typical consumer GPUs,
# and the daemon's own default (~4k) silently truncates prompts front-first
# instead. 8192 is the KV-cache-safe middle ground; users who want more set
# num_ctx in their Modelfile and we honor it exactly.
_OLLAMA_SERVED_CONTEXT_CAP = 8_192


def _ollama_effective_context_window(model: str | None) -> int:
    """The single source of truth for Ollama-mode context: the served
    window from show() (see _get_ollama_context_window) or the conservative
    default. Used by BOTH the budget side (ProviderRuntime.context_window)
    and the request side (OllamaProvider's options.num_ctx) so the two can
    never disagree."""
    window = _get_ollama_context_window(model)
    return window if window else _DEFAULT_CONTEXT_WINDOW


def known_context_window(model_id: str | None) -> int:
    """Best-known context window for an API-mode model id (see the table
    above). Falls back to the conservative default for unknown ids."""
    normalized = str(model_id or "").strip().lower()
    for prefix, window in _KNOWN_CONTEXT_WINDOWS:
        if normalized.startswith(prefix):
            return window
    return _DEFAULT_CONTEXT_WINDOW


_KNOWN_OLLAMA_AUDIO_MODEL_FAMILIES = {"gemma4"}
_OLLAMA_REASONING_RETRY_BACKOFF_SECONDS = 1.0
# ADR-006 stage 6.8: transient-transport retry, DISTINCT from Ollama's own
# reasoning-content retry above (they must never nest wrongly: the transport
# wrapper wraps the WHOLE provider stream/complete call, Ollama's reasoning
# retries included - a ReasoningWithoutAnswerError never escapes the
# provider, and its exhausted-retries RuntimeError is not transport-shaped,
# so the wrapper never re-runs a content retry).
_TRANSPORT_RETRY_MAX_ATTEMPTS = 2  # retries, so at most 3 total tries
_TRANSPORT_RETRY_BASE_BACKOFF_SECONDS = 1.0
_TRANSPORT_RETRY_MAX_SLEEP_SECONDS = 30.0
_TRANSPORT_RETRY_STATUS_CODES = (429, 500, 502, 503, 504)
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


_GGUF_SCAN_MAX_DIRECTORIES = 50_000
_GGUF_SCAN_MAX_SECONDS = 30


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
        # review-fix: do NOT cache a probe exception (daemon unreachable,
        # a transient connection error, ...) - it is not evidence the
        # model lacks the capability, only that this one probe failed.
        # Caching it as None poisoned every later capability check
        # (ollama_supports_tools et al map None -> False) for the rest of
        # the process, so one bad moment (app started before the daemon,
        # a brief restart) permanently and silently blocked the Builder
        # with a false "model does not support tool calling" error even
        # after the daemon came back - same negative-caching bug class as
        # crawl_etiquette's robots.txt fix. Returning None uncached lets
        # the next call retry the probe fresh.
        return None

    raw_capabilities = _extract_response_field(show_response, "capabilities")
    if raw_capabilities is None:
        _OLLAMA_CAPABILITY_CACHE[cache_key] = None
        return None

    capabilities = _normalize_ollama_capabilities(raw_capabilities)
    _OLLAMA_CAPABILITY_CACHE[cache_key] = capabilities
    return capabilities


def _get_ollama_context_window(model_name: str | None) -> int | None:
    """ADR-006 stage 6.6/6.8 review fix: the context window Ollama will
    actually SERVE for this model, cached like the capability cache above
    (including negative results). Returns None when the server, the model,
    or the metadata is unavailable - callers fall back to
    _DEFAULT_CONTEXT_WINDOW.

    Resolution order (see _extract_context_window_from_show):
    1. An explicit `num_ctx` in the Modelfile parameters - the daemon
       serves exactly that.
    2. Otherwise the TRAINED max ("<arch>.context_length") capped at
       _OLLAMA_SERVED_CONTEXT_CAP - the trained max is NOT what the daemon
       serves by default (it serves its own small default and truncates
       prompts front-first), and requesting the full trained max as num_ctx
       would balloon the KV cache (131k for llama3.1 can OOM a typical GPU).
       The request path passes this same value back as options.num_ctx
       (OllamaProvider), so the budget and the served context are the SAME
       number."""
    normalized_model = (model_name or "").strip()
    if not normalized_model:
        return None

    cache_key = normalized_model.lower()
    if cache_key in _OLLAMA_CONTEXT_WINDOW_CACHE:
        return _OLLAMA_CONTEXT_WINDOW_CACHE[cache_key]

    show_fn = getattr(ollama, "show", None)
    if not callable(show_fn):
        _OLLAMA_CONTEXT_WINDOW_CACHE[cache_key] = None
        return None

    try:
        try:
            show_response = show_fn(normalized_model)
        except TypeError:
            show_response = show_fn(model=normalized_model)
    except Exception:
        _OLLAMA_CONTEXT_WINDOW_CACHE[cache_key] = None
        return None

    window = _extract_context_window_from_show(show_response)
    _OLLAMA_CONTEXT_WINDOW_CACHE[cache_key] = window
    return window


def _extract_context_window_from_show(show_response) -> int | None:
    """The SERVED window from a show() response - explicit Modelfile num_ctx
    when present, else the trained "<arch>.context_length" capped at
    _OLLAMA_SERVED_CONTEXT_CAP (see _get_ollama_context_window's docstring
    for the rationale)."""
    explicit_num_ctx = _extract_modelfile_num_ctx(show_response)
    if explicit_num_ctx:
        return explicit_num_ctx

    model_info = _extract_response_field(show_response, "model_info")
    if model_info is None:
        model_info = _extract_response_field(show_response, "modelinfo")
    if not isinstance(model_info, dict):
        return None

    architecture = str(model_info.get("general.architecture") or "").strip()
    candidates = []
    if architecture:
        candidates.append(f"{architecture}.context_length")
    # Fallback: any "<arch>.context_length" key (architecture field absent
    # or mismatched in some manifests).
    candidates.extend(
        key for key in model_info if str(key).endswith(".context_length")
    )
    for key in candidates:
        raw_value = model_info.get(key)
        if raw_value is None:
            # Was reached by letting int(None) raise into the handler below.
            continue
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return min(value, _OLLAMA_SERVED_CONTEXT_CAP)
    return None


def _extract_modelfile_num_ctx(show_response) -> int | None:
    """An explicit `num_ctx` from show()'s Modelfile `parameters` blob (a
    newline-separated "name value" string on both the REST wire and the SDK
    object). None when absent or unparseable."""
    parameters = _extract_response_field(show_response, "parameters")
    if not isinstance(parameters, str):
        return None
    for line in parameters.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[0] == "num_ctx":
            try:
                value = int(parts[1])
            except (TypeError, ValueError):
                return None
            return value if value > 0 else None
    return None


def invalidate_ollama_capability_cache(model_name: str | None = None):
    """Drop cached `ollama.show()` capability info so it gets re-fetched next use.

    Call this after a model is pulled/updated (see ModelPullWorkerThread) - the cache
    otherwise never expires, so a model re-pulled with different capabilities (e.g.
    gaining audio support in a newer build) would keep answering with whatever it
    reported the first time it was seen this session.

    Args:
        model_name: Invalidate just this model's entry. Omit to clear the whole cache.
    """
    if model_name is None:
        _OLLAMA_CAPABILITY_CACHE.clear()
        _OLLAMA_CONTEXT_WINDOW_CACHE.clear()
        return
    _OLLAMA_CAPABILITY_CACHE.pop(model_name.strip().lower(), None)
    _OLLAMA_CONTEXT_WINDOW_CACHE.pop(model_name.strip().lower(), None)


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
        # ADR-007 stage 7.1: the two tool-turn roles the app's generic
        # message shape adds (ChatRequest's own docstring), translated to
        # Ollama's native shape - checked BEFORE the multimodal-content
        # branch below since neither ever carries list-shaped content.
        # Ollama's ToolCall has no `id` field at all (unlike OpenAI/
        # Anthropic), so the app's own call.id is simply dropped here - it
        # only ever existed to correlate a result back to ITS call, and
        # Ollama's own request/response cycle has no use for it.
        role = msg.get("role")
        if role == "tool":
            processed_messages.append({"role": "tool", "content": str(msg.get("content") or "")})
            continue
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            # Annotated because the value types differ: a checker reading the
            # literal alone infers dict[str, str] from the first two keys and
            # then rejects the list on the third.
            assistant_turn: dict[str, Any] = {
                "role": "assistant",
                "content": str(msg.get("content") or ""),
                "tool_calls": [
                    {"function": {"name": call["name"], "arguments": call["arguments"]}}
                    for call in tool_calls
                ],
            }
            processed_messages.append(assistant_turn)
            continue

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


# R8a: reasoning went from a bool "mode" (Thinking/Quick) to a graded
# level, because every provider this app talks to now has SOME real
# graded mechanism - confirmed against each one's own current docs rather
# than assumed:
#   - OpenAI: reasoning_effort (minimal/low/medium/high/xhigh, model-dependent)
#   - Anthropic: thinking.budget_tokens on older models; a newer `effort`
#     param on Opus 4.7+ that REJECTS budget_tokens outright (400 error) -
#     these are mutually exclusive, not two names for the same thing
#   - Gemini: thinkingBudget (integer, 2.5-series) vs thinkingLevel
#     (string, Gemini 3) - same story, different generations, different shape
#   - Ollama: think is a bool for qwen3/deepseek/qwq, but a REQUIRED string
#     level ("low"/"medium"/"high") for gpt-oss - two different mechanisms
#     on the same provider
# REASONING_LEVELS is the one vocabulary the composer UI and every
# per-provider mapping function below speaks, so a user learns one control
# regardless of which provider/model is active; each function here is
# responsible for translating it into whatever that specific
# provider/model actually accepts, including admitting when a model can't
# fully honor a rung (see the docstrings below for exactly which
# mappings are approximations and why).
REASONING_LEVELS = ("off", "low", "medium", "high")


_ANTHROPIC_EFFORT_MODEL_PATTERN = re.compile(r"opus-4-[7-9]\b|opus-4-\d{2,}\b|opus-[5-9]-|opus-\d{2,}-", re.IGNORECASE)


_ANTHROPIC_BUDGET_TOKENS = {"low": 2000, "medium": 8000, "high": 16000}
# Thinking tokens count against max_tokens, and budget_tokens must stay
# strictly under it (Anthropic rejects a budget >= max_tokens) - this is
# the headroom left for the actual final answer after a request's
# thinking budget, not an arbitrary constant.
_ANTHROPIC_THINKING_HEADROOM_TOKENS = 2048


_GEMINI_THINKING_BUDGET_TOKENS = {"off": 0, "low": 2048, "medium": 8192, "high": 24576}
# Gemini 3's thinkingLevel only defines three rungs (MINIMAL/LOW/HIGH,
# confirmed via Google's own docs) - "medium" and "high" both resolve to
# HIGH there rather than inventing a fourth string value the API doesn't
# define.
_GEMINI_THINKING_LEVEL = {"off": "MINIMAL", "low": "LOW", "medium": "HIGH", "high": "HIGH"}


# This endpoint may point at real OpenAI, or at ANY OpenAI-API-shaped
# server (Groq, a self-hosted vLLM/LM Studio proxy, etc. - see
# backend/settings.py's own comments on why "OpenAI-Compatible" is
# base_url-configurable rather than assumed to be api.openai.com).
# reasoning_effort is only sent when the model name itself suggests a
# real reasoning model - sending it to an arbitrary non-reasoning
# endpoint risks a hard 400 from a strict server, not a silent no-op.
_OPENAI_REASONING_MODEL_PREFIXES = ("o1", "o3", "o4", "gpt-5", "gpt-oss")


# ADR-004 stage 4.4: the canonical env var names per cloud provider - was
# previously 5 independent inline copies of the same "GRAPHLINK_* or
# GRAPHITE_* or <bare>" OR-chain (this file's own _get_gemini_api_key/
# _get_anthropic_api_key and all three branches of initialize_api below),
# a real drift risk (a future renamed/added var updated in one copy but
# not the other four). Consolidated to one source each; every call site
# below now reads through _first_env_api_key. env_api_key_configured (also
# below) is the NEW piece stage 4.4 adds: a presence-only check ("is an
# env var supplying this provider's key right now"), used solely so the
# Settings UI can surface "key provided by environment" (ADR-004 §4) - it
# never returns or logs the key's actual value.
_OPENAI_API_KEY_ENV_VARS = ("GRAPHLINK_OPENAI_API_KEY", "GRAPHITE_OPENAI_API_KEY", "OPENAI_API_KEY")
_ANTHROPIC_API_KEY_ENV_VARS = ("GRAPHLINK_ANTHROPIC_API_KEY", "GRAPHITE_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")
_GEMINI_API_KEY_ENV_VARS = ("GRAPHLINK_GEMINI_API_KEY", "GRAPHITE_GEMINI_API_KEY", "GEMINI_API_KEY")

_PROVIDER_API_KEY_ENV_VARS = {
    config.API_PROVIDER_OPENAI: _OPENAI_API_KEY_ENV_VARS,
    config.API_PROVIDER_ANTHROPIC: _ANTHROPIC_API_KEY_ENV_VARS,
    config.API_PROVIDER_GEMINI: _GEMINI_API_KEY_ENV_VARS,
}


def _first_env_api_key(env_vars: tuple[str, ...]) -> str | None:
    """The first non-empty value among `env_vars`, checked in priority
    order (GRAPHLINK_* wins over GRAPHITE_* wins over the SDK's own bare
    name), or None if none are set. The single implementation every
    provider's key-resolution OR-chain now delegates to."""
    for name in env_vars:
        value = os.environ.get(name)
        if value:
            return value
    return None


def env_api_key_configured(provider: str) -> bool:
    """True if at least one recognized env var for `provider` is currently
    set in this process's environment - a presence check ONLY, never the
    key's value. Note this does NOT mean the env var is necessarily the
    key actually IN USE: a key saved via Settings always wins over the
    environment (every OR-chain below tries the stored/argument key
    first) - callers surfacing this to a user should pair it with the
    stored-key state to show "environment" only when no stored key
    exists to take precedence (see backend/settings.py's own
    _api_key_source)."""
    return _first_env_api_key(_PROVIDER_API_KEY_ENV_VARS.get(provider, ())) is not None


def _get_gemini_api_key(snapshot_key: str | None = None) -> str:
    # `snapshot_key` carries the per-request provider snapshot's key (#9) so a mode
    # switch mid-request can't pair this request with a different provider's key.
    api_key = snapshot_key or API_KEY or _first_env_api_key(_GEMINI_API_KEY_ENV_VARS)
    if not api_key:
        raise RuntimeError("Gemini API key not configured. Open Settings and save your Gemini API key.")
    return api_key


def _get_anthropic_api_key(snapshot_key: str | None = None) -> str:
    api_key = snapshot_key or API_KEY or _first_env_api_key(_ANTHROPIC_API_KEY_ENV_VARS)
    if not api_key:
        raise RuntimeError("Anthropic API key not configured. Open Settings and save your Anthropic API key.")
    return api_key


def _is_local_base_url(base_url: str | None) -> bool:
    if not base_url:
        return False

    parsed = urlparse(base_url)
    hostname = (parsed.hostname or "").lower()
    return hostname in {"localhost", "127.0.0.1", "::1"}


def generate_image(prompt: str, size: str = "1024x1024", *, runtime=None) -> bytes:
    if not prompt or not prompt.strip():
        raise ValueError("Image prompt cannot be empty.")

    # One consistent view of the provider state for the whole request (#9) - a mode
    # switch mid-generation can no longer pair this request's provider branch with a
    # different provider's client/key/model. 6.5: an explicit per-session
    # runtime wins over the default session's module globals.
    state = runtime.snapshot() if runtime is not None else _snapshot_provider_state()

    if not state.use_api_mode:
        raise RuntimeError("Image generation is only available in API Endpoint mode.")

    if not state.api_client:
        raise RuntimeError("API client not initialized. Configure API settings first.")

    if state.api_provider_type == config.API_PROVIDER_ANTHROPIC:
        raise RuntimeError(
            "Image generation is not available for Anthropic Claude in Graphlink yet.\n\n"
            "Switch to Google Gemini or an OpenAI-compatible image endpoint for image generation."
        )

    api_model = state.api_models.get(config.TASK_IMAGE_GEN)
    if not api_model:
        raise RuntimeError(
            "No image generation model configured.\n"
            "Please select one in API Settings."
        )

    # ADR-006 leftover #4: chat()/chat_stream()'s transport-retry wrapper
    # (_complete_with_transport_retry) never covered this function - a
    # single 429/5xx/connection blip failed image generation outright
    # where an equivalent chat blip would have retried. The quota
    # special-casing below is unchanged: a 429/quota-shaped failure is
    # still translated and raised immediately, never retried (retrying an
    # exhausted quota is pointless) - only OTHER transient-shaped failures
    # (5xx, connection errors) now get chat()'s same retry treatment.
    attempt = 0
    while True:
        try:
            if state.api_provider_type == config.API_PROVIDER_OPENAI:
                client = state.api_client
                if not hasattr(client, "images") or not hasattr(client.images, "generate"):
                    raise RuntimeError("The configured OpenAI-compatible client does not expose an images.generate API.")

                response = client.images.generate(
                    model=api_model,
                    prompt=prompt,
                    size=size,
                )
                return _extract_openai_image_bytes(response)

            if state.api_provider_type == config.API_PROVIDER_GEMINI:
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
                    api_key=state.api_key,
                )
                return _extract_gemini_image_bytes(payload)

            raise RuntimeError(f"Unsupported API provider: {state.api_provider_type}")
        except Exception as exc:
            error_str = str(exc).lower()
            if "429" in error_str or "quota" in error_str or "resourceexhausted" in error_str:
                raise RuntimeError(
                    "Image generation quota exceeded.\n\n"
                    "Please use a lower-cost image model or verify billing is enabled for the selected provider."
                ) from exc
            if attempt >= _TRANSPORT_RETRY_MAX_ATTEMPTS or not _is_transient_transport_error(exc):
                raise
            _transport_retry_wait(exc, attempt, cancel_event=None)
            attempt += 1


def _provider_for_model_ref(model_ref: ModelRef, state: "_ProviderSnapshot"):
    """ADR-018 stage 18.1: construct the provider a resolved ModelRef names,
    the alternative to chat()/chat_stream()'s own task-keyed branch-select
    below. Only called when a caller supplies `model_ref` (every pre-18.1
    caller does not, and gets the byte-identical original behavior).

    Both local providers are constructible from the snapshot REGARDLESS of
    the session's active mode (`state.ollama_reasoning_level`/
    `state.llama_cpp_settings` are always populated, not mode-gated) - so a
    node/branch override CAN pin to "my local Ollama model" while the
    session's configured default is a cloud provider, and vice versa; that
    is the realistic mixed local+cloud comparison the ADR's context section
    describes. A CLOUD override is honored only when it names the session's
    OWN currently-configured provider (reusing state.api_client/api_key,
    the exact credentials already snapshotted) - pinning to a DIFFERENT
    cloud provider than the session's active one raises a clear,
    actionable error rather than either silently falling back to the
    session default or reaching for a second provider's stored credentials
    the request snapshot was never given. Genuine simultaneous multi-
    cloud-credential routing is deliberately out of scope for this stage;
    see doc/adr/ADR-018-model-routing.md's own status note.

    llama.cpp overrides are accepted only when the named model_id matches
    one of the two paths already configured in
    state.llama_cpp_settings (chat_model_path/title_model_path) - llama.cpp
    has no "many installed models" catalog the way Ollama does, so free
    model_id selection isn't meaningful there; a mismatch raises the same
    actionable-error posture as an unconfigured cloud override."""

    if model_ref.provider == config.LOCAL_PROVIDER_OLLAMA:
        from backend.providers.ollama_provider import OllamaProvider

        return OllamaProvider(
            model=model_ref.model_id, reasoning_level=state.ollama_reasoning_level,
            context_window=_ollama_effective_context_window(model_ref.model_id),
        )

    if model_ref.provider == config.LOCAL_PROVIDER_LLAMACPP:
        configured_paths = {
            Path(str(state.llama_cpp_settings.get("chat_model_path") or "")).name,
            Path(str(state.llama_cpp_settings.get("title_model_path") or "")).name,
        }
        if model_ref.model_id not in configured_paths:
            raise RuntimeError(
                f"'{model_ref.model_id}' is not one of this session's configured "
                "Llama.cpp model paths. Configure it in Settings > Llama.cpp first."
            )
        from backend.providers.llama_cpp_provider import LlamaCppProvider

        return LlamaCppProvider(settings=state.llama_cpp_settings)

    if model_ref.provider in (config.API_PROVIDER_OPENAI, config.API_PROVIDER_ANTHROPIC, config.API_PROVIDER_GEMINI):
        if not (state.use_api_mode and state.api_provider_type == model_ref.provider and state.api_client):
            raise RuntimeError(
                f"This model is pinned to {model_ref.provider}, but the session's active "
                f"API provider is {state.api_provider_type or 'not configured'}. Switch "
                "API Endpoint in Settings to use it, or change the pinned model."
            )
        if model_ref.provider == config.API_PROVIDER_OPENAI:
            from backend.providers.openai_provider import OpenAIProvider

            return OpenAIProvider(
                client=state.api_client, model=model_ref.model_id,
                reasoning_level=state.openai_reasoning_level,
            )
        if model_ref.provider == config.API_PROVIDER_ANTHROPIC:
            from backend.providers.anthropic_provider import AnthropicProvider

            return AnthropicProvider(
                client=state.api_client, api_key=state.api_key or "", model=model_ref.model_id,
                reasoning_level=state.anthropic_reasoning_level,
            )
        from backend.providers.gemini_provider import GeminiProvider

        return GeminiProvider(
            api_key=state.api_key or "", model=model_ref.model_id,
            reasoning_level=state.gemini_reasoning_level,
        )

    raise RuntimeError(f"Unknown model provider: {model_ref.provider!r}")


def _auto_fallback_model_ref(
    task: str, settings_manager, state: "_ProviderSnapshot", *, exclude_provider: str | None = None,
    extra_required: "tuple[str, ...]" = (),
) -> ModelRef | None:
    """ADR-018 stage 18.4: the auto rung of the resolution chain, tried by
    chat()/chat_stream() ONLY at the exact point they are about to raise
    "no model configured" - an explicit task assignment (the common case)
    is never second-guessed, so an already-working setup dispatches
    byte-identically to before this stage.

    Reuses _provider_for_model_ref's own single-live-cloud-credential
    posture: the catalog is filtered to what THIS session can actually
    dispatch right now (both local providers always constructible; a cloud
    provider only when it is the session's live credentialed one) BEFORE a
    policy ever picks from it - unified_catalog() otherwise spans every
    provider with a cached catalog, including ones this session has no
    live client for, and choose_auto_model_ref must never hand back a ref
    _provider_for_model_ref would then reject.

    `exclude_provider` (ADR-018 stage 18.5): reused by the fallback-on-
    failure path below to rule out the provider that just failed - a
    fallback that could re-pick the SAME broken provider would not be a
    fallback at all.

    `extra_required` (ADR-008): capabilities the CALL demands beyond the
    task's own TASK_REQUIREMENTS - chat_turn_with_tools passes ("tools",)
    so the auto rung never hands a tool-calling turn a model that cannot
    call tools. Best-effort at this layer (catalog entries with unknown
    capabilities pass permissively - graphlink_model_catalog's own
    matches_capabilities contract); the authoritative gate stays the
    constructed provider's own capabilities check at the call site."""
    if settings_manager is None:
        return None
    from graphlink_model_catalog import TASK_REQUIREMENTS, choose_auto_model_ref, unified_catalog
    from backend.token_counter import price_per_mtok

    catalog = [
        descriptor
        for descriptor in unified_catalog(
            settings_manager,
            price_lookup=lambda provider, model_id: price_per_mtok(
                provider, model_id, overrides=settings_manager.get_pricing_overrides(),
            ),
        )
        if descriptor.provider != exclude_provider
        and (
            descriptor.provider in (config.LOCAL_PROVIDER_OLLAMA, config.LOCAL_PROVIDER_LLAMACPP)
            or (state.use_api_mode and descriptor.provider == state.api_provider_type)
        )
    ]
    policy = settings_manager.get_auto_model_policy()
    required = tuple(TASK_REQUIREMENTS.get(task, ())) + tuple(extra_required)
    return choose_auto_model_ref(catalog, required, policy=policy)


def _fallback_model_ref_on_failure(
    task: str, exc: Exception, model_ref: "ModelRef | None", settings_manager, state: "_ProviderSnapshot",
) -> ModelRef | None:
    """ADR-018 stage 18.5: called from chat()/chat_stream()'s OUTER wrapper
    after the primary attempt (task-keyed lookup, or an explicit model_ref
    override) has raised - never from inside _chat_dispatch/
    _chat_stream_dispatch itself, which stay byte-identical to pre-18.5
    behavior. Returns None (no fallback) unless ALL of:

    - the task opts in (graphlink_model_catalog.FALLBACK_ENABLED_TASKS -
      "off by default for correctness-sensitive tasks, on by default for
      naming/triage", per the ADR's own decision #4)
    - a settings_manager was supplied (same precondition as the 18.4 auto
      rung - no catalog to fall back into otherwise)
    - the failure is the SAME "retryable/unavailable" shape ADR-006
      section 6 already classifies (_is_transient_transport_error) -
      never a cancellation, never a content/validation error a different
      model would fail identically at."""
    if settings_manager is None or task not in FALLBACK_ENABLED_TASKS or not _is_transient_transport_error(exc):
        return None
    failed_provider = model_ref.provider if model_ref is not None else (
        state.api_provider_type if state.use_api_mode else state.local_provider_type
    )
    return _auto_fallback_model_ref(task, settings_manager, state, exclude_provider=failed_provider)


def chat(task: str, messages: list, **kwargs) -> dict:
    """ADR-018 stage 18.5: the fallback-chain outer wrapper around
    _chat_dispatch (this function's entire pre-18.5 body, unchanged). The
    primary attempt always dispatches exactly as before; only on a
    _fallback_model_ref_on_failure-approved failure does a SECOND attempt
    fire, against a different provider, with `on_fallback` (additive,
    popped here so it never reaches _chat_dispatch/ChatRequest.extra_kwargs)
    invoked first so the caller can surface the substitution - "never a
    silent swap" per the ADR's own decision #4. `runtime`/`settings_manager`
    are peeked (kwargs.get, not pop) purely so this layer can compute the
    same snapshot _chat_dispatch will independently take - the inner call
    still receives its own untouched, unpopped copy of every kwarg."""
    on_fallback = kwargs.pop("on_fallback", None)
    runtime = kwargs.get("runtime")
    settings_manager = kwargs.get("settings_manager")
    try:
        return _chat_dispatch(task, messages, **kwargs)
    except Exception as exc:
        if isinstance(exc, RequestCancelledError):
            raise
        state = runtime.snapshot() if runtime is not None else _snapshot_provider_state()
        fallback_ref = _fallback_model_ref_on_failure(task, exc, kwargs.get("model_ref"), settings_manager, state)
        if fallback_ref is None:
            raise
        if on_fallback is not None:
            failed_provider = (
                kwargs["model_ref"].provider if kwargs.get("model_ref") is not None
                else (state.api_provider_type if state.use_api_mode else state.local_provider_type)
            )
            try:
                on_fallback(failed_provider, fallback_ref, exc)
            except Exception:
                pass  # a broken notification callback must never mask the real fallback result
        fallback_kwargs = dict(kwargs)
        fallback_kwargs["model_ref"] = fallback_ref
        return _chat_dispatch(task, messages, **fallback_kwargs)


def _chat_dispatch(task: str, messages: list, **kwargs) -> dict:
    cancel_event = kwargs.pop("cancellation_event", None)
    # ADR-018 stage 18.1: an explicitly resolved ModelRef, popped BEFORE
    # the remaining kwargs flow into ChatRequest.extra_kwargs - see
    # _provider_for_model_ref's own docstring. None (every pre-18.1 caller)
    # falls through to today's unchanged task-keyed branch-select below.
    model_ref = kwargs.pop("model_ref", None)
    # ADR-006 stage 6.5: an explicit per-session ProviderRuntime, popped
    # BEFORE the remaining kwargs flow into the provider call. None (every
    # pre-6.5 caller) means the default session's module-backed runtime.
    runtime = kwargs.pop("runtime", None)
    # ADR-018 stage 18.4: popped the same way - only used by the auto-
    # fallback rung below, never forwarded to a provider call.
    settings_manager = kwargs.pop("settings_manager", None)
    # ADR-013 stage 13.3: popped the same way and threaded into ChatRequest
    # explicitly below (not left in extra_kwargs) - only backend/
    # structured_output.py's Anthropic native path sets these today (a
    # single forced tool whose input_schema is the caller's JSON schema),
    # but the shape is provider-agnostic should a future caller need it.
    tools = kwargs.pop("tools", ())
    tool_choice = kwargs.pop("tool_choice", None)

    # One consistent view of the provider state for the whole request (#9). Worker
    # threads call chat() while the UI thread can re-run initialize_* at any time;
    # without the snapshot, the request's many separate global reads could interleave
    # with a swap and execute against a half-swapped provider (new provider type with
    # the old client/key, mixed llama.cpp settings, wrong error-message branch, ...).
    state = runtime.snapshot() if runtime is not None else _snapshot_provider_state()

    try:
        _raise_if_cancelled(cancel_event)

        if model_ref is not None:
            # ADR-018 stage 18.1: an already-resolved ModelRef takes
            # absolute precedence over every task-keyed branch below - see
            # _provider_for_model_ref's own docstring for exactly which
            # provider it constructs and why. Only the LOCAL branches
            # report real usage today (see the Ollama branch's own scope
            # comment below); a model_ref-driven local call keeps that.
            from backend.providers.base import CancelToken, ChatRequest

            provider = _provider_for_model_ref(model_ref, state)
            chat_request = ChatRequest(
                task=task, messages=messages, extra_kwargs=kwargs,
                tools=tools, tool_choice=tool_choice, model_ref=model_ref,
            )
            token = CancelToken(cancel_event)
            if model_ref.provider == config.LOCAL_PROVIDER_LLAMACPP:
                # llama.cpp is excluded from transport retry - in-process
                # inference has no transport (mirrors every other branch's
                # own posture, see chat_stream's twin comment).
                content = provider.complete(chat_request, token)
            else:
                content = _complete_with_transport_retry(provider, chat_request, token, cancel_event)
            return {
                "message": {"content": content, "role": "assistant"},
                # Real usage today only for the two branches that ever
                # populate provider.last_usage (Ollama/llama.cpp) - see the
                # unchanged branches below's own scope comment; getattr
                # simply returns None for the three cloud providers here,
                # matching that same documented gap exactly.
                "usage": getattr(provider, "last_usage", None),
            }

        if not state.use_api_mode:
            if state.local_provider_type == config.LOCAL_PROVIDER_OLLAMA:
                # ADR-006 stage 6.5 (H6): read from the SNAPSHOT's copy of
                # the model table, not config.OLLAMA_MODELS live - see
                # _ProviderSnapshot.ollama_models.
                model = state.ollama_models.get(task)
                if not model:
                    auto_ref = _auto_fallback_model_ref(task, settings_manager, state)
                    if auto_ref is not None:
                        # ADR-018 stage 18.5 review fix: settings_manager
                        # re-included (this function popped it into a local
                        # above, and the plain module-level `chat` name below
                        # resolves to the 18.5 fallback wrapper, not back to
                        # this function) - without it, a failure on THIS
                        # auto-picked ref could never trigger a further
                        # fallback attempt, silently defeating 18.5 for the
                        # exact population of requests 18.4's own auto-pick
                        # serves. on_fallback is NOT re-includable here: the
                        # wrapper already popped it before ever calling this
                        # function, so it is simply out of scope at this
                        # point - a fallback retry after THIS recursive hop
                        # still fires, just without a notification.
                        return chat(
                            task, messages, model_ref=auto_ref, settings_manager=settings_manager,
                            cancellation_event=cancel_event, runtime=runtime,
                            tools=tools, tool_choice=tool_choice, **kwargs,
                        )
                    raise ValueError(f"No Ollama model configured for task: {task}")

                # ADR-006 stage 6.1: the Ollama branch routes through the
                # Provider seam. OllamaProvider.complete() is a faithful port
                # of the ~65-line block that used to live inline here (prep,
                # think kwarg, 3-attempt reasoning retry, <think> composition)
                # - see backend/providers/ollama_provider.py's module doc for
                # the preserved-invariant inventory. Imported lazily: the
                # providers package imports this module's helpers at its own
                # top level, so a module-level import here would be circular.
                from backend.providers.base import CancelToken, ChatRequest
                from backend.providers.ollama_provider import OllamaProvider

                provider = OllamaProvider(
                    model=model, reasoning_level=state.ollama_reasoning_level,
                    # 6.8 review fix: serve exactly what we budget.
                    context_window=_ollama_effective_context_window(model),
                )
                # ADR-006 stage 6.8: Ollama is a network server, so its
                # blocking call rides the transient-transport retry too.
                content = _complete_with_transport_retry(
                    provider,
                    ChatRequest(task=task, messages=messages, extra_kwargs=kwargs),
                    CancelToken(cancel_event),
                    cancel_event,
                )
                return {
                    "message": {
                        "content": content,
                        "role": "assistant",
                    },
                    # ADR-006 stage 6.8: real token counts from the blocking
                    # response (see OllamaProvider.complete). SCOPE: only the
                    # local blocking branches surface usage - the API-mode
                    # blocking branches deliberately do not, because the chat
                    # UI streams everywhere since 6.5b and blocking API calls
                    # are non-chat agent tasks the counter doesn't display.
                    "usage": getattr(provider, "last_usage", None),
                }

            if state.local_provider_type == config.LOCAL_PROVIDER_LLAMACPP:
                # ADR-006 stage 6.3: routed through the Provider seam, same
                # lazy-import pattern as the Ollama branch above - see
                # backend/providers/llama_cpp_provider.py for the preserved
                # invariants (media rejection, frozen settings, cached client).
                from backend.providers.base import CancelToken, ChatRequest
                from backend.providers.llama_cpp_provider import LlamaCppProvider

                provider = LlamaCppProvider(settings=state.llama_cpp_settings)
                content = provider.complete(
                    ChatRequest(task=task, messages=messages, extra_kwargs=kwargs),
                    CancelToken(cancel_event),
                )
                return {
                    "message": {
                        "content": content,
                        "role": "assistant",
                    },
                    # ADR-006 stage 6.8: same local-blocking usage surface as
                    # the Ollama branch above (see its scope comment).
                    "usage": getattr(provider, "last_usage", None),
                }

            raise RuntimeError(f"Unsupported local provider: {state.local_provider_type}")

        if not state.api_client:
            raise RuntimeError("API client not initialized. Configure API settings first.")

        api_model = state.api_models.get(task)

        if not api_model:
            auto_ref = _auto_fallback_model_ref(task, settings_manager, state)
            if auto_ref is not None:
                # ADR-018 stage 18.5 review fix: see the Ollama branch's own
                # comment above - settings_manager re-included so a failure
                # on this auto-picked ref can still trigger a further
                # fallback attempt.
                return chat(
                    task, messages, model_ref=auto_ref, settings_manager=settings_manager,
                    cancellation_event=cancel_event, runtime=runtime,
                    tools=tools, tool_choice=tool_choice, **kwargs,
                )
            raise RuntimeError(
                f"No API model configured for task '{task}'.\n"
                "Please configure models in API Settings."
            )

        # ADR-006 stage 6.3: the three API-mode branches route through the
        # Provider seam. Each provider is constructed from THIS request's
        # snapshot (client/key/model/reasoning level), preserving the
        # mid-request-swap immunity the snapshot exists for. OpenAI's port
        # additionally closes C4: image/audio content parts are converted to
        # the OpenAI content-part format instead of being passed through raw.
        from backend.providers.base import CancelToken, ChatRequest

        chat_request = ChatRequest(
            task=task, messages=messages, extra_kwargs=kwargs, tools=tools, tool_choice=tool_choice,
        )
        token = CancelToken(cancel_event)

        if state.api_provider_type == config.API_PROVIDER_OPENAI:
            from backend.providers.openai_provider import OpenAIProvider

            provider = OpenAIProvider(
                client=state.api_client, model=api_model,
                reasoning_level=state.openai_reasoning_level,
            )
            # ADR-006 stage 6.8: transient-transport retry (429/5xx/
            # connection failures; never cancellations).
            content = _complete_with_transport_retry(provider, chat_request, token, cancel_event)
            return {"message": {"content": content, "role": "assistant"}}

        if state.api_provider_type == config.API_PROVIDER_ANTHROPIC:
            from backend.providers.anthropic_provider import AnthropicProvider

            provider = AnthropicProvider(
                client=state.api_client, api_key=state.api_key or "", model=api_model,
                reasoning_level=state.anthropic_reasoning_level,
            )
            # ADR-006 stage 6.8: transient-transport retry (429/5xx/
            # connection failures; never cancellations).
            content = _complete_with_transport_retry(provider, chat_request, token, cancel_event)
            return {"message": {"content": content, "role": "assistant"}}

        if state.api_provider_type == config.API_PROVIDER_GEMINI:
            from backend.providers.gemini_provider import GeminiProvider

            provider = GeminiProvider(
                api_key=state.api_key or "", model=api_model,
                reasoning_level=state.gemini_reasoning_level,
            )
            # ADR-006 stage 6.8: transient-transport retry (429/5xx/
            # connection failures; never cancellations).
            content = _complete_with_transport_retry(provider, chat_request, token, cancel_event)
            return {"message": {"content": content, "role": "assistant"}}

        raise RuntimeError(f"Unsupported API provider: {state.api_provider_type}")

    except Exception as exc:
        _translate_chat_exception(exc, state, messages)


def _is_connection_shaped_error(exc: Exception) -> bool:
    """The same connection-failure string checks _translate_chat_exception
    applies (extracted for the transport-retry predicate, ADR-006 stage
    6.8), plus the ConnectionError type the REST helpers raise directly."""
    if isinstance(exc, ConnectionError):
        return True
    error_str = str(exc).lower()
    return (
        "connection refused" in error_str
        or "connecterror" in error_str
        or "connection error" in error_str
        or "all connection attempts failed" in error_str
    )


def _is_transient_transport_error(exc: Exception) -> bool:
    """ADR-006 stage 6.8: what the transport-retry layer may retry. NEVER a
    cancellation (the user said stop), NEVER ReasoningWithoutAnswerError
    (that is Ollama's own CONTENT retry, fully handled inside the provider -
    the two retry mechanisms must stay distinct). Retryable: an HTTP status
    in _TRANSPORT_RETRY_STATUS_CODES (from the SDKs' native status_code
    attribute or the one _attach_http_error_metadata preserves on the REST
    path), or a connection-shaped transport failure."""
    if isinstance(exc, (RequestCancelledError, ReasoningWithoutAnswerError)):
        return False
    if getattr(exc, "status_code", None) in _TRANSPORT_RETRY_STATUS_CODES:
        return True
    return _is_connection_shaped_error(exc)


def _retry_after_from_exception(exc: Exception) -> float | None:
    """ADR-006 leftover #2: Retry-After only ever survived on the REST
    transport path, via _attach_http_error_metadata's own `.retry_after`
    attribute - an SDK-raised exception (openai.APIStatusError,
    anthropic.APIStatusError, google-genai's errors) never got that
    treatment, so _transport_retry_wait fell back to pure exponential+
    jitter even when the SDK's own exception carried a real Retry-After
    header. Both the openai and anthropic SDKs expose `.response` (an
    httpx.Response) on their status-error classes; read its headers the
    same way the REST path already does. Duck-typed so a different or
    absent shape (google-genai, or a future SDK version) degrades to None
    rather than raising."""
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        return retry_after
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    header_value = headers.get("Retry-After") if headers is not None else None
    if header_value is None:
        return None
    try:
        return float(str(header_value).strip())
    except (TypeError, ValueError):
        return None


def _transport_retry_wait(exc: Exception, attempt: int, cancel_event) -> None:
    """One backoff sleep between transport retries: exponential base with
    ±50% jitter, raised to a server-provided Retry-After when larger, capped
    at _TRANSPORT_RETRY_MAX_SLEEP_SECONDS. Cancellation is honored on BOTH
    sides of the sleep, and (when a cancel event exists) DURING it -
    Event.wait wakes promptly instead of sleeping out the full backoff."""
    _raise_if_cancelled(cancel_event)
    delay = _TRANSPORT_RETRY_BASE_BACKOFF_SECONDS * (2 ** attempt)
    delay *= random.uniform(0.5, 1.5)
    retry_after = _retry_after_from_exception(exc)
    if retry_after:
        delay = max(delay, retry_after)
    delay = min(delay, _TRANSPORT_RETRY_MAX_SLEEP_SECONDS)
    if cancel_event is not None:
        cancel_event.wait(delay)
    else:
        time.sleep(delay)
    _raise_if_cancelled(cancel_event)


def _complete_with_transport_retry(provider, chat_request, token, cancel_event):
    """ADR-006 stage 6.8: wrap ONE provider's whole blocking complete() call
    (its own internal content retries included) in the transient-transport
    retry loop. Used by chat()'s network-backed branches - llama.cpp is
    excluded at the call sites (in-process inference has no transport)."""
    attempt = 0
    while True:
        try:
            return provider.complete(chat_request, token)
        except Exception as exc:
            if attempt >= _TRANSPORT_RETRY_MAX_ATTEMPTS or not _is_transient_transport_error(exc):
                raise
            _transport_retry_wait(exc, attempt, cancel_event)
            attempt += 1


def _translate_chat_exception(exc: Exception, state, messages: list) -> NoReturn:
    """Shared exception-normalization for chat()/chat_stream(): translates raw
    provider/network exceptions into actionable, user-facing messages. Always
    raises - either a translated exception (chained `from exc`) or the
    original `exc` unchanged - never returns normally. Extracted as its own
    function (R4.4) so chat_stream()'s live-streaming branch gets the exact
    same error-translation behavior chat() already has, rather than letting
    raw provider/network exceptions (connection-refused, timeout, quota, ...)
    propagate unfriendly and untranslated on the now-exclusively-streaming
    Composer chat path."""
    if isinstance(exc, RequestCancelledError):
        raise exc

    # ADR-016 stage 16.3: the ONE choke point both chat() and chat_stream()
    # funnel every non-cancel failure through - record it once here rather
    # than at each of the two call sites (backend/diagnostics.py's own
    # module docstring explains why this is process-global, not per-session).
    from backend.diagnostics import record_provider_error

    record_provider_error(
        state.api_provider_type if state.use_api_mode else state.local_provider_type,
        str(exc),
    )

    error_str = str(exc).lower()
    status_code = getattr(exc, "status_code", None)

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
        if state.api_provider_type == config.API_PROVIDER_OPENAI:
            raise RuntimeError(
                "OpenAI-compatible API quota exceeded or rate limited.\n\n"
                "Please verify billing, rate limits, and the selected model for your endpoint."
            ) from exc
        if state.api_provider_type == config.API_PROVIDER_ANTHROPIC:
            raise RuntimeError(
                "Anthropic API quota exceeded or rate limited.\n\n"
                "Please verify billing, rate limits, and the selected Claude model."
            ) from exc
        raise RuntimeError(
            "Google Gemini API Quota Exceeded.\n\n"
            "Note: Google does not offer a free tier for their 'Pro' models. "
            "Please switch your default task models to a 'Flash' model in the API Settings, "
            "or link a billing account in Google AI Studio."
        ) from exc

    if state.use_api_mode and state.api_provider_type == config.API_PROVIDER_ANTHROPIC:
        if status_code in (401, 403) or "authentication" in error_str or "invalid x-api-key" in error_str:
            raise RuntimeError(
                "Anthropic API authentication failed.\n\n"
                "Please verify your Anthropic API key in Settings."
            ) from exc

    if (
        "connection refused" in error_str
        or "connecterror" in error_str
        or "connection error" in error_str
        or "all connection attempts failed" in error_str
    ):
        if not state.use_api_mode:
            raise ConnectionError(
                "Failed to connect to local Ollama server. Please ensure the Ollama app is running and accessible."
            ) from exc
        if state.api_provider_type == config.API_PROVIDER_ANTHROPIC:
            raise ConnectionError(
                "Failed to connect to the Anthropic API. Please verify your network connection and try again.\n\n"
                f"Details: {exc}"
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

    raise exc


def chat_stream(task: str, messages: list, on_chunk: Callable[[str, bool], None], **kwargs) -> dict:
    """ADR-018 stage 18.5: the streaming sibling of chat()'s own fallback-
    chain outer wrapper, around _chat_stream_dispatch (this function's
    entire pre-18.5 body, unchanged, renamed). Same on_fallback/exclude-
    the-failed-provider mechanics as chat() - see its own docstring -
    with one streaming-specific guard: a fallback attempt is legal ONLY
    while `on_chunk` has delivered NOTHING real yet for this request,
    mirroring the transport-retry layer's own "nothing forwarded yet"
    invariant (chat_stream's module docstring) - replaying a stream that
    already showed the user partial text, against a DIFFERENT model, would
    corrupt rather than continue that partial output. A `reset` event
    (mirrored below) already means the caller's own display is meant to be
    empty again, so it re-arms the guard exactly like it re-arms
    chat_stream's own accumulator (backend/agents.py's accumulated["text"]
    handling)."""
    on_fallback = kwargs.pop("on_fallback", None)
    runtime = kwargs.get("runtime")
    settings_manager = kwargs.get("settings_manager")
    delivered = {"any": False}

    def _tracking_on_chunk(delta: str, reset: bool) -> None:
        if reset:
            delivered["any"] = False
        elif delta:
            delivered["any"] = True
        on_chunk(delta, reset)

    try:
        return _chat_stream_dispatch(task, messages, _tracking_on_chunk, **kwargs)
    except Exception as exc:
        if isinstance(exc, RequestCancelledError) or delivered["any"]:
            raise
        state = runtime.snapshot() if runtime is not None else _snapshot_provider_state()
        fallback_ref = _fallback_model_ref_on_failure(task, exc, kwargs.get("model_ref"), settings_manager, state)
        if fallback_ref is None:
            raise
        if on_fallback is not None:
            failed_provider = (
                kwargs["model_ref"].provider if kwargs.get("model_ref") is not None
                else (state.api_provider_type if state.use_api_mode else state.local_provider_type)
            )
            try:
                on_fallback(failed_provider, fallback_ref, exc)
            except Exception:
                pass  # a broken notification callback must never mask the real fallback result
        fallback_kwargs = dict(kwargs)
        fallback_kwargs["model_ref"] = fallback_ref
        return _chat_stream_dispatch(task, messages, on_chunk, **fallback_kwargs)


def _chat_stream_dispatch(task: str, messages: list, on_chunk: Callable[[str, bool], None], **kwargs) -> dict:
    """Streaming sibling of chat() (Qt-removal R4.4: true token streaming).

    ADR-006 stage 6.5b: EVERY provider streams real incremental chunks now -
    the old non-Ollama fallback (one blocking chat() call plus exactly one
    synthetic full-text chunk) is gone; each branch below constructs the same
    provider class chat() does, from the same snapshot credentials, and
    consumes its stream().

    `on_chunk(delta, reset)` is called zero or more times with `reset=False` and
    incremental DELTAS (never cumulative text - streamed content fragments
    must be concatenated, not replaced). It is called once with `("", True)`
    immediately before a reasoning-retry attempt discards the prior attempt's partial
    text.

    Returns the exact same shape chat() returns: {"message": {"content": <full text>,
    "role": "assistant"}}. `**kwargs` behaves identically to chat()'s kwargs (including
    `cancellation_event`).
    """
    cancel_event = kwargs.get("cancellation_event")
    # ADR-018 stage 18.1: same short-circuit as chat() - see
    # _provider_for_model_ref's own docstring. Left in kwargs by kwargs.get
    # above's sibling; popped here since it must never reach
    # ChatRequest.extra_kwargs.
    model_ref = kwargs.pop("model_ref", None)
    # ADR-006 stage 6.5: same per-session runtime resolution as chat().
    runtime = kwargs.pop("runtime", None)
    # ADR-018 stage 18.4: same auto-fallback popping as chat().
    settings_manager = kwargs.pop("settings_manager", None)
    state = runtime.snapshot() if runtime is not None else _snapshot_provider_state()

    try:
        _raise_if_cancelled(cancel_event)
        # Provider construction mirrors chat()'s branches exactly: same lazy
        # imports (the top-level direction would be a genuine import cycle -
        # see the pinning test), same snapshot-credential kwargs. The
        # LAZY-GENERATOR CONTRACT (backend/providers/base.py) makes the
        # placement load-bearing: NOTHING in a provider's stream() body runs
        # until the first next(), so both construction and the whole
        # consuming loop below must sit inside this try for
        # _translate_chat_exception to see request-prep and mid-stream
        # failures alike. (The old short-circuit got translation for free by
        # recursing into chat(); real streaming owns its own.)
        from backend.providers.base import CancelToken, ChatRequest

        if model_ref is not None:
            provider = _provider_for_model_ref(model_ref, state)
        elif not state.use_api_mode:
            if state.local_provider_type == config.LOCAL_PROVIDER_OLLAMA:
                # ADR-006 stage 6.5 (H6): snapshot copy, not a live table
                # read - see chat()'s twin comment.
                model = state.ollama_models.get(task)
                if not model:
                    auto_ref = _auto_fallback_model_ref(task, settings_manager, state)
                    if auto_ref is not None:
                        # ADR-018 stage 18.5 review fix: settings_manager
                        # re-included - see chat()'s own identical fix for
                        # why (this function popped it into a local above;
                        # without re-including it, a failure on THIS
                        # auto-picked ref could never trigger a further
                        # fallback attempt).
                        return chat_stream(
                            task, messages, on_chunk, model_ref=auto_ref,
                            settings_manager=settings_manager, runtime=runtime, **kwargs,
                        )
                    raise ValueError(f"No Ollama model configured for task: {task}")
                from backend.providers.ollama_provider import OllamaProvider

                provider = OllamaProvider(
                    model=model, reasoning_level=state.ollama_reasoning_level,
                    # 6.8 review fix: serve exactly what we budget.
                    context_window=_ollama_effective_context_window(model),
                )
            elif state.local_provider_type == config.LOCAL_PROVIDER_LLAMACPP:
                from backend.providers.llama_cpp_provider import LlamaCppProvider

                provider = LlamaCppProvider(settings=state.llama_cpp_settings)
            else:
                raise RuntimeError(f"Unsupported local provider: {state.local_provider_type}")
        else:
            if not state.api_client:
                raise RuntimeError("API client not initialized. Configure API settings first.")
            api_model = state.api_models.get(task)
            if not api_model:
                auto_ref = _auto_fallback_model_ref(task, settings_manager, state)
                if auto_ref is not None:
                    # ADR-018 stage 18.5 review fix: settings_manager
                    # re-included - see chat()'s own identical fix.
                    return chat_stream(
                        task, messages, on_chunk, model_ref=auto_ref,
                        settings_manager=settings_manager, runtime=runtime, **kwargs,
                    )
                raise RuntimeError(
                    f"No API model configured for task '{task}'.\n"
                    "Please configure models in API Settings."
                )
            if state.api_provider_type == config.API_PROVIDER_OPENAI:
                from backend.providers.openai_provider import OpenAIProvider

                provider = OpenAIProvider(
                    client=state.api_client, model=api_model,
                    reasoning_level=state.openai_reasoning_level,
                )
            elif state.api_provider_type == config.API_PROVIDER_ANTHROPIC:
                from backend.providers.anthropic_provider import AnthropicProvider

                provider = AnthropicProvider(
                    client=state.api_client, api_key=state.api_key or "", model=api_model,
                    reasoning_level=state.anthropic_reasoning_level,
                )
            elif state.api_provider_type == config.API_PROVIDER_GEMINI:
                from backend.providers.gemini_provider import GeminiProvider

                provider = GeminiProvider(
                    api_key=state.api_key or "", model=api_model,
                    reasoning_level=state.gemini_reasoning_level,
                )
            else:
                raise RuntimeError(f"Unsupported API provider: {state.api_provider_type}")

        # The provider yields typed events; this adapter maps them onto the
        # on_chunk(delta, reset) contract: "text" deltas forward
        # incrementally, "reset" becomes on_chunk("", True), "reasoning"
        # deltas are NOT forwarded (a documented invariant - thinking never
        # reaches on_chunk; it only surfaces in the final <think> block where
        # a provider composes one), and "done" carries the full final text
        # this function returns.
        # ADR-006 stage 6.8: transient-transport retry around construct+
        # consume, legal ONLY while nothing has been forwarded to on_chunk
        # yet - once the first text delta is delivered, an error propagates
        # to translation exactly as before (silently replaying a half-
        # delivered stream would corrupt the caller's accumulated text).
        # llama.cpp is excluded: in-process inference has no transport.
        if model_ref is not None:
            transport_retry_allowed = model_ref.provider != config.LOCAL_PROVIDER_LLAMACPP
        else:
            transport_retry_allowed = (
                state.use_api_mode
                or state.local_provider_type == config.LOCAL_PROVIDER_OLLAMA
            )
        attempt = 0
        while True:
            full_response_content = None
            usage = None
            delivered_any = False
            try:
                for event in provider.stream(
                    ChatRequest(task=task, messages=messages, extra_kwargs=kwargs, model_ref=model_ref),
                    CancelToken(cancel_event),
                ):
                    if event.type == "text":
                        delivered_any = True
                        on_chunk(event.text, False)
                    elif event.type == "reset":
                        on_chunk("", True)  # tell the caller: discard the last attempt's partial text
                    elif event.type == "done":
                        full_response_content = event.text
                        # ADR-006 stage 6.8: providers attach normalized usage
                        # to their done event when the server reported counts.
                        usage = getattr(event, "usage", None)
                break
            except Exception as retry_exc:
                if (
                    not transport_retry_allowed
                    or delivered_any
                    or attempt >= _TRANSPORT_RETRY_MAX_ATTEMPTS
                    or not _is_transient_transport_error(retry_exc)
                ):
                    raise
                _transport_retry_wait(retry_exc, attempt, cancel_event)
                attempt += 1

        return {
            "message": {
                "content": full_response_content,
                "role": "assistant",
            },
            # ADR-006 stage 6.8: {"prompt_tokens": ..., "completion_tokens":
            # ...} or None - additive key, every consumer reads ["message"].
            "usage": usage,
        }
    except Exception as exc:
        # Same translation chat() gets - a real connection-refused/timeout/
        # quota failure here must show the same friendly, actionable message
        # as the blocking path, not raw exception text (this is the ONLY
        # code path Composer send uses).
        _translate_chat_exception(exc, state, messages)


def chat_turn_with_tools(task: str, messages: list, tools: tuple = (), **kwargs) -> dict:
    """ADR-008 stage 8.1: ONE model turn that can call tools - the primitive
    the Builder loop alternates with ToolRegistry.invoke().

    This is the layer the ADR-007 recon identified as the exact gap:
    _chat_stream_dispatch's consuming loop silently drops "tool_call"
    events and never sets ChatRequest.tools. This sibling collects them
    instead: it constructs the SAME provider the streaming path would
    (same snapshot credentials, same model_ref precedence, same lazy
    imports), passes `tools` through ChatRequest, and returns

        {"message": {"content": str, "role": "assistant"},
         "tool_calls": [backend.providers.base.ToolCall, ...],   # [] = a plain turn
         "usage": {"prompt_tokens", "completion_tokens"} | None}

    so the caller can invoke each ToolCall via the registry, append the
    {"role": "tool", ...} result messages (the app-neutral shapes each
    provider's own message-prep helper already translates - proven
    end-to-end per provider in backend/tests/test_tool_calling.py), and
    call this again for the next turn. The loop, budgets, and approval
    routing live in backend/builder.py - this function is deliberately
    single-turn and stateless.

    Tools capability is gated HERE, authoritatively: ChatRequest's own
    contract (backend/providers/base.py) says a provider with
    capabilities.tools=False must never receive non-empty tools, and the
    caller is the checker - so a non-tools model (llama.cpp always;
    Ollama per-model) raises an actionable RuntimeError before any
    request is sent, rather than a provider-level surprise mid-build.

    No 18.5 fallback wrapper: builder tasks are correctness-sensitive
    (FALLBACK_ENABLED_TASKS covers naming/triage only), and a mid-build
    silent model swap is exactly what ADR-018 decision #4 rules out.
    Transport retry IS kept (same policy/attempt cap as the streaming
    path) and is unconditionally safe here: nothing is forwarded to any
    on_chunk mid-attempt, so a failed attempt's partial collection is
    discarded wholesale and retried - there is no half-delivered UI state
    to corrupt, the exact hazard that forces the streaming path's
    "nothing forwarded yet" guard."""
    cancel_event = kwargs.get("cancellation_event")
    model_ref = kwargs.pop("model_ref", None)
    runtime = kwargs.pop("runtime", None)
    settings_manager = kwargs.pop("settings_manager", None)
    state = runtime.snapshot() if runtime is not None else _snapshot_provider_state()

    try:
        _raise_if_cancelled(cancel_event)
        from backend.providers.base import CancelToken, ChatRequest

        if model_ref is not None:
            provider = _provider_for_model_ref(model_ref, state)
        elif not state.use_api_mode:
            if state.local_provider_type == config.LOCAL_PROVIDER_OLLAMA:
                model = state.ollama_models.get(task)
                if not model:
                    auto_ref = _auto_fallback_model_ref(
                        task, settings_manager, state,
                        extra_required=("tools",) if tools else (),
                    )
                    if auto_ref is not None:
                        return chat_turn_with_tools(
                            task, messages, tools, model_ref=auto_ref,
                            settings_manager=settings_manager, runtime=runtime, **kwargs,
                        )
                    raise ValueError(f"No Ollama model configured for task: {task}")
                from backend.providers.ollama_provider import OllamaProvider

                provider = OllamaProvider(
                    model=model, reasoning_level=state.ollama_reasoning_level,
                    context_window=_ollama_effective_context_window(model),
                )
            elif state.local_provider_type == config.LOCAL_PROVIDER_LLAMACPP:
                from backend.providers.llama_cpp_provider import LlamaCppProvider

                provider = LlamaCppProvider(settings=state.llama_cpp_settings)
            else:
                raise RuntimeError(f"Unsupported local provider: {state.local_provider_type}")
        else:
            if not state.api_client:
                raise RuntimeError("API client not initialized. Configure API settings first.")
            api_model = state.api_models.get(task)
            if not api_model:
                auto_ref = _auto_fallback_model_ref(
                    task, settings_manager, state,
                    extra_required=("tools",) if tools else (),
                )
                if auto_ref is not None:
                    return chat_turn_with_tools(
                        task, messages, tools, model_ref=auto_ref,
                        settings_manager=settings_manager, runtime=runtime, **kwargs,
                    )
                raise RuntimeError(
                    f"No API model configured for task '{task}'.\n"
                    "Please configure models in API Settings."
                )
            if state.api_provider_type == config.API_PROVIDER_OPENAI:
                from backend.providers.openai_provider import OpenAIProvider

                provider = OpenAIProvider(
                    client=state.api_client, model=api_model,
                    reasoning_level=state.openai_reasoning_level,
                )
            elif state.api_provider_type == config.API_PROVIDER_ANTHROPIC:
                from backend.providers.anthropic_provider import AnthropicProvider

                provider = AnthropicProvider(
                    client=state.api_client, api_key=state.api_key or "", model=api_model,
                    reasoning_level=state.anthropic_reasoning_level,
                )
            elif state.api_provider_type == config.API_PROVIDER_GEMINI:
                from backend.providers.gemini_provider import GeminiProvider

                provider = GeminiProvider(
                    api_key=state.api_key or "", model=api_model,
                    reasoning_level=state.gemini_reasoning_level,
                )
            else:
                raise RuntimeError(f"Unsupported API provider: {state.api_provider_type}")

        if tools and not provider.capabilities.tools:
            model_label = getattr(provider, "model_id", None) or type(provider).__name__
            raise RuntimeError(
                f"The selected model ({model_label}) does not support tool "
                "calling. The Builder needs a tools-capable model - pick one "
                "in the model override or configure a tools-capable chat model."
            )

        if model_ref is not None:
            transport_retry_allowed = model_ref.provider != config.LOCAL_PROVIDER_LLAMACPP
        else:
            transport_retry_allowed = (
                state.use_api_mode
                or state.local_provider_type == config.LOCAL_PROVIDER_OLLAMA
            )
        attempt = 0
        while True:
            full_response_content = None
            usage = None
            tool_calls = []
            try:
                for event in provider.stream(
                    ChatRequest(
                        task=task, messages=messages, extra_kwargs=kwargs,
                        tools=tuple(tools), model_ref=model_ref,
                    ),
                    CancelToken(cancel_event),
                ):
                    if event.type == "tool_call" and event.tool_call is not None:
                        tool_calls.append(event.tool_call)
                    elif event.type == "reset":
                        # A reasoning-retry discarded the prior attempt
                        # wholesale - its collected tool calls go with it,
                        # exactly like the streaming path's partial text.
                        tool_calls = []
                    elif event.type == "done":
                        full_response_content = event.text
                        usage = getattr(event, "usage", None)
                break
            except Exception as retry_exc:
                if (
                    not transport_retry_allowed
                    or attempt >= _TRANSPORT_RETRY_MAX_ATTEMPTS
                    or not _is_transient_transport_error(retry_exc)
                ):
                    raise
                _transport_retry_wait(retry_exc, attempt, cancel_event)
                attempt += 1

        return {
            "message": {
                "content": full_response_content or "",
                "role": "assistant",
            },
            "tool_calls": tool_calls,
            "usage": usage,
        }
    except Exception as exc:
        _translate_chat_exception(exc, state, messages)


def describe_active_model(task: str, runtime: "ProviderRuntime | None" = None) -> tuple[str, str]:
    """ADR-006 stage 6.8: (provider, model) for `task` under the given
    runtime's current snapshot (default session when runtime is None) - the
    provenance pair intents_chat stamps onto reply nodes and hands the token
    counter for cost estimation. Local providers report "ollama" /
    "llama.cpp"; API mode reports the configured provider type string."""
    state = (runtime if runtime is not None else DEFAULT_RUNTIME).snapshot()
    if state.use_api_mode:
        return (state.api_provider_type or "", state.api_models.get(task) or "")
    if state.local_provider_type == config.LOCAL_PROVIDER_LLAMACPP:
        model_path = _get_llama_cpp_model_path(task, state.llama_cpp_settings) or ""
        return ("llama.cpp", Path(model_path).name if model_path else "")
    return ("ollama", state.ollama_models.get(task) or "")


def _build_api_client(provider: str, api_key: str, base_url: str | None = None):
    """The client-construction half of initialize_api, with NO state
    mutation - ADR-006 stage 6.5 splits it out so ProviderRuntime instances
    and the throwaway catalog listing (list_models_for_config) can build a
    client without repointing anything. Returns (client, resolved_key,
    resolved_base_url)."""
    # OpenAI/Anthropic SDK client, or the plain dict the REST fallbacks use.
    client: Any
    if provider == config.API_PROVIDER_OPENAI:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai package required. Install dependencies with: pip install -r requirements.txt"
            ) from exc

        if not base_url:
            base_url = "https://api.openai.com/v1"

        api_key = api_key or _first_env_api_key(_OPENAI_API_KEY_ENV_VARS) or ""
        if not api_key:
            if _is_local_base_url(base_url):
                api_key = "dummy-key-for-local"
            else:
                raise RuntimeError("OpenAI-compatible API key not configured. Open Settings and save your API key.")

        # ADR-006 stage 6.8: max_retries=0 - api_provider owns transport
        # retries now (with Retry-After handling and cancel-aware backoff);
        # leaving the SDK's default 2 would multiply attempts (3 SDK tries
        # per each of our 3 tries).
        try:
            client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0)
        except TypeError as exc:
            if "max_retries" not in str(exc):
                raise
            client = OpenAI(api_key=api_key, base_url=base_url)

    elif provider == config.API_PROVIDER_ANTHROPIC:
        api_key = api_key or _first_env_api_key(_ANTHROPIC_API_KEY_ENV_VARS) or ""
        if not api_key:
            raise RuntimeError("Anthropic API key not configured. Open Settings and save your Anthropic API key.")

        client = None
        try:
            from anthropic import Anthropic
            try:
                # ADR-006 stage 6.8: max_retries=0 for the same
                # no-multiplied-retries reason as the OpenAI client above.
                client = Anthropic(api_key=api_key, max_retries=0)
            except TypeError as exc:
                if "max_retries" in str(exc):
                    client = Anthropic(api_key=api_key)
                elif "unexpected keyword argument 'proxies'" not in str(exc):
                    raise
        except ImportError:
            client = None

        if client is None:
            client = {"provider": config.API_PROVIDER_ANTHROPIC, "transport": "rest"}
        base_url = None

    elif provider == config.API_PROVIDER_GEMINI:
        # Bake the env-resolved key into `api_key` the same way the OpenAI/
        # Anthropic branches above do - it's what ends up in the
        # module-global API_KEY below, which _snapshot_provider_state()
        # freezes into api_key for the whole request. Previously this
        # branch only did a presence CHECK (`if not (api_key or
        # _first_env_api_key(...))`) without ever reassigning `api_key`,
        # so an env-only Gemini key left API_KEY - and every request
        # snapshot's api_key field - as the original empty string. That
        # defeated the snapshot-consistency guarantee _get_gemini_api_key's
        # snapshot_key parameter exists for (see its own comment): the
        # snapshot's api_key OR-branch never fired, silently falling
        # through to a LIVE re-read of API_KEY and then os.environ at
        # actual-call time instead of the value frozen at request entry.
        api_key = api_key or _first_env_api_key(_GEMINI_API_KEY_ENV_VARS) or ""
        if not api_key:
            raise RuntimeError("Gemini API key not configured. Open Settings and save your Gemini API key.")
        client = {"provider": config.API_PROVIDER_GEMINI}
    else:
        raise ValueError(f"Unknown API provider: {provider}")

    return client, api_key, base_url


def initialize_api(provider: str, api_key: str, base_url: str | None = None):
    """Configure the DEFAULT session's runtime (the module globals) for an
    API endpoint - ADR-006 stage 6.5: the logic lives on ProviderRuntime,
    shared with per-session instances; this delegate keeps every existing
    caller and test working unchanged."""
    return DEFAULT_RUNTIME.initialize_api(provider, api_key, base_url)


def initialize_local_provider(
    provider: str,
    settings: dict | None = None,
    *,
    preload_model: bool = False,
):
    """Local-provider twin of initialize_api - same 6.5 delegation."""
    return DEFAULT_RUNTIME.initialize_local_provider(
        provider, settings, preload_model=preload_model
    )


def _list_models(provider_type, client, api_key=None):
    if provider_type == config.API_PROVIDER_OPENAI:
        models = client.models.list()
        return sorted([model.id for model in models.data])
    if provider_type == config.API_PROVIDER_ANTHROPIC:
        payload = _anthropic_get_json(ANTHROPIC_MODELS_URL, api_key=api_key)
        return sorted(
            {
                str(model_info.get("id", "")).strip()
                for model_info in payload.get("data", [])
                if str(model_info.get("id", "")).strip()
            },
            key=str.lower,
        )
    if provider_type == config.API_PROVIDER_GEMINI:
        return GEMINI_MODELS_STATIC
    return []


def get_available_models():
    if not API_CLIENT:
        raise RuntimeError("API client not initialized")

    try:
        return _list_models(API_PROVIDER_TYPE, API_CLIENT, API_KEY)
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch models from endpoint: {exc}") from exc


def list_models_for_config(provider: str, api_key: str, base_url: str | None = None):
    """ADR-006 stage 6.5: catalog listing WITHOUT touching live provider
    state. loadApiModels used to call initialize_api just to refresh a
    Settings dropdown - a read-only catalog fetch silently repointed the
    process's live provider (and with per-session runtimes, would have
    repointed every session's default). Builds a throwaway client instead."""
    client, resolved_key, _base_url = _build_api_client(provider, api_key, base_url)
    try:
        return _list_models(provider, client, resolved_key)
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch models from endpoint: {exc}") from exc


def get_available_model_descriptors() -> list[ModelDescriptor]:
    """Fetch the active provider catalog with stable metadata for the UI."""
    models = get_available_models()
    return sort_descriptors(
        ModelDescriptor(
            model_id=str(model_id).strip(),
            provider=str(API_PROVIDER_TYPE or ""),
            ready=True,
            available=True,
            source="endpoint",
        )
        for model_id in models
        if str(model_id).strip()
    )


# ADR-006 stage 6.5: set_mode/get_mode/get_task_models were dead code with
# zero callers repo-wide and are deleted; the reasoning-level and task-model
# setters below delegate to the default session's runtime (see
# ProviderRuntime), keeping every existing caller and test unchanged.


def set_ollama_reasoning_level(level: str):
    """Update the request snapshot source used by reasoning-capable Ollama models."""
    DEFAULT_RUNTIME.set_ollama_reasoning_level(level)


def set_anthropic_reasoning_level(level: str):
    DEFAULT_RUNTIME.set_anthropic_reasoning_level(level)


def set_gemini_reasoning_level(level: str):
    DEFAULT_RUNTIME.set_gemini_reasoning_level(level)


def set_openai_reasoning_level(level: str):
    DEFAULT_RUNTIME.set_openai_reasoning_level(level)


def set_task_model(task: str, api_model: str):
    DEFAULT_RUNTIME.set_task_model(task, api_model)


def is_api_mode() -> bool:
    return USE_API_MODE


def is_local_ollama_mode() -> bool:
    return not USE_API_MODE and LOCAL_PROVIDER_TYPE == config.LOCAL_PROVIDER_OLLAMA


def is_local_llama_cpp_mode() -> bool:
    return not USE_API_MODE and LOCAL_PROVIDER_TYPE == config.LOCAL_PROVIDER_LLAMACPP


# R8a: public "does this resolved model support reasoning at all" checks -
# backend/composer.py uses these to decide whether to show the reasoning
# control at all (the capability gate), rather than reaching into the
# private _is_*_reasoning_model detection helpers above directly.


def ollama_supports_reasoning(model_name: str) -> bool:
    return _is_ollama_gpt_oss_model(model_name) or _is_ollama_bool_reasoning_model(model_name)


def llama_cpp_supports_reasoning(model_path: str) -> bool:
    return _is_qwen_reasoning_model_path(model_path)


_ANTHROPIC_NO_REASONING_PATTERN = re.compile(r"claude-1\b|claude-instant|claude-2\b|claude-3-haiku\b", re.IGNORECASE)


def anthropic_supports_reasoning(model_id: str) -> bool:
    """No hardcoded Claude model list exists in this app (the catalog is
    fetched live from Anthropic's own API - see ANTHROPIC_MODELS_URL), so
    this is a denylist, not an allowlist: extended thinking is a broad,
    still-expanding feature across Anthropic's modern lineup, so a new,
    unrecognized model name is assumed capable rather than assumed not -
    only clearly legacy/non-reasoning models (Claude 1/2/Instant, Haiku 3)
    are excluded by name. Real Anthropic model ids put the version before
    the tier name ("claude-3-haiku-20240307", not "claude-haiku-3"), so
    the literal "claude-3-haiku" pattern here does NOT also match the
    newer, reasoning-capable "claude-3-5-haiku" - no lookahead trick
    needed, the "-5-" in between already makes them different substrings."""
    normalized = str(model_id or "").strip()
    if not normalized:
        return False
    return not _ANTHROPIC_NO_REASONING_PATTERN.search(normalized)


def gemini_supports_reasoning(model_id: str) -> bool:
    return _is_gemini_thinking_capable(model_id)


def openai_supports_reasoning(model_id: str) -> bool:
    return _is_openai_reasoning_model(model_id)


def ollama_supports_tools(model_name: str) -> bool:
    """ADR-007 stage 7.1: unlike reasoning (a pure string-family check) and
    unlike vision/audio (the request path sends attachment bytes
    unconditionally and lets the model ignore them), tool use is genuinely
    per-model server-side - sending a `tools` param to a model whose chat
    template doesn't support it is a real request-shape mismatch, not a
    politely-ignored extra. Reuses the SAME cached show()-backed probe
    _assert_ollama_audio_support already established (api_provider.py's
    _get_ollama_capabilities / _OLLAMA_CAPABILITY_CACHE) - Ollama's own
    /api/show response reports "tools" in its top-level `capabilities` list
    for models whose template actually supports function calling. None
    (server/model/metadata unavailable) is treated as NOT capable - the
    conservative default, matching _get_ollama_context_window's own
    fall-back-to-safe-default posture, since advertising tool support that
    doesn't exist would fail loudly mid-request instead of degrading
    gracefully to the no-tools path."""
    capabilities = _get_ollama_capabilities(model_name)
    if capabilities is None:
        return False
    return "tools" in capabilities


def ollama_supports_embedding(model_name: str) -> bool:
    """ADR-017 stage 17.3: same cached show()-backed probe as
    ollama_supports_tools' own docstring describes, checking for
    "embedding" in the model's own reported `capabilities` list instead of
    "tools" - Ollama's model library tags embedding-only models (e.g.
    nomic-embed-text, mxbai-embed-large) this way. A CHAT model (e.g.
    llama3) does NOT report "embedding" here, so this is what stops
    OllamaProvider.capabilities.embedding from being wrongly True just
    because SOME Ollama model somewhere supports it - ADR-017's own
    "Provider.embed()" is per configured-model, not per-server. None
    (server/model/metadata unavailable) is treated as NOT capable, the
    same conservative default ollama_supports_tools uses."""
    capabilities = _get_ollama_capabilities(model_name)
    if capabilities is None:
        return False
    return "embedding" in capabilities


def get_mode() -> str:
    if USE_API_MODE:
        return "API"
    return LOCAL_PROVIDER_TYPE


def is_configured() -> bool:
    """ADR-006 stage 6.5: delegates to the default runtime, whose logic now
    treats TASK_IMAGE_GEN as optional for EVERY API provider (previously
    only Anthropic) - a text-only OpenAI-compatible endpoint (vLLM, LM
    Studio, llama-server) is fully configured without an image model; image
    generation is capability-gated at call time instead (H6). See
    ProviderRuntime.is_configured."""
    return DEFAULT_RUNTIME.is_configured()
