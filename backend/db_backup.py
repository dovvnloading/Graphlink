"""ADR-009 stage 9.2: chats.db backup rotation, retention, and restore.

Owns the whole lifecycle of a backup FILE (take one, decide which ones
survive pruning, restore the newest one back into place) - the write/prune
side and the restore side share one module because they share the exact
same filename convention (`_parse_backup_timestamp` is the one place that
convention is parsed back out of a path), and keeping "what counts as a
backup for db_path" in a single spot means the two directions can never
quietly disagree about it.

WAL-SAFE SNAPSHOT, NOT A RAW FILE COPY: take_backup() uses SQLite's own
online backup API (`sqlite3.Connection.backup()`) rather than
`shutil.copy` on the live file. chats.db can genuinely be open elsewhere at
the moment a backup is taken (autosave's own tick, a manual Save, another
window) - a raw byte copy of a file that might be mid-write (even under
WAL, where the main file and the -wal sidecar can be inconsistent with
each other at any instant) could copy a torn, inconsistent snapshot. The
backup API instead reads through SQLite's own page cache/locking exactly
the way a live query would, so the destination is always a complete,
internally-consistent copy of SOME real commit, never a partial one -
this is precisely what the API exists for (it's the same mechanism behind
the `sqlite3` CLI's own `.backup` command and `VACUUM INTO`).

RESTORING is the opposite direction and does NOT need the backup API: the
source is a backup file THIS module itself already wrote via that API into
a directory nothing else ever opens for writing - a plain, static file no
concurrent writer can be mid-write against. A byte copy of an already-
inert file is safe; see restore_from_newest_backup's own docstring for how
that copy is still made atomic against a crash mid-restore.

RETENTION POLICY (the exact numbers, and why): keep the KEEP_MOST_RECENT
(10) most recent snapshots unconditionally, PLUS - among anything OLDER
than that cutoff - the single newest snapshot for each distinct UTC
calendar day still on disk (a "daily" bucket). Backups are taken at most
once per BACKUP_CADENCE_SECONDS (backend/chat_library.py's own constant,
10 minutes) per active session, so keeping the most-recent 10 covers
roughly the last ~100 minutes of activity at full resolution - enough to
recover from "the last several saves introduced a problem" without
special-casing WHY a recent backup might be bad. The daily bucket then
keeps the recovery window open indefinitely into the past (one snapshot
per day, forever) without retention ever growing unbounded - chats.db can
carry embedded image bytes (see backend/canvas.py's own asset-embedding
docstring), so an unbounded "keep everything" policy would grow backups/
without limit on a long-running install.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

BACKUP_FILENAME_PREFIX = "chats-"
BACKUP_FILENAME_SUFFIX = ".db"
_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"

# See this module's own docstring ("RETENTION POLICY") for the full
# reasoning behind this exact number.
KEEP_MOST_RECENT = 10


def backups_dir_for(db_path: Path) -> Path:
    """Mirrors backend/crash_recovery.py's own base_dir override pattern
    (`_data_dir(base_dir)`  Path.home()/".graphlink" by default,
    overridable) WITHOUT a second, independently-overridable parameter to
    keep in sync with db_path's own: backups live NEXT TO the database
    they back up (`db_path.parent / "backups"`). Every test that already
    passes an isolated `tmp_path / "chats.db"` gets an isolated backups/
    directory for free (never the real ~/.graphlink), and the real
    default (`backend/chat_library.py`'s `DEFAULT_DB_PATH`, `Path.home() /
    ".graphlink" / "chats.db"`) naturally lands backups at
    `~/.graphlink/backups/` - exactly the path the ADR text itself
    names."""
    return db_path.parent / "backups"


def _timestamp_now() -> str:
    return datetime.now(timezone.utc).strftime(_TIMESTAMP_FORMAT)


def backup_filename(timestamp: str) -> str:
    return f"{BACKUP_FILENAME_PREFIX}{timestamp}{BACKUP_FILENAME_SUFFIX}"


def _parse_backup_timestamp(path: Path) -> datetime | None:
    """None for anything that isn't one of OUR OWN backup filenames -
    list_backups/prune_backups must never trip over an unrelated file a
    user (or a future feature) happens to drop into the same directory;
    silently ignoring it, not raising, is the safe direction here since
    this is a read-only classification, not a delete decision by itself."""
    name = path.name
    if not (name.startswith(BACKUP_FILENAME_PREFIX) and name.endswith(BACKUP_FILENAME_SUFFIX)):
        return None
    raw = name[len(BACKUP_FILENAME_PREFIX):-len(BACKUP_FILENAME_SUFFIX)]
    try:
        return datetime.strptime(raw, _TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def list_backups(db_path: Path) -> list[Path]:
    """Every recognized backup for db_path, newest first. Empty (not an
    error) when the backups directory doesn't exist yet - a session that
    has never taken a backup is a normal, common state, not a fault."""
    backups_dir = backups_dir_for(db_path)
    if not backups_dir.is_dir():
        return []
    entries: list[tuple[datetime, Path]] = []
    for candidate in backups_dir.iterdir():
        if not candidate.is_file():
            continue
        timestamp = _parse_backup_timestamp(candidate)
        if timestamp is not None:
            entries.append((timestamp, candidate))
    entries.sort(key=lambda pair: pair[0], reverse=True)
    return [path for _, path in entries]


def newest_backup(db_path: Path) -> Path | None:
    backups = list_backups(db_path)
    return backups[0] if backups else None


def prune_backups(db_path: Path) -> list[Path]:
    """Applies the retention policy described in this module's own
    docstring and deletes whatever doesn't survive it. Returns the paths
    actually deleted (empty if nothing needed pruning) - real signal for
    tests, not just a side effect to infer from what's left.

    A single backup that fails to delete (a transient permission/lock
    issue) is logged and skipped, not raised - one stuck file must never
    block every other backup this call would otherwise have pruned, and
    the next prune_backups call (the very next take_backup) will simply
    try it again."""
    backups = list_backups(db_path)  # newest first
    recent = backups[:KEEP_MOST_RECENT]
    older = backups[KEEP_MOST_RECENT:]
    keep: set[Path] = set(recent)

    # A calendar day already represented in the most-recent-N window must
    # NOT also get a second, older representative kept via the daily
    # bucket below - otherwise a burst of activity that happens to fall
    # entirely within one day (the common case: several saves in a single
    # active session) would keep KEEP_MOST_RECENT + 1 backups instead of
    # exactly KEEP_MOST_RECENT, since the (N+1)th-newest backup would
    # always be "the first one seen for its day" from a naive scan of
    # `older` alone.
    covered_days: set[str] = set()
    for path in recent:
        timestamp = _parse_backup_timestamp(path)
        if timestamp is not None:
            covered_days.add(timestamp.strftime("%Y-%m-%d"))

    for path in older:
        # `older` is still newest-first (list_backups' own ordering,
        # sliced) - the FIRST entry seen for a given day is therefore
        # already the newest one for that day, so a plain "seen this day
        # yet?" check is enough; no separate max-by-day pass is needed.
        timestamp = _parse_backup_timestamp(path)
        if timestamp is None:
            continue
        day_key = timestamp.strftime("%Y-%m-%d")
        if day_key not in covered_days:
            covered_days.add(day_key)
            keep.add(path)

    deleted: list[Path] = []
    for path in backups:
        if path in keep:
            continue
        try:
            path.unlink()
            deleted.append(path)
        except OSError:
            logger.warning("could not delete old backup %s - continuing", path)
    return deleted


def take_backup(db_path: Path) -> Path | None:
    """Snapshots db_path into backups_dir_for(db_path) via SQLite's own
    online backup API - see this module's own docstring for why that API,
    not a raw file copy. Returns the new backup's path, or None when
    db_path does not exist yet at all (a session that has never saved
    anything has no file worth snapshotting - this is a normal no-op, not
    an error).

    Written to a temp name first, then os.replace'd into its final name -
    the same atomic-rename pattern graphlink_settings_store.py's own
    _save_state already establishes for exactly this reason: a crash mid-
    backup (the destination .backup() call is itself interrupted) can
    then only ever leave the TEMP file incomplete, never a file wearing a
    real backup's final name that a later restore might trust as
    complete. Calls prune_backups() on every successful backup, so
    retention is enforced continuously rather than needing a separate
    scheduled sweep."""
    if not db_path.exists():
        return None

    backups_dir = backups_dir_for(db_path)
    backups_dir.mkdir(parents=True, exist_ok=True)
    timestamp = _timestamp_now()
    final_name = backup_filename(timestamp)
    final_path = backups_dir / final_name
    tmp_path = backups_dir / f".{final_name}.tmp"

    # A stale .tmp from a previous crash mid-backup must never make
    # sqlite3.connect(tmp_path) below open (and back up INTO) a leftover
    # partial file - always start this backup from a clean slate.
    if tmp_path.exists():
        try:
            tmp_path.unlink()
        except OSError:
            logger.warning("could not remove stale temp backup %s - continuing", tmp_path)

    source_conn = sqlite3.connect(db_path, timeout=30)
    try:
        dest_conn = sqlite3.connect(tmp_path, timeout=30)
        try:
            # chats.db holds real conversation content - same 0600
            # sensitivity as the live file (backend/chat_library.py's own
            # _connect docstring). Chmod'd immediately after the
            # destination file is created, before .backup() writes any
            # page data into it - mirrors _save_state's own "chmod before
            # content lands" ordering.
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                logger.warning("could not chmod %s to 0600 before writing - continuing", tmp_path)
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        source_conn.close()

    try:
        os.chmod(tmp_path, 0o600)
    except OSError:
        logger.warning("could not chmod %s to 0600 - continuing", tmp_path)
    os.replace(tmp_path, final_path)

    prune_backups(db_path)
    return final_path


def restore_from_newest_backup(db_path: Path) -> Path | None:
    """Copies the newest surviving backup for db_path INTO db_path,
    overwriting whatever (if anything) is currently there. Returns the
    backup path that was restored, or None when no backup exists at all
    (db_path is left untouched in that case - see this function's own
    caller, backend/chat_library.py's _rescue_corrupt_chats_db, for why a
    missing-backup outcome must never fabricate a fresh empty file itself:
    that decision belongs one layer up, where it can be reported honestly
    rather than silently masked as "restored").

    ATOMICITY: reads the backup and writes to a TEMP name next to db_path
    first, then os.replace's it into db_path only once the full copy has
    landed and been fsync'd - the same atomic-rename discipline this
    module's own take_backup and graphlink_settings_store.py's own
    _save_state already use. This is the difference between "a second
    crash mid-restore leaves db_path exactly as absent/quarantined as it
    already was" (safe - the caller's own retry lands here again) and "a
    second crash mid-restore leaves a half-written db_path that looks like
    it might be fine" (exactly the kind of half-applied operation ground-
    rule #2 for this whole stage forbids). The destination file is opened
    with mode 0600 from its very first byte (this function's caller only
    ever runs after the corrupt live file has already been fully removed
    from db_path, so there is no pre-existing file whose permissions could
    otherwise be inherited) and chmod'd again explicitly afterward for
    certainty regardless of umask, matching this codebase's established
    "chmod, don't assume" posture elsewhere.

    The source backup file itself is never touched (not deleted, not
    moved) - restoring from it must be repeatable, e.g. if this exact
    restore is itself somehow interrupted and retried."""
    source = newest_backup(db_path)
    if source is None:
        return None

    tmp_path = db_path.with_name(db_path.name + ".restoring.tmp")
    if tmp_path.exists():
        try:
            tmp_path.unlink()
        except OSError:
            logger.warning("could not remove stale restore temp file %s - continuing", tmp_path)

    fd = os.open(tmp_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            logger.warning("could not chmod %s to 0600 - continuing", tmp_path)
        with os.fdopen(fd, "wb") as dest, open(source, "rb") as src:
            shutil.copyfileobj(src, dest)
            dest.flush()
            os.fsync(dest.fileno())
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    os.replace(tmp_path, db_path)
    try:
        os.chmod(db_path, 0o600)
    except OSError:
        logger.warning("could not chmod %s to 0600 - continuing", db_path)
    return source
