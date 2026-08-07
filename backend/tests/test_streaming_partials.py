"""ADR-006 stage 6.4 exit-criterion tests: universal streaming + partial-
output preservation (H5).

Two layers, mirroring the implementation's own split:

- Dispatcher level: _dispatch's `on_partial` contract - the loop-side
  accumulator, the blank-text guard, reset-frame clearing, and sync/async
  callable support. Uses test_agents.py's _configure_fake_chat_stream seam
  (api_provider.chat_stream installed directly), same as the R4.4 streaming
  section there.

- Intent level: regenerateResponse/sendMessage/sendConversationMessage
  through the real register_canvas + bus.dispatch_intent pipeline, with
  backend.agents._call_chat_agent_stream monkeypatched (the same seam the
  R6.1 branch-system-prompt tests use) - proving the node-scoped streaming
  identity and each caller's own partial-commit semantics end to end.

Helpers are imported from test_agents.py rather than duplicated - that
module already documents why each seam is the right one.
"""

from __future__ import annotations

import asyncio
import threading
import time

import backend.agents as agents_module
from backend.agents import AgentDispatcher
from backend.canvas import SceneDocument, register_canvas
from backend.composer import ComposerDocument
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.tests.conftest import chat_slots
from backend.tests.test_agents import (
    _FakeSettingsManager,
    _StreamRecorderConnection,
    _configure_fake_chat_stream,
    _configure_fake_ollama_provider_only,
    _make_dispatch_env,
)

import api_provider


# -- dispatcher level: _dispatch's on_partial contract ------------------------


def test_cancel_mid_stream_discards_the_partial_and_never_calls_on_partial(monkeypatch):
    """ADR-006 stage 6.4 review fix: a CANCEL discards the partial - the user
    said "stop, keep what I had", and committing would replace a regenerated
    node's complete original answer with a truncated partial (and, worse, a
    cancelled run's slot frees immediately while its worker can unwind
    arbitrarily late, so a late commit could clobber a replacement run).
    R4.2's cancel-discards-everything semantics stay pinned for BOTH
    callbacks; H5's partial preservation is for streams that DIE
    (error/timeout), covered by the tests below."""
    started = threading.Event()

    def fake_chat_stream(task, messages, on_chunk, cancellation_event=None, **kwargs):
        on_chunk("Hel", False)
        on_chunk("lo", False)
        started.set()
        while not cancellation_event.is_set():
            time.sleep(0.01)
        raise api_provider.RequestCancelledError("Request cancelled.")

    _configure_fake_chat_stream(monkeypatch, fake_chat_stream)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        replies, partials = [], []

        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=replies.append,
            on_partial=partials.append,
        )
        request_id, entry = next(iter(chat_slots(dispatcher).items()))

        await asyncio.to_thread(started.wait, 5)
        assert dispatcher.cancel(request_id) is True

        await entry["task"]

        assert partials == [], "cancel discards the partial - see the docstring"
        assert replies == [], "on_reply still never fires for a cancelled request"
        assert chat_slots(dispatcher) == {}

    asyncio.run(run())


def test_error_mid_stream_commits_the_accumulated_partial_exactly_once(monkeypatch):
    """ADR-006 stage 6.4 (H5): a stream that DIES commits its accumulated
    text via on_partial exactly once - the generic-Exception branch. The
    timeout branch has its own separate test below (review fix: it is a
    physically separate _commit_partial call site; "identical plumbing"
    left it deletable without a failure)."""

    def fake_chat_stream(task, messages, on_chunk, **kwargs):
        on_chunk("partial text", False)
        raise RuntimeError("boom")

    _configure_fake_chat_stream(monkeypatch, fake_chat_stream)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        replies, partials = [], []

        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=replies.append,
            on_partial=partials.append,
        )
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assert partials == ["partial text"]
        assert replies == []
        assert notifications.message == "AI response failed: boom"

    asyncio.run(run())


