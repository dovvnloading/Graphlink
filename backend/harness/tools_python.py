"""`python.exec` - the stateful Python REPL as a harness tool
(PLAN-2026-08-24 §3.2.6).

This is the capability Py-Coder existed to provide, finally expressed the
way the plan says it should be: not a bespoke three-agent pipeline with
its own approval fingerprinting and its own serializer branch, but ONE
tool among many, riding the shared `ToolRegistry` scope/approval regime
and the shared `PythonREPL` mechanics (nonce-delimited boundary, execution
guard, allowlisted env).

Why a persistent REPL rather than `shell.exec python -c ...`: state
survives between calls. Loading a dataframe once and querying it over
several turns is the ordinary shape of analysis work, and re-running the
load every turn is both slow and a different program each time. The REPL's
own `last_run_failed` is what distinguishes "the code raised" from "the
tool broke" - the model needs that difference to decide whether to fix its
code or its approach.

The REPL's cwd is the run's BOUND WORKSPACE (not a repl_id scratch dir),
while the interpreter comes from that workspace's .venv/venv or a lazily
created managed virtualenv outside a user's project folder. Thus `open("data.csv")` in executed code sees the same files `fs.read` and
`shell.exec` do. When that workspace is a user's own project folder the
REPL is constructed with `manage_cwd=False` - we never chmod or age-sweep
a directory the person owns (see PythonREPL's own docstring).

Registered `approval="always"` with forced fresh consent under `code.execute`.
Every call requires its own approval, including identical source and calls
after a session grant. Persistent state makes a repeated source string a new
execution with potentially different effects.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path, PurePosixPath

from api_provider import RequestCancelledError
from backend.harness.python_env import PythonEnvironmentError, ensure_python_environment
from backend.harness.python_isolation import (
    isolated_repl_command, planned_isolated_python_environment,
    prepare_isolated_python_environment,
)
from backend.harness.python_policy import source_error
from backend.harness.shell_isolation import IsolationUnavailableError, normalize_execution_profile
from backend.harness.workspace import WorkspaceError, ensure_workspace, workspace_dir
from backend.providers.base import ToolCall, ToolSpec
from backend.tools import CODE_EXECUTE, RunContext, ToolRegistry, ToolResult
from graphlink_plugins.common.python_repl import PythonREPL

# Matches the code_sandbox execute ceiling (graphlink_plugins/code_sandbox/
# domain.py): long enough for real analysis, short enough that a runaway
# loop surfaces as a timeout inside one turn rather than stalling the task.
PYTHON_EXEC_TIMEOUT_SECONDS = 240
_OUTPUT_CAP_CHARS = 20_000

PYTHON_EXEC_SPEC = ToolSpec(
    name="python.exec",
    description=(
        "Run Python in a persistent interpreter whose working directory is "
        "the workspace. Host uses a project .venv/venv or managed environment; "
        "Isolated always uses a separate managed container-native virtualenv "
        "inside a network-disabled container. "
        "Variables, imports, and loaded data survive between calls. "
        "Returns stdout/stderr plus whether the code raised. Use print() - "
        "expression values are not echoed. Every call needs the user's "
        f"approval. Hard timeout {PYTHON_EXEC_TIMEOUT_SECONDS}s."
    ),
    input_schema={
        "type": "object",
        "properties": {"code": {"type": "string"}},
        "required": ["code"],
    },
)


class PythonReplRegistry:
    """Per-dispatcher REPL store, keyed by harness workspace id.

    Same lifetime rationale as ShellSessionRegistry: a REPL whose whole
    value is surviving between calls must outlive the single task that
    created it, and must be torn down on the same three triggers (node
    delete, session evict, shutdown) so no interpreter outlives the node
    that owns it.
    """

    def __init__(self, *, lock: "threading.RLock | None" = None) -> None:
        # Value is (repl, cwd): the cwd is kept so a rebound workspace can
        # be detected - see get().
        self._repls: dict[str, tuple[PythonREPL, Path]] = {}
        # A Stop releases the run's slot before its worker has unwound.
        # Track the cancel event of the last run that borrowed each REPL,
        # so a new task can never reuse a still-stopping interpreter.
        self._owner_events: dict[str, threading.Event] = {}
        self._profiles: dict[str, str] = {}
        # Shared with dispatcher revocation and shell registration when
        # owned by a real dispatcher; standalone registries use their own.
        self._lock = lock if lock is not None else threading.RLock()

    def get(
        self, workspace_id: str, cwd: Path, *, manage_cwd: bool,
        python_executable: Path | PurePosixPath | None = None,
        cancel_event: threading.Event | None = None,
        profile: str = "host", command_builder=None,
    ) -> PythonREPL:
        """The workspace's interpreter, started on first use.

        A REPL's cwd is fixed at spawn, so an interpreter kept across a
        REBINDING (the node moves between scratch and a user's project
        folder) would keep executing in the old directory while fs.read and
        shell.exec had already moved to the new one - `open("data.csv")`
        silently reading a different file than `fs.read("data.csv")`. The
        cwd is therefore part of the cache key in effect: a changed root
        retires the old interpreter rather than reusing it.
        """
        with self._lock:
            stale = self._repls.get(workspace_id)
            selected = python_executable or Path(sys.executable)
            previous_event = self._owner_events.get(workspace_id)
            if (
                stale is not None
                and stale[1] == cwd
                and stale[0].python_executable == selected
                and self._profiles.get(workspace_id, "host") == profile
                and (previous_event is None or not previous_event.is_set())
            ):
                if cancel_event is not None:
                    self._owner_events[workspace_id] = cancel_event
                else:
                    self._owner_events.pop(workspace_id, None)
                return stale[0]
            # Replace the entry under the lock, so two callers can never
            # race two interpreters into one workspace; the retired one is
            # stopped outside it, since killing a process is slow and no
            # other workspace should wait on it.
            kwargs = {"command_builder": command_builder} if command_builder is not None else {}
            repl = PythonREPL(
                repl_id=workspace_id, cwd=cwd, manage_cwd=manage_cwd,
                python_executable=python_executable, **kwargs,
            )
            self._repls[workspace_id] = (repl, cwd)
            self._profiles[workspace_id] = profile
            if cancel_event is not None:
                self._owner_events[workspace_id] = cancel_event
            else:
                self._owner_events.pop(workspace_id, None)
        if stale is not None:
            stale[0].stop()
        return repl

    def bind_root(self, workspace_id: str, cwd: Path, profile: str) -> None:
        """Retire live interpreters on workspace rebind or profile change.

        This runs at task start, even if the next task does not call Python:
        an old Host interpreter must not linger after Isolated is selected.
        """
        with self._lock:
            stale = self._repls.get(workspace_id)
            retire = stale is not None and (
                stale[1] != cwd
                or self._profiles.get(workspace_id, "host") != profile
            )
        if retire and stale is not None:
            self.stop_workspace(workspace_id, expected_repl=stale[0])

    def stop_workspace(
        self, workspace_id: str, *, expected_repl: PythonREPL | None = None,
    ) -> None:
        # A cancelled task can still be unwinding when Stop frees its slot
        # and a new run creates an interpreter for the SAME workspace.
        # Retire only the interpreter belonging to the cancelled task.
        with self._lock:
            current = self._repls.get(workspace_id)
            if current is not None and (
                expected_repl is None or current[0] is expected_repl
            ):
                entry = self._repls.pop(workspace_id)
                self._owner_events.pop(workspace_id, None)
                self._profiles.pop(workspace_id, None)
            else:
                entry = None
        if expected_repl is not None:
            expected_repl.stop()
        elif entry is not None:
            entry[0].stop()

    def stop_root(self, root: Path, *, excluding: set[str] | None = None) -> None:
        """Retire REPLs still running in a revoked trusted directory."""
        resolved = root.resolve()
        protected = excluding or set()
        with self._lock:
            retired = []
            for workspace_id, (repl, cwd) in list(self._repls.items()):
                if workspace_id in protected or cwd.resolve() != resolved:
                    continue
                retired.append(repl)
                self._repls.pop(workspace_id, None)
                self._owner_events.pop(workspace_id, None)
                self._profiles.pop(workspace_id, None)
        for repl in retired:
            repl.stop()

    def stop_all(self) -> None:
        with self._lock:
            entries = list(self._repls.values())
            self._repls.clear()
            self._owner_events.clear()
            self._profiles.clear()
        for repl, _cwd in entries:
            repl.stop()


def register_harness_python_tool(
    registry: ToolRegistry, repls: "PythonReplRegistry | None" = None,
) -> None:
    # Supplied by the dispatcher in production so the REPL outlives the
    # task; the fallback keeps this standalone for tests.
    repl_registry = repls if repls is not None else PythonReplRegistry()

    async def python_exec(call: ToolCall, ctx: RunContext) -> ToolResult:
        error = source_error(call.arguments)
        if error is not None:
            return ToolResult(content=error, is_error=True)
        code = call.arguments["code"]

        workspace_id = getattr(ctx, "harness_workspace_id", None)
        if not isinstance(workspace_id, str) or not workspace_id:
            return ToolResult(content="No harness workspace is bound to this run.", is_error=True)
        bound = getattr(ctx, "harness_workspace_dir", None)
        try:
            if isinstance(bound, Path):
                # A bound project OR the run's managed scratch workspace.
                cwd, manage = bound, False
                is_scratch = (
                    getattr(ctx, "harness_workspace_is_user_dir", None) is False
                    or cwd.resolve() == workspace_dir(workspace_id).resolve()
                )
            else:
                cwd, manage = ensure_workspace(workspace_id), True
                is_scratch = True
        except (WorkspaceError, OSError) as exc:
            return ToolResult(content=f"Could not prepare the workspace: {exc}", is_error=True)

        cancel_event = ctx.cancel.event if ctx.cancel is not None else None
        if cancel_event is not None and cancel_event.is_set():
            raise RequestCancelledError("stopped")
        on_output = getattr(ctx, "on_tool_output", None)
        if on_output is not None:
            on_output("")  # after approval, including first-use venv setup
        profile = normalize_execution_profile(getattr(ctx, "harness_execution_profile", "host"))
        try:
            interpreter: Path | PurePosixPath
            if profile == "isolated":
                plan = planned_isolated_python_environment(
                    workspace_id, cwd, is_scratch=is_scratch,
                )
                await asyncio.to_thread(prepare_isolated_python_environment, plan)
                interpreter = plan.interpreter
            else:
                interpreter = await asyncio.to_thread(
                    lambda: ensure_python_environment(
                        workspace_id, cwd, is_scratch=is_scratch,
                    ),
                )
            if cancel_event is not None and cancel_event.is_set():
                raise RequestCancelledError("stopped")
            if profile == "isolated":
                repl = repl_registry.get(
                    workspace_id, cwd, manage_cwd=manage, python_executable=interpreter,
                    cancel_event=cancel_event, profile=profile,
                    command_builder=lambda script: isolated_repl_command(plan, cwd, script),
                )
            else:
                repl = repl_registry.get(
                    workspace_id, cwd, manage_cwd=manage, python_executable=interpreter,
                    cancel_event=cancel_event,
                )
        except (PythonEnvironmentError, OSError) as exc:
            return ToolResult(content=f"Could not prepare Python environment: {exc}", is_error=True)

        # asyncio.wait_for(to_thread(...)) only stops waiting: it does NOT
        # terminate user code in the child. Poll the same Stop event as the
        # shell tool and kill/retire this exact interpreter on cancellation
        # or timeout. The child guard kills spawned descendants as well.
        # Keep the old single-argument REPL contract for standalone
        # callers without a live/cancellable run (including instrumented
        # wrappers around execute). The harness passes the extra hooks.
        if on_output is None and cancel_event is None:
            invocation = asyncio.to_thread(repl.execute, code)
        else:
            invocation = asyncio.to_thread(repl.execute, code, on_output, cancel_event)
        worker = asyncio.create_task(invocation)
        deadline = asyncio.get_running_loop().time() + PYTHON_EXEC_TIMEOUT_SECONDS
        try:
            while not worker.done():
                if cancel_event is not None and cancel_event.is_set():
                    raise RequestCancelledError("stopped")
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                await asyncio.wait({worker}, timeout=min(0.1, remaining))
            if cancel_event is not None and cancel_event.is_set():
                raise RequestCancelledError("stopped")
            output = await worker
        except (IsolationUnavailableError, OSError) as exc:
            await asyncio.to_thread(
                repl_registry.stop_workspace, workspace_id, expected_repl=repl,
            )
            await asyncio.gather(worker, return_exceptions=True)
            return ToolResult(content=f"Isolated Python execution failed: {exc}", is_error=True)
        except (RequestCancelledError, asyncio.TimeoutError, asyncio.CancelledError) as exc:
            await asyncio.to_thread(
                repl_registry.stop_workspace, workspace_id, expected_repl=repl,
            )
            # The worker may have been reading stdout while Stop closed it;
            # drain its completion/exception so it cannot leak into a later
            # run or produce an unhandled task exception.
            await asyncio.gather(worker, return_exceptions=True)
            if not isinstance(exc, asyncio.TimeoutError):
                raise
            return ToolResult(
                content=(
                    f"Code timed out after {PYTHON_EXEC_TIMEOUT_SECONDS}s; the interpreter "
                    "was restarted, so previously defined variables are gone."
                ),
                is_error=True,
            )

        if len(output) > _OUTPUT_CAP_CHARS:
            output = output[:_OUTPUT_CAP_CHARS] + f"\n…[truncated at {_OUTPUT_CAP_CHARS} characters]"
        failed = bool(getattr(repl, "last_run_failed", False))
        if failed:
            # An exception in the executed code is NOT a tool error: the tool
            # worked, the code raised. Flagging it is_error would tell the
            # model its call was malformed when what it actually needs to do
            # is read the traceback and fix the code.
            return ToolResult(content=f"[code raised]\n{output}" if output else "[code raised, no output]")
        return ToolResult(content=output or "(no output)")

    registry.register(
        PYTHON_EXEC_SPEC, python_exec, scopes={CODE_EXECUTE}, approval="always",
        always_reprompt=lambda _call: True,
    )


USAGE_HINT = (
    "python.exec runs in the workspace's .venv/venv when available, or a persistent "
    "managed virtual environment created on first use in Host mode. In Isolated "
    "mode it always uses a network-disabled container with a separate managed environment. "
    "State survives between calls; every execution requires fresh approval."
)


def register_workspace_tools(registry: ToolRegistry, dispatcher, document) -> None:
    register_harness_python_tool(registry, dispatcher.harness_python_repls())
