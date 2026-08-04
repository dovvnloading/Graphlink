"""ADR-005 stage 5.2: Windows Job Object resource guard for executed code
(Py-Coder's persistent REPL and the Code Sandbox's venv/pip/script children).

THE THREAT (audit finding H2). Neither execution surface enforced any
memory, process-count, or lifecycle limit beyond wall-clock timeouts and a
human clicking Stop - a script that allocates until OOM, or that forks
itself repeatedly, was bounded only by the host's own limits. "Stop" only
ever killed the one directly-tracked child process, never anything that
child itself had spawned - a process that had already forked survived
being "stopped."

THE FIX. Every subprocess this app spawns for executed code is assigned to
a Windows Job Object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE plus a
committed-memory cap and an active-process-count cap. Because Windows job
objects are inherited by any further child process a job member spawns
(the default, unless a process explicitly opts out via
JOB_OBJECT_LIMIT_BREAKAWAY_OK, which this module never sets), killing the
job kills the entire process tree in one call - closing the
"Stop only kills the direct child" gap, not just the memory-bomb one. All
three mechanisms here (memory cap, active-process cap, whole-tree kill)
were empirically proven against real memory-bomb, fork-bomb, and
orphan-grandchild scripts before this module was written, not assumed
from the Win32 API documentation alone.

This is a **resource + lifecycle** boundary, not a security VM - see
doc/adr/THREAT_MODEL.md's "Executed code is a resource boundary, not a
security boundary" section. It does not stop a deliberately malicious
script from doing anything within its resource caps; it stops a runaway
or hostile dependency from taking down the host, and it makes "Stop"
actually mean stop.

SCOPE OF THIS STAGE (5.2), deliberately narrower than ADR-005 Decision #2's
full description: CPU-rate control and settings-driven (as opposed to
hardcoded) caps are NOT implemented here. CPU-rate control was never
empirically verified against a real CPU-bound busy-loop before this stage
shipped - the same discipline this ADR-004/ADR-005 effort holds itself to
everywhere else - so it is left for a follow-up rather than shipped
unverified. Settings-driven caps + approval-dialog disclosure of the real
limits is a UI-facing change orthogonal to this module's own correctness,
also left for a follow-up. Neither is silently dropped - both are named
here so a future stage does not have to rediscover the gap.

THE POSIX TIER (ADR-005 stage 5.3) is implemented below too - a dedicated
process group so close() can kill the whole tree via killpg(), plus
resource.setrlimit caps for address space, CPU seconds, process count and
file size. Read the long comment on that block before trusting it: unlike
the Windows tier, it has NOT been executed on a POSIX host (this project's
dev machine and its only CI runner are both Windows), so it is
unverified-in-practice by design and says so rather than implying parity.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import sys

logger = logging.getLogger(__name__)

# ADR-005 Decision #2's own example figure ("up to 2 GB RAM"). A generous
# default: real Py-Coder/Sandbox workloads (pandas, matplotlib, a venv's own
# interpreter + pip's dependency resolver) comfortably fit under it; a true
# memory bomb still dies well before threatening the host.
DEFAULT_MEMORY_LIMIT_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB

# Generous enough for legitimate multi-process work (pip install spawning
# its own build-backend/resolver subprocesses, a venv's bootstrap), but
# bounds a fork bomb to a small, fixed number of processes rather than an
# unbounded spiral - verified empirically against a real fork-bomb script
# (200-generation bomb capped at exactly the limit, not 200+).
DEFAULT_ACTIVE_PROCESS_LIMIT = 64


class ExecutionResourceGuard:
    """No-op base: what non-Windows platforms get in this stage (the POSIX
    tier is ADR-005 stage 5.3), and what Windows itself falls back to if
    job-object creation fails for any reason. A guard that cannot enforce
    limits must never block execution outright - the pre-existing,
    unenforced behavior is the safe fallback, not a hard failure, matching
    this codebase's established graceful-degradation precedent (DPAPI
    falling back to plaintext off-Windows rather than refusing to save)."""

    def popen_kwargs(self) -> dict:
        """Extra kwargs to merge into the `subprocess.Popen(...)` call this
        guard is about to govern. Empty for Windows (a job object is applied
        to an ALREADY-running process via assign()) and for the no-op base,
        but load-bearing on POSIX, where `resource.setrlimit` has to run
        inside the child between fork and exec - there is no way to impose
        an address-space limit on a process that is already running. Callers
        must therefore create the guard BEFORE Popen, pass these kwargs into
        it, and call assign() with the resulting pid afterwards."""
        return {}

    def assign(self, pid: int) -> None:
        pass

    def close(self) -> None:
        pass


if sys.platform == "win32":
    import ctypes.wintypes as _wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # Adversarial-review fix: explicit .restype/.argtypes on every function
    # this module calls. Without them, ctypes defaults an undeclared
    # restype to a 4-byte c_long - harmless for the four BOOL-returning
    # calls (BOOL is itself 4 bytes) but silently wrong for
    # CreateJobObjectW/OpenProcess, both of which return HANDLE (a pointer,
    # 8 bytes on 64-bit Windows). In practice real per-process kernel
    # handle-table values stay small enough that the truncation was a
    # no-op, but it is real latent fragility with no Python exception to
    # ever surface it - declaring the real types removes it outright.
    _kernel32.CreateJobObjectW.restype = _wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, _wintypes.LPCWSTR]
    _kernel32.OpenProcess.restype = _wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [_wintypes.DWORD, _wintypes.BOOL, _wintypes.DWORD]
    _kernel32.AssignProcessToJobObject.restype = _wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [_wintypes.HANDLE, _wintypes.HANDLE]
    _kernel32.SetInformationJobObject.restype = _wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = [
        _wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, _wintypes.DWORD,
    ]
    _kernel32.TerminateJobObject.restype = _wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [_wintypes.HANDLE, _wintypes.UINT]
    _kernel32.CloseHandle.restype = _wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [_wintypes.HANDLE]

    _JobObjectExtendedLimitInformation = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    _JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
    # Adversarial-review fix: was 0x1F0FFF, winnt.h's stale pre-Vista
    # PROCESS_ALL_ACCESS value (and broader than this module needs even by
    # its real modern definition, 0x1FFFFF). AssignProcessToJobObject's own
    # documented requirement is only PROCESS_SET_QUOTA (0x100) |
    # PROCESS_TERMINATE (0x001) - requesting exactly that, not "all
    # access," is both least-privilege and more likely to succeed under a
    # host's process-access restriction policy (EDR/AppLocker-style tools
    # that deny full-access OpenProcess calls but permit narrowly-scoped
    # ones).
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_ACCESS_FOR_JOB_ASSIGNMENT = _PROCESS_SET_QUOTA | _PROCESS_TERMINATE

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", _wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", _wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", _wintypes.DWORD),
            ("SchedulingClass", _wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _WindowsJobObjectGuard(ExecutionResourceGuard):
        def __init__(self, handle) -> None:
            self._handle = handle
            # Adversarial-review fix: without a lock, two threads calling
            # close() concurrently on the SAME guard can both pass the
            # `self._handle is None` check before either one nulls it,
            # both read the same real handle value, and both call
            # CloseHandle() on it - a handle-recycling hazard, since once
            # the first CloseHandle succeeds that numeric handle can be
            # reassigned to any other resource in the process before the
            # second CloseHandle runs. Reproduced live: two threads racing
            # a real close() call produced CloseHandle() invoked twice on
            # the identical handle value. Reachable in production via
            # PythonREPL's own concurrent-stop() scenario (see that
            # class's docstring) - fixing it here protects every caller of
            # this module, not just the one that happened to add its own
            # locking.
            self._lock = threading.Lock()

        def assign(self, pid: int) -> None:
            if self._handle is None:
                return
            proc_handle = _kernel32.OpenProcess(_PROCESS_ACCESS_FOR_JOB_ASSIGNMENT, False, pid)
            if not proc_handle:
                logger.warning(
                    "execution guard: OpenProcess failed for pid %s (error %s) - "
                    "this child will run WITHOUT resource caps",
                    pid, ctypes.get_last_error(),
                )
                return
            try:
                if not _kernel32.AssignProcessToJobObject(self._handle, proc_handle):
                    logger.warning(
                        "execution guard: AssignProcessToJobObject failed for pid "
                        "%s (error %s) - this child will run WITHOUT resource caps",
                        pid, ctypes.get_last_error(),
                    )
            finally:
                _kernel32.CloseHandle(proc_handle)

        def close(self) -> None:
            with self._lock:
                if self._handle is None:
                    return
                handle, self._handle = self._handle, None
            # Unconditionally kills anything still alive in the job - the
            # orphan/whole-tree-kill path. A harmless no-op if the job is
            # already empty (the normal "process exited cleanly" path).
            # The actual Win32 calls happen OUTSIDE the lock (they release
            # the GIL and can block briefly) - only the check-and-null of
            # self._handle needs to be atomic, so a second concurrent
            # close() call can return immediately once it sees None rather
            # than waiting on these calls to finish.
            if not _kernel32.TerminateJobObject(handle, 1):
                logger.debug(
                    "execution guard: TerminateJobObject reported failure "
                    "(error %s) - likely just an already-empty job",
                    ctypes.get_last_error(),
                )
            _kernel32.CloseHandle(handle)

    def _create_windows_job_object_guard(
        memory_limit_bytes: int, active_process_limit: int
    ) -> ExecutionResourceGuard:
        handle = _kernel32.CreateJobObjectW(None, None)
        if not handle:
            logger.warning(
                "execution guard: CreateJobObjectW failed (error %s) - "
                "falling back to no resource caps for this run",
                ctypes.get_last_error(),
            )
            return ExecutionResourceGuard()

        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        flags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if memory_limit_bytes:
            flags |= _JOB_OBJECT_LIMIT_JOB_MEMORY
            info.JobMemoryLimit = memory_limit_bytes
        if active_process_limit:
            flags |= _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            info.BasicLimitInformation.ActiveProcessLimit = active_process_limit
        info.BasicLimitInformation.LimitFlags = flags

        if not _kernel32.SetInformationJobObject(
            handle, _JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
        ):
            logger.warning(
                "execution guard: SetInformationJobObject failed (error %s) - "
                "falling back to no resource caps for this run",
                ctypes.get_last_error(),
            )
            _kernel32.CloseHandle(handle)
            return ExecutionResourceGuard()

        return _WindowsJobObjectGuard(handle)

    # The platform decision is made ONCE, here at import time, and bound to
    # a single name the public factory calls. Deliberately not re-checked
    # via `sys.platform` inside create_execution_guard(): the classes below
    # only EXIST on their own platform, so a runtime re-check could route to
    # a name that was never defined (a latent NameError, and one that a test
    # monkeypatching sys.platform would trip over immediately).
    _create_platform_guard = _create_windows_job_object_guard


else:
    # ADR-005 stage 5.3: the POSIX tier. Two independent mechanisms, both
    # applied at spawn time rather than after the fact:
    #
    #   1. A dedicated PROCESS GROUP (`process_group=0` -> setpgid(0, 0) in
    #      the child). This is what makes close() able to kill the whole
    #      tree via killpg() rather than just the one tracked pid - the
    #      POSIX analogue of the Windows job object's kill-on-close, and the
    #      same "Stop actually stops everything it spawned" property.
    #      Deliberately uses subprocess's own `process_group=` kwarg (Python
    #      3.11+, implemented in C inside the fork-exec helper) rather than
    #      doing setsid() from preexec_fn: preexec_fn runs arbitrary Python
    #      between fork and exec, which CPython's own docs call out as
    #      unsafe in the presence of threads (it can deadlock on a lock held
    #      by another thread at fork time) - and BOTH call sites here are
    #      threaded (the sandbox's reader thread, agents.py's
    #      asyncio.to_thread). Keeping the group setup in C-level code
    #      removes that hazard for the mechanism that matters most.
    #
    #   2. resource.setrlimit caps (address space, CPU seconds, process
    #      count, file size). These have no subprocess kwarg equivalent, so
    #      they DO require preexec_fn. The callback is kept deliberately
    #      tiny and allocation-free for the thread-safety reason above.
    #
    # HONESTY NOTE, because this matters for how much to trust this code:
    # unlike the Windows tier in stage 5.2 - which was proven against real
    # memory-bomb, fork-bomb and orphan-grandchild processes before it
    # shipped - this POSIX tier has NOT been executed on a POSIX host. The
    # development machine and the only CI runner are both windows-latest,
    # so `create_execution_guard()` never returns this class in either
    # place and its enforcement tests are skipped there. It is written
    # against documented stdlib behaviour and unit-tested for the parts
    # that can be exercised anywhere (which kwargs it contributes, which
    # rlimits it would set, that close() targets the group not the pid),
    # but the real caps have not been observed firing. Treat it as
    # unverified-in-practice until someone runs the POSIX-gated tests in
    # backend/tests/test_execution_guard_posix.py on a real POSIX box.
    import os
    import signal

    try:
        import resource as _resource
    except ImportError:  # pragma: no cover - POSIX-only import
        _resource = None

    # ADR-005 Decision #2 names a CPU cap ("60 s CPU") alongside the memory
    # cap. RLIMIT_CPU is the POSIX way to express it and costs nothing to
    # set here - note this is CPU-seconds consumed, not wall-clock, so it
    # is complementary to (not a replacement for) the existing wall-clock
    # timeouts in both execution surfaces.
    DEFAULT_CPU_SECONDS = 60

    # Bounds a single runaway write; well above any legitimate sandbox
    # output while still stopping a disk-filling loop.
    DEFAULT_FILE_SIZE_LIMIT_BYTES = 1 * 1024 * 1024 * 1024  # 1 GiB

    class _PosixResourceGuard(ExecutionResourceGuard):
        def __init__(
            self,
            memory_limit_bytes: int,
            active_process_limit: int,
            cpu_seconds: int,
            file_size_limit_bytes: int,
        ) -> None:
            self._memory_limit_bytes = memory_limit_bytes
            self._active_process_limit = active_process_limit
            self._cpu_seconds = cpu_seconds
            self._file_size_limit_bytes = file_size_limit_bytes
            self._pgid = None
            # Same check-then-null race the Windows guard's own close() has
            # to defend against (see its comment) - an external stop() can
            # race the run loop's own cleanup on a different thread.
            self._lock = threading.Lock()

        def _apply_child_limits(self) -> None:  # pragma: no cover - runs post-fork
            """Runs in the forked child, before exec. Deliberately minimal:
            no allocation, no logging, no imports - see the preexec_fn
            thread-safety note on this module's POSIX block."""
            if _resource is None:
                return
            for limit_name, value in (
                ("RLIMIT_AS", self._memory_limit_bytes),
                ("RLIMIT_CPU", self._cpu_seconds),
                ("RLIMIT_NPROC", self._active_process_limit),
                ("RLIMIT_FSIZE", self._file_size_limit_bytes),
            ):
                if not value:
                    continue
                limit = getattr(_resource, limit_name, None)
                if limit is None:
                    continue
                try:
                    _resource.setrlimit(limit, (value, value))
                except (ValueError, OSError):
                    # A limit the kernel refuses (already lower, or not
                    # supported on this platform - RLIMIT_NPROC is absent on
                    # some systems) must not abort the run: the same
                    # fail-open stance the Windows tier takes when job
                    # creation fails.
                    pass

        def popen_kwargs(self) -> dict:
            return {
                "process_group": 0,
                "preexec_fn": self._apply_child_limits,
            }

        def assign(self, pid: int) -> None:
            # With process_group=0 the child leads its own group, so the
            # group id IS the child pid - that is what close() kills.
            with self._lock:
                self._pgid = pid

        def close(self) -> None:
            with self._lock:
                pgid, self._pgid = self._pgid, None
            if pgid is None:
                return
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                # Already gone (the normal clean-exit path), or never
                # actually became a group leader. Nothing to clean up.
                pass

    # Same single-source-of-truth dispatch the Windows branch uses - see
    # its comment.
    def _create_platform_guard(
        memory_limit_bytes: int, active_process_limit: int
    ) -> ExecutionResourceGuard:
        return _PosixResourceGuard(
            memory_limit_bytes=memory_limit_bytes,
            active_process_limit=active_process_limit,
            cpu_seconds=DEFAULT_CPU_SECONDS,
            file_size_limit_bytes=DEFAULT_FILE_SIZE_LIMIT_BYTES,
        )


def create_execution_guard(
    memory_limit_bytes: int = DEFAULT_MEMORY_LIMIT_BYTES,
    active_process_limit: int = DEFAULT_ACTIVE_PROCESS_LIMIT,
) -> ExecutionResourceGuard:
    """One guard per subprocess-management lifecycle (a Py-Coder REPL's
    single long-lived child; one Code Sandbox subprocess invocation).

    Call order matters, and is the same on every platform:
      1. `guard = create_execution_guard()`
      2. `Popen(..., **guard.popen_kwargs())` - POSIX applies its rlimits
         between fork and exec, so the guard must exist BEFORE the spawn.
      3. `guard.assign(process.pid)`
      4. `guard.close()` exactly once when that process is done with -
         whether it exited on its own or is being forcibly stopped. Safe to
         call on a guard whose process already exited cleanly."""
    return _create_platform_guard(memory_limit_bytes, active_process_limit)
