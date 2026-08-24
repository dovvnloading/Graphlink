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
list - in a scratch workspace the risk is not `rm` specifically, it is
arbitrary code running as the user, which is exactly the risk class the
builder's autopilot code.execute review-fix concluded always warrants a
human look. Sessions/stdin write-back for long-running processes are a
later increment; one command per call, with a hard wall timeout.

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


def register_harness_shell_tool(registry: ToolRegistry) -> None:
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

    registry.register(SHELL_EXEC_SPEC, shell_exec, scopes={CODE_EXECUTE}, approval="always")
