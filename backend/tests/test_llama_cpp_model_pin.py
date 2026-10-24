"""ADR-018: a llama.cpp ModelRef pin must be the model that actually runs.

_provider_for_model_ref accepts a llama.cpp pin by the BASENAME of one of
the session's two configured paths (chat_model_path / title_model_path),
while every downstream lookup in provider_runtime/llama_cpp_runtime.py
resolves the model by TASK. A pin that only passed validation and then let
the task pick the model would silently answer a "tinyllama" node with the
chat model - and the model-comparison workflow ADR-018 exists for would
compare a model with itself.

These tests drive the real resolution chain all the way down to the
Llama(...) construction; only the native class is faked, so the model path
asserted is the one that would actually be loaded.
"""

from __future__ import annotations

import os

import pytest

import api_provider
import graphlink_task_config as config
from graphlink_model_catalog import ModelRef

# Captured at import time, before the autouse conftest fixture swaps
# api_provider.chat_stream for its one-chunk stub - see test_providers.py's
# own identical pattern/comment.
_REAL_CHAT_STREAM = api_provider.chat_stream

_CHAT_NAME = "qwen3-8b.gguf"   # a Qwen model: the /think directive applies
_TITLE_NAME = "tinyllama.gguf"  # not a reasoning model: no directive


class _LlamaEnv:
    def __init__(self, chat_path: str, title_path: str):
        self.paths = {_CHAT_NAME: chat_path, _TITLE_NAME: title_path}
        self.loaded: list[str] = []  # model_path of every Llama(...) construction
        self.calls: list[tuple[str, list]] = []  # (model_path, messages) per generation


def _write_gguf(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"GGUF")
    return os.path.abspath(str(path))


@pytest.fixture
def llama_env(monkeypatch, tmp_path):
    env = _LlamaEnv(
        _write_gguf(tmp_path / "chat" / _CHAT_NAME),
        _write_gguf(tmp_path / "title" / _TITLE_NAME),
    )

    class FakeLlama:
        def __init__(self, *, model_path, **_kwargs):
            self.model_path = model_path
            env.loaded.append(model_path)

        def create_chat_completion(self, messages=None, stream=False, **_kwargs):
            env.calls.append((self.model_path, messages))
            name = os.path.basename(self.model_path)
            if stream:
                return iter([
                    {"choices": [{"delta": {"content": f"from {name}"}, "finish_reason": None}]},
                    {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                ])
            return {"choices": [{"message": {"content": f"from {name}"}}]}

    monkeypatch.setattr(api_provider, "_load_llama_cpp_class", lambda: FakeLlama)
    monkeypatch.setattr(api_provider, "_LLAMA_CPP_CLIENT_CACHE", {})
    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_LLAMACPP)
    monkeypatch.setitem(api_provider.LLAMA_CPP_SETTINGS, "chat_model_path", env.paths[_CHAT_NAME])
    monkeypatch.setitem(api_provider.LLAMA_CPP_SETTINGS, "title_model_path", env.paths[_TITLE_NAME])
    monkeypatch.setitem(api_provider.LLAMA_CPP_SETTINGS, "reasoning_level", "high")
    return env


def _pin(model_id: str) -> ModelRef:
    return ModelRef(config.LOCAL_PROVIDER_LLAMACPP, model_id)


def _has_qwen_directive(messages: list) -> bool:
    return any(
        message.get("role") == "system"
        and str(message.get("content") or "").lstrip().startswith(("/think", "/no_think"))
        for message in messages
    )


_PIN_CASES = [
    (_CHAT_NAME, config.TASK_CHAT),
    (_CHAT_NAME, config.TASK_TITLE),
    (_TITLE_NAME, config.TASK_CHAT),
    (_TITLE_NAME, config.TASK_TITLE),
]


@pytest.mark.parametrize("pinned, task", _PIN_CASES)
def test_blocking_chat_runs_the_pinned_model_for_every_task(llama_env, pinned, task):
    response = api_provider.chat(task, [{"role": "user", "content": "hi"}], model_ref=_pin(pinned))

    assert llama_env.calls[-1][0] == llama_env.paths[pinned]
    assert response["message"]["content"] == f"from {pinned}"


@pytest.mark.parametrize("pinned, task", _PIN_CASES)
def test_streaming_chat_runs_the_pinned_model_for_every_task(llama_env, pinned, task):
    chunks = []
    response = _REAL_CHAT_STREAM(
        task, [{"role": "user", "content": "hi"}], lambda delta, _reset: chunks.append(delta),
        model_ref=_pin(pinned),
    )

    assert llama_env.calls[-1][0] == llama_env.paths[pinned]
    assert response["message"]["content"] == f"from {pinned}"
    assert "".join(chunks) == f"from {pinned}"


def test_unpinned_requests_still_resolve_the_model_by_task(llama_env):
    api_provider.chat(config.TASK_CHAT, [{"role": "user", "content": "hi"}])
    assert llama_env.calls[-1][0] == llama_env.paths[_CHAT_NAME]

    api_provider.chat(config.TASK_TITLE, [{"role": "user", "content": "hi"}])
    assert llama_env.calls[-1][0] == llama_env.paths[_TITLE_NAME]


