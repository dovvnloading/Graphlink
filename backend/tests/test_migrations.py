"""Tests for graphlink_migrations.py (ADR-009 stage 9.1): the shared
ordered-migration-chain primitive both chats.db (SQLite) and session.dat
(plain dict) will build their real migration chains on top of.

Every fixture below uses a fresh in-memory or tmp_path-scoped SQLite
connection / plain dict - never a real ~/.graphlink path - matching this
codebase's isolated-test-fixture rule for anything that touches persistence
primitives."""

from __future__ import annotations

import sqlite3

import pytest

import graphlink_migrations as migrations_mod
from graphlink_migrations import (
    MigrationGapError,
    run_dict_migrations,
    run_sqlite_migrations,
)


# ---------------------------------------------------------------------------
# SQLite runner
# ---------------------------------------------------------------------------


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    yield connection
    connection.close()


class TestRunSqliteMigrationsOrdering:
    def test_three_step_chain_applies_in_ascending_order_not_dict_literal_order(self, conn):
        # Deliberately written out of order (3, 1, 2) in the literal - if
        # this ran in insertion/dict order instead of sorted-by-version
        # order, step "3" (which INSERTs into a table step "1" hasn't
        # created yet) would raise immediately.
        applied_order = []

        def step_1(c):
            applied_order.append(1)
            c.execute("CREATE TABLE marker (step INTEGER)")
            c.execute("INSERT INTO marker VALUES (1)")

        def step_2(c):
            applied_order.append(2)
            c.execute("INSERT INTO marker VALUES (2)")

        def step_3(c):
            applied_order.append(3)
            c.execute("INSERT INTO marker VALUES (3)")

        landed = run_sqlite_migrations(conn, 3, {3: step_3, 1: step_1, 2: step_2})

        assert applied_order == [1, 2, 3]
        assert landed == 3
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        rows = [row[0] for row in conn.execute("SELECT step FROM marker ORDER BY step")]
        assert rows == [1, 2, 3]

    def test_partial_range_only_applies_missing_versions(self, conn):
        # Simulates re-opening a chats.db that's already at version 1: only
        # versions 2 and 3 should run, never 1 again.
        conn.execute("PRAGMA user_version = 1")
        applied = []
        migrations = {
            1: lambda c: applied.append(1),
            2: lambda c: applied.append(2),
            3: lambda c: applied.append(3),
        }

        landed = run_sqlite_migrations(conn, 3, migrations)

        assert applied == [2, 3]
        assert landed == 3


