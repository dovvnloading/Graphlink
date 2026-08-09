"""ADR-009 stage 9.1: a reusable ordered-migration-chain primitive shared by
`backend/chat_library.py` (chats.db, a real SQLite connection) and
`graphlink_settings_store.py` (session.dat, a plain JSON-loaded dict). Lives
at the repo root, not backend/, for the same reason graphlink_settings_store.py
and graphlink_scratch_dirs.py do: it is imported by BOTH a root-level module
(graphlink_settings_store.py) and a backend/ module (backend/chat_library.py),
and this codebase's dependency direction is backend/ -> root graphlink_*.py,
never the reverse - a root module importing from backend/ would invert that
and risk a circular import the day backend/ needs this module too.

Both runners share one thing: the calling convention is a dict/mapping keyed
by the version a migration function PRODUCES (migration N takes you from
N-1 to N), not the version it starts from - the same convention Rails/Django
migrations use. `_ordered_steps` is the one place that convention is
interpreted; everything else just applies whatever list it returns.

WHY TWO FUNCTIONS, NOT ONE SHARED RUNNER: the two callers have genuinely
different commit semantics - chats.db has a real ACID transaction a failed
step can be rolled back out of by SQLite itself; session.dat is a plain
in-memory dict with no such primitive, and the caller (SettingsManager) owns
the actual atomic temp-file+os.replace disk write, done only after this
module hands back a fully-migrated dict. Forcing both through one "pluggable
commit strategy" function would mean a fake commit/rollback shim on the dict
side standing in for something that doesn't exist there. They share the one
piece that genuinely is identical - `_ordered_steps`, the ordering/gap-
detection planning logic - and stay separate for the part that isn't.
"""

from __future__ import annotations

import copy
import sqlite3
from collections.abc import Callable, Mapping
from typing import Any


class MigrationGapError(RuntimeError):
    """Raised when a migration chain has no registered function for a
    version it must pass through to reach the target. This is a
    configuration bug in the caller's migration table (a step renumbered,
    deleted, or never added) - not a data problem - so it is never caught
    and silently skipped anywhere in this module. Skipping a gap would leave
    the store's version number claiming schema properties that later code
    is entitled to assume are true but that were, in fact, never applied."""


def _ordered_steps(
    current_version: int,
    target_version: int,
    migrations: Mapping[int, Callable[..., Any]],
) -> list[tuple[int, Callable[..., Any]]]:
    """Pure planning step shared by both runners below: given where a store
    currently is and where it needs to end up, returns
    [(version, migration_fn), ...] for every version in
    (current_version, target_version], strictly ascending - regardless of
    what order `migrations` happens to be written or iterated in, since this
    always sorts by the explicit integer key, never dict/list insertion
    order. That is what makes a `{3: fn3, 1: fn1, 2: fn2}` literal apply as
    1 -> 2 -> 3 rather than whatever order Python happened to keep it in.

    Already-at-or-past target (target_version <= current_version) returns an
    empty list - a correct, cheap no-op for both "nothing to do" and a
    theoretical downgrade request (a newer-schema store opened by an older
    build). This function never moves a store backward; it only ever
    refuses to move it forward when there's nothing to apply.

    Raises MigrationGapError up front, before returning anything, if ANY
    version in the required range has no registered function - even one
    hole partway through the range - so a caller can never end up applying
    a chain that silently stops short of target because of a gap, then
    reporting success anyway."""
    if target_version <= current_version:
        return []

    needed_versions = range(current_version + 1, target_version + 1)
    missing = [version for version in needed_versions if version not in migrations]
    if missing:
        raise MigrationGapError(
            f"no migration registered for version(s) {missing} "
            f"(need to go from {current_version} to {target_version})"
        )
    return [(version, migrations[version]) for version in needed_versions]


