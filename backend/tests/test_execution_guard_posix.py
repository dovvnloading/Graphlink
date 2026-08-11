"""ADR-005 stage 5.3: the POSIX resource-guard tier.

READ THIS BEFORE TRUSTING THE POSIX TIER. This project's development
machine and its only CI runner are both Windows. `create_execution_guard()`
therefore never returns `_PosixResourceGuard` in either place, and every
test in `TestPosixEnforcementIsReal` below is skipped there. Those are the
tests that would actually prove the caps fire - a memory bomb dying at
RLIMIT_AS, a fork bomb capped by RLIMIT_NPROC, `killpg` reaping a whole
process tree. Until someone runs this file on a real POSIX host, the
enforcement is *written against documented stdlib behaviour but not
observed working*, and this codebase should not claim otherwise. That is a
deliberate, disclosed gap, not an oversight - contrast ADR-005 stage 5.2's
Windows tier, which was proven against real memory-bomb / fork-bomb /
orphan-grandchild processes before it shipped.

What DOES run everywhere is `TestPosixGuardLogic`. The POSIX classes live
inside the module's `else:` branch, which on Windows is never executed at
all - meaning a typo or NameError in that whole block would be invisible to
every test AND to CI, since the only CI runner is Windows. That is an
unacceptable blind spot for code that cannot otherwise be verified, so the
`posix_guard_module` fixture below loads a SECOND, independent copy of
graphlink_execution_guard.py with `sys.platform` faked to "linux", forcing
the POSIX branch to actually execute. (An independent load via
`spec_from_file_location`, deliberately NOT `importlib.reload` of the real
module: reload would mutate the module every other test shares, and would
leave the real `create_execution_guard` pointing at the POSIX factory if a
teardown were ever missed.) That turns "never executed anywhere" into "its
decisions are asserted on every push" - which rlimits it sets and to what
values, that close() targets the process GROUP rather than the bare pid,
that an already-dead group is not an error. It still cannot catch a wrong
assumption about how the kernel behaves; only TestPosixEnforcementIsReal
can do that, and only on a POSIX host.
"""

from __future__ import annotations

import contextlib
import importlib.util
import pathlib
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

import graphlink_execution_guard as guard_module

POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX resource guard: rlimits/process groups do not exist on Windows",
)


@pytest.fixture(scope="module")
def posix_guard_module():
    """An independently-loaded copy of graphlink_execution_guard with the
    POSIX branch forced to execute - see the module docstring for why this
    is a separate load rather than a reload. Never registered in
    sys.modules, so nothing else in the suite can observe it."""
    spec = importlib.util.spec_from_file_location(
        "graphlink_execution_guard__posix_probe",
        pathlib.Path(guard_module.__file__),
    )
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "platform", "linux"):
        spec.loader.exec_module(module)
    assert hasattr(module, "_PosixResourceGuard"), (
        "forcing sys.platform=linux did not define the POSIX guard - the "
        "module's platform branching changed shape"
    )
    return module


@contextlib.contextmanager
def _patched_group_kill(module, **kwargs):
    """Fake out BOTH POSIX-only names close() touches.

    `os.killpg` and `signal.SIGKILL` are both absent on Windows, where these
    tests still run via the forced-load fixture - and SIGKILL is evaluated
    as an ARGUMENT before killpg is even called, so patching killpg alone
    still raises AttributeError. Deliberately faked here in the test rather
    than papered over with a `getattr(signal, "SIGKILL", ...)` fallback in
    the guard itself: on every platform that actually runs this code the
    name exists, so such a fallback would be dead production code added
    purely to serve a test."""
    with patch.object(module.signal, "SIGKILL", 9, create=True):
        with patch.object(module.os, "killpg", create=True, **kwargs) as killpg:
            yield killpg


def _make_posix_guard(module, **overrides):
    kwargs = {
        "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
        "active_process_limit": 64,
        "cpu_seconds": 60,
        "file_size_limit_bytes": 1024 * 1024 * 1024,
    }
    kwargs.update(overrides)
    return module._PosixResourceGuard(**kwargs)


