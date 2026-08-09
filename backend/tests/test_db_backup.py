"""ADR-009 stage 9.2: backend/db_backup.py - backup rotation, retention,
and restore.

Every test uses tmp_path (via the db_path fixture) - never Path.home() or
the real ~/.graphlink, matching this codebase's established pattern for
anything touching persistence (see backend/chat_library.py's own tests)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from backend.db_backup import (
    BACKUP_FILENAME_PREFIX,
    KEEP_MOST_RECENT,
    backup_filename,
    backups_dir_for,
    list_backups,
    newest_backup,
    prune_backups,
    restore_from_newest_backup,
    take_backup,
)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "chats.db"


def _make_real_db(db_path, title="Hello"):
    """A minimal but REAL sqlite file (not a stand-in) - take_backup uses
    the sqlite3 backup API, which requires a genuinely openable database,
    not an arbitrary blob."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE chats (id INTEGER PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO chats (title) VALUES (?)", (title,))
        conn.commit()
    finally:
        conn.close()


def _write_fake_backup(db_path, timestamp: str) -> None:
    """Directly creates a backup FILE with a specific timestamp in its
    name, bypassing take_backup's own clock - the only way to deterministically
    test retention across many synthetic points in time without a real
    sleep between each one."""
    backups_dir = backups_dir_for(db_path)
    backups_dir.mkdir(parents=True, exist_ok=True)
    path = backups_dir / backup_filename(timestamp)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE chats (id INTEGER PRIMARY KEY, title TEXT)")
        conn.commit()
    finally:
        conn.close()


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


# -- take_backup: real WAL-safe snapshot via the sqlite3 backup API ----------


def test_take_backup_returns_none_for_a_db_that_does_not_exist_yet(db_path):
    assert take_backup(db_path) is None
    assert not backups_dir_for(db_path).exists()


def test_take_backup_creates_a_real_openable_sqlite_file_with_the_same_data(db_path):
    _make_real_db(db_path, title="Real Chat")

    backup_path = take_backup(db_path)

    assert backup_path is not None
    assert backup_path.exists()
    assert backup_path.parent == backups_dir_for(db_path)
    conn = sqlite3.connect(backup_path)
    try:
        rows = conn.execute("SELECT title FROM chats").fetchall()
    finally:
        conn.close()
    assert rows == [("Real Chat",)]


def test_take_backup_filename_matches_the_documented_convention(db_path):
    _make_real_db(db_path)
    backup_path = take_backup(db_path)
    assert backup_path.name.startswith(BACKUP_FILENAME_PREFIX)
    assert backup_path.name.endswith(".db")
    # The exact timestamp segment must round-trip through list_backups' own
    # parser - proven indirectly via list_backups picking it up below, and
    # directly here via the format string itself.
    raw = backup_path.name[len(BACKUP_FILENAME_PREFIX):-len(".db")]
    datetime.strptime(raw, "%Y%m%dT%H%M%SZ")  # raises ValueError if wrong shape


def test_take_backup_is_wal_safe_against_a_live_writer_holding_a_transaction(db_path):
    # This is the property that distinguishes the backup API from a raw
    # shutil.copy: a writer holding an open transaction on the SOURCE at
    # the exact moment of backup must not produce a torn/inconsistent
    # snapshot, and must not be blocked/broken by the backup itself.
    _make_real_db(db_path, title="Before")
    writer = sqlite3.connect(db_path, timeout=30)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO chats (title) VALUES ('mid-write')")
    try:
        backup_path = take_backup(db_path)
        assert backup_path is not None
        # The uncommitted INSERT must NOT appear in the backup (a real
        # snapshot of a real committed state, not of in-flight work).
        conn = sqlite3.connect(backup_path)
        try:
            rows = {row[0] for row in conn.execute("SELECT title FROM chats").fetchall()}
        finally:
            conn.close()
        assert rows == {"Before"}
    finally:
        writer.rollback()
        writer.close()


def test_take_backup_chmods_the_backup_to_0600(db_path):
    import os
    import stat
    import sys

    if sys.platform == "win32":
        pytest.skip("chmod is a no-op on Windows")
    _make_real_db(db_path)
    backup_path = take_backup(db_path)
    assert stat.S_IMODE(backup_path.stat().st_mode) == 0o600


def test_take_backup_calls_prune_after_every_backup(db_path, monkeypatch):
    _make_real_db(db_path)
    calls = []
    import backend.db_backup as db_backup_module

    real_prune = db_backup_module.prune_backups
    monkeypatch.setattr(
        db_backup_module, "prune_backups", lambda p: (calls.append(p), real_prune(p))[1],
    )
    take_backup(db_path)
    assert calls == [db_path]


