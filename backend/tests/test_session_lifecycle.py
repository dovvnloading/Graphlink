"""ADR-004 stage 4.3: session issuance restriction + idle eviction tests.

Split into three layers, matching the module boundaries the implementation
itself respects (see backend/events.py's own docstring for why):

1. EventBus unit tests - the generic mechanism, unrestricted by default
   (preserves backend/tests/test_event_bus.py's own coverage of the
   pre-stage-4.3 behavior) and restricted when allowed_session_ids is
   supplied.
2. sweep_idle_sessions unit tests - the eviction DECISION logic, exercised
   directly (no sleep, no free-running background task) by manipulating
   SessionBus.idle_since directly - same "test the real logic
   deterministically" precedent backend/autosave.py's own
   bus.autosave_guarded_tick established.
3. Integration tests through the real backend.app.create_app() - the
   literal exit-criterion behaviors: an unknown ?session= is rejected on
   both /ws and /api/assets/*, and the real _evict_idle_session callback's
   in-flight-run veto and autosave-cancellation.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import WebSocket
from fastapi.testclient import TestClient

from backend.app import create_app, _evict_idle_session
from backend.events import DEFAULT_SESSION_ID, EventBus, SessionBus, UnknownSessionError
from backend.notifications import NotificationState
from backend.session_context import SessionContext, attach_session_context, get_session_context


# -- layer 1: EventBus.session() issuance restriction ------------------------


def test_unrestricted_bus_creates_any_session_id():
    # The pre-stage-4.3 default, preserved: allowed_session_ids=None is the
    # class's own baseline behavior, matching test_event_bus.py.
    bus = EventBus()
    a = bus.session("a")
    b = bus.session("b")
    assert a.session_id == "a"
    assert b.session_id == "b"
    assert a is not b


def test_restricted_bus_creates_only_the_allowed_id():
    bus = EventBus(allowed_session_ids=frozenset({DEFAULT_SESSION_ID}))
    default = bus.session(DEFAULT_SESSION_ID)
    assert default.session_id == DEFAULT_SESSION_ID


def test_restricted_bus_rejects_an_unknown_id():
    bus = EventBus(allowed_session_ids=frozenset({DEFAULT_SESSION_ID}))
    with pytest.raises(UnknownSessionError):
        bus.session("attacker-chosen-id")


def test_restricted_bus_rejecting_an_unknown_id_does_not_create_it():
    # The whole point: a rejected id must never be added to _sessions -
    # otherwise a repeated attempt would eventually succeed once the
    # exception-raising branch had already (incorrectly) inserted it.
    bus = EventBus(allowed_session_ids=frozenset({DEFAULT_SESSION_ID}))
    with pytest.raises(UnknownSessionError):
        bus.session("attacker-chosen-id")
    assert bus.session_ids() == []
    with pytest.raises(UnknownSessionError):
        bus.session("attacker-chosen-id")


def test_restricted_bus_still_allows_reconnecting_to_an_id_that_already_exists():
    # A real reconnect (the SAME id calling session() a second time) must
    # keep working regardless of the restriction - this is what makes
    # "default" itself (created once, then reconnected to many times over
    # a session's life) work at all under a restrictive policy.
    bus = EventBus(allowed_session_ids=frozenset({DEFAULT_SESSION_ID}))
    first = bus.session(DEFAULT_SESSION_ID)
    second = bus.session(DEFAULT_SESSION_ID)
    assert first is second


# -- layer 2: sweep_idle_sessions decision logic ------------------------------


def _configure(bus: SessionBus) -> None:
    bus.register_topic("t", lambda: {})


def test_sweep_is_a_no_op_without_an_evict_callback():
    bus = EventBus(configure_session=_configure)
    session = bus.session("s1")
    session.idle_since -= 10_000  # far past any real TTL
    assert bus.sweep_idle_sessions() == []
    assert bus.session_ids() == ["s1"]


def test_sweep_never_evicts_a_session_with_a_live_connection():
    bus = EventBus(configure_session=_configure, session_idle_ttl_seconds=0.0, evict_idle_session=lambda b: True)
    session = bus.session("s1")

    class _Conn:
        async def send_json(self, data):
            pass

    session.attach(_Conn())
    assert session.idle_since is None

    assert bus.sweep_idle_sessions() == []
    assert bus.session_ids() == ["s1"]


def test_sweep_does_not_evict_before_the_ttl_elapses():
    bus = EventBus(configure_session=_configure, session_idle_ttl_seconds=10_000.0, evict_idle_session=lambda b: True)
    bus.session("s1")  # idle_since stamped at construction, "just now"

    assert bus.sweep_idle_sessions() == []
    assert bus.session_ids() == ["s1"]


def test_sweep_evicts_a_session_idle_past_the_ttl():
    bus = EventBus(configure_session=_configure, session_idle_ttl_seconds=1.0, evict_idle_session=lambda b: True)
    session = bus.session("s1")
    session.idle_since -= 10.0  # push it well past the 1s TTL

    evicted = bus.sweep_idle_sessions()

    assert evicted == ["s1"]
    assert bus.session_ids() == []


def test_sweep_honors_the_evict_callbacks_veto():
    # The callback deciding "no, not this one" (e.g. a real in-flight run)
    # must actually stop the eviction, not just be consulted for show.
    bus = EventBus(configure_session=_configure, session_idle_ttl_seconds=1.0, evict_idle_session=lambda b: False)
    session = bus.session("s1")
    session.idle_since -= 10.0

    evicted = bus.sweep_idle_sessions()

    assert evicted == []
    assert bus.session_ids() == ["s1"]


def test_sweep_only_evicts_the_sessions_actually_past_their_ttl():
    bus = EventBus(configure_session=_configure, session_idle_ttl_seconds=1.0, evict_idle_session=lambda b: True)
    old_session = bus.session("old")
    old_session.idle_since -= 10.0
    bus.session("fresh")  # idle_since is "just now" - not past the TTL

    evicted = bus.sweep_idle_sessions()

    assert evicted == ["old"]
    assert bus.session_ids() == ["fresh"]


def test_the_background_eviction_loop_survives_a_sweep_that_raises():
    """Adversarial-review finding: every eviction test above calls
    sweep_idle_sessions() directly and synchronously, by design (see the
    module docstring) - none of them actually drive _eviction_loop() as a
    real background asyncio.Task, so the "one bad sweep must never end the
    loop forever" guarantee (mirroring backend/autosave.py's own _loop()
    precedent) had zero regression coverage. A future refactor that moved
    the try/except outside the while-loop, or narrowed what it catches,
    would ship undetected. This test starts the REAL free-running loop
    (via the real _ensure_eviction_loop_started() entry point, triggered
    by a real session() call inside a running event loop) and proves it
    keeps calling sweep_idle_sessions() on every subsequent interval after
    an injected failure."""

    async def _run():
        calls = {"n": 0}

        def broken_evict(bus):
            calls["n"] += 1
            raise RuntimeError(f"simulated bug in evict_idle_session #{calls['n']}")

        bus = EventBus(
            configure_session=_configure,
            session_idle_ttl_seconds=0.0,
            sweep_interval_seconds=0.02,
            evict_idle_session=broken_evict,
        )
        session = bus.session("s1")
        session.idle_since -= 10_000
        assert bus._eviction_task is not None, "session() must have lazily started the real background loop"

        await asyncio.sleep(0.15)

        assert calls["n"] >= 3, "the loop must keep calling the sweep on every interval, not stop after the first failure"
        assert not bus._eviction_task.done(), "one bad sweep must never end the free-running loop"

        bus._eviction_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await bus._eviction_task

    asyncio.run(_run())


# -- layer 3: integration through the real app --------------------------------


def _make_client() -> TestClient:
    # settings_state_file/chat_db_path: without an explicit override,
    # create_app() falls through to its real production defaults
    # (~/.graphlink/session.dat, ~/.graphlink/chats.db) - every one of this
    # helper's callers would read AND rewrite the developer's real live
    # settings/chat data. Matches test_assets.py's own make_client() and
    # test_http_trust_boundary.py's _make_client().
    import tempfile
    from pathlib import Path

    state_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    state_path = Path(state_dir.name)
    client = TestClient(
        create_app(
            auth_token="test-token",
            settings_state_file=state_path / "session.dat",
            chat_db_path=state_path / "chats.db",
        ),
        base_url="http://127.0.0.1",
        headers={"host": "127.0.0.1"},
    )
    client._state_tmpdir = state_dir  # type: ignore[attr-defined]
    return client


def test_ws_rejects_an_unknown_session_id():
    client = _make_client()
    with pytest.raises(Exception):
        with client.websocket_connect("/ws?session=attacker-chosen-id&token=test-token"):
            pass


def test_ws_accepts_the_default_session_id():
    client = _make_client()
    with client.websocket_connect("/ws?session=default&token=test-token") as ws:
        ws.send_json({"kind": "subscribe", "topics": ["system"]})
        message = ws.receive_json()
    assert message["payload"]["sessionId"] == "default"


def test_ws_reconnect_survives_an_eviction_sweep_racing_the_handshake_accept(monkeypatch):
    """Adversarial-review finding: ws_endpoint's own session-lookup-then-
    attach sequence has exactly one await gap (websocket.accept()) between
    bus.session(session_id) and session.attach(websocket). A session idle
    past the TTL - exactly the state a reconnect after a network blip or
    laptop sleep is already in by design - used to be evictable by the
    free-running background sweep during that gap, orphaning the
    reconnecting client's session (autosave cancelled, removed from
    EventBus._sessions, while session.attach() below still silently
    succeeds on the now-orphaned object anyway). Confirmed via an unforced
    concurrent-asyncio-scheduling stress test to fire in 8/500 trials under
    real production defaults - not a purely theoretical race.

    Forces the race deterministically (not relying on real scheduler
    nondeterminism, which would make this test flaky) by monkeypatching
    WebSocket.accept to run a REAL sweep_idle_sessions() the moment it's
    called - i.e. exactly inside the vulnerable window, on every run, not
    just probabilistically. If ws_endpoint's own `session.idle_since =
    None` fix (set immediately after its bus.session() call, before this
    same await) were ever removed or reordered, this test would fail: the
    sweep would evict "default" here and the assertions below would catch
    it.
    """
    client = _make_client()
    bus = client.app.state.bus

    # Force "default" into the exact state a stale reconnect is already in
    # - idle well past the real create_app() default TTL (300s) - via the
    # SAME direct idle_since manipulation the unit tests above use.
    default_bus = bus.session(DEFAULT_SESSION_ID)
    default_bus.idle_since = time.monotonic() - 10_000

    swept = {"ran": False, "evicted": None}
    real_accept = WebSocket.accept

    async def accept_after_a_racing_sweep(self, *args, **kwargs):
        if not swept["ran"]:
            swept["ran"] = True
            # The real production sweep - not a stand-in - racing the
            # handshake at exactly the vulnerable point.
            swept["evicted"] = bus.sweep_idle_sessions()
        return await real_accept(self, *args, **kwargs)

    monkeypatch.setattr(WebSocket, "accept", accept_after_a_racing_sweep)

    with client.websocket_connect("/ws?session=default&token=test-token") as ws:
        ws.send_json({"kind": "subscribe", "topics": ["system"]})
        message = ws.receive_json()

    assert swept["ran"] is True, "the sweep never actually raced the handshake - this test isn't exercising the window"
    # The fix in action: idle_since was already cleared before accept(),
    # so the racing sweep found nothing eligible to evict.
    assert swept["evicted"] == []
    assert bus.session_ids() == ["default"]
    assert bus.session(DEFAULT_SESSION_ID) is default_bus, "must still be the SAME bus object, not orphaned+recreated"
    assert message["payload"]["sessionId"] == "default"


def test_asset_route_rejects_an_unknown_session_id_as_a_plain_404():
    # ADR-004 stage 4.3: must NOT 500, and must NOT distinguish itself from
    # a genuinely unknown asset id - see backend/assets.py's own comment on
    # why the response shape is deliberately unchanged from before this
    # stage (a bogus session used to silently create an empty document,
    # which would 404 on any asset id anyway).
    #
    # Adversarial-review finding: the response-shape assertions alone
    # cannot tell "rejected before a session was ever created" (the real
    # fix) apart from "a session WAS silently created for the attacker id,
    # and merely happened to 404 on the asset lookup" (the exact pre-stage-
    # 4.3, C6-vulnerable behavior) - both produce byte-identical HTTP
    # responses (confirmed empirically). The session_ids() assertion below
    # is what actually distinguishes them, and is the one that would catch
    # a regression that silently reintroduced the leak on this route.
    client = _make_client()
    bus = client.app.state.bus
    response = client.get(
        "/api/assets/some-asset-id?session=attacker-chosen-id", headers={"Authorization": "Bearer test-token"}
    )
    assert response.status_code == 404
    assert response.json() == {"error": "unknown asset"}
    assert bus.session_ids() == [], "no session may be minted for a rejected id, even though the HTTP response looks identical either way"


def test_chart_export_route_rejects_an_unknown_session_id_as_a_plain_404():
    # Adversarial-review finding: see test_asset_route_rejects_an_unknown_
    # session_id_as_a_plain_404's own comment - the session_ids() check is
    # what actually proves the fix, not the response shape alone.
    client = _make_client()
    bus = client.app.state.bus
    response = client.get(
        "/api/assets/chart/some-node/export?session=attacker-chosen-id",
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 404
    assert response.json() == {"error": "unknown chart"}
    assert bus.session_ids() == []


def _real_session_context():
    """A minimal but real SessionContext - enough for _evict_idle_session
    to exercise its actual agent_dispatcher.has_in_flight_runs() check,
    without spinning up a whole create_app()."""
    from backend.agents import AgentDispatcher
    from backend.canvas import SceneDocument
    from graphlink_settings_store import SettingsManager
    import tempfile
    from pathlib import Path

    state_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    manager = SettingsManager(Path(state_dir.name) / "session.dat")
    dispatcher = AgentDispatcher(manager)
    document = SceneDocument()
    return SessionContext(agent_dispatcher=dispatcher, canvas_document=document), state_dir


def test_evict_idle_session_vetoes_eviction_when_a_run_is_in_flight():
    bus = SessionBus("s1")
    context, _tmp = _real_session_context()
    attach_session_context(bus, context)
    context.agent_dispatcher._runs.claim("chat")  # a real in-flight handle

    assert _evict_idle_session(bus) is False


def test_evict_idle_session_proceeds_and_cancels_the_autosave_task_when_idle():
    async def _run():
        bus = SessionBus("s1")
        context, _tmp = _real_session_context()
        attach_session_context(bus, context)

        async def _never_ending():
            await asyncio.sleep(9999)

        bus.autosave_task = asyncio.create_task(_never_ending())

        result = _evict_idle_session(bus)
        assert result is True
        # Give the cancellation a moment to actually land.
        await asyncio.sleep(0)
        assert bus.autosave_task.cancelled() or bus.autosave_task.done()

    asyncio.run(_run())


def test_evict_idle_session_releases_the_mutation_guard_when_cancelling_mid_write():
    """Adversarial-review finding: the test above only ever cancels a
    stand-in coroutine that never touches backend/chat_library.py's shared
    mutation_guard - it cannot detect a regression in _guarded_tick's own
    try/finally guarantee. Before ADR-004 stage 4.3, register_autosave's
    task was "deliberately never explicitly cancelled" (see
    backend/autosave.py's own updated docstring), so cancelling a REAL
    _guarded_tick while it holds the guard mid-write was purely
    theoretical dead code; this stage makes it a genuine, reachable
    production path (_evict_idle_session cancels a live session's
    autosave_task any time eviction proceeds while a write happens to be
    in flight) for the first time. This test drives that real path: a
    real register_autosave-installed task, actually suspended mid-write
    (inside asyncio.to_thread, not idling in the inter-tick sleep), then
    cancelled via the real _evict_idle_session call.
    """
    import backend.autosave as autosave_mod
    from backend.chat_library import _new_mutation_guard, _new_save_state
    from backend.autosave import register_autosave
    import tempfile
    from pathlib import Path

    async def _run():
        bus = SessionBus("s1")
        context, _tmp = _real_session_context()
        attach_session_context(bus, context)
        context.canvas_document.add_node(0, 0, "hello")
        mutation_guard = _new_mutation_guard()
        last_saved = _new_save_state()
        write_entered = asyncio.Event()
        loop = asyncio.get_running_loop()

        real_write = autosave_mod.save_chat_atomically_row

        def slow_write(*args, **kwargs):
            loop.call_soon_threadsafe(write_entered.set)
            time.sleep(0.3)
            # ADR-009 stage 9.2: (chat_id, updated_at) - any truthy chat_id
            # and a well-formed timestamp string; DB unused for this
            # assertion, but the shape must match the real function's
            # contract so autosave_tick's own unpacking doesn't itself
            # raise (masking this test's actual assertion behind a
            # swallowed TypeError in autosave_tick's own except Exception).
            return 1, "2026-01-01 00:00:00"

        state_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        db_path = Path(state_dir.name) / "chats.db"
        autosave_mod.save_chat_atomically_row = slow_write
        try:
            register_autosave(bus, db_path, context.canvas_document, None, mutation_guard, last_saved, interval_seconds=0.01)
            await asyncio.wait_for(write_entered.wait(), timeout=2.0)
            # The tick is now genuinely suspended mid-write, holding the guard.
            assert mutation_guard["active"] is True
            assert mutation_guard["owner"] == "autosave"

            result = _evict_idle_session(bus)
            assert result is True

            for _ in range(20):
                if bus.autosave_task.done():
                    break
                await asyncio.sleep(0.05)
            assert bus.autosave_task.done()

            # The whole point: cancellation mid-write must still release
            # the guard via _guarded_tick's finally block, or a future
            # manual save/load/new-chat intent on some OTHER session
            # reusing this same guard shape would hang or wrongly report
            # "busy" forever.
            assert mutation_guard["active"] is False
            assert mutation_guard["owner"] is None
            assert mutation_guard["released"].is_set() is True
        finally:
            autosave_mod.save_chat_atomically_row = real_write

    asyncio.run(_run())


def test_evict_idle_session_returns_true_for_a_never_configured_bus():
    # SessionNotConfiguredError branch - a bus that never finished setup
    # has nothing to tear down, so eviction should proceed rather than
    # veto forever.
    bus = SessionBus("s1")
    assert _evict_idle_session(bus) is True


def test_evict_idle_session_disposes_every_live_pycoder_repl():
    """ADR-005 stage 5.3: without this, a REPL subprocess left idle (not
    in-flight - has_in_flight_runs() already vetoed eviction above if one
    were) is orphaned the instant EventBus.sweep_idle_sessions drops this
    SessionBus - nothing else would ever hold a reference able to call
    stop() on it again. get_pycoder_repl without ever calling start()
    mirrors test_canvas.py's own dispose_pycoder_repl tests - there is no
    real subprocess to tear down, only the dict entry, which is exactly
    what this asserts."""
    bus = SessionBus("s1")
    context, _tmp = _real_session_context()
    attach_session_context(bus, context)
    context.agent_dispatcher.get_pycoder_repl("node-1", "repl-1")
    assert "node-1" in context.agent_dispatcher._pycoder_repls

    result = _evict_idle_session(bus)

    assert result is True
    assert context.agent_dispatcher._pycoder_repls == {}, (
        "every live REPL must be torn down before the session itself is evicted"
    )


def test_evict_idle_session_does_not_remove_the_repls_scratch_dir():
    """See AgentDispatcher.dispose_all_pycoder_repls' own docstring:
    eviction means "no one is currently connected", not "discard this
    node's work" - unlike explicit node deletion (test_canvas.py's own
    scratch-dir GC tests), the directory must survive a mere idle eviction
    so a reconnecting user finds their files still there."""
    bus = SessionBus("s1")
    context, _tmp = _real_session_context()
    attach_session_context(bus, context)
    repl = context.agent_dispatcher.get_pycoder_repl("node-1", "repl-1")
    repl.cwd.mkdir(parents=True, exist_ok=True)
    (repl.cwd / "leftover.txt").write_text("data", encoding="utf-8")

    result = _evict_idle_session(bus)

    assert result is True
    assert repl.cwd.is_dir(), "eviction must not delete the REPL's scratch directory"


# -- ADR-009 stage 9.2 / ADR-004 stage 4.3 interaction: flush-before-evict ---


def test_evict_idle_session_flushes_a_dirty_chat_before_teardown(tmp_path):
    """Before this fix, _evict_idle_session cancelled the autosave task
    outright with no final write - any edit made since the last successful
    autosave tick (up to a full interval_seconds, 30s by default) was
    silently lost the instant an idle session was torn down. Confirmed as
    a real, live gap by reading the pre-9.2 _evict_idle_session directly:
    cancel_all -> cancel_all_pending_approvals -> dispose_all_pycoder_repls
    -> autosave_task.cancel(), with no flush anywhere in that sequence.

    Drives the REAL backend.chat_library.register_chat_library wiring (so
    bus.chat_db_path/bus.chat_save_state/bus.chat_mutation_guard are all
    genuinely set, not stand-ins a test constructed by hand) with
    autosave's own background timer explicitly DISABLED
    (autosave_interval_seconds=None) - so only the eviction-time flush
    itself, never a lucky prior tick, could possibly be what persists the
    edit below."""
    from backend.chat_library import get_all_chats, load_chat_row, register_chat_library

    db_path = tmp_path / "chats.db"
    bus = SessionBus("s1")
    context, _tmp = _real_session_context()
    attach_session_context(bus, context)
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    register_chat_library(bus, db_path, context.canvas_document, notifications, autosave_interval_seconds=None)
    assert getattr(bus, "autosave_task", None) is None, (
        "test setup: no background autosave timer should be running"
    )

    context.canvas_document.add_chat_node(0, 0, "unsaved edit", is_user=True)
    assert get_all_chats(db_path) == [], "test setup: nothing saved yet is the whole point of this test"

    result = _evict_idle_session(bus)

    assert result is True
    rows = get_all_chats(db_path)
    assert len(rows) == 1, "the dirty edit must be flushed to disk before the session is torn down"
    row = load_chat_row(db_path, rows[0]["id"])
    assert row["data"]["nodes"][0]["raw_content"] == "unsaved edit"


def test_evict_idle_session_does_not_write_a_redundant_row_for_a_clean_session(tmp_path):
    # The flush must honor the SAME change-guard autosave_tick's own docstring
    # establishes - a session that has nothing unsaved must not get a
    # pointless extra write (and re-sorted Chat Library) just because it
    # happened to go idle.
    from backend.chat_library import get_all_chats, register_chat_library

    db_path = tmp_path / "chats.db"
    bus = SessionBus("s1")
    context, _tmp = _real_session_context()
    attach_session_context(bus, context)
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    register_chat_library(bus, db_path, context.canvas_document, notifications, autosave_interval_seconds=None)

    asyncio.run(bus.dispatch_intent("app-chat-library", "saveChat", []))
    assert bus.chat_save_state["digest"] is None, (
        "test setup: an empty, never-touched canvas has nothing to save at all"
    )
    assert get_all_chats(db_path) == []

    context.canvas_document.add_chat_node(0, 0, "hello", is_user=True)
    asyncio.run(bus.dispatch_intent("app-chat-library", "saveChat", []))
    saved_updated_at = bus.chat_save_state["updated_at"]
    assert len(get_all_chats(db_path)) == 1

    result = _evict_idle_session(bus)

    assert result is True
    rows = get_all_chats(db_path)
    assert len(rows) == 1, "eviction must not duplicate the already-saved row"
    assert rows[0]["updatedAtIso"] is not None
    # The row's own updated_at must be byte-identical to what the manual
    # Save already wrote - a redundant flush write would have bumped it.
    from backend.chat_library import load_chat_row
    assert load_chat_row(db_path, rows[0]["id"])["updated_at"] == saved_updated_at


def test_evict_idle_session_does_not_flush_while_a_write_is_genuinely_in_flight(tmp_path):
    # If the mutation guard is already held (a tick or manual op mid-write),
    # the flush must not race a SECOND write against it - autosave_task.
    # cancel() below still lets any genuinely in-flight tick finish and
    # record its own result correctly (see
    # test_evict_idle_session_releases_the_mutation_guard_when_cancelling_
    # mid_write above), so attempting a flush here would only risk an
    # avoidable spurious lost-race warning for no real benefit.
    from backend.chat_library import register_chat_library

    db_path = tmp_path / "chats.db"
    bus = SessionBus("s1")
    context, _tmp = _real_session_context()
    attach_session_context(bus, context)
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    register_chat_library(bus, db_path, context.canvas_document, notifications, autosave_interval_seconds=None)
    context.canvas_document.add_chat_node(0, 0, "unsaved edit", is_user=True)

    bus.chat_mutation_guard["active"] = True
    bus.chat_mutation_guard["owner"] = "autosave"

    import backend.app as app_module

    flush_calls = []
    real_flush = app_module.flush_dirty_session_before_teardown
    app_module.flush_dirty_session_before_teardown = lambda *a, **k: flush_calls.append(a)
    try:
        result = _evict_idle_session(bus)
    finally:
        app_module.flush_dirty_session_before_teardown = real_flush

    assert result is True
    assert flush_calls == [], "a flush must not be attempted while a write is already in flight"
