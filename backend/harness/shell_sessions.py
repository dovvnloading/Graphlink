"""Long-running shell sessions (PLAN-2026-08-24 §2.3/§3.2.6).

`shell.exec` runs one command to completion and returns its output. That
is the wrong shape for the other half of real workspace work: a dev
server, a watch build, a REPL, an interactive installer - processes that
never exit on their own and that the agent needs to keep talking to. The
plan's requirement is "session-based exec ... with `write_stdin` for
long-running processes", over "a pluggable backend ABC (local guarded
subprocess now; container later per ADR-005)".

Structure:

- `ShellBackend` is the ABC. `LocalSubprocessBackend` is the only
  implementation today and carries the SAME two non-negotiable spawn
  invariants every other subprocess in this codebase follows (§3.3):
  `create_execution_guard()` built BEFORE Popen so its popen_kwargs reach
  the spawn, and `env=safe_subprocess_env()` so the child never inherits
  the backend's environ. A container backend slots in here later without
  touching the tool layer.

- `ShellSession` owns one live process plus a bounded output ring drained
  by a daemon reader thread. Bounded because a watch build emits output
  forever and an unbounded buffer is a memory leak with a friendly name;
  the ring keeps the most recent output, which is what anyone debugging
  actually wants. `read_new()` returns only what arrived since the last
  read, so the model sees a stream rather than re-reading the same tail.

- `ShellSessionRegistry` is per-DISPATCHER (one per app session), keyed by
  harness workspace id, because a session must outlive the single task
  that started it - starting a dev server in one message and curling it in
  the next is the entire point. Teardown hangs off the same three triggers
  the scratch dirs already use: node delete, session evict, app shutdown.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from abc import ABC, abstractmethod
from collections import deque
from pathlib import Path

from graphlink_execution_guard import spawn_guarded_subprocess, assign_guarded_subprocess, ExecutionResourceGuard, create_execution_guard
from graphlink_process_env import safe_subprocess_env

# Per-session output ring. Lines, not bytes: a line is the unit anyone
# reading build output thinks in, and a cap on lines bounds memory just as
# effectively once each line is itself capped.
MAX_BUFFERED_LINES = 2_000
MAX_LINE_CHARS = 4_000

# Per workspace. Small by design (§2.7's "concurrency capped - single
# digits" applied to processes): a workspace needing more than this many
# simultaneous long-running processes is doing something the agent should
# be made to justify explicitly rather than accumulate silently.
MAX_SESSIONS_PER_WORKSPACE = 4

# How long stop() waits for a terminated process to actually die before
# giving up and letting the guard's own tree-kill be the last word.
_STOP_JOIN_SECONDS = 3.0


class ShellSessionError(RuntimeError):
    """Anything a caller should surface to the model as a tool error."""


class ShellBackend(ABC):
    """The seam a container/VM execution backend implements later."""

    @abstractmethod
    def spawn(self, command: str, cwd: Path):
        """Return an object with the subprocess.Popen surface this module
        uses: stdin/stdout, poll(), wait(), kill(), pid."""

    @abstractmethod
    def teardown(self, handle) -> None:
        """Release whatever containment `spawn` established for `handle`."""


class LocalSubprocessBackend(ShellBackend):
    """Guarded local subprocess - the ADR-005 stage-5.2/5.3 posture."""

    def __init__(self) -> None:
        self._guards: dict[int, ExecutionResourceGuard] = {}

    def spawn(self, command: str, cwd: Path):
        kwargs: dict = {"env": safe_subprocess_env()}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        guard = create_execution_guard()
        try:
            process = spawn_guarded_subprocess(guard,
                command,
                shell=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(cwd),
                **guard.popen_kwargs(),
                **kwargs,
            )
        except Exception:
            guard.close()
            raise
        try:
            assign_guarded_subprocess(guard, process)
        except BaseException:
            # Do not return a live, unguarded session to the Harness.
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                pass
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        pass
            guard.close()
            raise
        self._guards[id(process)] = guard
        return process

    def teardown(self, handle) -> None:
        guard = self._guards.pop(id(handle), None)
        if guard is not None:
            # Closing the guard is what kills the whole TREE - a watch build
            # that spawned its own children dies with it, which plain
            # process.kill() would leave orphaned (the ADR-005 finding).
            guard.close()


class ShellSession:
    """One named long-running process inside a workspace."""

    def __init__(self, name: str, command: str, cwd: Path, backend: ShellBackend) -> None:
        self.name = name
        self.command = command
        self.cwd = cwd
        self._backend = backend
        self._lines: deque[str] = deque(maxlen=MAX_BUFFERED_LINES)
        self._lock = threading.Lock()
        self._dropped = 0
        self._process = backend.spawn(command, cwd)
        self._reader = threading.Thread(target=self._drain, name=f"shell-session-{name}", daemon=True)
        self._reader.start()

    def _drain(self) -> None:
        """Reader thread: pulls stdout line-by-line until EOF. Daemon +
        best-effort - a process killed out from under this loop raises on
        read, which is a normal end-of-life, not an error to report."""
        stream = self._process.stdout
        if stream is None:
            return
        try:
            # A plain "for line in stream" reads a *complete* logical line
            # before the MAX_LINE_CHARS truncation. A subprocess producing
            # gigabytes without a newline would exhaust memory in readline().
            # Bound each read itself; discard the remainder of an oversized
            # line without ever materializing it.
            discarding_long_line = False
            while True:
                fragment = stream.readline(MAX_LINE_CHARS + 1)
                if not fragment:
                    break
                completed_line = fragment.endswith("\n")
                if discarding_long_line:
                    if completed_line:
                        discarding_long_line = False
                    continue
                text = fragment.rstrip("\n")
                if len(text) > MAX_LINE_CHARS:
                    text = text[:MAX_LINE_CHARS] + "…[line truncated]"
                    discarding_long_line = not completed_line
                with self._lock:
                    if len(self._lines) == self._lines.maxlen:
                        self._dropped += 1
                    self._lines.append(text)
        except (ValueError, OSError):
            return

    @property
    def running(self) -> bool:
        return self._process.poll() is None

    @property
    def exit_code(self) -> "int | None":
        return self._process.poll()

    def write(self, text: str) -> None:
        """Write to the process's stdin. A newline is appended when absent -
        an interactive program waits on a line, and a model that omits the
        newline would otherwise hang with no feedback."""
        if not self.running:
            raise ShellSessionError(f"session {self.name!r} has already exited")
        stdin = self._process.stdin
        if stdin is None:
            raise ShellSessionError(f"session {self.name!r} has no stdin")
        try:
            stdin.write(text if text.endswith("\n") else text + "\n")
            stdin.flush()
        except (ValueError, OSError) as exc:
            raise ShellSessionError(f"could not write to session {self.name!r}: {exc}") from exc

    def read_new(self) -> str:
        """Drain and return everything buffered since the last read."""
        with self._lock:
            lines = list(self._lines)
            dropped = self._dropped
            self._lines.clear()
            self._dropped = 0
        if dropped:
            lines.insert(0, f"…[{dropped} earlier line(s) dropped - output exceeded the buffer]")
        return "\n".join(lines)

    def stop(self) -> None:
        """Terminate the process and its whole tree, then join the reader."""
        # Kill the child BEFORE closing stdin. If a concurrent write() has
        # filled the pipe, stdin.close() can block waiting for the stream's
        # write lock; killing the process first releases the blocked writer.
        try:
            self._process.kill()
        except (ValueError, OSError, ProcessLookupError):
            pass
        self._backend.teardown(self._process)
        try:
            if self._process.stdin is not None:
                self._process.stdin.close()
        except (ValueError, OSError):
            pass
        try:
            self._process.wait(timeout=_STOP_JOIN_SECONDS)
        except Exception:
            pass
        self._reader.join(timeout=_STOP_JOIN_SECONDS)


class ShellSessionRegistry:
    """Per-dispatcher store of live sessions, keyed by workspace id."""

    def __init__(self, backend: "ShellBackend | None" = None) -> None:
        self._backend = backend or LocalSubprocessBackend()
        self._by_workspace: dict[str, dict[str, ShellSession]] = {}
        self._roots: dict[str, Path] = {}
        self._lock = threading.RLock()

    def bind_root(self, workspace_id: str, cwd: Path) -> None:
        """Retire processes when the same node binds to a different root.

        A harness can switch from a previously trusted project folder back
        to managed scratch between runs. The old sessions used to remain
        reachable by name under the same workspace_id, so a new run could
        write stdin to a process STILL running in the old trusted folder.
        """
        resolved = cwd.resolve()
        with self._lock:
            if self._roots.get(workspace_id) == resolved:
                return
            retired = self._by_workspace.pop(workspace_id, {})
            self._roots[workspace_id] = resolved
        for session in retired.values():
            session.stop()

    def start(self, workspace_id: str, name: str, command: str, cwd: Path) -> ShellSession:
        with self._lock:
            resolved = cwd.resolve()
            bound = self._roots.setdefault(workspace_id, resolved)
            if bound != resolved:
                raise ShellSessionError(
                    "Harness workspace root changed; retry in the currently bound workspace."
                )
            sessions = self._by_workspace.setdefault(workspace_id, {})
            existing = sessions.get(name)
            if existing is not None:
                if existing.running:
                    raise ShellSessionError(
                        f"session {name!r} is already running - read it, write to it, "
                        "or stop it before starting a new one under that name"
                    )
                # A dead session's name is reusable, but its process tree
                # still needs releasing before we overwrite the entry.
                existing.stop()
                sessions.pop(name, None)
            live = sum(1 for session in sessions.values() if session.running)
            if live >= MAX_SESSIONS_PER_WORKSPACE:
                raise ShellSessionError(
                    f"this workspace already has {live} running sessions "
                    f"(limit {MAX_SESSIONS_PER_WORKSPACE}) - stop one first"
                )
            session = ShellSession(name, command, cwd, self._backend)
            sessions[name] = session
            return session

    def get(self, workspace_id: str, name: str, expected_root: Path | None = None) -> ShellSession:
        with self._lock:
            if (expected_root is not None
                    and self._roots.get(workspace_id) != expected_root.resolve()):
                raise ShellSessionError("The workspace root changed; old shell sessions are unavailable.")
            session = self._by_workspace.get(workspace_id, {}).get(name)
        if session is None:
            raise ShellSessionError(f"no session named {name!r} in this workspace")
        return session

    def names(self, workspace_id: str) -> list[tuple[str, bool, "int | None"]]:
        with self._lock:
            sessions = dict(self._by_workspace.get(workspace_id, {}))
        return [(name, s.running, s.exit_code) for name, s in sorted(sessions.items())]

    def stop(self, workspace_id: str, name: str) -> None:
        with self._lock:
            session = self._by_workspace.get(workspace_id, {}).pop(name, None)
        if session is None:
            raise ShellSessionError(f"no session named {name!r} in this workspace")
        session.stop()

    def stop_workspace(self, workspace_id: str) -> None:
        """Node-delete teardown: every session in one workspace."""
        with self._lock:
            sessions = self._by_workspace.pop(workspace_id, {})
            self._roots.pop(workspace_id, None)
        for session in sessions.values():
            session.stop()

    def stop_all(self) -> None:
        """Session-evict / shutdown teardown: everything this registry owns."""
        with self._lock:
            everything = list(self._by_workspace.values())
            self._by_workspace.clear()
            self._roots.clear()
        for sessions in everything:
            for session in sessions.values():
                session.stop()