class TestRunSqliteMigrationsFailureLeavesVersionUnbumped:
    def test_failing_step_rolls_back_ddl_and_version_bump_together(self, tmp_path):
        # A real ON-DISK db file, not :memory: - re-opening via a BRAND NEW
        # connection afterward is the whole point of this test: it proves
        # the rollback actually reached the file, not just this process's
        # in-memory view of a connection that might be lying to itself.
        db_path = tmp_path / "chats.db"
        setup_conn = sqlite3.connect(db_path)
        setup_conn.close()

        def step_1(c):
            c.execute("CREATE TABLE t1 (id INTEGER)")

        def step_2_raises(c):
            c.execute("CREATE TABLE t2 (id INTEGER)")
            raise RuntimeError("boom mid-migration")

        def step_3_never_runs(c):
            c.execute("CREATE TABLE t3 (id INTEGER)")

        conn1 = sqlite3.connect(db_path)
        with pytest.raises(RuntimeError, match="boom mid-migration"):
            run_sqlite_migrations(
                conn1, 3, {1: step_1, 2: step_2_raises, 3: step_3_never_runs}
            )
        conn1.close()

        # Fresh connection, fresh process-level view of the file on disk.
        conn2 = sqlite3.connect(db_path)
        try:
            assert conn2.execute("PRAGMA user_version").fetchone()[0] == 0
            table_names = {
                row[0]
                for row in conn2.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            assert table_names == set(), (
                "step_1's CREATE TABLE t1 and step_2's CREATE TABLE t2 must both "
                "be rolled back together with the failed step, not left behind "
                "as a partially-applied schema"
            )
        finally:
            conn2.close()

    def test_original_isolation_level_restored_after_failure(self, conn):
        original = conn.isolation_level

        def raises(c):
            raise ValueError("nope")

        with pytest.raises(ValueError):
            run_sqlite_migrations(conn, 1, {1: raises})

        assert conn.isolation_level == original

    def test_original_isolation_level_restored_after_success(self, conn):
        original = conn.isolation_level

        run_sqlite_migrations(conn, 1, {1: lambda c: None})

        assert conn.isolation_level == original


class TestRunSqliteMigrationsNoOp:
    def test_already_at_target_is_a_noop(self, conn):
        conn.execute("PRAGMA user_version = 5")
        called = []

        landed = run_sqlite_migrations(conn, 5, {5: lambda c: called.append(True)})

        assert landed == 5
        assert called == []

    def test_current_ahead_of_target_is_a_noop_not_a_downgrade(self, conn):
        conn.execute("PRAGMA user_version = 7")
        called = []

        landed = run_sqlite_migrations(conn, 3, {1: lambda c: called.append(1)})

        assert landed == 7
        assert called == []
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7

    def test_noop_does_not_require_migrations_dict_to_cover_current_version(self, conn):
        # Already-at-target must short-circuit before gap detection ever
        # looks at the migrations mapping - an empty mapping must not raise.
        landed = run_sqlite_migrations(conn, 0, {})
        assert landed == 0


class TestRunSqliteMigrationsMissingFunctionRaisesLoudly:
    def test_gap_in_the_middle_of_the_range_raises_before_applying_anything(self, conn):
        applied = []
        # version 2 is missing entirely.
        migrations = {1: lambda c: applied.append(1), 3: lambda c: applied.append(3)}

        with pytest.raises(MigrationGapError, match=r"\[2\]"):
            run_sqlite_migrations(conn, 3, migrations)

        assert applied == [], "no step may run when the chain has a gap in it"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0

    def test_gap_error_is_not_a_generic_exception_type(self, conn):
        assert issubclass(MigrationGapError, RuntimeError)


class TestRunSqliteMigrationsRequiresFreshConnection:
    def test_open_transaction_on_entry_raises(self, conn):
        conn.execute("BEGIN")
        conn.execute("CREATE TABLE pending (id INTEGER)")
        try:
            with pytest.raises(RuntimeError, match="no transaction already open"):
                run_sqlite_migrations(conn, 1, {1: lambda c: None})
        finally:
            conn.rollback()


# ---------------------------------------------------------------------------
# Dict runner
# ---------------------------------------------------------------------------


class TestRunDictMigrationsOrdering:
    def test_three_step_chain_applies_in_ascending_order_not_dict_literal_order(self):
        applied_order = []

        def step_1(state):
            applied_order.append(1)
            state = dict(state)
            state["log"] = state.get("log", []) + [1]
            return state

        def step_2(state):
            applied_order.append(2)
            state = dict(state)
            state["log"] = state.get("log", []) + [2]
            return state

        def step_3(state):
            applied_order.append(3)
            state = dict(state)
            state["log"] = state.get("log", []) + [3]
            return state

        result, landed = run_dict_migrations(
            {"log": []}, 0, 3, {3: step_3, 1: step_1, 2: step_2}
        )

        assert applied_order == [1, 2, 3]
        assert result["log"] == [1, 2, 3]
        assert landed == 3


class TestRunDictMigrationsFailureLeavesInputUntouched:
    def test_failing_step_propagates_and_original_state_is_unchanged(self):
        original_state = {"schema_version": 0, "value": "original"}

        def step_1(state):
            state["value"] = "changed by step 1"
            return state

        def step_2_raises(state):
            state["value"] = "changed by step 2, but about to blow up"
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            run_dict_migrations(original_state, 0, 2, {1: step_1, 2: step_2_raises})

        # The caller's own dict object must come back exactly as it went
        # in - migration functions were fed a deep copy, not this object.
        assert original_state == {"schema_version": 0, "value": "original"}

    def test_none_return_from_a_migration_raises_typeerror_not_silently_propagating(self):
        def forgets_to_return(state):
            state["touched"] = True
            # no return -> None

        with pytest.raises(TypeError, match="returned None"):
            run_dict_migrations({}, 0, 1, {1: forgets_to_return})


class TestRunDictMigrationsNoOp:
    def test_already_at_target_is_a_noop_and_returns_same_object_identity(self):
        original_state = {"schema_version": 4}
        called = []

        result, landed = run_dict_migrations(
            original_state, 4, 4, {4: lambda s: called.append(True) or s}
        )

        assert landed == 4
        assert called == []
        assert result is original_state

    def test_current_ahead_of_target_is_a_noop(self):
        original_state = {"schema_version": 9}
        called = []

        result, landed = run_dict_migrations(
            original_state, 9, 3, {1: lambda s: called.append(1) or s}
        )

        assert landed == 9
        assert called == []
        assert result is original_state


class TestRunDictMigrationsMissingFunctionRaisesLoudly:
    def test_gap_in_the_middle_of_the_range_raises_before_applying_anything(self):
        applied = []
        original_state = {"value": "untouched"}

        def step_1(state):
            applied.append(1)
            return state

        def step_3(state):
            applied.append(3)
            return state

        with pytest.raises(MigrationGapError, match=r"\[2\]"):
            run_dict_migrations(original_state, 0, 3, {1: step_1, 3: step_3})

        assert applied == []
        assert original_state == {"value": "untouched"}


# ---------------------------------------------------------------------------
# Shared planning helper (exercised indirectly above; a couple of direct
# checks here since both public runners depend on it for correctness).
# ---------------------------------------------------------------------------


class TestOrderedStepsHelper:
    def test_empty_migrations_and_target_equal_current_returns_empty_list(self):
        assert migrations_mod._ordered_steps(2, 2, {}) == []

    def test_full_gap_check_lists_every_missing_version_not_just_the_first(self):
        with pytest.raises(MigrationGapError, match=r"\[2, 4\]"):
            migrations_mod._ordered_steps(0, 4, {1: object(), 3: object()})
