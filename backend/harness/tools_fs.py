"""H1's read-only workspace tools: fs.read / fs.list / fs.grep, all under
the fs.read scope with `auto` approval (the read-only posture every other
auto tool in backend/tools.py's vocabulary already takes).

Every handler resolves its path arguments through
workspace.resolve_in_workspace - the single confinement choke point - and
bounds its result before returning it (PLAN §2.3: results are capped at
the boundary so no tool can flood the context; the same posture the MCP
result caps and builder activity caps already establish).

Handlers find their workspace via the duck-typed run context
(ctx.harness_workspace_id) - the exact channel builder.py's control tools
already read ctx.controls through: tools stay ordinary registry
registrations with no special dispatch, and a context without the
attribute (some non-harness run invoking them by mistake) degrades to an
ordinary error ToolResult, never a crash.
"""

from __future__ import annotations

import re

from backend.harness.workspace import WorkspaceError, resolve_in_workspace, workspace_dir
from backend.providers.base import ToolCall, ToolSpec
from backend.tools import FS_READ, FS_WRITE, RunContext, ToolRegistry, ToolResult

_READ_CAP_CHARS = 40_000
# H2 write bounds: a single fs.write/fs.edit call's content ceiling. Wire
# input is untrusted model output - the same bounds-everywhere posture
# every other cap in this file takes.
_WRITE_CAP_CHARS = 200_000
_LIST_CAP_ENTRIES = 500
_GREP_CAP_MATCHES = 100
_GREP_LINE_CAP = 400
_GREP_FILE_SIZE_CAP = 2_000_000  # bytes; larger files are skipped, and say so
_GREP_PATTERN_CAP = 500

FS_READ_SPEC = ToolSpec(
    name="fs.read",
    description=(
        "Read a text file from the workspace. path is relative to the "
        "workspace root. Optional offset (0-based line) and limit (line "
        "count) window large files; output is truncated past "
        f"{_READ_CAP_CHARS} characters."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
        "required": ["path"],
    },
)

FS_LIST_SPEC = ToolSpec(
    name="fs.list",
    description=(
        "List files in the workspace. Optional glob pattern (e.g. '**/*.py') "
        "filters the listing; default lists everything. Returns "
        "workspace-relative paths, directories suffixed with '/'."
    ),
    input_schema={
        "type": "object",
        "properties": {"pattern": {"type": "string"}},
        "required": [],
    },
)

FS_WRITE_SPEC = ToolSpec(
    name="fs.write",
    description=(
        "Create or overwrite one text file in the workspace with the given "
        "content. path is workspace-relative; parent directories are "
        "created as needed. Overwrites silently - fs.read first if the "
        "current content matters."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    },
)

FS_EDIT_SPEC = ToolSpec(
    name="fs.edit",
    description=(
        "Edit one file by exact string replacement: old_string must occur "
        "EXACTLY ONCE in the file (the call fails on zero or multiple "
        "occurrences - include more surrounding context to disambiguate); "
        "it is replaced with new_string."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
        },
        "required": ["path", "old_string", "new_string"],
    },
)

FS_GREP_SPEC = ToolSpec(
    name="fs.grep",
    description=(
        "Search workspace files for a Python-flavored regular expression. "
        "Optional glob restricts which files are searched (default "
        "'**/*'). Returns 'path:line: text' matches, first "
        f"{_GREP_CAP_MATCHES} only."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "glob": {"type": "string"},
        },
        "required": ["pattern"],
    },
)


def _workspace_id_of(ctx: RunContext) -> str | None:
    workspace_id = getattr(ctx, "harness_workspace_id", None)
    return workspace_id if isinstance(workspace_id, str) and workspace_id else None


def _iter_workspace_files(workspace_id: str, pattern: str):
    """Workspace-relative iteration; resolve_in_workspace re-checks every
    yielded candidate so a glob cannot be a traversal primitive (Path.glob
    itself refuses '..' segments, but the double-check keeps confinement a
    single-choke-point property rather than a per-caller convention)."""
    root = workspace_dir(workspace_id).resolve()
    for path in sorted(root.glob(pattern or "**/*")):
        resolved = resolve_in_workspace(workspace_id, str(path))
        yield resolved, resolved.relative_to(root).as_posix()


