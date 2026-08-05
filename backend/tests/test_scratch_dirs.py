"""ADR-005 stage 5.3: graphlink_scratch_dirs.py's own unit tests -
permissioning (prepare_scratch_dir) and the three GC entry points
(remove_scratch_dir, gc_stale_by_age, sweep_stale_scratch_dirs_on_launch).

chmod's real effect is POSIX-only (os.chmod on Windows only ever toggles the
read-only attribute, never real permission bits) - same split this codebase
already uses for graphlink_settings_store.py/chat_library.py's own 0600 file
chmods: a platform-independent "chmod was invoked with 0o700" spy test that
runs everywhere, plus a POSIX-only real-stat test skipped on Windows.
"""

from __future__ import annotations

import os
import stat
import sys
import time

import pytest

import graphlink_scratch_dirs as scratch_dirs

POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="POSIX chmod semantics only apply on POSIX")


# -- safe_scratch_id -----------------------------------------------------


class TestSafeScratchId:
    def test_alnum_and_hyphen_underscore_pass_through_unchanged(self):
        assert scratch_dirs.safe_scratch_id("node-123_ABC") == "node-123_ABC"

    def test_disallowed_characters_are_replaced_with_underscore(self):
        assert scratch_dirs.safe_scratch_id("../../etc/passwd") == "______etc_passwd"
        assert scratch_dirs.safe_scratch_id("n1:n2 n3") == "n1_n2_n3"

    def test_blank_or_none_falls_back_to_default(self):
        assert scratch_dirs.safe_scratch_id("") == "default"
        assert scratch_dirs.safe_scratch_id(None) == "default"


# -- prepare_scratch_dir ---------------------------------------------------


class TestPrepareScratchDir:
    def test_creates_the_directory_including_parents(self, tmp_path):
        target = tmp_path / "a" / "b" / "c"

        scratch_dirs.prepare_scratch_dir(target)

        assert target.is_dir()

    def test_is_idempotent_against_an_existing_directory(self, tmp_path):
        target = tmp_path / "existing"
        target.mkdir()

        scratch_dirs.prepare_scratch_dir(target)  # must not raise

        assert target.is_dir()

    def test_chmod_is_invoked_with_0700_on_posix(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scratch_dirs.sys, "platform", "linux")
        calls = []
        monkeypatch.setattr(scratch_dirs.os, "chmod", lambda path, mode: calls.append((path, mode)))

        target = tmp_path / "sandbox"
        scratch_dirs.prepare_scratch_dir(target)

        assert (target, 0o700) in calls

    def test_chmod_is_not_invoked_on_windows(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scratch_dirs.sys, "platform", "win32")
        calls = []
        monkeypatch.setattr(scratch_dirs.os, "chmod", lambda path, mode: calls.append((path, mode)))

        scratch_dirs.prepare_scratch_dir(tmp_path / "sandbox")

        assert calls == []

    def test_a_chmod_failure_does_not_raise(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scratch_dirs.sys, "platform", "linux")

        def _boom(path, mode):
            raise OSError("permission denied")

        monkeypatch.setattr(scratch_dirs.os, "chmod", _boom)

        scratch_dirs.prepare_scratch_dir(tmp_path / "sandbox")  # must not raise

    @POSIX_ONLY
    def test_the_real_directory_is_actually_0700_on_posix(self, tmp_path):
        target = tmp_path / "sandbox"

        scratch_dirs.prepare_scratch_dir(target)

        assert stat.S_IMODE(target.stat().st_mode) == 0o700

    @POSIX_ONLY
    def test_re_running_on_an_existing_looser_directory_tightens_it_back_to_0700(self, tmp_path):
        target = tmp_path / "sandbox"
        target.mkdir()
        os.chmod(target, 0o755)

        scratch_dirs.prepare_scratch_dir(target)

        assert stat.S_IMODE(target.stat().st_mode) == 0o700


# -- remove_scratch_dir -----------------------------------------------------


