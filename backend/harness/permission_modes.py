"""Per-task workspace agent permission modes.

This is a *narrow exemption* to the existing tool approval gate. It never
grants an extra scope, applies outside an Agent run, approves network access,
or converts an ambiguous command into a trusted command. The actual handlers
retain their own path confinement, cancellation and execution guards.
"""
from __future__ import annotations

import re
from pathlib import Path

from backend.harness import shell_policy
from backend.harness.workspace import WorkspaceError, resolve_under_root

ASK = "ask"
AUTO_EDIT = "auto_edit"
AUTONOMOUS = "autonomous"
VALID_PERMISSION_MODES = frozenset({ASK, AUTO_EDIT, AUTONOMOUS})

_METADATA_DIRS = frozenset({".git", ".hg", ".svn"})
_NETWORK_WORDS = re.compile(r"(?i)(?:https?://|ftp://|git@|www\.|\b(?:curl|wget|ssh|scp|sftp|rsync|telnet|nc|ncat|ping)\b)")
_SAFE_LS_FLAGS = frozenset({"-a", "-l", "-la", "-al", "-1", "-h", "-lh", "-lah", "-alh"})
_SAFE_FILE_COMMANDS = frozenset({"cat", "head", "tail", "wc", "touch"})
# Host PowerShell read-only commands with literal, workspace-bound operands.
# Isolated execution uses /bin/sh regardless of the preferred host shell.
_POWERSHELL_LIST_READS = frozenset({"get-childitem", "gci", "dir"})
_POWERSHELL_FILE_READS = frozenset({"get-item", "gi", "get-content", "gc"})
# A shell prefix is suitable for disclosure, not proof of harmless execution.
# No interpreter, build tool, wrapper, path-qualified executable or Git helper
# qualifies for unprompted Autonomous execution.
_PLAIN_SEGMENT = re.compile(r"[A-Za-z0-9_./:@=+,-]+(?: [A-Za-z0-9_./:@=+,-]+)*")


def normalize_permission_mode(mode: object) -> str:
    """Unknown or hand-edited values never acquire implicit privileges."""
    return mode if isinstance(mode, str) and mode in VALID_PERMISSION_MODES else ASK


def effective_permission_mode(mode: object, profile: object) -> str:
    """Autonomous cannot be enabled by changing serialized node state alone."""
    chosen = normalize_permission_mode(mode)
    if chosen == AUTONOMOUS and profile != "isolated":
        return ASK
    return chosen


def _within(root: Path, raw: str, *, container_path: bool = False) -> bool:
    if not raw or "\x00" in raw or raw.startswith("~") or "$" in raw or "%" in raw:
        return False
    # Shell commands run *inside* the container, where the one project mount
    # appears at /workspace rather than the host's trusted directory.
    if container_path and raw == "/workspace":
        raw = "."
    elif container_path and raw.startswith("/workspace/"):
        raw = raw[len("/workspace/"):]
    try:
        path = resolve_under_root(root, raw)
    except (WorkspaceError, OSError, ValueError, RuntimeError):
        return False
    return not any(part.casefold() in _METADATA_DIRS for part in path.parts)


