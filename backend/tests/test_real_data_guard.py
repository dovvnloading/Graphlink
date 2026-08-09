"""Proves conftest.py's _never_touch_the_real_user_data_dir guard actually
fires.

A guard that silently never triggers is worse than no guard - it reads as
protection while providing none. This mirrors the same "prove the gate
catches a deliberate regression" convention ADR-019's own CI gates follow:
each test here reproduces the exact real-data access the guard exists to
stop, and asserts it is refused.

See conftest.py's own docstring for the bug this class of guard closes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REAL_DIR = Path.home() / ".graphlink"


def test_constructing_a_settings_manager_on_the_real_session_dat_is_refused():
    from graphlink_settings_store import SettingsManager

    with pytest.raises(AssertionError, match="REAL user data"):
        SettingsManager(REAL_DIR / "session.dat")


def test_the_real_path_is_refused_even_when_reached_via_the_default_argument():
    # The original bug's exact shape: nobody passes the real path explicitly,
    # they just omit the override and let the production default apply.
    from graphlink_settings_store import SettingsManager

    with pytest.raises(AssertionError, match="REAL user data"):
        SettingsManager()


def test_connecting_to_the_real_chats_db_is_refused():
    from backend import chat_library

    with pytest.raises(AssertionError, match="REAL user data"):
        chat_library._connect(REAL_DIR / "chats.db")


def test_creating_the_app_without_path_overrides_is_refused():
    # The end-to-end case: the four helpers that regressed all looked exactly
    # like this - a bare create_app() whose defaults resolve to the real dir.
    from backend.app import create_app

    with pytest.raises(AssertionError, match="REAL user data"):
        create_app()


def test_a_tmp_path_derived_override_is_allowed(tmp_path):
    # The complementary half: a guard that refused EVERYTHING would pass the
    # four tests above while breaking the whole suite, so prove the correct
    # usage still works.
    from graphlink_settings_store import SettingsManager

    manager = SettingsManager(tmp_path / "session.dat")

    assert manager.state_file == tmp_path / "session.dat"