def test_on_partial_is_not_called_when_only_whitespace_accumulated(monkeypatch):
    """The blank-text guard: a stream that died before producing anything
    worth preserving keeps the exact pre-6.4 discard behavior - no partial
    node/message is ever committed for whitespace."""

    def fake_chat_stream(task, messages, on_chunk, **kwargs):
        on_chunk("  \n\t ", False)
        raise RuntimeError("boom")

    _configure_fake_chat_stream(monkeypatch, fake_chat_stream)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        partials = []

        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=lambda text: None,
            on_partial=partials.append,
        )
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assert partials == [], "whitespace-only accumulation must not commit a partial"

    asyncio.run(run())


def test_reset_frame_clears_the_partial_accumulator(monkeypatch):
    """A reset (a discarded reasoning-retry attempt) discards the prior
    attempt's text EVERYWHERE - including the 6.4 accumulator, not just the
    live preview. Otherwise a partial commit after a reset would resurrect
    text the stream itself already threw away."""

    def fake_chat_stream(task, messages, on_chunk, **kwargs):
        on_chunk("first", False)
        on_chunk("", True)  # reset - the "first" attempt is discarded
        on_chunk("second", False)
        raise RuntimeError("boom")

    _configure_fake_chat_stream(monkeypatch, fake_chat_stream)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        partials = []

        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=lambda text: None,
            on_partial=partials.append,
        )
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assert partials == ["second"], "only text streamed AFTER the reset survives"

    asyncio.run(run())


def test_async_on_partial_coroutine_is_awaited(monkeypatch):
    """_commit_partial supports both callable shapes, same
    inspect.iscoroutinefunction duck-typing as on_reply - sendMessage's
    _on_partial is a real `async def` (it awaits publish_token_counter), so
    this is a load-bearing branch, not gold-plating."""

    def fake_chat_stream(task, messages, on_chunk, **kwargs):
        on_chunk("async partial", False)
        raise RuntimeError("boom")

    _configure_fake_chat_stream(monkeypatch, fake_chat_stream)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        partials = []

        async def on_partial(text):
            partials.append(text)

        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=lambda text: None,
            on_partial=on_partial,
        )
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assert partials == ["async partial"], "an async on_partial must be awaited, not dropped"

    asyncio.run(run())


# -- intent level: register_canvas + real dispatch ----------------------------


def _make_canvas_env(session_name: str):
    """The register_canvas environment every intent-level test below shares -
    same construction as test_agents.py's R6.1 end-to-end tests."""
    bus = SessionBus(session_name)
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    composer_document = ComposerDocument()
    bus.register_topic("app-composer", composer_document.payload)
    dispatcher = AgentDispatcher(_FakeSettingsManager())
    document = register_canvas(bus, notifications, dispatcher, composer_document)
    return bus, notifications, composer_document, dispatcher, document


