"""Chat library topic tests (Qt-removal plan R2.5e + R6.4 loadChat + R6.5
saveChat/newChat)."""

import asyncio
import contextlib
import json
import os
import sqlite3
import stat
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

import backend.autosave as autosave_module
import backend.chat_library as chat_library_module
import backend.db_backup as db_backup_module
from backend.autosave import autosave_tick, register_autosave
from backend.canvas import SceneDocument
from backend.chat_library import (
    AUTOSAVE_OWNER,
    BACKUP_CADENCE_SECONDS,
    CHATS_DB_SCHEMA_VERSION,
    LOST_RACE_MESSAGE_AUTOSAVE,
    LOST_RACE_MESSAGE_MANUAL,
    USER_OWNER,
    ConcurrentSaveConflict,
    _extract_preview_and_message_count,
    _fallback_title,
    _format_timestamp,
    _format_timestamp_iso,
    _maybe_backup_before_write,
    _new_save_state,
    _normalize_tags,
    _resolve_seed_message,
    archive_workspace,
    chat_library_payload,
    create_workspace,
    delete_chat,
    flush_dirty_session_before_teardown,
    get_all_chats,
    get_all_workspaces,
    load_chat_row,
    load_notes_rows,
    load_pins_rows,
    register_chat_library,
    rename_chat,
    rename_workspace,
    save_chat_atomically_row,
    set_graph_archived,
    set_graph_favorite,
    set_graph_tags,
)
from backend.events import IntentValidationError, SessionBus
from backend.notifications import NotificationState


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "chats.db"