# -- list_backups / newest_backup ---------------------------------------------


def test_list_backups_is_empty_when_nothing_has_ever_been_backed_up(db_path):
    assert list_backups(db_path) == []
    assert newest_backup(db_path) is None


def test_list_backups_ignores_unrelated_files_in_the_backups_dir(db_path):
    backups_dir = backups_dir_for(db_path)
    backups_dir.mkdir(parents=True)
    (backups_dir / "not-a-backup.txt").write_text("junk")
    (backups_dir / "chats-not-a-real-timestamp.db").write_text("junk")
    assert list_backups(db_path) == []


def test_list_backups_orders_newest_first(db_path):
    now = datetime.now(timezone.utc)
    _write_fake_backup(db_path, _ts(now - timedelta(minutes=5)))
    _write_fake_backup(db_path, _ts(now))
    _write_fake_backup(db_path, _ts(now - timedelta(minutes=2)))

    names = [p.name for p in list_backups(db_path)]
    assert names == sorted(names, reverse=True)
    assert newest_backup(db_path) == list_backups(db_path)[0]


# -- prune_backups: the exact retention policy --------------------------------


def test_prune_keeps_everything_when_under_the_retention_limit(db_path):
    now = datetime.now(timezone.utc)
    for i in range(KEEP_MOST_RECENT - 2):
        _write_fake_backup(db_path, _ts(now - timedelta(minutes=i)))

    deleted = prune_backups(db_path)

    assert deleted == []
    assert len(list_backups(db_path)) == KEEP_MOST_RECENT - 2


def test_prune_keeps_exactly_the_most_recent_N_when_all_on_the_same_day(db_path):
    # All within the SAME UTC calendar day, well past the retention count -
    # the daily bucket must add NOTHING here (there is no "older, different
    # day" backup for it to rescue), so the surviving set is exactly
    # KEEP_MOST_RECENT, not one more.
    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    total = KEEP_MOST_RECENT + 5
    timestamps = [now - timedelta(minutes=i) for i in range(total)]
    for ts in timestamps:
        _write_fake_backup(db_path, _ts(ts))

    deleted = prune_backups(db_path)

    survivors = list_backups(db_path)
    assert len(survivors) == KEEP_MOST_RECENT
    assert len(deleted) == total - KEEP_MOST_RECENT
    # The survivors are EXACTLY the most-recent N, not an arbitrary subset.
    expected_survivor_names = {backup_filename(_ts(ts)) for ts in timestamps[:KEEP_MOST_RECENT]}
    assert {p.name for p in survivors} == expected_survivor_names


def test_prune_keeps_one_per_calendar_day_beyond_the_recent_window(db_path):
    # Exercises the exact policy this module's own docstring documents:
    # KEEP_MOST_RECENT most recent survive unconditionally, PLUS one
    # (the newest) per distinct UTC calendar day among anything older.
    base = datetime(2026, 1, 20, 12, 0, 0, tzinfo=timezone.utc)

    # KEEP_MOST_RECENT backups, all "today" (base), 1 minute apart -
    # these must ALL survive via the most-recent-N rule alone.
    recent = [base - timedelta(minutes=i) for i in range(KEEP_MOST_RECENT)]
    # Three older backups on three separate distinct days, TWO snapshots
    # on the oldest of those three days (only the newer of that pair
    # should survive via the daily rule).
    day_minus_1_a = base - timedelta(days=1, hours=1)
    day_minus_1_b = base - timedelta(days=1, hours=3)  # older same-day duplicate
    day_minus_2 = base - timedelta(days=2)
    day_minus_5 = base - timedelta(days=5)
    older = [day_minus_1_a, day_minus_1_b, day_minus_2, day_minus_5]

    for ts in recent + older:
        _write_fake_backup(db_path, _ts(ts))

    deleted = prune_backups(db_path)

    survivor_names = {p.name for p in list_backups(db_path)}
    expected_survivors = {backup_filename(_ts(ts)) for ts in recent} | {
        backup_filename(_ts(day_minus_1_a)),  # newer of the day-minus-1 pair
        backup_filename(_ts(day_minus_2)),
        backup_filename(_ts(day_minus_5)),
    }
    assert survivor_names == expected_survivors
    # The older, same-day duplicate is the ONE that must be gone.
    assert backup_filename(_ts(day_minus_1_b)) in {p.name for p in deleted}
    assert len(deleted) == len(recent) + len(older) - len(expected_survivors)