class TestPosixGuardLogic:
    """Runs on EVERY platform, via the forced-load fixture above. Asserts
    the guard's decisions, not kernel behaviour - see the module docstring."""

    def test_the_posix_branch_actually_executes(self, posix_guard_module):
        # The blind-spot canary: if the POSIX block ever fails to load
        # (typo, bad import, syntax that only bites at exec time), this
        # fails on Windows CI rather than silently shipping broken code to
        # the one platform nobody here can run.
        assert posix_guard_module._PosixResourceGuard is not None
        guard = posix_guard_module.create_execution_guard()
        assert type(guard).__name__ == "_PosixResourceGuard"

    def test_popen_kwargs_request_a_dedicated_process_group(self, posix_guard_module):
        guard = _make_posix_guard(posix_guard_module)
        kwargs = guard.popen_kwargs()
        # process_group=0 -> setpgid(0, 0) in the child, done in C inside
        # subprocess's own fork-exec helper rather than via preexec_fn.
        # This is what makes close()'s killpg able to reap the whole tree.
        assert kwargs["process_group"] == 0

    def test_popen_kwargs_install_the_rlimit_hook(self, posix_guard_module):
        guard = _make_posix_guard(posix_guard_module)
        kwargs = guard.popen_kwargs()
        assert kwargs["preexec_fn"] == guard._apply_child_limits

    def test_close_kills_the_process_group_not_just_the_pid(self, posix_guard_module):
        guard = _make_posix_guard(posix_guard_module)
        guard.assign(4242)
        with _patched_group_kill(posix_guard_module) as killpg:
            guard.close()
        killpg.assert_called_once()
        assert killpg.call_args[0][0] == 4242, (
            "close() must target the process GROUP id - killing only the "
            "tracked pid is exactly the 'Stop left orphans behind' gap this "
            "tier exists to close"
        )

    def test_close_is_idempotent_and_only_kills_once(self, posix_guard_module):
        guard = _make_posix_guard(posix_guard_module)
        guard.assign(4242)
        with _patched_group_kill(posix_guard_module) as killpg:
            guard.close()
            guard.close()
        assert killpg.call_count == 1

    def test_close_before_assign_does_not_kill_anything(self, posix_guard_module):
        guard = _make_posix_guard(posix_guard_module)
        with _patched_group_kill(posix_guard_module) as killpg:
            guard.close()
        killpg.assert_not_called()

    def test_an_already_dead_group_is_not_an_error(self, posix_guard_module):
        guard = _make_posix_guard(posix_guard_module)
        guard.assign(4242)
        with _patched_group_kill(posix_guard_module, side_effect=ProcessLookupError):
            guard.close()  # the normal clean-exit path; must not raise

    def test_every_configured_rlimit_is_applied_with_its_value(self, posix_guard_module):
        guard = _make_posix_guard(
            posix_guard_module,
            memory_limit_bytes=111,
            active_process_limit=222,
            cpu_seconds=333,
            file_size_limit_bytes=444,
        )
        fake_resource = MagicMock()
        fake_resource.RLIMIT_AS = "AS"
        fake_resource.RLIMIT_CPU = "CPU"
        fake_resource.RLIMIT_NPROC = "NPROC"
        fake_resource.RLIMIT_FSIZE = "FSIZE"

        with patch.object(posix_guard_module, "_resource", fake_resource):
            guard._apply_child_limits()

        applied = {call.args[0]: call.args[1] for call in fake_resource.setrlimit.call_args_list}
        assert applied == {
            "AS": (111, 111),
            "CPU": (333, 333),
            "NPROC": (222, 222),
            "FSIZE": (444, 444),
        }

    def test_a_rlimit_the_kernel_refuses_does_not_abort_the_run(self, posix_guard_module):
        # Fail-open, matching the Windows tier's stance when job creation
        # fails: a limit we cannot set must not stop the user's code from
        # running at all.
        guard = _make_posix_guard(posix_guard_module)
        fake_resource = MagicMock()
        fake_resource.RLIMIT_AS = "AS"
        fake_resource.RLIMIT_CPU = "CPU"
        fake_resource.RLIMIT_NPROC = "NPROC"
        fake_resource.RLIMIT_FSIZE = "FSIZE"
        fake_resource.setrlimit.side_effect = ValueError("kernel says no")

        with patch.object(posix_guard_module, "_resource", fake_resource):
            guard._apply_child_limits()  # must not raise

    def test_a_rlimit_absent_on_this_kernel_is_skipped(self, posix_guard_module):
        # RLIMIT_NPROC genuinely does not exist on some POSIX systems.
        guard = _make_posix_guard(posix_guard_module)
        fake_resource = MagicMock(spec=["RLIMIT_AS", "setrlimit"])
        fake_resource.RLIMIT_AS = "AS"

        with patch.object(posix_guard_module, "_resource", fake_resource):
            guard._apply_child_limits()

        applied = [call.args[0] for call in fake_resource.setrlimit.call_args_list]
        assert applied == ["AS"]

    def test_no_rlimits_are_attempted_when_the_resource_module_is_absent(self, posix_guard_module):
        # The stdlib `resource` module is POSIX-only; the guard imports it
        # defensively. If it is missing, the process-group half must still
        # work rather than the whole guard blowing up.
        guard = _make_posix_guard(posix_guard_module)
        with patch.object(posix_guard_module, "_resource", None):
            guard._apply_child_limits()  # must not raise
        assert guard.popen_kwargs()["process_group"] == 0