def test_regenerate_streams_with_node_scoped_identity_never_the_composer(monkeypatch):
    """ADR-006 stage 6.4: regenerate now streams, and the frames' identity
    lives on the TARGET NODE (pending_request_id, published on "scene") -
    the Composer's live preview must never light up for a regenerate on
    some other node, which is exactly the confusion R4.4's stream=False
    deferral existed to prevent, now dissolved by identity instead of
    suppression."""
    _configure_fake_ollama_provider_only(monkeypatch)
    started = threading.Event()
    release = threading.Event()

    def fake_stream(conversation_history, persona_text, cancel_event, on_chunk):
        on_chunk("regenerated ", False)
        started.set()
        release.wait(5)
        on_chunk("reply", False)
        return "regenerated reply"

    monkeypatch.setattr(agents_module, "_call_chat_agent_stream", fake_stream)

    async def run():
        bus, notifications, composer_document, dispatcher, document = _make_canvas_env(
            "regenerate-node-identity-test"
        )
        recorder = _StreamRecorderConnection()
        bus.attach(recorder)

        root = document.add_chat_node(0, 0, "root message", True)
        assistant_reply = document.add_chat_node(0, 100, "old reply", False, parent_id=root.id)

        await bus.dispatch_intent("scene", "regenerateResponse", [assistant_reply.id])
        entry = next(iter(chat_slots(dispatcher).values()))

        # Mid-stream: the fake is parked on `release`, so the request is
        # genuinely in flight right now.
        await asyncio.to_thread(started.wait, 5)
        pending_id = assistant_reply.pending_request_id
        assert pending_id is not None, "the TARGET node carries the in-flight request id"
        assert composer_document.request_id is None, "the Composer never sees this request"
        assert composer_document.request_state == "idle"

        release.set()
        await entry["task"]

        assert recorder.frames, "stream frames were broadcast"
        assert all(f["topic"] == "scene" for f in recorder.frames), (
            'node-scoped frames publish on "scene", never "app-composer"'
        )
        assert {f["requestId"] for f in recorder.frames} == {pending_id}
        assert recorder.frames[-1]["done"] is True

        assert assistant_reply.pending_request_id is None, "cleared once the reply lands"
        assert assistant_reply.content == "regenerated reply"
        assert assistant_reply.state.response_incomplete is False, (
            "a COMPLETE regenerate never sets the interrupted marker"
        )

    asyncio.run(run())


def test_regenerate_killed_mid_stream_preserves_partial_and_is_retryable(monkeypatch):
    """The regenerate half of H5: the partial replaces the node's content
    (flagged response_incomplete), the old content children are torn down
    (they annotated content that no longer exists), and a subsequent
    SUCCESSFUL regenerate clears the marker - update_chat_node_content's
    default incomplete=False doubles as the clear."""
    _configure_fake_ollama_provider_only(monkeypatch)

    def dying_stream(conversation_history, persona_text, cancel_event, on_chunk):
        on_chunk("half a regen", False)
        raise RuntimeError("boom")

    monkeypatch.setattr(agents_module, "_call_chat_agent_stream", dying_stream)

    async def run():
        bus, notifications, composer_document, dispatcher, document = _make_canvas_env(
            "regenerate-partial-test"
        )
        root = document.add_chat_node(0, 0, "root message", True)
        assistant_reply = document.add_chat_node(0, 100, "old reply", False, parent_id=root.id)
        code_child = document.add_code_node(0, 200, "print('old')", "python", assistant_reply.id)

        await bus.dispatch_intent("scene", "regenerateResponse", [assistant_reply.id])
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assert assistant_reply.content == "half a regen", "the partial landed in place"
        assert assistant_reply.state.response_incomplete is True
        assert code_child.id not in document.nodes, (
            "old content children annotated content that no longer exists"
        )

        # Retryable: a SECOND, successful regenerate clears the marker.
        def full_stream(conversation_history, persona_text, cancel_event, on_chunk):
            on_chunk("a full regenerated reply", False)
            return "a full regenerated reply"

        monkeypatch.setattr(agents_module, "_call_chat_agent_stream", full_stream)

        await bus.dispatch_intent("scene", "regenerateResponse", [assistant_reply.id])
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assert assistant_reply.content == "a full regenerated reply"
        assert assistant_reply.state.response_incomplete is False, (
            "retry succeeded - the interrupted marker is gone"
        )

    asyncio.run(run())


