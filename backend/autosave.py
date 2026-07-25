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
skips the write entirely if it matches the hash from the last successful
save (auto OR manual - see register_autosave's own `last_hash` argument,
shared with nothing else, this module's own private bookkeeping). Without
this, a session with no activity at all would still re-write its own
unchanged row to disk (and re-publish app-chat-library, causing a pointless
list re-render if the library dialog happens to be open) every single
interval, forever, for the entire remaining lifetime of the process.

SILENT ON SUCCESS, LOUD ON FAILURE: unlike the explicit Save button (which
shows a 'Saved "..."' toast every time - the user just took an action and
expects confirmation), a successful autosave tick publishes no notification
at all - matching the common editor convention (VS Code, Google Docs) that
a background save should not interrupt anyone. A FAILED autosave tick DOES
surface a real notification, since silently failing to protect the user's
work would defeat the entire point of this feature.

CONCURRENCY: shares the SAME mutation_guard dict backend/chat_library.py's
own loadChat/saveChat/newChat intents already use (see that module's own
docstring on _serialize_mutating_intent) - an autosave tick that fires while
a manual load/save/new-chat is already in flight skips itself for this
interval rather than racing it (there will be another tick along in
`interval_seconds`, so skipping one is free; racing a manual operation is
not).

TASK LIFETIME: deliberately never explicitly cancelled. Every SessionBus
this backend ever creates lives for the remaining lifetime of the process
once created - EventBus never removes an entry from its own `_sessions`
dict, even once every WS connection to it detaches (see backend/events.py) -
so there is no "session ended" event anywhere in this codebase to hook a
cancellation to in the first place; this task's own lifetime already
matches every other piece of a session's state (SceneDocument, ComposerDocument,
etc.), none of which get torn down early either. This is a pre-existing,
accepted characteristic of a single-user desktop-shell backend (pywebview),
not a new leak introduced here - a multi-tenant web server would need real
session eviction, this does not.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from backend.canvas import SceneDocument
from backend.chat_library import _fallback_title, _resolve_seed_message, load_chat_row, save_chat_atomically_row
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.session_save import build_chat_data

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 30.0


def _content_digest(chat_data: dict[str, Any], notes_data: list, pins_data: list) -> str:
    """A pure function of "what would actually get written" - sort_keys
    makes this independent of dict insertion order (build_chat_data's own
    key order never changes call to call, but this is cheap insurance
    against a false "changed" from something that isn't); default=str
    tolerates any value json can't natively encode without ever raising
    (a digest mismatch on an unexpected type just means "assume changed,
    write it" - the safe direction to fail in, never "assume unchanged,
    skip a real write")."""
    payload = json.dumps(
        {"chat_data": chat_data, "notes_data": notes_data, "pins_data": pins_data},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def autosave_tick(
    bus: SessionBus,
    db_path: Path,
    canvas_document: SceneDocument,
    notifications: NotificationState | None,
    last_hash: dict[str, str | None],
) -> None:
    """One autosave attempt - directly callable (no timer involved), so
    tests can exercise the actual decision/write logic deterministically
    rather than waiting on a real sleep. `last_hash` is a single-key mutable
    dict (`{"value": ...}`) rather than a plain variable so register_autosave's
    closure and this function can share and update the same cell across
    calls without a class just for one field."""
    if not canvas_document.nodes and canvas_document.current_chat_id is None:
        # Mirrors saveChat's own "Nothing was added to the chat canvas yet"
        # guard - an empty, never-saved canvas has nothing worth protecting.
        return

    try:
        chat_data = build_chat_data(canvas_document)
    except Exception:
        logger.exception("autosave: failed to build chat data for session %r", bus.session_id)
        return

    notes_data = chat_data.pop("notes_data", [])
    pins_data = chat_data.pop("pins_data", [])

    digest = _content_digest(chat_data, notes_data, pins_data)
    if digest == last_hash["value"]:
        return

    chat_id_for_save: int | None = None
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
        else:
            title = _fallback_title(_resolve_seed_message(canvas_document))
    else:
        title = _fallback_title(_resolve_seed_message(canvas_document))

    try:
        new_chat_id = await asyncio.to_thread(
            save_chat_atomically_row, db_path, chat_id_for_save, title, chat_data, notes_data, pins_data,
        )
    except Exception as exc:
        logger.exception("autosave: DB write failed for session %r", bus.session_id)
        if notifications is not None:
            notifications.show(f"Autosave failed: {exc}", "error")
            await bus.publish("notification")
        return

    canvas_document.current_chat_id = int(new_chat_id)
    last_hash["value"] = digest
    await bus.publish("app-chat-library")


def register_autosave(
    bus: SessionBus,
    db_path: Path,
    canvas_document: SceneDocument,
    notifications: NotificationState | None,
    mutation_guard: dict[str, bool],
    *,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
) -> None:
    """Starts the one long-lived per-session background task. Stashed on
    `bus.autosave_task` purely so a caller COULD cancel it (e.g. a future
    test or a graceful-shutdown path) - nothing in this backend does today,
    see this module's own docstring on why that is fine for now."""
    last_hash: dict[str, str | None] = {"value": None}

    async def _guarded_tick() -> None:
        if mutation_guard["active"]:
            # A manual load/save/new-chat is already in flight - skip this
            # interval entirely rather than racing it; the next tick will
            # try again in `interval_seconds`.
            return
        mutation_guard["active"] = True
        try:
            await autosave_tick(bus, db_path, canvas_document, notifications, last_hash)
        finally:
            mutation_guard["active"] = False

    async def _loop() -> None:
        while True:
            await asyncio.sleep(interval_seconds)
            await _guarded_tick()

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