class TestRemoveScratchDir:
    def test_removes_an_existing_directory_and_its_contents(self, tmp_path):
        target = tmp_path / "sandbox"
        target.mkdir()
        (target / "file.txt").write_text("data", encoding="utf-8")
        (target / "nested").mkdir()
        (target / "nested" / "inner.txt").write_text("data", encoding="utf-8")

        scratch_dirs.remove_scratch_dir(target)

        assert not target.exists()

    def test_a_missing_directory_is_a_silent_no_op(self, tmp_path):
        scratch_dirs.remove_scratch_dir(tmp_path / "never-existed")  # must not raise

    def test_an_os_error_that_survives_the_retry_is_logged_and_swallowed(self, tmp_path, monkeypatch):
        target = tmp_path / "sandbox"
        target.mkdir()
        calls = []

        def _boom(path):
            calls.append(path)
            raise PermissionError("file still in use")

        monkeypatch.setattr(scratch_dirs.shutil, "rmtree", _boom)
        monkeypatch.setattr(scratch_dirs.time, "sleep", lambda _seconds: None)

        scratch_dirs.remove_scratch_dir(target)  # must not raise

        assert len(calls) == 2, "one retry, not zero and not unbounded"

    def test_retries_once_after_a_transient_failure_then_succeeds(self, tmp_path, monkeypatch):
        # ADR-005 stage 5.3 (review-fix): a still-terminating subprocess can
        # briefly hold a handle open even after it has been asked to stop
        # (e.g. code_sandbox's cooperative, ~100ms-polled cancel) - one
        # retry meaningfully improves the odds of a clean removal without
        # any architectural change.
        target = tmp_path / "sandbox"
        target.mkdir()
        real_rmtree = scratch_dirs.shutil.rmtree
        attempts = {"count": 0}
        sleep_calls = []

        def _flaky_rmtree(path):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise PermissionError("file still in use")
            real_rmtree(path)

        monkeypatch.setattr(scratch_dirs.shutil, "rmtree", _flaky_rmtree)
        monkeypatch.setattr(scratch_dirs.time, "sleep", lambda seconds: sleep_calls.append(seconds))

        scratch_dirs.remove_scratch_dir(target)

        assert not target.exists(), "the second attempt must actually remove the directory"
        assert attempts["count"] == 2
        assert sleep_calls == [0.25]


# -- remove_scratch_dir_for_id -----------------------------------------


class TestRemoveScratchDirForId:
    def test_removes_the_directory_derived_from_the_raw_id(self, tmp_path):
        root = tmp_path / "root"
        target = root / "n1"
        target.mkdir(parents=True)

        scratch_dirs.remove_scratch_dir_for_id(root, "n1")

        assert not target.exists()

    def test_sanitizes_the_raw_id_the_same_way_prepare_scratch_dir_callers_do(self, tmp_path):
        root = tmp_path / "root"
        target = root / scratch_dirs.safe_scratch_id("weird:id/../x")
        target.mkdir(parents=True)

        scratch_dirs.remove_scratch_dir_for_id(root, "weird:id/../x")

        assert not target.exists()

    def test_refuses_to_touch_the_shared_default_bucket_for_a_blank_id(self, tmp_path):
        # ADR-005 stage 5.3 (review-fix): a blank id resolves to the same
        # "default" directory a DIFFERENT blank-id node could also be
        # using - rmtree-ing it because ONE such node was deleted would
        # destroy the other's still-live directory too.
        root = tmp_path / "root"
        shared_default = root / "default"
        shared_default.mkdir(parents=True)
        (shared_default / "someone_elses_file.txt").write_text("data", encoding="utf-8")

        scratch_dirs.remove_scratch_dir_for_id(root, "")

        assert shared_default.exists(), "a blank id must never trigger removal of the shared bucket"
        assert (shared_default / "someone_elses_file.txt").exists()

    def test_refuses_to_touch_the_shared_default_bucket_for_none(self, tmp_path):
        root = tmp_path / "root"
        shared_default = root / "default"
        shared_default.mkdir(parents=True)

        scratch_dirs.remove_scratch_dir_for_id(root, None)

        assert shared_default.exists()


# -- touch_scratch_dir_usage --------------------------------------------