def run_sqlite_migrations(
    conn: sqlite3.Connection,
    target_version: int,
    migrations: Mapping[int, Callable[[sqlite3.Connection], None]],
) -> int:
    """Applies every migration from `conn`'s current `PRAGMA user_version`
    up to `target_version`, in order, inside a single transaction - and only
    issues `PRAGMA user_version = target_version` after every step has
    succeeded. A migration function raising anything rolls back EVERYTHING
    from this call, including any DDL/PRAGMA earlier steps already ran, so
    `PRAGMA user_version` is left exactly where it was before this call was
    made - never a partially-applied schema wearing a version number that
    claims otherwise.

    Each `migrations[version]` function receives `conn` itself and applies
    its schema change directly against it (CREATE TABLE/ALTER TABLE/data
    backfill/etc.) - it must not commit, rollback, or otherwise manage the
    transaction itself; this function owns that for the whole chain.

    Already-at-or-past target is a correct no-op: returns the current
    version immediately without opening a transaction or touching
    PRAGMA user_version at all (no write, no lock taken) - important since
    this can run on every connection open, not just the first one for a
    given database.

    TRANSACTIONAL SUBTLETY (verified empirically, not from a documentation
    read alone - see this module's own test file): Python's sqlite3 module
    defaults every Connection to "legacy transaction control"
    (isolation_level=""), under which it implicitly COMMITS any open
    transaction before running a non-DML statement - which includes exactly
    the statements a schema migration needs (CREATE TABLE, ALTER TABLE,
    PRAGMA). Under that default, wrapping a migration chain in `with conn:`
    is NOT atomic - each DDL/PRAGMA statement lands on disk immediately as
    it runs, so a later step raising leaves the earlier steps' changes (and
    a stray `PRAGMA user_version` write, if one happened to run before the
    failure) permanently in place despite the exception. This function
    avoids that entirely by taking manual control: it temporarily sets
    `conn.isolation_level = None` (pure autocommit - Python does no implicit
    transaction management at all) and drives `BEGIN`/`COMMIT`/`ROLLBACK`
    itself as literal SQL, which SQLite honors as one real ACID transaction
    covering DDL and PRAGMA exactly the same as DML. The connection's
    original isolation_level is restored in a `finally`, so this call has no
    lasting effect on how the caller's connection behaves afterward.

    Requires `conn` to have no transaction already open when called
    (`conn.in_transaction` must be False) - raises RuntimeError immediately
    if it does, rather than silently taking over a transaction some other
    code on the same connection was already mid-way through. Every caller in
    this codebase opens a fresh connection per call (see
    backend/chat_library.py's `_connect`), so this is never a real
    constraint in practice, only a guard against a future misuse."""
    current_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    target_version = int(target_version)
    steps = _ordered_steps(current_version, target_version, migrations)
    if not steps:
        return current_version

    if conn.in_transaction:
        raise RuntimeError(
            "run_sqlite_migrations requires a connection with no transaction "
            "already open - pass a fresh connection, or one that has just "
            "committed or rolled back."
        )

    previous_isolation_level = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("BEGIN")
        try:
            for _version, migration_fn in steps:
                migration_fn(conn)
            # PRAGMA statements do not accept bound parameters ("?") - the
            # int() cast two lines above and the int() re-cast here are what
            # make splicing this into the SQL string safe (an int can never
            # carry a SQL-injection payload).
            conn.execute(f"PRAGMA user_version = {target_version}")
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()
    finally:
        conn.isolation_level = previous_isolation_level

    return target_version


def run_dict_migrations(
    state: dict[str, Any],
    current_version: int,
    target_version: int,
    migrations: Mapping[int, Callable[[dict[str, Any]], dict[str, Any]]],
) -> tuple[dict[str, Any], int]:
    """Applies every migration from `current_version` up to `target_version`,
    in order, to a plain Python dict (session.dat's already-`json.load`-ed
    state) and returns `(migrated_state, version_landed_on)`. Writes nothing
    to disk - the caller (graphlink_settings_store.py's SettingsManager)
    already owns an atomic temp-file+os.replace write and should persist the
    returned dict itself, stamping whatever version key it uses
    (`state["schema_version"]`, currently) from the returned version. This
    function is deliberately agnostic of that key's name: it takes
    `current_version` as an explicit argument rather than reading a
    hardcoded key out of `state`, so it stays reusable by any future
    dict-shaped store that names its version field differently.

    Each `migrations[version]` function receives the in-progress dict and
    must return the migrated dict (in place mutate-and-return-self, or
    return a new dict - both are accepted, see below).

    Never mutates the caller's `state` argument in place: the very first
    thing this does is take a deep copy, and every migration function in the
    chain is fed that copy (or whatever it returned last), never the
    original object. If any migration raises, the exception propagates
    immediately and `state` - the object the caller passed in - is
    guaranteed byte-for-byte unchanged, exactly as if this had never been
    called; nothing partially migrated ever becomes visible to the caller on
    a failure path, matching this module's SQLite side never leaving
    `PRAGMA user_version` bumped on a failed chain. (The deep copy also
    means a migration function is free to mutate its input in place without
    corrupting the pre-migration `state` the caller still holds a reference
    to - useful for the common case of `state[key] = value; return state`.)

    A migration function that returns `None` (a common accidental bug -
    mutate in place, forget the `return state`) raises TypeError immediately
    rather than silently feeding `None` into the next step in the chain,
    where it would fail confusingly deep inside that step's own `.get(...)`
    call instead of at the actual point of the mistake.

    Already-at-or-past target is a correct no-op: returns `(state,
    current_version)` - note this is the ORIGINAL `state` object, not a
    copy, since nothing needed transforming and there is no reason to pay
    for (or return a different identity than) a copy that was never used."""
    current_version = int(current_version)
    target_version = int(target_version)
    steps = _ordered_steps(current_version, target_version, migrations)
    if not steps:
        return state, current_version

    working_state = copy.deepcopy(state)
    for version, migration_fn in steps:
        working_state = migration_fn(working_state)
        if working_state is None:
            raise TypeError(
                f"migration for version {version} returned None - migration "
                "functions must return the migrated dict, not mutate it and "
                "return nothing"
            )

    return working_state, target_version