def test_prune_is_idempotent_a_second_call_deletes_nothing_more(db_path):
    now = datetime.now(timezone.utc)
    # Two snapshots per calendar day among the older ones, so there is
    # genuinely something for the daily bucket to prune (one distinct day
    # each would keep every single one, proving nothing about idempotency).
    for i in range(KEEP_MOST_RECENT + 10):
        day = i // 2
        ts = now - timedelta(days=day, hours=(i % 2))
        _write_fake_backup(db_path, _ts(ts))

    first_pass_deleted = prune_backups(db_path)
    assert first_pass_deleted != []

    second_pass_deleted = prune_backups(db_path)
    assert second_pass_deleted == []


def test_prune_tolerates_a_single_backup_it_cannot_delete(db_path, monkeypatch):
    # A precisely deterministic scenario (not "however many happen to be
    # prunable"): KEEP_MOST_RECENT snapshots today (all unconditionally
    # kept), plus exactly ONE older day with TWO snapshots on it - only the
    # OLDER of that pair is prunable at all (the daily rule keeps the
    # newer one), so exactly one deletable backup exists, and it is the
    # one this test makes stuck.
    now = datetime.now(timezone.utc)
    for i in range(KEEP_MOST_RECENT):
        _write_fake_backup(db_path, _ts(now - timedelta(minutes=i)))
    older_day_newer = now - timedelta(days=3, hours=1)
    older_day_older = now - timedelta(days=3, hours=5)
    _write_fake_backup(db_path, _ts(older_day_newer))
    _write_fake_backup(db_path, _ts(older_day_older))

    stuck_path = backups_dir_for(db_path) / backup_filename(_ts(older_day_older))
    assert stuck_path.exists(), "test setup did not produce the expected file"

    import pathlib
    real_unlink = pathlib.Path.unlink

    def flaky_unlink(self, *args, **kwargs):
        if self == stuck_path:
            raise OSError("simulated: file locked")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "unlink", flaky_unlink)

    deleted = prune_backups(db_path)

    assert stuck_path not in deleted
    assert stuck_path.exists(), "a single stuck backup must not block pruning everything else"
    assert deleted == [], "the only prunable backup was the stuck one - nothing else should be touched"
    # The newer of the older-day pair, and every recent one, must all
    # still survive regardless of the stuck-unlink failure elsewhere.
    survivor_names = {p.name for p in list_backups(db_path)}
    assert backup_filename(_ts(older_day_newer)) in survivor_names
    assert stuck_path.name in survivor_names


# -- restore_from_newest_backup ------------------------------------------------


def test_restore_returns_none_when_no_backup_exists(db_path):
    assert restore_from_newest_backup(db_path) is None
    assert not db_path.exists()


def test_restore_copies_the_newest_backups_data_into_db_path(db_path):
    _make_real_db(db_path, title="Version 1")
    take_backup(db_path)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("UPDATE chats SET title = 'Version 2'")
        conn.commit()
    finally:
        conn.close()
    take_backup(db_path)

    # Simulate the live file having since become garbage (the corrupt-DB
    # rescue scenario this function exists for).
    db_path.write_bytes(b"garbage")

    restored_from = restore_from_newest_backup(db_path)

    assert restored_from == newest_backup(db_path) or restored_from is not None
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT title FROM chats").fetchall()
    finally:
        conn.close()
    assert rows == [("Version 2",)], "must restore the NEWEST backup, not just any backup"


def test_restore_is_atomic_no_leftover_temp_file_on_success(db_path):
    _make_real_db(db_path, title="Original")
    take_backup(db_path)
    db_path.write_bytes(b"garbage")

    restore_from_newest_backup(db_path)

    tmp_path = db_path.with_name(db_path.name + ".restoring.tmp")
    assert not tmp_path.exists()


def test_restore_chmods_the_restored_file_to_0600(db_path):
    import stat
    import sys

    if sys.platform == "win32":
        pytest.skip("chmod is a no-op on Windows")
    _make_real_db(db_path)
    take_backup(db_path)
    db_path.write_bytes(b"garbage")

    restore_from_newest_backup(db_path)

    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600


def test_restore_never_touches_the_source_backup_file(db_path):
    _make_real_db(db_path, title="Keep me")
    backup_path = take_backup(db_path)
    original_bytes = backup_path.read_bytes()

    restore_from_newest_backup(db_path)

    assert backup_path.exists()
    assert backup_path.read_bytes() == original_bytes