def test_send_conversation_message_killed_mid_stream_appends_incomplete_history(monkeypatch):
    """The conversation half of H5: the partial lands as a real assistant
    history message carrying incomplete=True, and scene_payload's strict
    history allow-list surfaces it as "incomplete": True on the wire."""
    _configure_fake_ollama_provider_only(monkeypatch)

    def dying_stream(conversation_history, persona_text, cancel_event, on_chunk):
        on_chunk("half a convo reply", False)
        raise RuntimeError("boom")

    monkeypatch.setattr(agents_module, "_call_chat_agent_stream", dying_stream)

    async def run():
        bus, notifications, composer_document, dispatcher, document = _make_canvas_env(
            "conversation-partial-test"
        )
        parent_id = await bus.dispatch_intent("scene", "addNode", [0, 0, "parent"])
        node_id = await bus.dispatch_intent("scene", "addConversationNode", [10, 10, parent_id])

        await bus.dispatch_intent("scene", "sendConversationMessage", [node_id, "hello"])
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assert document.nodes[node_id].history == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "half a convo reply", "incomplete": True},
        ]

        rows = {n["id"]: n for n in document.scene_payload()["nodes"]}
        assert rows[node_id]["history"] == [
            {"role": "user", "content": "hello", "incomplete": False},
            {"role": "assistant", "content": "half a convo reply", "incomplete": True},
        ]

    asyncio.run(run())


def test_send_message_killed_mid_stream_creates_an_incomplete_ai_node(monkeypatch):
    """The sendMessage half of H5: the partial lands as a NEW assistant chat
    node (there was no node to update in place - the complete path creates
    one too) flagged responseIncomplete on the wire, parented to the user's
    message so the branch wiring matches the complete path."""
    _configure_fake_ollama_provider_only(monkeypatch)

    def dying_stream(conversation_history, persona_text, cancel_event, on_chunk):
        on_chunk("half a reply", False)
        raise RuntimeError("boom")

    monkeypatch.setattr(agents_module, "_call_chat_agent_stream", dying_stream)

    async def run():
        bus, notifications, composer_document, dispatcher, document = _make_canvas_env(
            "send-message-partial-test"
        )

        user_node_id = await bus.dispatch_intent("scene", "sendMessage", ["hi there"])
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        ai_nodes = [
            n for n in document.nodes.values() if n.kind == "chat" and n.id != user_node_id
        ]
        assert len(ai_nodes) == 1, "exactly one partial AI node was committed"
        ai_node = ai_nodes[0]
        assert ai_node.content == "half a reply"
        assert ai_node.state.response_incomplete is True
        assert any(
            e.source == user_node_id and e.target == ai_node.id
            for e in document.edges.values()
        ), "parent wiring matches the complete path, so Regenerate works on it"

        rows = {n["id"]: n for n in document.scene_payload()["nodes"]}
        assert rows[ai_node.id]["responseIncomplete"] is True

    asyncio.run(run())


# -- wire defaults ------------------------------------------------------------
#
# The NODE-row "responseIncomplete" default (False, all kinds) is already
# pinned by tests/test_node_state_migration.py's
# _EXPECTED_NON_OWNING_KIND_WIRE_DEFAULTS - not duplicated here. That suite
# only covers node rows, though, so the HISTORY-row "incomplete" default is
# pinned below.


def test_history_wire_rows_default_incomplete_false_for_normal_messages():
    """scene_payload's history projection always emits "incomplete" (a
    strict allow-list widened by exactly one key in 6.4) - False for every
    normal message, including legacy entries that never carried the key at
    all."""
    doc = SceneDocument()
    parent = doc.add_node(0, 0)
    conv = doc.add_conversation_node(10, 10, parent.id)
    doc.append_conversation_user_message(conv.id, "hi")
    doc.append_conversation_assistant_message(conv.id, "a full reply")

    rows = {n["id"]: n for n in doc.scene_payload()["nodes"]}
    assert rows[conv.id]["history"] == [
        {"role": "user", "content": "hi", "incomplete": False},
        {"role": "assistant", "content": "a full reply", "incomplete": False},
    ]


# -- 6.4 review-fix coverage --------------------------------------------------


