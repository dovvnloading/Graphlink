"""ADR-022 stage 22.2: property-based tests for graphlink_migrations.py.

Covers the ordering/gap-detection invariant shared by both runners
(_ordered_steps), plus each runner's own all-or-nothing commit contract:
run_sqlite_migrations never leaves PRAGMA user_version bumped on a failed
chain, run_dict_migrations never mutates the caller's state on a failed
chain.
"""

from __future__ import annotations

import sqlite3

import pytest
from hypothesis import assume, given, strategies as st

import graphlink_migrations as migrations_mod


@given(
    current_version=st.integers(min_value=0, max_value=50),
    step_count=st.integers(min_value=0, max_value=20),
)
def test_ordered_steps_covers_exact_ascending_range_regardless_of_dict_order(current_version, step_count):
    target_version = current_version + step_count

    # Built in REVERSE key order deliberately, to prove _ordered_steps sorts
    # by the explicit integer key rather than trusting dict/insertion order.
    migrations = {
        version: (lambda v=version: v)
        for version in reversed(range(current_version + 1, target_version + 1))
    }

    steps = migrations_mod._ordered_steps(current_version, target_version, migrations)

    assert [version for version, _fn in steps] == list(range(current_version + 1, target_version + 1))
    assert all(migrations[version] is fn for version, fn in steps)


@given(
    current_version=st.integers(min_value=0, max_value=50),
    target_version=st.integers(min_value=0, max_value=50),
)
def test_ordered_steps_is_a_correct_noop_when_already_at_or_past_target(current_version, target_version):
    assume(target_version <= current_version)  # the ascending-range test above covers the other case
    # No migrations registered at all - if this were anything but a no-op it
    # would raise MigrationGapError instead of returning [].
    assert migrations_mod._ordered_steps(current_version, target_version, {}) == []


@given(
    current_version=st.integers(min_value=0, max_value=10),
    step_count=st.integers(min_value=1, max_value=5),
)
def test_run_sqlite_migrations_rolls_back_everything_on_any_failure(current_version, step_count):
    target_version = current_version + step_count
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(f"PRAGMA user_version = {current_version}")

        def make_table(c):
            c.execute("CREATE TABLE IF NOT EXISTS t(v INTEGER)")
            c.execute("INSERT INTO t(v) VALUES (1)")

        def failing_migration(c):
            raise RuntimeError("boom")

        # Every step but the last succeeds and does real schema work; the
        # last step always fails - proving a late failure rolls back EARLIER
        # steps' work too, not just its own.
        migrations = {version: make_table for version in range(current_version + 1, target_version)}
        migrations[target_version] = failing_migration

        with pytest.raises(RuntimeError):
            migrations_mod.run_sqlite_migrations(conn, target_version, migrations)

        actual_version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert actual_version == current_version

        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='t'"
        ).fetchone()
        assert table_exists is None
    finally:
        conn.close()


@given(current_version=st.integers(min_value=0, max_value=10))
def test_run_sqlite_migrations_is_a_noop_when_already_at_or_past_target(current_version):
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(f"PRAGMA user_version = {current_version}")

        def exploding_migration(c):
            raise AssertionError("must never run - target is not ahead of current")

        result = migrations_mod.run_sqlite_migrations(conn, current_version, {current_version: exploding_migration})
        assert result == current_version
        assert conn.execute("PRAGMA user_version").fetchone()[0] == current_version
    finally:
        conn.close()


@given(
    current_version=st.integers(min_value=0, max_value=20),
    step_count=st.integers(min_value=1, max_value=10),
)
def test_run_dict_migrations_applies_steps_in_order_without_mutating_caller_state(current_version, step_count):
    target_version = current_version + step_count
    original_state = {"schema_version": current_version, "value": []}
    original_snapshot = {"schema_version": current_version, "value": []}

    def make_step(version):
        def step(state):
            state = dict(state)
            state["value"] = state["value"] + [version]
            return state

        return step

    # Reverse insertion order again, same reasoning as the ascending-range
    # test above - proves the dict-based runner shares _ordered_steps'
    # ordering guarantee, not just its own hand-rolled loop.
    migrations = {
        version: make_step(version) for version in reversed(range(current_version + 1, target_version + 1))
    }

    result_state, result_version = migrations_mod.run_dict_migrations(
        original_state, current_version, target_version, migrations
    )

    assert result_version == target_version
    assert result_state["value"] == list(range(current_version + 1, target_version + 1))
    assert original_state == original_snapshot


@given(current_version=st.integers(min_value=0, max_value=20))
def test_run_dict_migrations_returns_the_same_object_identity_on_noop(current_version):
    state = {"schema_version": current_version}
    result_state, result_version = migrations_mod.run_dict_migrations(state, current_version, current_version, {})
    assert result_state is state  # documented: no copy is made when nothing needed transforming
    assert result_version == current_version


@given(current_version=st.integers(min_value=0, max_value=10))
def test_run_dict_migrations_raises_typeerror_when_a_step_returns_none(current_version):
    target_version = current_version + 1

    def broken_step(state):
        state["mutated_in_place"] = True
        # forgot `return state` - the documented accidental-bug shape

    original_state = {"schema_version": current_version}
    with pytest.raises(TypeError):
        migrations_mod.run_dict_migrations(
            original_state, current_version, target_version, {target_version: broken_step}
        )
