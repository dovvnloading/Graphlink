"""ADR-005 stage 5.2: the Windows Job Object resource guard
(graphlink_execution_guard.py) and its wiring into Py-Coder's PythonREPL
and the Code Sandbox's VirtualEnvSandbox.

The stage's own exit criterion is "Memory bomb killed at cap; orphan test:
stop kills the whole tree" - TestJobObjectMechanism below proves both,
against real subprocesses, not mocks (a mock cannot prove an OS-level
resource cap actually enforces anything). TestPythonReplUsesTheGuard and
TestVirtualEnvSandboxUsesTheGuard then prove each wired call site actually
creates/assigns/closes a guard at the right moments - fast, mock-based for
the call-site plumbing, plus one real end-to-end test per surface proving
the full wiring genuinely kills a grandchild process on stop (the same
property TestJobObjectMechanism proves for the guard in isolation).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import graphlink_execution_guard as guard_module
from graphlink_execution_guard import ExecutionResourceGuard, create_execution_guard
from graphlink_plugins.code_sandbox.domain import VirtualEnvSandbox
from graphlink_plugins.pycoder.domain import PythonREPL

WINDOWS_ONLY = pytest.mark.skipif(
    sys.platform != "win32", reason="Job Object mechanism is Windows-only in this stage (POSIX is ADR-005 5.3)"
)


class _FakeGuard:
    """Records assign()/close() calls without touching any real OS
    resource - used for fast call-site tests (does start()/stop() call the
    guard correctly?), distinct from the slow, real-mechanism tests below
    (does the guard itself actually enforce anything?)."""

    def __init__(self, popen_kwargs=None):
        self.assigned_pids = []
        self.closed = False
        self.popen_kwargs_calls = 0
        self._popen_kwargs = popen_kwargs or {}

    def popen_kwargs(self):
        self.popen_kwargs_calls += 1
        return dict(self._popen_kwargs)

    def assign(self, pid):
        self.assigned_pids.append(pid)

    def close(self):
        self.closed = True


# -- The guard mechanism itself, against real subprocesses ------------------


@WINDOWS_ONLY
class TestJobObjectMechanism:
    def test_a_memory_bomb_is_killed_at_the_cap(self):
        guard = create_execution_guard(memory_limit_bytes=100 * 1024 * 1024, active_process_limit=64)
        proc = subprocess.Popen(
            [sys.executable, "-c", "x = bytearray(500 * 1024 * 1024); import time; time.sleep(5)"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        guard.assign(proc.pid)
        try:
            returncode = proc.wait(timeout=10)
            assert returncode != 0, "a 500 MiB allocation against a 100 MiB job cap should have been killed"
        finally:
            guard.close()

    def test_a_fork_bomb_is_capped_by_the_active_process_limit(self, tmp_path):
        counter_path = tmp_path / "forkcount.txt"
        bomb_script = tmp_path / "forkbomb.py"
        bomb_script.write_text(
            "import subprocess, sys, time\n"
            f"path = r'{counter_path}'\n"
            "n = int(sys.argv[1]) if len(sys.argv) > 1 else 0\n"
            "with open(path, 'a') as fh:\n"
            "    fh.write('x')\n"
            "if n < 200:\n"
            "    subprocess.Popen([sys.executable, __file__, str(n + 1)])\n"
            "time.sleep(3)\n",
            encoding="utf-8",
        )
        guard = create_execution_guard(active_process_limit=5)
        proc = subprocess.Popen(
            [sys.executable, str(bomb_script)], creationflags=subprocess.CREATE_NO_WINDOW
        )
        try:
            guard.assign(proc.pid)
            time.sleep(4)
            count = len(counter_path.read_text(encoding="utf-8")) if counter_path.exists() else 0
            assert count < 200, f"active-process cap of 5 did not stop the fork bomb - {count} processes ran"
        finally:
            guard.close()

    def test_closing_the_guard_kills_a_grandchild_process_too(self, tmp_path):
        marker = tmp_path / "marker.txt"
        grandchild_script = tmp_path / "grandchild.py"
        grandchild_script.write_text(
            "import time\n"
            f"with open(r'{marker}', 'w') as m:\n"
            "    while True:\n"
            "        m.write('x'); m.flush(); time.sleep(0.2)\n",
            encoding="utf-8",
        )
        parent_script = tmp_path / "parent.py"
        parent_script.write_text(
            "import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, r'{grandchild_script}'])\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )

        guard = create_execution_guard()
        proc = subprocess.Popen([sys.executable, str(parent_script)], creationflags=subprocess.CREATE_NO_WINDOW)
        guard.assign(proc.pid)

        time.sleep(1.5)
        assert marker.exists(), "grandchild never started writing"
        size_before = marker.stat().st_size
        time.sleep(1)
        assert marker.stat().st_size > size_before, "grandchild isn't actively writing before close()"

        guard.close()  # the actual mechanism PythonREPL.stop()/VirtualEnvSandbox.stop() use
        time.sleep(1.5)
        size_after_close = marker.stat().st_size
        time.sleep(1.5)
        assert marker.stat().st_size == size_after_close, "grandchild kept writing after guard.close()"

    def test_close_is_idempotent(self):
        guard = create_execution_guard()
        guard.close()
        guard.close()  # must not raise

    def test_assign_on_an_already_dead_pid_does_not_raise(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        guard = create_execution_guard()
        guard.assign(proc.pid)  # pid may already be reused/invalid - must not raise
        guard.close()

    def test_the_windows_guard_contributes_no_popen_kwargs(self):
        # The Windows tier applies its cap to an ALREADY-running process via
        # assign(), so it must not perturb the Popen call at all - if this
        # ever returned something, both execution surfaces would silently
        # start passing an unexpected kwarg into their subprocess spawn.
        # (The platform-dispatch counterpart of this - that a POSIX host
        # gets the rlimit/process-group kwargs instead - is asserted in
        # test_execution_guard_posix.py, which only runs there.)
        guard = create_execution_guard()
        try:
            assert guard.popen_kwargs() == {}
        finally:
            guard.close()


class TestNullGuardNeverRaises:
    def test_assign_and_close_are_no_ops_on_any_input(self):
        guard = ExecutionResourceGuard()
        guard.assign(-1)
        guard.assign(0)
        guard.close()
        guard.close()


# -- PythonREPL wiring --------------------------------------------------------


class TestPythonReplUsesTheGuard:
    def test_starting_the_repl_assigns_it_to_a_guard(self):
        fake = _FakeGuard()
        with patch("graphlink_plugins.pycoder.domain.create_execution_guard", return_value=fake):
            repl = PythonREPL(node_id="guard-assign-test")
            try:
                repl.start()
                assert repl.guard is fake
                assert fake.assigned_pids == [repl.process.pid]
            finally:
                repl.stop()

    def test_stopping_the_repl_closes_the_guard(self):
        fake = _FakeGuard()
        with patch("graphlink_plugins.pycoder.domain.create_execution_guard", return_value=fake):
            repl = PythonREPL(node_id="guard-close-test")
            repl.start()
            repl.stop()
            assert fake.closed is True
            assert repl.guard is None

    @WINDOWS_ONLY
    def test_a_real_stop_kills_a_grandchild_the_repl_itself_spawned(self, tmp_path):
        marker = tmp_path / "marker.txt"
        grandchild_script = tmp_path / "grandchild.py"
        grandchild_script.write_text(
            "import time\n"
            f"with open(r'{marker}', 'w') as m:\n"
            "    while True:\n"
            "        m.write('x'); m.flush(); time.sleep(0.2)\n",
            encoding="utf-8",
        )

        repl = PythonREPL(node_id="guard-real-tree-test")
        # stdin/stdout/stderr=DEVNULL: a background child spawned from
        # inside the REPL otherwise inherits the REPL's own stdin PIPE
        # handle, which - a pre-existing PythonREPL characteristic,
        # confirmed unrelated to this stage's guard (reproduces identically
        # with the guard replaced by a no-op) - can leave a detached,
        # non-waited grandchild hung at interpreter startup. Real exec'd
        # code that wants a genuinely detached background process needs
        # the same redirection for the same reason.
        repl.execute(
            "import subprocess, sys; subprocess.Popen([sys.executable, r'"
            f"{grandchild_script}'], stdin=subprocess.DEVNULL, "
            "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)"
        )

        time.sleep(1.5)
        assert marker.exists(), "grandchild never started writing"
        size_before = marker.stat().st_size
        time.sleep(1)
        assert marker.stat().st_size > size_before

        repl.stop()
        time.sleep(1.5)
        size_after_stop = marker.stat().st_size
        time.sleep(1.5)
        assert marker.stat().st_size == size_after_stop, "grandchild kept writing after repl.stop()"


# -- VirtualEnvSandbox wiring -------------------------------------------------


class TestVirtualEnvSandboxUsesTheGuard:
    def test_a_normally_completed_subprocess_assigns_and_closes_the_guard(self):
        fake = _FakeGuard()
        with patch("graphlink_plugins.code_sandbox.domain.create_execution_guard", return_value=fake):
            sandbox = VirtualEnvSandbox("guard-normal-test")
            output, code = sandbox._run_subprocess(
                [sys.executable, "-c", "print('ok')"],
                should_continue=lambda: True,
                timeout_seconds=10,
            )
            assert code == 0
            assert len(fake.assigned_pids) == 1
            assert fake.closed is True
            assert sandbox.guard is None

    def test_a_should_continue_stop_closes_the_guard(self):
        fake = _FakeGuard()
        with patch("graphlink_plugins.code_sandbox.domain.create_execution_guard", return_value=fake):
            sandbox = VirtualEnvSandbox("guard-stop-test")
            flag = {"go": True}

            def runner():
                with pytest.raises(InterruptedError):
                    sandbox._run_subprocess(
                        [sys.executable, "-c", "import time; time.sleep(5)"],
                        should_continue=lambda: flag["go"],
                        timeout_seconds=30,
                    )

            t = threading.Thread(target=runner)
            t.start()
            time.sleep(0.5)
            flag["go"] = False
            t.join(timeout=10)

            assert fake.closed is True
            assert sandbox.guard is None

    def test_calling_stop_in_isolation_closes_the_guard(self):
        # Deliberately NOT run through a real, concurrently-running
        # _run_subprocess() call (unlike the two tests above): that call's
        # own `finally` block closes the guard too, as a safety net, which
        # would mask stop()'s OWN close() call behind a race - by the time
        # a background thread's finally block is observed to have run,
        # stop() may have simply been blocked (in its own
        # current_process.wait(timeout=3)) long enough for the OTHER
        # thread's independent cleanup to win the race first. Confirmed by
        # mutation: a threaded version of this test asserting only after
        # stop() returned still passed with stop()'s own guard.close() call
        # removed entirely. Directly setting sandbox.guard/current_process
        # and calling stop() with nothing else running isolates stop()'s
        # own behavior from that safety net.
        fake = _FakeGuard()
        sandbox = VirtualEnvSandbox("guard-explicit-stop-test")
        sandbox.guard = fake
        sandbox.current_process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"]
        )
        try:
            sandbox.stop()
            assert fake.closed is True
            assert sandbox.guard is None
        finally:
            if sandbox.current_process and sandbox.current_process.poll() is None:
                sandbox.current_process.kill()

    @WINDOWS_ONLY
    def test_a_real_stop_kills_a_grandchild_the_sandbox_itself_spawned(self, tmp_path):
        marker = tmp_path / "marker.txt"
        grandchild_script = tmp_path / "grandchild.py"
        grandchild_script.write_text(
            "import time\n"
            f"with open(r'{marker}', 'w') as m:\n"
            "    while True:\n"
            "        m.write('x'); m.flush(); time.sleep(0.2)\n",
            encoding="utf-8",
        )
        parent_script = tmp_path / "parent.py"
        parent_script.write_text(
            "import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, r'{grandchild_script}'])\n"
            "time.sleep(20)\n",
            encoding="utf-8",
        )

        sandbox = VirtualEnvSandbox("guard-real-tree-test")
        flag = {"go": True}

        def runner():
            with pytest.raises(InterruptedError):
                sandbox._run_subprocess(
                    [sys.executable, str(parent_script)],
                    should_continue=lambda: flag["go"],
                    timeout_seconds=30,
                )

        t = threading.Thread(target=runner)
        t.start()
        time.sleep(1.5)
        assert marker.exists(), "grandchild never started writing"
        size_before = marker.stat().st_size
        time.sleep(1)
        assert marker.stat().st_size > size_before

        flag["go"] = False
        t.join(timeout=10)

        time.sleep(1.5)
        size_after_stop = marker.stat().st_size
        time.sleep(1.5)
        assert marker.stat().st_size == size_after_stop, "grandchild kept writing after sandbox stop"


# -- Adversarial-review-driven regression tests ------------------------------
# The four classes below pin the fixes for findings the ADR-005 stage 5.2
# review confirmed against the ORIGINAL diff (each independently reproduced
# with real handle counts / real crashes before being fixed): PythonREPL's
# restart path leaking the previous guard's Job Object handle, concurrent
# stop() calls crashing/double-closing, and the two fail-open fallback
# paths in graphlink_execution_guard.py being completely unexercised.


class TestPythonReplRestartDoesNotLeakTheOldGuard:
    def test_restarting_a_dead_repl_closes_the_old_guard_first(self):
        guards_created = []

        def factory():
            fake = _FakeGuard()
            guards_created.append(fake)
            return fake

        with patch("graphlink_plugins.pycoder.domain.create_execution_guard", side_effect=factory):
            repl = PythonREPL(node_id="restart-leak-test")
            repl.execute("1 + 1")
            assert len(guards_created) == 1
            first_guard = guards_created[0]
            assert first_guard.closed is False

            # The process dies WITHOUT going through stop() - e.g. an
            # external kill, or (plausible given this same stage's own new
            # memory cap) the OS terminating it for exceeding
            # JobMemoryLimit sometime after the previous execute() call
            # already returned.
            repl.process.kill()
            repl.process.wait()

            repl.execute("2 + 2")  # execute()'s poll()-based restart path
            assert len(guards_created) == 2
            assert first_guard.closed is True, (
                "start() must close any pre-existing guard before "
                "overwriting self.guard, or the old Job Object handle leaks"
            )
            repl.stop()
            assert guards_created[1].closed is True


class TestPythonReplConcurrentStopIsSafe:
    # Both tests below deterministically FORCE the race rather than merely
    # hoping enough concurrent threads happen to interleave at the right
    # instant - the actual hazard is a check-then-write on a couple of
    # attributes, a window far too narrow for unforced thread scheduling to
    # reliably hit. Confirmed empirically: a naive version of this test
    # spawning 5-10 plain concurrent threads with no forced interleaving
    # passed 0/5 times even with the lock removed entirely from
    # PythonREPL.stop()/graphlink_execution_guard's close(). Forcing the
    # interleaving by having the TEST itself hold the lock a second thread
    # must wait for is what makes this a real regression pin rather than a
    # test that merely looks like one.

    def test_stop_is_serialized_by_the_repls_own_lock(self):
        repl = PythonREPL(node_id="concurrent-stop-lock-test")
        repl.execute("1 + 1")

        repl._lock.acquire()
        try:
            entered = threading.Event()

            def stopper():
                entered.set()
                repl.stop()

            t = threading.Thread(target=stopper)
            t.start()
            assert entered.wait(timeout=5)
            time.sleep(0.3)  # let the thread actually reach the lock
            assert t.is_alive(), (
                "a second stop() call must block on self._lock while this "
                "thread holds it, not race past the process/guard cleanup - "
                "this is what prevents the real production crash "
                "('NoneType' object has no attribute 'kill') from one "
                "thread nulling self.process out from under another"
            )
        finally:
            repl._lock.release()
        t.join(timeout=10)

        assert repl.process is None
        assert repl.guard is None

    @WINDOWS_ONLY
    def test_guard_close_is_serialized_by_its_own_lock(self):
        # Isolates the guard module's own thread-safety, independent of
        # PythonREPL entirely - protects every caller of
        # graphlink_execution_guard, not just this one.
        guard = create_execution_guard()

        guard._lock.acquire()
        try:
            entered = threading.Event()

            def closer():
                entered.set()
                guard.close()

            t = threading.Thread(target=closer)
            t.start()
            assert entered.wait(timeout=5)
            time.sleep(0.3)
            assert t.is_alive(), "a second close() call must block on self._lock, not race the check-then-null of self._handle"
            assert guard._handle is not None, "the handle must not be nulled while another close() holds the lock"
        finally:
            guard._lock.release()
        t.join(timeout=10)

        assert guard._handle is None


@WINDOWS_ONLY
class TestFailOpenFallbackPaths:
    def test_create_job_object_failure_falls_back_to_the_null_guard(self, monkeypatch):
        monkeypatch.setattr(guard_module._kernel32, "CreateJobObjectW", lambda *a: None)
        guard = create_execution_guard()
        assert type(guard) is ExecutionResourceGuard
        guard.assign(os.getpid())  # no-op, must not raise
        guard.close()

    def test_set_information_job_object_failure_falls_back_and_closes_the_handle(self, monkeypatch):
        closed_handles = []
        real_create = guard_module._kernel32.CreateJobObjectW
        real_close = guard_module._kernel32.CloseHandle

        def recording_close(handle):
            closed_handles.append(handle)
            return real_close(handle)

        monkeypatch.setattr(guard_module._kernel32, "SetInformationJobObject", lambda *a: 0)
        monkeypatch.setattr(guard_module._kernel32, "CloseHandle", recording_close)

        guard = create_execution_guard()

        assert type(guard) is ExecutionResourceGuard
        assert len(closed_handles) == 1, "the job handle CreateJobObjectW allocated must be closed on this fallback path, not leaked"


@WINDOWS_ONLY
class TestRealDefaultsReachTheWin32Api:
    def test_create_execution_guard_with_no_arguments_applies_the_real_default_caps(self, monkeypatch):
        # The three real, non-mocked TestJobObjectMechanism tests above all
        # pass EXPLICIT non-default cap values - this is the one test that
        # confirms the module's actual shipped defaults (what
        # PythonREPL.start()/VirtualEnvSandbox._run_subprocess() call with
        # zero arguments in production) really reach SetInformationJobObject
        # with the right flags/values, by inspecting the real struct passed
        # to the real Win32 call rather than mocking it away.
        captured = {}
        real_set_info = guard_module._kernel32.SetInformationJobObject

        def recording_set_info(handle, info_class, info_ptr, info_size):
            info = guard_module.ctypes.cast(
                info_ptr, guard_module.ctypes.POINTER(guard_module._JOBOBJECT_EXTENDED_LIMIT_INFORMATION)
            ).contents
            captured["LimitFlags"] = info.BasicLimitInformation.LimitFlags
            captured["JobMemoryLimit"] = info.JobMemoryLimit
            captured["ActiveProcessLimit"] = info.BasicLimitInformation.ActiveProcessLimit
            return real_set_info(handle, info_class, info_ptr, info_size)

        monkeypatch.setattr(guard_module._kernel32, "SetInformationJobObject", recording_set_info)

        guard = create_execution_guard()
        try:
            assert captured["JobMemoryLimit"] == guard_module.DEFAULT_MEMORY_LIMIT_BYTES
            assert captured["ActiveProcessLimit"] == guard_module.DEFAULT_ACTIVE_PROCESS_LIMIT
            assert captured["LimitFlags"] & guard_module._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            assert captured["LimitFlags"] & guard_module._JOB_OBJECT_LIMIT_JOB_MEMORY
            assert captured["LimitFlags"] & guard_module._JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        finally:
            guard.close()
