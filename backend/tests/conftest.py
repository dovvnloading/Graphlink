import pytest

import backend  # noqa: F401 - exercises the package import
# R7.2: api_provider.py sits at the repo root, a sibling of backend/ - the
# same directory pytest already put on sys.path to make `backend` itself
# importable, so this needs no setup and no particular ordering relative to
# the import above.
import api_provider


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
    through on_chunk as a single synthetic chunk - the exact shape
    chat_stream's own documented non-Ollama fallback already uses. This tests
    send_message's downstream logic (node creation, parsing, cancellation),
    which is unaffected by whether the reply arrived in one chunk or many -
    real incremental chunking is covered separately by
    graphlink_app/tests/test_api_provider_chat_stream.py and this suite's own
    dedicated streaming tests in test_agents.py."""

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


def pycoder_slots(dispatcher):
    return _run_slots(dispatcher, "pycoder")


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
