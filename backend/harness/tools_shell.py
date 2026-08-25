"""H2's shell.exec: one shell command run inside the harness workspace.

Every spawn follows the codebase's two invariants without exception
(PLAN-2026-08-24 §3.3): `create_execution_guard()` is created BEFORE
Popen so its popen_kwargs reach the spawn (POSIX process group + rlimits;
Windows job object assigned right after), and `env=safe_subprocess_env()`
so the child never inherits the backend's environ (provider API keys).
cwd is the workspace directory - the same never-the-app's-own-cwd posture
PythonREPL takes - and stdout/stderr are merged, bounded, and fed back as
the tool result.

Registered approval="always" under code.execute: EVERY distinct command
prompts a human with the verbatim command text (the fingerprint means an
identical repeat in the same run does not re-ask; any changed argument is
a new decision). That is deliberately stricter than a dangerous-command
list alone - in a scratch workspace the risk is not `rm` specifically, it
is arbitrary code running as the user, which is exactly the risk class
the builder's autopilot code.execute review-fix concluded always warrants
a human look.

§2.4's two additional permission mechanics ride on top of that baseline
(backend/harness/shell_policy.py does the analysis):

- **Segmentation.** A chained command is split on shell separators and the
  approval prompt discloses one line per thing that will actually run,
  so a dangerous tail cannot hide behind a benign head.
- **The dangerous list.** A segment naming a non-undoable command forces a
  FRESH prompt via ToolRegistry's `always_reprompt` hook, defeating the
  fingerprint memo that would otherwise let an identical repeat through
  silently. Command substitution and subshells are treated as dangerous
  by default, because their contents are not parsed.

`shell.session` is the long-running counterpart (§2.3's "session-based
exec with stdin write-back") - see backend/harness/shell_sessions.py.

Cancellation is cooperative: the worker thread polls the run's cancel
event alongside the timeout and tears the whole process tree down via
guard.close() on either - so Stop lands mid-command, not after it.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time

from pathlib import Path

from api_provider import RequestCancelledError
from backend.harness.shell_policy import is_dangerous_command
from backend.harness.shell_sessions import ShellSessionError, ShellSessionRegistry
from backend.harness.workspace import WorkspaceError, ensure_workspace
from backend.providers.base import ToolCall, ToolSpec
from backend.tools import CODE_EXECUTE, RunContext, ToolRegistry, ToolResult
from graphlink_execution_guard import create_execution_guard
from graphlink_process_env import safe_subprocess_env

SHELL_EXEC_TIMEOUT_SECONDS = 120
_OUTPUT_CAP_CHARS = 20_000
_COMMAND_CAP_CHARS = 4_000
_POLL_SECONDS = 0.2

SHELL_EXEC_SPEC = ToolSpec(
    name="shell.exec",
    description=(
        "Run one shell command inside the workspace (cwd is the workspace "
        "root) and return its merged stdout/stderr and exit code. Every "
        "command needs the user's approval before it runs. Hard timeout "
        f"{SHELL_EXEC_TIMEOUT_SECONDS}s; output truncated past "
        f"{_OUTPUT_CAP_CHARS} characters. One command per call - no "
        "interactive stdin."
    ),
    input_schema={
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
)


def _run_command(command: str, cwd, cancel_event) -> tuple[str, "int | None", str]:
    """Blocking worker (called via to_thread): returns (output, exit_code,
    ended) where ended is "ok" | "timeout" | "cancelled". guard-then-Popen,
    then a poll loop so cancel/timeout kill the whole tree promptly."""
    kwargs = {"env": safe_subprocess_env()}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    guard = create_execution_guard()
    try:
        # shell=True hands the RAW string to cmd.exe /c (Windows) or
        # /bin/sh -c (POSIX). A hand-built argv list here would round-trip
        # through list2cmdline's backslash-quote escaping, which cmd.exe
        # does not parse - quotes inside the command silently turn a
        # program's argument into a no-op expression (found by test:
        # `python -c "print(123)"` exited 0 printing nothing).
        process = subprocess.Popen(
            command,
            shell=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(cwd),
            **guard.popen_kwargs(),
            **kwargs,
        )
        guard.assign(process.pid)
        deadline = time.monotonic() + SHELL_EXEC_TIMEOUT_SECONDS
        # communicate() in a nested thread would be a second thread per
        # call; polling with a bounded read at the end is enough because
        # the pipe buffer is drained once the process exits, and a child
        # that fills the pipe and blocks still hits the timeout kill.
        while True:
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                return output, process.returncode, "ok"
            if cancel_event is not None and cancel_event.is_set():
                return "", None, "cancelled"
            if time.monotonic() > deadline:
                return "", None, "timeout"
            time.sleep(_POLL_SECONDS)
    finally:
        # Kills the whole tree for the cancelled/timeout paths; a no-op
        # beyond bookkeeping for a process that already exited.
        guard.close()


SHELL_SESSION_SPEC = ToolSpec(
    name="shell.session",
    description=(
        "Manage a long-running process in the workspace (dev server, watch "
        "build, REPL) that shell.exec cannot hold open. Actions: 'start' "
        "(needs name + command), 'write' (needs name + input; sends a line "
        "to its stdin), 'read' (needs name; returns output since the last "
        "read), 'stop' (needs name), 'list' (no arguments). Output is "
        "buffered between reads, so poll with 'read' rather than expecting "
        "'start' to return results. Every action needs the user's approval."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["start", "write", "read", "stop", "list"]},
            "name": {"type": "string", "description": "Session name; required for every action except 'list'."},
            "command": {"type": "string", "description": "start only: the command to run."},
            "input": {"type": "string", "description": "write only: the line to send to stdin."},
        },
        "required": ["action"],
    },
)


# How long `start`/`write` wait for a session to say something before
# reporting back. A bounded POLL, not a fixed sleep: a fixed sleep short
# enough to feel responsive (0.3s) is shorter than a Python interpreter
# takes to start on Windows, so a command that dies on launch was reported
# as healthy - the poll instead returns the instant either the process
# exits or it produces output, and only pays the full wait for a process
# that starts silently and stays up.
_SETTLE_SECONDS = 2.0
_SETTLE_POLL_SECONDS = 0.05


async def _settle(session, *, want_output: bool = True) -> str:
    """Wait briefly for a just-started/just-written session to react.
    Returns whatever output accumulated. Exits early on process death (the
    caller reports that) or, when `want_output`, on the first output."""
    deadline = time.monotonic() + _SETTLE_SECONDS
    collected: list[str] = []
    while time.monotonic() < deadline:
        if not session.running:
            break
        chunk = session.read_new()
        if chunk:
            collected.append(chunk)
            if want_output:
                break
        await asyncio.sleep(_SETTLE_POLL_SECONDS)
    tail = session.read_new()
    if tail:
        collected.append(tail)
    return "\n".join(part for part in collected if part)


def _workspace_for(ctx: RunContext):
    """The run's bound root - a trusted user directory when one is bound
    (already resolved by the loop and carried on the context), else the
    managed scratch dir. Shared by both tools below."""
    bound = getattr(ctx, "harness_workspace_dir", None)
    if isinstance(bound, Path):
        return bound
    workspace_id = getattr(ctx, "harness_workspace_id", None)
    if not isinstance(workspace_id, str) or not workspace_id:
        raise WorkspaceError("No harness workspace is bound to this run.")
    return ensure_workspace(workspace_id)


def register_harness_shell_tool(registry: ToolRegistry, sessions: "ShellSessionRegistry | None" = None) -> None:
    # A registry is normally supplied by the dispatcher (so sessions outlive
    # the task that started them); the fallback keeps this callable standalone
    # in tests, where per-call lifetime is exactly what is wanted.
    session_registry = sessions if sessions is not None else ShellSessionRegistry()

    async def shell_session(call: ToolCall, ctx: RunContext) -> ToolResult:
        action = str(call.arguments.get("action") or "").strip().lower()
        name = str(call.arguments.get("name") or "").strip()
        workspace_id = getattr(ctx, "harness_workspace_id", None)
        if not isinstance(workspace_id, str) or not workspace_id:
            return ToolResult(content="No harness workspace is bound to this run.", is_error=True)
        if action != "list" and not name:
            return ToolResult(content=f"shell.session '{action}' needs a session name.", is_error=True)
        try:
            if action == "list":
                rows = session_registry.names(workspace_id)
                if not rows:
                    return ToolResult(content="No shell sessions in this workspace.")
                lines = [
                    f"{n}: {'running' if running else f'exited ({code})'}"
                    for n, running, code in rows
                ]
                return ToolResult(content="\n".join(lines))

            if action == "start":
                command = str(call.arguments.get("command") or "").strip()
                if not command:
                    return ToolResult(content="shell.session 'start' needs a command.", is_error=True)
                if len(command) > _COMMAND_CAP_CHARS:
                    return ToolResult(
                        content=f"Command longer than {_COMMAND_CAP_CHARS} characters.", is_error=True,
                    )
                workspace = _workspace_for(ctx)
                session = await asyncio.to_thread(
                    session_registry.start, workspace_id, name, command, workspace,
                )
                # A process that dies instantly (bad command, missing binary)
                # is the common failure, and reporting it as a successful
                # start would leave the model polling a corpse.
                output = await _settle(session, want_output=False)
                if not session.running:
                    return ToolResult(
                        content=f"Session {name!r} exited immediately (code {session.exit_code}).\n{output}",
                        is_error=True,
                    )
                return ToolResult(content=f"Session {name!r} started.\n{output}".rstrip())

            if action == "write":
                text = call.arguments.get("input")
                if not isinstance(text, str) or not text:
                    return ToolResult(content="shell.session 'write' needs non-empty input.", is_error=True)
                session = session_registry.get(workspace_id, name)
                await asyncio.to_thread(session.write, text)
                output = await _settle(session)
                return ToolResult(content=output or f"Wrote to {name!r} (no output yet).")

            if action == "read":
                session = session_registry.get(workspace_id, name)
                output = session.read_new()
                status = "running" if session.running else f"exited ({session.exit_code})"
                return ToolResult(content=f"[{status}]\n{output}".rstrip())

            if action == "stop":
                await asyncio.to_thread(session_registry.stop, workspace_id, name)
                return ToolResult(content=f"Session {name!r} stopped.")

            return ToolResult(
                content=f"Unknown action {action!r} - use start, write, read, stop, or list.",
                is_error=True,
            )
        except ShellSessionError as exc:
            return ToolResult(content=str(exc), is_error=True)
        except (WorkspaceError, OSError) as exc:
            return ToolResult(content=f"Could not prepare the workspace: {exc}", is_error=True)

    async def shell_exec(call: ToolCall, ctx: RunContext) -> ToolResult:
        # The command runs with cwd = the run's bound root: the trusted
        # user directory when one is bound (already ensured by the loop and
        # carried on the context), else the managed scratch dir created
        # here. A user dir is the person's own folder - never chmod'd or
        # created by us.
        bound = getattr(ctx, "harness_workspace_dir", None)
        command = str(call.arguments.get("command") or "").strip()
        if not command:
            return ToolResult(content="shell.exec needs a non-empty command.", is_error=True)
        if len(command) > _COMMAND_CAP_CHARS:
            return ToolResult(content=f"Command longer than {_COMMAND_CAP_CHARS} characters.", is_error=True)
        try:
            if isinstance(bound, Path):
                workspace = bound
            else:
                workspace_id = getattr(ctx, "harness_workspace_id", None)
                if not isinstance(workspace_id, str) or not workspace_id:
                    return ToolResult(content="No harness workspace is bound to this run.", is_error=True)
                workspace = ensure_workspace(workspace_id)
        except (WorkspaceError, OSError) as exc:
            return ToolResult(content=f"Could not prepare the workspace: {exc}", is_error=True)

        cancel_event = ctx.cancel.event if ctx.cancel is not None else None
        output, exit_code, ended = await asyncio.to_thread(
            _run_command, command, workspace, cancel_event,
        )
        if ended == "cancelled":
            # The one exception invoke() propagates - cancellation is the
            # loop's mechanism, never a tool "error" fed to the model.
            raise RequestCancelledError("stopped")
        if ended == "timeout":
            return ToolResult(
                content=f"Command timed out after {SHELL_EXEC_TIMEOUT_SECONDS}s and was killed.",
                is_error=True,
            )
        if len(output) > _OUTPUT_CAP_CHARS:
            output = output[:_OUTPUT_CAP_CHARS] + f"\n…[truncated at {_OUTPUT_CAP_CHARS} characters]"
        return ToolResult(
            content=f"exit code {exit_code}\n{output}" if output else f"exit code {exit_code} (no output)",
            is_error=bool(exit_code),
        )

    def _exec_needs_fresh_prompt(call: ToolCall) -> bool:
        return is_dangerous_command(str(call.arguments.get("command") or ""))

    def _session_needs_fresh_prompt(call: ToolCall) -> bool:
        # 'start' carries a command and is judged like shell.exec. 'write'
        # always re-prompts: feeding stdin to an already-running process is
        # unbounded in effect (it is that program's input language, not a
        # command line we can analyze), so a remembered grant must never
        # cover a second, different write.
        action = str(call.arguments.get("action") or "").strip().lower()
        if action == "start":
            return is_dangerous_command(str(call.arguments.get("command") or ""))
        return action == "write"

    registry.register(
        SHELL_EXEC_SPEC, shell_exec, scopes={CODE_EXECUTE}, approval="always",
        always_reprompt=_exec_needs_fresh_prompt,
    )
    registry.register(
        SHELL_SESSION_SPEC, shell_session, scopes={CODE_EXECUTE}, approval="always",
        always_reprompt=_session_needs_fresh_prompt,
    )
