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
    script = (
        "import sys; sys.stdout.write('X' * 131072); sys.stdout.flush(); "
        "sys.stderr.write('E' * 131072); sys.stderr.flush(); print('END')"
    )
    try:
        proc = _launch(guard, script)
        stdout, stderr = proc.communicate(timeout=10)
        assert proc.returncode == 0, stderr
        assert stdout.startswith("X" * 131072)
        assert stdout.endswith("END\n")
        assert stderr == "E" * 131072
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


def test_native_job_attribute_failure_never_launches_unguarded_child(tmp_path, monkeypatch):
    """A rejected JOB_LIST attribute must not fall back to ordinary Popen."""
    from graphlink_windows_job_popen import _kernel32, _PROC_THREAD_ATTRIBUTE_JOB_LIST

    guard = create_execution_guard()
    marker = tmp_path / "should-not-start.txt"
    script = "from pathlib import Path; Path(" + repr(str(marker)) + ").write_text('unsafe')"
    actual_update = _kernel32.UpdateProcThreadAttribute
    recorded: list[int] = []

    def reject_job_list(attribute_list, flags, attribute, value, size, previous, return_size):
        if attribute == _PROC_THREAD_ATTRIBUTE_JOB_LIST:
            recorded.append(attribute)
            ctypes.set_last_error(87)  # ERROR_INVALID_PARAMETER
            return False
        return actual_update(attribute_list, flags, attribute, value, size, previous, return_size)

    monkeypatch.setattr(_kernel32, "UpdateProcThreadAttribute", reject_job_list)
    try:
        with pytest.raises(ExecutionGuardUnavailable, match="creation-time attachment unavailable"):
            _launch(guard, script)
        assert recorded == [_PROC_THREAD_ATTRIBUTE_JOB_LIST]
        assert not marker.exists(), "native launch must not execute without a job attribute"
    finally:
        guard.close()


def test_native_guard_close_racing_create_does_not_leave_running_child(tmp_path, monkeypatch):
    """A concurrent Stop between job-handle duplication and CreateProcess is contained."""
    import graphlink_windows_job_popen as native

    original_create = native._create_process_in_job
    marker = tmp_path / "pre-creation-marker.txt"
    script = "from pathlib import Path; Path(" + repr(str(marker)) + ").write_text('executed')"
    reached_create = threading.Event()
    resume_create = threading.Event()
    result: dict[str, object] = {}

    def delayed_create(*args, **kwargs):
        reached_create.set()
        if not resume_create.wait(timeout=6):
            raise RuntimeError("test did not release the native creation checkpoint")
        return original_create(*args, **kwargs)

    monkeypatch.setattr(native, "_create_process_in_job", delayed_create)
    guard = create_execution_guard()

    def launch():
        try:
            result["process"] = _launch(guard, script + "; import time;time.sleep(30)")
        except (ExecutionGuardUnavailable, OSError) as exc:
            result["denied"] = exc

    thread = threading.Thread(target=launch, daemon=True)
    thread.start()
    try:
        assert reached_create.wait(timeout=6), "native launcher never reached job creation"
        assert not marker.exists(), "child executed before native creation-time job attachment"
        # The native launcher has already duplicated the configured job
        # handle; closing its original cannot let a child escape the job.
        guard.close()
    finally:
        resume_create.set()

    thread.join(timeout=10)
    assert not thread.is_alive(), "concurrent Stop hung the launching thread"
    if "process" in result:
        process = result["process"]
        assert isinstance(process, subprocess.Popen)
        try:
            process.wait(timeout=6)
            assert process.poll() is not None
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
    else:
        assert isinstance(result.get("denied"), (ExecutionGuardUnavailable, OSError))


def test_native_guarded_repeated_stop_reaps_every_process():
    """Repeated Start/Stop through another thread cannot leak live children."""
    for _ in range(6):
        guard = create_execution_guard()
        proc = None
        try:
            proc = _launch(guard, "import time; time.sleep(30)")
            stop = threading.Thread(target=guard.close, daemon=True)
            stop.start()
            stop.join(timeout=6)
            assert not stop.is_alive()
            proc.wait(timeout=6)
            assert proc.poll() is not None
        finally:
            guard.close()
            if proc is not None:
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    if stream is not None:
                        stream.close()