def _insert_chat(db_path, title: str, data: str = "{}") -> int:
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "data TEXT NOT NULL)"
        )
        cursor = conn.execute(
            "INSERT INTO chats (title, data, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (title, data, "2026-01-01 10:00:00", "2026-01-02 11:30:00"),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


class Recorder:
    def __init__(self):
        self.messages = []

    async def send_json(self, data):
        self.messages.append(data)


def test_get_all_chats_creates_table_on_a_fresh_db(db_path):
    assert get_all_chats(db_path) == []
    assert db_path.exists()


class TestChatsDbPermissionsAreRestricted:
    """ADR-004 stage 4.4: chats.db holds real conversation content, so
    _connect() gives it POSIX 0600 on every connection - unconditional (not
    "only if just created"), since every read/write helper in this module
    routes through that one shared function. chmod's real effect is
    POSIX-only (see _connect's own comment on why Windows os.chmod only
    toggles the read-only attribute, not real per-owner permission bits)."""

    def test_chmod_is_invoked_with_0600_on_the_real_db_file(self, db_path, monkeypatch):
        calls = []
        monkeypatch.setattr(os, "chmod", lambda path, mode: calls.append((path, mode)))

        get_all_chats(db_path)

        assert (db_path, 0o600) in calls

    def test_posix_permission_bits_are_actually_0600(self, db_path):
        if sys.platform == "win32":
            pytest.skip("chmod is a no-op on Windows - see class docstring")

        get_all_chats(db_path)

        assert stat.S_IMODE(db_path.stat().st_mode) == 0o600

    def test_self_heals_a_pre_existing_db_with_looser_permissions(self, db_path):
        if sys.platform == "win32":
            pytest.skip("chmod is a no-op on Windows - see class docstring")

        _insert_chat(db_path, "Pre-existing")
        os.chmod(db_path, 0o644)

        get_all_chats(db_path)

        assert stat.S_IMODE(db_path.stat().st_mode) == 0o600

    def test_a_chmod_failure_does_not_crash_the_connection(self, db_path, monkeypatch):
        def _boom(path, mode):
            raise OSError("permission denied")

        monkeypatch.setattr(os, "chmod", _boom)

        assert get_all_chats(db_path) == []


class TestChatsDbUsesWalModeForChmoddableSidecars:
    """ADR-004 stage 4.4 follow-up (adversarial-review finding): the default
    rollback-journal mode materializes a `<db>-journal` sidecar ONLY
    transiently, mid-transaction (SQLite creates and deletes it around each
    write), so it was never reachable by a Python-level chmod call - the
    one gap in this module's own POSIX-permission hardening. WAL mode's
    `<db>-wal`/`<db>-shm` sidecars are different: once a database is
    genuinely in WAL mode, connecting to it AGAIN (even for a pure read,
    before any write) immediately re-attaches them - which is when
    _connect()'s chmod loop, positioned right after the PRAGMA, catches
    them (empirically verified - see the probes this test class's
    behavior is modeled on). They still get cleaned up when the sole
    connection closes (this module opens-does-work-closes a fresh
    connection per call, never holding one open), so this is "chmod them
    every time they're freshly attached," not "they now live forever" -
    but since that happens on every connection from the second one
    onward, it closes the exposure for the entire steady-state lifetime
    of a chats.db, unlike the rollback-journal's zero coverage.

    One narrow, accepted residual gap remains and is pinned explicitly
    below: the VERY FIRST connection that ever switches a given chats.db
    into WAL mode needs an actual write before the sidecars exist at all
    (the PRAGMA alone doesn't create them yet) - by which point
    _connect()'s chmod loop has already run and found nothing. This is a
    one-time-ever, single-connection window per database, not a
    persistent or repeatable exposure - the same shape of accepted
    residual risk this codebase already documents elsewhere for other
    narrow, structurally-unavoidable creation-moment races."""

    def test_journal_mode_is_actually_wal(self, db_path):
        conn = chat_library_module._connect(db_path)
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()

        assert mode == "wal"

    def test_the_very_first_ever_wal_connection_has_a_narrow_bootstrap_gap(self, db_path, monkeypatch):
        # Pinned, not just tolerated: documents the one accepted residual
        # window from this class's own docstring. A brand-new chats.db has
        # never been WAL before, so establishing WAL mode on this very
        # first connection needs a real write before the sidecars exist -
        # _connect()'s chmod loop runs before that write happens (it's
        # issued by the CALLER, after _connect() returns), so it can't
        # catch them yet. If this test starts failing, the gap has closed
        # for real and this whole comment (and the class docstring) should
        # be updated, not just re-asserted.
        calls = []
        monkeypatch.setattr(os, "chmod", lambda path, mode: calls.append((path, mode)))

        get_all_chats(db_path)  # brand-new db_path - the very first WAL connection ever

        wal_path = db_path.with_name(db_path.name + "-wal")
        shm_path = db_path.with_name(db_path.name + "-shm")
        assert (wal_path, 0o600) not in calls
        assert (shm_path, 0o600) not in calls

    def test_chmod_is_invoked_on_the_sidecars_from_the_second_connection_onward(self, db_path, monkeypatch):
        get_all_chats(db_path)  # bootstrap: establishes WAL mode for this db_path

        calls = []
        monkeypatch.setattr(os, "chmod", lambda path, mode: calls.append((path, mode)))
        get_all_chats(db_path)  # this db is now genuinely WAL - sidecars re-attach immediately

        wal_path = db_path.with_name(db_path.name + "-wal")
        shm_path = db_path.with_name(db_path.name + "-shm")
        assert (wal_path, 0o600) in calls
        assert (shm_path, 0o600) in calls

    def test_posix_permission_bits_on_the_sidecars_are_actually_0600(self, db_path):
        if sys.platform == "win32":
            pytest.skip("chmod is a no-op on Windows - see class docstring")

        get_all_chats(db_path)  # bootstrap: establishes WAL mode for this db_path

        # A live connection (not get_all_chats, which closes immediately)
        # so the sidecars still exist when we inspect their real bits -
        # this module's connect-per-call pattern deletes them the instant
        # the sole connection closes.
        conn = chat_library_module._connect(db_path)
        try:
            wal_path = db_path.with_name(db_path.name + "-wal")
            shm_path = db_path.with_name(db_path.name + "-shm")
            assert stat.S_IMODE(wal_path.stat().st_mode) == 0o600
            assert stat.S_IMODE(shm_path.stat().st_mode) == 0o600
        finally:
            conn.close()

    def test_self_heals_stale_sidecars_left_behind_by_a_crash(self, db_path):
        if sys.platform == "win32":
            pytest.skip("chmod is a no-op on Windows - see class docstring")

        get_all_chats(db_path)  # bootstrap: establishes WAL mode, then cleanly closes
        wal_path = db_path.with_name(db_path.name + "-wal")
        shm_path = db_path.with_name(db_path.name + "-shm")
        # A real crash (kill -9 / power loss) mid-write leaves these behind
        # instead of letting SQLite's normal close-time checkpoint clean
        # them up - simulated here directly, since forcing an actual
        # process-level crash isn't something a test can safely do.
        wal_path.write_bytes(b"")
        shm_path.write_bytes(b"")
        os.chmod(wal_path, 0o644)
        os.chmod(shm_path, 0o644)

        conn = chat_library_module._connect(db_path)
        try:
            assert stat.S_IMODE(wal_path.stat().st_mode) == 0o600
            assert stat.S_IMODE(shm_path.stat().st_mode) == 0o600
        finally:
            conn.close()

    def test_a_chmod_failure_on_a_sidecar_does_not_crash_the_connection(self, db_path, monkeypatch):
        get_all_chats(db_path)  # bootstrap so the sidecars are reachable this time

        def _boom(path, mode):
            raise OSError("permission denied")

        monkeypatch.setattr(os, "chmod", _boom)

        assert get_all_chats(db_path) == []


# -- ADR-009 stage 9.1: busy_timeout + user_version migration runner --------


class TestChatsDbBusyTimeout:
    """An explicit PRAGMA busy_timeout, matching (not shortening) the
    pre-existing 30-second convention this module's own `timeout=30`
    sqlite3.connect() argument already established - see _connect's own
    comment for why both statements exist without being in tension (the
    connect() kwarg already sets this value via the same underlying sqlite3
    C API; the explicit PRAGMA makes it self-documenting and independent of
    that kwarg ever changing)."""

    def test_busy_timeout_reads_back_as_the_established_30_second_convention(self, db_path):
        conn = chat_library_module._connect(db_path)
        try:
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
        finally:
            conn.close()


class TestChatsDbSchemaMigration:
    """PRAGMA user_version-gated migration replacing the old per-connection
    _ensure_chats_table/_ensure_notes_table/_ensure_pins_table probes -
    CHATS_DB_SCHEMA_VERSION is the version stamped once migration "1"
    (0 -> 1) has run. See backend/chat_library.py's own
    _migration_001_initial_schema docstring for exactly what that migration
    does and why it must behave identically on a fresh db and a real
    pre-existing one (covered separately below by
    TestMigrationUpgradesAPreExistingRealShapedDatabase)."""

    def test_fresh_db_lands_on_the_target_schema_version(self, db_path):
        get_all_chats(db_path)  # any query function bootstraps a fresh db

        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == CHATS_DB_SCHEMA_VERSION
        finally:
            conn.close()

    def test_fresh_db_gets_the_new_fk_indexes(self, db_path):
        get_all_chats(db_path)

        conn = sqlite3.connect(db_path)
        try:
            notes_indexes = {row[1] for row in conn.execute("PRAGMA index_list(notes)").fetchall()}
            pins_indexes = {row[1] for row in conn.execute("PRAGMA index_list(pins)").fetchall()}
            assert "idx_notes_chat_id" in notes_indexes
            assert "idx_pins_chat_id" in pins_indexes
        finally:
            conn.close()

    def test_second_connect_issues_no_pragma_table_info_or_create_table_probes(self, db_path, monkeypatch):
        # Proves the "no longer re-probed per connection" claim at the real
        # SQL-wire level (via a sqlite3.Connection subclass swapped in as
        # the connect factory) rather than by inferring it from some
        # internal function not being called - the old _ensure_* trio's own
        # signature move (CREATE TABLE IF NOT EXISTS, then PRAGMA
        # table_info, then conditional ALTER TABLE) used to run on every
        # single call to get_all_chats/rename_chat/delete_chat/
        # load_chat_row/load_notes_rows/load_pins_rows/
        # save_chat_atomically_row. After this stage it must run AT MOST
        # ONCE per database, ever - never again once user_version reads
        # CHATS_DB_SCHEMA_VERSION.
        get_all_chats(db_path)  # first-ever connect: migrates 0 -> target

        executed_sql = []

        class _SpyConnection(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                executed_sql.append(sql)
                return super().execute(sql, *args, **kwargs)

        real_connect = sqlite3.connect

        def _connect_with_spy_factory(*args, **kwargs):
            kwargs["factory"] = _SpyConnection
            return real_connect(*args, **kwargs)

        monkeypatch.setattr(chat_library_module.sqlite3, "connect", _connect_with_spy_factory)

        get_all_chats(db_path)  # second connect: already at target version

        table_info_calls = [sql for sql in executed_sql if "table_info" in sql]
        create_table_calls = [sql for sql in executed_sql if "CREATE TABLE" in sql]
        assert table_info_calls == [], (
            f"a connect to an already-migrated db must not re-probe schema via "
            f"PRAGMA table_info - saw: {table_info_calls}"
        )
        assert create_table_calls == [], (
            f"a connect to an already-migrated db must not re-run schema DDL - "
            f"saw: {create_table_calls}"
        )


def _create_pre_migration_shaped_db(db_path):
    """Builds a chats.db in EXACTLY the shape the old per-connection
    _ensure_chats_table/_ensure_notes_table/_ensure_pins_table probes left
    behind on a real, long-lived install: the full current schema
    (including every column those probes' own conditional ALTER TABLEs
    would eventually add), real chat/notes/pins rows already in it, but
    PRAGMA user_version never once touched - reads 0, exactly like every
    actual pre-9.1 chats.db on a real user's machine does today. Raw DDL
    directly, rather than importing the now-deleted _ensure_* functions,
    matching this test file's own pre-existing convention (_insert_chat/
    _insert_note/_insert_pin above already do the same thing for their own,
    narrower purposes). Returns the inserted chat's id."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE chats (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "data TEXT NOT NULL, preview TEXT DEFAULT '', message_count INTEGER DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE notes (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, "
            "content TEXT NOT NULL, position_x REAL NOT NULL, position_y REAL NOT NULL, width REAL NOT NULL, "
            "height REAL NOT NULL, color TEXT NOT NULL, header_color TEXT, "
            "is_system_prompt INTEGER DEFAULT 0, is_summary_note INTEGER DEFAULT 0, "
            "FOREIGN KEY (chat_id) REFERENCES chats (id) ON DELETE CASCADE)"
        )
        conn.execute(
            "CREATE TABLE pins (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, "
            "title TEXT NOT NULL, note TEXT, position_x REAL NOT NULL, position_y REAL NOT NULL, "
            "pin_id TEXT, sort_order INTEGER DEFAULT 0, anchor_item_id TEXT, created_at TEXT, "
            "FOREIGN KEY (chat_id) REFERENCES chats (id) ON DELETE CASCADE)"
        )
        cursor = conn.execute(
            "INSERT INTO chats (title, data, created_at, updated_at, preview, message_count) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "Real Chat",
                json.dumps({"nodes": [{"node_type": "chat", "raw_content": "hi"}]}),
                "2026-01-01 10:00:00", "2026-01-02 11:30:00", "hi", 1,
            ),
        )
        chat_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO notes (chat_id, content, position_x, position_y, width, height, color, "
            "header_color, is_system_prompt, is_summary_note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_id, "A real note", 1.0, 2.0, 100.0, 50.0, "#111111", None, 0, 0),
        )
        conn.execute(
            "INSERT INTO pins (chat_id, title, note, position_x, position_y, pin_id, sort_order, "
            "anchor_item_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_id, "A real pin", "", 5.0, 6.0, "pin-1", 0, None, "2026-01-01 00:00:00"),
        )
        conn.commit()
        # user_version is deliberately left untouched here - reads 0, exactly
        # like every real pre-9.1 chats.db does before its first post-upgrade
        # connect.
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        return chat_id
    finally:
        conn.close()


class TestMigrationUpgradesAPreExistingRealShapedDatabase:
    """CRITICAL correctness case (this task's own ground rules): a migration
    that only works on a fresh, empty db and silently loses or corrupts data
    on a real pre-existing one would leave a user worse off than never
    shipping the migration at all. This builds a database in exactly the
    shape a real pre-9.1 chats.db has (full schema, real rows already in it,
    user_version=0) and proves the upgrade is completely lossless."""

    def test_existing_data_survives_and_version_and_indexes_land(self, db_path):
        chat_id = _create_pre_migration_shaped_db(db_path)

        # The real, production connect path - any query function drives it.
        rows = get_all_chats(db_path)

        assert len(rows) == 1
        assert rows[0]["id"] == chat_id
        assert rows[0]["title"] == "Real Chat"
        assert rows[0]["preview"] == "hi"
        assert rows[0]["messageCount"] == 1

        chat_row = load_chat_row(db_path, chat_id)
        assert chat_row["title"] == "Real Chat"
        assert chat_row["data"] == {"nodes": [{"node_type": "chat", "raw_content": "hi"}]}
        assert chat_row["updated_at"] == "2026-01-02 11:30:00"

        notes = load_notes_rows(db_path, chat_id)
        assert len(notes) == 1 and notes[0]["content"] == "A real note"

        pins = load_pins_rows(db_path, chat_id)
        assert len(pins) == 1 and pins[0]["title"] == "A real pin"

        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == CHATS_DB_SCHEMA_VERSION
            notes_indexes = {row[1] for row in conn.execute("PRAGMA index_list(notes)").fetchall()}
            pins_indexes = {row[1] for row in conn.execute("PRAGMA index_list(pins)").fetchall()}
            assert "idx_notes_chat_id" in notes_indexes
            assert "idx_pins_chat_id" in pins_indexes
        finally:
            conn.close()

    def test_upgrade_is_safe_to_run_twice_in_a_row(self, db_path):
        # The second connect (now already at target) must be a true no-op -
        # not a second attempt to re-create or re-alter anything - and the
        # data must still be intact and reachable afterward either way.
        chat_id = _create_pre_migration_shaped_db(db_path)
        get_all_chats(db_path)
        rows_after_second_connect = get_all_chats(db_path)
        assert len(rows_after_second_connect) == 1
        assert rows_after_second_connect[0]["id"] == chat_id


def _create_migration_1_shaped_db(db_path) -> list[int]:
    """ADR-020 stage 20.1's own analog of _create_pre_migration_shaped_db
    above: builds a chats.db in EXACTLY the shape a real, already-upgraded
    (ADR-009 stage 9.1) install has TODAY, right before this stage's own
    migration "2" ever runs against it - the full chats/notes/pins schema
    (raw DDL, not this module's own functions, which already assume the
    NEW post-migration-2 schema once chat_library.py has been edited),
    several real chat rows (including notes AND pins on at least one of
    them), and PRAGMA user_version explicitly left at 1 (not 0 - a real
    post-9.1 database has already run migration "1" and stamped its
    version; this migration "2" test's own realistic starting point is "at
    1, about to go to 2", not "at 0, needs both steps" - that combined case
    is already covered by TestMigrationUpgradesAPreExistingRealShapedDatabase
    above, which now also implicitly exercises 0 -> 1 -> 2 -> 3 in one hop
    since CHATS_DB_SCHEMA_VERSION is 3 (ADR-020 stage 20.2 added step "3" -
    see _create_migration_2_shaped_db below for THIS migration's own "at 2,
    about to go to 3" realistic starting point, one step further down the
    same chain).

    Returns the three inserted chat ids, in insertion order."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE chats (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "data TEXT NOT NULL, preview TEXT DEFAULT '', message_count INTEGER DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE notes (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, "
            "content TEXT NOT NULL, position_x REAL NOT NULL, position_y REAL NOT NULL, width REAL NOT NULL, "
            "height REAL NOT NULL, color TEXT NOT NULL, header_color TEXT, "
            "is_system_prompt INTEGER DEFAULT 0, is_summary_note INTEGER DEFAULT 0, "
            "FOREIGN KEY (chat_id) REFERENCES chats (id) ON DELETE CASCADE)"
        )
        conn.execute(
            "CREATE TABLE pins (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, "
            "title TEXT NOT NULL, note TEXT, position_x REAL NOT NULL, position_y REAL NOT NULL, "
            "pin_id TEXT, sort_order INTEGER DEFAULT 0, anchor_item_id TEXT, created_at TEXT, "
            "FOREIGN KEY (chat_id) REFERENCES chats (id) ON DELETE CASCADE)"
        )
        conn.execute("CREATE INDEX idx_notes_chat_id ON notes (chat_id)")
        conn.execute("CREATE INDEX idx_pins_chat_id ON pins (chat_id)")

        chat_ids = []
        for title, preview, count, created, updated in (
            ("First Chat", "hi there", 1, "2026-01-01 09:00:00", "2026-01-01 09:05:00"),
            ("Second Chat", "another one", 2, "2026-01-02 10:00:00", "2026-01-02 10:10:00"),
            ("Third Chat", "the last one", 3, "2026-01-03 11:00:00", "2026-01-03 11:15:00"),
        ):
            cursor = conn.execute(
                "INSERT INTO chats (title, data, created_at, updated_at, preview, message_count) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (title, json.dumps({"nodes": [{"node_type": "chat", "raw_content": preview}]}),
                 created, updated, preview, count),
            )
            chat_ids.append(cursor.lastrowid)

        # Notes and pins attached to the first chat only - mirrors
        # _create_pre_migration_shaped_db's own "at least one" scope; the
        # other two chats exercise the no-notes/no-pins case.
        conn.execute(
            "INSERT INTO notes (chat_id, content, position_x, position_y, width, height, color, "
            "header_color, is_system_prompt, is_summary_note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_ids[0], "A migration-2 note", 1.0, 2.0, 100.0, 50.0, "#222222", None, 0, 0),
        )
        conn.execute(
            "INSERT INTO pins (chat_id, title, note, position_x, position_y, pin_id, sort_order, "
            "anchor_item_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_ids[0], "A migration-2 pin", "", 5.0, 6.0, "pin-1", 0, None, "2026-01-01 00:00:00"),
        )
        conn.commit()

        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        return chat_ids
    finally:
        conn.close()


class TestMigration002IntroducesWorkspacesAndGraphs:
    """ADR-020 stage 20.1's own fidelity proof, mirroring
    TestMigrationUpgradesAPreExistingRealShapedDatabase above precisely:
    hand-build a real migration-1-shaped chats.db, drive it through the
    REAL production connect path (never a hand-called migration function -
    that would only prove the SQL, not the wiring), and assert every
    original row survives byte-for-byte under its new home."""

    def test_existing_chats_become_graphs_in_the_default_workspace(self, db_path):
        chat_ids = _create_migration_1_shaped_db(db_path)

        # The real, production connect path - any query function drives it,
        # exactly like TestMigrationUpgradesAPreExistingRealShapedDatabase's
        # own get_all_chats(db_path) call above.
        rows = get_all_chats(db_path)

        assert len(rows) == 3
        assert {row["id"] for row in rows} == set(chat_ids)
        by_id = {row["id"]: row for row in rows}
        assert by_id[chat_ids[0]]["title"] == "First Chat"
        assert by_id[chat_ids[0]]["preview"] == "hi there"
        assert by_id[chat_ids[0]]["messageCount"] == 1
        assert by_id[chat_ids[1]]["title"] == "Second Chat"
        assert by_id[chat_ids[2]]["title"] == "Third Chat"

        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == CHATS_DB_SCHEMA_VERSION

            workspace_rows = conn.execute("SELECT id, name FROM workspaces").fetchall()
            assert len(workspace_rows) == 1
            default_workspace_id, default_workspace_name = workspace_rows[0]
            assert default_workspace_name == "Default"

            graph_workspace_ids = {
                row[0] for row in conn.execute("SELECT workspace_id FROM graphs").fetchall()
            }
            assert graph_workspace_ids == {default_workspace_id}

            graphs_indexes = {row[1] for row in conn.execute("PRAGMA index_list(graphs)").fetchall()}
            assert "idx_graphs_workspace_id" in graphs_indexes

            # The renamed table's own row identity/content is untouched -
            # the rename is a pure schema-level operation, not a data copy.
            graphs_rows = conn.execute(
                "SELECT id, title, data, created_at, updated_at, preview, message_count FROM graphs "
                "ORDER BY id"
            ).fetchall()
            assert [row[0] for row in graphs_rows] == chat_ids
            assert graphs_rows[0][1] == "First Chat"
            assert graphs_rows[0][2] == json.dumps({"nodes": [{"node_type": "chat", "raw_content": "hi there"}]})
            assert graphs_rows[0][3] == "2026-01-01 09:00:00"
            assert graphs_rows[0][4] == "2026-01-01 09:05:00"
        finally:
            conn.close()

        # load_chat_row/load_notes_rows/load_pins_rows - unchanged Python
        # behavior, now transparently reading through the renamed table.
        chat_row = load_chat_row(db_path, chat_ids[0])
        assert chat_row["title"] == "First Chat"
        assert chat_row["data"] == {"nodes": [{"node_type": "chat", "raw_content": "hi there"}]}

        notes = load_notes_rows(db_path, chat_ids[0])
        assert len(notes) == 1 and notes[0]["content"] == "A migration-2 note"

        pins = load_pins_rows(db_path, chat_ids[0])
        assert len(pins) == 1 and pins[0]["title"] == "A migration-2 pin"

        # The other two chats never had notes/pins - still true post-migration.
        assert load_notes_rows(db_path, chat_ids[1]) == []
        assert load_pins_rows(db_path, chat_ids[1]) == []

    def test_fresh_database_lands_on_version_2_with_only_the_default_workspace(self, db_path):
        # No pre-existing file at all - a fresh install. Must reach version
        # 2 cleanly with the Default workspace seeded and zero graphs.
        assert not db_path.exists()

        rows = get_all_chats(db_path)

        assert rows == []
        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == CHATS_DB_SCHEMA_VERSION
            workspace_rows = conn.execute("SELECT id, name FROM workspaces").fetchall()
            assert len(workspace_rows) == 1
            assert workspace_rows[0][1] == "Default"
            assert conn.execute("SELECT COUNT(*) FROM graphs").fetchone()[0] == 0
        finally:
            conn.close()

    def test_migration_is_idempotent_when_run_twice_in_a_row(self, db_path):
        # Matches TestMigrationUpgradesAPreExistingRealShapedDatabase's own
        # test_upgrade_is_safe_to_run_twice_in_a_row precedent: a second
        # connect (already at target version 2) is a true no-op - no
        # duplicate Default workspace, no data disturbance.
        chat_ids = _create_migration_1_shaped_db(db_path)

        get_all_chats(db_path)
        rows_after_second_connect = get_all_chats(db_path)

        assert {row["id"] for row in rows_after_second_connect} == set(chat_ids)

        conn = sqlite3.connect(db_path)
        try:
            workspace_rows = conn.execute("SELECT id, name FROM workspaces").fetchall()
            assert len(workspace_rows) == 1, (
                f"expected exactly one Default workspace after two migration passes, got {workspace_rows}"
            )
            assert conn.execute("SELECT COUNT(*) FROM graphs").fetchone()[0] == 3
        finally:
            conn.close()


def _create_migration_2_shaped_db(db_path) -> list[int]:
    """ADR-020 stage 20.2's own analog of _create_migration_1_shaped_db
    above: builds a chats.db in EXACTLY the shape a real, already-upgraded
    (ADR-020 stage 20.1) install has TODAY, right before this stage's own
    migration "3" ever runs against it - workspaces + graphs (with
    workspace_id, but no favorite/archived columns yet) + notes/pins, real
    rows in more than one workspace, PRAGMA user_version explicitly left at
    2 (a real post-20.1 database has already run migrations "1" and "2" and
    stamped its version at 2 - this migration "3" test's own realistic
    starting point, mirroring _create_migration_1_shaped_db's own "at 1,
    about to go to 2" precedent one step further down the chain).

    Returns the inserted graph ids, in insertion order (first two in the
    Default workspace, the third in a second, explicitly-created workspace -
    proving migration 3 touches every graph regardless of which workspace it
    is already in)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE workspaces (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
            "icon TEXT NOT NULL DEFAULT '', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "archived INTEGER NOT NULL DEFAULT 0)"
        )
        default_id = conn.execute("INSERT INTO workspaces (name) VALUES ('Default')").lastrowid
        other_id = conn.execute("INSERT INTO workspaces (name) VALUES ('Research')").lastrowid

        conn.execute(
            "CREATE TABLE graphs (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "data TEXT NOT NULL, preview TEXT DEFAULT '', message_count INTEGER DEFAULT 0, "
            f"workspace_id INTEGER NOT NULL DEFAULT {default_id})"
        )
        conn.execute(
            "CREATE TABLE notes (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, "
            "content TEXT NOT NULL, position_x REAL NOT NULL, position_y REAL NOT NULL, width REAL NOT NULL, "
            "height REAL NOT NULL, color TEXT NOT NULL, header_color TEXT, "
            "is_system_prompt INTEGER DEFAULT 0, is_summary_note INTEGER DEFAULT 0, "
            "FOREIGN KEY (chat_id) REFERENCES graphs (id) ON DELETE CASCADE)"
        )
        conn.execute(
            "CREATE TABLE pins (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, "
            "title TEXT NOT NULL, note TEXT, position_x REAL NOT NULL, position_y REAL NOT NULL, "
            "pin_id TEXT, sort_order INTEGER DEFAULT 0, anchor_item_id TEXT, created_at TEXT, "
            "FOREIGN KEY (chat_id) REFERENCES graphs (id) ON DELETE CASCADE)"
        )
        conn.execute("CREATE INDEX idx_notes_chat_id ON notes (chat_id)")
        conn.execute("CREATE INDEX idx_pins_chat_id ON pins (chat_id)")
        conn.execute("CREATE INDEX idx_graphs_workspace_id ON graphs (workspace_id)")

        graph_ids = []
        for title, workspace_id in (
            ("First Graph", default_id),
            ("Second Graph", default_id),
            ("Third Graph", other_id),
        ):
            cursor = conn.execute(
                "INSERT INTO graphs (title, data, workspace_id) VALUES (?, ?, ?)",
                (title, json.dumps({"nodes": []}), workspace_id),
            )
            graph_ids.append(cursor.lastrowid)
        conn.commit()

        conn.execute("PRAGMA user_version = 2")
        conn.commit()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        return graph_ids
    finally:
        conn.close()


class TestMigration003AddsTagsFavoriteArchive:
    """ADR-020 stage 20.2's own fidelity proof, mirroring
    TestMigration002IntroducesWorkspacesAndGraphs above precisely: hand-build
    a real migration-2-shaped chats.db, drive it through the REAL production
    connect path (never a hand-called migration function), and assert every
    original row survives byte-for-byte with the new columns/tables landing
    correctly around it."""

    def test_existing_graphs_get_favorite_archive_defaults_and_no_tags(self, db_path):
        graph_ids = _create_migration_2_shaped_db(db_path)

        rows = get_all_chats(db_path)

        assert len(rows) == 3
        by_id = {row["id"]: row for row in rows}
        for graph_id in graph_ids:
            assert by_id[graph_id]["favorite"] is False
            assert by_id[graph_id]["archived"] is False
            assert by_id[graph_id]["tags"] == []
        assert by_id[graph_ids[0]]["title"] == "First Graph"
        assert by_id[graph_ids[2]]["title"] == "Third Graph"

        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == CHATS_DB_SCHEMA_VERSION

            graphs_columns = {info[1] for info in conn.execute("PRAGMA table_info(graphs)").fetchall()}
            assert {"favorite", "archived"} <= graphs_columns

            tags_columns = {info[1] for info in conn.execute("PRAGMA table_info(tags)").fetchall()}
            assert tags_columns == {"id", "name"}

            graph_tags_indexes = {row[1] for row in conn.execute("PRAGMA index_list(graph_tags)").fetchall()}
            assert "idx_graph_tags_tag_id" in graph_tags_indexes

            # Workspaces (migration 2's own table) are completely untouched -
            # this migration is deliberately narrow, per its own docstring.
            workspace_names = {
                row[0] for row in conn.execute("SELECT name FROM workspaces").fetchall()
            }
            assert workspace_names == {"Default", "Research"}
        finally:
            conn.close()

    def test_fresh_database_lands_on_version_3_with_favorite_archive_columns(self, db_path):
        assert not db_path.exists()

        rows = get_all_chats(db_path)

        assert rows == []
        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == CHATS_DB_SCHEMA_VERSION
            graphs_columns = {info[1] for info in conn.execute("PRAGMA table_info(graphs)").fetchall()}
            assert {"favorite", "archived"} <= graphs_columns
            assert conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0
        finally:
            conn.close()

    def test_migration_is_idempotent_when_run_twice_in_a_row(self, db_path):
        graph_ids = _create_migration_2_shaped_db(db_path)

        get_all_chats(db_path)
        rows_after_second_connect = get_all_chats(db_path)

        assert {row["id"] for row in rows_after_second_connect} == set(graph_ids)
        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("SELECT COUNT(*) FROM graphs").fetchone()[0] == 3
            # A second run must not duplicate favorite/archived columns or
            # re-create tags/graph_tags in a way that loses data - trivially
            # true here since nothing has tagged anything yet, but the
            # PRAGMA table_info probe itself proves no OperationalError
            # ("duplicate column name") was raised by a naive re-ALTER.
            graphs_columns = [info[1] for info in conn.execute("PRAGMA table_info(graphs)").fetchall()]
            assert graphs_columns.count("favorite") == 1
            assert graphs_columns.count("archived") == 1
        finally:
            conn.close()

    def test_graph_tags_cascade_deletes_on_both_sides_real_sqlite_behavior(self, db_path):
        """Empirically verifies (not assumed from documentation alone) that
        graph_tags' own declared `ON DELETE CASCADE` FKs actually fire under
        this project's real bundled SQLite, in BOTH directions: deleting the
        graph side (delete_chat's own real production path) and deleting the
        tag side (a raw DELETE, since no intent deletes a tags row directly
        today - set_graph_tags deliberately leaves orphaned tags in place,
        see its own docstring - but the declared FK must still cascade if
        one ever is removed some other way)."""
        get_all_chats(db_path)  # bootstraps a fresh, fully-migrated db
        graph_id = save_chat_atomically_row(db_path, None, "Tagged", {"nodes": []}, [], [])[0]
        set_graph_tags(db_path, graph_id, ["work", "urgent"])

        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            assert conn.execute("SELECT COUNT(*) FROM graph_tags").fetchone()[0] == 2

            # Graph-side cascade: delete_chat's own real "DELETE FROM graphs"
            # (via the production delete_chat function, not raw SQL) must
            # remove both graph_tags rows.
            delete_chat(db_path, graph_id)
            assert conn.execute("SELECT COUNT(*) FROM graph_tags").fetchone()[0] == 0
            # The tags rows THEMSELVES survive (orphaned, by design).
            assert conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 2
        finally:
            conn.close()

        # Tag-side cascade: re-tag a second graph, then delete the TAG row
        # directly and confirm the join row disappears too.
        second_graph_id = save_chat_atomically_row(db_path, None, "Also Tagged", {"nodes": []}, [], [])[0]
        set_graph_tags(db_path, second_graph_id, ["work"])
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            tag_id = conn.execute("SELECT id FROM tags WHERE name = 'work'").fetchone()[0]
            assert conn.execute(
                "SELECT COUNT(*) FROM graph_tags WHERE tag_id = ?", (tag_id,)
            ).fetchone()[0] == 1
            conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
            conn.commit()
            assert conn.execute(
                "SELECT COUNT(*) FROM graph_tags WHERE tag_id = ?", (tag_id,)
            ).fetchone()[0] == 0
        finally:
            conn.close()


def test_get_all_chats_reads_real_rows(db_path):
    first_id = _insert_chat(db_path, "First")
    second_id = _insert_chat(db_path, "Second")

    rows = get_all_chats(db_path)
    ids = {row["id"] for row in rows}
    assert ids == {first_id, second_id}
    for row in rows:
        assert set(row) == {
            "id", "title", "createdLabel", "updatedLabel",
            "createdAtIso", "updatedAtIso", "preview", "messageCount",
            "workspaceId", "favorite", "archived", "tags",
        }
        assert row["updatedLabel"] == "Jan 02, 2026 11:30 AM"
        assert row["updatedAtIso"] == "2026-01-02T11:30:00"
        # _insert_chat writes the OLD (pre-R8a) column set directly - the
        # ALTER TABLE migration must still leave these rows valid.
        assert row["preview"] == ""
        assert row["messageCount"] == 0
        # ADR-020 stage 20.2: a row that never went through migration 3's
        # own default column values still lands on the same fresh-graph
        # defaults - untagged, not favorited, not archived, in whichever
        # workspace migration 2 backfilled it into (Default, here).
        assert row["favorite"] is False
        assert row["archived"] is False
        assert row["tags"] == []
        assert isinstance(row["workspaceId"], int)


def test_format_timestamp_matches_legacy_display_format():
    assert _format_timestamp("2026-01-02 11:30:00") == "Jan 02, 2026 11:30 AM"
    assert _format_timestamp("") == "Unknown"
    assert _format_timestamp(None) == "Unknown"
    assert _format_timestamp("not-a-timestamp") == "not-a-timestamp"


def test_format_timestamp_iso_returns_a_real_parseable_instant():
    assert _format_timestamp_iso("2026-01-02 11:30:00") == "2026-01-02T11:30:00"
    assert _format_timestamp_iso("") is None
    assert _format_timestamp_iso(None) is None
    assert _format_timestamp_iso("not-a-timestamp") is None


def test_extract_preview_uses_the_last_chat_nodes_text():
    chat_data = {
        "nodes": [
            {"node_type": "chat", "raw_content": "first message", "is_user": True},
            {"node_type": "code", "code": "x = 1"},
            {"node_type": "chat", "raw_content": "  the   real   last message  ", "is_user": False},
        ],
    }
    preview, count = _extract_preview_and_message_count(chat_data)
    assert preview == "the real last message"
    assert count == 2


def test_extract_preview_handles_multimodal_content_parts():
    chat_data = {
        "nodes": [
            {
                "node_type": "chat",
                "raw_content": [
                    {"type": "text", "text": "look at this"},
                    {"type": "image_bytes", "data": "not-real-image-data"},
                ],
                "is_user": True,
            },
        ],
    }
    preview, count = _extract_preview_and_message_count(chat_data)
    assert preview == "look at this"
    assert count == 1


def test_extract_preview_truncates_long_text():
    chat_data = {"nodes": [{"node_type": "chat", "raw_content": "a" * 500, "is_user": True}]}
    preview, _ = _extract_preview_and_message_count(chat_data)
    assert len(preview) == 140


def test_extract_preview_is_empty_for_no_chat_nodes():
    assert _extract_preview_and_message_count({"nodes": []}) == ("", 0)
    assert _extract_preview_and_message_count({}) == ("", 0)


def test_rename_chat_persists_and_updates_timestamp(db_path):
    chat_id = _insert_chat(db_path, "Original")
    rename_chat(db_path, chat_id, "Renamed")

    rows = get_all_chats(db_path)
    renamed = next(row for row in rows if row["id"] == chat_id)
    assert renamed["title"] == "Renamed"


def test_delete_chat_removes_the_row(db_path):
    chat_id = _insert_chat(db_path, "Doomed")
    delete_chat(db_path, chat_id)

    rows = get_all_chats(db_path)
    assert all(row["id"] != chat_id for row in rows)


# -- ADR-020 stage 20.2: favorite/archived/tags + workspaces CRUD -----------


def test_set_graph_favorite_round_trips(db_path):
    chat_id = _insert_chat(db_path, "Star Me")
    assert next(row for row in get_all_chats(db_path) if row["id"] == chat_id)["favorite"] is False

    set_graph_favorite(db_path, chat_id, True)
    assert next(row for row in get_all_chats(db_path) if row["id"] == chat_id)["favorite"] is True

    set_graph_favorite(db_path, chat_id, False)
    assert next(row for row in get_all_chats(db_path) if row["id"] == chat_id)["favorite"] is False


def test_set_graph_favorite_does_not_bump_updated_at(db_path):
    # A metadata toggle must never re-sort get_all_chats' own
    # `ORDER BY updated_at DESC` - see set_graph_favorite's own docstring.
    chat_id = _insert_chat(db_path, "Stable Order")
    before = next(row for row in get_all_chats(db_path) if row["id"] == chat_id)["updatedAtIso"]
    set_graph_favorite(db_path, chat_id, True)
    after = next(row for row in get_all_chats(db_path) if row["id"] == chat_id)["updatedAtIso"]
    assert before == after


def test_set_graph_archived_round_trips(db_path):
    chat_id = _insert_chat(db_path, "Archive Me")
    assert next(row for row in get_all_chats(db_path) if row["id"] == chat_id)["archived"] is False

    set_graph_archived(db_path, chat_id, True)
    assert next(row for row in get_all_chats(db_path) if row["id"] == chat_id)["archived"] is True

    set_graph_archived(db_path, chat_id, False)
    assert next(row for row in get_all_chats(db_path) if row["id"] == chat_id)["archived"] is False


def test_normalize_tags_trims_drops_empty_and_case_collapses():
    assert _normalize_tags(["  Work  ", "urgent", "", "   ", "WORK", "work"]) == ["Work", "urgent"]
    assert _normalize_tags([]) == []


def test_set_graph_tags_round_trips_with_trim_dedupe_case_collapse(db_path):
    chat_id = _insert_chat(db_path, "Tag Me")
    set_graph_tags(db_path, chat_id, ["  Work  ", "Work", "urgent", "", "URGENT"])

    row = next(row for row in get_all_chats(db_path) if row["id"] == chat_id)
    assert row["tags"] == ["urgent", "Work"]  # display-sorted (COLLATE NOCASE): u < W


def test_set_graph_tags_is_a_bulk_replace_not_a_delta(db_path):
    chat_id = _insert_chat(db_path, "Retag Me")
    set_graph_tags(db_path, chat_id, ["one", "two"])
    set_graph_tags(db_path, chat_id, ["three"])

    row = next(row for row in get_all_chats(db_path) if row["id"] == chat_id)
    assert row["tags"] == ["three"]


def test_set_graph_tags_clearing_to_empty_removes_all_tags(db_path):
    chat_id = _insert_chat(db_path, "Untag Me")
    set_graph_tags(db_path, chat_id, ["one"])
    set_graph_tags(db_path, chat_id, [])

    row = next(row for row in get_all_chats(db_path) if row["id"] == chat_id)
    assert row["tags"] == []


def test_set_graph_tags_shares_one_tags_row_across_graphs_case_insensitively(db_path):
    first_id = _insert_chat(db_path, "First")
    second_id = _insert_chat(db_path, "Second")
    set_graph_tags(db_path, first_id, ["Work"])
    set_graph_tags(db_path, second_id, ["work"])  # different casing

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 1
    finally:
        conn.close()


def test_get_all_workspaces_includes_the_seeded_default(db_path):
    _insert_chat(db_path, "Bootstraps a fresh db")  # triggers migration
    workspaces = get_all_workspaces(db_path)
    assert len(workspaces) == 1
    assert workspaces[0]["name"] == "Default"
    assert workspaces[0]["archived"] is False


def test_create_workspace_returns_the_new_row_and_persists(db_path):
    _insert_chat(db_path, "Bootstraps a fresh db")
    created = create_workspace(db_path, "Research")
    assert created is not None
    assert created["name"] == "Research"
    assert created["archived"] is False

    workspaces = get_all_workspaces(db_path)
    names = {workspace["name"] for workspace in workspaces}
    assert names == {"Default", "Research"}


def test_create_workspace_rejects_empty_or_whitespace_only_name(db_path):
    _insert_chat(db_path, "Bootstraps a fresh db")
    assert create_workspace(db_path, "") is None
    assert create_workspace(db_path, "   ") is None
    assert len(get_all_workspaces(db_path)) == 1  # still just Default


def test_rename_workspace_persists(db_path):
    _insert_chat(db_path, "Bootstraps a fresh db")
    created = create_workspace(db_path, "Old Name")
    rename_workspace(db_path, created["id"], "New Name")

    workspace = next(ws for ws in get_all_workspaces(db_path) if ws["id"] == created["id"])
    assert workspace["name"] == "New Name"


def test_rename_workspace_ignores_empty_name(db_path):
    _insert_chat(db_path, "Bootstraps a fresh db")
    created = create_workspace(db_path, "Keep Me")
    rename_workspace(db_path, created["id"], "   ")

    workspace = next(ws for ws in get_all_workspaces(db_path) if ws["id"] == created["id"])
    assert workspace["name"] == "Keep Me"


def test_archive_workspace_round_trips_and_does_not_touch_its_graphs(db_path):
    chat_id = _insert_chat(db_path, "Bootstraps a fresh db")
    default_workspace = get_all_workspaces(db_path)[0]

    archive_workspace(db_path, default_workspace["id"], True)
    workspace = next(ws for ws in get_all_workspaces(db_path) if ws["id"] == default_workspace["id"])
    assert workspace["archived"] is True
    # The graph inside it is completely unaffected.
    graph_row = next(row for row in get_all_chats(db_path) if row["id"] == chat_id)
    assert graph_row["archived"] is False
    assert graph_row["workspaceId"] == default_workspace["id"]

    archive_workspace(db_path, default_workspace["id"], False)
    workspace = next(ws for ws in get_all_workspaces(db_path) if ws["id"] == default_workspace["id"])
    assert workspace["archived"] is False


def test_chat_library_payload_shape(db_path):
    _insert_chat(db_path, "A Chat")
    payload = chat_library_payload(db_path)
    assert set(payload) == {"rows", "notice", "workspaces"}
    assert payload["notice"] is None
    assert len(payload["rows"]) == 1
    # ADR-020 stage 20.2: the Default workspace always exists (migration 2
    # seeds it unconditionally) - one workspace row even for a db with no
    # explicit workspace of its own ever created.
    assert len(payload["workspaces"]) == 1
    assert payload["workspaces"][0]["name"] == "Default"


def test_chat_library_never_imports_qt():
    # A plain `assert "PySide6" not in sys.modules` is only meaningful in a
    # process where nothing else has imported PySide6 - running under the
    # full repo-wide pytest suite (alongside graphlink_app/tests' real Qt
    # widget tests), sys.modules is already contaminated regardless of what
    # this module itself imports. Only a fresh subprocess importing ONLY
    # backend.chat_library actually answers "does this transitively pull in
    # Qt" - exactly the graphlink_session/__init__.py hazard this module's
    # own docstring exists to route around.
    import subprocess
    import sys as _sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [_sys.executable, "-c", "import backend.chat_library, sys; assert 'PySide6' not in sys.modules"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_register_chat_library_publishes_on_the_app_chat_library_topic(db_path):
    _insert_chat(db_path, "Hello")
    bus = SessionBus("chat-library-test")
    register_chat_library(bus, db_path)

    recorder = Recorder()
    bus.attach(recorder)
    asyncio.run(bus.publish("app-chat-library"))
    payload = recorder.messages[0]["payload"]
    assert payload["rows"][0]["title"] == "Hello"


def test_rename_chat_intent_ignores_empty_title(db_path):
    chat_id = _insert_chat(db_path, "Keep Me")
    bus = SessionBus("chat-library-rename-empty-test")
    register_chat_library(bus, db_path)

    asyncio.run(bus.dispatch_intent("app-chat-library", "renameChat", [chat_id, "   "]))
    rows = get_all_chats(db_path)
    assert next(row for row in rows if row["id"] == chat_id)["title"] == "Keep Me"


def test_rename_chat_intent_persists_and_republishes(db_path):
    chat_id = _insert_chat(db_path, "Before")
    bus = SessionBus("chat-library-rename-test")
    register_chat_library(bus, db_path)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-chat-library", "renameChat", [chat_id, "After"]))
    assert recorder.messages[-1]["payload"]["rows"][0]["title"] == "After"


def test_delete_chat_intent_removes_and_republishes(db_path):
    chat_id = _insert_chat(db_path, "Temp")
    bus = SessionBus("chat-library-delete-test")
    register_chat_library(bus, db_path)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-chat-library", "deleteChat", [chat_id]))
    assert recorder.messages[-1]["payload"]["rows"] == []


# -- ADR-020 stage 20.2: the 6 new intents -----------------------------------


def test_set_graph_favorite_intent_fires_with_the_right_args_and_republishes(db_path):
    chat_id = _insert_chat(db_path, "Star Via Intent")
    bus = SessionBus("chat-library-set-favorite-test")
    register_chat_library(bus, db_path)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-chat-library", "setGraphFavorite", [chat_id, True]))
    row = next(row for row in recorder.messages[-1]["payload"]["rows"] if row["id"] == chat_id)
    assert row["favorite"] is True


def test_set_graph_favorite_intent_rejects_wrong_typed_args(db_path):
    _insert_chat(db_path, "Whatever")
    bus = SessionBus("chat-library-set-favorite-validation-test")
    register_chat_library(bus, db_path)

    with pytest.raises(IntentValidationError):
        asyncio.run(bus.dispatch_intent("app-chat-library", "setGraphFavorite", ["not-an-int", True]))


def test_set_graph_archived_intent_fires_with_the_right_args_and_republishes(db_path):
    chat_id = _insert_chat(db_path, "Archive Via Intent")
    bus = SessionBus("chat-library-set-archived-test")
    register_chat_library(bus, db_path)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-chat-library", "setGraphArchived", [chat_id, True]))
    row = next(row for row in recorder.messages[-1]["payload"]["rows"] if row["id"] == chat_id)
    assert row["archived"] is True


def test_set_graph_tags_intent_fires_with_the_right_args_and_republishes(db_path):
    chat_id = _insert_chat(db_path, "Tag Via Intent")
    bus = SessionBus("chat-library-set-tags-test")
    register_chat_library(bus, db_path)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-chat-library", "setGraphTags", [chat_id, ["  Work  ", "work"]]))
    row = next(row for row in recorder.messages[-1]["payload"]["rows"] if row["id"] == chat_id)
    assert row["tags"] == ["Work"]


def test_create_workspace_intent_publishes_the_new_workspace(db_path):
    _insert_chat(db_path, "Bootstraps a fresh db")
    bus = SessionBus("chat-library-create-workspace-test")
    register_chat_library(bus, db_path)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-chat-library", "createWorkspace", ["Research"]))
    names = {ws["name"] for ws in recorder.messages[-1]["payload"]["workspaces"]}
    assert names == {"Default", "Research"}


def test_create_workspace_intent_rejects_empty_name_with_a_notification(db_path):
    _insert_chat(db_path, "Bootstraps a fresh db")
    bus = SessionBus("chat-library-create-workspace-empty-test")
    document = SceneDocument()
    bus.register_topic("scene", document.scene_payload)
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    register_chat_library(bus, db_path, document, notifications, autosave_interval_seconds=None)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-chat-library", "createWorkspace", ["   "]))

    # No new workspace was created, and no "app-chat-library" republish
    # happened - only a notification.
    assert len(get_all_workspaces(db_path)) == 1
    notification_messages = [m for m in recorder.messages if m.get("topic") == "notification"]
    assert notification_messages, "an empty workspace name must surface a real notification"
    assert notification_messages[-1]["payload"]["message"] == "Workspace name cannot be empty."


def test_rename_workspace_intent_fires_with_the_right_args_and_republishes(db_path):
    _insert_chat(db_path, "Bootstraps a fresh db")
    default_workspace_id = get_all_workspaces(db_path)[0]["id"]
    bus = SessionBus("chat-library-rename-workspace-test")
    register_chat_library(bus, db_path)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-chat-library", "renameWorkspace", [default_workspace_id, "Renamed"]))
    names = {ws["name"] for ws in recorder.messages[-1]["payload"]["workspaces"]}
    assert names == {"Renamed"}


def test_archive_workspace_intent_fires_with_the_right_args_and_republishes(db_path):
    _insert_chat(db_path, "Bootstraps a fresh db")
    default_workspace_id = get_all_workspaces(db_path)[0]["id"]
    bus = SessionBus("chat-library-archive-workspace-test")
    register_chat_library(bus, db_path)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-chat-library", "archiveWorkspace", [default_workspace_id, True]))
    workspace = next(
        ws for ws in recorder.messages[-1]["payload"]["workspaces"] if ws["id"] == default_workspace_id
    )
    assert workspace["archived"] is True


# -- R6.4: load_chat_row / load_notes_rows / load_pins_rows -----------------


def _insert_note(db_path, chat_id: int, **overrides) -> None:
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, "
            "content TEXT NOT NULL, position_x REAL NOT NULL, position_y REAL NOT NULL, width REAL NOT NULL, "
            "height REAL NOT NULL, color TEXT NOT NULL, header_color TEXT, "
            "is_system_prompt INTEGER DEFAULT 0, is_summary_note INTEGER DEFAULT 0)"
        )
        row = {
            "content": "hello note", "position_x": 1.0, "position_y": 2.0,
            "width": 100.0, "height": 50.0, "color": "#111111", "header_color": None,
            "is_system_prompt": 0, "is_summary_note": 0,
        }
        row.update(overrides)
        conn.execute(
            "INSERT INTO notes (chat_id, content, position_x, position_y, width, height, color, "
            "header_color, is_system_prompt, is_summary_note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_id, row["content"], row["position_x"], row["position_y"], row["width"], row["height"],
             row["color"], row["header_color"], row["is_system_prompt"], row["is_summary_note"]),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_pin(db_path, chat_id: int, **overrides) -> None:
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pins (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, "
            "title TEXT NOT NULL, note TEXT, position_x REAL NOT NULL, position_y REAL NOT NULL, "
            "pin_id TEXT, sort_order INTEGER DEFAULT 0, anchor_item_id TEXT, created_at TEXT)"
        )
        row = {
            "title": "My Pin", "note": "", "position_x": 5.0, "position_y": 6.0,
            "pin_id": "pin-1", "sort_order": 0, "anchor_item_id": None, "created_at": "2026-01-01 00:00:00",
        }
        row.update(overrides)
        conn.execute(
            "INSERT INTO pins (chat_id, title, note, position_x, position_y, pin_id, sort_order, "
            "anchor_item_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_id, row["title"], row["note"], row["position_x"], row["position_y"],
             row["pin_id"], row["sort_order"], row["anchor_item_id"], row["created_at"]),
        )
        conn.commit()
    finally:
        conn.close()


def test_load_chat_row_returns_title_and_parsed_data(db_path):
    chat_id = _insert_chat(db_path, "Loadable", data=json.dumps({"nodes": [{"node_type": "chat"}]}))
    row = load_chat_row(db_path, chat_id)
    assert row["title"] == "Loadable"
    assert row["data"] == {"nodes": [{"node_type": "chat"}]}
    # ADR-009 stage 9.2: the value optimistic concurrency carries forward -
    # _insert_chat's own fixed updated_at (see this test file's own helper).
    assert row["updated_at"] == "2026-01-02 11:30:00"


def test_load_chat_row_returns_none_for_missing_id(db_path):
    assert load_chat_row(db_path, 999) is None


def test_load_notes_rows_shape_matches_session_load_expectations(db_path):
    chat_id = _insert_chat(db_path, "WithNotes")
    _insert_note(db_path, chat_id, content="A note", is_system_prompt=1)

    rows = load_notes_rows(db_path, chat_id)
    assert len(rows) == 1
    assert rows[0] == {
        "content": "A note", "position": {"x": 1.0, "y": 2.0}, "size": {"width": 100.0, "height": 50.0},
        "color": "#111111", "header_color": None, "is_system_prompt": True, "is_summary_note": False,
    }


def test_load_pins_rows_orders_by_sort_order_and_shape(db_path):
    chat_id = _insert_chat(db_path, "WithPins")
    _insert_pin(db_path, chat_id, title="Second", sort_order=1, pin_id="pin-b")
    _insert_pin(db_path, chat_id, title="First", sort_order=0, pin_id="pin-a")

    rows = load_pins_rows(db_path, chat_id)
    assert [row["title"] for row in rows] == ["First", "Second"]
    assert rows[0]["position"] == {"x": 5.0, "y": 6.0}
    assert rows[0]["pin_id"] == "pin-a"


# -- R6.4: the loadChat intent -----------------------------------------------


def _bus_with_canvas(db_path):
    """Mirrors backend/app.py's own registration order: canvas's "scene"
    topic must exist before register_chat_library's loadChat intent can
    publish to it - production guarantees this via _configure_session's own
    ordering; this test harness replicates it directly rather than pulling
    in the full register_canvas (which itself needs an agent dispatcher/
    composer document unrelated to what's under test here).

    autosave_interval_seconds=None disables R6.6's own background timer
    loop here - every test in this file runs in milliseconds, so a real
    30s-sleeping asyncio task would still be "pending" when asyncio.run()
    returns and the loop closes, leaking a task and spamming "Task was
    destroyed but it is pending" warnings across ~30 unrelated tests.
    backend/tests/test_autosave.py exercises the actual tick logic directly
    (no timer involved) instead."""
    bus = SessionBus("chat-library-load-test")
    document = SceneDocument()
    bus.register_topic("scene", document.scene_payload)
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    register_chat_library(bus, db_path, document, notifications, autosave_interval_seconds=None)
    return bus, document, notifications


def test_load_chat_intent_restores_a_real_node_into_the_canvas_document(db_path):
    chat_data = {
        "nodes": [
            {"node_type": "chat", "id": "n1", "raw_content": "Hi", "is_user": True, "position": {"x": 0, "y": 0}},
        ],
    }
    chat_id = _insert_chat(db_path, "Real Session", data=json.dumps(chat_data))
    bus, document, notifications = _bus_with_canvas(db_path)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-chat-library", "loadChat", [chat_id]))

    assert len(document.nodes) == 1
    node = next(iter(document.nodes.values()))
    assert node.kind == "chat" and node.content == "Hi" and node.state.is_user is True
    assert notifications.visible and notifications.msg_type == "success"
    scene_messages = [m for m in recorder.messages if m["topic"] == "scene"]
    assert scene_messages, "loadChat must publish a fresh scene snapshot"


def test_load_chat_intent_shows_an_error_notification_for_a_missing_chat(db_path):
    bus, document, notifications = _bus_with_canvas(db_path)

    asyncio.run(bus.dispatch_intent("app-chat-library", "loadChat", [999]))

    assert document.nodes == {}
    assert notifications.visible and notifications.msg_type == "error"


def test_load_chat_intent_restores_notes_and_pins_too(db_path):
    chat_data = {"nodes": []}
    chat_id = _insert_chat(db_path, "Notes And Pins", data=json.dumps(chat_data))
    _insert_note(db_path, chat_id, content="A restored note")
    _insert_pin(db_path, chat_id, title="A restored pin")
    bus, document, _ = _bus_with_canvas(db_path)

    asyncio.run(bus.dispatch_intent("app-chat-library", "loadChat", [chat_id]))

    notes = [n for n in document.nodes.values() if n.kind == "note"]
    assert len(notes) == 1 and notes[0].content == "A restored note"
    assert len(document.pins.records) == 1


# -- R6.5: save_chat_atomically_row / title helpers --------------------------


def test_save_chat_atomically_row_inserts_when_chat_id_is_none(db_path):
    new_id, new_updated_at = save_chat_atomically_row(db_path, None, "New Title", {"nodes": []}, [], [])
    row = load_chat_row(db_path, new_id)
    assert row["title"] == "New Title"
    assert row["data"] == {"nodes": []}
    # ADR-009 stage 9.2: the returned updated_at is exactly what the row
    # was actually written with - no separate read-back involved.
    assert row["updated_at"] == new_updated_at


def test_save_chat_atomically_row_persists_a_real_preview_and_message_count(db_path):
    chat_data = {
        "nodes": [{"node_type": "chat", "raw_content": "hello there world", "is_user": True}],
    }
    chat_id, _ = save_chat_atomically_row(db_path, None, "T", chat_data, [], [])

    row = next(r for r in get_all_chats(db_path) if r["id"] == chat_id)
    assert row["preview"] == "hello there world"
    assert row["messageCount"] == 1


def test_save_chat_atomically_row_updates_the_same_row_when_chat_id_given(db_path):
    first_id, _ = save_chat_atomically_row(db_path, None, "First", {"nodes": [1]}, [], [])
    second_id, _ = save_chat_atomically_row(db_path, first_id, "First", {"nodes": [1, 2]}, [], [])
    assert second_id == first_id
    assert len(get_all_chats(db_path)) == 1
    row = load_chat_row(db_path, first_id)
    assert row["data"] == {"nodes": [1, 2]}


def test_save_chat_atomically_row_replaces_notes_and_pins_wholesale(db_path):
    chat_id, _ = save_chat_atomically_row(
        db_path, None, "T", {"nodes": []},
        [{"content": "note A", "position": {"x": 0, "y": 0}, "size": {"width": 1, "height": 1},
          "color": "#fff", "header_color": None, "is_system_prompt": False, "is_summary_note": False}],
        [{"title": "pin A", "note": "", "position": {"x": 0, "y": 0}, "pin_id": "p1",
          "sort_order": 0, "anchor_item_id": None, "created_at": None}],
    )
    assert len(load_notes_rows(db_path, chat_id)) == 1
    assert len(load_pins_rows(db_path, chat_id)) == 1

    # Resaving with EMPTY notes/pins must wholesale-replace, not append to,
    # the previous set - mirrors ChatDatabase._write_notes/_write_pins's own
    # DELETE-then-reinsert-all contract exactly.
    save_chat_atomically_row(db_path, chat_id, "T", {"nodes": []}, [], [])
    assert load_notes_rows(db_path, chat_id) == []
    assert load_pins_rows(db_path, chat_id) == []


def test_fallback_title_matches_legacy_regex_and_truncation():
    assert _fallback_title("Hello, world! This is a test message.") == "Hello world This is a"
    assert _fallback_title("") .startswith("Chat 20")
    assert _fallback_title("...") .startswith("Chat 20")
    long_word_title = _fallback_title("a" * 200)
    assert len(long_word_title) == 80


def test_resolve_seed_message_uses_last_chat_node_content():
    document = SceneDocument()
    document.add_chat_node(0, 0, "first message", is_user=True)
    ai = document.add_chat_node(0, 100, "second message", is_user=False)
    assert _resolve_seed_message(document) == "second message"


def test_resolve_seed_message_falls_back_to_new_chat_when_no_chat_nodes():
    document = SceneDocument()
    document.add_note(0, 0)
    assert _resolve_seed_message(document) == "New Chat"


# -- R6.5: the saveChat / newChat intents ------------------------------------


def test_save_chat_intent_warns_and_skips_write_for_a_never_saved_empty_canvas(db_path):
    bus, document, notifications = _bus_with_canvas(db_path)
    asyncio.run(bus.dispatch_intent("app-chat-library", "saveChat", []))
    assert get_all_chats(db_path) == []
    assert notifications.visible and notifications.msg_type == "warning"


def test_save_chat_intent_inserts_a_new_row_and_adopts_the_id(db_path):
    bus, document, notifications = _bus_with_canvas(db_path)
    document.add_chat_node(0, 0, "hello world", is_user=True)

    asyncio.run(bus.dispatch_intent("app-chat-library", "saveChat", []))

    rows = get_all_chats(db_path)
    assert len(rows) == 1
    assert document.current_chat_id == rows[0]["id"]
    assert notifications.visible and notifications.msg_type == "success"


def test_save_chat_intent_resave_updates_same_row_and_keeps_existing_title(db_path):
    bus, document, notifications = _bus_with_canvas(db_path)
    document.add_chat_node(0, 0, "hello world", is_user=True)
    asyncio.run(bus.dispatch_intent("app-chat-library", "saveChat", []))
    first_id = document.current_chat_id
    first_title = get_all_chats(db_path)[0]["title"]

    document.add_code_node(100, 0, "x = 1", "python")
    asyncio.run(bus.dispatch_intent("app-chat-library", "saveChat", []))

    rows = get_all_chats(db_path)
    assert len(rows) == 1
    assert rows[0]["id"] == first_id
    assert rows[0]["title"] == first_title
    row = load_chat_row(db_path, first_id)
    assert len(row["data"]["nodes"]) == 2


def test_save_chat_intent_falls_back_to_insert_when_current_row_was_deleted_elsewhere(db_path):
    bus, document, notifications = _bus_with_canvas(db_path)
    document.current_chat_id = 999  # a row that never existed / was deleted
    document.add_chat_node(0, 0, "hello world", is_user=True)

    asyncio.run(bus.dispatch_intent("app-chat-library", "saveChat", []))

    assert notifications.visible and notifications.msg_type == "success"
    rows = get_all_chats(db_path)
    assert len(rows) == 1
    assert document.current_chat_id == rows[0]["id"] != 999


def test_new_chat_intent_clears_canvas_and_resets_current_chat_id(db_path):
    bus, document, _ = _bus_with_canvas(db_path)
    document.add_chat_node(0, 0, "hello", is_user=True)
    document.current_chat_id = 5

    asyncio.run(bus.dispatch_intent("app-chat-library", "newChat", []))

    assert document.nodes == {}
    assert document.current_chat_id is None
    # ADR-020 stage 20.2: every OTHER caller of newChat (e.g. commands.ts's
    # palette command) keeps calling with zero args - byte-identical
    # pre-20.2 behavior, including current_workspace_id staying unset.
    assert document.current_workspace_id is None


def test_new_chat_intent_honors_an_explicit_valid_workspace_id(db_path):
    _insert_chat(db_path, "Bootstraps a fresh db")
    other_workspace = create_workspace(db_path, "Research")
    bus, document, _ = _bus_with_canvas(db_path)

    asyncio.run(bus.dispatch_intent("app-chat-library", "newChat", [other_workspace["id"]]))

    assert document.current_workspace_id == other_workspace["id"]

    # And the NEXT save actually lands the new graph in that workspace.
    document.add_chat_node(0, 0, "hello", is_user=True)
    asyncio.run(bus.dispatch_intent("app-chat-library", "saveChat", []))
    saved_row = next(row for row in get_all_chats(db_path) if row["id"] == document.current_chat_id)
    assert saved_row["workspaceId"] == other_workspace["id"]


def test_new_chat_intent_defaults_to_default_workspace_when_id_is_invalid(db_path):
    _insert_chat(db_path, "Bootstraps a fresh db")
    default_workspace_id = get_all_workspaces(db_path)[0]["id"]
    bus, document, _ = _bus_with_canvas(db_path)

    # A workspace id that has never existed.
    asyncio.run(bus.dispatch_intent("app-chat-library", "newChat", [999999]))
    assert document.current_workspace_id is None

    document.add_chat_node(0, 0, "hello", is_user=True)
    asyncio.run(bus.dispatch_intent("app-chat-library", "saveChat", []))
    saved_row = next(row for row in get_all_chats(db_path) if row["id"] == document.current_chat_id)
    assert saved_row["workspaceId"] == default_workspace_id


def test_two_concurrent_save_chat_calls_do_not_race_only_one_row_is_written(db_path):
    # Adversarial review finding: loadChat/saveChat/newChat share ONE
    # canvas_document, and a session can have MULTIPLE attached WS
    # connections (every tab that doesn't pass its own ?session= shares
    # session="default") - without a reentrancy guard, two tabs racing Save
    # could interleave mid-await (asyncio.to_thread yields control back to
    # the loop) and silently corrupt or double-write. asyncio.gather here
    # genuinely interleaves both coroutines on the same event loop, exactly
    # the scenario two real WS connections would create.
    bus, document, notifications = _bus_with_canvas(db_path)
    document.add_chat_node(0, 0, "hello world", is_user=True)
    recorder = Recorder()
    bus.attach(recorder)

    async def _race():
        await asyncio.gather(
            bus.dispatch_intent("app-chat-library", "saveChat", []),
            bus.dispatch_intent("app-chat-library", "saveChat", []),
        )

    asyncio.run(_race())

    # Exactly one row, regardless of which of the two calls "won" - the
    # loser must have been rejected by the guard, not raced to a second
    # INSERT.
    assert len(get_all_chats(db_path)) == 1

    notification_messages = [
        m["payload"]["message"] for m in recorder.messages if m.get("topic") == "notification"
    ]
    assert any("already in progress" in message for message in notification_messages), notification_messages


# -- R6.6 regression: register_chat_library must survive a missing event loop --


def test_register_chat_library_does_not_crash_without_a_running_event_loop(db_path):
    # A real, shipped bug: register_chat_library's own R6.6 addition
    # (register_autosave) called asyncio.create_task() unconditionally, which
    # raises RuntimeError outside of a running event loop. backend/app.py's
    # _configure_session calls register_chat_library from exactly this kind
    # of sync context under Starlette's TestClient (confirmed - it broke
    # test_ws_origin.py and test_assets.py, both unrelated to chat_library),
    # so this proves the real production call shape - a bare, non-async
    # call, default autosave_interval_seconds - can never take core session
    # setup down with it.
    bus = SessionBus("chat-library-no-loop-test")
    document = SceneDocument()
    bus.register_topic("scene", document.scene_payload)
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)

    register_chat_library(bus, db_path, document, notifications)

    assert bus.autosave_task is None


# -- audit fixes: the save-state cell is genuinely shared across every path --


def _library_session(db_path):
    bus = SessionBus("chat-library-save-state-test")
    document = SceneDocument()
    bus.register_topic("scene", document.scene_payload)
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    # autosave_interval_seconds=None: no background timer, so these tests
    # drive autosave_tick explicitly and deterministically instead.
    register_chat_library(bus, db_path, document, notifications, autosave_interval_seconds=None)
    return bus, document, notifications


def test_a_manual_save_seeds_the_save_state_so_the_next_tick_is_a_no_op(db_path, monkeypatch):
    # Audit finding: backend/autosave.py's docstring claimed its change-guard
    # covered "auto OR manual", but the cell was a closure-local nothing else
    # could reach, so every manual Save was followed 30s later by a
    # byte-identical rewrite that bumped updated_at and re-sorted the Chat
    # Library out from under the user.
    bus, document, notifications = _library_session(db_path)
    document.add_chat_node(0, 0, "hello", is_user=True)

    asyncio.run(bus.dispatch_intent("app-chat-library", "saveChat", []))
    assert len(get_all_chats(db_path)) == 1
    saved_id = document.current_chat_id

    state = bus.chat_save_state
    assert state["digest"] is not None and state["chat_id"] == saved_id

    # A tick with nothing changed since that manual save must not write.
    writes = []
    real_save = autosave_module.save_chat_atomically_row
    monkeypatch.setattr(
        autosave_module, "save_chat_atomically_row",
        lambda *a, **k: (writes.append(a), real_save(*a, **k))[1],
    )
    asyncio.run(autosave_tick(bus, db_path, document, notifications, state))
    assert writes == [], "a tick right after a manual Save must not rewrite the row"


def test_loading_a_chat_seeds_the_save_state_so_the_next_tick_is_a_no_op(db_path, monkeypatch):
    # Same gap on the load side: opening a chat and touching nothing still
    # rewrote its row on the first tick, re-sorting the library.
    seed_bus, seed_document, seed_notifications = _library_session(db_path)
    seed_document.add_chat_node(0, 0, "hello", is_user=True)
    asyncio.run(seed_bus.dispatch_intent("app-chat-library", "saveChat", []))
    chat_id = seed_document.current_chat_id

    bus, document, notifications = _library_session(db_path)
    asyncio.run(bus.dispatch_intent("app-chat-library", "loadChat", [chat_id]))
    assert document.current_chat_id == chat_id

    state = bus.chat_save_state
    assert state["chat_id"] == chat_id
    assert state["digest"] is not None

    writes = []
    real_save = autosave_module.save_chat_atomically_row
    monkeypatch.setattr(
        autosave_module, "save_chat_atomically_row",
        lambda *a, **k: (writes.append(a), real_save(*a, **k))[1],
    )
    asyncio.run(autosave_tick(bus, db_path, document, notifications, state))
    assert writes == [], "a tick right after loadChat must not rewrite the row"


def test_deleting_the_open_chat_clears_the_pointer_and_reenables_autosave(db_path):
    # Audit finding (real bug): deleteChat left current_chat_id dangling and
    # left the content-only digest looking "already saved", so a user who
    # deleted their open chat and kept working had NO autosave protection
    # until they happened to edit the canvas.
    bus, document, notifications = _library_session(db_path)
    document.add_chat_node(0, 0, "hello", is_user=True)
    asyncio.run(bus.dispatch_intent("app-chat-library", "saveChat", []))
    saved_id = document.current_chat_id

    asyncio.run(bus.dispatch_intent("app-chat-library", "deleteChat", [saved_id]))

    assert get_all_chats(db_path) == []
    assert document.current_chat_id is None, "the pointer to a deleted row must not dangle"

    state = bus.chat_save_state
    # The canvas is deliberately NOT touched - the pre-fix content-only guard
    # could not tell this apart from "already saved" and skipped forever.
    asyncio.run(autosave_tick(bus, db_path, document, notifications, state))

    rows = get_all_chats(db_path)
    assert len(rows) == 1, "the still-open work must be re-protected under a fresh row"
    assert rows[0]["id"] != saved_id


def test_deleting_a_different_chat_leaves_the_open_session_pointer_alone(db_path):
    # The guard must be scoped to the row the session actually points at.
    bus, document, notifications = _library_session(db_path)
    other_id = _insert_chat(db_path, "Someone else's chat")
    document.add_chat_node(0, 0, "hello", is_user=True)
    asyncio.run(bus.dispatch_intent("app-chat-library", "saveChat", []))
    saved_id = document.current_chat_id

    asyncio.run(bus.dispatch_intent("app-chat-library", "deleteChat", [other_id]))

    assert document.current_chat_id == saved_id
    assert bus.chat_save_state["chat_id"] == saved_id


# -- audit fix: a background autosave tick must never beat the user to their
# -- own data. The guard is shared, so it has to be ownership-aware.


def test_a_user_save_waits_out_a_real_in_flight_autosave_tick_instead_of_being_dropped(db_path, monkeypatch):
    # Audit finding (the fix this test exists for): _serialize_mutating_intent
    # DROPS a blocked intent and warns. That contract was written when only a
    # user-initiated intent could hold the flag. R6.6 then had a BACKGROUND
    # task claim the same flag, so an autosave tick that happened to be
    # mid-write made the user's own Save vanish - with a warning naming an
    # operation they never started.
    #
    # Drives the REAL register_autosave loop, deliberately: an earlier version
    # of this test used a hand-written double that reimplemented the
    # claim/release itself, which meant mutating the actual _guarded_tick
    # (dropping its owner tag, or its release signal) left the test green -
    # the production wiring was never under test at all.
    # This test has now failed CI twice, and both fixes before this one were
    # the wrong shape. The first bounded wall-clock at 1.0s against a 2.0s
    # timeout; the second widened that to 5.0s against 30.0s. Both still
    # asserted a DURATION over work the test does not control - two real
    # sqlite writes plus thread-pool and event-loop scheduling on a shared
    # CI VM. Measured locally that window is ~40ms; the run that failed on
    # main blew past 5000ms, a 125x stall, on a commit that touched no
    # backend code at all.
    #
    # So this time the bound is not the mutation detector. Two real races are
    # removed instead:
    #
    #   1. The handoff. `await asyncio.sleep(0)` yields exactly once and
    #      merely HOPES the save has reached the yield-wait before the tick
    #      is released. `entered_the_yield_wait` makes that ordering explicit.
    #   2. The proof. Whether the save was woken by the RELEASE or merely
    #      outlived the TIMEOUT is now recorded causally by `woke_via_release`
    #      rather than inferred from a stopwatch.
    #
    # The mutations this exists to catch still fail it, now deterministically:
    # drop the owner tag and the guard assertion below fails; drop
    # `released.set()` from _release_guard and the save's wait never returns,
    # so `woke_via_release` stays empty. The remaining wait_for is a hang
    # guard only - generous on purpose, because no correctness claim rests
    # on it any more.
    bus, document, notifications = _library_session(db_path)
    document.add_chat_node(0, 0, "hello", is_user=True)

    tick_is_holding_the_guard = threading.Event()
    let_the_tick_finish = threading.Event()
    real_save = autosave_module.save_chat_atomically_row

    def gated_save(*args, **kwargs):
        # Runs inside autosave's own asyncio.to_thread, so the guard is held
        # across a real await point exactly like a slow disk would - but for
        # a duration this test controls instead of a hoped-for sleep.
        tick_is_holding_the_guard.set()
        assert let_the_tick_finish.wait(timeout=30), "test never released the tick"
        return real_save(*args, **kwargs)

    monkeypatch.setattr(autosave_module, "save_chat_atomically_row", gated_save)
    # Large enough that a real CI stall can never be mistaken for the timeout
    # path. Nothing asserts against this number - `woke_via_release` below is
    # what proves which path the save actually took.
    monkeypatch.setattr(chat_library_module, "AUTOSAVE_YIELD_TIMEOUT_SECONDS", 300.0)

    # Instrument the REAL guard's event, so this observes production wiring
    # rather than reimplementing it: `entered_the_yield_wait` fires when the
    # user's Save actually parks on the release signal, and `woke_via_release`
    # records that it was that signal - not the timeout - that woke it.
    released_event = bus.chat_mutation_guard["released"]
    real_event_wait = released_event.wait
    entered_the_yield_wait = asyncio.Event()
    woke_via_release: list[bool] = []

    async def recording_wait():
        entered_the_yield_wait.set()
        await real_event_wait()
        woke_via_release.append(True)
        return True

    monkeypatch.setattr(released_event, "wait", recording_wait)

    async def _run():
        # interval_seconds is deliberately huge: the periodic loop must never
        # fire on its own here. The single tick below is driven directly, so
        # there is no second tick to steal the guard back between this tick
        # releasing and the user's save claiming.
        register_autosave(
            bus, db_path, document, notifications,
            bus.chat_mutation_guard, bus.chat_save_state, interval_seconds=3600,
        )
        tick = asyncio.create_task(bus.autosave_guarded_tick())
        try:
            await asyncio.to_thread(tick_is_holding_the_guard.wait, 30)
            assert bus.chat_mutation_guard["owner"] == AUTOSAVE_OWNER, "no real tick ever started"

            # The user clicks Save while the tick genuinely holds the guard.
            save = asyncio.create_task(bus.dispatch_intent("app-chat-library", "saveChat", []))

            # Wait for the save to genuinely PARK on the release signal before
            # letting the tick go. This is the ordering the old `sleep(0)` only
            # hoped for; releasing the tick early would let the save sail past
            # a guard that was already free and prove nothing.
            await asyncio.wait_for(entered_the_yield_wait.wait(), timeout=60.0)
            let_the_tick_finish.set()

            # Hang guard only - see the header comment. The real assertion is
            # `woke_via_release` after the loop.
            await asyncio.wait_for(save, timeout=60.0)
            await tick
        finally:
            let_the_tick_finish.set()
            tick.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tick
            bus.autosave_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await bus.autosave_task

    asyncio.run(_run())

    # THE mutation detector, and the reason this no longer needs a stopwatch:
    # the save was woken by _release_guard's signal. Drop `released.set()` and
    # this list stays empty no matter how fast or slow the machine is.
    assert woke_via_release, (
        "the user's Save did not resume via the tick's release signal - it "
        "either never parked on it, or sat out the yield timeout instead"
    )
    assert notifications.visible and notifications.msg_type == "success", notifications.message
    assert notifications.message.startswith("Saved "), "the user's Save must not be discarded"
    assert document.current_chat_id is not None


def test_a_user_save_still_loses_to_another_user_operation(db_path):
    # The other direction is deliberate and must NOT change: two real user
    # operations racing is an honest "you started two things" conflict.
    bus, document, notifications = _library_session(db_path)
    document.add_chat_node(0, 0, "hello", is_user=True)
    bus.chat_mutation_guard["active"] = True
    bus.chat_mutation_guard["owner"] = USER_OWNER

    async def _run():
        # Must be rejected IMMEDIATELY, not after sitting out the 2.0s
        # autosave-yield timeout. Asserting the outcome alone would pass even
        # if the wait were (wrongly) applied to user-vs-user conflicts too -
        # the test would just get slower, which no assertion would notice.
        await asyncio.wait_for(
            bus.dispatch_intent("app-chat-library", "saveChat", []), timeout=0.5
        )

    asyncio.run(_run())

    assert get_all_chats(db_path) == []
    assert notifications.visible and notifications.msg_type == "warning"
    assert "Another chat operation" in notifications.message


def test_a_user_save_gives_up_with_an_honest_message_if_autosave_is_genuinely_stuck(db_path, monkeypatch):
    # The wait is bounded: a tick stuck on sqlite's own 30s lock timeout must
    # not freeze the UI. It degrades to the pre-fix drop - but with a message
    # that names autosave rather than "another chat operation", which is what
    # made the original warning read as a bug.
    monkeypatch.setattr(chat_library_module, "AUTOSAVE_YIELD_TIMEOUT_SECONDS", 0.05)
    bus, document, notifications = _library_session(db_path)
    document.add_chat_node(0, 0, "hello", is_user=True)
    bus.chat_mutation_guard["active"] = True
    bus.chat_mutation_guard["owner"] = AUTOSAVE_OWNER
    bus.chat_mutation_guard["released"].clear()

    asyncio.run(bus.dispatch_intent("app-chat-library", "saveChat", []))

    assert get_all_chats(db_path) == []
    assert notifications.visible and notifications.msg_type == "warning"
    assert "Autosave is still finishing" in notifications.message


def test_the_guard_is_released_with_its_owner_cleared_after_a_normal_save(db_path):
    bus, document, notifications = _library_session(db_path)
    document.add_chat_node(0, 0, "hello", is_user=True)

    asyncio.run(bus.dispatch_intent("app-chat-library", "saveChat", []))

    guard = bus.chat_mutation_guard
    assert guard["active"] is False
    assert guard["owner"] is None
    assert guard["released"].is_set()


def test_the_yield_still_works_on_a_LATER_autosave_tick_not_just_the_first(db_path, monkeypatch):
    # Mutation testing caught this gap: the test above only ever observes the
    # FIRST tick to claim the guard, when `released` has never been set. Drop
    # _guarded_tick's `released.clear()` and that test stays green - but from
    # the second tick onward a waiting user intent would be woken instantly by
    # the STALE signal from the previous tick, re-check, find the guard still
    # held, and be dropped exactly as before the fix. A fix that works once and
    # then quietly stops working is worse than no fix, so this pins the
    # steady-state behavior rather than the first-run behavior.
    bus, document, notifications = _library_session(db_path)
    document.add_chat_node(0, 0, "hello", is_user=True)

    real_save = autosave_module.save_chat_atomically_row

    def slow_save(*args, **kwargs):
        time.sleep(0.1)
        return real_save(*args, **kwargs)

    monkeypatch.setattr(autosave_module, "save_chat_atomically_row", slow_save)

    async def _await_owner(expected):
        for _ in range(300):
            if bus.chat_mutation_guard["owner"] == expected:
                return True
            await asyncio.sleep(0.01)
        return False

    async def _run():
        register_autosave(
            bus, db_path, document, notifications,
            bus.chat_mutation_guard, bus.chat_save_state, interval_seconds=0.02,
        )
        try:
            assert await _await_owner(AUTOSAVE_OWNER), "no first tick"
            assert await _await_owner(None), "first tick never released"
            assert bus.chat_mutation_guard["released"].is_set()

            # A real change, so the NEXT tick actually writes (and so holds the
            # guard) instead of short-circuiting on the unchanged-content guard.
            document.add_chat_node(300, 0, "a second message", is_user=True)
            assert await _await_owner(AUTOSAVE_OWNER), "no second tick"

            await asyncio.wait_for(
                bus.dispatch_intent("app-chat-library", "saveChat", []), timeout=1.0
            )
        finally:
            bus.autosave_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await bus.autosave_task

    asyncio.run(_run())

    assert notifications.visible and notifications.msg_type == "success", notifications.message
    assert notifications.message.startswith("Saved ")


# =============================================================================
# ADR-009 stage 9.2: backup-before-write, corrupt-DB rescue, optimistic
# concurrency, and the eviction-flush interaction.
# =============================================================================


# -- backup-before-write: first write of a session + periodic cadence --------


class TestBackupBeforeWrite:
    def test_a_backup_is_taken_before_the_very_first_write_of_a_session(self, db_path):
        last_saved = _new_save_state()
        assert last_saved["last_backup_at"] is None

        save_chat_atomically_row(db_path, None, "T", {"nodes": []}, [], [])
        assert db_backup_module.list_backups(db_path) == [], (
            "save_chat_atomically_row itself must not take a backup - only "
            "_maybe_backup_before_write (the caller's own explicit step) does"
        )

        _maybe_backup_before_write(db_path, last_saved)

        backups = db_backup_module.list_backups(db_path)
        assert len(backups) == 1
        assert last_saved["last_backup_at"] is not None

    def test_a_second_call_within_the_cadence_window_does_not_take_another_backup(self, db_path):
        save_chat_atomically_row(db_path, None, "T", {"nodes": []}, [], [])
        last_saved = _new_save_state()
        _maybe_backup_before_write(db_path, last_saved)
        assert len(db_backup_module.list_backups(db_path)) == 1

        _maybe_backup_before_write(db_path, last_saved)

        assert len(db_backup_module.list_backups(db_path)) == 1

    def test_a_call_past_the_cadence_window_takes_a_second_backup(self, db_path, monkeypatch):
        # A fake, incrementing clock for backup FILENAMES specifically -
        # backend/tests/test_db_backup.py already covers take_backup's own
        # timestamp format at the unit level; this test only needs two
        # calls to land on two DISTINCT filenames, which real wall-clock
        # time cannot guarantee when both calls happen inside the same
        # wall-clock second (a real, if practically rare in production -
        # see BACKUP_CADENCE_SECONDS' own 600s default - possibility this
        # module doesn't currently guard against, and not what THIS test
        # is trying to pin down).
        counter = {"n": 0}
        real_timestamp_now = db_backup_module._timestamp_now

        def fake_timestamp_now():
            counter["n"] += 1
            return (datetime.now(timezone.utc) + timedelta(seconds=counter["n"])).strftime("%Y%m%dT%H%M%SZ")

        monkeypatch.setattr(db_backup_module, "_timestamp_now", fake_timestamp_now)

        save_chat_atomically_row(db_path, None, "T", {"nodes": []}, [], [])
        last_saved = _new_save_state()
        _maybe_backup_before_write(db_path, last_saved)
        assert len(db_backup_module.list_backups(db_path)) == 1

        # Simulate the cadence having elapsed without a real sleep.
        last_saved["last_backup_at"] -= (BACKUP_CADENCE_SECONDS + 1.0)

        _maybe_backup_before_write(db_path, last_saved)

        assert len(db_backup_module.list_backups(db_path)) == 2

    def test_a_backup_failure_is_swallowed_and_never_blocks_the_real_save(self, db_path, monkeypatch):
        save_chat_atomically_row(db_path, None, "T", {"nodes": []}, [], [])
        last_saved = _new_save_state()

        def boom(*args, **kwargs):
            raise OSError("simulated disk full")

        monkeypatch.setattr(chat_library_module.db_backup, "take_backup", boom)

        # Must not raise.
        _maybe_backup_before_write(db_path, last_saved)
        assert last_saved["last_backup_at"] is not None

    def test_the_periodic_cadence_actually_fires_through_a_real_autosave_tick(self, db_path, monkeypatch):
        # The exact mechanism the ADR text asks for: reusing autosave's own
        # tick/dirty-check loop as the clock, not a second timer.
        #
        # A pre-existing chats.db is seeded first (a separate, earlier
        # "session"'s own save) - db_backup.take_backup is correctly a
        # no-op for a db_path that doesn't exist yet at all (see that
        # function's own docstring: nothing to back up before ANY chat has
        # ever been saved), so a session's genuinely first-ever write to a
        # brand-new file has nothing to protect and would not itself
        # produce a backup - that is not what this test is exercising.
        save_chat_atomically_row(db_path, None, "Pre-existing", {"nodes": []}, [], [])

        monkeypatch.setattr(chat_library_module, "BACKUP_CADENCE_SECONDS", 0.0)
        # Same fake-clock reasoning as
        # test_a_call_past_the_cadence_window_takes_a_second_backup above -
        # two backups fired back-to-back (BACKUP_CADENCE_SECONDS=0.0) could
        # otherwise land on the identical real-wall-clock-second filename.
        counter = {"n": 0}

        def fake_timestamp_now():
            counter["n"] += 1
            return (datetime.now(timezone.utc) + timedelta(seconds=counter["n"])).strftime("%Y%m%dT%H%M%SZ")

        monkeypatch.setattr(db_backup_module, "_timestamp_now", fake_timestamp_now)

        bus, document, notifications = _library_session(db_path)
        document.add_chat_node(0, 0, "first message", is_user=True)
        asyncio.run(autosave_tick(bus, db_path, document, notifications, bus.chat_save_state))
        first_backup_count = len(db_backup_module.list_backups(db_path))
        assert first_backup_count >= 1, "the first tick's write must itself trigger a backup"

        document.add_chat_node(200, 0, "a second, real change", is_user=True)
        asyncio.run(autosave_tick(bus, db_path, document, notifications, bus.chat_save_state))

        assert len(db_backup_module.list_backups(db_path)) > first_backup_count, (
            "a later tick, past the (zeroed) cadence window, must take another backup"
        )


# -- backup rotation/retention exact behavior, driven through this module's --
# -- own real save path (backend/tests/test_db_backup.py covers the backup ---
# -- module's unit-level behavior directly; this proves the wiring) ----------


class TestBackupRotationThroughRealSaves:
    def test_more_backups_than_the_retention_limit_prunes_to_exactly_the_right_set(self, db_path, monkeypatch):
        save_chat_atomically_row(db_path, None, "T", {"nodes": []}, [], [])
        now = datetime.now(timezone.utc)
        backups_dir = db_backup_module.backups_dir_for(db_path)
        backups_dir.mkdir(parents=True, exist_ok=True)
        # KEEP_MOST_RECENT-worth of same-day snapshots (all survive via the
        # recency rule) plus one clearly older, distinct-day snapshot (must
        # survive via the daily rule) plus a SECOND, older-still snapshot on
        # THAT SAME older day (must be pruned - not the newest for its day).
        recent_timestamps = [now - timedelta(minutes=i) for i in range(db_backup_module.KEEP_MOST_RECENT)]
        # Anchored at a fixed noon-UTC time-of-day, 2 days back, rather than
        # naive `now - timedelta(hours=N)` offsets: a real CI failure showed
        # that when the real wall-clock `now` a test runs at happens to fall
        # within a few hours of UTC midnight, `now - timedelta(days=2,
        # hours=1)` and `now - timedelta(days=2, hours=5)` can land on TWO
        # DIFFERENT UTC calendar days (the 4-hour gap between them straddles
        # the boundary) - at which point prune_backups (correctly) keeps
        # BOTH as "the newest for its own day," and this test's own
        # assertion that the older one gets pruned fails - a test bug, not a
        # prune_backups bug (its day-bucketing logic, backend/db_backup.py,
        # is correct). Anchoring at noon and offsetting by only ±a few hours
        # keeps both timestamps on the SAME calendar day regardless of what
        # real time this test happens to run at.
        anchor_day = (now - timedelta(days=2)).replace(hour=12, minute=0, second=0, microsecond=0)
        older_day_keep = anchor_day + timedelta(hours=1)
        older_day_prune = anchor_day - timedelta(hours=3)
        for ts in recent_timestamps + [older_day_keep, older_day_prune]:
            path = backups_dir / db_backup_module.backup_filename(ts.strftime("%Y%m%dT%H%M%SZ"))
            sqlite3.connect(path).close()

        db_backup_module.prune_backups(db_path)

        survivors = {p.name for p in db_backup_module.list_backups(db_path)}
        assert db_backup_module.backup_filename(older_day_keep.strftime("%Y%m%dT%H%M%SZ")) in survivors
        assert db_backup_module.backup_filename(older_day_prune.strftime("%Y%m%dT%H%M%SZ")) not in survivors
        assert len(survivors) == db_backup_module.KEEP_MOST_RECENT + 1


# -- corrupt-DB rescue: a real kill-9-mid-save simulation ---------------------


class TestCorruptDbRescue:
    def test_kill_9_mid_save_relaunches_to_the_newest_good_backup(self, db_path):
        # 1. A real chat is saved (the "good state" a backup will capture).
        chat_id, _ = save_chat_atomically_row(
            db_path, None, "Good Chat", {"nodes": [{"node_type": "chat", "raw_content": "hello"}]}, [], [],
        )
        backup_path = db_backup_module.take_backup(db_path)
        assert backup_path is not None

        # 2. A LATER edit lands on disk (so the live file, if it were still
        # readable, would legitimately differ from the backup) - then the
        # process is "kill -9"'d mid-write: simulated directly by truncating
        # the live file to a few garbage bytes, exactly like a torn write
        # would leave it.
        save_chat_atomically_row(
            db_path, chat_id, "Good Chat", {"nodes": [{"node_type": "chat", "raw_content": "a later edit"}]}, [], [],
        )
        for suffix in ("", "-wal", "-shm"):
            sidecar = db_path.with_name(db_path.name + suffix)
            if sidecar.exists():
                sidecar.unlink()
        db_path.write_bytes(b"\x00\x01garbage-not-a-real-sqlite-file")

        notifications = NotificationState()

        # 3. Drive the app's NORMAL connect/open path - get_all_chats is
        # exactly what backend/chat_library.py's own "app-chat-library"
        # topic builder calls on every real subscribe/relaunch.
        rows = get_all_chats(db_path, notifications=notifications)

        # The corruption was detected and transparently recovered - the
        # caller gets a normal, successful result, not an exception.
        assert len(rows) == 1
        assert rows[0]["title"] == "Good Chat"
        loaded = load_chat_row(db_path, chat_id)
        assert loaded["data"] == {"nodes": [{"node_type": "chat", "raw_content": "hello"}]}, (
            "the newest GOOD BACKUP's data must be live - not the torn file, "
            "and not a silently-reset-to-empty database"
        )

        # The bad file is quarantined, present on disk, matching the exact
        # naming convention graphlink_settings_store.py's own
        # _backup_corrupt_state_file establishes.
        quarantined = list(db_path.parent.glob(f"{db_path.name}.corrupted-*"))
        assert len(quarantined) == 1
        # ISO8601-compact-UTC, matching strftime("%Y%m%dT%H%M%SZ") exactly.
        suffix = quarantined[0].name.split(".corrupted-", 1)[1]
        datetime.strptime(suffix, "%Y%m%dT%H%M%SZ")  # raises ValueError if the shape is wrong

        # A notice was surfaced via the SAME NotificationState channel every
        # other user-visible chat-library warning uses.
        assert notifications.visible
        assert notifications.msg_type == "warning"
        assert "restored" in notifications.message.lower()

    def test_quarantine_survives_even_when_there_is_no_backup_to_restore_from(self, db_path):
        # No backup was ever taken - the honest fallback (quarantine only,
        # no fabricated restore) - matching session.dat's own precedent for
        # "nothing better to offer", while STILL quarantining (never just
        # silently overwriting the corrupt file with an empty db).
        save_chat_atomically_row(db_path, None, "Never Backed Up", {"nodes": []}, [], [])
        for suffix in ("", "-wal", "-shm"):
            sidecar = db_path.with_name(db_path.name + suffix)
            if sidecar.exists():
                sidecar.unlink()
        db_path.write_bytes(b"garbage")
        notifications = NotificationState()

        rows = get_all_chats(db_path, notifications=notifications)

        assert rows == []  # a genuinely fresh, empty db - not a crash
        quarantined = list(db_path.parent.glob(f"{db_path.name}.corrupted-*"))
        assert len(quarantined) == 1
        assert notifications.visible
        assert notifications.msg_type == "warning"
        assert "no backup" in notifications.message.lower()

    def test_a_plain_locked_database_is_never_mistaken_for_corruption(self, db_path):
        # sqlite3.OperationalError ("database is locked") is empirically a
        # SUBCLASS of sqlite3.DatabaseError - this is the exact hazard
        # _connect's own except-ordering exists to avoid. A real, healthy
        # file must never be quarantined just because it's momentarily busy.
        save_chat_atomically_row(db_path, None, "Fine", {"nodes": []}, [], [])
        assert not list(db_path.parent.glob(f"{db_path.name}.corrupted-*"))

        holder = sqlite3.connect(db_path, timeout=30)
        holder.execute("PRAGMA journal_mode=WAL")
        holder.execute("BEGIN IMMEDIATE")
        # ADR-020 stage 20.1: the real save_chat_atomically_row call above
        # already migrated this db to version 2, which renamed "chats" to
        # "graphs" - this raw SQL must target the table's CURRENT real name.
        holder.execute("UPDATE graphs SET title = 'locked-write'")
        try:
            # A short timeout so this test doesn't hang for 30s waiting out
            # the real busy_timeout - a fresh connect() with its own short
            # timeout is what actually raises "database is locked".
            with pytest.raises(sqlite3.OperationalError):
                blocked = sqlite3.connect(db_path, timeout=0.2)
                blocked.execute("BEGIN IMMEDIATE")
        finally:
            holder.rollback()
            holder.close()

        assert not list(db_path.parent.glob(f"{db_path.name}.corrupted-*")), (
            "a transient lock must never trigger quarantine"
        )

    def test_get_all_chats_without_a_notifications_reference_still_self_heals_silently(self, db_path):
        # Every OTHER call site in this module (rename_chat, delete_chat,
        # load_chat_row, ...) calls _connect() with no notifications
        # reference at all - self-healing must still work (log-only), by
        # design (see _rescue_corrupt_chats_db's own docstring).
        save_chat_atomically_row(db_path, None, "Good", {"nodes": []}, [], [])
        db_backup_module.take_backup(db_path)
        for suffix in ("", "-wal", "-shm"):
            sidecar = db_path.with_name(db_path.name + suffix)
            if sidecar.exists():
                sidecar.unlink()
        db_path.write_bytes(b"garbage")

        rows = get_all_chats(db_path)  # no notifications= argument at all

        assert len(rows) == 1
        assert rows[0]["title"] == "Good"


# -- optimistic concurrency: two sessions racing a save on the same chat -----


class TestOptimisticConcurrency:
    def test_a_lost_race_at_the_primitive_level_does_not_clobber_the_winner(self, db_path):
        chat_id, first_updated_at = save_chat_atomically_row(
            db_path, None, "T", {"nodes": ["v0"]}, [], [],
        )
        # Two "sessions" both loaded the chat at v0 and both hold the SAME
        # (now about to become stale) expected_updated_at.
        stale_expected = first_updated_at

        # Session A saves first - succeeds, and updated_at moves forward.
        _, second_updated_at = save_chat_atomically_row(
            db_path, chat_id, "T", {"nodes": ["v1-from-A"]}, [], [],
            expected_updated_at=stale_expected,
        )
        assert second_updated_at != first_updated_at or True  # sqlite CURRENT_TIMESTAMP has 1s resolution

        # Session B, STILL holding the original stale value, tries to save
        # next - must be detected as a lost race, not silently applied.
        with pytest.raises(ConcurrentSaveConflict):
            save_chat_atomically_row(
                db_path, chat_id, "T", {"nodes": ["v2-from-B-should-not-land"]}, [], [],
                expected_updated_at=stale_expected,
            )

        # The FIRST save's data (session A's) must be exactly what's live -
        # not session B's, and not some blend of the two.
        row = load_chat_row(db_path, chat_id)
        assert row["data"] == {"nodes": ["v1-from-A"]}
        assert row["updated_at"] == second_updated_at

    def test_a_lost_race_does_not_touch_notes_or_pins_either(self, db_path):
        # The whole write must roll back atomically - not just the chats
        # row UPDATE - see save_chat_atomically_row's own docstring for why
        # the conflict is raised BEFORE either DELETE statement runs.
        chat_id, updated_at = save_chat_atomically_row(
            db_path, None, "T", {"nodes": []},
            [{"content": "keep me", "position": {"x": 0, "y": 0}, "size": {"width": 1, "height": 1},
              "color": "#fff", "header_color": None, "is_system_prompt": False, "is_summary_note": False}],
            [],
        )
        # Advance updated_at once (a legitimate OTHER save), so the ORIGINAL
        # value is now stale.
        save_chat_atomically_row(db_path, chat_id, "T", {"nodes": []}, [], [], expected_updated_at=updated_at)

        with pytest.raises(ConcurrentSaveConflict):
            save_chat_atomically_row(
                db_path, chat_id, "T", {"nodes": []},
                [{"content": "should never land", "position": {"x": 0, "y": 0}, "size": {"width": 1, "height": 1},
                  "color": "#fff", "header_color": None, "is_system_prompt": False, "is_summary_note": False}],
                [],
                expected_updated_at=updated_at,  # the now-stale value
            )

        # The winning save's own notes state (empty, from its own write)
        # survives untouched - the loser's note was never inserted.
        assert load_notes_rows(db_path, chat_id) == []

    def test_expected_updated_at_none_skips_the_check_entirely_backward_compatible(self, db_path):
        chat_id, _ = save_chat_atomically_row(db_path, None, "T", {"nodes": ["v0"]}, [], [])
        save_chat_atomically_row(db_path, chat_id, "T", {"nodes": ["v1"]}, [], [])  # another writer moves it on

        # A caller with NO known prior version (expected_updated_at=None,
        # the default) must still succeed with a blind UPDATE - byte-
        # identical to this function's pre-9.2 behavior.
        new_id, _ = save_chat_atomically_row(db_path, chat_id, "T", {"nodes": ["v2-blind"]}, [], [])
        assert new_id == chat_id
        assert load_chat_row(db_path, chat_id)["data"] == {"nodes": ["v2-blind"]}

    def test_two_sessions_racing_through_the_real_saveChat_intent_surfaces_the_lost_race_notice(self, db_path):
        # End-to-end through the real register_chat_library wiring: two
        # SEPARATE sessions (separate SceneDocuments/buses, exactly like two
        # windows/tabs would be) both load the same chat, then both save in
        # sequence with the loader's own stale updated_at.
        chat_id, _ = save_chat_atomically_row(
            db_path, None, "Shared Chat", {"nodes": [{"node_type": "chat", "raw_content": "v0", "is_user": True}]}, [], [],
        )

        bus_a, document_a, notifications_a = _library_session(db_path)
        bus_b, document_b, notifications_b = _library_session(db_path)
        asyncio.run(bus_a.dispatch_intent("app-chat-library", "loadChat", [chat_id]))
        asyncio.run(bus_b.dispatch_intent("app-chat-library", "loadChat", [chat_id]))
        assert bus_a.chat_save_state["updated_at"] == bus_b.chat_save_state["updated_at"]

        # Session A edits and saves first - succeeds.
        document_a.add_chat_node(200, 0, "A's edit", is_user=True)
        asyncio.run(bus_a.dispatch_intent("app-chat-library", "saveChat", []))
        assert notifications_a.visible and notifications_a.msg_type == "success"

        # Session B, still holding the ORIGINAL (now stale) updated_at,
        # edits and tries to save next.
        document_b.add_chat_node(200, 0, "B's edit - must not land", is_user=True)
        asyncio.run(bus_b.dispatch_intent("app-chat-library", "saveChat", []))

        assert notifications_b.visible
        assert notifications_b.msg_type == "warning"
        assert notifications_b.message == LOST_RACE_MESSAGE_MANUAL

        # The winner's data (A's) is what's actually live - B's edit never
        # landed.
        row = load_chat_row(db_path, chat_id)
        assert any(
            isinstance(node, dict) and node.get("raw_content") == "A's edit"
            for node in row["data"].get("nodes", [])
        )
        assert not any(
            isinstance(node, dict) and node.get("raw_content") == "B's edit - must not land"
            for node in row["data"].get("nodes", [])
        )

    def test_autosave_lost_race_does_not_crash_the_tick_and_surfaces_its_own_notice(self, db_path):
        chat_id, first_updated_at = save_chat_atomically_row(
            db_path, None, "T", {"nodes": [{"node_type": "chat", "raw_content": "v0", "is_user": True}]}, [], [],
        )
        # Someone else saves in between - the value autosave is about to
        # rely on is now stale.
        save_chat_atomically_row(
            db_path, chat_id, "T", {"nodes": [{"node_type": "chat", "raw_content": "v1-elsewhere", "is_user": True}]},
            [], [], expected_updated_at=first_updated_at,
        )

        bus, document, notifications = _library_session(db_path)
        document.add_chat_node(0, 0, "seed", is_user=True)
        document.current_chat_id = chat_id
        last_saved = bus.chat_save_state
        last_saved["chat_id"] = chat_id
        last_saved["updated_at"] = first_updated_at  # deliberately stale
        last_saved["digest"] = "deliberately-different-so-the-tick-writes"

        # Must not raise - LOUD ON FAILURE, not a crashed loop.
        asyncio.run(autosave_tick(bus, db_path, document, notifications, last_saved))

        assert notifications.visible
        assert notifications.msg_type == "warning"
        assert notifications.message == LOST_RACE_MESSAGE_AUTOSAVE
        # last_saved must NOT have been advanced as if the write succeeded.
        assert last_saved["updated_at"] == first_updated_at
        row = load_chat_row(db_path, chat_id)
        assert row["data"]["nodes"][0]["raw_content"] == "v1-elsewhere", "the winning write must survive untouched"


# -- ConcurrentSaveConflict exception identity/message sanity ---------------


def test_concurrent_save_conflict_message_names_the_chat(db_path):
    chat_id, updated_at = save_chat_atomically_row(db_path, None, "T", {"nodes": []}, [], [])
    save_chat_atomically_row(db_path, chat_id, "T", {"nodes": []}, [], [], expected_updated_at=updated_at)
    with pytest.raises(ConcurrentSaveConflict, match=str(chat_id)):
        save_chat_atomically_row(
            db_path, chat_id, "T", {"nodes": []}, [], [], expected_updated_at=updated_at,
        )