def test_a_pinned_model_shares_the_cached_client_its_unpinned_task_uses(llama_env):
    """The client cache is keyed by the resolved model path, so a pin must
    land on the SAME cached Llama an unpinned request for that model uses -
    never a second multi-GB load of a model that is already resident."""
    api_provider.chat(config.TASK_TITLE, [{"role": "user", "content": "hi"}])
    api_provider.chat(config.TASK_CHAT, [{"role": "user", "content": "hi"}], model_ref=_pin(_TITLE_NAME))

    assert llama_env.loaded.count(llama_env.paths[_TITLE_NAME]) == 1
    assert llama_env.paths[_CHAT_NAME] not in llama_env.loaded


def test_the_qwen_thinking_directive_follows_the_pinned_model_not_the_task_slot(llama_env):
    """Message prep decides whether to inject Qwen's /think directive from
    the model path - a non-Qwen pin on a chat turn must not receive it just
    because the session's CHAT slot happens to hold a Qwen model."""
    api_provider.chat(config.TASK_CHAT, [{"role": "user", "content": "hi"}], model_ref=_pin(_TITLE_NAME))
    assert not _has_qwen_directive(llama_env.calls[-1][1])

    api_provider.chat(config.TASK_CHAT, [{"role": "user", "content": "hi"}], model_ref=_pin(_CHAT_NAME))
    assert _has_qwen_directive(llama_env.calls[-1][1])


def test_the_pinned_provider_reports_the_pinned_models_reasoning_capability(llama_env):
    state = api_provider._snapshot_provider_state()

    assert api_provider._provider_for_model_ref(_pin(_CHAT_NAME), state).capabilities.reasoning is True
    assert api_provider._provider_for_model_ref(_pin(_TITLE_NAME), state).capabilities.reasoning is False


def test_a_basename_shared_by_two_different_configured_files_is_rejected(llama_env, monkeypatch, tmp_path):
    """Two different files with the same name cannot be told apart by a
    basename pin - running either one would be a guess the user never
    made. The pin is refused with an actionable error instead."""
    other = _write_gguf(tmp_path / "elsewhere" / _CHAT_NAME)
    monkeypatch.setitem(api_provider.LLAMA_CPP_SETTINGS, "title_model_path", other)

    with pytest.raises(RuntimeError, match="matches more than one"):
        api_provider.chat(config.TASK_CHAT, [{"role": "user", "content": "hi"}], model_ref=_pin(_CHAT_NAME))
    assert llama_env.calls == []


def test_the_same_file_configured_in_both_slots_is_not_ambiguous(llama_env, monkeypatch):
    # Spelled differently (a redundant path segment) but the same file.
    chat_path = llama_env.paths[_CHAT_NAME]
    respelled = os.path.join(os.path.dirname(chat_path), ".", _CHAT_NAME)
    monkeypatch.setitem(api_provider.LLAMA_CPP_SETTINGS, "title_model_path", respelled)

    api_provider.chat(config.TASK_TITLE, [{"role": "user", "content": "hi"}], model_ref=_pin(_CHAT_NAME))
    assert llama_env.calls[-1][0] == chat_path


@pytest.mark.parametrize("link", ["hardlink", "symlink"])
def test_one_file_reached_through_a_link_from_both_slots_is_not_ambiguous(llama_env, monkeypatch, tmp_path, link):
    """Path normalization cannot see that a link (or a junction, an 8.3
    short name, a subst drive) reaches the same file the other slot names -
    refusing that pin would block a configuration holding one model."""
    chat_path = llama_env.paths[_CHAT_NAME]
    linked = tmp_path / "linked" / _CHAT_NAME
    linked.parent.mkdir()
    try:
        (os.link if link == "hardlink" else os.symlink)(chat_path, linked)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create a {link} here: {exc}")
    monkeypatch.setitem(api_provider.LLAMA_CPP_SETTINGS, "title_model_path", str(linked))

    api_provider.chat(config.TASK_TITLE, [{"role": "user", "content": "hi"}], model_ref=_pin(_CHAT_NAME))
    assert llama_env.calls[-1][0] == chat_path


@pytest.mark.parametrize("pinned, task", _PIN_CASES)
def test_provenance_for_a_pinned_dispatch_names_the_model_that_ran(llama_env, pinned, task):
    """describe_active_model feeds the reply's model badge and the token
    counter's cost lookup; for a pinned dispatch it must name the pinned
    model that actually answered, not whatever the task's slot holds."""
    api_provider.chat(task, [{"role": "user", "content": "hi"}], model_ref=_pin(pinned))

    ran = os.path.basename(llama_env.calls[-1][0])
    assert api_provider.describe_active_model(task, model_ref=_pin(pinned)) == ("llama.cpp", ran)
    # Unpinned provenance keeps reading the task's own slot.
    assert api_provider.describe_active_model(config.TASK_CHAT) == ("llama.cpp", _CHAT_NAME)
    assert api_provider.describe_active_model(config.TASK_TITLE) == ("llama.cpp", _TITLE_NAME)


def test_an_unconfigured_or_empty_pin_is_rejected(llama_env, monkeypatch):
    with pytest.raises(RuntimeError, match="not one of this session's configured"):
        api_provider.chat(config.TASK_CHAT, [{"role": "user", "content": "hi"}], model_ref=_pin("other.gguf"))

    # No title override configured: the empty title slot must not make an
    # empty model_id look like a configured model.
    monkeypatch.setitem(api_provider.LLAMA_CPP_SETTINGS, "title_model_path", "")
    with pytest.raises(RuntimeError, match="not one of this session's configured"):
        api_provider.chat(config.TASK_CHAT, [{"role": "user", "content": "hi"}], model_ref=_pin(""))
    assert llama_env.calls == []