def register_harness_fs_tools(registry: ToolRegistry) -> None:
    async def fs_read(call: ToolCall, ctx: RunContext) -> ToolResult:
        workspace_id = _workspace_id_of(ctx)
        if workspace_id is None:
            return ToolResult(content="No harness workspace is bound to this run.", is_error=True)
        try:
            target = resolve_in_workspace(workspace_id, str(call.arguments.get("path") or ""))
        except WorkspaceError as exc:
            return ToolResult(content=str(exc), is_error=True)
        if not target.is_file():
            return ToolResult(content=f"Not a file: {call.arguments.get('path')!r}.", is_error=True)
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult(content=f"Could not read file: {exc}", is_error=True)
        lines = text.splitlines()
        try:
            offset = max(0, int(call.arguments.get("offset") or 0))
        except (TypeError, ValueError):
            offset = 0
        try:
            limit = int(call.arguments.get("limit") or 0)
        except (TypeError, ValueError):
            limit = 0
        window = lines[offset : offset + limit] if limit > 0 else lines[offset:]
        body = "\n".join(window)
        if len(body) > _READ_CAP_CHARS:
            body = body[:_READ_CAP_CHARS] + f"\n…[truncated at {_READ_CAP_CHARS} characters]"
        header = f"{len(lines)} line(s) total; showing from line {offset + 1}.\n"
        return ToolResult(content=header + body)

    async def fs_list(call: ToolCall, ctx: RunContext) -> ToolResult:
        workspace_id = _workspace_id_of(ctx)
        if workspace_id is None:
            return ToolResult(content="No harness workspace is bound to this run.", is_error=True)
        pattern = str(call.arguments.get("pattern") or "**/*")
        entries: list[str] = []
        truncated = False
        try:
            for resolved, relative in _iter_workspace_files(workspace_id, pattern):
                if len(entries) >= _LIST_CAP_ENTRIES:
                    truncated = True
                    break
                entries.append(relative + "/" if resolved.is_dir() else relative)
        except (WorkspaceError, OSError, ValueError) as exc:
            return ToolResult(content=f"Could not list {pattern!r}: {exc}", is_error=True)
        if not entries:
            return ToolResult(content="The workspace has no files matching that pattern.")
        suffix = f"\n…[capped at {_LIST_CAP_ENTRIES} entries]" if truncated else ""
        return ToolResult(content="\n".join(entries) + suffix)

    async def fs_grep(call: ToolCall, ctx: RunContext) -> ToolResult:
        workspace_id = _workspace_id_of(ctx)
        if workspace_id is None:
            return ToolResult(content="No harness workspace is bound to this run.", is_error=True)
        raw_pattern = str(call.arguments.get("pattern") or "")
        if not raw_pattern:
            return ToolResult(content="fs.grep needs a non-empty pattern.", is_error=True)
        if len(raw_pattern) > _GREP_PATTERN_CAP:
            return ToolResult(content=f"Pattern longer than {_GREP_PATTERN_CAP} characters.", is_error=True)
        try:
            expression = re.compile(raw_pattern)
        except re.error as exc:
            return ToolResult(content=f"Invalid regular expression: {exc}", is_error=True)
        file_glob = str(call.arguments.get("glob") or "**/*")
        matches: list[str] = []
        skipped_large = 0
        try:
            for resolved, relative in _iter_workspace_files(workspace_id, file_glob):
                if len(matches) >= _GREP_CAP_MATCHES:
                    break
                if not resolved.is_file():
                    continue
                try:
                    if resolved.stat().st_size > _GREP_FILE_SIZE_CAP:
                        skipped_large += 1
                        continue
                    text = resolved.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if "\x00" in text[:1024]:
                    continue  # binary
                for line_number, line in enumerate(text.splitlines(), start=1):
                    if expression.search(line):
                        snippet = line if len(line) <= _GREP_LINE_CAP else line[:_GREP_LINE_CAP] + "…"
                        matches.append(f"{relative}:{line_number}: {snippet}")
                        if len(matches) >= _GREP_CAP_MATCHES:
                            break
        except (WorkspaceError, OSError, ValueError) as exc:
            return ToolResult(content=f"Could not search: {exc}", is_error=True)
        if not matches:
            note = f" ({skipped_large} file(s) skipped as too large)" if skipped_large else ""
            return ToolResult(content=f"No matches{note}.")
        footer = ""
        if len(matches) >= _GREP_CAP_MATCHES:
            footer = f"\n…[capped at {_GREP_CAP_MATCHES} matches]"
        if skipped_large:
            footer += f"\n[{skipped_large} file(s) skipped as too large]"
        return ToolResult(content="\n".join(matches) + footer)

    async def fs_write(call: ToolCall, ctx: RunContext) -> ToolResult:
        workspace_id = _workspace_id_of(ctx)
        if workspace_id is None:
            return ToolResult(content="No harness workspace is bound to this run.", is_error=True)
        content = call.arguments.get("content")
        if not isinstance(content, str):
            return ToolResult(content="fs.write needs string content.", is_error=True)
        if len(content) > _WRITE_CAP_CHARS:
            return ToolResult(
                content=f"Content longer than {_WRITE_CAP_CHARS} characters - split it into parts.",
                is_error=True,
            )
        try:
            target = resolve_in_workspace(workspace_id, str(call.arguments.get("path") or ""))
        except WorkspaceError as exc:
            return ToolResult(content=str(exc), is_error=True)
        if target == workspace_dir(workspace_id).resolve():
            return ToolResult(content="path names the workspace root, not a file.", is_error=True)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return ToolResult(content=f"Could not write file: {exc}", is_error=True)
        return ToolResult(content=f"Wrote {len(content)} characters to {call.arguments.get('path')}.")

    async def fs_edit(call: ToolCall, ctx: RunContext) -> ToolResult:
        workspace_id = _workspace_id_of(ctx)
        if workspace_id is None:
            return ToolResult(content="No harness workspace is bound to this run.", is_error=True)
        old_string = call.arguments.get("old_string")
        new_string = call.arguments.get("new_string")
        if not isinstance(old_string, str) or not old_string or not isinstance(new_string, str):
            return ToolResult(content="fs.edit needs a non-empty old_string and a new_string.", is_error=True)
        if len(new_string) > _WRITE_CAP_CHARS:
            return ToolResult(
                content=f"Replacement longer than {_WRITE_CAP_CHARS} characters.", is_error=True,
            )
        try:
            target = resolve_in_workspace(workspace_id, str(call.arguments.get("path") or ""))
        except WorkspaceError as exc:
            return ToolResult(content=str(exc), is_error=True)
        if not target.is_file():
            return ToolResult(content=f"Not a file: {call.arguments.get('path')!r}.", is_error=True)
        try:
            text = target.read_text(encoding="utf-8")
        except OSError as exc:
            return ToolResult(content=f"Could not read file: {exc}", is_error=True)
        occurrences = text.count(old_string)
        if occurrences == 0:
            return ToolResult(content="old_string was not found in the file - fs.read it and retry with exact text.", is_error=True)
        if occurrences > 1:
            return ToolResult(
                content=f"old_string occurs {occurrences} times - include more surrounding context so it is unique.",
                is_error=True,
            )
        try:
            target.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
        except OSError as exc:
            return ToolResult(content=f"Could not write file: {exc}", is_error=True)
        return ToolResult(content=f"Edited {call.arguments.get('path')}.")

    registry.register(FS_READ_SPEC, fs_read, scopes={FS_READ}, approval="auto")
    registry.register(FS_LIST_SPEC, fs_list, scopes={FS_READ}, approval="auto")
    registry.register(FS_GREP_SPEC, fs_grep, scopes={FS_READ}, approval="auto")
    # H2: writes prompt once per distinct call in a run (the managed-
    # workspace posture the plan's §3.2.6 sets; "always" with its
    # fingerprint memory would re-prompt only on ARGUMENT changes anyway -
    # "once" is the honest name for a scratch-confined write).
    registry.register(FS_WRITE_SPEC, fs_write, scopes={FS_WRITE}, approval="once")
    registry.register(FS_EDIT_SPEC, fs_edit, scopes={FS_WRITE}, approval="once")