class TestTouchScratchDirUsage:
    def test_bumps_mtime_to_now(self, tmp_path):
        target = tmp_path / "sandbox"
        target.mkdir()
        old_time = time.time() - 1000
        os.utime(target, (old_time, old_time))
        assert target.stat().st_mtime < time.time() - 500

        scratch_dirs.touch_scratch_dir_usage(target)

        assert target.stat().st_mtime > time.time() - 5

    def test_a_missing_directory_does_not_raise(self, tmp_path):
        scratch_dirs.touch_scratch_dir_usage(tmp_path / "never-existed")  # must not raise

    def test_an_os_error_is_logged_and_swallowed(self, tmp_path, monkeypatch):
        def _boom(path, times):
            raise OSError("disk unavailable")

        monkeypatch.setattr(scratch_dirs.os, "utime", _boom)

        scratch_dirs.touch_scratch_dir_usage(tmp_path)  # must not raise

    def test_used_directory_survives_an_age_sweep_that_would_otherwise_reap_it(self, tmp_path):
        # ADR-005 stage 5.3 (review-fix): the actual bug this closes - a
        # directory created long ago but genuinely still in active use
        # must not be indistinguishable, to gc_stale_by_age, from one
        # abandoned the day after creation.
        root = tmp_path / "root"
        target = _touch_dir_with_age(root, "actively-used", age_seconds=1000)

        scratch_dirs.touch_scratch_dir_usage(target)
        removed = scratch_dirs.gc_stale_by_age(root, max_age_seconds=100)

        assert removed == []
        assert target.exists()


# -- gc_stale_by_age ----------------------------------------------------


def _touch_dir_with_age(root, name, age_seconds):
    path = root / name
    path.mkdir(parents=True)
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))
    return path


class TestGcStaleByAge:
    def test_a_root_that_does_not_exist_yet_is_a_silent_no_op(self, tmp_path):
        assert scratch_dirs.gc_stale_by_age(tmp_path / "never-created", max_age_seconds=60) == []

    def test_removes_only_children_older_than_max_age(self, tmp_path):
        root = tmp_path / "root"
        old_dir = _touch_dir_with_age(root, "old", age_seconds=1000)
        fresh_dir = _touch_dir_with_age(root, "fresh", age_seconds=10)

        removed = scratch_dirs.gc_stale_by_age(root, max_age_seconds=100)

        assert removed == [old_dir]
        assert not old_dir.exists()
        assert fresh_dir.exists()

    def test_nothing_removed_when_every_child_is_within_the_age_window(self, tmp_path):
        root = tmp_path / "root"
        fresh_dir = _touch_dir_with_age(root, "fresh", age_seconds=5)

        removed = scratch_dirs.gc_stale_by_age(root, max_age_seconds=scratch_dirs.DEFAULT_MAX_AGE_SECONDS)

        assert removed == []
        assert fresh_dir.exists()


# -- sweep_stale_scratch_dirs_on_launch --------------------------------


class TestSweepStaleScratchDirsOnLaunch:
    def test_sweeps_both_roots(self, tmp_path, monkeypatch):
        pycoder_root = tmp_path / "pycoder"
        sandbox_root = tmp_path / "sandbox"
        old_pycoder_dir = _touch_dir_with_age(pycoder_root, "old-node", age_seconds=1000)
        old_sandbox_dir = _touch_dir_with_age(sandbox_root, "old-sandbox", age_seconds=1000)
        monkeypatch.setattr(scratch_dirs, "PYCODER_REPL_ROOT", pycoder_root)
        monkeypatch.setattr(scratch_dirs, "EXECUTION_SANDBOX_ROOT", sandbox_root)

        scratch_dirs.sweep_stale_scratch_dirs_on_launch(max_age_seconds=100)

        assert not old_pycoder_dir.exists()
        assert not old_sandbox_dir.exists()

    def test_a_failure_sweeping_one_root_does_not_stop_the_other(self, tmp_path, monkeypatch):
        pycoder_root = tmp_path / "pycoder"
        sandbox_root = tmp_path / "sandbox"
        old_sandbox_dir = _touch_dir_with_age(sandbox_root, "old-sandbox", age_seconds=1000)
        monkeypatch.setattr(scratch_dirs, "PYCODER_REPL_ROOT", pycoder_root)
        monkeypatch.setattr(scratch_dirs, "EXECUTION_SANDBOX_ROOT", sandbox_root)

        real_gc = scratch_dirs.gc_stale_by_age

        def _flaky_gc(root, max_age_seconds=scratch_dirs.DEFAULT_MAX_AGE_SECONDS):
            if root == pycoder_root:
                raise OSError("network share unavailable")
            return real_gc(root, max_age_seconds)

        monkeypatch.setattr(scratch_dirs, "gc_stale_by_age", _flaky_gc)

        scratch_dirs.sweep_stale_scratch_dirs_on_launch(max_age_seconds=100)  # must not raise

        assert not old_sandbox_dir.exists(), "the second root must still be swept despite the first raising"
