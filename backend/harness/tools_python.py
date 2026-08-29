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
so `open("data.csv")` in executed code sees the same files `fs.read` and
`shell.exec` do. When that workspace is a user's own project folder the
REPL is constructed with `manage_cwd=False` - we never chmod or age-sweep
a directory the person owns (see PythonREPL's own docstring).

Registered `approval="always"` under `code.execute`, exactly like
`shell.exec`: arbitrary Python running with the user's full privileges is
the same risk class as an arbitrary shell command, and the fingerprint
memo means an identical repeat inside one run does not re-ask.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from backend.harness.workspace import WorkspaceError, ensure_workspace
from backend.providers.base import ToolCall, ToolSpec
from backend.tools import CODE_EXECUTE, RunContext, ToolRegistry, ToolResult
from graphlink_plugins.common.python_repl import PythonREPL

# Matches the code_sandbox execute ceiling (graphlink_plugins/code_sandbox/
# domain.py): long enough for real analysis, short enough that a runaway
# loop surfaces as a timeout inside one turn rather than stalling the task.
PYTHON_EXEC_TIMEOUT_SECONDS = 240
_OUTPUT_CAP_CHARS = 20_000
_CODE_CAP_CHARS = 20_000

PYTHON_EXEC_SPEC = ToolSpec(
    name="python.exec",
    description=(
        "Run Python in a persistent interpreter whose working directory is "
        "the workspace. Variables, imports, and loaded data survive between "
        "calls, so build up state across turns instead of re-running setup. "
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

    def __init__(self) -> None:
        # Value is (repl, cwd): the cwd is kept so a rebound workspace can
        # be detected - see get().
        self._repls: dict[str, tuple[PythonREPL, Path]] = {}
        self._lock = threading.RLock()

    def get(self, workspace_id: str, cwd: Path, *, manage_cwd: bool) -> PythonREPL:
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
            if stale is not None and stale[1] == cwd:
                return stale[0]
            # Replace the entry under the lock, so two callers can never
            # race two interpreters into one workspace; the retired one is
            # stopped outside it, since killing a process is slow and no
            # other workspace should wait on it.
            repl = PythonREPL(repl_id=workspace_id, cwd=cwd, manage_cwd=manage_cwd)
            self._repls[workspace_id] = (repl, cwd)
        if stale is not None:
            stale[0].stop()
        return repl

    def stop_workspace(self, workspace_id: str) -> None:
        with self._lock:
            entry = self._repls.pop(workspace_id, None)
        if entry is not None:
            entry[0].stop()

    def stop_all(self) -> None:
        with self._lock:
            entries = list(self._repls.values())
            self._repls.clear()
        for repl, _cwd in entries:
            repl.stop()


def register_harness_python_tool(
    registry: ToolRegistry, repls: "PythonReplRegistry | None" = None,
) -> None:
    # Supplied by the dispatcher in production so the REPL outlives the
    # task; the fallback keeps this standalone for tests.
    repl_registry = repls if repls is not None else PythonReplRegistry()

    async def python_exec(call: ToolCall, ctx: RunContext) -> ToolResult:
        code = str(call.arguments.get("code") or "").strip()
        if not code:
            return ToolResult(content="python.exec needs non-empty code.", is_error=True)
        if len(code) > _CODE_CAP_CHARS:
            return ToolResult(content=f"Code longer than {_CODE_CAP_CHARS} characters.", is_error=True)

        workspace_id = getattr(ctx, "harness_workspace_id", None)
        if not isinstance(workspace_id, str) or not workspace_id:
            return ToolResult(content="No harness workspace is bound to this run.", is_error=True)
        bound = getattr(ctx, "harness_workspace_dir", None)
        try:
            if isinstance(bound, Path):
                # A user's own project directory: bound but NOT managed by us.
                cwd, manage = bound, False
            else:
                cwd, manage = ensure_workspace(workspace_id), True
        except (WorkspaceError, OSError) as exc:
            return ToolResult(content=f"Could not prepare the workspace: {exc}", is_error=True)

        repl = repl_registry.get(workspace_id, cwd, manage_cwd=manage)
        try:
            output = await asyncio.wait_for(
                asyncio.to_thread(repl.execute, code),
                timeout=PYTHON_EXEC_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            # A hung interpreter cannot be reused - its stdin/stdout framing
            # is desynchronized past the boundary line it never printed. Tear
            # it down so the NEXT call transparently starts a fresh one
            # (PythonREPL.execute restarts a dead process on demand).
            await asyncio.to_thread(repl_registry.stop_workspace, workspace_id)
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

    registry.register(PYTHON_EXEC_SPEC, python_exec, scopes={CODE_EXECUTE}, approval="always")