@POSIX_ONLY
class TestPosixEnforcementIsReal:
    """The tests that actually prove the kernel enforces what we asked for.
    THESE HAVE NEVER RUN in this project's CI - see the module docstring.
    They are written so that the first person to run them on a POSIX host
    gets a real answer rather than having to invent the harness."""

    def test_a_memory_bomb_is_killed_at_the_address_space_cap(self):
        guard = guard_module._PosixResourceGuard(
            memory_limit_bytes=100 * 1024 * 1024,
            active_process_limit=64,
            cpu_seconds=60,
            file_size_limit_bytes=1024 * 1024 * 1024,
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", "x = bytearray(500 * 1024 * 1024); import time; time.sleep(5)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **guard.popen_kwargs(),
        )
        guard.assign(proc.pid)
        try:
            assert proc.wait(timeout=30) != 0, (
                "a 500 MiB allocation under a 100 MiB RLIMIT_AS should have failed"
            )
        finally:
            guard.close()

    def test_closing_the_guard_kills_a_grandchild_process_too(self, tmp_path):
        marker = tmp_path / "marker.txt"
        grandchild = tmp_path / "grandchild.py"
        grandchild.write_text(
            "import time\n"
            f"with open(r'{marker}', 'w') as m:\n"
            "    while True:\n"
            "        m.write('x'); m.flush(); time.sleep(0.2)\n",
            encoding="utf-8",
        )
        parent = tmp_path / "parent.py"
        parent.write_text(
            "import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, r'{grandchild}'])\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )

        guard = _make_posix_guard(guard_module)
        proc = subprocess.Popen(
            [sys.executable, str(parent)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **guard.popen_kwargs(),
        )
        guard.assign(proc.pid)

        time.sleep(1.5)
        assert marker.exists(), "grandchild never started writing"
        size_before = marker.stat().st_size
        time.sleep(1)
        assert marker.stat().st_size > size_before

        guard.close()
        time.sleep(1.5)
        settled = marker.stat().st_size
        time.sleep(1.5)
        assert marker.stat().st_size == settled, (
            "grandchild kept writing after guard.close() - killpg did not "
            "reap the whole process group"
        )

    def test_a_fork_bomb_is_capped_by_the_process_limit(self, tmp_path):
        counter = tmp_path / "forkcount.txt"
        bomb = tmp_path / "forkbomb.py"
        bomb.write_text(
            "import subprocess, sys, time\n"
            f"path = r'{counter}'\n"
            "n = int(sys.argv[1]) if len(sys.argv) > 1 else 0\n"
            "with open(path, 'a') as fh:\n"
            "    fh.write('x')\n"
            "if n < 200:\n"
            "    try:\n"
            "        subprocess.Popen([sys.executable, __file__, str(n + 1)])\n"
            "    except Exception:\n"
            "        pass\n"
            "time.sleep(3)\n",
            encoding="utf-8",
        )

        guard = _make_posix_guard(guard_module, active_process_limit=5)
        proc = subprocess.Popen(
            [sys.executable, str(bomb)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **guard.popen_kwargs(),
        )
        guard.assign(proc.pid)
        try:
            time.sleep(5)
            count = len(counter.read_text(encoding="utf-8")) if counter.exists() else 0
            assert count < 200, f"RLIMIT_NPROC did not cap the fork bomb - {count} processes ran"
        finally:
            guard.close()
