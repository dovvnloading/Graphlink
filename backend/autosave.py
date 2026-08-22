"""Autosave (Qt-removal plan R6.6) - a NET-NEW capability, not a port.

Confirmed during R6.5's own recon: no QTimer-based (or any other) autosave
mechanism exists anywhere in graphlink_app/ - every save there is a single
explicit user action (Ctrl+S / the toolbar Save button), see
backend/session_save.py's own module docstring for the exact grep-confirmed
citation. There is nothing here to reimplement faithfully; this module's
job is to build a reasonable NEW capability on top of R6.5's already-real
save primitive, not to match legacy behavior that never existed.

DESIGN: one long-lived background asyncio task per session, ticking every
`interval_seconds` (default 30s - aggressive enough that a crash/power-loss/
forgotten-Ctrl+S never loses more than half a minute of real work, without
writing to disk so often it becomes wasteful for a large session). Each tick
reuses build_chat_data/save_chat_atomically_row DIRECTLY - the exact same
primitives backend/chat_library.py's own explicit saveChat intent calls -
rather than duplicating any serialization or DB-write logic.

CHANGE-GUARDED, not blind: a tick hashes the about-to-be-written JSON and
skips the write when BOTH that hash and the current chat_id still match what
was last put on disk. Without this, a session with no activity at all would
still re-write its own unchanged row to disk (and re-publish
app-chat-library, causing a pointless list re-render if the library dialog
happens to be open) every single interval, forever, for the entire remaining
lifetime of the process.

That "what was last put on disk" cell is `last_saved`, a real argument passed
in by backend/chat_library.py's register_chat_library and genuinely shared
with the manual saveChat/loadChat/deleteChat paths - see _new_save_state's
own docstring there for its shape, and for the two bugs an audit found in
the original version of this guard, which owned the cell privately (nothing
else could seed it) and compared content only (so a row deleted underneath
the session still read as "already saved", silently ending all autosave
protection for that session).

SILENT ON SUCCESS, LOUD ON FAILURE: unlike the explicit Save button (which
shows a 'Saved "..."' toast every time - the user just took an action and
expects confirmation), a successful autosave tick publishes no notification
at all - matching the common editor convention (VS Code, Google Docs) that
a background save should not interrupt anyone. A FAILED autosave tick DOES
surface a real notification, since silently failing to protect the user's
work would defeat the entire point of this feature.

CONCURRENCY: shares the SAME mutation_guard dict backend/chat_library.py's
own loadChat/saveChat/newChat/renameChat intents use (see that module's own
docstring on _serialize_mutating_intent) - an autosave tick that fires while
a manual load/save/new-chat/rename is already in flight skips itself for
this interval rather than racing it (there will be another tick along in
`interval_seconds`, so skipping one is free; racing a manual operation is
not).

TASK LIFETIME (updated, ADR-004 stage 4.3): this task now DOES get
explicitly cancelled - by backend/app.py's own _evict_idle_session, the
teardown callback EventBus.sweep_idle_sessions() (backend/events.py) calls
for any session idle (zero connections) for its TTL with no in-flight
agent run. Before stage 4.3, this section documented the opposite as a
deliberately-accepted characteristic ("every SessionBus lives for the
remaining lifetime of the process... a multi-tenant web server would need
real session eviction, this does not") - that was true when written (no
"session ended" event existed anywhere to hook a cancellation to), but it
was also the OTHER half of audit finding C6 alongside unbounded session
creation: an idle session's task keeps ticking forever, its closure
holding the whole SceneDocument alive via a strong reference nothing can
ever reach again. Eviction closes both halves together. Preserved here as
a record of the prior reasoning, not silently deleted - see this session's
own "no room for error, document mistakes rather than quietly editing"
discipline.

Eviction cancels this task only after the mutation guard is inactive. That
ordering is data-safety-critical: cancellation during autosave_tick's
pre-write load/backup awaits could otherwise stop the only dirty copy before
the database write begins. _guarded_tick's try/finally still guarantees the
guard itself is released on cancellation or any other exit, but guard cleanup
alone is not evidence that the user's pending write completed.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from backend.asset_store import store_for
from backend.canvas import SceneDocument
from backend.chat_library import (
    AUTOSAVE_OWNER,
    LOST_RACE_MESSAGE_AUTOSAVE,
    ConcurrentSaveConflict,
    _content_digest,
    _fallback_title,
    _maybe_backup_before_write,
    _resolve_seed_message,
    load_chat_row,
    save_chat_atomically_row,
)
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.session_save import build_chat_data
from graphlink_settings_store import SettingsManager

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 30.0


async def autosave_tick(
    bus: SessionBus,
    db_path: Path,
    canvas_document: SceneDocument,
    notifications: NotificationState | None,
    last_saved: dict[str, Any],
    settings_manager: "SettingsManager | None" = None,
) -> None:
    """One autosave attempt - directly callable (no timer involved), so
    tests can exercise the actual decision/write logic deterministically
    rather than waiting on a real sleep. `last_saved` is the mutable cell
    backend/chat_library.py's _new_save_state() creates and every write path
    shares (see this module's own docstring, and that function's).

    ADR-014 review-fix: `settings_manager`, when given, is threaded to
    build_chat_data so a plugin's own serialize hook is gated on its
    current Settings > Plugins grant on every autosave tick too, not just
    a manual Save - see session_save.py's _serialize_plugin_node for the
    actual check. `None` (this parameter's own default) preserves the
    exact prior ungated behavior."""
    if not canvas_document.nodes and canvas_document.current_chat_id is None:
        # Mirrors saveChat's own "Nothing was added to the chat canvas yet"
        # guard - an empty, never-saved canvas has nothing worth protecting.
        return

    try:
        # ADR-009 stage 9.5: image bytes go to the content-addressed store
        # and only a ref is written into the row. This path is the whole
        # reason that stage exists - without it every 30-second tick
        # rewrote megabytes of base64 for pictures that had not changed.
        chat_data = build_chat_data(
            canvas_document, asset_store=store_for(db_path), settings_manager=settings_manager,
        )
    except Exception as exc:
        logger.exception("autosave: failed to build chat data for session %r", bus.session_id)
        # LOUD ON FAILURE (see this module's own docstring) - this branch used
        # to log and return silently, every tick, forever. The most likely way
        # to reach it is AssetStore.put() raising OSError because the assets
        # directory is unwritable or the disk is full, which is precisely the
        # moment the user most needs to know autosave has stopped protecting
        # their work. The manual Save path already notifies for the same
        # failure; this one now matches it.
        if notifications is not None:
            notifications.show(f"Autosave failed: {exc}", "error")
            await bus.publish("notification")
        return

    notes_data = chat_data.pop("notes_data", [])
    pins_data = chat_data.pop("pins_data", [])

    try:
        digest = _content_digest(chat_data, notes_data, pins_data)
    except Exception as exc:
        # _content_digest json.dumps(..., sort_keys=True) the payload, which
        # raises on a dict with mixed-type keys - a shape a plugin's own
        # serialize hook can produce and that session_save's json validation
        # accepts (it does not require sort-ability). Escaping here would kill
        # this tick outside every try below, so the guard belongs here too,
        # and it reports rather than dying quietly.
        logger.exception("autosave: failed to digest chat data for session %r", bus.session_id)
        if notifications is not None:
            notifications.show(f"Autosave failed: {exc}", "error")
            await bus.publish("notification")
        return
    if digest == last_saved["digest"] and canvas_document.current_chat_id == last_saved["chat_id"]:
        # Both halves matter. Content alone is not enough: chats.db is shared
        # mutable state, and a row that vanished underneath this session (the
        # user deleting their own open chat from the library) leaves the
        # document's content digest completely unchanged - comparing chat_id
        # too is what stops that from reading as "already saved" forever.
        return

    chat_id_for_save: int | None = None
    # ADR-009 stage 9.2: what THIS session believes is on disk for the chat
    # it's about to (re)save - see backend/chat_library.py's own saveChat
    # closure for the identical reasoning (only trusted when it actually
    # describes the SAME row; otherwise expected_updated_at stays None and
    # save_chat_atomically_row falls back to a blind UPDATE, matching this
    # function's pre-9.2 behavior for that case).
    expected_updated_at: str | None = None
    current_id = canvas_document.current_chat_id
    if current_id:
        try:
            existing_row = await asyncio.to_thread(load_chat_row, db_path, int(current_id))
        except Exception as exc:
            # Adversarial review finding: this lookup was previously
            # unwrapped. An uncaught exception here (e.g. chats.db
            # transiently locked/unreadable) would propagate out of
            # autosave_tick, through _guarded_tick's try/finally (which
            # only protects mutation_guard, not the task itself), and kill
            # register_autosave's own _loop task outright - silently and
            # PERMANENTLY disabling autosave for the rest of this session's
            # process lifetime, with no error ever surfaced to the user.
            # That directly violates this module's own "LOUD ON FAILURE"
            # principle, which must cover every failure mode a tick can
            # hit, not just the final DB write.
            logger.exception("autosave: failed to read existing chat row for session %r", bus.session_id)
            if notifications is not None:
                notifications.show(f"Autosave failed: {exc}", "error")
                await bus.publish("notification")
            return
        if existing_row is not None:
            # Never regenerate an existing chat's title - same rule
            # saveChat's own resave path follows.
            title = str(existing_row.get("title") or "Untitled")
            chat_id_for_save = int(current_id)
            if last_saved.get("chat_id") == int(current_id):
                expected_updated_at = last_saved.get("updated_at")
        else:
            title = _fallback_title(_resolve_seed_message(canvas_document))
    else:
        title = _fallback_title(_resolve_seed_message(canvas_document))

    try:
        # ADR-009 stage 9.2: backup-before-write, the SAME call
        # backend/chat_library.py's own saveChat closure makes - see
        # _maybe_backup_before_write's own docstring for why this one call
        # covers both "before the first mutating write of a session" and
        # the ongoing periodic cadence, reusing THIS tick loop as the clock
        # rather than a second timer. Best-effort: a backup failure is
        # logged inside that function and never raised.
        await asyncio.to_thread(_maybe_backup_before_write, db_path, last_saved)
        new_chat_id, new_updated_at = await asyncio.to_thread(
            save_chat_atomically_row, db_path, chat_id_for_save, title, chat_data, notes_data, pins_data,
            expected_updated_at=expected_updated_at,
            # ADR-020 stage 20.2: an autosave tick can be the FIRST write of
            # a session that started with an explicit New Chat -> pick-a-
            # workspace, then never got around to a manual Save - see
            # backend/chat_library.py's own save_chat closure for the
            # identical reasoning (only consulted on save_chat_atomically_
            # row's own INSERT branch; a resave of an existing graph ignores
            # this value entirely).
            workspace_id=canvas_document.current_workspace_id,
        )
    except ConcurrentSaveConflict:
        # ADR-009 stage 9.2 exit criterion: a lost autosave race must
        # neither clobber the newer version NOR crash this tick/the loop -
        # last_saved is deliberately left untouched (still pointing at the
        # STALE updated_at), so the very next tick detects the same
        # conflict again rather than silently believing it caught up.
        logger.warning(
            "autosave: lost a save race for chat %r (session=%r) - not clobbering the newer version",
            current_id, bus.session_id,
        )
        if notifications is not None:
            notifications.show(LOST_RACE_MESSAGE_AUTOSAVE, "warning")
            await bus.publish("notification")
        return
    except Exception as exc:
        logger.exception("autosave: DB write failed for session %r", bus.session_id)
        if notifications is not None:
            notifications.show(f"Autosave failed: {exc}", "error")
            await bus.publish("notification")
        return

    canvas_document.current_chat_id = int(new_chat_id)
    last_saved["digest"] = digest
    last_saved["chat_id"] = int(new_chat_id)
    last_saved["updated_at"] = new_updated_at
    await bus.publish("app-chat-library")


def register_autosave(
    bus: SessionBus,
    db_path: Path,
    canvas_document: SceneDocument,
    notifications: NotificationState | None,
    mutation_guard: dict[str, bool],
    last_saved: dict[str, Any],
    *,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    settings_manager: "SettingsManager | None" = None,
) -> None:
    """Starts the one long-lived per-session background task. Stashed on
    `bus.autosave_task` purely so a caller COULD cancel it (e.g. a future
    test or a graceful-shutdown path) - nothing in this backend does today,
    see this module's own docstring on why that is fine for now.

    `last_saved` is owned by the caller (register_chat_library), not created
    here, precisely so the manual save/load/delete paths can seed and
    invalidate the same cell - see this module's own CHANGE-GUARDED
    paragraph.

    ADR-014 review-fix: `settings_manager` is threaded straight through to
    autosave_tick - see that function's own docstring."""

    async def _guarded_tick() -> None:
        if mutation_guard["active"]:
            # A manual load/save/new-chat is already in flight - skip this
            # interval entirely rather than racing it; the next tick will
            # try again in `interval_seconds`.
            return
        # Audit fix: claim the guard as AUTOSAVE, not anonymously. A user
        # intent arriving mid-tick used to be discarded outright, warned about
        # an operation they never started; knowing an unattended tick is the
        # holder is what lets chat_library.py's _serialize_mutating_intent
        # wait the tick out and honor the click instead. Releasing must also
        # SIGNAL, or a waiting intent would sit out its whole timeout for a
        # tick that already finished.
        mutation_guard["active"] = True
        mutation_guard["owner"] = AUTOSAVE_OWNER
        mutation_guard["released"].clear()
        try:
            await autosave_tick(bus, db_path, canvas_document, notifications, last_saved, settings_manager)
        finally:
            mutation_guard["active"] = False
            mutation_guard["owner"] = None
            mutation_guard["released"].set()

    async def _loop() -> None:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await _guarded_tick()
            except Exception:
                # Defence in depth, not a fix for a known escape: an audit
                # walked every unguarded line in autosave_tick and found no
                # reachable one that can raise today. But this task is never
                # awaited by anyone and holds a strong reference for the
                # process's whole life, so if one ever did escape, the task
                # would die and Python's own "Task exception was never
                # retrieved" warning would never fire either - autosave would
                # be off for the rest of the session with literally no signal
                # anywhere. One bad tick must never be able to end the loop.
                logger.exception("autosave: tick failed for session %r", bus.session_id)

    # Exposed for the same reason bus.autosave_task and bus.chat_save_state
    # are: a closure-local is unreachable to the tests that have to prove the
    # real claim/release/signal wiring works. Driving ONE tick directly is
    # what lets the user-save-yield test be deterministic - racing the
    # free-running loop instead means asserting a wall-clock bound against a
    # loop that re-claims every interval, which is exactly how that test
    # turned flaky on a contended CI runner.
    bus.autosave_guarded_tick = _guarded_tick

    loop_coro = _loop()
    try:
        bus.autosave_task = asyncio.create_task(loop_coro)
    except RuntimeError:
        # A background convenience feature must never be able to take down
        # session initialization itself. _configure_session (this function's
        # own caller, via backend/chat_library.py) is NOT guaranteed to
        # always run with a running event loop - confirmed directly:
        # Starlette's TestClient drives its own WebSocket handshake tests
        # (backend/tests/test_ws_origin.py, test_assets.py) through a sync
        # bridge that constructs a session bus outside of any running loop
        # at this exact call site, even though the REAL production path
        # (ws_endpoint, an async function the ASGI server itself awaits)
        # always has one. Rather than let that surface as a fatal
        # RuntimeError crashing EVERY session-configuring test in the repo
        # (which is exactly what happened here before this fix), autosave
        # simply stays off for a session created this way - a missing
        # background save is a far smaller problem than an unusable
        # session/test harness.
        logger.warning(
            "autosave: no running event loop at registration time for session %r - autosave disabled",
            bus.session_id,
        )
        # create_task() raised before scheduling the coroutine, so it was
        # never going to run and never going to close itself either -
        # closing it explicitly here is what silences Python's own
        # "coroutine was never awaited" RuntimeWarning.
        loop_coro.close()
        bus.autosave_task = None