def test_timeout_mid_stream_commits_the_accumulated_partial(monkeypatch):
    """The TIMEOUT branch's _commit_partial is a physically separate call in
    a separate except block (agents.py) - this pins it independently so
    deleting that one call fails a test (6.4 adversarial review: the
    cancel/error tests alone left it deletable)."""
    monkeypatch.setattr(agents_module, "WATCHDOG_TIMEOUT_SECONDS", 0.3)

    def stalling_stream(task, messages, on_chunk, cancellation_event=None, **kwargs):
        on_chunk("timed-out partial", False)
        # Outlive the shrunk watchdog; exit promptly once cancel lands so the
        # worker thread doesn't linger past the test.
        cancellation_event.wait(5)
        raise api_provider.RequestCancelledError("Request cancelled.")

    _configure_fake_chat_stream(monkeypatch, stalling_stream)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        replies, partials = [], []

        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=replies.append,
            on_partial=partials.append,
        )
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assert partials == ["timed-out partial"]
        assert replies == []

    asyncio.run(run())


def test_stale_run_with_popped_handle_never_commits_its_partial(monkeypatch):
    """6.4 review fix (HIGH): cancel pops the handle immediately, but the
    cancelled worker can unwind arbitrarily late - and it may unwind via the
    GENERIC exception branch, not RequestCancelledError. By then a
    replacement run may own the node, so _commit_partial is gated on this
    run still being registered (the same staleness gate 6.2 put on
    web_research's terminal callbacks). Here the worker raises RuntimeError
    AFTER the cancel popped its handle: no partial may land."""
    started = threading.Event()

    def late_dying_stream(task, messages, on_chunk, cancellation_event=None, **kwargs):
        on_chunk("stale text", False)
        started.set()
        cancellation_event.wait(5)
        raise RuntimeError("late failure after cancel already released the slot")

    _configure_fake_chat_stream(monkeypatch, late_dying_stream)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        replies, partials = [], []

        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=replies.append,
            on_partial=partials.append,
        )
        request_id, entry = next(iter(chat_slots(dispatcher).items()))

        await asyncio.to_thread(started.wait, 5)
        assert dispatcher.cancel(request_id) is True  # handle popped NOW

        await entry["task"]  # generic-Exception unwind, handle already gone

        assert partials == [], "a popped handle means this run's outputs no longer land"
        assert replies == []

    asyncio.run(run())


def test_cancelled_regenerate_keeps_the_original_response_intact(monkeypatch):
    """6.4 review fix (intent level): cancelling a regenerate must keep the
    node's COMPLETE original answer - content untouched, children intact, no
    incomplete flag. The user aborted the retry; telling them to redo it
    while destroying what they had would invert the point of cancel."""
    _configure_fake_ollama_provider_only(monkeypatch)
    started = threading.Event()

    def cancellable_stream(conversation_history, persona_text, cancel_event, on_chunk):
        on_chunk("half a regen", False)
        started.set()
        cancel_event.wait(5)
        raise api_provider.RequestCancelledError("Request cancelled.")

    monkeypatch.setattr(agents_module, "_call_chat_agent_stream", cancellable_stream)

    async def run():
        bus, notifications, composer_document, dispatcher, document = _make_canvas_env(
            "regenerate-cancel-keeps-original-test"
        )
        root = document.add_chat_node(0, 0, "root message", True)
        assistant_reply = document.add_chat_node(0, 100, "old reply", False, parent_id=root.id)
        code_child = document.add_code_node(0, 200, "print('old')", "python", assistant_reply.id)

        await bus.dispatch_intent("scene", "regenerateResponse", [assistant_reply.id])
        request_id, entry = next(iter(chat_slots(dispatcher).items()))
        await asyncio.to_thread(started.wait, 5)
        assert dispatcher.cancel(request_id) is True
        await entry["task"]

        assert assistant_reply.content == "old reply", "cancel keeps what the user had"
        assert assistant_reply.state.response_incomplete is False
        assert code_child.id in document.nodes, "original children survive a cancel"

    asyncio.run(run())