def _safe_shell(command: object, root: Path, shell: str, *, isolated: bool = False) -> bool:
    """Accept only a small, inspectable shell grammar for approval reuse.

    This is also the Autonomous Isolated auto-approval gate. Shells and
    interpreters can execute scripts even when a command looks local; the
    Isolated shell always uses /bin/sh, regardless of Host shell preference.
    """
    if not isinstance(command, str) or not command.strip() or len(command) > 4000:
        return False
    if _NETWORK_WORDS.search(command):
        return False
    try:
        selected = shell_policy.effective_shell_for_rules(
            shell, profile="isolated" if isolated else "host",
        )
        plan = shell_policy.analyze(
            command, shell="bash" if selected == "git-bash" else selected,
        )
    except Exception:
        return False
    if plan.is_dangerous or not plan.segments:
        return False
    for segment in plan.segments:
        # Reject quoting, substitutions, assignment syntax, redirections,
        # path-qualified executables and all other complex shell grammar.
        if len(segment) > 256 or _PLAIN_SEGMENT.fullmatch(segment) is None:
            return False
        words = segment.split()
        program, args = words[0].lower(), words[1:]
        if selected == "powershell" and program in {"get-location", "gl"}:
            if args:
                return False
            continue
        if selected == "powershell" and (
            program in _POWERSHELL_LIST_READS or program in _POWERSHELL_FILE_READS
        ):
            paths = args
            if program in _POWERSHELL_FILE_READS and not paths:
                return False
            # Provider-qualified paths (env:, HKLM:), splatting and cmdlet
            # parameters are not literal workspace-relative file operands.
            if any(path.startswith("-") or ":" in path or "@" in path for path in paths):
                return False
        elif program == "echo":
            continue
        elif program == "pwd":
            if args:
                return False
            continue
        elif program == "ls":
            paths = [arg for arg in args if arg not in _SAFE_LS_FLAGS]
            if any(arg.startswith("-") for arg in paths):
                return False
        elif program == "mkdir":
            paths = [arg for arg in args if arg != "-p"]
            if not paths or any(arg.startswith("-") for arg in paths):
                return False
        elif program in _SAFE_FILE_COMMANDS:
            paths = args
            if not paths or any(arg.startswith("-") for arg in paths):
                return False
        else:
            return False
        # Bare file names may resolve through a symlink outside the root.
        if any(not _within(root, path, container_path=isolated) for path in paths):
            return False
    return True


def requires_explicit_workspace_approval(call, ctx) -> bool:
    """Override even pre-existing session grants / shell prefix rules.

    A previous 'always allow' must not authorize an out-of-root argument,
    dangerous command or network request. This protection also applies
    while using Ask or Auto-edit, not only Autonomous.
    """
    root = getattr(ctx, "harness_workspace_dir", None)
    if not isinstance(root, Path) or not getattr(ctx, "harness_node_id", None):
        return False
    if call.name in {"fs.write", "fs.edit"}:
        raw = call.arguments.get("path")
        return not isinstance(raw, str) or not _within(root, raw)
    if call.name == "python.exec":
        # REPL state can shadow even harmless builtin names. Source inspection
        # is not a sound sandbox or a substitute for a fresh approval.
        return True
    if call.name == "shell.exec":
        return call.arguments.get("network") is True or not _safe_shell(
            call.arguments.get("command"), root, getattr(ctx, "harness_shell", "auto"),
            isolated=getattr(ctx, "harness_execution_profile", "host") == "isolated",
        )
    if call.name == "shell.session":
        action = call.arguments.get("action")
        if action == "write":
            return True
        if action == "start":
            return not _safe_shell(
                call.arguments.get("command"), root, getattr(ctx, "harness_shell", "auto"),
                isolated=getattr(ctx, "harness_execution_profile", "host") == "isolated",
            )
    return False


def may_skip_workspace_approval(call, ctx) -> bool:
    """True only for narrow operations of a *snapshotted* Agent task.

    The caller checks scopes first. Stateful Python, scripts, network and
    graph mutations are not exempt from approval.
    """
    root = getattr(ctx, "harness_workspace_dir", None)
    if not isinstance(root, Path) or not getattr(ctx, "harness_node_id", None) or not getattr(ctx, "harness_workspace_id", None):
        return False
    profile = getattr(ctx, "harness_execution_profile", "host")
    mode = effective_permission_mode(getattr(ctx, "harness_permission_mode", ASK), profile)
    if mode == ASK:
        return False
    if call.name in {"fs.write", "fs.edit"}:
        raw = call.arguments.get("path")
        return isinstance(raw, str) and _within(root, raw)
    if mode != AUTONOMOUS or profile != "isolated":
        return False
    if call.name == "python.install":
        # Only offline cached-wheel installs bypass. The installer requests a
        # fresh, bound approval before *any* PyPI download on a cache miss.
        return True
    if call.name == "shell.exec":
        if call.arguments.get("network") is not None and call.arguments.get("network") is not False:
            return False
        return _safe_shell(
            call.arguments.get("command"), root, getattr(ctx, "harness_shell", "auto"),
            isolated=True,
        )
    if call.name == "shell.session":
        action = call.arguments.get("action")
        if action in ("read", "list", "stop"):
            return True
        if action == "start":
            return _safe_shell(
                call.arguments.get("command"), root, getattr(ctx, "harness_shell", "auto"),
                isolated=True,
            )
        # A session's stdin may target a Python/Node/shell REPL; its language
        # cannot be inferred from the input alone. Ask for every write.
    return False
