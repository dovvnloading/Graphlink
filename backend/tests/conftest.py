import os
from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings

import backend  # noqa: F401 - exercises the package import
# R7.2: api_provider.py sits at the repo root, a sibling of backend/ - the
# same directory pytest already put on sys.path to make `backend` itself
# importable, so this needs no setup and no particular ordering relative to
# the import above.
import api_provider


_REAL_DATA_DIR = (Path.home() / ".graphlink").resolve()

# ADR-022 stage 22.1: shared runners are noisy (the same reasoning already
# applied to faulthandler_timeout=60 and the perf-gate tier's generous CI
# ceiling) - a per-test wall-clock deadline flakes under -n auto/worksteal
# contention, and CI doesn't need hundreds of examples to get real signal.
# GitHub Actions sets CI=true automatically in every job; local runs get
# Hypothesis's fuller default profile (100 examples, real deadline).
settings.register_profile("ci", max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile("ci" if os.environ.get("CI") else "default")


@pytest.fixture(autouse=True)
def _never_touch_the_real_user_data_dir(monkeypatch, tmp_path):
    """Hard-fail any test that opens the developer's REAL ~/.graphlink files.

    create_app() defaults settings_state_file/chat_db_path to
    ~/.graphlink/session.dat and ~/.graphlink/chats.db (see
    graphlink_settings_store.SettingsManager and chat_library.DEFAULT_DB_PATH).
    A test that constructs the app without overriding BOTH therefore reads and
    REWRITES the live settings/chat history of whoever runs the suite - which
    is exactly what happened: several files here called create_app() bare, and
    every `pytest` run silently rewrote the real session.dat (empirically
    confirmed by watching its mtime change). It was benign only by luck -
    bootstrap_provider_state() happened to write back an identical value, and
    its except-branch would have overwritten the real current_mode outright.

    This guard makes that class of bug impossible to reintroduce silently: it
    fails loudly at the moment of access, naming the offending path, rather
    than leaving a future contributor to notice their own data drifting. Every
    test must pass a tmp_path/TemporaryDirectory-derived override - see
    test_assets.py's or test_http_trust_boundary.py's make_client helpers for
    the established shape.

    ADR-020 stage 20.4: extended to cover backend.knowledge_store too (its
    own `_connect`, the same shape as chat_library's) - backend/chat_library.
    py's save_chat_atomically_row/delete_chat now write/delete through it
    (indexing a saved graph's content for global search - see that
    function's own docstring) on EVERY real call, not just ones a caller
    opted into. Also REDIRECTS (not just guards) knowledge_store.
    DEFAULT_DB_PATH to a throwaway tmp_path for the duration of every test:
    this suite has 40+ pre-existing save_chat_atomically_row/delete_chat
    call sites (this file, test_session_lifecycle.py, test_autosave.py) that
    predate that indexing hook and pass no explicit knowledge_db_path
    override, so without this redirect every one of them would immediately
    start hard-failing against the guard above the moment it fires - exactly
    the class of bug this fixture exists to make impossible, just reached
    through a NEW call path this fixture's own pre-20.4 shape had no way to
    anticipate. Safe to redirect the constant itself (rather than touching
    each call site): backend/chat_library.py's own indexing hook reads
    `knowledge_store.DEFAULT_DB_PATH` as a live module attribute at CALL
    time (`from backend import knowledge_store` + `knowledge_store.
    DEFAULT_DB_PATH`, never `from backend.knowledge_store import
    DEFAULT_DB_PATH`, which would bind the value at IMPORT time instead and
    never see this patch) - the same pattern backend/tests/
    test_intents_knowledge.py's own module docstring already documents and
    relies on for its own, unrelated monkeypatching of this exact constant.
    A test that explicitly passes its own knowledge_db_path (or its own
    explicit monkeypatch of this same constant, applied after this fixture
    via ordinary fixture/setattr ordering) is unaffected either way.
    """
    def _guard(path, what):
        try:
            resolved = Path(path).resolve()
        except (OSError, ValueError):  # unresolvable path - nothing real to hit
            return
        if resolved == _REAL_DATA_DIR or _REAL_DATA_DIR in resolved.parents:
            raise AssertionError(
                f"test touched REAL user data: {what} -> {resolved}. Pass a "
                f"tmp_path-derived settings_state_file=/chat_db_path= instead "
                f"(see backend/tests/conftest.py's own docstring)."
            )

    import graphlink_settings_store as settings_store
    from backend import chat_library
    from backend import knowledge_store

    real_settings_init = settings_store.SettingsManager.__init__

    def guarded_settings_init(self, state_file=None, *args, **kwargs):
        _guard(
            state_file if state_file is not None else _REAL_DATA_DIR / "session.dat",
            "SettingsManager(state_file=...)",
        )
        return real_settings_init(self, state_file, *args, **kwargs)

    real_connect = chat_library._connect

    def guarded_connect(db_path, *args, **kwargs):
        _guard(db_path, "chat_library._connect(db_path=...)")
        return real_connect(db_path, *args, **kwargs)

    real_knowledge_connect = knowledge_store._connect

    def guarded_knowledge_connect(db_path, *args, **kwargs):
        _guard(db_path, "knowledge_store._connect(db_path=...)")
        return real_knowledge_connect(db_path, *args, **kwargs)

    monkeypatch.setattr(settings_store.SettingsManager, "__init__", guarded_settings_init)
    monkeypatch.setattr(chat_library, "_connect", guarded_connect)
    monkeypatch.setattr(knowledge_store, "_connect", guarded_knowledge_connect)
    monkeypatch.setattr(knowledge_store, "DEFAULT_DB_PATH", tmp_path / "knowledge-test-default" / "knowledge.db")
    yield


@pytest.fixture(autouse=True)
def _chat_stream_delegates_to_patched_chat(monkeypatch):
    """R4.4: send_message's reply path now always calls api_provider.chat_stream
    (AgentDispatcher.start_chat_reply passes stream=True unconditionally), not
    api_provider.chat. Every existing test in this suite fakes only chat() via
    patch.object(api_provider, "chat", fake_chat) - without this fixture,
    chat_stream's real Ollama branch runs instead (these tests configure Ollama
    mode to match production), attempting a genuine network call.

    This generic chat_stream fake looks up api_provider.chat AT CALL TIME (a
    fresh module-attribute read, not a captured reference), so it transparently
    picks up whatever fake_chat a given test has patched into api_provider.chat
    for the duration of its own `with patch.object(...)` block, and forwards it
    through on_chunk as a single synthetic chunk (the shape the real
    function's non-Ollama fallback used before ADR-006 stage 6.5b made every
    provider stream for real - still a valid double, since on_chunk's
    contract is delta-agnostic). This tests send_message's downstream logic
    (node creation, parsing, cancellation), which is unaffected by whether
    the reply arrived in one chunk or many - real incremental chunking is
    covered separately by backend/tests/test_providers.py's chat_stream
    tests and this suite's own dedicated streaming tests in test_agents.py."""

    def _generic_chat_stream(task, messages, on_chunk, **kwargs):
        response = api_provider.chat(task, messages, **kwargs)
        on_chunk(response["message"].get("content", ""), False)
        return response

    monkeypatch.setattr(api_provider, "chat_stream", _generic_chat_stream)
    yield


def _run_slots(dispatcher, kind):
    """ADR-002 stage 2.3+ test adapter: reproduces the pre-migration
    dict[request_id, {"cancel_event": ..., "approval_future": ..., "task":
    ...}] shape that every fire-and-forget dispatch surface's tests in
    this suite were written against, filtered to one AgentDispatcher._runs
    kind - see backend/run_lifecycle.py for the real (RunHandle-based)
    production shape this is adapting. Returns a plain dict so callers can
    keep using .values()/.items()/.keys()/subscript/len()/== {} exactly as
    they did against the old dict (a kind with no cancel_event/
    approval_future, like image, simply carries None for those keys in the
    returned dict - harmless, since no pre-existing test for such a kind
    ever read them)."""
    return {
        handle.request_id: {
            "cancel_event": handle.cancel_event,
            "approval_future": handle.approval_future,
            "task": handle.task,
        }
        for handle in dispatcher._runs.values()
        if handle.kind == kind
    }


def chat_slots(dispatcher):
    return _run_slots(dispatcher, "chat")


def image_slots(dispatcher):
    return _run_slots(dispatcher, "image")


def artifact_slots(dispatcher):
    return _run_slots(dispatcher, "artifact")


def gitlink_run_slots(dispatcher):
    return _run_slots(dispatcher, "gitlink_run")


def gitlink_apply_slots(dispatcher):
    return _run_slots(dispatcher, "gitlink_apply")


def code_sandbox_slots(dispatcher):
    return _run_slots(dispatcher, "code_sandbox")


def web_research_slots(dispatcher):
    """Note: web_research's cancellation mechanism is RunHandle.on_cancel
    (a bound CancellationToken.cancel), not cancel_event - _run_slots'
    returned "cancel_event" key is always None for this kind. No
    pre-existing test in this suite ever read a "cancel_token" key
    directly (only .values()/len()/== {}/entry["task"]), so this is a
    safe, faithful adapter."""
    return _run_slots(dispatcher, "web_research")


async def drain_runs(dispatcher, kind=None):
    """ADR-006 stage 6.2 fire-and-forget test adapter: run_single_shot's
    surfaces (chart/note/branch_comparison/branch_synthesis) now claim
    their slot synchronously and SCHEDULE the generation as an asyncio
    task, returning BEFORE it runs - so a test that awaits start_* must
    drain the scheduled task(s) before asserting on callbacks/
    notifications/slot state (and before the test's event loop closes).
    Filters to one kind when given; a directly-seeded handle with no task
    (registry.claim() in busy-guard tests) is skipped either way."""
    tasks = [
        handle.task
        for handle in list(dispatcher._runs.values())
        if handle.task is not None and (kind is None or handle.kind == kind)
    ]
    for task in tasks:
        await task


def busy_count(dispatcher, kind):
    """ADR-002 stage 2.3 test adapter: the count-based equivalent of the
    old dict-of-sentinels' len()/truthiness checks for a "directly-
    awaited, single-shot" kind (chart, note) now living in
    AgentDispatcher._runs."""
    return sum(1 for handle in dispatcher._runs.values() if handle.kind == kind)
