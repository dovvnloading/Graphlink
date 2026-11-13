"""Real Windows tests of creation-time Job Object membership (issue #545).

These tests are intentionally harmless: marker writes, pipe output and a
sleeping Python grandchild. No untrusted code, network access or resource
bomb is used. Tests run only on a genuine Windows runner, never on a fake
sys.platform monkeypatch that cannot exercise kernel32.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
import subprocess

import pytest

from graphlink_execution_guard import (
    ExecutionGuardUnavailable, assign_guarded_subprocess, create_execution_guard,
    spawn_guarded_subprocess,
)
from graphlink_process_env import safe_subprocess_env

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="real Windows Job Objects required")


def _launch(guard, script: str, **kwargs):
    return spawn_guarded_subprocess(
        guard, [sys.executable, "-c", script],
        env=safe_subprocess_env(), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, creationflags=subprocess.CREATE_NO_WINDOW, **kwargs,
    )


def test_native_launcher_sets_exact_job_membership_before_post_spawn_assign(tmp_path):
    """A zero-delay child must be in *this* Job Object, not merely some CI job."""
    from ctypes import wintypes

    guard = create_execution_guard()
    marker = tmp_path / "started.txt"
    script = "from pathlib import Path; Path(" + repr(str(marker)) + ").write_text('hello')"
    try:
        process = _launch(guard, script)
        try:
            assert process._graphlink_atomic_job is True
            # The existing post-spawn API is a no-op after atomic creation.
            # The child may have already run by this point; its resource
            # boundary must be present since its first instruction.
            assign_guarded_subprocess(guard, process)
            handle = guard.duplicate_native_handle()
            try:
                is_member = wintypes.BOOL()
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.IsProcessInJob.restype = wintypes.BOOL
                kernel.IsProcessInJob.argtypes = [
                    wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
                ]
                assert kernel.IsProcessInJob(process._handle, handle, ctypes.byref(is_member))
                assert is_member.value, "created worker was not in the requested job"
            finally:
                ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
            stdout, stderr = process.communicate(timeout=8)
            assert process.returncode == 0, (stdout, stderr)
            assert marker.read_text(encoding="utf-8") == "hello"
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    finally:
        guard.close()


def test_native_guard_failure_refuses_to_start_child_and_leaks_no_running_process(tmp_path, monkeypatch):
    guard = create_execution_guard()
    marker = tmp_path / "should-not-exist.txt"
    script = "from pathlib import Path; Path(" + repr(str(marker)) + ").write_text('unsafe')"

    def fail_duplicate():
        raise ExecutionGuardUnavailable("injected Job Object handle failure")

    monkeypatch.setattr(guard, "duplicate_native_handle", fail_duplicate)
    try:
        with pytest.raises(ExecutionGuardUnavailable, match="injected"):
            _launch(guard, script)
        assert not marker.exists()
    finally:
        guard.close()


def test_native_guarded_popen_preserves_bounded_large_pipe_output():
    guard = create_execution_guard()
    script = "import sys; sys.stdout.write('X' * 131072); sys.stdout.flush(); print('END')"
    try:
        proc = _launch(guard, script)
        stdout, stderr = proc.communicate(timeout=10)
        assert proc.returncode == 0, stderr
        assert stdout.startswith("X" * 131072)
        assert stdout.endswith("END\n")
    finally:
        guard.close()


def test_native_guard_close_kills_running_grandchild_tree(tmp_path):
    """Tree-lifetime enforcement is retained when assignment happens at create."""
    marker = tmp_path / "grandchild-heartbeat"
    grandchild = (
        "from pathlib import Path; import time; p=Path(" + repr(str(marker)) + "); "
        "[(p.open('a').write('x'), time.sleep(.08)) for _ in range(200)]"
    )
    parent = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c'," + repr(grandchild) +
        "],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        "print('running',flush=True);time.sleep(30)"
    )
    guard = create_execution_guard()
    proc = None
    try:
        proc = _launch(guard, parent)
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "running"
        start = time.monotonic()
        while (not marker.exists() or marker.stat().st_size == 0) and time.monotonic() - start < 6:
            time.sleep(.05)
        assert marker.exists() and marker.stat().st_size > 0
    finally:
        guard.close()
        if proc is not None:
            proc.wait(timeout=6)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
    size_after_stop = marker.stat().st_size
    time.sleep(.4)
    assert marker.stat().st_size == size_after_stop, "grandchild survived Job Object closure"


def test_native_guarded_shell_command_keeps_existing_quoting_semantics(tmp_path):
    """shell.exec must still pass a raw command string through Windows cmd.exe."""
    from backend.harness.tools_shell import _run_command

    command = f'"{sys.executable}" -c "print(13579)"'
    output, code, ended = _run_command(command, tmp_path, threading.Event())
    assert ended == "ok"
    assert code == 0
    assert "13579" in output
